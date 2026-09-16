"""`_post` retries a 2xx response DataForSEO already billed but whose body could not be
parsed or used (e.g. truncated JSON) — the retry re-sends the same payload, DFS bills it
again, and the FIRST charge was never recorded anywhere. This is a money-visibility gap,
not a correctness bug: `record_http` only runs after `resp.json()` succeeds (see commit
9d9e03e, which deliberately keeps a billed success out of the retry `try` so it can never
be discarded and re-sent — this fix must not touch that).

These tests pin down the additive fix: a zero-cost marker row goes into cost_log (via the
active RunUnit) whenever `_post` hits a 2xx it cannot use, so the gap is visible in
BigQuery instead of only in logs — and NOT for failures DataForSEO did not bill for
(connection errors, non-2xx statuses).
"""

from __future__ import annotations

import json

import pandas as pd
import requests

from skyward.data.dataforseo import DataForSEOClient
from skyward.data.dataforseo.estimates import CostEstimate, RunPlan
from skyward.data.dataforseo.run import RunContext, RunUnit
from skyward.functions import generate_job_id
from tests.conftest import FakeBigQueryClient

BASE = "https://api.dataforseo.com/v3"
LIVE_URL = f"{BASE}/dataforseo_labs/google/keyword_suggestions/live"

GOOD = {"tasks": [{"id": "t1", "status_code": 20000, "cost": 0.02, "result": [{"items": [{}]}]}]}


class _FlakyResp:
    """A response whose `.json()` can be made to fail independently of its status."""

    def __init__(self, status_code, json_data=None, json_exc=None):
        self.status_code = status_code
        self._json_data = json_data
        self._json_exc = json_exc

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data


class _SeqSession:
    """Returns (or raises from) the next behavior in a fixed sequence, one per POST."""

    def __init__(self, behaviors):
        self._behaviors = list(behaviors)
        self.calls = 0

    def post(self, url, json=None, timeout=None):
        self.calls += 1
        behavior = self._behaviors[min(self.calls - 1, len(self._behaviors) - 1)]
        return behavior()


def _client(bq=None):
    return DataForSEOClient(username="u", password="p", bq_client=bq)


def _make_run(bq, client=None):
    targets = ("kw",)
    plan = RunPlan("ep", "live", len(targets), len(targets), len(targets), tuple(targets))
    est = CostEstimate(0.0, 0.0, 0.0, "list_price", plan)
    client = client or _client(bq)
    run = RunContext(
        client=client, endpoint_key="ep", job_id=generate_job_id(), plan=plan, estimate=est,
        endpoint_mode="live", upload=True,
        write=lambda df, uid: None,
        stamp=lambda df: df.assign(endpoint_mode="live"),
        empty_columns=["task_id", "endpoint_mode"],
    )
    return run, client


def _cost_rows(bq):
    return [r for ins in bq.client.inserted_rows if ins["table"].endswith(".cost_log")
            for r in ins["rows"]]


def _run_post(run, client, session):
    def fn():
        data = client._post(LIVE_URL, [{"keyword": "kw"}], session=session,
                            max_retries=2, retry_delay=0)
        assert data == GOOD
        return pd.DataFrame([{"task_id": "t1"}])
    run.run_unit("kw", fn)
    run.close()


def test_billed_2xx_unparseable_body_then_success_records_marker_and_cost_row():
    bq = FakeBigQueryClient()
    run, client = _make_run(bq)
    session = _SeqSession([
        lambda: _FlakyResp(200, json_exc=ValueError("Expecting value: line 1 column 1")),
        lambda: _FlakyResp(200, json_data=GOOD),
    ])

    _run_post(run, client, session)

    costs = _cost_rows(bq)
    assert len(costs) == 2
    marker = next(r for r in costs if r["task_id"] is None)
    normal = next(r for r in costs if r["task_id"] == "t1")
    assert marker["cost_usd"] == 0.0
    assert marker["http_status"] == 200
    assert json.loads(marker["price_inputs"])["unattributed_billed_retry"] is True
    assert normal["cost_usd"] == 0.02
    # The marker must never be mistaken for real spend.
    assert run.spent_usd == 0.02


def test_connection_error_before_response_records_no_marker():
    bq = FakeBigQueryClient()
    run, client = _make_run(bq)

    def boom():
        raise requests.exceptions.ConnectionError("dns fail")

    session = _SeqSession([boom, lambda: _FlakyResp(200, json_data=GOOD)])

    _run_post(run, client, session)

    costs = _cost_rows(bq)
    assert len(costs) == 1
    assert costs[0]["task_id"] == "t1"


def test_non_2xx_then_success_records_no_marker():
    bq = FakeBigQueryClient()
    run, client = _make_run(bq)
    session = _SeqSession([lambda: _FlakyResp(500), lambda: _FlakyResp(200, json_data=GOOD)])

    _run_post(run, client, session)

    costs = _cost_rows(bq)
    assert len(costs) == 1
    assert costs[0]["task_id"] == "t1"


def test_marker_recording_failure_does_not_break_caller(monkeypatch):
    bq = FakeBigQueryClient()
    run, client = _make_run(bq)
    session = _SeqSession([
        lambda: _FlakyResp(200, json_exc=ValueError("bad body")),
        lambda: _FlakyResp(200, json_data=GOOD),
    ])

    def boom(*args, **kwargs):
        raise RuntimeError("marker bookkeeping exploded")

    monkeypatch.setattr(RunUnit, "record_unattributed_billed_retry", boom)

    _run_post(run, client, session)  # must not raise

    costs = _cost_rows(bq)
    assert len(costs) == 1  # only the successful attempt; marker dropped silently
    assert costs[0]["task_id"] == "t1"
