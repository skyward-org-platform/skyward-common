"""Collector service: allowlist build, drain cycle (canonical write + status update +
_unattributed), and the forever-loop's error resilience. Stubs DFS and BQ.
"""

from __future__ import annotations

import pandas as pd

from skyward.data.dataforseo import ClientConfig, DataForSEOClient
from skyward.data.dataforseo.collector import service
from skyward.data.dataforseo.collector.allowlist import EndpointHandler, build_allowlist
from tests.conftest import FakeBigQueryClient


class _FakeClient:
    BASE_URL = "https://api.dataforseo.com/v3"

    def __init__(self, bq, get_fn):
        self.bq_client = bq
        self._get_fn = get_fn

    def _get(self, url):
        return self._get_fn(url)


class _FakeStore:
    def __init__(self, lookup):
        self._lookup = lookup
        self.marked = None
        self.completed = []  # jobs returned by claim_completed_jobs

    def lookup_tasks(self, *, endpoint, task_ids):
        return {k: v for k, v in self._lookup.items() if k in task_ids}

    def mark_fetched(self, *, endpoint, results, job_ids=None):
        self.marked = (endpoint, results, job_ids)

    def claim_completed_jobs(self, *, endpoint):
        return self.completed


class _AccumStore(_FakeStore):
    """Like _FakeStore but records every mark_fetched call (incremental flushing makes >1)."""

    def __init__(self, lookup):
        super().__init__(lookup)
        self.marks = []

    def mark_fetched(self, *, endpoint, results, job_ids=None):
        self.marks.append({"endpoint": endpoint,
                           "results": list(results),
                           "job_ids": list(job_ids or [])})
        self.marked = (endpoint, results, job_ids)


def _handler():
    return EndpointHandler(
        key="serp_google_organic",
        ready_url="serp/google/organic/tasks_ready",
        get_url="serp/google/organic/task_get/advanced",
        canonical_table="serp-google-organic",
        parse=lambda raw, kw: pd.DataFrame(
            [{"keyword": kw or raw["tasks"][0]["id"], "rank": 1, "task_id": raw["tasks"][0]["id"]}]),
        cast=lambda df: df,
    )


def _ready_then_get(handler, ready_ids):
    def _get(url):
        if url.endswith(handler.ready_url):
            return {"tasks": [{"result": [{"id": i} for i in ready_ids]}]}
        tid = url.rsplit("/", 1)[1]
        return {"status_code": 20000, "tasks": [{"status_code": 20000, "id": tid, "result": [{"x": 1}]}]}
    return _get


def test_run_cycle_writes_canonical_marks_and_attributes_unknown():
    bq = FakeBigQueryClient()
    h = _handler()
    client = _FakeClient(bq, _ready_then_get(h, ["t0", "t1", "unk"]))
    store = _FakeStore({
        "t0": {"job_id": "j1", "keyword": "k0", "domain_id": 7, "domain": "x.com"},
        "t1": {"job_id": "j1", "keyword": "k1", "domain_id": 7, "domain": "x.com"},
    })

    # grace_s=0: an untracked ("unk") task is bucketed to the sentinel immediately (no defer)
    stats = service.run_cycle(client, store, h, grace_s=0.0)
    assert stats == {"endpoint": "serp_google_organic", "ready": 3, "skipped": 0,
                     "deferred": 0, "fetched": 3, "failed": 0}

    loaded = [t for t in bq.client.loaded_tables if str(t["table_ref"]).endswith("serp-google-organic")]
    assert len(loaded) == 1
    df = loaded[0]["df"]
    assert len(df) == 3
    assert {"job_id", "domain_id", "domain", "endpoint_mode", "upload_id", "ingest_timestamp"}.issubset(df.columns)
    assert (df["endpoint_mode"] == "standard").all()
    unk = df[df["task_id"] == "unk"].iloc[0]
    assert unk["job_id"] == "d4s_standard_unattributed" and pd.isna(unk["domain_id"])

    ep, results, job_ids = store.marked
    assert ep == "serp_google_organic"
    assert {r["task_id"] for r in results} == {"t0", "t1", "unk"}
    assert all(r["status"] == "fetched" for r in results)
    # touched jobs are passed through so the summary recompute can be scoped
    assert set(job_ids) == {"j1", "d4s_standard_unattributed"}


