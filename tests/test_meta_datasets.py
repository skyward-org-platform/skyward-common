from tests.conftest_pg import requires_pg
from skyward.data.meta import MetaClient


@requires_pg
def test_add_client_dataset_creates_catalog_and_link(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    res = meta.add_client_dataset(cid, "analytics_123", "ga4", hostname="acme.com")
    assert res["status"] == "added"
    cat = meta.get_dataset_catalog(dataset_type="ga4")
    assert "analytics_123" in list(cat["dataset"])
    cds = meta.get_client_datasets(client_id=cid)
    assert cds.iloc[0]["dataset_id"] == "analytics_123"
    assert cds.iloc[0]["hostname"] == "acme.com"


@requires_pg
def test_check_dataset_assignment(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    meta.add_client_dataset(cid, "analytics_123", "ga4")
    a = meta.check_dataset_assignment("analytics_123")
    assert a["client_id"] == cid
    assert meta.check_dataset_assignment("nope") is None


@requires_pg
def test_update_and_delete_client_dataset(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    meta.add_client_dataset(cid, "analytics_123", "ga4")
    meta.update_client_dataset(cid, "analytics_123", is_active=False, notes="x")
    assert meta.get_client_datasets(client_id=cid, active_only=False).iloc[0]["is_active"] is False
    meta.delete_client_dataset(cid, "analytics_123")
    assert meta.get_client_datasets(client_id=cid, active_only=False).empty
