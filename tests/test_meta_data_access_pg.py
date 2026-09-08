"""meta.site and meta.data_access against a real Postgres.

Skips when TEST_DATABASE_URL is unset. The unit tests in
test_meta_data_access.py cover the same methods with a fake and always
run.
"""
from tests.conftest_pg import requires_pg
from skyward.data.meta import MetaClient


@requires_pg
def test_a_domain_we_do_not_work_on_has_no_site(pg_client):
    assert MetaClient(pg_client).get_site(999999) is None


@requires_pg
def test_data_access_returns_a_frame_not_a_row(pg_client):
    """A site can hold more than one row per tool: buscharter has two
    Google Ads accounts, which is why the key includes the account."""
    df = MetaClient(pg_client).get_data_access(domain_id=999999)

    assert df.empty
    assert "account_identifier" in df.columns


@requires_pg
def test_list_sites_narrows_by_engagement(pg_client):
    meta = MetaClient(pg_client)
    everything = meta.list_sites()
    live = meta.list_sites(engagement_status="client")

    assert len(live) <= len(everything)
