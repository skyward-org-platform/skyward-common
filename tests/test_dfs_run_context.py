import pytest

from skyward.data.dataforseo import DataForSEOClient
from skyward.data.dataforseo.exceptions import InsufficientBalanceError
from skyward.data.dataforseo.run import (
    RunUnit, _ACTIVE_UNIT, active_unit, check_balance, target_list, write_job_run_row,
)
from tests.conftest import FakeBigQueryClient

BASE = "https://api.dataforseo.com/v3"


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._data


class _Session:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    def post(self, url, json=None, timeout=None):
        self.calls += 1
        return _Resp(self.data)


def _client(bq=None):
    return DataForSEOClient(username="u", password="p", bq_client=bq)


def test_target_list():
    assert target_list("a") == ["a"]
    assert target_list(["a", "b"]) == ["a", "b"]


def test_post_records_into_active_unit_and_counts_attempts():
    client = _client()
    client._session = _Session({"tasks": [{"id": "t", "status_code": 20000, "cost": 0.0121,
                                           "result": [{"items": [{}]}]}]})
    unit = RunUnit("pizza")
    token = _ACTIVE_UNIT.set(unit)
    try:
        url = f"{BASE}/dataforseo_labs/google/keyword_suggestions/live"
        client._post(url, [{"keyword": "pizza"}], max_retries=1, retry_delay=0)
        client._post(url, [{"keyword": "pizza"}], max_retries=1, retry_delay=0)
    finally:
        _ACTIVE_UNIT.reset(token)
    assert [r["attempt"] for r in unit.records] == [1, 2]
    assert unit.records[0]["http_status"] == 200
    assert unit.records[0]["cost_usd"] == 0.0121


def test_post_without_active_unit_records_nothing():
    client = _client()
    client._session = _Session({"tasks": [{"id": "t", "cost": 1}]})
    assert active_unit() is None
    assert client._post(f"{BASE}/x/live", [{}], max_retries=1, retry_delay=0) is not None


def test_get_balance_cached_reuses_value(monkeypatch):
    client = _client()
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return {"balance": 10.0, "total": 0.0, "raw": {"balance": 10.0}}

    monkeypatch.setattr(client, "get_balance", fake)
    client.get_balance_cached()
    client.get_balance_cached()
    assert calls["n"] == 1
    client.get_balance_cached(max_age_s=0)
    assert calls["n"] == 2


def test_get_balance_cached_does_not_cache_failures(monkeypatch):
    client = _client()
    calls = {"n": 0}

    def fake():
        calls["n"] += 1
        return {"balance": 0.0, "total": 0.0, "raw": {}}

    monkeypatch.setattr(client, "get_balance", fake)
    client.get_balance_cached()
    client.get_balance_cached()
    assert calls["n"] == 2


def _balance(client, monkeypatch, value, raw=True):
    info = {"balance": value, "total": 0.0, "raw": {"balance": value} if raw else {}}
    monkeypatch.setattr(client, "get_balance_cached", lambda max_age_s=60.0: info)


def test_check_balance_passes_when_enough(monkeypatch):
    client = _client()
    _balance(client, monkeypatch, 5.0)
    assert check_balance(client, required_usd=4.0, balance_buffer=1.2, ignore=False,
                         job_id="j", endpoint="e") == 5.0


def test_check_balance_raises_with_context(monkeypatch):
    client = _client()
    _balance(client, monkeypatch, 4.0)
    with pytest.raises(InsufficientBalanceError) as ei:
        check_balance(client, required_usd=4.0, balance_buffer=1.2, ignore=False,
                      job_id="j", endpoint="e", remaining_targets=["a", "b"],
                      completed_targets=["z"], upload_ids=["u1"])
    err = ei.value
    assert (err.job_id, err.endpoint, err.balance, err.required) == ("j", "e", 4.0, 4.8)
    assert err.remaining_targets == ["a", "b"]
    assert err.completed_targets == ["z"]
    assert err.upload_ids == ["u1"]


def test_check_balance_override_warns(monkeypatch, capsys):
    client = _client()
    _balance(client, monkeypatch, 1.0)
    assert check_balance(client, required_usd=4.0, balance_buffer=1.2, ignore=True,
                         job_id="j", endpoint="e") == 1.0
    assert "ignore_balance_check=True" in capsys.readouterr().out


def test_check_balance_skips_when_unreadable_or_zero_cost(monkeypatch, capsys):
    client = _client()
    _balance(client, monkeypatch, 0.0, raw=False)
    assert check_balance(client, required_usd=4.0, balance_buffer=1.2, ignore=False,
                         job_id="j", endpoint="e") is None
    assert "Could not read DataForSEO balance" in capsys.readouterr().out
    assert check_balance(client, required_usd=0.0, balance_buffer=1.2, ignore=False,
                         job_id="j", endpoint="e") is None


def test_write_job_run_row_streams_and_retries():
    bq = FakeBigQueryClient()
    bq.client.insert_errors = [["boom"]]
    assert write_job_run_row(bq, {"job_id": "j"}, sleep=lambda s: None) is True
    assert bq.client.inserted_rows[0]["table"] == "data-hub-468216.DataForSEO.job_runs"


def test_write_job_run_row_without_bq_is_noop():
    assert write_job_run_row(None, {"job_id": "j"}) is False