def test_run_cycle_flushes_incrementally_every_n_tasks():
    # With flush_every=2 and 5 ready tasks, the cycle commits in batches of 2,2,1 instead of
    # one big end-of-cycle write — this is the memory-bounding + progress-persistence change.
    bq = FakeBigQueryClient()
    h = _handler()
    ids = [f"t{i}" for i in range(5)]
    client = _FakeClient(bq, _ready_then_get(h, ids))
    store = _AccumStore({i: {"job_id": "j1", "keyword": i, "domain_id": 1, "domain": "x.com"} for i in ids})

    stats = service.run_cycle(client, store, h, flush_every=2)
    assert stats["fetched"] == 5

    loaded = [t for t in bq.client.loaded_tables if str(t["table_ref"]).endswith("serp-google-organic")]
    assert [len(t["df"]) for t in loaded] == [2, 2, 1]  # three flushes, batched
    assert len(store.marks) == 3
    # every task is marked exactly once across the flushes, and each flush scopes its job_ids
    marked_ids = [r["task_id"] for call in store.marks for r in call["results"]]
    assert sorted(marked_ids) == sorted(ids)
    assert all(call["job_ids"] == ["j1"] for call in store.marks)


def test_run_cycle_flush_appends_canonical_before_marking(monkeypatch):
    # Crash-safety invariant: within each flush, canonical rows are written BEFORE the task is
    # marked fetched, so a crash between them never leaves 'fetched' rows with no data.
    order = []
    real_append = service._append_canonical

    def spy_append(bq_client, table, df):
        order.append(("append", len(df)))
        return real_append(bq_client, table, df)

    monkeypatch.setattr(service, "_append_canonical", spy_append)

    bq = FakeBigQueryClient()
    h = _handler()
    ids = [f"t{i}" for i in range(3)]
    client = _FakeClient(bq, _ready_then_get(h, ids))
    store = _AccumStore({i: {"job_id": "j1", "keyword": i, "domain_id": 1, "domain": "x.com"} for i in ids})

    def marker(*, endpoint, results, job_ids=None):
        order.append(("mark", len(results)))

    monkeypatch.setattr(store, "mark_fetched", marker)

    service.run_cycle(client, store, h, flush_every=2)
    assert order == [("append", 2), ("mark", 2), ("append", 1), ("mark", 1)]


def test_run_cycle_skips_already_resolved_tasks():
    # A task DFS still lists in tasks_ready but that we already fetched must NOT be re-fetched
    # (the double-write guard). It's skipped: no canonical row, not in the mark_fetched results.
    bq = FakeBigQueryClient()
    h = _handler()
    client = _FakeClient(bq, _ready_then_get(h, ["t0", "t1"]))
    store = _FakeStore({
        "t0": {"job_id": "j1", "keyword": "k0", "domain_id": 7, "domain": "x.com", "resolved": True},
        "t1": {"job_id": "j1", "keyword": "k1", "domain_id": 7, "domain": "x.com", "resolved": False},
    })

    stats = service.run_cycle(client, store, h)
    assert stats == {"endpoint": "serp_google_organic", "ready": 2, "skipped": 1,
                     "deferred": 0, "fetched": 1, "failed": 0}
    df = [t for t in bq.client.loaded_tables
          if str(t["table_ref"]).endswith("serp-google-organic")][0]["df"]
    assert set(df["task_id"]) == {"t1"}  # t0 skipped, only t1 written
    _, results, _ = store.marked
    assert {r["task_id"] for r in results} == {"t1"}  # t0 not re-marked


def test_run_cycle_defers_unattributed_within_grace():
    # A ready task with no dfs_task_log row yet (producer-write race) is DEFERRED within the
    # grace window — not fetched, not bucketed to the sentinel — so it can attribute correctly
    # once the producer's write lands.
    bq = FakeBigQueryClient()
    h = _handler()
    client = _FakeClient(bq, _ready_then_get(h, ["unk"]))
    store = _FakeStore({})
    pending, clock = {}, [1000.0]
    stats = service.run_cycle(client, store, h, pending=pending, grace_s=120.0, now=lambda: clock[0])
    assert stats["deferred"] == 1 and stats["fetched"] == 0
    assert "unk" in pending  # grace state retained for next cycle
    assert bq.client.loaded_tables == []  # nothing written while deferred
    assert store.marked is None  # nothing processed -> no mark_fetched call (empty flush is a no-op)


