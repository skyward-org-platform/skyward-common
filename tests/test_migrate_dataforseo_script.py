"""Tests for scripts/migrate_dataforseo_tables.py using a fake BQ client."""

from __future__ import annotations

import pandas as pd
import pytest
from click.testing import CliRunner

from scripts import migrate_dataforseo_tables as mod


def test_dry_run_prints_every_sql_statement_without_executing(fake_bq):
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--dry-run"])
        assert result.exit_code == 0, result.output
        assert "CREATE TABLE" in result.output
        assert "[dry-run]" in result.output
        # Every migration should emit a BACKUP (except the new table with old_name=None)
        # and a CREATE NEW
        assert "BACKUP" in result.output
        assert "CREATE NEW" in result.output
    finally:
        mod._bq_override = None


def test_build_copy_sql_introspects_old_columns(fake_bq, monkeypatch):
    """Real _build_copy_sql queries INFORMATION_SCHEMA and builds an INSERT."""
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


def test_only_filter_limits_to_single_migration(fake_bq):
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--dry-run", "--only", "backlinks-backlinks"])
        assert result.exit_code == 0
        assert "backlinks-backlinks" in result.output
        # Should NOT have other migrations listed in the "Migrations:" header
        assert "serp-google-organic" not in result.output
    finally:
        mod._bq_override = None
