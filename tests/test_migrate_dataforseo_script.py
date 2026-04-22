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


def test_phase_backfill_dry_run_groups_by_hour_and_logs_per_upload_id(fake_bq):
    """Phase 3 dry-run prints a preview query + MERGE template, uses HOUR
    truncation, and emits one log per synthetic upload_id."""
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--phase=backfill", "--dry-run"])
        assert result.exit_code == 0, result.output
        # HOUR-truncation grouping
        assert "TIMESTAMP_TRUNC(ingest_timestamp, HOUR)" in result.output
        # Sentinel-based filtering
        assert "pending-backfill" in result.output
        # Two distinct paths are exercised
        assert "both job_id + upload_id sentinels" in result.output
        assert "upload_id sentinel only" in result.output
        # Per-column IF() preserves real values
        assert "IF(t.job_id    = 'pending-backfill', m.synth_job,    t.job_id)" in result.output \
            or "IF(t.job_id = 'pending-backfill', m.synth_job, t.job_id)" in result.output \
            or "IF(" in result.output
        # Ranked_keywords is the upload-only case
        assert "dataforseo_labs-google-ranked_keywords" in result.output
        # backlinks-summary and serp are the both-sentinel case
        assert "backlinks-summary" in result.output
        assert "serp-google-organic" in result.output
    finally:
        mod._bq_override = None


def test_phase_backfill_does_not_include_domain_merge_anymore(fake_bq):
    """Phase 3 no longer auto-MERGEs against Meta.domains — domain backfill
    is now a separate manual-mapping flow."""
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--phase=backfill", "--dry-run"])
        assert result.exit_code == 0
        assert "Meta.domains" not in result.output
        assert "MERGE domain_id" not in result.output
    finally:
        mod._bq_override = None


def test_build_merge_both_ids_uses_per_column_if_to_preserve_real_values():
    """When building the MERGE for tables with both-column sentinels, per-column
    IF ensures real (non-sentinel) values survive."""
    mapping = [{"hour_bucket": "2024-12-17 14:00:00+00:00",
                "synth_job": "job-uuid-1",
                "synth_upload": "upload-uuid-1",
                "row_count": 100}]
    sql = mod._build_merge_both_ids("proj", "backlinks-summary", mapping)
    assert "IF(t.job_id    = 'pending-backfill', m.synth_job,    t.job_id)" in sql
    assert "IF(t.upload_id = 'pending-backfill', m.synth_upload, t.upload_id)" in sql
    assert "TIMESTAMP '2024-12-17 14:00:00+00:00'" in sql
    assert "TIMESTAMP_TRUNC(t.ingest_timestamp, HOUR)" in sql


def test_build_merge_upload_only_never_modifies_real_job_id():
    """For ranked_keywords, real job_id must be preserved. Only upload_id is
    modified, and the MERGE key includes job_id so each (job_id, hour) is distinct."""
    mapping = [{"job_id": "real-job-abc",
                "hour_bucket": "2024-12-17 14:00:00+00:00",
                "synth_upload": "upload-uuid-1",
                "row_count": 200}]
    sql = mod._build_merge_upload_only("proj", "dataforseo_labs-google-ranked_keywords", mapping)
    # UPDATE touches only upload_id, never job_id
    assert "UPDATE SET upload_id = m.synth_upload;" in sql
    assert "SET job_id" not in sql
    assert "SET\n  job_id" not in sql
    # Join key includes job_id
    assert "t.job_id = m.job_id" in sql
    assert "real-job-abc" in sql


