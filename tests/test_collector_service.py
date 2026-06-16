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

    def lookup_tasks(self, *, endpoint, task_ids):
        return {k: v for k, v in self._lookup.items() if k in task_ids}

    def mark_fetched(self, *, endpoint, results):
        self.marked = (endpoint, results)


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

    stats = service.run_cycle(client, store, h)
    assert stats == {"endpoint": "serp_google_organic", "ready": 3, "fetched": 3, "failed": 0}

    loaded = [t for t in bq.client.loaded_tables if str(t["table_ref"]).endswith("serp-google-organic")]
    assert len(loaded) == 1
    df = loaded[0]["df"]
    assert len(df) == 3
    assert {"job_id", "domain_id", "domain", "endpoint_mode", "upload_id", "ingest_timestamp"}.issubset(df.columns)
    assert (df["endpoint_mode"] == "standard").all()
    unk = df[df["task_id"] == "unk"].iloc[0]
    assert unk["job_id"] == "d4s_standard_unattributed" and pd.isna(unk["domain_id"])

    ep, results = store.marked
    assert ep == "serp_google_organic"
    assert {r["task_id"] for r in results} == {"t0", "t1", "unk"}
    assert all(r["status"] == "fetched" for r in results)


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
                        poll_interval=0, max_cycles=2, sleep=lambda s: None)
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