def test_run_cycle_attributes_unattributed_after_grace():
    # Once a task has been unattributable past grace_s, it's a genuine orphan -> sentinel.
    bq = FakeBigQueryClient()
    h = _handler()
    client = _FakeClient(bq, _ready_then_get(h, ["unk"]))
    store = _FakeStore({})
    clock = [1000.0]
    pending = {"unk": 1000.0 - 200}  # first seen 200s ago, grace is 120s
    stats = service.run_cycle(client, store, h, pending=pending, grace_s=120.0, now=lambda: clock[0])
    assert stats["deferred"] == 0 and stats["fetched"] == 1
    df = [t for t in bq.client.loaded_tables
          if str(t["table_ref"]).endswith("serp-google-organic")][0]["df"]
    assert df.iloc[0]["job_id"] == "d4s_standard_unattributed"
    assert "unk" not in pending  # cleared after bucketing


def test_run_cycle_fires_job_complete_for_finished_jobs():
    bq = FakeBigQueryClient()
    h = _handler()
    client = _FakeClient(bq, _ready_then_get(h, ["t0"]))
    store = _FakeStore({"t0": {"job_id": "j1", "keyword": "k", "domain_id": None, "domain": None}})
    store.completed = [{"job_id": "j1", "succeeded": 1, "failed": 0}]
    al = _FakeAlerter()
    service.run_cycle(client, store, h, alerter=al)
    assert ("job_complete", "j1", 1, 0) in al.events
    # the dedup backstop fires for the completed job, scoped to its job_id
    dedup = [q for q in bq.client.queries
             if "DELETE" in q["sql"].upper() and "serp-google-organic" in q["sql"]]
    assert len(dedup) == 1
    jp = [p for p in dedup[0]["job_config"].query_parameters if p.name == "job_id"][0]
    assert jp.value == "j1"


def test_dedup_canonical_prunes_and_keeps_latest_upload():
    bq = FakeBigQueryClient()
    service._dedup_canonical(bq, "serp-google-organic", "j1")
    q = [c for c in bq.client.queries if "DELETE" in c["sql"].upper()][0]
    sql = q["sql"]
    assert "serp-google-organic" in sql
    assert "INTERVAL 2 DAY" in sql and "ingest_timestamp" in sql  # partition pruning
    assert "U.task_id = T.task_id" in sql and "U.upload_id > T.upload_id" in sql  # keep latest
    jp = [p for p in q["job_config"].query_parameters if p.name == "job_id"][0]
    assert jp.value == "j1"


def test_run_cycle_no_alerter_skips_job_complete():
    bq = FakeBigQueryClient()
    h = _handler()
    client = _FakeClient(bq, _ready_then_get(h, ["t0"]))
    store = _FakeStore({"t0": {"job_id": "j1", "keyword": "k", "domain_id": None, "domain": None}})
    store.completed = [{"job_id": "j1", "succeeded": 1, "failed": 0}]
    service.run_cycle(client, store, h)  # no alerter -> no claim/alert, no error


def test_run_cycle_no_ready_is_noop():
    bq = FakeBigQueryClient()
    h = _handler()
    client = _FakeClient(bq, lambda url: {"tasks": [{"result": []}]})
    stats = service.run_cycle(client, _FakeStore({}), h)
    assert stats["ready"] == 0 and stats["fetched"] == 0
    assert bq.client.loaded_tables == []


def test_run_cycle_classifies_not_found_and_network_failure():
    bq = FakeBigQueryClient()
    h = _handler()

    def _get(url):
        if url.endswith(h.ready_url):
            return {"tasks": [{"result": [{"id": "t0"}, {"id": "t1"}]}]}
        tid = url.rsplit("/", 1)[1]
        if tid == "t0":
            return {"status_code": 20000, "tasks": [{"status_code": 40401, "id": "t0"}]}  # not found
        return None  # network failure

    client = _FakeClient(bq, _get)
    store = _FakeStore({
        "t0": {"job_id": "j", "keyword": "k", "domain_id": None, "domain": None},
        "t1": {"job_id": "j", "keyword": "k", "domain_id": None, "domain": None},
    })
    stats = service.run_cycle(client, store, h)
    assert stats["fetched"] == 0 and stats["failed"] == 2
    statuses = {r["task_id"]: r["status"] for r in store.marked[1]}
    assert statuses["t0"] == "failed_not_found"
    assert statuses["t1"] == "failed_other"
    assert bq.client.loaded_tables == []  # nothing to write


def test_run_forever_loops_and_survives_endpoint_errors(monkeypatch):
    calls = []

    def fake_run_cycle(client, store, handler, **kw):
        calls.append(handler.key)
        if handler.key == "boom":
            raise RuntimeError("kaboom")
        return {"endpoint": handler.key, "ready": 1, "fetched": 1, "failed": 0}

    monkeypatch.setattr(service, "run_cycle", fake_run_cycle)
    boom = EndpointHandler(key="boom", ready_url="r", get_url="g", canonical_table="c",
                           parse=lambda *a: None, cast=lambda df: df)
    service.run_forever(None, None, {"serp_google_organic": _handler(), "boom": boom},
                        alerter=_FakeAlerter(), poll_interval=0, max_cycles=2, sleep=lambda s: None)
    assert calls.count("serp_google_organic") == 2
    assert calls.count("boom") == 2  # error in 'boom' did not kill the loop


