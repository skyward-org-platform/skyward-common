"""Unit tests for the new BaseEndpoint in base.py.

Uses a throwaway `_FakeEndpoint` subclass that short-circuits the API call to
verify the orchestration layer (validation, domain resolution, metadata stamping,
upload flow) without requiring real HTTP.
"""

from __future__ import annotations

import pandas as pd
import pytest

from skyward.data.dataforseo import DataForSEOClient, ClientConfig
from skyward.data.dataforseo.base import BaseEndpoint
from skyward.functions import generate_job_id


class _FakeEndpoint(BaseEndpoint):
    LIVE_URL = "fake/endpoint/live"
    TABLE_NAME = "fake-endpoint"

    mock_rows: list = []
    mock_task_id: str = "task-abc-123"

    def _build_payload(self, target, **kwargs):
        return [{"target": target}]

    def _parse_response(self, response, target):
        rows = [{**r, "task_id": self.mock_task_id} for r in self.mock_rows]
        return pd.DataFrame(rows)

    def _get_schema(self):
        return ["keyword", "rank", "task_id"]

    def _get_dedupe_keys(self):
        return ["keyword"]

    def _cast_types(self, df):
        return df

    def _fetch_live(self, target, **kwargs):
        return self._parse_response({}, target)


@pytest.fixture
def dfs_client(fake_bq):
    return DataForSEOClient(username="u", password="p", bq_client=fake_bq)


@pytest.fixture
def fake_endpoint(dfs_client):
    ep = _FakeEndpoint(dfs_client)
    ep.mock_rows = [{"keyword": "pizza", "rank": 1}]
    ep.mock_task_id = "task-abc-123"
    return ep


def test_live_rejects_invalid_job_id(fake_endpoint):
    with pytest.raises(ValueError, match="not a valid UUID"):
        fake_endpoint.live(target="pizza", domain=None, job_id="test-123")


def test_live_accepts_generated_job_id(fake_endpoint):
    df = fake_endpoint.live(
        target="pizza",
        domain=None,
        job_id=generate_job_id(),
        upload=False,
    )
    assert not df.empty


def test_live_rejects_both_domain_and_domain_id(fake_endpoint):
    with pytest.raises(ValueError, match="exactly one"):
        fake_endpoint.live(
            target="pizza",
            domain="example.com",
            domain_id=1,
            job_id=generate_job_id(),
        )


def test_live_rejects_neither_domain_nor_domain_id(fake_endpoint):
    with pytest.raises(ValueError, match="exactly one"):
        fake_endpoint.live(target="pizza", job_id=generate_job_id())


def test_live_accepts_explicit_none_for_domain(fake_endpoint):
    df = fake_endpoint.live(
        target="pizza",
        domain=None,
        job_id=generate_job_id(),
        upload=False,
    )
    assert df["domain_id"].isna().all()
    assert df["domain"].isna().all()


def test_live_empty_result_skips_upload_and_returns_empty_df(fake_endpoint, capsys):
    fake_endpoint.mock_rows = []
    df = fake_endpoint.live(
        target="pizza",
        domain=None,
        job_id=generate_job_id(),
    )
    assert df.empty
    captured = capsys.readouterr()
    assert "No rows returned" in captured.out
    assert not fake_endpoint._client.bq_client.client.loaded_tables


def test_live_stamps_endpoint_mode_live(fake_endpoint):
    df = fake_endpoint.live(
        target="pizza",
        domain=None,
        job_id=generate_job_id(),
        upload=False,
    )
    assert (df["endpoint_mode"] == "live").all()


def test_live_stamps_task_id_from_parse_response(fake_endpoint):
    fake_endpoint.mock_task_id = "t-xyz"
    df = fake_endpoint.live(
        target="pizza",
        domain=None,
        job_id=generate_job_id(),
        upload=False,
    )
    assert (df["task_id"] == "t-xyz").all()


def test_upload_stamps_job_id_upload_id_and_timestamp(fake_endpoint):
    df = pd.DataFrame([{
        "keyword": "pizza", "rank": 1, "task_id": "t",
        "domain_id": None, "domain": None, "endpoint_mode": "live",
    }])
    job_id = generate_job_id()
    fake_endpoint.upload(fake_endpoint._client.bq_client, df, job_id=job_id)

    loaded = fake_endpoint._client.bq_client.client.loaded_tables
    assert len(loaded) == 1
    written = loaded[0]["df"]
    assert (written["job_id"] == job_id).all()
    assert written["upload_id"].notna().all()
    assert written["upload_id"].nunique() == 1
    assert written["ingest_timestamp"].notna().all()


def test_upload_rejects_invalid_job_id(fake_endpoint):
    df = pd.DataFrame([{
        "keyword": "pizza", "rank": 1, "task_id": "t",
        "domain_id": None, "domain": None, "endpoint_mode": "live",
    }])
    with pytest.raises(ValueError, match="not a valid UUID"):
        fake_endpoint.upload(fake_endpoint._client.bq_client, df, job_id="test")


def test_live_default_auto_uploads(fake_endpoint):
    fake_endpoint.live(
        target="pizza",
        domain=None,
        job_id=generate_job_id(),
    )
    assert len(fake_endpoint._client.bq_client.client.loaded_tables) == 1


def test_live_upload_false_skips_upload(fake_endpoint):
    fake_endpoint.live(
        target="pizza",
        domain=None,
        job_id=generate_job_id(),
        upload=False,
    )
    assert fake_endpoint._client.bq_client.client.loaded_tables == []
