from unittest.mock import MagicMock

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
    if bq is not None:
        # RunContext._after_save calls bq.log_upload_event() after every saved window;
        # FakeBigQueryClient has no such method, so mock it here for every test that
        # goes through _client()/_make_run() rather than let each one see the
        # AttributeError-derived "Cost-log upload event failed" print.
        bq.log_upload_event = MagicMock()
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


def test_post_records_error_does_not_fail_data_pull(monkeypatch):
    client = _client()
    client._session = _Session({"tasks": [{"id": "t", "cost": 1}]})
    unit = RunUnit("test")
    token = _ACTIVE_UNIT.set(unit)
    try:
        # Monkeypatch record_http to raise an exception
        def raise_error(*args, **kwargs):
            raise RuntimeError("json.dumps failed on mixed-type keys")
        monkeypatch.setattr(unit, "record_http", raise_error)
        # _post should still return data and not retry
        result = client._post(f"{BASE}/x/live", [{}], max_retries=3, retry_delay=0)
        assert result is not None
        assert client._session.calls == 1  # Only called once, no retries
    finally:
        _ACTIVE_UNIT.reset(token)


import asyncio
import threading

import pandas as pd

from skyward.data.dataforseo.estimates import CostEstimate, RunPlan
from skyward.data.dataforseo.run import RunContext
from skyward.functions import generate_job_id

LIVE_URL = f"{BASE}/dataforseo_labs/google/keyword_suggestions/live"


def _record(task_id, cost=0.01):
    unit = active_unit()
    unit.record_http(LIVE_URL, [{"keyword": task_id}],
                     {"tasks": [{"id": task_id, "status_code": 20000, "cost": cost,
                                 "result": [{"items": [{}]}]}]}, 200)


def _make_run(bq, *, upload=True, upload_batch_rows=None, max_usd=0.0, tag=True,
              targets=("a", "b", "c"), writes=None, client=None):
    client = client or _client(bq)
    writes = writes if writes is not None else []
    plan = RunPlan("ep", "live", len(targets), len(targets), len(targets), tuple(targets))
    est = CostEstimate(max_usd, max_usd, max_usd, "list_price", plan)
    run = RunContext(
        client=client, endpoint_key="ep", job_id=generate_job_id(), plan=plan, estimate=est,
        endpoint_mode="live", upload=upload,
        write=lambda df, uid: writes.append((df.copy(), uid)),
        stamp=lambda df: df.assign(endpoint_mode="live"),
        empty_columns=["task_id", "endpoint_mode"],
        upload_batch_rows=upload_batch_rows, tag_cost_with_upload=tag,
    )
    return run, writes, client


def _cost_rows(bq):
    return [r for ins in bq.client.inserted_rows if ins["table"].endswith(".cost_log")
            for r in ins["rows"]]


def _job_rows(bq):
    return [r for ins in bq.client.inserted_rows if ins["table"].endswith(".job_runs")
            for r in ins["rows"]]


def _unit_fn(task_id):
    def fn():
        _record(task_id)
        return pd.DataFrame([{"task_id": task_id}])
    return fn


def test_cost_rows_share_upload_id_with_their_data_rows():
    bq = FakeBigQueryClient()
    run, writes, _ = _make_run(bq, upload_batch_rows=2)
    run.start(balance=None)
    for t in ["a", "b", "c"]:
        run.run_unit(t, _unit_fn(t))
    df = run.close()
    assert len(df) == 3 and (df["endpoint_mode"] == "live").all()
    data_uid = {tid: uid for frame, uid in writes for tid in frame["task_id"]}
    costs = _cost_rows(bq)
    assert {r["task_id"] for r in costs} == {"a", "b", "c"}
    for r in costs:
        assert r["upload_id"] == data_uid[r["task_id"]]
        assert r["endpoint"] == "ep" and r["endpoint_mode"] == "live"
        assert r["job_id"] == run.job_id
    assert run.upload_ids == [uid for _, uid in writes]


