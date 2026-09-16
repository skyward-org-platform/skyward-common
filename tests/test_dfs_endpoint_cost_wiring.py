import asyncio
import itertools

import pytest

from skyward.data.dataforseo import DataForSEOClient
from skyward.functions import generate_job_id
from tests.conftest import FakeBigQueryClient


class _Resp:
    def __init__(self, data):
        self._data, self.status_code = data, 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def _items_for(url, task):
    if "keyword_overview" in url:
        return [{"keyword": k, "keyword_info": {"search_volume": 10}} for k in task["keywords"]]
    if "search_intent" in url:
        return [{"keyword": k, "keyword_intent": {"label": "informational", "probability": 0.9}}
                for k in task["keywords"]]
    if "bulk_pages_summary" in url:
        return [{"url": t, "backlinks": 1} for t in task["targets"]]
    if "ranked_keywords" in url:
        start = task.get("offset", 0)
        return [{"keyword_data": {"keyword": f"kw{start + i}", "keyword_info": {"search_volume": 10}},
                 "ranked_serp_element": {"serp_item": {"url": f"https://a.com/{start + i}", "rank_group": 1}}}
                for i in range(task.get("limit", 1))]
    if "search_volume" in url:
        return None
    return [{"keyword": task.get("keyword", "x")}]


class FakeDfsSession:
    """Answers any DFS POST with one task per payload entry and a unique task id."""

    def __init__(self, cost=0.0124):
        self.calls: list[tuple[str, list]] = []
        self.posted: dict[str, dict] = {}
        self._ids = itertools.count(1)
        self._cost = cost

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        tasks = []
        for task in json:
            tid = f"task-{next(self._ids)}"
            if url.endswith("/task_post"):
                self.posted[tid] = task
                tasks.append({"id": tid, "status_code": 20100, "cost": self._cost,
                              "data": {"tag": task.get("tag") or task.get("keyword", "")},
                              "result": None})
                continue
            items = _items_for(url, task)
            if items is None:  # search volume live: result is the keyword rows
                result = [{"keyword": k, "search_volume": 5} for k in task["keywords"]]
            else:
                result = [{"items": items}]
            tasks.append({"id": tid, "status_code": 20000, "cost": self._cost,
                          "data": task, "result": result})
        return _Resp({"status_code": 20000, "tasks": tasks})


@pytest.fixture
def bq():
    return FakeBigQueryClient()


@pytest.fixture
def client(bq):
    c = DataForSEOClient(username="u", password="p", bq_client=bq)
    c._session = FakeDfsSession()
    return c


def cost_rows(bq):
    return [r for ins in bq.client.inserted_rows if ins["table"].endswith(".cost_log")
            for r in ins["rows"]]


def assert_linked(bq):
    """Every data row's upload_id equals the upload_id on its task's cost row."""
    by_task = {r["task_id"]: r["upload_id"] for r in cost_rows(bq)}
    loaded = [l for l in bq.client.loaded_tables if "task_id" in l["df"].columns]
    assert loaded, "no data was uploaded"
    for load in loaded:
        for tid, uid in zip(load["df"]["task_id"], load["df"]["upload_id"]):
            assert by_task[tid] == uid


def test_keyword_overview_batches_log_one_row_per_request(client, bq):
    kws = [f"k{i}" for i in range(1500)]
    df = asyncio.run(client.dataforseo_labs_google_keyword_overview.live_all(
        kws, domain=None, job_id=generate_job_id(), batch_delay=0, upload_batch_rows=600))
    assert len(df) == 1500
    assert len(cost_rows(bq)) == 3 == len(client._session.calls)
    assert {r["items_sent"] for r in cost_rows(bq)} == {700, 100}
    assert_linked(bq)


def test_search_intent(client, bq):
    kws = [f"k{i}" for i in range(2100)]
    asyncio.run(client.dataforseo_labs_google_search_intent.live_all(
        kws, domain=None, job_id=generate_job_id(), batch_delay=0))
    assert len(cost_rows(bq)) == 3
    assert_linked(bq)


def test_bulk_pages_summary(client, bq):
    urls = [f"https://a.com/{i}" for i in range(1200)]
    asyncio.run(client.backlinks_bulk_pages_summary.live_all(
        urls, domain=None, job_id=generate_job_id(), batch_delay=0))
    assert len(cost_rows(bq)) == 2
    assert_linked(bq)


def test_ranked_keywords_paginates_and_links(client, bq):
    df = asyncio.run(client.dataforseo_labs_google_ranked_keywords.live_all(
        ["a.com", "b.com"], domain=None, job_id=generate_job_id(),
        limit_per_domain=6, page_size=3, upload_batch_rows=4))
    assert len(df) == 12
    rows = cost_rows(bq)
    assert len(rows) == 4
    assert {r["call_type"] for r in rows} == {"live_page"}
    assert_linked(bq)


