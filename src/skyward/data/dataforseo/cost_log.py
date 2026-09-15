"""Per-task DataForSEO cost records and the streaming writer for DataForSEO.cost_log.

Only billed calls are recorded: `/live` requests and `/task_post` submissions. `task_get`
is never recorded because DFS repeats the task_post cost on it (SERP), which would double
count. One record per entry in the response's `tasks[]`.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
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
    for i, task in enumerate(resp.get("tasks") or []):
        if not isinstance(task, dict):
            continue
        task_payload = payload_list[i] if i < len(payload_list) else {}
        records.append({
            "task_id": task.get("id"),
            "call_type": call_type,
            "http_status": http_status,
            "dfs_status_code": task.get("status_code"),
            "items_sent": _items_sent(task_payload),
            "result_rows": _result_rows(task),
            "price_inputs": json.dumps(_price_inputs(task_payload), default=str, sort_keys=True),
            "cost_usd": float(task.get("cost") or 0.0),
            "requested_at": requested_at,
        })
    return records


def cost_flush_every(planned_requests: int) -> int:
    """Stream about every 1% of a run's planned requests, between 1 and 500 rows."""
    return max(1, min(500, math.ceil(max(planned_requests, 0) / 100)))


class CostLogWriter:
    """Thread-safe buffer that streams finalized cost rows into DataForSEO.cost_log."""

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
        self.written_rows = 0
        self.rows_by_upload_id: dict[str, int] = {}

    @property
    def table_id(self) -> str:
        return f"{self._bq.client.project}.{DATASET}.{COST_LOG_TABLE}"

    def add(self, rows: list[dict], *, flush: bool = True) -> None:
        if not rows:
            return
        with self._lock:
            self._buffer.extend(rows)
            for r in rows:
                uid = r.get("upload_id")
                if uid:
                    self.rows_by_upload_id[uid] = self.rows_by_upload_id.get(uid, 0) + 1
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
                return True
            with self._lock:
                self._buffer = batch + self._buffer
            return False

    def _insert(self, rows: list[dict]) -> bool:
        last_error = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                errors = self._bq.client.insert_rows_json(self.table_id, rows)
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
        if self.flush():
            return
        with self._lock:
            n = len(self._buffer)
            cost = sum(float(r.get("cost_usd") or 0.0) for r in self._buffer)
        msg = (f"DFS cost logging FAILED: {n} cost rows (${cost:.4f}) could not be written "
               f"to {COST_LOG_TABLE}.")
        print(msg)
        logger.error(msg)
