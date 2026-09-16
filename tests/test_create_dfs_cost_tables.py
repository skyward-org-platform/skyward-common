"""Keeps the v1.6.1 DDL in sync with the code that writes each table."""

import importlib.util
from pathlib import Path

from skyward.data.dataforseo.cost_rates import Rate
from skyward.data.dataforseo.locations import LOCATION_COLUMNS

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_dfs_cost_tables.py"


def _load():
    spec = importlib.util.spec_from_file_location("create_dfs_cost_tables", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


COST_LOG_COLS = {
    "job_id", "upload_id", "task_id", "endpoint", "endpoint_mode", "call_type", "attempt",
    "http_status", "dfs_status_code", "items_sent", "result_rows", "price_inputs",
    "cost_usd", "client_id", "requested_at", "ingest_timestamp",
}
JOB_RUNS_COLS = {
    "job_id", "endpoint", "endpoint_mode", "event", "status", "planned_requests",
    "planned_items", "planned_max_rows", "estimate_max_usd", "estimate_avg_usd",
    "balance_at_start", "balance_override", "error", "client_id", "ingest_timestamp",
}


def test_cost_log_matches_writer_and_is_partitioned():
    ddl = _load().COST_LOG_DDL
    for c in COST_LOG_COLS:
        assert f"  {c} " in ddl, c
    assert "PARTITION BY DATE(ingest_timestamp)" in ddl
    assert "CLUSTER BY job_id, endpoint" in ddl
    assert "data-hub-468216.DataForSEO.cost_log" in ddl


def test_job_runs_matches_writer():
    ddl = _load().JOB_RUNS_DDL
    for c in JOB_RUNS_COLS:
        assert f"  {c} " in ddl, c
    assert "CLUSTER BY job_id" in ddl


def test_cost_estimates_matches_rate_fields():
    ddl = _load().COST_ESTIMATES_DDL
    for name in Rate.__dataclass_fields__:
        assert f"  {name} " in ddl, name


def test_locations_matches_catalog_columns():
    ddl = _load().LOCATIONS_DDL
    for c in LOCATION_COLUMNS:
        assert f"  {c} " in ddl, c


def test_every_column_has_description():
    m = _load()
    for ddl, cols in ((m.COST_LOG_DDL, COST_LOG_COLS), (m.JOB_RUNS_DDL, JOB_RUNS_COLS)):
        assert ddl.count("OPTIONS(description=") >= len(cols)


def test_views_read_the_right_sources():
    m = _load()
    assert "DataForSEO.cost_log" in m.JOB_COSTS_VIEW
    assert "DataForSEO.job_runs" in m.JOB_PROGRESS_VIEW
    assert "DataForSEO.dfs_job_summary" in m.JOB_PROGRESS_VIEW
    assert "INTERVAL 90 DAY" in m.ENDPOINT_COST_ACTUALS_VIEW
    assert set(m.LOCATION_VIEWS) == {
        "locations_countries", "locations_us_states", "locations_us_dma",
        "locations_us_cities", "locations_us_counties", "locations_us_postal_codes",
    }
    assert "location_type = 'DMA Region'" in m.LOCATION_VIEWS["locations_us_dma"]


def test_tables_are_created_before_views():
    names = [name for name, _ in _load().ALL_STATEMENTS]
    assert names.index("locations") < names.index("locations_countries")
    assert names.index("cost_log") < names.index("job_costs")
    assert names.index("job_runs") < names.index("job_progress")
