import asyncio

import pandas as pd
import pytest

from skyward.data.dataforseo import (
    DataForSEOClient, InsufficientBalanceError, InvalidLocationError,
)
from skyward.data.dataforseo.base import BaseEndpoint
from skyward.functions import generate_job_id
from tests.conftest import FakeBigQueryClient


class _Resp:
    def __init__(self, data):
        self._data, self.status_code = data, 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _Session:
    """Returns one DFS task per POST; task id echoes the keyword sent."""

    def __init__(self):
        self.calls = 0

    def post(self, url, json=None, timeout=None):
        self.calls += 1
        kw = json[0]["keyword"]
        return _Resp({"tasks": [{"id": f"task-{kw}", "status_code": 20000, "cost": 0.0124,
                                 "result": [{"items": [{"keyword": kw}]}]}]})


class _LabsLike(BaseEndpoint):
    ENDPOINT_KEY = "dataforseo_labs_google_keyword_suggestions"
    LIVE_URL = "dataforseo_labs/google/keyword_suggestions/live"
    TABLE_NAME = "fake-table"

    def _build_payload(self, target, **kwargs):
        return [{"keyword": target, "location_code": kwargs.get("location_code", 2840)}]

    def _parse_response(self, response, target):
        task = response["tasks"][0]
        return pd.DataFrame([{"keyword": target, "task_id": task["id"]}])

    def _get_schema(self):
        return ["keyword"]

    def _get_dedupe_keys(self):
        return ["keyword"]

    def _cast_types(self, df):
        return df

    def _fetch_live(self, target, **kwargs):
        kwargs.pop("_debug_collector", None)
        resp = self._client._post(f"{self._client.BASE_URL}/{self.LIVE_URL}",
                                  self._build_payload(target, **kwargs),
                                  max_retries=1, retry_delay=0)
        return self._parse_response(resp, target)


@pytest.fixture
def bq():
    return FakeBigQueryClient()


@pytest.fixture
def ep(bq):
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    client._session = _Session()
    return _LabsLike(client)


def _inserted(bq, table):
    return [r for ins in bq.client.inserted_rows if ins["table"].endswith(f".{table}")
            for r in ins["rows"]]


def _data_upload_ids(bq):
    return {tid: uid for load in bq.client.loaded_tables
            for tid, uid in zip(load["df"]["task_id"], load["df"]["upload_id"])}


def test_endpoint_key_defaults_to_module_name():
    class _Plain(_LabsLike):
        pass
    assert _LabsLike.ENDPOINT_KEY == "dataforseo_labs_google_keyword_suggestions"
    assert _Plain.ENDPOINT_KEY == "test_dfs_base_run_wiring"


def test_location_flag_mapping(ep):
    assert ep.location_flag == "in_labs"


def test_live_logs_cost_linked_to_uploaded_rows(ep, bq):
    df = ep.live("pizza", domain=None, job_id=generate_job_id())
    assert len(df) == 1
    costs = _inserted(bq, "cost_log")
    assert len(costs) == 1
    assert costs[0]["task_id"] == "task-pizza"
    assert costs[0]["upload_id"] == _data_upload_ids(bq)["task-pizza"]
    assert costs[0]["endpoint"] == "dataforseo_labs_google_keyword_suggestions"
    runs = _inserted(bq, "job_runs")
    assert [(r["event"], r["status"]) for r in runs] == [("start", "running"), ("end", "completed")]


def test_live_all_concurrent_windows_stay_linked(ep, bq):
    targets = [f"kw{i}" for i in range(20)]
    df = asyncio.run(ep.live_all(targets, domain=None, job_id=generate_job_id(),
                                 batch_size=5, batch_delay=0, upload_batch_rows=4))
    assert len(df) == 20
    assert len(bq.client.loaded_tables) == 5
    uids = _data_upload_ids(bq)
    costs = _inserted(bq, "cost_log")
    assert len(costs) == 20
    for r in costs:
        assert r["upload_id"] == uids[r["task_id"]]


def test_upload_false_still_logs_cost(ep, bq):
    ep.live("pizza", domain=None, job_id=generate_job_id(), upload=False)
    assert bq.client.loaded_tables == []
    assert _inserted(bq, "cost_log")[0]["upload_id"] is None


def test_low_balance_rejects_before_spending(ep, bq, monkeypatch):
    monkeypatch.setattr(ep._client, "get_balance_cached",
                        lambda max_age_s=60.0: {"balance": 0.001, "total": 0, "raw": {"b": 1}})
    with pytest.raises(InsufficientBalanceError):
        ep.live("pizza", domain=None, job_id=generate_job_id())
    assert ep._client._session.calls == 0
    rejection = _inserted(bq, "job_runs")[-1]
    assert rejection["status"] == "rejected_low_balance"
    assert rejection["balance_at_start"] == 0.001


def test_low_balance_rejection_rounds_balance_at_start(ep, bq, monkeypatch):
    # Real DataForSEO balances carry far more than 9 decimal digits (e.g. from cents
    # division); BigQuery NUMERIC only allows 9, so the writer must round defensively.
    monkeypatch.setattr(ep._client, "get_balance_cached",
                        lambda max_age_s=60.0: {"balance": 0.00133360000000123,
                                                "total": 0, "raw": {"b": 1}})
    with pytest.raises(InsufficientBalanceError):
        ep.live("pizza", domain=None, job_id=generate_job_id())
    rejection = _inserted(bq, "job_runs")[-1]
    assert rejection["status"] == "rejected_low_balance"
    assert rejection["balance_at_start"] == round(0.00133360000000123, 6)
    assert round(rejection["balance_at_start"], 6) == rejection["balance_at_start"]


def test_balance_override_proceeds_with_warning(ep, bq, monkeypatch, capsys):
    monkeypatch.setattr(ep._client, "get_balance_cached",
                        lambda max_age_s=60.0: {"balance": 0.001, "total": 0, "raw": {"b": 1}})
    df = ep.live("pizza", domain=None, job_id=generate_job_id(), ignore_balance_check=True)
    assert len(df) == 1
    assert "ignore_balance_check=True" in capsys.readouterr().out


def test_unsupported_location_rejects(ep, bq):
    bq.client.queue_result(pd.DataFrame())   # cost_estimates read
    bq.client.queue_result(pd.DataFrame())   # endpoint_cost_actuals read
    bq.client.queue_result(pd.DataFrame([{"total_rows": 100, "in_serp": True,
                                          "in_google_ads": True, "in_labs": False}]))
    with pytest.raises(InvalidLocationError) as ei:
        ep.live("pizza", domain=None, job_id=generate_job_id(), location_code=200528)
    assert ei.value.supported_by == "in_labs"
    assert ep._client._session.calls == 0
    assert _inserted(bq, "job_runs")[-1]["status"] == "rejected_location"


def test_location_override_proceeds(ep, bq):
    df = ep.live("pizza", domain=None, job_id=generate_job_id(), location_code=200528,
                 ignore_location_check=True)
    assert len(df) == 1


def test_estimate_cost_spends_nothing(ep):
    est = ep.estimate_cost(["a", "b"])
    assert est.plan.planned_requests == 2
    assert est.max_usd > 0
    assert ep._client._session.calls == 0


def test_upload_accepts_explicit_upload_id(ep, bq):
    df = pd.DataFrame([{"keyword": "a", "task_id": "t", "domain_id": None, "domain": None,
                        "endpoint_mode": "live"}])
    ep.upload(bq, df, job_id=generate_job_id(), upload_id="fixed-id")
    assert (bq.client.loaded_tables[0]["df"]["upload_id"] == "fixed-id").all()