class _FakeAlerter:
    def __init__(self):
        self.events = []

    def fire(self, key, **kw):
        self.events.append(("fire", key))

    def resolve(self, key, **kw):
        self.events.append(("resolve", key))

    def startup(self):
        self.events.append(("startup",))

    def shutdown(self, reason="x"):
        self.events.append(("shutdown",))

    def crash(self, message):
        self.events.append(("crash",))

    def heartbeat(self):
        self.events.append(("heartbeat",))

    def job_complete(self, *, job_id, endpoint, succeeded, failed):
        self.events.append(("job_complete", job_id, succeeded, failed))


def test_run_forever_alerts_on_dfs_unreachable_then_recovers(monkeypatch):
    from skyward.data.dataforseo.collector.service import CollectorDfsError
    seq = [CollectorDfsError("serp_google_organic"),
           {"endpoint": "serp_google_organic", "ready": 0, "fetched": 0, "failed": 0}]

    def fake_rc(client, store, handler, **k):
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(service, "run_cycle", fake_rc)
    al = _FakeAlerter()
    service.run_forever(None, None, {"serp_google_organic": _handler()}, alerter=al,
                        max_cycles=2, poll_interval=0, sleep=lambda s: None)
    assert ("fire", "dfs:serp_google_organic") in al.events
    assert ("resolve", "dfs:serp_google_organic") in al.events
    assert ("heartbeat",) in al.events


def test_run_forever_classifies_bigquery_error(monkeypatch):
    from google.api_core import exceptions as gexc
    monkeypatch.setattr(service, "run_cycle",
                        lambda *a, **k: (_ for _ in ()).throw(gexc.ServiceUnavailable("503")))
    al = _FakeAlerter()
    service.run_forever(None, None, {"serp_google_organic": _handler()}, alerter=al,
                        max_cycles=1, poll_interval=0, sleep=lambda s: None)
    assert ("fire", "bigquery:serp_google_organic") in al.events


def test_run_forever_alerts_high_failure_rate(monkeypatch):
    monkeypatch.setattr(service, "run_cycle",
                        lambda *a, **k: {"endpoint": "serp_google_organic", "ready": 4,
                                         "fetched": 1, "failed": 3})
    al = _FakeAlerter()
    service.run_forever(None, None, {"serp_google_organic": _handler()}, alerter=al,
                        max_cycles=1, poll_interval=0, sleep=lambda s: None)
    assert ("fire", "failrate:serp_google_organic") in al.events


def test_run_forever_should_stop_breaks_before_cycle(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "run_cycle",
                        lambda *a, **k: calls.append(1) or {"endpoint": "e", "ready": 0, "fetched": 0, "failed": 0})
    service.run_forever(None, None, {"serp_google_organic": _handler()}, alerter=_FakeAlerter(),
                        max_cycles=None, poll_interval=0, sleep=lambda s: None, should_stop=lambda: True)
    assert calls == []


def test_run_forever_sleep_is_interruptible_by_should_stop(monkeypatch):
    monkeypatch.setattr(service, "run_cycle",
                        lambda *a, **k: {"endpoint": "e", "ready": 0, "fetched": 0, "failed": 0})
    flag = {"stop": False}
    steps = {"n": 0}

    def fake_sleep(_s):
        steps["n"] += 1
        flag["stop"] = True  # request stop after the first 1s step

    service.run_forever(None, None, {"serp_google_organic": _handler()}, alerter=_FakeAlerter(),
                        max_cycles=None, poll_interval=30, sleep=fake_sleep,
                        should_stop=lambda: flag["stop"])
    assert steps["n"] <= 2  # broke out mid-sleep, didn't wait the full 30s


def test_build_allowlist_has_both_standard_endpoints():
    client = DataForSEOClient(username="u", password="p", bq_client=FakeBigQueryClient(),
                              config=ClientConfig())
    al = build_allowlist(client)
    assert set(al) == {"serp_google_organic", "keywords_data_google_ads_search_volume"}
    assert al["serp_google_organic"].canonical_table == "serp-google-organic"
    assert "tasks_ready" in al["serp_google_organic"].ready_url
