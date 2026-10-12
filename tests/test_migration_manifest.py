"""Sanity test for the migration manifest."""

from scripts.migrate_dataforseo_manifest import MIGRATIONS, Migration


def test_manifest_has_12_entries():
    assert len(MIGRATIONS) == 12


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
        "ai_optimization-chat_gpt-llm_scraper",
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


# ----- ChatGPT fan-out table -----

FANOUT = "ai_optimization-chat_gpt-llm_scraper"


def _fanout():
    return next(m for m in MIGRATIONS if m.new_name == FANOUT)


def test_fanout_table_is_new_and_clustered_on_the_prompt():
    m = _fanout()
    assert m.old_name is None
    assert m.clustering_fields == ("prompt_id", "job_id")
    assert m.partition_field == "ingest_timestamp"
    others = [x for x in MIGRATIONS if x.new_name != FANOUT]
    assert all(x.clustering_fields == ("domain_id", "job_id") for x in others)


def test_fanout_schema_lists_every_endpoint_column_in_order_with_a_description():
    from skyward.data.dataforseo.fanout import SCHEMA

    lines = [l.strip() for l in _fanout().new_schema.splitlines() if l.strip()]
    names = [l.split()[0] for l in lines]
    assert names == ["job_id", "upload_id", "ingest_timestamp", "domain_id", "domain",
                     "task_id", "endpoint_mode", *SCHEMA]
    assert all("OPTIONS(description=" in l for l in lines)
    assert all(l.endswith(",") for l in lines[:-1]) and not lines[-1].endswith(",")


def test_fanout_domain_columns_are_documented_as_always_null():
    schema = _fanout().new_schema
    assert schema.count("Always NULL in this table") == 2
    assert "Foreign key to Meta.domains" not in schema
    # the shared block other tables use is untouched
    from scripts.migrate_dataforseo_manifest import METADATA_BLOCK_SQL
    assert "Foreign key to Meta.domains" in METADATA_BLOCK_SQL


def test_fanout_lists_are_json_text_not_native_arrays():
    schema = _fanout().new_schema
    assert "ARRAY<" not in schema and "STRUCT<" not in schema


def test_fanout_script_statements():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "create_dfs_fanout_table.py"
    spec = importlib.util.spec_from_file_location("create_dfs_fanout_table", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    statements = mod.all_statements("proj")
    assert [n for n, _ in statements] == [FANOUT, f"{FANOUT}_queries", f"{FANOUT}_sources"]
    table_sql = statements[0][1]
    assert table_sql.startswith(f"CREATE TABLE IF NOT EXISTS `proj.DataForSEO.{FANOUT}`")
    assert "PARTITION BY DATE(ingest_timestamp)" in table_sql
    assert "CLUSTER BY prompt_id, job_id" in table_sql
    assert all(sql.startswith("CREATE OR REPLACE VIEW") for _, sql in statements[1:])
    assert "JSON_VALUE_ARRAY(t.fan_out_queries)" in statements[1][1]
    assert "JSON_QUERY_ARRAY(t.sources)" in statements[2][1]
