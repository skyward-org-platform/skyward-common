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
