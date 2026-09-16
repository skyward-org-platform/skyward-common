"""Per-task DataForSEO cost records and the streaming writer for DataForSEO.cost_log.

Only billed calls are recorded: `/live` requests and `/task_post` submissions. `task_get`
is never recorded because DFS repeats the task_post cost on it (SERP), which would double
count. One record per entry in the response's `tasks[]`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DATASET = "DataForSEO"
COST_LOG_TABLE = "cost_log"
_TARGET_KEYS = ("keyword", "keywords", "target", "targets", "tag", "filters", "order_by")


def classify_call(url: str, payload) -> str | None:
    if "/task_post" in url:
        return "task_post"
    if "/task_get" in url or url.endswith("/tasks_ready") or url.endswith("/tasks_fixed"):
        return None
    if "/live" in url:
        first = payload[0] if isinstance(payload, list) and payload else {}
        return "live_page" if isinstance(first, dict) and "offset" in first else "live"
    return None


def _items_sent(task_payload) -> int:
    if not isinstance(task_payload, dict):
        return 1
    for key in ("keywords", "targets"):
        value = task_payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 1


def _price_inputs(task_payload) -> dict:
    if not isinstance(task_payload, dict):
        return {}
    return {k: v for k, v in task_payload.items() if k not in _TARGET_KEYS}


def _result_rows(task: dict) -> int:
    results = task.get("result") or []
    total = 0
    has_items = False
    for r in results:
        if isinstance(r, dict) and isinstance(r.get("items"), list):
            has_items = True
            total += len(r["items"])
    return total if has_items else len(results)


def extract_cost_records(url: str, payload, resp, http_status) -> list[dict]:
    call_type = classify_call(url, payload)
    if call_type is None or not isinstance(resp, dict):
        return []
    payload_list = payload if isinstance(payload, list) else [payload]
    requested_at = datetime.now(timezone.utc).isoformat()
    records: list[dict] = []
    tasks = resp.get("tasks")
    if not isinstance(tasks, list):
        if tasks is not None:
            logger.warning("DFS response 'tasks' is not a list: %r", type(tasks).__name__)
        tasks = []
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        task_payload = payload_list[i] if i < len(payload_list) else {}
        try:
            cost_value = task.get("cost")
            if cost_value is None:
                cost_usd = 0.0
            else:
                try:
                    cost_usd = float(cost_value)
                except (ValueError, TypeError):
                    logger.warning("DFS task cost is non-numeric: %r, using 0.0", cost_value)
                    cost_usd = 0.0
            records.append({
                "task_id": task.get("id"),
                "call_type": call_type,
                "http_status": http_status,
                "dfs_status_code": task.get("status_code"),
                "items_sent": _items_sent(task_payload),
                "result_rows": _result_rows(task),
                "price_inputs": json.dumps(_price_inputs(task_payload), default=str, sort_keys=True),
                "cost_usd": cost_usd,
                "requested_at": requested_at,
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to build cost record for task %r: %s", task.get("id"), e)
    return records


def extract_unattributed_billed_retry_record(url: str, payload, http_status: int,
                                              attempt: int) -> dict | None:
    """One marker row for a 2xx response DataForSEO already billed but whose body could
    not be parsed or used, forcing `_post` to retry with the same payload -- which DFS
    then bills again while the first, billed attempt is never recorded anywhere.

    Returns None for a call `_post` never bills for in the first place (task_get,
    tasks_ready/tasks_fixed, or any non-`/live`/`/task_post` endpoint) -- same gate as
    `extract_cost_records` -- since there is no billing gap to signal there.

    `cost_usd` is intentionally not filled in by the caller: we do not know DataForSEO's
    actual charge for the lost attempt, and guessing would corrupt job totals. The row
    exists to make the event visible, not to price it.
    """
    call_type = classify_call(url, payload)
    if call_type is None:
        return None
    task_payload = payload[0] if isinstance(payload, list) and payload else payload
    price_inputs = _price_inputs(task_payload)
    price_inputs["unattributed_billed_retry"] = True
    return {
        "task_id": None,
        "call_type": call_type,
        "http_status": http_status,
        "dfs_status_code": None,
        "items_sent": _items_sent(task_payload),
        "result_rows": 0,
        "price_inputs": json.dumps(price_inputs, default=str, sort_keys=True),
        "cost_usd": 0.0,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "attempt": attempt,
    }


def cost_flush_every(planned_requests: int) -> int:
    """Stream about every 1% of a run's planned requests, between 25 and 500 rows.

    Below a floor of 25, flush_if_due() (called after every absorbed unit) would fire on
    almost every unit; CostLogWriter.flush() holds its write lock across a BigQuery round
    trip, so that cadence serializes fan-out workers on small/medium runs for no benefit.
    """
    return max(25, min(500, math.ceil(max(planned_requests, 0) / 100)))


def _assign_row_id(row: dict) -> dict:
    """Attach a BigQuery streaming-insert id to `row` under the private key `_row_id`.

    Assigned exactly once, when the row is built (here, the first time it enters the
    writer's buffer), and carried unchanged with the row through every retry -- so a
    retry after a lost acknowledgement reuses the same id instead of minting a fresh
    UUID on each attempt (the original bug: BigQuery's client generates a random
    insertId per call unless one is supplied, so a retried insert after a lost ack
    double-counts the row in DataForSEO.cost_log).

    When the row has a `task_id`, the id is a deterministic digest of the row's
    immutable identity (job_id, task_id, attempt, call_type) -- deliberately NOT a hash
    of the whole row's content. Two genuinely distinct zero-cost billed calls could
    otherwise share identical content (same job/endpoint/call_type/attempt, null
    task_id, same cost, same timestamp string) and hash identically, which would make
    BigQuery silently drop one of them -- turning a double-count into an under-count of
    spend, which is worse than the duplicate-row bug this exists to fix.

    When `task_id` is None (network failures, malformed responses) there is no stable
    identity to hash, so a uuid4 is generated once here and carried with the row instead.

    Note: BigQuery's insertId de-duplication is best effort and time limited (roughly a
    few minutes), so this narrows the duplicate window rather than guaranteeing
    exactly-once delivery.
    """
    if row.get("task_id") is not None:
        key = "|".join(str(row.get(k, "")) for k in ("job_id", "task_id", "attempt", "call_type"))
        row["_row_id"] = hashlib.sha256(key.encode("utf-8")).hexdigest()
    else:
        row["_row_id"] = str(uuid.uuid4())
    return row


class CostLogWriter:
    """Thread-safe buffer that streams finalized cost rows into DataForSEO.cost_log.

    Each row is assigned a stable id (see `_assign_row_id`) the moment it is added, and
    that id is passed as BigQuery's `row_ids` (insertId) on every insert attempt so a
    retry after a lost acknowledgement can be de-duplicated instead of double-counted.
    That de-duplication is best effort and time limited (roughly a few minutes) on
    BigQuery's side, so it narrows rather than eliminates the duplicate window.
    """

    def __init__(self, bq_client, *, flush_every: int, max_attempts: int = 3,
                 retry_delay: float = 1.0, sleep=time.sleep) -> None:
        self._bq = bq_client
        self._every = max(1, flush_every)
        self._max_attempts = max(1, max_attempts)
        self._retry_delay = retry_delay
        self._sleep = sleep
        self._buffer: list[dict] = []
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._closed = False
        self.written_rows = 0
        self.rows_by_upload_id: dict[str, int] = {}

    @property
    def table_id(self) -> str:
        return f"{self._bq.client.project}.{DATASET}.{COST_LOG_TABLE}"

    def add(self, rows: list[dict], *, flush: bool = True) -> None:
        if not rows:
            return
        for r in rows:
            if "_row_id" not in r:
                _assign_row_id(r)
        with self._lock:
            self._buffer.extend(rows)
            late = self._closed
        if late:
            # A run unit that was still in flight when close() ran -- possible wherever
            # units execute concurrently without a shutdown barrier. These rows are REAL
            # DataForSEO charges. Refusing them would make the loss loud but would still
            # under-count spend, and cost_log is append-only and linked by job_id and
            # task_id rather than by run lifecycle, so a late insert is perfectly valid.
            # Flush now instead of leaving them buffered: the normal follow-up is
            # flush_if_due(), which needs `flush_every` rows (floor 25) to fire, so a
            # handful of late rows would otherwise sit in the buffer forever and vanish.
            n = len(rows)
            cost = sum(float(r.get("cost_usd") or 0.0) for r in rows)
            logger.warning(
                "DFS cost rows arrived after the cost writer closed: %d row(s) "
                "($%.4f). Writing them now so the spend is not under-counted.", n, cost)
            self.flush()
            return
        if flush:
            self.flush_if_due()

    def flush_if_due(self) -> None:
        with self._lock:
            due = len(self._buffer) >= self._every
        if due:
            self.flush()

    def flush(self) -> bool:
        with self._write_lock:
            with self._lock:
                batch, self._buffer = self._buffer, []
            if not batch:
                return True
            if self._insert(batch):
                self.written_rows += len(batch)
                with self._lock:
                    for r in batch:
                        uid = r.get("upload_id")
                        if uid:
                            self.rows_by_upload_id[uid] = self.rows_by_upload_id.get(uid, 0) + 1
                return True
            with self._lock:
                self._buffer = batch + self._buffer
            return False

    def _insert(self, rows: list[dict]) -> bool:
        # `_row_id` was assigned once, in add(), and is carried on `rows` across every
        # retry below (and across separate flush() calls after a failed one) -- pop it
        # into BigQuery's row_ids/insertId argument here rather than sending it as part
        # of the row payload, which must stay byte-for-byte what it was before this fix.
        payload, row_ids = [], []
        for r in rows:
            clean = dict(r)
            row_ids.append(clean.pop("_row_id", None))
            payload.append(clean)
        last_error = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                errors = self._bq.client.insert_rows_json(self.table_id, payload, row_ids=row_ids)
                if not errors:
                    return True
                last_error = errors
            except Exception as e:  # noqa: BLE001 - logging must never break a run
                last_error = e
            if attempt < self._max_attempts:
                self._sleep(self._retry_delay * attempt)
        logger.warning("DFS cost_log insert failed after %d attempts: %r",
                       self._max_attempts, last_error)
        return False

    def close(self) -> None:
        # The flag only changes what a later add() does; this drain calls flush()
        # directly, so it is unaffected either way. Ordered this way for readability:
        # everything buffered goes out, and only then is the writer marked closed.
        ok = self.flush()
        with self._lock:
            self._closed = True
        if ok:
            return
        with self._lock:
            n = len(self._buffer)
            cost = sum(float(r.get("cost_usd") or 0.0) for r in self._buffer)
        msg = (f"DFS cost logging FAILED: {n} cost rows (${cost:.4f}) could not be written "
               f"to {COST_LOG_TABLE}.")
        print(msg)
        logger.error(msg)
