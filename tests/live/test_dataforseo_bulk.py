"""Layer C — bulk & concurrency tests for each DataForSEO endpoint.

Layer B (tests/live/test_dataforseo_live.py) verifies single-call schema fidelity.
Layer C verifies the multi-target, multi-batch, concurrent, and worker-pool paths
that production traffic actually exercises.

Per-test pattern: 6 targets with a small batch_size to force multiple batches.
Each test asserts:
  - Expected row count
  - endpoint_mode stamped on every row
  - task_id uniqueness (one per API call) to prove concurrency happened
  - domain/domain_id stamped consistently across all batches

Gate: @pytest.mark.live + --run-live flag. Skipped by default.
"""

from __future__ import annotations

import pytest

from skyward.functions import generate_job_id
from tests.live.conftest import SEEDED_TEST_DOMAIN


# ---------------------------------------------------------------------------
# Fan-out endpoints (one API call per target, concurrent via ThreadPoolExecutor)
# ---------------------------------------------------------------------------

URL_TARGETS_6 = [
    "https://example.com",
    "https://example.org",
    "https://example.net",
    "https://example.edu",
    "https://iana.org",
    "https://w3.org",
]

KEYWORD_TARGETS_6 = ["pizza", "pasta", "bread", "coffee", "tea", "water"]
DOMAIN_TARGETS_6 = ["example.com", "example.org", "example.net", "iana.org", "w3.org", "mozilla.org"]


def _assert_fan_out_result(df, *, expected_rows_min, expected_task_ids, endpoint_mode="live"):
    """Shared assertions for fan-out tests."""
    assert len(df) >= expected_rows_min, f"expected ≥{expected_rows_min} rows, got {len(df)}"
    assert "endpoint_mode" in df.columns
    assert (df["endpoint_mode"] == endpoint_mode).all(), "endpoint_mode inconsistent across rows"
    assert "task_id" in df.columns
    assert df["task_id"].nunique() == expected_task_ids, (
        f"expected {expected_task_ids} unique task_ids, got {df['task_id'].nunique()}"
    )
    assert "domain_id" in df.columns
    assert df["domain_id"].nunique() == 1, "domain_id should be uniform (caller's context)"
    assert "domain" in df.columns
    assert df["domain"].nunique() == 1, "domain should be uniform"


@pytest.mark.live
@pytest.mark.asyncio
async def test_backlinks_backlinks_bulk_multi_batch(dfs_client_live, cost_tracker, seeded_domain):
    """Fan-out via ThreadPoolExecutor: 6 URLs, batch_size=2 → 3 sequential batches."""
    ep = dfs_client_live.backlinks_backlinks
    df = await ep.live_all(
        targets=URL_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        batch_size=2,
        limit=3,
        upload=False,
    )
    _assert_fan_out_result(df, expected_rows_min=6, expected_task_ids=6)


@pytest.mark.live
@pytest.mark.asyncio
async def test_backlinks_summary_bulk_multi_batch(dfs_client_live, cost_tracker, seeded_domain):
    """Base live_all fan-out: 6 URLs, batch_size=2."""
    ep = dfs_client_live.backlinks_summary
    df = await ep.live_all(
        targets=URL_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        batch_size=2,
        upload=False,
    )
    _assert_fan_out_result(df, expected_rows_min=6, expected_task_ids=6)


@pytest.mark.live
@pytest.mark.asyncio
async def test_serp_google_organic_live_all_multi_batch(
    dfs_client_live, cost_tracker, seeded_domain
):
    """Fan-out: 6 keywords, batch_size=2."""
    ep = dfs_client_live.serp_google_organic
    df = await ep.live_all(
        targets=KEYWORD_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        location_code=2840,
        batch_size=2,
        upload=False,
    )
    # SERP returns many rows per keyword; 6 keywords × ≥1 row = ≥6 rows minimum
    _assert_fan_out_result(df, expected_rows_min=6, expected_task_ids=6)


