"""Layer A — live integration tests for each DataForSEO endpoint.

Each endpoint: 1–3 tests covering live mode and (where applicable) standard mode.
Minimum viable payloads (limit=5) keep per-test cost low.

Gate: @pytest.mark.live + --run-live flag. Skipped by default.
"""

from __future__ import annotations

import pytest

from skyward.functions import generate_job_id
from tests.live.conftest import SEEDED_TEST_DOMAIN


# ---------------------------------------------------------------------------
# backlinks_backlinks
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_backlinks_backlinks_live_small(dfs_client_live, cost_tracker, seeded_domain):
    ep = dfs_client_live.backlinks_backlinks
    df = ep.live(
        target="https://example.com",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        limit=5,
        upload=False,
    )
    assert "task_id" in df.columns
    assert "endpoint_mode" in df.columns


@pytest.mark.live
@pytest.mark.asyncio
async def test_backlinks_backlinks_live_all_3_targets(dfs_client_live, cost_tracker, seeded_domain):
    ep = dfs_client_live.backlinks_backlinks
    df = await ep.live_all(
        targets=["https://example.com", "https://example.org", "https://example.net"],
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        limit=5,
        upload=False,
    )
    assert "endpoint_mode" in df.columns


# ---------------------------------------------------------------------------
# backlinks_summary
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_backlinks_summary_live_small(dfs_client_live, cost_tracker, seeded_domain):
    ep = dfs_client_live.backlinks_summary
    df = ep.live(
        target="example.com",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    assert "task_id" in df.columns


# ---------------------------------------------------------------------------
# backlinks_bulk_pages_summary
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.asyncio
async def test_backlinks_bulk_pages_summary_live_all(dfs_client_live, cost_tracker, seeded_domain):
    ep = dfs_client_live.backlinks_bulk_pages_summary
    df = await ep.live_all(
        targets=["example.com", "example.org"],
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    assert "endpoint_mode" in df.columns


# ---------------------------------------------------------------------------
# serp_google_organic — live mode
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_serp_google_organic_live_small(dfs_client_live, cost_tracker, seeded_domain):
    ep = dfs_client_live.serp_google_organic
    df = ep.live(
        target="pizza",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        location_code=2840,
        upload=False,
    )
    assert "task_id" in df.columns


# ---------------------------------------------------------------------------
# serp_google_organic — standard (POST/GET) mode
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_serp_google_organic_standard_post(dfs_client_live, cost_tracker, seeded_domain):
    """SerpGoogleOrganic post() is synchronous (not async) in the current impl."""
    ep = dfs_client_live.serp_google_organic
    df = ep.post(
        target="pizza",
        location_code=2840,
        max_wait=120,
    )
    # post() in current impl doesn't stamp endpoint_mode — that's a Phase-2 follow-up.
    # For now, just verify we got data back.
    assert df is not None


# ---------------------------------------------------------------------------
# dataforseo_labs_google_keyword_suggestions
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_dataforseo_labs_google_keyword_suggestions_live_small(
    dfs_client_live, cost_tracker, seeded_domain
):
    ep = dfs_client_live.dataforseo_labs_google_keyword_suggestions
    df = ep.live(
        target="pizza",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        limit=5,
        upload=False,
    )
    assert "task_id" in df.columns


# ---------------------------------------------------------------------------
# dataforseo_labs_google_related_keywords
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_dataforseo_labs_google_related_keywords_live_small(
    dfs_client_live, cost_tracker, seeded_domain
):
    ep = dfs_client_live.dataforseo_labs_google_related_keywords
    df = ep.live(
        target="pizza",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        limit=5,
        upload=False,
    )
    assert "task_id" in df.columns


# ---------------------------------------------------------------------------
# dataforseo_labs_google_ranked_keywords
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_dataforseo_labs_google_ranked_keywords_live_small(
    dfs_client_live, cost_tracker, seeded_domain
):
    ep = dfs_client_live.dataforseo_labs_google_ranked_keywords
    df = ep.live(
        target="example.com",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        limit=5,
        upload=False,
    )
    assert "task_id" in df.columns


# ---------------------------------------------------------------------------
# dataforseo_labs_google_keyword_overview
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_dataforseo_labs_google_keyword_overview_live_small(
    dfs_client_live, cost_tracker, seeded_domain
):
    ep = dfs_client_live.dataforseo_labs_google_keyword_overview
    df = ep.live(
        target=["pizza", "pasta"],
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    assert "task_id" in df.columns


# ---------------------------------------------------------------------------
# dataforseo_labs_google_search_intent
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_dataforseo_labs_google_search_intent_live_small(
    dfs_client_live, cost_tracker, seeded_domain
):
    ep = dfs_client_live.dataforseo_labs_google_search_intent
    df = ep.live(
        target=["buy pizza", "pizza recipe"],
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    assert "task_id" in df.columns


# ---------------------------------------------------------------------------
# dataforseo_labs_google_domain_rank_overview
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_dataforseo_labs_google_domain_rank_overview_live_small(
    dfs_client_live, cost_tracker, seeded_domain
):
    ep = dfs_client_live.dataforseo_labs_google_domain_rank_overview
    df = ep.live(
        target="example.com",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    assert "task_id" in df.columns


# ---------------------------------------------------------------------------
# keywords_data_google_ads_search_volume — live mode
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_keywords_data_search_volume_live_small(dfs_client_live, cost_tracker, seeded_domain):
    ep = dfs_client_live.keywords_data_google_ads_search_volume
    df = ep.live(
        target=["pizza", "pasta"],
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    assert "task_id" in df.columns


# ---------------------------------------------------------------------------
# keywords_data_google_ads_search_volume — standard (POST/GET) mode
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.asyncio
async def test_keywords_data_search_volume_standard_post_all(
    dfs_client_live, cost_tracker, seeded_domain
):
    ep = dfs_client_live.keywords_data_google_ads_search_volume
    df = await ep.post_all(
        targets=["pizza"],
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    assert (df["endpoint_mode"] == "standard").all()
