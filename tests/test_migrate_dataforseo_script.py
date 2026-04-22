"""Tests for scripts/migrate_dataforseo_tables.py using a fake BQ client."""

from __future__ import annotations

import pandas as pd
import pytest
from click.testing import CliRunner

from scripts import migrate_dataforseo_tables as mod


def test_phase_setup_dry_run_prints_schema_snapshot_and_create(fake_bq):
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--phase=setup", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "CREATE SCHEMA" in result.output
        assert "DataForSEO_backup_04_20_2026" in result.output
        assert "SNAPSHOT" in result.output
        assert "CREATE NEW" in result.output
        assert "PARTITION BY DATE(ingest_timestamp)" in result.output
        assert "CLUSTER BY domain_id, job_id" in result.output
        # Legacy non-endpoint backup gets snapshotted too
        assert "serp_google_organic_live_advanced_backup" in result.output
        # In-place migration (ranked_keywords) gets a drop-first step
        assert "DROP OLD (in-place)" in result.output
    finally:
        mod._bq_override = None


def test_phase_migrate_dry_run_prints_copy_and_drop(fake_bq):
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--phase=migrate", "--dry-run"])
        assert result.exit_code == 0, result.output
        # TRUNCATE before INSERT makes phase 2 idempotent.
        assert "TRUNCATE TABLE" in result.output
        assert "COPY DATA" in result.output
        # Copy always sources from the backup dataset.
        assert "DataForSEO_backup_04_20_2026" in result.output
        # Domain-preservation notes should appear per endpoint.
        assert "domain preserved from source" in result.output
        assert "domain=NULL (old column dropped per semantic review)" in result.output
        # Drops old sources + legacy backup.
        assert "DROP OLD" in result.output
        assert "DROP LEGACY" in result.output
    finally:
        mod._bq_override = None


def test_phase_backfill_dry_run_replaces_sentinel_placeholder(fake_bq):
    """Phase 3 step 3a must UPDATE rows where job_id/upload_id equal the
    'pending-backfill' sentinel stamped by phase 2 (not NULL anymore)."""
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--phase=backfill", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "pending-backfill" in result.output
        assert "WHERE job_id = 'pending-backfill' OR upload_id = 'pending-backfill'" in result.output
    finally:
        mod._bq_override = None


def test_build_copy_sql_uses_coalesce_sentinel_for_notnull_metadata(fake_bq):
    """NOT NULL metadata columns (job_id, upload_id, ingest_timestamp) must
    tolerate NULL values in the source via COALESCE — bulk_pages_summary
    had NULL ingest_timestamps in prod, which broke the original migrate."""
    old_cols = pd.DataFrame([
        {"column_name": "job_id"},
        {"column_name": "upload_id"},
        {"column_name": "ingest_timestamp"},
        {"column_name": "url"},
    ])
    fake_bq.client.queue_result(old_cols)

    from scripts.migrate_dataforseo_manifest import MIGRATIONS
    m = next(mm for mm in MIGRATIONS if mm.new_name == "backlinks-backlinks")
    sql = mod._build_copy_sql(fake_bq, m, "proj")

    assert sql is not None
    assert "COALESCE(job_id, 'pending-backfill') AS job_id" in sql
    assert "COALESCE(upload_id, 'pending-backfill') AS upload_id" in sql
    assert "COALESCE(ingest_timestamp, CURRENT_TIMESTAMP()) AS ingest_timestamp" in sql


def test_phase_backfill_dry_run_includes_synthetic_ids_and_domain_merge(fake_bq):
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--phase=backfill", "--dry-run"])
        assert result.exit_code == 0, result.output
        # Step 3a: synthetic IDs replace the phase-2 sentinel placeholder.
        assert "BACKFILL IDs" in result.output
        assert "UPDATE" in result.output
        assert "pending-backfill" in result.output
        # Step 3b: domain merge
        assert "MERGE" in result.output
        assert "Meta.domains" in result.output
        # Non-preserved-domain migrations should be skipped in step 3b.
        assert "SKIP" in result.output
    finally:
        mod._bq_override = None


def test_build_copy_sql_drops_domain_when_preserve_is_false(fake_bq):
    """For the 3 endpoints where old `domain` was not caller-context, the copy
    must force new `domain` to NULL regardless of the source column."""
    old_cols = pd.DataFrame([
        {"column_name": "job_id"},
        {"column_name": "upload_id"},
        {"column_name": "ingest_timestamp"},
        {"column_name": "domain"},      # present in source
        {"column_name": "url"},
    ])
    fake_bq.client.queue_result(old_cols)

    from scripts.migrate_dataforseo_manifest import MIGRATIONS
    # backlinks_bulk_pages_summary has preserve_domain_column=False.
    m = next(mm for mm in MIGRATIONS if mm.new_name == "backlinks-bulk_pages_summary")
    assert m.preserve_domain_column is False
    sql = mod._build_copy_sql(fake_bq, m, "proj")

    assert sql is not None
    # Despite `domain` being present in old_cols, it should NOT be carried forward.
    assert "CAST(NULL AS STRING) AS domain" in sql
    # But the rest of the metadata block is preserved.
    assert "job_id" in sql
    assert "upload_id" in sql


