"""Integration: keyword_suggestions / related_keywords `_fetch_live` capture.

Stubs the client's `_post` with canned DFS responses and asserts the wired retry
loop records one debug row per attempt (with the right endpoint, n_items,
is_terminal, http_status, task_status_code) — and that with no collector the path is
unchanged.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from skyward.data.dataforseo import DataForSEOClient
from skyward.data.dataforseo.debug_log import DebugLogCollector
from skyward.data.dataforseo.endpoints.dataforseo_labs_google_keyword_suggestions import (
    DataforseoLabsGoogleKeywordSuggestions,
)
from skyward.data.dataforseo.endpoints.dataforseo_labs_google_related_keywords import (
    DataforseoLabsGoogleRelatedKeywords,
)
from skyward.functions import generate_job_id

SUGGEST_OK = {
    "status_code": 20000,
    "tasks": [{
        "id": "t1", "status_code": 20000, "status_message": "Ok.", "cost": 0.0101,
        "result": [{
            "se_type": "google", "seed_keyword": "busbank",
            "location_code": 2840, "language_code": "en",
            "items": [{"keyword": "bus rental", "keyword_info": {"search_volume": 100}}],
        }],
    }],
}
SUGGEST_EMPTY = {
    "status_code": 20000,
    "tasks": [{"id": "t1", "status_code": 20000, "status_message": "Ok.", "cost": 0.01, "result": []}],
}
RELATED_OK = {
    "status_code": 20000,
    "tasks": [{
        "id": "t1", "status_code": 20000, "status_message": "Ok.", "cost": 0.0101,
        "result": [{
            "seed_keyword": "busbank", "location_code": 2840, "language_code": "en",
            "items": [{"keyword_data": {"keyword": "charter bus", "keyword_info": {"search_volume": 50}}, "depth": 1}],
        }],
    }],
}


@pytest.fixture
def bq():
    from tests.conftest import FakeBigQueryClient

    client = FakeBigQueryClient()
    client.log_upload_event = MagicMock()
    return client


def _stub_post(responses):
    state = {"i": 0}

    def _post(url, payload, max_retries=None, retry_delay=None, status_sink=None, session=None):
        resp = responses[min(state["i"], len(responses) - 1)]
        state["i"] += 1
        if status_sink is not None:
            status_sink["http_status"] = 200 if resp is not None else None
            status_sink["error"] = ""
        return resp

    return _post


def _all_rows(bq) -> pd.DataFrame:
    return pd.concat([t["df"] for t in bq.client.loaded_tables], ignore_index=True)


def test_keyword_suggestions_records_success(bq):
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    client._post = _stub_post([SUGGEST_OK])
    ep = DataforseoLabsGoogleKeywordSuggestions(client)
    col = DebugLogCollector(bq, job_id=generate_job_id(), flush_threshold=100)

    df = ep._fetch_live("busbank", _debug_collector=col)
    col.flush()

    assert not df.empty
    row = _all_rows(bq).iloc[0]
    assert row["endpoint"] == "keyword_suggestions"
    assert row["target"] == "busbank"
    assert row["n_items"] == 1
    assert bool(row["is_terminal"]) is True
    assert row["http_status"] == 200
    assert row["task_status_code"] == 20000
    assert row["task_cost"] == 0.0101


def test_keyword_suggestions_records_every_retry_when_empty(bq):
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    client._post = _stub_post([SUGGEST_EMPTY])
    ep = DataforseoLabsGoogleKeywordSuggestions(client)
    col = DebugLogCollector(bq, job_id=generate_job_id(), flush_threshold=100)

    df = ep._fetch_live("deadseed", _debug_collector=col, max_retries=3, retry_delay=0)
    col.flush()

    assert df.empty
    rows = _all_rows(bq)
    assert len(rows) == 3
    assert (rows["n_items"] == 0).all()
    assert bool(rows.iloc[0]["is_terminal"]) is False
    assert bool(rows.iloc[2]["is_terminal"]) is True


def test_keyword_suggestions_no_collector_is_unchanged(bq):
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    client._post = _stub_post([SUGGEST_OK])
    ep = DataforseoLabsGoogleKeywordSuggestions(client)

    df = ep._fetch_live("busbank", _debug_collector=None)
    assert not df.empty
    assert bq.client.loaded_tables == []


def test_related_keywords_records_success(bq):
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    client._post = _stub_post([RELATED_OK])
    ep = DataforseoLabsGoogleRelatedKeywords(client)
    col = DebugLogCollector(bq, job_id=generate_job_id(), flush_threshold=100)

    df = ep._fetch_live("busbank", _debug_collector=col)
    col.flush()

    assert not df.empty
    row = _all_rows(bq).iloc[0]
    assert row["endpoint"] == "related_keywords"
    assert row["target"] == "busbank"
    assert row["n_items"] == 1
    assert row["task_status_code"] == 20000
