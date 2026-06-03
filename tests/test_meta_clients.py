from tests.conftest_pg import requires_pg
from skyward.data.meta import MetaClient


@requires_pg
def test_add_and_get_client(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme", abbreviation="ACM", notes="hi")
    assert isinstance(cid, int) and cid > 0
    row = meta.get_client(cid)
    assert row["client_name"] == "Acme" and row["abbreviation"] == "ACM"
    assert row["is_active"] is True


@requires_pg
def test_get_client_missing_returns_none(pg_client):
    assert MetaClient(pg_client).get_client(99999) is None


@requires_pg
def test_list_clients_search_and_counts(pg_client):
    meta = MetaClient(pg_client)
    meta.add_client("Acme", abbreviation="ACM")
    meta.add_client("Beta", abbreviation="BTA")
    df = meta.list_clients(search="acm")
    assert list(df["client_name"]) == ["Acme"]
    df2 = meta.list_clients(include_counts=True)
    assert {"domain_count", "competitor_count", "project_count"} <= set(df2.columns)


@requires_pg
def test_update_and_deactivate_client(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    meta.update_client(cid, client_name="Acme2")
    assert meta.get_client(cid)["client_name"] == "Acme2"
    meta.deactivate_client(cid)
    assert meta.get_client(cid)["is_active"] is False
