"""Unit tests for TrackingStore — the read/write layer over dfs_job_summary +
dfs_task_log. Verifies batch (never per-row) writes against FakeBQClient.
"""

from __future__ import annotations

import pandas as pd

from skyward.data.dataforseo.collector.tracking import TrackingStore
from skyward.functions import generate_job_id
from tests.conftest import FakeBigQueryClient


def _store():
    bq = FakeBigQueryClient()
    return TrackingStore(bq), bq


def _scalar_params(job_config) -> dict:
    out = {}
    for p in (getattr(job_config, "query_parameters", None) or []):
        if hasattr(p, "value"):
            out[p.name] = p.value
    return out


def _tasks(n: int):
    return [{"task_id": f"t{i}", "keyword": f"kw{i}"} for i in range(n)]


# ---- submit() ----

def test_submit_batch_loads_pending_task_log_rows():
    store, bq = _store()
    jid = generate_job_id()
    store.submit(job_id=jid, endpoint="serp_google_organic", tasks=_tasks(3))

    loaded = [t for t in bq.client.loaded_tables if str(t["table_ref"]).endswith("dfs_task_log")]
    assert len(loaded) == 1, "exactly one batch load into dfs_task_log"
    df = loaded[0]["df"]
    assert len(df) == 3
    assert (df["status"] == "pending").all()
    assert (df["job_id"] == jid).all()
    assert (df["endpoint"] == "serp_google_organic").all()
    assert list(df["keyword"]) == ["kw0", "kw1", "kw2"]


def test_submit_upserts_one_summary_row_with_total():
    store, bq = _store()
    jid = generate_job_id()
    store.submit(job_id=jid, endpoint="serp_google_organic", tasks=_tasks(3))

    merges = [c for c in bq.client.queries
              if "dfs_job_summary" in c["sql"] and "MERGE" in c["sql"].upper()]
    assert len(merges) == 1, "exactly one MERGE upsert into dfs_job_summary"
    params = _scalar_params(merges[0]["job_config"])
    assert params.get("total") == 3
    assert params.get("job_id") == jid
    assert params.get("endpoint") == "serp_google_organic"


# ---- completion_pct() ----

def test_completion_pct_returns_fetched_over_total():
    store, bq = _store()
    bq.client.queue_result(pd.DataFrame([{"total_tasks": 10, "fetched_count": 7}]))
    pct = store.completion_pct(job_id=generate_job_id(), endpoint="serp_google_organic")
    assert pct == 0.7


def test_completion_pct_empty_row_is_zero():
    store, bq = _store()
    bq.client.queue_result(pd.DataFrame())  # no summary row yet
    assert store.completion_pct(job_id="x", endpoint="serp_google_organic") == 0.0


def test_completion_pct_zero_total_is_zero():
    store, bq = _store()
    bq.client.queue_result(pd.DataFrame([{"total_tasks": 0, "fetched_count": 0}]))
    assert store.completion_pct(job_id="x", endpoint="serp_google_organic") == 0.0


# ---- mark_fetched() ----

def test_mark_fetched_one_merge_and_one_summary_update():
    store, bq = _store()
    results = [
        {"task_id": "t0", "status": "fetched", "dfs_status_code": 20000, "result_rows": 30, "attempts": 1},
        {"task_id": "t1", "status": "fetched", "dfs_status_code": 20000, "result_rows": 12, "attempts": 1},
        {"task_id": "t2", "status": "failed_not_found", "dfs_status_code": 40401, "result_rows": 0, "attempts": 3},
    ]
    store.mark_fetched(endpoint="serp_google_organic", results=results)

    log_merges = [c for c in bq.client.queries
                  if "dfs_task_log" in c["sql"] and "MERGE" in c["sql"].upper()]
    assert len(log_merges) == 1, "single batched MERGE, not per-row"
    assert "UNNEST" in log_merges[0]["sql"].upper()
    # the array param carries all three rows
    arr = [p for p in log_merges[0]["job_config"].query_parameters if p.name == "rows"][0]
    assert len(arr.values) == 3

    summary_updates = [c for c in bq.client.queries
                       if "dfs_job_summary" in c["sql"] and "UPDATE" in c["sql"].upper()]
    assert len(summary_updates) == 1


def test_mark_fetched_empty_is_noop():
    store, bq = _store()
    store.mark_fetched(endpoint="serp_google_organic", results=[])
    assert bq.client.queries == []
    assert bq.client.loaded_tables == []