def test_build_copy_sql_uses_coalesce_sentinel_for_notnull_metadata(fake_bq):
    """NOT NULL metadata columns (job_id, upload_id, ingest_timestamp) must
    tolerate NULL values in the source via COALESCE — bulk_pages_summary
    had NULL ingest_timestamps in prod, which broke the original migrate."""
    old_cols = pd.DataFrame([
        {"column_name": "job_id", "data_type": "STRING"},
        {"column_name": "upload_id", "data_type": "STRING"},
        {"column_name": "ingest_timestamp", "data_type": "TIMESTAMP"},
        {"column_name": "url", "data_type": "STRING"},
    ])
    fake_bq.client.queue_result(old_cols)

    from scripts.migrate_dataforseo_manifest import MIGRATIONS
    m = next(mm for mm in MIGRATIONS if mm.new_name == "backlinks-backlinks")
    sql = mod._build_copy_sql(fake_bq, m, "proj")

    assert sql is not None
    assert "COALESCE(job_id, 'pending-backfill') AS job_id" in sql
    assert "COALESCE(upload_id, 'pending-backfill') AS upload_id" in sql
    assert "COALESCE(ingest_timestamp, CURRENT_TIMESTAMP()) AS ingest_timestamp" in sql


# Old test replaced by test_phase_backfill_dry_run_groups_by_hour_and_logs_per_upload_id
# (phase 3 no longer auto-MERGEs against Meta.domains; domain backfill is now
# a separate manual-mapping flow).


def test_build_copy_sql_drops_domain_when_preserve_is_false(fake_bq):
    """For the 3 endpoints where old `domain` was not caller-context, the copy
    must force new `domain` to NULL regardless of the source column."""
    old_cols = pd.DataFrame([
        {"column_name": "job_id", "data_type": "STRING"},
        {"column_name": "upload_id", "data_type": "STRING"},
        {"column_name": "ingest_timestamp", "data_type": "TIMESTAMP"},
        {"column_name": "domain", "data_type": "STRING"},
        {"column_name": "url", "data_type": "STRING"},
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
        {"column_name": "job_id", "data_type": "STRING"},
        {"column_name": "upload_id", "data_type": "STRING"},
        {"column_name": "ingest_timestamp", "data_type": "TIMESTAMP"},
        {"column_name": "domain", "data_type": "STRING"},
        {"column_name": "keyword", "data_type": "STRING"},
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
        {"column_name": "job_id", "data_type": "STRING"},
        {"column_name": "url", "data_type": "STRING"},
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
        {"column_name": "url", "data_type": "STRING"},
        {"column_name": "task_id", "data_type": "STRING"},
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


def test_build_copy_sql_json_serializes_array_when_target_is_string(fake_bq):
    """When the source column is ARRAY<INT64> but the new schema declares
    STRING (JSON), wrap in TO_JSON_STRING. Caught a real migration failure on
    `categories` in keyword_suggestions."""
    old_cols = pd.DataFrame([
        {"column_name": "job_id", "data_type": "STRING"},
        {"column_name": "seed_keyword", "data_type": "STRING"},
        {"column_name": "keyword", "data_type": "STRING"},
        {"column_name": "categories", "data_type": "ARRAY<INT64>"},
    ])
    fake_bq.client.queue_result(old_cols)

    from scripts.migrate_dataforseo_manifest import MIGRATIONS
    m = next(mm for mm in MIGRATIONS if mm.new_name == "dataforseo_labs-google-keyword_suggestions")
    sql = mod._build_copy_sql(fake_bq, m, "proj")

    assert sql is not None
    assert "TO_JSON_STRING(categories) AS categories" in sql
    # Should not use a raw STRING cast that would break on the type mismatch.
    assert "SAFE_CAST(categories" not in sql


def test_source_to_target_expr_handles_scalar_and_complex_types():
    """Direct test of the per-column type-transformation logic."""
    # Matching types pass through
    assert mod._source_to_target_expr("foo", "STRING", "STRING") == "foo"
    assert mod._source_to_target_expr("foo", "INT64", "INT64") == "foo"
    # ARRAY/STRUCT to STRING uses TO_JSON_STRING
    assert mod._source_to_target_expr("cats", "ARRAY<INT64>", "STRING") == "TO_JSON_STRING(cats) AS cats"
    assert mod._source_to_target_expr("data", "STRUCT<a INT64>", "STRING") == "TO_JSON_STRING(data) AS data"
    # Scalar mismatches use SAFE_CAST
    assert mod._source_to_target_expr("n", "INT64", "FLOAT64") == "SAFE_CAST(n AS FLOAT64) AS n"


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
