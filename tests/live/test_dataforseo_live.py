"""Layer A — live integration tests for each DataForSEO endpoint.

Each endpoint: 1–3 tests covering live mode and (where applicable) standard mode.
Minimum viable payloads (limit=5) keep per-test cost low.

Each test also checks semantic data invariants (enum membership, rank bounds,
timestamp ordering) — catches parser regressions that would pass a shape-only
test.

Gate: @pytest.mark.live + --run-live flag. Skipped by default.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from skyward.functions import generate_job_id
from tests.live.conftest import SEEDED_TEST_DOMAIN


INTENT_ENUM = {"informational", "commercial", "navigational", "transactional"}
COMPETITION_LEVEL_ENUM = {"LOW", "MEDIUM", "HIGH"}
COMPETITION_STRING_ENUM = {"LOW", "MEDIUM", "HIGH"}


def _assert_enum(df, col, allowed, *, allow_null=True):
    """Assert every value in df[col] is in the allowed set (or null if allowed)."""
    if col not in df.columns:
        return
    if allow_null:
        values = df[col].dropna().unique()
    else:
        values = df[col].unique()
    bad = set(values) - allowed
    assert not bad, f"{col}: unexpected values {bad} (allowed: {allowed})"


def _assert_range(df, col, lo, hi, *, allow_null=True):
    """Assert every value in df[col] is in [lo, hi]."""
    if col not in df.columns:
        return
    series = df[col].dropna() if allow_null else df[col]
    if series.empty:
        return
    assert (series >= lo).all(), f"{col}: values below {lo}: {series[series < lo].head().tolist()}"
    assert (series <= hi).all(), f"{col}: values above {hi}: {series[series > hi].head().tolist()}"


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
    # Invariants
    _assert_range(df, "domain_from_rank", 0, 1000)
    _assert_range(df, "backlink_to_spam_score", 0, 100)
    _assert_range(df, "backlink_spam_score", 0, 100)
    # first_seen <= last_seen for any row with both populated.
    # Parse explicitly since upload=False means _cast_types hasn't run.
    paired = df.dropna(subset=["first_seen", "last_seen"]).copy()
    if not paired.empty:
        paired["first_seen"] = pd.to_datetime(paired["first_seen"], utc=True)
        paired["last_seen"] = pd.to_datetime(paired["last_seen"], utc=True)
        assert (paired["first_seen"] <= paired["last_seen"]).all(), (
            "first_seen > last_seen on some rows"
        )


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
    _assert_range(df, "rank", 0, 1000)
    _assert_range(df, "backlinks_spam_score", 0, 100)
    _assert_range(df, "target_spam_score", 0, 100)
    # referring_domains should not exceed backlinks
    paired = df.dropna(subset=["backlinks", "referring_domains"])
    if not paired.empty:
        assert (paired["referring_domains"] <= paired["backlinks"]).all()


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
    # Every row must have a rank_absolute >= 1 and a populated item_type
    assert (df["rank_absolute"] >= 1).all(), "rank_absolute must be ≥ 1"
    assert df["item_type"].notna().all(), "item_type should never be null"
    # SERP datetime should be recent (within last 7 days). Parse explicitly
    # because _cast_types only runs during upload; upload=False leaves raw strings.
    serp_dt = pd.to_datetime(df["serp_datetime"], utc=True)
    cutoff = pd.Timestamp.utcnow() - timedelta(days=7)
    assert (serp_dt >= cutoff).all(), (
        "serp_datetime older than 7 days — stale SERP data?"
    )
    # position is either "left" or "right"
    _assert_enum(df, "position", {"left", "right"})


# ---------------------------------------------------------------------------
# serp_google_organic — standard (POST/GET) mode
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_serp_google_organic_standard_post(dfs_client_live, cost_tracker, seeded_domain):
    """SerpGoogleOrganic post() is synchronous (not async) in the current impl."""
    ep = dfs_client_live.serp_google_organic
    df = ep.post(
        target="pizza",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        location_code=2840,
        max_wait=120,
        upload=False,
    )
    assert df is not None
    assert "endpoint_mode" in df.columns
    assert (df["endpoint_mode"] == "standard").all()


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
    _assert_enum(df, "competition_level", COMPETITION_LEVEL_ENUM)
    _assert_enum(df, "main_intent", INTENT_ENUM)
    _assert_range(df, "keyword_difficulty", 0, 100)
    _assert_range(df, "competition", 0.0, 1.0)
    _assert_range(df, "words_count", 1, 50)


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
    _assert_enum(df, "competition_level", COMPETITION_LEVEL_ENUM)
    _assert_enum(df, "search_intent_main", INTENT_ENUM)
    _assert_range(df, "depth", 0, 10)
    _assert_range(df, "keyword_difficulty", 0, 100)


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
    _assert_range(df, "rank", 1, 1000)
    _assert_range(df, "rank_absolute", 1, 1000)
    _assert_enum(df, "competition_level", COMPETITION_LEVEL_ENUM)
    _assert_enum(df, "main_intent", INTENT_ENUM)
    # rank_is_up and rank_is_down should never both be true on the same row
    both_up_down = df.dropna(subset=["rank_is_up", "rank_is_down"])
    if not both_up_down.empty:
        assert not (both_up_down["rank_is_up"] & both_up_down["rank_is_down"]).any(), (
            "rank_is_up and rank_is_down both True on the same row"
        )


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
    _assert_enum(df, "competition_level", COMPETITION_LEVEL_ENUM)
    _assert_enum(df, "main_intent", INTENT_ENUM)
    _assert_range(df, "keyword_difficulty", 0, 100)
    _assert_range(df, "competition", 0.0, 1.0)


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
    _assert_enum(df, "search_intent", INTENT_ENUM)
    _assert_range(df, "intent_probability", 0.0, 1.0)


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
    # organic_count must equal the sum of all 12 organic position buckets
    pos_cols = [
        "organic_pos_1", "organic_pos_2_3", "organic_pos_4_10",
        "organic_pos_11_20", "organic_pos_21_30", "organic_pos_31_40",
        "organic_pos_41_50", "organic_pos_51_60", "organic_pos_61_70",
        "organic_pos_71_80", "organic_pos_81_90", "organic_pos_91_100",
    ]
    for _, row in df.iterrows():
        if pd.notna(row["organic_count"]):
            bucket_sum = sum(row[c] for c in pos_cols if pd.notna(row[c]))
            assert bucket_sum == row["organic_count"], (
                f"organic_count ({row['organic_count']}) != bucket sum ({bucket_sum})"
            )


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
    _assert_enum(df, "competition", COMPETITION_STRING_ENUM)
    _assert_range(df, "competition_index", 0, 100)
    _assert_range(df, "cpc", 0.0, 10000.0)
    _assert_range(df, "search_volume", 0, 10_000_000_000)


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
