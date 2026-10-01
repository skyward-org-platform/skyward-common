"""DataForSEO failed-task handling (v1.6.1, 2026-10-01).

Found live on busbank: GSC-sourced keywords that are LLM prompts made DataForSEO answer
40501 "Invalid Field: 'keywords'." for every 700-keyword keyword_overview / search_intent
batch that held one. The endpoint retried each batch five times on a permanent error, the
whole batch's other keywords went unenriched, nothing recorded why, and job_runs said
"completed". Separately, a direct `_post` outside a run wrote no cost_log row at all.
"""
import asyncio
import itertools
import json

import pytest

from skyward.data.dataforseo import DataForSEOClient
from skyward.data.dataforseo.cost_log import extract_cost_records
from skyward.data.dataforseo.run import active_unit
from skyward.functions import generate_job_id
from tests.conftest import FakeBigQueryClient

BASE = "https://api.dataforseo.com/v3"
BAD = "<brand>you are an expert</brand>"


class _Resp:
    def __init__(self, data):
        self._data, self.status_code = data, 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class RejectingSession:
    """keyword_overview / search_intent: rejects any task whose keyword list holds BAD
    with 40501, as DataForSEO does, at no cost; otherwise answers 20000."""

    def __init__(self, cost=0.0124, status=40501, message="Invalid Field: 'keywords'."):
        self.calls = []
        self._ids = itertools.count(1)
        self._cost, self._status, self._message = cost, status, message

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        tasks = []
        for task in json:
            tid = f"task-{next(self._ids)}"
            if url.endswith("/task_post"):
                tasks.append({"id": tid, "status_code": 20100, "cost": 0.0006, "result": None})
                continue
            if BAD in task.get("keywords", []):
                tasks.append({"id": tid, "status_code": self._status,
                              "status_message": self._message, "cost": 0, "result": None})
                continue
            if "keyword_overview" in url:
                items = [{"keyword": k, "keyword_info": {"search_volume": 10}}
                         for k in task["keywords"]]
            else:
                items = [{"keyword": k, "keyword_intent": {"label": "informational",
                                                           "probability": 0.9}}
                         for k in task["keywords"]]
            tasks.append({"id": tid, "status_code": 20000, "cost": self._cost,
                          "data": task, "result": [{"items": items}]})
        return _Resp({"status_code": 20000, "tasks": tasks})


@pytest.fixture
def bq():
    return FakeBigQueryClient()


def _client(bq, session):
    c = DataForSEOClient(username="u", password="p", bq_client=bq)
    c._session = session
    return c


def _rows(bq, table):
    return [r for ins in bq.client.inserted_rows if ins["table"].endswith(f".{table}")
            for r in ins["rows"]]


# ---- status_message on failed tasks -----------------------------------------------------

def test_failed_task_records_dfs_status_message():
    resp = {"tasks": [{"id": "t", "status_code": 40501,
                       "status_message": "Invalid Field: 'keywords'.", "cost": 0}]}
    rec = extract_cost_records(f"{BASE}/dataforseo_labs/google/keyword_overview/live",
                               [{"keywords": ["a"], "language_code": "en"}], resp, 200)[0]
    assert json.loads(rec["price_inputs"])["dfs_status_message"] == "Invalid Field: 'keywords'."


def test_successful_task_carries_no_status_message():
    resp = {"tasks": [{"id": "t", "status_code": 20000, "status_message": "Ok.",
                       "cost": 0.01, "result": []}]}
    rec = extract_cost_records(f"{BASE}/dataforseo_labs/google/keyword_overview/live",
                               [{"keywords": ["a"]}], resp, 200)[0]
    assert "dfs_status_message" not in json.loads(rec["price_inputs"])


# ---- a direct _post outside any run is still cost-logged ---------------------------------

