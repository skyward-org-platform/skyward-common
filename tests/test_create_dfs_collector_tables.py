"""Guards the collector DDL: every column the tracking/collector code writes must
exist in the CREATE TABLE, and the partitioning/clustering must match the plan.
Keeps the schema and the code that writes it in sync.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_dfs_collector_tables.py"


def _load():
    spec = importlib.util.spec_from_file_location("create_dfs_collector_tables", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


JOB_SUMMARY_COLS = {
    "job_id", "endpoint", "domain_id", "domain", "total_tasks", "fetched_count",
    "failed_count", "status", "submitted_at", "last_updated", "notes", "notified_at",
}
TASK_LOG_COLS = {
    "task_id", "job_id", "endpoint", "keyword", "submitted_at", "fetched_at",
    "status", "dfs_status_code", "result_rows", "attempts",
}


def test_ddl_is_create_if_not_exists():
    m = _load()
    assert "CREATE TABLE IF NOT EXISTS" in m.JOB_SUMMARY_DDL
    assert "CREATE TABLE IF NOT EXISTS" in m.TASK_LOG_DDL


def test_tables_target_dataforseo_dataset():
    m = _load()
    assert "data-hub-468216.DataForSEO.dfs_job_summary" in m.JOB_SUMMARY_DDL
    assert "data-hub-468216.DataForSEO.dfs_task_log" in m.TASK_LOG_DDL


def test_job_summary_covers_all_columns_and_clusters_on_job_id():
    ddl = _load().JOB_SUMMARY_DDL
    for c in JOB_SUMMARY_COLS:
        assert c in ddl, f"dfs_job_summary missing column: {c}"
    assert "CLUSTER BY job_id" in ddl


def test_task_log_covers_all_columns_partition_and_cluster():
    ddl = _load().TASK_LOG_DDL
    for c in TASK_LOG_COLS:
        assert c in ddl, f"dfs_task_log missing column: {c}"
    assert "PARTITION BY DATE(submitted_at)" in ddl
    assert "CLUSTER BY endpoint, status, job_id" in ddl


def test_every_column_has_a_description():
    m = _load()
    # one OPTIONS(description=...) per column in each table
    assert m.JOB_SUMMARY_DDL.count("OPTIONS(description=") >= len(JOB_SUMMARY_COLS)
    assert m.TASK_LOG_DDL.count("OPTIONS(description=") >= len(TASK_LOG_COLS)
