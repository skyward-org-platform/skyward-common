"""Tests for scripts/migrate_dataforseo_hotfix_renames.py — smoke tests on
the generated SQL."""

from __future__ import annotations

from click.testing import CliRunner

from scripts import migrate_dataforseo_hotfix_renames as mod


def test_search_volume_sql_maps_local_columns():
    """The search_volume hot-fix must SELECT local_search_volume AS search_volume,
    and local_location_code AS location_code."""
    sql = mod._sql_search_volume("proj")
    assert "local_search_volume AS search_volume" in sql
    assert "local_location_code AS location_code" in sql
    # Writes to new target, reads from backup source.
    assert "INTO `proj.DataForSEO.keywords_data-google_ads-search_volume`" in sql
    assert "FROM `proj.DataForSEO_backup_04_20_2026.keyword_data-google_ads-search_volume`" in sql
    # Metadata NOT NULL cols use sentinel COALESCE.
    assert "COALESCE(job_id, 'pending-backfill')" in sql
    assert "COALESCE(upload_id, 'pending-backfill')" in sql


def test_search_intent_sql_converts_secondary_intents_to_json():
    """The search_intent hot-fix must SPLIT old comma-joined secondary_intents
    into a JSON array of {label} dicts."""
    sql = mod._sql_search_intent("proj")
    assert "secondary_intents" in sql
    assert "secondary_keyword_intents" in sql
    assert "SPLIT(secondary_intents" in sql
    assert "TO_JSON_STRING(" in sql
    # Guard against NULL / empty strings.
    assert "WHEN secondary_intents IS NULL OR TRIM(secondary_intents) = '' THEN NULL" in sql
    # Writes to new target, reads from backup source.
    assert "INTO `proj.DataForSEO.dataforseo_labs-google-search_intent`" in sql
    assert "FROM `proj.DataForSEO_backup_04_20_2026.dataforseo_labs_google_search_intent`" in sql


def test_cli_dry_run_exits_zero_and_prints_truncates(fake_bq):
    """Smoke test: --dry-run should print TRUNCATE + INSERT without hitting BQ."""
    mod._bq_override = fake_bq
    try:
        runner = CliRunner()
        result = runner.invoke(mod.cli, ["--dry-run"])
        assert result.exit_code == 0, result.output
        assert "TRUNCATE TABLE" in result.output
        assert "keywords_data-google_ads-search_volume" in result.output
        assert "dataforseo_labs-google-search_intent" in result.output
        # Hot-fix outputs should carry the mapping SQL.
        assert "local_search_volume AS search_volume" in result.output
        assert "SPLIT(secondary_intents" in result.output
    finally:
        mod._bq_override = None
