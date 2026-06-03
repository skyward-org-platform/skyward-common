import pandas as pd

from tests.conftest_pg import requires_pg


@requires_pg
def test_list_tables_reads_supabase(hub, pg_client):
    pg_client.execute(
        "insert into meta.table_catalog (dataset, table_name, row_count, is_active, last_indexed_at) "
        "values ('DataForSEO', 't1', 5, true, now())"
    )
    df = hub.list_tables(dataset="DataForSEO")
    assert list(df["table_name"]) == ["t1"]


@requires_pg
def test_reindex_catalog_upserts_from_bq_scan(hub, fake_bq):
    # The single BQ INFORMATION_SCHEMA scan returns one table.
    fake_bq.client.queue_result(
        pd.DataFrame([{"table_name": "t1", "row_count": 10, "size_bytes": 100}])
    )
    result = hub.reindex_catalog("DataForSEO")
    assert "t1" in result["new_tables"]
    df = hub.list_tables(dataset="DataForSEO")
    assert df.iloc[0]["row_count"] == 10
    assert df.iloc[0]["size_bytes"] == 100


@requires_pg
def test_get_client_data_domain_lookup_resolves_from_supabase(hub, fake_bq):
    cid = hub.add_client("Acme")
    hub.add_domain("acme.com", client_id=cid)
    fake_bq.client.queue_result(pd.DataFrame([{"domain": "acme.com", "kw": "x"}]))
    hub.get_client_data(
        str(cid), "dataforseo_labs-google-ranked_keywords", use_domain_lookup=True
    )
    last = fake_bq.client.queries[-1]
    assert "UNNEST(@domains)" in last["sql"]
    domains_param = [p for p in last["job_config"].query_parameters if p.name == "domains"][0]
    assert list(domains_param.values) == ["acme.com"]


@requires_pg
def test_search_uploads_still_queries_bq(hub, fake_bq):
    fake_bq.client.queue_result(pd.DataFrame([{"job_id": "j1", "client_id": 1}]))
    df = hub.search_uploads(job_id="j1")
    assert df.iloc[0]["job_id"] == "j1"
    assert "Logs.upload_events" in fake_bq.client.queries[-1]["sql"]