@pytest.mark.live
@pytest.mark.asyncio
async def test_dataforseo_labs_keyword_suggestions_bulk_multi_batch(
    dfs_client_live, cost_tracker, seeded_domain
):
    """Fan-out: 6 seed keywords, batch_size=2."""
    ep = dfs_client_live.dataforseo_labs_google_keyword_suggestions
    df = await ep.live_all(
        targets=KEYWORD_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        batch_size=2,
        limit=3,
        upload=False,
    )
    _assert_fan_out_result(df, expected_rows_min=6, expected_task_ids=6)


@pytest.mark.live
@pytest.mark.asyncio
async def test_dataforseo_labs_related_keywords_bulk_multi_batch(
    dfs_client_live, cost_tracker, seeded_domain
):
    """Fan-out: 6 seed keywords, batch_size=2."""
    ep = dfs_client_live.dataforseo_labs_google_related_keywords
    df = await ep.live_all(
        targets=KEYWORD_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        batch_size=2,
        limit=3,
        upload=False,
    )
    _assert_fan_out_result(df, expected_rows_min=6, expected_task_ids=6)


@pytest.mark.live
@pytest.mark.asyncio
async def test_dataforseo_labs_ranked_keywords_bulk_multi_batch(
    dfs_client_live, cost_tracker, seeded_domain
):
    """Fan-out: 6 domains, batch_size=2. Uses `limit_per_domain` (not `limit`)
    to avoid a kwarg collision with the internal positional argument to
    `_fetch_domain_keywords`."""
    ep = dfs_client_live.dataforseo_labs_google_ranked_keywords
    df = await ep.live_all(
        targets=DOMAIN_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        batch_size=2,
        limit_per_domain=3,
        upload=False,
    )
    # Some domains may have fewer ranked keywords than limit_per_domain; assert
    # that at least 5 of 6 returned data (example.net typically has zero rankings).
    _assert_fan_out_result(df, expected_rows_min=5, expected_task_ids=5)


@pytest.mark.live
@pytest.mark.asyncio
async def test_dataforseo_labs_domain_rank_overview_bulk_multi_batch(
    dfs_client_live, cost_tracker, seeded_domain
):
    """Fan-out: 6 domains, batch_size=2. One row per domain that has data.
    Tiny domains (e.g., example.net) may return no rankings → zero rows for
    that target. Assert at least 5 of 6 return data."""
    ep = dfs_client_live.dataforseo_labs_google_domain_rank_overview
    df = await ep.live_all(
        targets=DOMAIN_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        batch_size=2,
        upload=False,
    )
    _assert_fan_out_result(df, expected_rows_min=5, expected_task_ids=5)


# ---------------------------------------------------------------------------
# In-call bulk endpoint (many targets per API call, auto-split)
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.asyncio
async def test_backlinks_bulk_pages_summary_multi_batch(
    dfs_client_live, cost_tracker, seeded_domain
):
    """In-call batching: 6 URLs, batch_size=2 → 3 API calls with 2 URLs each.

    Distinct from fan-out: each API call contains multiple targets, and the
    endpoint's custom live_all() splits into batches based on batch_size
    (capped at API max of 1000).
    """
    ep = dfs_client_live.backlinks_bulk_pages_summary
    df = await ep.live_all(
        targets=URL_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        batch_size=2,
        upload=False,
    )
    # 6 URLs → 6 rows (one per target), 3 API calls → 3 unique task_ids
    _assert_fan_out_result(df, expected_rows_min=6, expected_task_ids=3)


