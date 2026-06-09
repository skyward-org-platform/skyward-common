"""Run-scoped debug-log collector for DataForSEO live calls.

When `include_debug_logs=True` is passed to an endpoint's `live()` / `live_all()`,
a single `DebugLogCollector` is allocated for the run and threaded into the retry
loop. Each attempt appends one record; the collector flushes to the shared
`DataForSEO.debug_request_logs` BigQuery table in batches — every `flush_threshold`
records and once at run end.

Batching (never per-row DML) keeps us within repo conventions, and the periodic
flush means a mid-run crash loses at most the last unflushed batch rather than the
whole run — important when the thing being diagnosed is a flaky failure.

Thread-safe: `live_all` shares one collector across its ThreadPoolExecutor. Records
are appended under a lock; the buffer is swapped out under the lock and the BigQuery
write happens outside it so workers aren't blocked on the network round-trip.
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

import pandas as pd

from skyward.functions import generate_upload_id

if TYPE_CHECKING:
    from skyward.data.bigquery import BigQueryClient

DATASET = "DataForSEO"
TABLE_NAME = "debug_request_logs"
DEFAULT_FLUSH_THRESHOLD = 1000


def build_attempt_record(
    *,
    endpoint: str,
    target,
    attempt: int,
    is_terminal: bool,
    started_at: str,
    duration_ms: int,
    status_sink: dict | None,
    payload,
    resp: dict | None,
    n_items: int,
) -> dict:
    """Assemble one debug-log row from the raw parts of a single live attempt.

    Pulls DFS task-level status/cost out of the response, stringifies payload and
    response as JSON, and reads transport `http_status` / `error` from the sink
    `_post` populated. `job_id`, `upload_id` and `ingest_timestamp` are stamped
    later by the collector at flush time.
    """
    sink = status_sink or {}
    task_status_code = task_status_message = None
    task_cost = 0.0
    if resp:
        try:
            t0 = (resp.get("tasks") or [{}])[0]
            task_status_code = t0.get("status_code")
            task_status_message = t0.get("status_message")
            task_cost = float(t0.get("cost") or 0.0)
        except Exception:
            pass

    return {
        "endpoint": endpoint,
        "target": target if isinstance(target, str) else json.dumps(target, default=str),
        "attempt": attempt,
        "is_terminal": is_terminal,
        "started_at": started_at,
        "duration_ms": duration_ms,
        "thread_name": threading.current_thread().name,
        "http_status": sink.get("http_status"),
        "task_status_code": task_status_code,
        "task_status_message": task_status_message,
        "task_cost": task_cost,
        "n_items": n_items,
        "payload": json.dumps(payload, default=str),
        "response": json.dumps(resp, default=str) if resp is not None else None,
        "error": sink.get("error", ""),
    }


class DebugLogCollector:
    """Buffers per-attempt debug records and batch-loads them to BigQuery."""

    def __init__(
        self,
        bq_client: "BigQueryClient",
        *,
        job_id: str,
        flush_threshold: int = DEFAULT_FLUSH_THRESHOLD,
    ) -> None:
        self._bq = bq_client
        self._job_id = job_id
        self._threshold = max(1, flush_threshold)
        self._buffer: list[dict] = []
        self._lock = threading.Lock()

    def record(self, row: dict) -> None:
        """Append one attempt record; flush if the buffer hit the threshold."""
        with self._lock:
            self._buffer.append(row)
            if len(self._buffer) >= self._threshold:
                batch = self._buffer
                self._buffer = []
            else:
                batch = None
        if batch is not None:
            self._write(batch)

    def flush(self) -> None:
        """Write any buffered records. No-op when the buffer is empty."""
        with self._lock:
            batch = self._buffer
            self._buffer = []
        if batch:
            self._write(batch)

    def _write(self, rows: list[dict]) -> None:
        from google.cloud import bigquery

        df = pd.DataFrame(rows)
        upload_id = generate_upload_id()
        timestamp = pd.Timestamp.utcnow()
        df["job_id"] = self._job_id
        df["upload_id"] = upload_id
        df["ingest_timestamp"] = timestamp

        full_table_id = f"{self._bq.client.project}.{DATASET}.{TABLE_NAME}"
        try:
            job_config = bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND
            )
            load_job = self._bq.client.load_table_from_dataframe(
                df, full_table_id, job_config=job_config
            )
            load_job.result()

            self._bq.log_upload_event(
                job_id=self._job_id,
                upload_id=upload_id,
                source="dataforseo",
                source_program="dfs_debug_log",
                dataset=DATASET,
                table=TABLE_NAME,
                row_count=len(df),
                timestamp=timestamp,
            )
        except Exception as e:  # pragma: no cover - logging must never break a run
            print(f"Debug-log flush failed: {e}")