import skyward.data.dataforseo.endpoints.keywords_data_google_ads_search_volume as sv_mod
from skyward.data.dataforseo import ClientConfig


def _stub_task_get(client):
    session = client._session

    def _get(url, *a, **k):
        if url.endswith("/tasks_ready"):
            return {"tasks": [{"result": [{"id": tid} for tid in session.posted]}]}
        tid = url.rsplit("/", 1)[-1]
        task = session.posted[tid]
        return {"tasks": [{"id": tid, "status_code": 20000, "cost": 0, "data": task,
                           "result": [{"keyword": k, "search_volume": 1} for k in task["keywords"]]}]}

    client._get = _get


@pytest.fixture
def std_client(bq):
    c = DataForSEOClient(username="u", password="p", bq_client=bq,
                         config=ClientConfig(task_poll_interval=0))
    c._session = FakeDfsSession(cost=0.06)
    _stub_task_get(c)
    return c


def test_search_volume_live_all_logs_per_task(client, bq):
    kws = [f"k{i}" for i in range(1500)]
    df = asyncio.run(client.keywords_data_google_ads_search_volume.live_all(
        kws, domain=None, job_id=generate_job_id(), batch_delay=0, language_code="es"))
    assert len(df) == 1500
    rows = cost_rows(bq)
    assert len(rows) == 2
    assert all('"language_code": "es"' in r["price_inputs"] for r in rows)
    assert_linked(bq)


def test_search_volume_post_all_legacy_logs_task_post_only(std_client, bq):
    kws = [f"k{i}" for i in range(1500)]
    df = asyncio.run(std_client.keywords_data_google_ads_search_volume.post_all(
        kws, job_id=generate_job_id(), keywords_per_task=1000, use_collector=False))
    assert len(df) == 1500
    rows = cost_rows(bq)
    assert len(rows) == 2
    assert {r["call_type"] for r in rows} == {"task_post"}
    assert {r["endpoint_mode"] for r in rows} == {"standard"}
    assert {r["upload_id"] for r in rows} == {None}
    assert len([l for l in bq.client.loaded_tables if "task_id" in l["df"].columns]) == 1


def test_search_volume_post_single_batch(std_client, bq):
    df = std_client.keywords_data_google_ads_search_volume.post(
        ["a", "b"], job_id=generate_job_id(), language_code="es")
    assert len(df) == 2
    assert len(cost_rows(bq)) == 1
    assert std_client._session.calls[0][1][0]["language_code"] == "es"


def test_search_volume_collector_path_logs_submit_cost(std_client, bq, monkeypatch):
    monkeypatch.setattr(sv_mod, "submit_and_wait",
                        lambda **k: {"total_tasks": len(k["posted_tasks"]), "proceeded": True})
    summary = asyncio.run(std_client.keywords_data_google_ads_search_volume.post_all(
        [f"k{i}" for i in range(250)], job_id=generate_job_id(), keywords_per_task=100,
        use_collector=True))
    assert summary["total_tasks"] == 3
    rows = cost_rows(bq)
    assert len(rows) == 3 and {r["call_type"] for r in rows} == {"task_post"}
    assert [l for l in bq.client.loaded_tables if "task_id" in l["df"].columns] == []


import skyward.data.dataforseo.endpoints.serp_google_organic as serp_mod


class _SerpGetSession:
    """task_get answers for tasks the FakeDfsSession accepted; repeats the task cost like DFS."""

    def __init__(self, posting_session):
        self._s = posting_session

    def get(self, url, timeout=None):
        tid = url.rsplit("/", 1)[-1]
        task = self._s.posted[tid]
        return _Resp({"tasks": [{"id": tid, "status_code": 20000, "cost": 0.0006, "data": task,
                                 "result": [{"items": [{"type": "organic", "rank_absolute": 1,
                                                        "url": "https://x.com"}]}]}]})


@pytest.fixture
def serp_client(bq):
    c = DataForSEOClient(username="u", password="p", bq_client=bq,
                         config=ClientConfig(task_poll_interval=0))
    c._session = FakeDfsSession(cost=0.0006)
    getter = _SerpGetSession(c._session)
    c._get = lambda url, session=None, max_retries=None, retry_delay=None: getter.get(url).json()
    c._create_session = lambda: _SerpGetSession(c._session)
    return c


def test_serp_post_logs_task_post_cost_once(serp_client, bq):
    df = serp_client.serp_google_organic.post(["a", "b"], domain=None, job_id=generate_job_id())
    assert len(df) == 2
    rows = cost_rows(bq)
    assert len(rows) == 2
    assert {r["call_type"] for r in rows} == {"task_post"}
    assert round(sum(r["cost_usd"] for r in rows), 6) == 0.0012   # task_get echo not counted


