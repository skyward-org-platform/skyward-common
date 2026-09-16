import pytest

from skyward.data.dataforseo import DataForSEOClient
from skyward.data.dataforseo.exceptions import InsufficientBalanceError
from skyward.data.dataforseo.run import (
    RunUnit, _ACTIVE_UNIT, active_unit, check_balance, round_money, target_list,
    write_job_run_row,
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
    # FakeBigQueryClient.log_upload_event is a MagicMock by default (tests/conftest.py) —
    # RunContext._after_save and BaseEndpoint.upload() both call it after a saved window.
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


def _job_run_row(job_id="j", endpoint="ep", endpoint_mode="live", event="start",
                 status="running", ts="2026-01-01T00:00:00+00:00"):
    return {"job_id": job_id, "endpoint": endpoint, "endpoint_mode": endpoint_mode,
            "event": event, "status": status, "ingest_timestamp": ts}


def test_job_run_row_insert_passes_row_id_and_strips_it_from_payload():
    bq = FakeBigQueryClient()
    assert write_job_run_row(bq, _job_run_row(), sleep=lambda s: None) is True
    ins = bq.client.inserted_rows[0]
    assert ins["row_ids"] is not None and ins["row_ids"][0] is not None
    assert "_row_id" not in ins["rows"][0]


def test_job_run_row_retry_after_lost_ack_reuses_the_same_row_id():
    """A duplicated start/end row corrupts job_progress's runs_started/runs_ended counts,
    so a retry after a lost ack must reuse the same insertId, not mint a fresh one."""
    bq = FakeBigQueryClient()
    calls = []
    original_insert = bq.client.insert_rows_json

    def flaky(table, rows, row_ids=None):
        calls.append(row_ids)
        if len(calls) == 1:
            raise TimeoutError("ack lost in transit")
        return original_insert(table, rows, row_ids=row_ids)

    bq.client.insert_rows_json = flaky
    assert write_job_run_row(bq, _job_run_row(), sleep=lambda s: None) is True

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert len(bq.client.inserted_rows) == 1


def test_job_run_row_start_and_end_get_different_row_ids():
    bq = FakeBigQueryClient()
    write_job_run_row(bq, _job_run_row(event="start", status="running", ts="t0"),
                      sleep=lambda s: None)
    write_job_run_row(bq, _job_run_row(event="end", status="completed", ts="t1"),
                      sleep=lambda s: None)
    ids = [ins["row_ids"][0] for ins in bq.client.inserted_rows]
    assert ids[0] != ids[1]


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
import logging
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


def test_mid_run_balance_check_never_reuses_a_cached_reading(monkeypatch):
    # get_balance_cached()'s default 60s TTL is right for the pre-run check (nothing has
    # happened yet, a fresh-enough reading is fine), but a mid-run check must never reuse
    # the pre-run reading or an earlier mid-run one — the balance may have moved since.
    bq = FakeBigQueryClient()
    run, _, client = _make_run(bq, upload_batch_rows=1, max_usd=10.0)
    calls = []

    def fake_get_balance_cached(max_age_s=60.0):
        calls.append(max_age_s)
        return {"balance": 1_000_000.0, "total": 0, "raw": {"b": 1}}

    monkeypatch.setattr(client, "get_balance_cached", fake_get_balance_cached)
    run.run_unit("a", _unit_fn("a"))
    run.close()
    assert calls == [0]   # the only call here is the mid-run one, and it must be max_age_s=0


def test_after_save_inside_the_close_window_does_not_run_a_balance_check(monkeypatch):
    """_after_save used to read self._closing alone, while close() set _closed first and
    _closing several statements later. A concurrent threshold-tripping uploader.add landing
    in that gap ran a LIVE mid-run balance check against a run that had already closed, and
    could set _stop_error and raise InsufficientBalanceError inside a worker during
    teardown.

    Testing this AFTER close() returns proves nothing: by then both flags are set either
    way. The window has to be entered while close() is mid-flight, so we block close()
    inside the gap and drive _after_save from another thread from exactly there.
    """
    bq = FakeBigQueryClient()
    run, _writes, client = _make_run(bq, upload_batch_rows=1, max_usd=10.0)

    calls = []

    def spy_get_balance_cached(max_age_s=60.0):
        calls.append(max_age_s)
        return {"balance": 0.0, "total": 0, "raw": {"b": 0}}

    monkeypatch.setattr(client, "get_balance_cached", spy_get_balance_cached)

    in_the_window = threading.Event()
    released = threading.Event()

    class _BlockingColumns(list):
        """close() builds its placeholder result from _empty_columns inside the gap."""

        def __iter__(self):
            in_the_window.set()
            released.wait(5)
            return super().__iter__()

    run._empty_columns = _BlockingColumns(["task_id", "endpoint_mode"])

    closer = threading.Thread(target=lambda: run.close(quiet=True))
    closer.start()
    try:
        assert in_the_window.wait(5), "close() never reached the window"
        # Here _closed is set. Under the old single-flag read _closing was not yet, so
        # this call proceeded into a live balance check against a closed run.
        run._after_save("some-upload-id")
    finally:
        released.set()
        closer.join(10)

    # A balance of 0 would have raised had the check run, so this asserts twice over.
    assert calls == []
    assert run._stop_error is None


def test_pre_run_balance_check_keeps_the_default_cache_ttl():
    from skyward.data.dataforseo.run import check_balance
    calls = []

    class _C:
        def get_balance_cached(self, max_age_s=60.0):
            calls.append(max_age_s)
            return {"balance": 100.0, "total": 0, "raw": {"b": 1}}

    check_balance(_C(), required_usd=1.0, balance_buffer=1.2, ignore=False,
                 job_id="j", endpoint="e")
    assert calls == [60.0]   # pre-run stage: unchanged default TTL


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


def test_round_money_rounds_to_six_decimal_places():
    # Real DataForSEO balances (and float math generally) can carry far more than 9
    # digits after the decimal point, which BigQuery NUMERIC rejects outright.
    assert round_money(None) is None
    assert round_money(33.36875400000026) == 33.368754
    assert round_money(0.012399999999999995) == 0.0124
    assert round_money(0.1) == 0.1
    assert round_money(0) == 0.0


def test_job_run_row_rounds_balance_and_estimate_fields():
    bq = FakeBigQueryClient()
    targets = ("a",)
    plan = RunPlan("ep", "live", 1, 1, 1, targets)
    # Bypass estimates.py's own rounding to simulate a CostEstimate whose fields still
    # carry raw float noise by the time they reach the job_runs writer.
    est = CostEstimate(max_usd=0.38279999999999936, avg_usd=0.34799999999999986,
                       list_price_usd=0.348, basis="list_price", plan=plan)
    run = RunContext(
        client=_client(bq), endpoint_key="ep", job_id=generate_job_id(), plan=plan,
        estimate=est, endpoint_mode="live", upload=False,
        write=lambda df, uid: None, stamp=lambda df: df, empty_columns=["task_id"],
    )
    run.start(balance=33.36875400000026)
    run.close()
    rows = _job_rows(bq)
    assert len(rows) == 2
    for row in rows:
        for field_name in ("balance_at_start", "estimate_max_usd", "estimate_avg_usd"):
            value = row[field_name]
            if value is not None:
                assert round(value, 6) == value, f"{field_name}={value!r} has >6 decimals"
    assert rows[0]["balance_at_start"] == 33.368754
    assert rows[0]["estimate_max_usd"] == 0.3828
    assert rows[0]["estimate_avg_usd"] == 0.348


def test_cost_log_row_rounds_cost_usd():
    bq = FakeBigQueryClient()
    run, _, _ = _make_run(bq, upload=False)

    def fn():
        active_unit().record_http(
            LIVE_URL, [{"keyword": "a"}],
            {"tasks": [{"id": "a", "status_code": 20000, "cost": 0.012399999999999995,
                        "result": [{"items": [{}]}]}]}, 200)
        return pd.DataFrame([{"task_id": "a"}])

    run.run_unit("a", fn)
    run.close()
    row = _cost_rows(bq)[0]
    assert round(row["cost_usd"], 6) == row["cost_usd"]
    assert row["cost_usd"] == 0.0124


def _boom_write(df, uid):
    raise RuntimeError("boom-write")


def _run_with_failing_final_save(bq):
    """A run holding one buffered row whose only save happens in uploader.close() -- and
    that save raises, so close() has to decide what really ended the run."""
    plan = RunPlan("ep", "live", 1, 1, 1, ("a",))
    est = CostEstimate(0.0, 0.0, 0.0, "list_price", plan)
    run = RunContext(
        client=_client(bq), endpoint_key="ep", job_id=generate_job_id(), plan=plan,
        estimate=est, endpoint_mode="live", upload=True, write=_boom_write,
        stamp=lambda df: df.assign(endpoint_mode="live"),
        empty_columns=["task_id", "endpoint_mode"],
    )
    run.run_unit("a", _unit_fn("a"))
    return run


def test_balance_stop_survives_a_failing_final_save():
    """A failing final save must not overwrite the reason the run actually stopped.

    Both halves matter. If the save error becomes the cause, the end row says "failed"
    and the low-balance reason is gone; and if close() then raises that save error, the
    caller's `except BaseException: run.close(error=exc); raise` never reaches its bare
    `raise`, so the consumer is handed the save error instead of the real one.
    """
    bq = FakeBigQueryClient()
    run = _run_with_failing_final_save(bq)
    err = InsufficientBalanceError(
        "low balance mid-run", job_id=run.job_id, endpoint="ep", balance=0.0,
        required=1.0, upload_ids=[], completed_targets=[], remaining_targets=[],
    )
    run._stop_error = err

    with pytest.raises(InsufficientBalanceError):
        try:
            raise err
        except BaseException as exc:
            run.close(error=exc)
            raise

    assert _job_rows(bq)[-1]["status"] == "stopped_low_balance"
    # The save failure is still reported, just not as the cause of death. It belongs in
    # the end row's error field, NOT in save_failures, which holds upload_ids.
    assert "boom-write" in _job_rows(bq)[-1]["error"]
    assert not any("boom-write" in f for f in run.save_failures)


def test_final_save_failure_alone_still_raises_and_marks_failed():
    """With no pre-existing cause the save failure IS the cause: unchanged behaviour."""
    bq = FakeBigQueryClient()
    run = _run_with_failing_final_save(bq)
    with pytest.raises(RuntimeError, match="boom-write"):
        run.close()
    assert _job_rows(bq)[-1]["status"] == "failed"


def test_absorb_after_close_drops_the_rows_loudly_but_still_records_their_cost(caplog):
    """_absorb is the OTHER writer to _frames. add_rows was guarded; this one was not.

    With upload=False the uploader is None, so there was no refusal log from that layer
    either and a late unit's rows vanished in complete silence. The data cannot be
    rescued -- close() has already snapshotted _frames and cached the result -- but the
    DataForSEO charge is real and must still reach cost_log.
    """
    bq = FakeBigQueryClient()
    run, _writes, _c = _make_run(bq, upload=False, targets=("a",))
    run.close(quiet=True)
    frames_after_close = len(run._frames)

    with caplog.at_level(logging.ERROR):
        run.run_unit("late", _unit_fn("late"))

    assert len(run._frames) == frames_after_close     # nothing appended for nobody to read
    assert "absorbed after the run closed" in caplog.text
    assert any(r["task_id"] == "late" for r in _cost_rows(bq))   # the spend is recorded


def test_late_cost_rows_carry_a_null_upload_id_not_a_phantom_one():
    """With an uploader present, a late unit's on_joined must not hand out an upload_id
    for a window that will never land. The cost is real, the window is not.
    """
    bq = FakeBigQueryClient()
    run, _writes, _c = _make_run(bq, upload=True, targets=("a",))
    run.close(quiet=True)

    run.run_unit("late", _unit_fn("late"))

    late = [r for r in _cost_rows(bq) if r["task_id"] == "late"]
    assert len(late) == 1
    assert late[0]["upload_id"] is None


def test_add_rows_after_close_is_dropped_loudly(caplog):
    """A worker still in flight when close() ran must not write into a closed run.

    The frames are already concatenated and the uploader closed, so the rows cannot reach
    the result or BigQuery. add_rows must refuse them and say so at ERROR rather than
    accept them and let them vanish.
    """
    bq = FakeBigQueryClient()
    run, writes, _ = _make_run(bq)
    run.add_rows(pd.DataFrame([{"task_id": "in-time"}]))
    run.close()
    saved = len(writes)

    with caplog.at_level(logging.ERROR, logger="skyward.data.dataforseo.run"):
        run.add_rows(pd.DataFrame([{"task_id": "too-late"}]))

    # Assert on what the guard actually protects, not just on the cached close() result:
    # close() caches its DataFrame, so a late frame appended to _frames would not show up
    # in a second close() either way, and a late uploader.add() only buffers rather than
    # writing. Both would be silent losses.
    assert run._frames == [] or all("too-late" not in list(f["task_id"]) for f in run._frames)
    assert run.uploader._frames == [], "a closed run's uploader was handed more rows"
    assert len(writes) == saved
    assert list(run.close()["task_id"]) == ["in-time"]
    dropped = [r.getMessage() for r in caplog.records if run.job_id in r.getMessage()]
    assert dropped and "1 row(s)" in dropped[0]


def test_close_done_is_set_even_when_cost_writer_close_raises():
    bq = FakeBigQueryClient()
    run, _, _ = _make_run(bq)
    run.run_unit("a", _unit_fn("a"))

    def raise_close():
        raise RuntimeError("cost writer close blew up")

    run.cost_writer.close = raise_close

    with pytest.raises(RuntimeError, match="cost writer close blew up"):
        run.close()

    result_holder: dict = {}

    def call_close():
        result_holder["value"] = run.close()

    t = threading.Thread(target=call_close)
    t.start()
    t.join(timeout=2)
    assert not t.is_alive(), "second close() hung — _close_done was not set"
    assert isinstance(result_holder.get("value"), pd.DataFrame)