def test_upload_false_logs_cost_with_null_upload_id():
    bq = FakeBigQueryClient()
    run, writes, _ = _make_run(bq, upload=False)
    run.run_unit("a", _unit_fn("a"))
    df = run.close()
    assert writes == [] and len(df) == 1
    assert _cost_rows(bq)[0]["upload_id"] is None


def test_untagged_mode_uploads_data_but_leaves_cost_upload_id_null():
    bq = FakeBigQueryClient()
    run, writes, _ = _make_run(bq, tag=False)
    run.run_unit("a", _unit_fn("a"))
    run.close()
    assert len(writes) == 1
    assert _cost_rows(bq)[0]["upload_id"] is None


def test_job_runs_start_and_end_rows():
    bq = FakeBigQueryClient()
    run, _, _ = _make_run(bq)
    run.start(balance=12.5)
    run.run_unit("a", _unit_fn("a"))
    run.close()
    rows = _job_rows(bq)
    assert [(r["event"], r["status"]) for r in rows] == [("start", "running"), ("end", "completed")]
    assert rows[0]["balance_at_start"] == 12.5
    assert rows[0]["planned_requests"] == 3


def test_failure_still_saves_buffered_rows_and_marks_failed():
    bq = FakeBigQueryClient()
    run, writes, _ = _make_run(bq)
    run.run_unit("a", _unit_fn("a"))

    def boom():
        _record("b")
        raise RuntimeError("network")

    with pytest.raises(RuntimeError):
        run.run_unit("b", boom)
    run.close(error=RuntimeError("network"))
    assert len(writes) == 1
    assert {r["task_id"] for r in _cost_rows(bq)} == {"a", "b"}
    assert _job_rows(bq)[-1]["status"] == "failed"


def test_close_is_idempotent_and_prints_no_rows(capsys):
    bq = FakeBigQueryClient()
    run, _, _ = _make_run(bq)
    first = run.close()
    second = run.close()
    assert first is second and first.empty
    assert list(first.columns) == ["task_id", "endpoint_mode"]
    assert "No rows returned. Skipping upload." in capsys.readouterr().out


def test_mid_run_low_balance_saves_then_stops(monkeypatch):
    bq = FakeBigQueryClient()
    run, writes, client = _make_run(bq, upload_batch_rows=1, max_usd=10.0)
    monkeypatch.setattr(client, "get_balance_cached",
                        lambda max_age_s=60.0: {"balance": 1.0, "total": 0, "raw": {"b": 1}})
    with pytest.raises(InsufficientBalanceError) as ei:
        run.run_unit("a", _unit_fn("a"))
    assert len(writes) == 1                    # window saved before stopping
    assert ei.value.completed_targets == ["a"]
    assert ei.value.remaining_targets == ["b", "c"]
    assert ei.value.upload_ids == [writes[0][1]]
    with pytest.raises(InsufficientBalanceError):
        run.run_unit("b", _unit_fn("b"))       # no more spending after a stop
    run.close()
    assert _job_rows(bq)[-1]["status"] == "stopped_low_balance"


def test_mid_run_override_continues(monkeypatch):
    bq = FakeBigQueryClient()
    client = _client(bq)
    monkeypatch.setattr(client, "get_balance_cached",
                        lambda max_age_s=60.0: {"balance": 1.0, "total": 0, "raw": {"b": 1}})
    plan = RunPlan("ep", "live", 2, 2, 2, ("a", "b"))
    run = RunContext(
        client=client, endpoint_key="ep", job_id=generate_job_id(), plan=plan,
        estimate=CostEstimate(10.0, 10.0, 10.0, "list_price", plan), endpoint_mode="live",
        upload=True, write=lambda df, uid: None, stamp=lambda df: df,
        empty_columns=["task_id"], upload_batch_rows=1, ignore_balance_check=True,
    )
    run.run_unit("a", _unit_fn("a"))
    run.run_unit("b", _unit_fn("b"))
    assert len(run.close()) == 2


