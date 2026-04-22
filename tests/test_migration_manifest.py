"""Sanity test for the migration manifest."""

from scripts.migrate_dataforseo_manifest import MIGRATIONS, Migration


def test_manifest_has_11_entries():
    assert len(MIGRATIONS) == 11


def test_new_names_match_canonical_format():
    expected = {
        "backlinks-backlinks",
        "backlinks-bulk_pages_summary",
        "backlinks-summary",
        "serp-google-organic",
        "dataforseo_labs-google-keyword_suggestions",
        "dataforseo_labs-google-related_keywords",
        "dataforseo_labs-google-ranked_keywords",
        "dataforseo_labs-google-keyword_overview",
        "dataforseo_labs-google-search_intent",
        "dataforseo_labs-google-domain_rank_overview",
        "keywords_data-google_ads-search_volume",
    }
    actual = {m.new_name for m in MIGRATIONS}
    assert expected == actual


def test_domain_rank_overview_is_new_table():
    m = next(m for m in MIGRATIONS if m.new_name == "dataforseo_labs-google-domain_rank_overview")
    assert m.old_name is None


def test_ranked_keywords_is_in_place_update():
    m = next(m for m in MIGRATIONS if m.new_name == "dataforseo_labs-google-ranked_keywords")
    assert m.old_name == "dataforseo_labs-google-ranked_keywords"


def test_three_tables_drop_project_id():
    drops = {m.new_name for m in MIGRATIONS if m.drop_project_id}
    assert drops == {
        "backlinks-bulk_pages_summary",
        "backlinks-summary",
        "serp-google-organic",
    }


def test_every_schema_starts_with_metadata_block():
    # Every new_schema must begin with the job_id/upload_id/ingest_timestamp block
    for m in MIGRATIONS:
        assert "job_id STRING NOT NULL" in m.new_schema
        assert "upload_id STRING NOT NULL" in m.new_schema
        assert "ingest_timestamp TIMESTAMP NOT NULL" in m.new_schema
        assert "domain_id INT64" in m.new_schema
        assert "task_id STRING" in m.new_schema
        assert "endpoint_mode STRING NOT NULL" in m.new_schema
