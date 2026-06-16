import pytest

from tests.conftest_pg import requires_pg
from skyward.data.meta import MetaClient


@requires_pg
def test_site_competitors_table_rejects_self_competition(pg_client):
    """The CHECK (domain_id <> competitor_domain_id) must reject self-rows."""
    meta = MetaClient(pg_client)
    did = meta.add_domain("acme.com")
    with pytest.raises(Exception):
        pg_client.execute(
            "insert into meta.site_competitors (domain_id, competitor_domain_id) "
            "values (%(d)s, %(d)s)",
            {"d": did},
        )


@requires_pg
def test_site_competitors_table_accepts_directed_row(pg_client):
    meta = MetaClient(pg_client)
    a = meta.add_domain("acme.com")
    b = meta.add_domain("rival.com")
    pg_client.execute(
        "insert into meta.site_competitors (domain_id, competitor_domain_id, priority) "
        "values (%(a)s, %(b)s, 'HIGH')",
        {"a": a, "b": b},
    )
    df = pg_client.query(
        "select domain_id, competitor_domain_id, priority from meta.site_competitors"
    )
    assert df.iloc[0]["domain_id"] == a
    assert df.iloc[0]["competitor_domain_id"] == b
    assert df.iloc[0]["priority"] == "HIGH"


@requires_pg
def test_add_and_get_site_competitor(pg_client):
    meta = MetaClient(pg_client)
    site = meta.add_domain("acme.com")
    comp = meta.add_domain("rival.com")
    meta.add_site_competitor(site, comp, priority="HIGH")
    df = meta.get_site_competitors(site)
    assert list(df["competitor_domain_id"]) == [comp]
    assert list(df["domain"]) == ["rival.com"]
    assert df.iloc[0]["priority"] == "HIGH"


@requires_pg
def test_add_site_competitor_rejects_self(pg_client):
    meta = MetaClient(pg_client)
    site = meta.add_domain("acme.com")
    with pytest.raises(ValueError):
        meta.add_site_competitor(site, site)


@requires_pg
def test_add_site_competitor_invalid_priority_raises(pg_client):
    meta = MetaClient(pg_client)
    site = meta.add_domain("acme.com")
    comp = meta.add_domain("rival.com")
    with pytest.raises(ValueError):
        meta.add_site_competitor(site, comp, priority="SOMETIMES")


@requires_pg
def test_add_site_competitor_idempotent(pg_client):
    meta = MetaClient(pg_client)
    site = meta.add_domain("acme.com")
    comp = meta.add_domain("rival.com")
    meta.add_site_competitor(site, comp)
    meta.add_site_competitor(site, comp)
    assert len(meta.get_site_competitors(site)) == 1


@requires_pg
def test_get_site_competitors_empty(pg_client):
    meta = MetaClient(pg_client)
    site = meta.add_domain("acme.com")
    assert meta.get_site_competitors(site).empty


@requires_pg
def test_directionality_is_not_symmetric(pg_client):
    meta = MetaClient(pg_client)
    a = meta.add_domain("acme.com")
    b = meta.add_domain("rival.com")
    meta.add_site_competitor(a, b)
    assert list(meta.get_site_competitors(a)["competitor_domain_id"]) == [b]
    assert meta.get_site_competitors(b).empty


@requires_pg
def test_get_sites_competing_with(pg_client):
    meta = MetaClient(pg_client)
    a = meta.add_domain("acme.com")
    b = meta.add_domain("buster.com")
    rival = meta.add_domain("rival.com")
    meta.add_site_competitor(a, rival)
    meta.add_site_competitor(b, rival)
    df = meta.get_sites_competing_with(rival)
    assert set(df["domain_id"]) == {a, b}


@requires_pg
def test_add_site_competitors_bulk_skips_self(pg_client):
    meta = MetaClient(pg_client)
    site = meta.add_domain("acme.com")
    c1 = meta.add_domain("r1.com")
    c2 = meta.add_domain("r2.com")
    n = meta.add_site_competitors(site, [c1, c2, site], priority="LOW")
    assert n == 2
    df = meta.get_site_competitors(site)
    assert set(df["competitor_domain_id"]) == {c1, c2}
    assert set(df["priority"]) == {"LOW"}