def test_serp_post_all_legacy_saves_windows_and_logs_every_task(serp_client, bq):
    kws = [f"kw{i}" for i in range(150)]
    results_df, failed_df = serp_client.serp_google_organic._post_all_sync(
        kws, domain=None, job_id=generate_job_id(), batch_size=100, num_workers=2,
        max_wait=60, upload_batch_rows=50)
    assert len(results_df) == 150 and failed_df.empty
    rows = cost_rows(bq)
    assert len(rows) == 150
    assert {r["endpoint_mode"] for r in rows} == {"standard"}
    data_loads = [l for l in bq.client.loaded_tables if "task_id" in l["df"].columns]
    assert len(data_loads) == 3
    assert sum(len(l["df"]) for l in data_loads) == 150


def test_serp_legacy_mid_run_balance_stop_reaches_the_caller(serp_client, bq, monkeypatch):
    # run.add_rows() can trigger a window save, and a mid-run balance check on that save
    # can raise InsufficientBalanceError. That is a real stop signal for the whole run,
    # not a per-task failure and not something a worker thread should merely log and
    # swallow -- the caller of post_all/_post_all_sync must receive it. A single worker
    # processes two keywords in order: the first is collected normally (and must still
    # be saved), the second trips the balance stop.
    from skyward.data.dataforseo.exceptions import InsufficientBalanceError
    from skyward.data.dataforseo.run import RunContext

    real_add_rows = RunContext.add_rows
    calls = {"n": 0}

    def fake_add_rows(self, df):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_add_rows(self, df)
        err = InsufficientBalanceError(
            "low balance mid-run", job_id="j", endpoint="serp_google_organic",
            balance=0.0, required=1.0, upload_ids=[], completed_targets=[],
            remaining_targets=[],
        )
        # Mirrors what the real _after_save balance recheck does: stash the stop cause
        # on the run before raising, so close() below can report it.
        self._stop_error = err
        raise err

    monkeypatch.setattr(RunContext, "add_rows", fake_add_rows)

    job_id = generate_job_id()
    with pytest.raises(InsufficientBalanceError):
        serp_client.serp_google_organic._post_all_sync(
            ["kw0", "kw1"], domain=None, job_id=job_id, batch_size=1, num_workers=1,
            max_wait=60)

    assert calls["n"] == 2

    # The row collected before the stop was still saved -- run.close(quiet=True) flushes
    # any buffered rows regardless of the stop.
    data_loads = [l for l in bq.client.loaded_tables if "task_id" in l["df"].columns]
    assert sum(len(l["df"]) for l in data_loads) == 1

    end_rows = [r for ins in bq.client.inserted_rows if ins["table"].endswith(".job_runs")
                for r in ins["rows"] if r["job_id"] == job_id and r["event"] == "end"]
    assert len(end_rows) == 1
    assert end_rows[0]["status"] == "stopped_low_balance"


def test_serp_legacy_max_wait_timeout_returns_tuple_without_raising(serp_client, bq, monkeypatch):
    # The ordinary timeout path (no balance stop, just tasks that never come back within
    # max_wait) must still behave as before: no exception, a normal (results_df, failed_df)
    # tuple with the stragglers recorded as "timeout" failures.
    session = serp_client._session

    def never_ready(url, timeout=None):
        return _Resp({"tasks": [{"id": url.rsplit("/", 1)[-1], "status_code": 40602,
                                  "data": {}, "result": None}]})

    serp_client._get = lambda url, session=None, max_retries=None, retry_delay=None: never_ready(url).json()
    serp_client._create_session = lambda: type("S", (), {"get": staticmethod(never_ready)})()

    results_df, failed_df = serp_client.serp_google_organic._post_all_sync(
        ["kw0"], domain=None, job_id=generate_job_id(), batch_size=1, num_workers=1,
        max_wait=1)

    assert results_df.empty
    assert len(failed_df) == 1
    assert failed_df.iloc[0]["reason"] == "timeout"


def test_serp_collector_path_logs_submit_cost(serp_client, bq, monkeypatch):
    monkeypatch.setattr(serp_mod, "submit_and_wait",
                        lambda **k: {"total_tasks": len(k["posted_tasks"]), "proceeded": True})
    summary = asyncio.run(serp_client.serp_google_organic.post_all(
        [f"kw{i}" for i in range(250)], domain=None, job_id=generate_job_id(),
        use_collector=True))
    assert summary["total_tasks"] == 250
    assert len(cost_rows(bq)) == 250