# ---------------------------------------------------------------------------
# List-input endpoints with live_all chunking (keyword_overview, search_intent,
# search_volume). Each takes a keyword list and chunks it into batches of up
# to batch_size per API call.
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_dataforseo_labs_keyword_overview_bulk_multi_batch(
    dfs_client_live, cost_tracker, seeded_domain
):
    """In-call batching: 6 keywords, batch_size=2 → 3 API calls with 2 kw each."""
    ep = dfs_client_live.dataforseo_labs_google_keyword_overview
    df = ep.live_all(
        keywords=KEYWORD_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        batch_size=2,
        upload=False,
    )
    # 6 keywords → 6 rows (one per keyword), 3 API calls → 3 unique task_ids
    _assert_fan_out_result(df, expected_rows_min=6, expected_task_ids=3)


@pytest.mark.live
def test_dataforseo_labs_search_intent_bulk_multi_batch(
    dfs_client_live, cost_tracker, seeded_domain
):
    """In-call batching: 6 keywords, batch_size=2 → 3 API calls."""
    ep = dfs_client_live.dataforseo_labs_google_search_intent
    df = ep.live_all(
        keywords=KEYWORD_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        batch_size=2,
        upload=False,
    )
    _assert_fan_out_result(df, expected_rows_min=6, expected_task_ids=3)


@pytest.mark.live
@pytest.mark.asyncio
async def test_keywords_data_search_volume_live_all_multi_batch(
    dfs_client_live, cost_tracker, seeded_domain
):
    """In-call batching: 6 keywords, batch_size=2 → 3 API calls.

    Distinct from `post_all` which uses `keywords_per_task` — `live_all` uses
    `batch_size`. See comment in test_keywords_data_search_volume_post_all_multi_batch
    re: the naming inconsistency."""
    ep = dfs_client_live.keywords_data_google_ads_search_volume
    df = await ep.live_all(
        targets=KEYWORD_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        batch_size=2,
        upload=False,
    )
    _assert_fan_out_result(df, expected_rows_min=6, expected_task_ids=3)


# ---------------------------------------------------------------------------
# POST/GET worker-pool endpoints
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.asyncio
async def test_keywords_data_search_volume_post_all_multi_batch(
    dfs_client_live, cost_tracker, seeded_domain
):
    """Multi-batch POST/GET: 6 keywords, keywords_per_task=2 → 3 tasks.

    Note: this endpoint uses `keywords_per_task` (not `batch_size`, which is
    what `serp_google_organic.post_all` uses). Naming inconsistency between
    the two post_all variants is a Phase-2 cleanup follow-up.
    """
    ep = dfs_client_live.keywords_data_google_ads_search_volume
    df = await ep.post_all(
        targets=KEYWORD_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        keywords_per_task=2,
        upload=False,
    )
    assert len(df) >= 6, f"expected ≥6 rows, got {len(df)}"
    assert (df["endpoint_mode"] == "standard").all()
    assert df["task_id"].nunique() == 3, (
        f"expected 3 unique task_ids (one per keywords_per_task chunk), got {df['task_id'].nunique()}"
    )


@pytest.mark.live
def test_serp_google_organic_post_all_multi_batch(
    dfs_client_live, cost_tracker, seeded_domain
):
    """Worker-pool POST/GET: 6 keywords, batch_size=2 → 3 batches, worker queue.

    Note: serp's post_all returns (results_df, failed_df) tuple, not a single df.
    """
    ep = dfs_client_live.serp_google_organic
    results_df, failed_df = ep.post_all(
        targets=KEYWORD_TARGETS_6,
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        location_code=2840,
        batch_size=2,
        num_workers=4,
        max_wait=300,
        upload=False,
    )
    assert len(failed_df) == 0, f"expected no failures, got {len(failed_df)}: {failed_df}"
    assert len(results_df) >= 6, f"expected ≥6 rows (1+ per keyword), got {len(results_df)}"
    assert (results_df["endpoint_mode"] == "standard").all()
    # worker-pool submits one task per keyword (batch_size is batch of submissions);
    # so 6 keywords → 6 unique task_ids
    assert results_df["task_id"].nunique() == 6, (
        f"expected 6 unique task_ids (one per keyword), got {results_df['task_id'].nunique()}"
    )
