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

def test_submit_records_domain_context():
    store, bq = _store()
    store.submit(job_id="x", endpoint="serp_google_organic", tasks=_tasks(2),
                 domain_id=42, domain="busbank.com")
    merge = [c for c in bq.client.queries if "dfs_job_summary" in c["sql"]][0]
    params = _scalar_params(merge["job_config"])
    assert params.get("domain_id") == 42
    assert params.get("domain") == "busbank.com"
    assert "domain_id" in merge["sql"] and "domain" in merge["sql"]


def test_completion_pct_returns_fetched_over_total():
    store, bq = _store()
    bq.client.queue_result(pd.DataFrame([{"total_tasks": 10, "fetched_count": 7}]))
    pct = store.completion_pct(job_id=generate_job_id(), endpoint="serp_google_organic")
    assert pct == 0.7


def test_completion_pct_empty_row_is_zero():
    store, bq = _store()
    bq.client.queue_result(pd.DataFrame())  # no summary row yet
    assert store.completion_pct(job_id="x", endpoint="serp_google_organic") == 0.0


def test_completion_pct_zero_total_is_complete():
    # empty submit (0 tasks) reads as complete, so the producer doesn't hang forever
    store, bq = _store()
    bq.client.queue_result(pd.DataFrame([{"total_tasks": 0, "fetched_count": 0}]))
    assert store.completion_pct(job_id="x", endpoint="serp_google_organic") == 1.0


# ---- job_status() ----

def test_job_status_returns_per_endpoint_breakdown():
    store, bq = _store()
    bq.client.queue_result(pd.DataFrame([
        {"endpoint": "serp_google_organic", "total_tasks": 10, "fetched_count": 7,
         "failed_count": 1, "status": "partial", "submitted_at": None, "last_updated": None},
        {"endpoint": "keywords_data_google_ads_search_volume", "total_tasks": 2, "fetched_count": 2,
         "failed_count": 0, "status": "done", "submitted_at": None, "last_updated": None},
    ]))
    rows = store.job_status(job_id="j")
    assert len(rows) == 2
    serp = next(r for r in rows if r["endpoint"] == "serp_google_organic")
    assert serp["total_tasks"] == 10 and serp["fetched"] == 7 and serp["failed"] == 1
    assert abs(serp["completion_pct"] - 0.7) < 1e-9 and serp["status"] == "partial"


def test_job_status_empty_when_unknown():
    store, bq = _store()
    bq.client.queue_result(pd.DataFrame())
    assert store.job_status(job_id="nope") == []


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


def test_submit_dedupes_duplicate_task_ids_within_call():
    store, bq = _store()
    tasks = [{"task_id": "t0", "keyword": "a"}, {"task_id": "t0", "keyword": "a2"},
             {"task_id": "t1", "keyword": "b"}]
    store.submit(job_id="x", endpoint="serp_google_organic", tasks=tasks)
    loaded = [t for t in bq.client.loaded_tables if str(t["table_ref"]).endswith("dfs_task_log")][0]["df"]
    assert sorted(loaded["task_id"]) == ["t0", "t1"]  # 3 in -> 2 unique
    merge = [c for c in bq.client.queries if "dfs_job_summary" in c["sql"]][0]
    assert _scalar_params(merge["job_config"]).get("total") == 2


def test_submit_empty_tasks_skips_load_writes_zero_total():
    store, bq = _store()
    store.submit(job_id="x", endpoint="serp_google_organic", tasks=[])
    # no task_log load for zero tasks
    assert [t for t in bq.client.loaded_tables if str(t["table_ref"]).endswith("dfs_task_log")] == []
    # one summary MERGE with total=0
    merges = [c for c in bq.client.queries if "dfs_job_summary" in c["sql"]]
    assert len(merges) == 1
    assert _scalar_params(merges[0]["job_config"]).get("total") == 0


# ---- lookup_tasks() ----

def test_lookup_tasks_maps_to_job_and_domain():
    store, bq = _store()
    bq.client.queue_result(pd.DataFrame([
        {"task_id": "t0", "job_id": "j1", "keyword": "k0", "domain_id": 7, "domain": "x.com"},
        {"task_id": "t1", "job_id": "j1", "keyword": "k1", "domain_id": 7, "domain": "x.com"},
    ]))
    m = store.lookup_tasks(endpoint="serp_google_organic", task_ids=["t0", "t1", "t2"])
    assert m["t0"]["job_id"] == "j1" and m["t0"]["domain_id"] == 7 and m["t0"]["keyword"] == "k0"
    assert m["t1"]["domain"] == "x.com"
    assert "t2" not in m  # untracked -> caller attributes to sentinel


def test_lookup_tasks_empty_ids_is_noop():
    store, bq = _store()
    assert store.lookup_tasks(endpoint="x", task_ids=[]) == {}
    assert bq.client.queries == []


# ---- resilience: retries ----

class _FakeResult:
    def __init__(self, df=None):
        self._df = pd.DataFrame() if df is None else df

    def result(self):
        return self

    def to_dataframe(self):
        return self._df


class _FlakyClient:
    """Raises `exc` on the first `fail_times` query() calls, then succeeds."""

    def __init__(self, fail_times, exc):
        self.project = "data-hub-468216"
        self._fail_times = fail_times
        self._exc = exc
        self.calls = 0

    def query(self, sql, job_config=None):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc
        return _FakeResult()

    def load_table_from_dataframe(self, df, ref, job_config=None):
        return _FakeResult()


class _FlakyBQ:
    def __init__(self, fail_times, exc):
        self.client = _FlakyClient(fail_times, exc)


def _flaky_store(fail_times, exc, **kw):
    bq = _FlakyBQ(fail_times, exc)
    return TrackingStore(bq, retry_base_delay=0.0, _sleep=lambda *_: None, **kw), bq


def test_retry_recovers_from_transient_error():
    from google.api_core import exceptions as gexc
    store, bq = _flaky_store(2, gexc.ServiceUnavailable("503 backend"))
    store.completion_pct(job_id="x", endpoint="serp_google_organic")  # must not raise
    assert bq.client.calls == 3  # 2 failures + 1 success


def test_retry_raises_after_exhaustion():
    from google.api_core import exceptions as gexc
    import pytest
    store, bq = _flaky_store(99, gexc.ServiceUnavailable("503"), max_retries=3)
    with pytest.raises(gexc.ServiceUnavailable):
        store.completion_pct(job_id="x", endpoint="serp_google_organic")
    assert bq.client.calls == 3


def test_non_retryable_error_raises_immediately():
    import pytest
    store, bq = _flaky_store(99, ValueError("bad sql"))
    with pytest.raises(ValueError):
        store.completion_pct(job_id="x", endpoint="serp_google_organic")
    assert bq.client.calls == 1


def test_serialize_conflict_message_is_retryable():
    store, bq = _flaky_store(1, RuntimeError("Could not serialize access to table due to concurrent update"))
    store.completion_pct(job_id="x", endpoint="serp_google_organic")  # must not raise
    assert bq.client.calls == 2