def test_post_outside_a_run_writes_an_unattributed_cost_row(bq):
    session = RejectingSession()
    client = _client(bq, session)
    assert active_unit() is None
    url = f"{BASE}/dataforseo_labs/google/keyword_overview/live"
    client._post(url, [{"keywords": ["a"], "location_code": 2840}], max_retries=1, retry_delay=0)
    rows = _rows(bq, "cost_log")
    assert len(rows) == 1
    row = rows[0]
    assert row["job_id"] == "unattributed"
    assert row["endpoint"] == "dataforseo_labs_google_keyword_overview"
    assert row["endpoint_mode"] == "live"
    assert row["cost_usd"] == 0.0124
    assert json.loads(row["price_inputs"])["unattributed"] is True


def test_post_outside_a_run_without_bigquery_still_returns_data():
    client = _client(None, RejectingSession())
    url = f"{BASE}/dataforseo_labs/google/keyword_overview/live"
    assert client._post(url, [{"keywords": ["a"]}], max_retries=1, retry_delay=0) is not None


def test_unbilled_calls_outside_a_run_are_not_logged(bq):
    client = _client(bq, RejectingSession())
    client._post(f"{BASE}/serp/google/organic/tasks_ready", [{}], max_retries=1, retry_delay=0)
    assert _rows(bq, "cost_log") == []


def test_serp_task_post_outside_a_run_is_standard_mode(bq):
    client = _client(bq, RejectingSession())
    client._post(f"{BASE}/serp/google/organic/task_post", [{"keyword": "a"}],
                 max_retries=1, retry_delay=0)
    row = _rows(bq, "cost_log")[0]
    assert row["endpoint"] == "serp_google_organic"
    assert row["endpoint_mode"] == "standard"


# ---- keyword-list endpoints: no retry on permanent errors, bisect a 40501 batch ----------

@pytest.mark.parametrize("attr", ["dataforseo_labs_google_keyword_overview",
                                  "dataforseo_labs_google_search_intent"])
def test_bad_keyword_is_isolated_and_the_rest_of_the_batch_is_kept(bq, attr):
    session = RejectingSession()
    ep = getattr(_client(bq, session), attr)
    keywords = ["a", "b", BAD, "c", "d"]
    df = asyncio.run(ep.live_all(keywords, domain=None, job_id=generate_job_id(),
                                 upload=False, ignore_balance_check=True))
    assert sorted(df["keyword"]) == ["a", "b", "c", "d"]
    assert ep.rejected_keywords == [BAD]
    # Bisection, not five blind retries of the full batch: a handful of requests.
    assert len(session.calls) <= 2 * 3 + 1
    full_batch_posts = [c for c in session.calls if len(c[1][0]["keywords"]) == len(keywords)]
    assert len(full_batch_posts) == 1


@pytest.mark.parametrize("attr", ["dataforseo_labs_google_keyword_overview",
                                  "dataforseo_labs_google_search_intent"])
def test_other_permanent_errors_are_not_retried(bq, attr):
    session = RejectingSession(status=40200, message="Payment Required.")
    ep = getattr(_client(bq, session), attr)
    df = asyncio.run(ep.live_all(["a", BAD], domain=None, job_id=generate_job_id(),
                                 upload=False, ignore_balance_check=True))
    assert df.empty
    assert len(session.calls) == 1


def test_job_runs_end_row_is_partial_when_a_task_failed(bq):
    session = RejectingSession()
    ep = _client(bq, session).dataforseo_labs_google_keyword_overview
    asyncio.run(ep.live_all(["a", BAD, "c"], domain=None, job_id=generate_job_id(),
                            upload=False, ignore_balance_check=True))
    end = [r for r in _rows(bq, "job_runs") if r["event"] == "end"][-1]
    assert end["status"] == "partial"
    assert "1 keyword(s) rejected" in end["error"]
    assert "40501" in end["error"]


def test_job_runs_end_row_stays_completed_when_every_task_succeeded(bq):
    ep = _client(bq, RejectingSession()).dataforseo_labs_google_keyword_overview
    asyncio.run(ep.live_all(["a", "b"], domain=None, job_id=generate_job_id(),
                            upload=False, ignore_balance_check=True))
    end = [r for r in _rows(bq, "job_runs") if r["event"] == "end"][-1]
    assert end["status"] == "completed"
