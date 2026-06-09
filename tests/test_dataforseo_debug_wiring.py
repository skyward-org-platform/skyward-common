"""Base-layer wiring for the include_debug_logs flag.

Verifies that `live()` / `live_all()` allocate a run-scoped DebugLogCollector when
`include_debug_logs=True`, thread it into `_fetch_live` via the `_debug_collector`
kwarg, and flush it once the run completes — and that the default (flag off) path is
untouched (no collector, no debug writes).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pandas as pd
import pytest

from skyward.data.dataforseo import DataForSEOClient
from skyward.data.dataforseo.base import BaseEndpoint
from skyward.data.dataforseo.debug_log import DebugLogCollector
from skyward.functions import generate_job_id


class _CaptureEndpoint(BaseEndpoint):
    LIVE_URL = "fake/endpoint/live"
    TABLE_NAME = "fake-endpoint"

    def __init__(self, client):
        super().__init__(client)
        self.received_collectors: list = []

    def _build_payload(self, target, **kwargs):
        return [{"target": target}]

    def _parse_response(self, response, target):
        return pd.DataFrame([{"keyword": target, "rank": 1, "task_id": "t"}])

    def _get_schema(self):
        return ["keyword", "rank", "task_id"]

    def _get_dedupe_keys(self):
        return ["keyword"]

    def _cast_types(self, df):
        return df

    def _fetch_live(self, target, **kwargs):
        collector = kwargs.get("_debug_collector")
        self.received_collectors.append(collector)
        if collector is not None:
            collector.record(
                {"endpoint": "fake", "target": target, "attempt": 1, "n_items": 1}
            )
        return self._parse_response({}, target)


@pytest.fixture
def bq():
    from tests.conftest import FakeBigQueryClient

    client = FakeBigQueryClient()
    client.log_upload_event = MagicMock()
    return client


@pytest.fixture
def endpoint(bq):
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    return _CaptureEndpoint(client)


def _debug_loads(bq):
    return [
        t for t in bq.client.loaded_tables
        if str(t["table_ref"]).endswith("DataForSEO.debug_request_logs")
    ]


def test_live_without_flag_passes_no_collector(endpoint, bq):
    endpoint.live(target="pizza", domain=None, job_id=generate_job_id(), upload=False)
    assert endpoint.received_collectors == [None]
    assert _debug_loads(bq) == []


def test_live_with_flag_passes_collector_and_flushes(endpoint, bq):
    endpoint.live(
        target="pizza",
        domain=None,
        job_id=generate_job_id(),
        upload=False,
        include_debug_logs=True,
    )
    assert len(endpoint.received_collectors) == 1
    assert isinstance(endpoint.received_collectors[0], DebugLogCollector)
    # the one recorded attempt was flushed to the shared debug table
    loads = _debug_loads(bq)
    assert len(loads) == 1
    assert len(loads[0]["df"]) == 1


def test_live_with_flag_but_no_bq_client_does_not_crash():
    client = DataForSEOClient(username="u", password="p", bq_client=None)
    ep = _CaptureEndpoint(client)
    df = ep.live(
        target="pizza",
        domain=None,
        job_id=generate_job_id(),
        upload=False,
        include_debug_logs=True,
    )
    assert not df.empty
    # no bq client → no collector allocated, no crash
    assert endpoint_received_none(ep)


def endpoint_received_none(ep) -> bool:
    return ep.received_collectors == [None]


def test_live_all_shares_one_collector_and_flushes_all(endpoint, bq):
    targets = ["a", "b", "c"]
    asyncio.run(
        endpoint.live_all(
            targets,
            domain=None,
            job_id=generate_job_id(),
            upload=False,
            include_debug_logs=True,
        )
    )
    collectors = [c for c in endpoint.received_collectors if c is not None]
    assert len(collectors) == 3
    # all three _fetch_live calls got the SAME collector instance
    assert len({id(c) for c in collectors}) == 1
    total = sum(len(t["df"]) for t in _debug_loads(bq))
    assert total == 3
