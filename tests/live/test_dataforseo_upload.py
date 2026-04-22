"""Layer D — end-to-end BigQuery upload tests.

Layer B + C test fetch + parse + shape. This layer proves that pandas dtypes
actually map cleanly into the BQ schema defined in the migration manifest —
the thing that would fail silently on first production run otherwise.

Strategy:
  1. Create an ephemeral BQ dataset per test session (1-hour TTL safety net).
  2. For each endpoint under test, create the table from the migration manifest
     schema.
  3. Fetch live data with upload=False, monkey-patch the endpoint's DATASET
     attribute to the test dataset, then call upload() directly.
  4. Query the test table, verify row count matches the uploaded df.
  5. Teardown: drop the dataset (unless --keep-test-dataset is passed).

Covers all 11 DataForSEO tables. Pass `--keep-test-dataset` to skip teardown
for manual BQ inspection.

Note: log_upload_event writes to PROD Meta.upload_log. Test rows there are
filterable by source_program (contains `upload_<endpointname>`).

Gate: @pytest.mark.live + --run-live flag.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from google.cloud import bigquery

from scripts.migrate_dataforseo_manifest import (
    BACKLINKS_BACKLINKS_COLS,
    BACKLINKS_BULK_PAGES_SUMMARY_COLS,
    BACKLINKS_SUMMARY_COLS,
    DATAFORSEO_LABS_DOMAIN_RANK_OVERVIEW_COLS,
    DATAFORSEO_LABS_KEYWORD_OVERVIEW_COLS,
    DATAFORSEO_LABS_KEYWORD_SUGGESTIONS_COLS,
    DATAFORSEO_LABS_RANKED_KEYWORDS_COLS,
    DATAFORSEO_LABS_RELATED_KEYWORDS_COLS,
    DATAFORSEO_LABS_SEARCH_INTENT_COLS,
    KEYWORDS_DATA_SEARCH_VOLUME_COLS,
    METADATA_BLOCK_SQL,
    SERP_GOOGLE_ORGANIC_COLS,
)
from skyward.functions import generate_job_id
from tests.live.conftest import SEEDED_TEST_DOMAIN


# ---------------------------------------------------------------------------
# Ephemeral test dataset (session-scoped)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_bq_dataset(dfs_client_live, request):
    """Create a throwaway BQ dataset for upload tests. Teardown drops it
    unless `--keep-test-dataset` is passed.

    Dataset name: `DataForSEO_test_<uuid8>`. 1-hour default table TTL as a
    safety net in case teardown doesn't run (does not apply when kept).
    """
    bq = dfs_client_live.bq_client
    dataset_id = f"DataForSEO_test_{uuid.uuid4().hex[:8]}"
    full_id = f"{bq.client.project}.{dataset_id}"

    ds = bigquery.Dataset(full_id)
    ds.location = "US"
    keep = request.config.getoption("--keep-test-dataset")
    if not keep:
        ds.default_table_expiration_ms = 3600 * 1000
    bq.client.create_dataset(ds)
    print(f"\n[test_bq_dataset] Created: {full_id}")

    yield dataset_id

    if keep:
        print(f"\n[test_bq_dataset] KEEPING dataset: {full_id} (manual cleanup required)")
        return
    bq.client.delete_dataset(full_id, delete_contents=True, not_found_ok=True)


def _create_table_from_manifest(bq, project, dataset_id, table_name, schema_sql):
    full_table_id = f"{project}.{dataset_id}.{table_name}"
    ddl = f"""
    CREATE TABLE `{full_table_id}` (
    {schema_sql}
    )
    PARTITION BY DATE(ingest_timestamp)
    CLUSTER BY domain_id, job_id
    """
    bq.client.query(ddl).result()
    return full_table_id


def _count_rows(bq, full_table_id):
    query = f"SELECT COUNT(*) AS n FROM `{full_table_id}`"
    return list(bq.client.query(query).result())[0].n


def _upload_and_verify(bq, ep, df, full_table_id, test_dataset_id):
    """Monkey-patch ep.DATASET, upload, verify row count, restore DATASET."""
    assert not df.empty, "expected live() to return rows"
    expected_n = len(df)
    original_dataset = ep.DATASET
    try:
        ep.DATASET = test_dataset_id
        ep.upload(bq, df, job_id=generate_job_id())
    finally:
        ep.DATASET = original_dataset
    actual_n = _count_rows(bq, full_table_id)
    assert actual_n == expected_n, f"expected {expected_n} rows in BQ, got {actual_n}"


# ---------------------------------------------------------------------------
# Per-endpoint roundtrip tests (11 total)
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_backlinks_backlinks_upload_roundtrip(dfs_client_live, test_bq_dataset):
    bq = dfs_client_live.bq_client
    ep = dfs_client_live.backlinks_backlinks
    full_table_id = _create_table_from_manifest(
        bq, bq.client.project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + BACKLINKS_BACKLINKS_COLS,
    )
    df = ep.live(
        target="https://example.com",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        limit=5,
        upload=False,
    )
    _upload_and_verify(bq, ep, df, full_table_id, test_bq_dataset)


@pytest.mark.live
def test_backlinks_bulk_pages_summary_upload_roundtrip(dfs_client_live, test_bq_dataset):
    bq = dfs_client_live.bq_client
    ep = dfs_client_live.backlinks_bulk_pages_summary
    full_table_id = _create_table_from_manifest(
        bq, bq.client.project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + BACKLINKS_BULK_PAGES_SUMMARY_COLS,
    )
    df = asyncio.run(ep.live_all(
        targets=["example.com", "example.org"],
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    ))
    _upload_and_verify(bq, ep, df, full_table_id, test_bq_dataset)


@pytest.mark.live
def test_backlinks_summary_upload_roundtrip(dfs_client_live, test_bq_dataset):
    bq = dfs_client_live.bq_client
    ep = dfs_client_live.backlinks_summary
    full_table_id = _create_table_from_manifest(
        bq, bq.client.project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + BACKLINKS_SUMMARY_COLS,
    )
    df = ep.live(
        target="example.com",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    _upload_and_verify(bq, ep, df, full_table_id, test_bq_dataset)


@pytest.mark.live
def test_serp_google_organic_upload_roundtrip(dfs_client_live, test_bq_dataset):
    bq = dfs_client_live.bq_client
    ep = dfs_client_live.serp_google_organic
    full_table_id = _create_table_from_manifest(
        bq, bq.client.project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + SERP_GOOGLE_ORGANIC_COLS,
    )
    df = ep.live(
        target="pizza",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        location_code=2840,
        upload=False,
    )
    _upload_and_verify(bq, ep, df, full_table_id, test_bq_dataset)


@pytest.mark.live
def test_keyword_suggestions_upload_roundtrip(dfs_client_live, test_bq_dataset):
    bq = dfs_client_live.bq_client
    ep = dfs_client_live.dataforseo_labs_google_keyword_suggestions
    full_table_id = _create_table_from_manifest(
        bq, bq.client.project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + DATAFORSEO_LABS_KEYWORD_SUGGESTIONS_COLS,
    )
    df = ep.live(
        target="pizza",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        limit=5,
        upload=False,
    )
    _upload_and_verify(bq, ep, df, full_table_id, test_bq_dataset)


@pytest.mark.live
def test_related_keywords_upload_roundtrip(dfs_client_live, test_bq_dataset):
    bq = dfs_client_live.bq_client
    ep = dfs_client_live.dataforseo_labs_google_related_keywords
    full_table_id = _create_table_from_manifest(
        bq, bq.client.project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + DATAFORSEO_LABS_RELATED_KEYWORDS_COLS,
    )
    df = ep.live(
        target="pizza",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        limit=5,
        upload=False,
    )
    _upload_and_verify(bq, ep, df, full_table_id, test_bq_dataset)


@pytest.mark.live
def test_ranked_keywords_upload_roundtrip(dfs_client_live, test_bq_dataset):
    bq = dfs_client_live.bq_client
    ep = dfs_client_live.dataforseo_labs_google_ranked_keywords
    full_table_id = _create_table_from_manifest(
        bq, bq.client.project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + DATAFORSEO_LABS_RANKED_KEYWORDS_COLS,
    )
    df = ep.live(
        target="example.com",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        limit=5,
        upload=False,
    )
    _upload_and_verify(bq, ep, df, full_table_id, test_bq_dataset)


@pytest.mark.live
def test_keyword_overview_upload_roundtrip(dfs_client_live, test_bq_dataset):
    bq = dfs_client_live.bq_client
    ep = dfs_client_live.dataforseo_labs_google_keyword_overview
    full_table_id = _create_table_from_manifest(
        bq, bq.client.project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + DATAFORSEO_LABS_KEYWORD_OVERVIEW_COLS,
    )
    df = ep.live(
        target=["pizza", "pasta"],
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    _upload_and_verify(bq, ep, df, full_table_id, test_bq_dataset)


@pytest.mark.live
def test_search_intent_upload_roundtrip(dfs_client_live, test_bq_dataset):
    bq = dfs_client_live.bq_client
    ep = dfs_client_live.dataforseo_labs_google_search_intent
    full_table_id = _create_table_from_manifest(
        bq, bq.client.project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + DATAFORSEO_LABS_SEARCH_INTENT_COLS,
    )
    df = ep.live(
        target=["buy pizza", "pizza recipe"],
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    _upload_and_verify(bq, ep, df, full_table_id, test_bq_dataset)


@pytest.mark.live
def test_domain_rank_overview_upload_roundtrip(dfs_client_live, test_bq_dataset):
    bq = dfs_client_live.bq_client
    ep = dfs_client_live.dataforseo_labs_google_domain_rank_overview
    full_table_id = _create_table_from_manifest(
        bq, bq.client.project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + DATAFORSEO_LABS_DOMAIN_RANK_OVERVIEW_COLS,
    )
    df = ep.live(
        target="example.com",
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    _upload_and_verify(bq, ep, df, full_table_id, test_bq_dataset)


@pytest.mark.live
def test_search_volume_upload_roundtrip(dfs_client_live, test_bq_dataset):
    bq = dfs_client_live.bq_client
    ep = dfs_client_live.keywords_data_google_ads_search_volume
    full_table_id = _create_table_from_manifest(
        bq, bq.client.project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + KEYWORDS_DATA_SEARCH_VOLUME_COLS,
    )
    df = ep.live(
        target=["pizza", "pasta"],
        domain=SEEDED_TEST_DOMAIN,
        job_id=generate_job_id(),
        upload=False,
    )
    _upload_and_verify(bq, ep, df, full_table_id, test_bq_dataset)
