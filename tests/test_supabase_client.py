from tests.conftest_pg import requires_pg


@requires_pg
def test_execute_and_query_roundtrip(pg_client):
    pg_client.execute(
        "insert into meta.clients (client_name, abbreviation) values (%(n)s, %(a)s)",
        {"n": "Acme", "a": "ACM"},
    )
    df = pg_client.query("select client_name, abbreviation from meta.clients", {})
    assert df.iloc[0]["client_name"] == "Acme"
    assert df.iloc[0]["abbreviation"] == "ACM"


@requires_pg
def test_query_returns_empty_dataframe(pg_client):
    df = pg_client.query("select * from meta.clients where client_id = %(id)s", {"id": -1})
    assert df.empty
    assert "client_id" in df.columns
