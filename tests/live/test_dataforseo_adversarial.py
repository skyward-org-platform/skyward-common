"""Layer C — adversarial / failure-mode tests.

Most of these fail before any HTTP call (validation errors). Those that need
a real request use mocked responses (no cost).

All tests are tagged @pytest.mark.live for uniform gating with Layer A.
"""

from __future__ import annotations

import pandas as pd
import pytest

from skyward.data.dataforseo import DataForSEOClient, IncompleteTaskError
from skyward.functions import generate_job_id


@pytest.fixture
def client(fake_bq):
    return DataForSEOClient(username="u", password="p", bq_client=fake_bq)


# ---------------------------------------------------------------------------
# Validation failures — no HTTP
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_rejects_non_uuid_job_id(client):
    ep = client.backlinks_backlinks
    with pytest.raises(ValueError, match="not a valid UUID"):
        ep.live(target="https://example.com", domain=None, job_id="test-123")


@pytest.mark.live
def test_rejects_both_domain_and_domain_id(client):
    ep = client.backlinks_backlinks
    with pytest.raises(ValueError, match="exactly one"):
        ep.live(
            target="https://example.com",
            domain="example.com",
            domain_id=1,
            job_id=generate_job_id(),
        )


@pytest.mark.live
def test_rejects_neither_domain_nor_domain_id(client):
    ep = client.backlinks_backlinks
    with pytest.raises(ValueError, match="exactly one"):
        ep.live(target="https://example.com", job_id=generate_job_id())


# ---------------------------------------------------------------------------
# Empty-result handling — no HTTP
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_empty_result_skips_upload(client, monkeypatch):
    ep = client.backlinks_backlinks
    monkeypatch.setattr(ep, "_fetch_live", lambda target, **kw: pd.DataFrame())

    df = ep.live(
        target="https://example.com",
        domain=None,
        job_id=generate_job_id(),
    )
    assert df.empty
    # Upload skipped → no rows in fake bq's loaded_tables
    assert client.bq_client.client.loaded_tables == []


# ---------------------------------------------------------------------------
# Timeout path for POST/GET — synthetic, no HTTP
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_search_volume_post_timeout_raises_incomplete_task(client, monkeypatch):
    ep = client.keywords_data_google_ads_search_volume
    monkeypatch.setattr(ep, "_task_post", lambda **kw: ["task-xyz"])
    monkeypatch.setattr(ep, "_tasks_ready", lambda debug=False: [])
    ep.config.task_poll_interval = 0
    ep.config.task_total_timeout = 1

    with pytest.raises(IncompleteTaskError) as exc:
        ep.post(
            target=["pizza"],
            domain=None,
            job_id=generate_job_id(),
            upload=False,
        )
    assert exc.value.task_ids == ["task-xyz"]


# ---------------------------------------------------------------------------
# Malformed response — mocked
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_malformed_response_returns_empty_df(client, monkeypatch):
    ep = client.backlinks_backlinks
    # Respond with garbage that _parse_response can't destructure
    monkeypatch.setattr(client, "_post", lambda *a, **kw: {"random": "garbage"})
    ep.config.max_retries = 1
    ep.config.retry_delay = 0

    df = ep.live(
        target="https://example.com",
        domain=None,
        job_id=generate_job_id(),
        upload=False,
    )
    # Either empty or has columns but no rows
    assert df.empty or len(df) == 0


# ---------------------------------------------------------------------------
# Invalid credentials — mocked
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_invalid_credentials_returns_empty(client, monkeypatch):
    ep = client.backlinks_backlinks
    # _post returns None on auth failure
    monkeypatch.setattr(client, "_post", lambda *a, **kw: None)
    ep.config.max_retries = 1
    ep.config.retry_delay = 0

    df = ep.live(
        target="https://example.com",
        domain=None,
        job_id=generate_job_id(),
        upload=False,
    )
    assert df.empty


# ---------------------------------------------------------------------------
# Upload rejects non-UUID job_id
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_upload_rejects_non_uuid_job_id(client):
    ep = client.backlinks_backlinks
    df = pd.DataFrame([{"url": "x", "domain": "y", "task_id": "t"}])
    with pytest.raises(ValueError, match="not a valid UUID"):
        ep.upload(client.bq_client, df, job_id="nope")


# ---------------------------------------------------------------------------
# Upload on empty df is a no-op
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_upload_on_empty_df_is_noop(client):
    ep = client.backlinks_backlinks
    ep.upload(client.bq_client, pd.DataFrame(), job_id=generate_job_id())
    assert client.bq_client.client.loaded_tables == []
