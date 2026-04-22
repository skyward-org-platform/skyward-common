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
  5. Teardown: drop the dataset.

Covers two endpoints per data-shape category:
  - backlinks_summary: INT/FLOAT/TIMESTAMP/STRING scalars, JSON-stringified dict cols
  - serp_google_organic: heavy JSON-stringification (`item`, `data`, `refinement_chips`, `item_types`)

Note: log_upload_event is called during upload() and writes to the PROD
Meta.upload_log table. Test rows there are filterable by upload_id and the
source_program field (contains `upload_<endpointname>`) if cleanup becomes
necessary — for now we tolerate a few test rows in that log.

Gate: @pytest.mark.live + --run-live flag. Skipped by default.
"""

from __future__ import annotations

import uuid

import pytest
from google.cloud import bigquery

from scripts.migrate_dataforseo_manifest import (
    BACKLINKS_SUMMARY_COLS,
    METADATA_BLOCK_SQL,
    SERP_GOOGLE_ORGANIC_COLS,
)
from skyward.functions import generate_job_id
from tests.live.conftest import SEEDED_TEST_DOMAIN


# ---------------------------------------------------------------------------
# Ephemeral test dataset (session-scoped)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_bq_dataset(dfs_client_live):
    """Create a throwaway BQ dataset for upload tests. Teardown drops it.

    Dataset name: `DataForSEO_test_<uuid8>`. 1-hour default table TTL as a
    safety net in case teardown doesn't run.
    """
    bq = dfs_client_live.bq_client
    dataset_id = f"DataForSEO_test_{uuid.uuid4().hex[:8]}"
    full_id = f"{bq.client.project}.{dataset_id}"

    ds = bigquery.Dataset(full_id)
    ds.location = "US"
    ds.default_table_expiration_ms = 3600 * 1000
    bq.client.create_dataset(ds)

    yield dataset_id

    bq.client.delete_dataset(full_id, delete_contents=True, not_found_ok=True)


def _create_table_from_manifest(bq, project, dataset_id, table_name, schema_sql):
    """Create a BQ table in the test dataset matching the migration manifest schema.

    Mirrors the CREATE TABLE DDL the migration script emits: partitioned by
    ingest_timestamp, clustered by domain_id + job_id.
    """
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


# ---------------------------------------------------------------------------
# Scalar-heavy endpoint: backlinks_summary
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_backlinks_summary_upload_roundtrip(dfs_client_live, test_bq_dataset):
    """End-to-end: fetch real data → upload to test BQ table → verify row count.

    Exercises INT/FLOAT/TIMESTAMP/STRING casts + 6 JSON-stringified cols
    (referring_links_*). If pandas dtypes don't map to the declared BQ schema,
    load_table_from_dataframe raises — this test catches that.
    """
    bq = dfs_client_live.bq_client
    project = bq.client.project

    ep = dfs_client_live.backlinks_summary
    original_dataset = ep.DATASET

    full_table_id = _create_table_from_manifest(
        bq, project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + BACKLINKS_SUMMARY_COLS,
    )

    try:
        df = ep.live(
            target="example.com",
            domain=SEEDED_TEST_DOMAIN,
            job_id=generate_job_id(),
            upload=False,
        )
        assert not df.empty, "expected live() to return rows"
        expected_n = len(df)

        ep.DATASET = test_bq_dataset
        ep.upload(bq, df, job_id=generate_job_id())
    finally:
        ep.DATASET = original_dataset

    actual_n = _count_rows(bq, full_table_id)
    assert actual_n == expected_n, f"expected {expected_n} rows in BQ, got {actual_n}"


# ---------------------------------------------------------------------------
# JSON-heavy endpoint: serp_google_organic
# ---------------------------------------------------------------------------

@pytest.mark.live
def test_serp_google_organic_upload_roundtrip(dfs_client_live, test_bq_dataset):
    """End-to-end: SERP → upload → verify.

    Exercises heavy JSON-stringification (`item` + `data` + `refinement_chips`
    + `item_types` are all JSON blobs per row). If any of the stringify paths
    emit something BQ won't load (e.g., non-serializable types, oversized
    strings), this test catches it.
    """
    bq = dfs_client_live.bq_client
    project = bq.client.project

    ep = dfs_client_live.serp_google_organic
    original_dataset = ep.DATASET

    full_table_id = _create_table_from_manifest(
        bq, project, test_bq_dataset, ep.TABLE_NAME,
        METADATA_BLOCK_SQL + SERP_GOOGLE_ORGANIC_COLS,
    )

    try:
        df = ep.live(
            target="pizza",
            domain=SEEDED_TEST_DOMAIN,
            job_id=generate_job_id(),
            location_code=2840,
            upload=False,
        )
        assert not df.empty, "expected live() to return rows"
        expected_n = len(df)

        ep.DATASET = test_bq_dataset
        ep.upload(bq, df, job_id=generate_job_id())
    finally:
        ep.DATASET = original_dataset

    actual_n = _count_rows(bq, full_table_id)
    assert actual_n == expected_n, f"expected {expected_n} rows in BQ, got {actual_n}"