def test_run_unit_async_records_cost():
    bq = FakeBigQueryClient()
    run, _, _ = _make_run(bq)

    async def coro():
        _record("x")
        return pd.DataFrame([{"task_id": "x"}])

    asyncio.run(run.run_unit_async(["x"], coro))
    run.close()
    assert _cost_rows(bq)[0]["task_id"] == "x"


def test_add_rows_uploads_without_cost_rows():
    bq = FakeBigQueryClient()
    run, writes, _ = _make_run(bq)
    run.add_rows(pd.DataFrame([{"task_id": "s1"}]))
    run.close()
    assert len(writes) == 1 and _cost_rows(bq) == []


def test_concurrent_units_keep_cost_and_data_windows_aligned():
    bq = FakeBigQueryClient()
    targets = [f"t{i}" for i in range(80)]
    run, writes, _ = _make_run(bq, upload_batch_rows=3, targets=targets)

    def worker(chunk):
        for t in chunk:
            run.run_unit(t, _unit_fn(t))

    threads = [threading.Thread(target=worker, args=(targets[i::8],)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    run.close()
    data_uid = {tid: uid for frame, uid in writes for tid in frame["task_id"]}
    costs = _cost_rows(bq)
    assert len(costs) == 80
    for r in costs:
        assert r["upload_id"] == data_uid[r["task_id"]]
    assert round(run.spent_usd, 6) == 0.8


def test_final_save_failure_marks_end_row_failed_and_second_close_returns_df():
    bq = FakeBigQueryClient()
    client = _client(bq)

    def failing_write(df, uid):
        raise RuntimeError("boom-write")

    plan = RunPlan("ep", "live", 1, 1, 1, ("a",))
    est = CostEstimate(0.0, 0.0, 0.0, "list_price", plan)
    run = RunContext(
        client=client, endpoint_key="ep", job_id=generate_job_id(), plan=plan, estimate=est,
        endpoint_mode="live", upload=True, write=failing_write,
        stamp=lambda df: df.assign(endpoint_mode="live"),
        empty_columns=["task_id", "endpoint_mode"],
    )
    run.run_unit("a", _unit_fn("a"))
    with pytest.raises(RuntimeError, match="boom-write"):
        run.close()
    assert _job_rows(bq)[-1]["status"] == "failed"

    second = run.close()
    assert isinstance(second, pd.DataFrame)
    assert len(second) == 1 and list(second["task_id"]) == ["a"]


def test_run_unit_raising_fn_does_not_mark_target_completed():
    bq = FakeBigQueryClient()
    run, writes, _ = _make_run(bq)

    def raising_fn():
        _record("a")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run.run_unit("a", raising_fn)
    assert run.completed_targets() == []
    assert run.remaining_targets() == ["a", "b", "c"]

    run.close(error=RuntimeError("boom"))
    assert {r["task_id"] for r in _cost_rows(bq)} == {"a"}


def test_after_save_logs_cost_upload_event_per_window():
    bq = FakeBigQueryClient()
    run, writes, client = _make_run(bq, upload_batch_rows=2)
    for t in ["a", "b", "c"]:
        run.run_unit(t, _unit_fn(t))
    run.close()

    mock = client.bq_client.log_upload_event
    costs = _cost_rows(bq)
    assert mock.call_count == len(writes) == 2
    for call, (_, uid) in zip(mock.call_args_list, writes):
        kwargs = call.kwargs
        expected_rows = len([r for r in costs if r["upload_id"] == uid])
        assert expected_rows > 0
        assert kwargs["upload_id"] == uid
        assert kwargs["source_program"] == "dfs_cost_log"
        assert kwargs["table"] == "cost_log"
        assert kwargs["row_count"] == expected_rows
