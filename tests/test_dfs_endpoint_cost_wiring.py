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
