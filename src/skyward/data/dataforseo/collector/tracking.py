"""TrackingStore — the read/write layer over the two standard-mode collector tables,
`DataForSEO.dfs_job_summary` and `DataForSEO.dfs_task_log`.

The one place that knows those tables' schemas:
- `submit()`   — producer side: batch-load pending task rows + upsert the summary row.
- `completion_pct()` — producer side: one tiny read of the summary row.
- `mark_fetched()` — collector side: batch-update task statuses + recompute the summary.

All writes are batched (per repo rules — never one DML statement per row).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

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


class TrackingStore:
    def __init__(self, bq_client: "BigQueryClient") -> None:
        self._bq = bq_client

    def _table(self, name: str) -> str:
        return f"{self._bq.client.project}.{DATASET}.{name}"

    # ----- producer: submit -----

    def submit(self, *, job_id: str, endpoint: str, tasks: list[dict]) -> None:
        """Record a freshly-posted batch: load pending task rows, upsert the summary."""
        from google.cloud import bigquery

        ts = pd.Timestamp.now("UTC")
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
        } for t in tasks]
        df = pd.DataFrame(rows, columns=_TASK_LOG_COLUMNS)

        load_cfg = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND
        )
        self._bq.client.load_table_from_dataframe(
            df, self._table(TASK_LOG_TABLE), job_config=load_cfg
        ).result()

        # Upsert exactly one summary row per (job_id, endpoint) — idempotent submit.
        merge = f"""
        MERGE `{self._table(JOB_SUMMARY_TABLE)}` T
        USING (SELECT @job_id AS job_id, @endpoint AS endpoint) S
        ON T.job_id = S.job_id AND T.endpoint = S.endpoint
        WHEN MATCHED THEN UPDATE SET
          total_tasks = @total, status = 'pending',
          submitted_at = @ts, last_updated = @ts
        WHEN NOT MATCHED THEN INSERT
          (job_id, endpoint, total_tasks, fetched_count, failed_count, status, submitted_at, last_updated)
          VALUES (@job_id, @endpoint, @total, 0, 0, 'pending', @ts, @ts)
        """
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("endpoint", "STRING", endpoint),
            bigquery.ScalarQueryParameter("total", "INT64", len(tasks)),
            bigquery.ScalarQueryParameter("ts", "TIMESTAMP", ts.to_pydatetime()),
        ])
        self._bq.client.query(merge, job_config=cfg).result()

    # ----- producer: progress read -----

    def completion_pct(self, *, job_id: str, endpoint: str) -> float:
        """fetched_count / total_tasks for one (job_id, endpoint). 0.0 if no row / no tasks."""
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
        df = self._bq.client.query(sql, job_config=cfg).result().to_dataframe()
        if df.empty:
            return 0.0
        total = df.iloc[0]["total_tasks"] or 0
        fetched = df.iloc[0]["fetched_count"] or 0
        return 0.0 if not total else float(fetched) / float(total)

    # ----- collector: batch status update -----

    def mark_fetched(self, *, endpoint: str, results: list[dict]) -> None:
        """Batch-update task rows from a collector cycle, then recompute affected summaries.

        `results`: dicts with task_id, status, dfs_status_code, result_rows, attempts.
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
        merge_cfg = bigquery.QueryJobConfig(query_parameters=[
            rows_param,
            bigquery.ScalarQueryParameter("ts", "TIMESTAMP", ts.to_pydatetime()),
        ])
        self._bq.client.query(merge, job_config=merge_cfg).result()

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
                 COUNTIF(status = 'fetched') AS fetched,
                 COUNTIF(STARTS_WITH(status, 'failed')) AS failed
          FROM `{self._table(TASK_LOG_TABLE)}`
          WHERE endpoint = @endpoint
          GROUP BY job_id, endpoint
        ) c
        WHERE S.job_id = c.job_id AND S.endpoint = c.endpoint
        """
        update_cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("endpoint", "STRING", endpoint),
            bigquery.ScalarQueryParameter("ts", "TIMESTAMP", ts.to_pydatetime()),
        ])
        self._bq.client.query(update, job_config=update_cfg).result()
