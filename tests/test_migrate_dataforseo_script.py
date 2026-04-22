"""Tests for scripts/migrate_dataforseo_tables.py using a fake BQ client."""

from __future__ import annotations

import pandas as pd
import pytest
from click.testing import CliRunner

from scripts import migrate_dataforseo_tables as mod


def test_phase_backup_dry_run_prints_snapshot_sql(fake_bq):
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--phase=backup", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "CREATE SCHEMA" in result.output
        assert "DataForSEO_backup_04_20_2026" in result.output
        assert "SNAPSHOT" in result.output
        # 11 endpoint tables + 1 extra legacy table should show up in output.
        assert "backlinks_backlinks_live" in result.output
        assert "serp_google_organic_live_advanced_backup" in result.output
        assert "[dry-run]" in result.output
    finally:
        mod._bq_override = None


def test_phase_migrate_dry_run_prints_create_and_copy(fake_bq):
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--phase=migrate", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "CREATE TABLE" in result.output
        assert "CREATE NEW" in result.output
        assert "COPY FROM BACKUP" in result.output
        assert "PARTITION BY DATE(ingest_timestamp)" in result.output
        assert "CLUSTER BY domain_id, job_id" in result.output
        # Copy source should reference backup dataset
        assert "DataForSEO_backup_04_20_2026" in result.output
    finally:
        mod._bq_override = None


def test_phase_drop_old_dry_run_references_backup_for_verification(fake_bq):
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--phase=drop-old", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "DROP-OLD" in result.output
        assert "DataForSEO_backup_04_20_2026" in result.output
        # Also handles the extra legacy table
        assert "serp_google_organic_live_advanced_backup" in result.output
    finally:
        mod._bq_override = None


def test_phase_backfill_ids_dry_run_produces_update_statements(fake_bq):
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--phase=backfill-ids", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "UPDATE" in result.output
        assert "job_id" in result.output and "upload_id" in result.output
        assert "WHERE job_id IS NULL OR upload_id IS NULL" in result.output
    finally:
        mod._bq_override = None


def test_phase_backfill_domains_dry_run_produces_merge_from_meta(fake_bq):
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--phase=backfill-domains", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "MERGE" in result.output
        assert "Meta.domains" in result.output
        assert "WHEN MATCHED AND t.domain_id IS NULL THEN" in result.output
    finally:
        mod._bq_override = None


def test_build_copy_sql_introspects_old_columns(fake_bq):
    """Real _build_copy_sql queries INFORMATION_SCHEMA (on the backup dataset)
    and builds an INSERT."""
    old_cols = pd.DataFrame([
        {"column_name": "job_id"},
        {"column_name": "upload_id"},
        {"column_name": "ingest_timestamp"},
        {"column_name": "domain"},
        {"column_name": "project_id"},  # should be dropped
        {"column_name": "url"},
        {"column_name": "rank"},
    ])
    fake_bq.client.queue_result(old_cols)

    from scripts.migrate_dataforseo_manifest import MIGRATIONS
    m = next(mm for mm in MIGRATIONS if mm.new_name == "backlinks-backlinks")
    sql = mod._build_copy_sql(fake_bq, m, "proj")

    assert sql is not None
    assert "job_id" in sql
    assert "upload_id" in sql
    assert "domain" in sql
    assert "url" in sql
    assert "rank" in sql
    # Dropped column
    assert "project_id" not in sql
    # Historical defaults
    assert "CAST(NULL AS INT64) AS domain_id" in sql
    assert "CAST(NULL AS STRING) AS task_id" in sql
    assert "'live' AS endpoint_mode" in sql
    # Copy reads from the backup dataset, writes to the source dataset
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
        # Other migrations should not be present in the phase body
        assert "--- serp-google-organic ---" not in result.output
    finally:
        mod._bq_override = None


def test_parse_endpoint_columns_handles_options_description_clauses():
    """The new *_COLS constants include OPTIONS(description=...) — the parser
    must still extract clean column names, ignoring commas inside parentheses."""
    sql = """
      url STRING OPTIONS(description="foo, bar, baz"),
      rank INT64 OPTIONS(description="sample"),
      dofollow BOOL
    """
    names = mod._parse_endpoint_columns(sql)
    assert names == ["url", "rank", "dofollow"]
