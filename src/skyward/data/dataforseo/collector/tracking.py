"""TrackingStore — the read/write layer over the two standard-mode collector tables,
`DataForSEO.dfs_job_summary` and `DataForSEO.dfs_task_log`.

The one place that knows those tables' schemas:
- `submit()`   — producer side: batch-load pending task rows + upsert the summary row.
- `completion_pct()` — producer side: one tiny read of the summary row.
- `mark_fetched()` — collector side: batch-update task statuses + recompute the summary.

Resilience: the collector runs forever and the producer waits on these tables, so every
BigQuery operation goes through `_retry()` — transient errors (5xx, rate limits, concurrent
DML serialization) are retried with exponential backoff. Writes are batched (never per-row
DML), and each BQ op is retried independently so a retry never double-applies a prior op
(load jobs are atomic; the summary write is an idempotent MERGE; status updates recompute
from the task-log source of truth).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

import pandas as pd
from google.api_core import exceptions as gexc

if TYPE_CHECKING:
    from skyward.data.bigquery import BigQueryClient

DATASET = "DataForSEO"
JOB_SUMMARY_TABLE = "dfs_job_summary"
TASK_LOG_TABLE = "dfs_task_log"
SENTINEL_JOB_ID = "d4s_standard_unattributed"

_TASK_LOG_COLUMNS = [
    "task_id", "job_id", "endpoint", "keyword", "submitted_at",
    "fetched_at", "status", "dfs_status_code", "result_rows", "attempts",
]

# Transient BigQuery failures worth retrying.
_RETRYABLE_EXC = (
    gexc.ServiceUnavailable, gexc.InternalServerError, gexc.TooManyRequests,
    gexc.BadGateway, gexc.GatewayTimeout, gexc.DeadlineExceeded, gexc.Conflict,
)
_RETRYABLE_MSG = (
    "could not serialize access", "concurrent update", "ratelimitexceeded",
    "backenderror", "try again later", "transient", "deadline",
)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, _RETRYABLE_EXC):
        return True
    msg = str(exc).lower()
    return any(s in msg for s in _RETRYABLE_MSG)


class TrackingStore:
    def __init__(
        self,
        bq_client: "BigQueryClient",
        *,
        max_retries: int = 5,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 30.0,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._bq = bq_client
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._sleep = _sleep

    def _table(self, name: str) -> str:
        return f"{self._bq.client.project}.{DATASET}.{name}"

    def _retry(self, fn: Callable, *, label: str):
        """Run `fn`, retrying transient BQ errors with exponential backoff."""
        for attempt in range(1, self._max_retries + 1):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 - classify, then re-raise if not transient
                if not _is_retryable(e) or attempt == self._max_retries:
                    raise
                delay = min(self._retry_max_delay, self._retry_base_delay * (2 ** (attempt - 1)))
                print(f"[TrackingStore] {label}: transient error (attempt "
                      f"{attempt}/{self._max_retries}): {e!r}; retrying in {delay}s")
                self._sleep(delay)

    def _query(self, sql: str, params: list, *, label: str):
        from google.cloud import bigquery
        cfg = bigquery.QueryJobConfig(query_parameters=params)
        return self._retry(lambda: self._bq.client.query(sql, job_config=cfg).result(), label=label)

    # ----- producer: submit -----

    def submit(
        self,
        *,
        job_id: str,
        endpoint: str,
        tasks: list[dict],
        domain_id: int | None = None,
        domain: str | None = None,
    ) -> None:
        """Record a freshly-posted batch: load pending task rows, upsert the summary.

        `domain_id`/`domain` are the run's attribution context — stored on the summary so the
        collector can stamp them onto canonical rows (DFS itself doesn't know the domain).

        Each BQ op is retried independently. Load jobs are atomic (retry-safe); the summary
        MERGE is idempotent on (job_id, endpoint), so re-running submit converges.
        """
        from google.cloud import bigquery

        ts = pd.Timestamp.now("UTC")

        # Dedupe by task_id within the call (task_ids are unique per DFS post; this is
        # defensive). Summary counts also use COUNT(DISTINCT task_id), so even a duplicate
        # load from a retry can't inflate the rollup.
        unique = list({t["task_id"]: t for t in tasks}.values())

        if unique:
            rows = [{
                "task_id": t["task_id"],
                "job_id": job_id,
                "endpoint": endpoint,
                "keyword": t.get("keyword"),
                "submitted_at": ts,
                "fetched_at": pd.NaT,
                "status": "pending",
                "dfs_status_code": pd.NA,
                "result_rows": pd.NA,
                "attempts": 0,
            } for t in unique]
            df = pd.DataFrame(rows, columns=_TASK_LOG_COLUMNS)
            load_cfg = bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND
            )
            self._retry(
                lambda: self._bq.client.load_table_from_dataframe(
                    df, self._table(TASK_LOG_TABLE), job_config=load_cfg
                ).result(),
                label="submit.load_task_log",
            )

        merge = f"""
        MERGE `{self._table(JOB_SUMMARY_TABLE)}` T
        USING (SELECT @job_id AS job_id, @endpoint AS endpoint) S
        ON T.job_id = S.job_id AND T.endpoint = S.endpoint
        WHEN MATCHED THEN UPDATE SET
          total_tasks = @total, domain_id = @domain_id, domain = @domain,
          status = 'pending', submitted_at = @ts, last_updated = @ts
        WHEN NOT MATCHED THEN INSERT
          (job_id, endpoint, domain_id, domain, total_tasks, fetched_count, failed_count, status, submitted_at, last_updated)
          VALUES (@job_id, @endpoint, @domain_id, @domain, @total, 0, 0, 'pending', @ts, @ts)
        """
        self._query(merge, [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("endpoint", "STRING", endpoint),
            bigquery.ScalarQueryParameter("domain_id", "INT64", domain_id),
            bigquery.ScalarQueryParameter("domain", "STRING", domain),
            bigquery.ScalarQueryParameter("total", "INT64", len(unique)),
            bigquery.ScalarQueryParameter("ts", "TIMESTAMP", ts.to_pydatetime()),
        ], label="submit.merge_summary")

    # ----- producer: progress read -----

    def completion_pct(self, *, job_id: str, endpoint: str) -> float:
        """fetched_count / total_tasks for one (job_id, endpoint).

        - No summary row yet (not submitted) -> 0.0 (keep waiting).
        - Row exists with total_tasks == 0 (empty submit) -> 1.0 (nothing to wait for).
        """
        from google.cloud import bigquery

        sql = f"""
        SELECT total_tasks, fetched_count
        FROM `{self._table(JOB_SUMMARY_TABLE)}`
        WHERE job_id = @job_id AND endpoint = @endpoint
        """
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("endpoint", "STRING", endpoint),
        ])
        df = self._retry(
            lambda: self._bq.client.query(sql, job_config=cfg).result().to_dataframe(),
            label="completion_pct",
        )
        if df.empty:
            return 0.0
        total = df.iloc[0]["total_tasks"] or 0
        fetched = df.iloc[0]["fetched_count"] or 0
        if not total:
            return 1.0  # zero tasks submitted == trivially complete (don't hang the producer)
        return float(fetched) / float(total)

    def job_status(self, *, job_id: str, endpoint: str | None = None) -> list[dict]:
        """Per-endpoint progress for a job_id, read from dfs_job_summary.

        Returns one dict per (job_id, endpoint): total_tasks, fetched, failed, status,
        completion_pct (fetched/total), submitted_at, last_updated. Empty list if unknown.
        """
        from google.cloud import bigquery

        where = "job_id = @job_id" + (" AND endpoint = @endpoint" if endpoint else "")
        params = [bigquery.ScalarQueryParameter("job_id", "STRING", job_id)]
        if endpoint:
            params.append(bigquery.ScalarQueryParameter("endpoint", "STRING", endpoint))
        sql = f"""
        SELECT endpoint, total_tasks, fetched_count, failed_count, status, submitted_at, last_updated
        FROM `{self._table(JOB_SUMMARY_TABLE)}`
        WHERE {where}
        ORDER BY endpoint
        """
        cfg = bigquery.QueryJobConfig(query_parameters=params)
        df = self._retry(
            lambda: self._bq.client.query(sql, job_config=cfg).result().to_dataframe(),
            label="job_status",
        )
        out = []
        for _, r in df.iterrows():
            total = int(r["total_tasks"] or 0)
            fetched = int(r["fetched_count"] or 0)
            failed = int(r["failed_count"] or 0)
            out.append({
                "endpoint": r["endpoint"],
                "total_tasks": total,
                "fetched": fetched,
                "failed": failed,
                "status": r["status"],
                "completion_pct": 1.0 if total == 0 else fetched / total,
                "submitted_at": r["submitted_at"],
                "last_updated": r["last_updated"],
            })
        return out

    # ----- collector: batch status update -----

    def mark_fetched(self, *, endpoint: str, results: list[dict]) -> None:
        """Batch-update task rows from a collector cycle, then recompute affected summaries.

        `results`: dicts with task_id, status, dfs_status_code, result_rows, attempts.
        Idempotent: MERGE keys on task_id and the summary recomputes from the task-log, so a
        retried/duplicated call yields the same counts.
        """
        if not results:
            return
        from google.cloud import bigquery

        ts = pd.Timestamp.now("UTC")
        elements = [
            bigquery.StructQueryParameter(
                None,
                bigquery.ScalarQueryParameter("task_id", "STRING", r["task_id"]),
                bigquery.ScalarQueryParameter("status", "STRING", r["status"]),
                bigquery.ScalarQueryParameter("dfs_status_code", "INT64", r.get("dfs_status_code")),
                bigquery.ScalarQueryParameter("result_rows", "INT64", r.get("result_rows")),
                bigquery.ScalarQueryParameter("attempts", "INT64", r.get("attempts")),
            )
            for r in results
        ]
        rows_param = bigquery.ArrayQueryParameter("rows", "STRUCT", elements)
        ts_param = bigquery.ScalarQueryParameter("ts", "TIMESTAMP", ts.to_pydatetime())

        merge = f"""
        MERGE `{self._table(TASK_LOG_TABLE)}` T
        USING UNNEST(@rows) S
        ON T.task_id = S.task_id
        WHEN MATCHED THEN UPDATE SET
          status = S.status,
          fetched_at = @ts,
          dfs_status_code = S.dfs_status_code,
          result_rows = S.result_rows,
          attempts = S.attempts
        """
        self._query(merge, [rows_param, ts_param], label="mark_fetched.merge_task_log")

        # Recompute summary counts/status from dfs_task_log (source of truth) for this endpoint.
        update = f"""
        UPDATE `{self._table(JOB_SUMMARY_TABLE)}` S
        SET fetched_count = c.fetched,
            failed_count = c.failed,
            last_updated = @ts,
            status = CASE
              WHEN c.fetched + c.failed >= S.total_tasks THEN 'done'
              WHEN c.fetched + c.failed > 0 THEN 'partial'
              ELSE 'pending' END
        FROM (
          SELECT job_id, endpoint,
                 COUNT(DISTINCT IF(status = 'fetched', task_id, NULL)) AS fetched,
                 COUNT(DISTINCT IF(STARTS_WITH(status, 'failed'), task_id, NULL)) AS failed
          FROM `{self._table(TASK_LOG_TABLE)}`
          WHERE endpoint = @endpoint
          GROUP BY job_id, endpoint
        ) c
        WHERE S.job_id = c.job_id AND S.endpoint = c.endpoint
        """
        self._query(update, [
            bigquery.ScalarQueryParameter("endpoint", "STRING", endpoint),
            ts_param,
        ], label="mark_fetched.update_summary")