def test_build_copy_sql_preserves_domain_when_flag_is_true(fake_bq):
    """For caller-context-valid endpoints (ranked_keywords), preserve the old
    `domain` column verbatim."""
    old_cols = pd.DataFrame([
        {"column_name": "job_id"},
        {"column_name": "upload_id"},
        {"column_name": "ingest_timestamp"},
        {"column_name": "domain"},
        {"column_name": "keyword"},
    ])
    fake_bq.client.queue_result(old_cols)

    from scripts.migrate_dataforseo_manifest import MIGRATIONS
    m = next(mm for mm in MIGRATIONS if mm.new_name == "backlinks-backlinks")
    assert m.preserve_domain_column is True
    sql = mod._build_copy_sql(fake_bq, m, "proj")

    assert sql is not None
    # Should carry the source `domain` column directly, not default to NULL.
    # Check via exact tokens to avoid matching `domain_from`, `domain_to`, etc.
    assert "CAST(NULL AS STRING) AS domain\n" not in sql
    assert "CAST(NULL AS STRING) AS domain," not in sql
    lines = [ln.strip().rstrip(",") for ln in sql.splitlines()]
    assert "domain" in lines, sql


def test_build_copy_sql_copy_source_is_backup_dataset(fake_bq):
    old_cols = pd.DataFrame([
        {"column_name": "job_id"},
        {"column_name": "url"},
    ])
    fake_bq.client.queue_result(old_cols)

    from scripts.migrate_dataforseo_manifest import MIGRATIONS
    m = next(mm for mm in MIGRATIONS if mm.new_name == "backlinks-backlinks")
    sql = mod._build_copy_sql(fake_bq, m, "proj")

    assert sql is not None
    assert "FROM `proj.DataForSEO_backup_04_20_2026.backlinks_backlinks_live`" in sql
    assert "INTO `proj.DataForSEO.backlinks-backlinks`" in sql


def test_only_filter_limits_a_phase_to_single_migration(fake_bq):
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(
            mod.cli, ["--phase=migrate", "--dry-run", "--only", "backlinks-backlinks"],
        )
        assert result.exit_code == 0
        assert "backlinks-backlinks" in result.output
        # Other migrations should not run in the body.
        assert "--- serp-google-organic ---" not in result.output
    finally:
        mod._bq_override = None


def test_parse_endpoint_columns_returns_name_and_type_pairs():
    """The parser must extract (name, type) pairs, ignoring commas inside
    OPTIONS(description="...") clauses. Types are needed to emit the right
    NULL cast for columns missing from the source table."""
    sql = """
      url STRING OPTIONS(description="foo, bar, baz"),
      rank INT64 OPTIONS(description="sample"),
      dofollow BOOL,
      first_seen TIMESTAMP,
      cpc FLOAT64 OPTIONS(description="cost per click")
    """
    pairs = mod._parse_endpoint_columns(sql)
    assert pairs == [
        ("url", "STRING"),
        ("rank", "INT64"),
        ("dofollow", "BOOL"),
        ("first_seen", "TIMESTAMP"),
        ("cpc", "FLOAT64"),
    ]


def test_build_copy_sql_uses_typed_null_for_missing_columns(fake_bq):
    """For columns absent from the source table, the fallback NULL must use
    the target column's declared type — not STRING."""
    # Old table is missing `original` (BOOL) and `first_seen` (TIMESTAMP);
    # has url and task_id.
    old_cols = pd.DataFrame([
        {"column_name": "url"},
        {"column_name": "task_id"},
    ])
    fake_bq.client.queue_result(old_cols)

    from scripts.migrate_dataforseo_manifest import MIGRATIONS
    m = next(mm for mm in MIGRATIONS if mm.new_name == "backlinks-backlinks")
    sql = mod._build_copy_sql(fake_bq, m, "proj")

    assert sql is not None
    # `original` is BOOL — must cast NULL to BOOL, not STRING.
    assert "CAST(NULL AS BOOL) AS original" in sql
    assert "CAST(NULL AS STRING) AS original" not in sql
    # TIMESTAMP fallbacks too.
    assert "CAST(NULL AS TIMESTAMP) AS first_seen" in sql
    # INT64 and FLOAT64 fallbacks get typed correctly.
    assert "CAST(NULL AS INT64) AS rank" in sql


def test_manifest_has_three_domain_drop_endpoints():
    """Sanity: the 3 endpoints we identified as semantic-mismatch must have
    preserve_domain_column=False; the rest default to True."""
    from scripts.migrate_dataforseo_manifest import MIGRATIONS
    drop_domain = {m.new_name for m in MIGRATIONS if not m.preserve_domain_column}
    assert drop_domain == {
        "backlinks-bulk_pages_summary",
        "backlinks-summary",
        "serp-google-organic",
    }
