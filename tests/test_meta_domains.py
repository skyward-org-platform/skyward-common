import pytest

from tests.conftest_pg import requires_pg
from skyward.data.meta import MetaClient


@requires_pg
def test_add_domains_dedup_and_link(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    res = meta.add_domains(["https://www.acme.com/", "acme.com"], client_id=cid)
    # both normalize to acme.com -> one domain row
    assert len({r["domain_id"] for r in res}) == 1
    links = meta.get_client_domains(cid)
    assert list(links["domain"]) == ["acme.com"]


@requires_pg
def test_add_domains_existing_domain_reused(pg_client):
    meta = MetaClient(pg_client)
    d1 = meta.add_domain("acme.com")
    d2 = meta.add_domain("acme.com")
    assert d1 == d2


@requires_pg
def test_get_domain_and_search(pg_client):
    meta = MetaClient(pg_client)
    meta.add_domain("acme.com")
    assert meta.get_domain("https://acme.com")["domain"] == "acme.com"
    assert not meta.search_domains("acme").empty


@requires_pg
def test_remove_client_domain(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    did = meta.add_domain("acme.com", client_id=cid)
    meta.remove_client_domain(cid, did)
    assert meta.get_client_domains(cid).empty


@requires_pg
def test_add_domains_invalid_priority_raises(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("A")
    with pytest.raises(ValueError):
        meta.add_domains(["acme.com"], client_id=cid, priority="URGENT")


@requires_pg
def test_update_domain_and_priority_batch(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    did = meta.add_domain("acme.com", client_id=cid)
    meta.update_domain(did, domain_name="Acme Co", is_active=False, notes="x")
    d = meta.get_domain("acme.com")
    assert d["domain_name"] == "Acme Co" and d["is_active"] is False
    meta.update_client_domains_priority_batch(cid, [{"domain_id": did, "priority": "high"}])
    assert meta.get_client_domains(cid).iloc[0]["priority"] == "HIGH"