@requires_pg
def test_add_site_competitors_empty_returns_zero(pg_client):
    meta = MetaClient(pg_client)
    site = meta.add_domain("acme.com")
    assert meta.add_site_competitors(site, []) == 0


@requires_pg
def test_remove_site_competitor(pg_client):
    meta = MetaClient(pg_client)
    site = meta.add_domain("acme.com")
    comp = meta.add_domain("rival.com")
    meta.add_site_competitor(site, comp)
    meta.remove_site_competitor(site, comp)
    assert meta.get_site_competitors(site).empty


@requires_pg
def test_set_site_competitor_priority(pg_client):
    meta = MetaClient(pg_client)
    site = meta.add_domain("acme.com")
    comp = meta.add_domain("rival.com")
    meta.add_site_competitor(site, comp, priority="NORMAL")
    meta.set_site_competitor_priority(site, comp, "VERY HIGH")
    assert meta.get_site_competitors(site).iloc[0]["priority"] == "VERY HIGH"


@requires_pg
def test_add_site_competitor_by_domain_creates_domains(pg_client):
    meta = MetaClient(pg_client)
    site_id, comp_id = meta.add_site_competitor_by_domain(
        "https://www.acme.com/", "rival.com", priority="HIGH"
    )
    assert meta.get_domain("acme.com")["domain_id"] == site_id
    assert meta.get_domain("rival.com")["domain_id"] == comp_id
    df = meta.get_site_competitors(site_id)
    assert list(df["competitor_domain_id"]) == [comp_id]
    assert df.iloc[0]["priority"] == "HIGH"


@requires_pg
def test_set_site_competitors_syncs_set(pg_client):
    meta = MetaClient(pg_client)
    site = meta.add_domain("acme.com")
    c1 = meta.add_domain("r1.com")
    c2 = meta.add_domain("r2.com")
    c3 = meta.add_domain("r3.com")
    meta.add_site_competitors(site, [c1, c2])
    meta.set_site_competitors(site, [c2, c3], priority="HIGH")
    df = meta.get_site_competitors(site)
    assert set(df["competitor_domain_id"]) == {c2, c3}


@requires_pg
def test_set_site_competitors_empty_clears(pg_client):
    meta = MetaClient(pg_client)
    site = meta.add_domain("acme.com")
    c1 = meta.add_domain("r1.com")
    meta.add_site_competitor(site, c1)
    meta.set_site_competitors(site, [])
    assert meta.get_site_competitors(site).empty


@requires_pg
def test_copy_site_competitors(pg_client):
    meta = MetaClient(pg_client)
    src = meta.add_domain("busbank.com")
    dst = meta.add_domain("corporateshuttle.com")
    c1 = meta.add_domain("r1.com")
    meta.add_site_competitor(src, c1, priority="HIGH")
    meta.add_site_competitor(src, dst)
    n = meta.copy_site_competitors(src, dst)
    df = meta.get_site_competitors(dst)
    assert set(df["competitor_domain_id"]) == {c1}
    assert df.iloc[0]["priority"] == "HIGH"
    assert n == 1


@requires_pg
def test_get_client_competitors_aggregates_across_sites(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    s1 = meta.add_domain("acme.com", client_id=cid)
    s2 = meta.add_domain("acme-shop.com", client_id=cid)
    rival = meta.add_domain("rival.com")
    other = meta.add_domain("other.com")
    meta.add_site_competitor(s1, rival)
    meta.add_site_competitor(s2, other)
    meta.add_site_competitor(s2, rival)
    df = meta.get_client_competitors(cid)
    assert set(df["competitor_domain_id"]) == {rival, other}


@requires_pg
def test_get_client_competitors_empty(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    meta.add_domain("acme.com", client_id=cid)
    assert meta.get_client_competitors(cid).empty


@requires_pg
def test_set_client_domain_competitor_flips_flag(pg_client):
    meta = MetaClient(pg_client)
    cid = meta.add_client("Acme")
    did = meta.add_domain("acme.com", client_id=cid, is_competitor=False)
    meta.set_client_domain_competitor(cid, did, True)
    assert meta.get_client_domains(cid, is_competitor=True).iloc[0]["domain_id"] == did
    meta.set_client_domain_competitor(cid, did, False)
    assert meta.get_client_domains(cid, is_competitor=True).empty
