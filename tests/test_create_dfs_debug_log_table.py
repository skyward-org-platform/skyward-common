"""The DDL for the shared debug-log table must cover every column the collector
writes — otherwise a real run would fail the BigQuery load. This guards them in sync.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

from skyward.data.dataforseo.debug_log import build_attempt_record

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_dfs_debug_log_table.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("create_dfs_debug_log_table", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collector_columns() -> set[str]:
    rec = build_attempt_record(
        endpoint="e", target="t", attempt=1, is_terminal=True,
        started_at="x", duration_ms=1, status_sink={}, payload=[{}],
        resp=None, n_items=0,
    )
    # the three the collector stamps at flush time
    return set(rec) | {"job_id", "upload_id", "ingest_timestamp"}


def test_ddl_is_create_if_not_exists():
    mod = _load_script()
    ddl = mod._ddl("proj")
    assert "CREATE TABLE IF NOT EXISTS" in ddl
    assert "proj.DataForSEO.debug_request_logs" in ddl


def test_ddl_covers_every_collector_column():
    mod = _load_script()
    ddl = mod._ddl("proj")
    for col in _collector_columns():
        assert col in ddl, f"DDL is missing column written by the collector: {col}"
