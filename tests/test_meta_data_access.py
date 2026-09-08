"""Reads for meta.site and meta.data_access.

ADDITIVE. The existing client_datasets and client_domains methods are
untouched and still work, so tagging this release changes no consumer's
behaviour. Consumers move one at a time; the old methods are deleted
last.

These use a fake so they run without Postgres. The integration tests in
test_meta_data_access_pg.py exercise the same methods against a real
database and skip when TEST_DATABASE_URL is unset.
"""
import pandas as pd

from skyward.data.meta import MetaClient


class FakeSb:
    """Records SQL and params, returns whatever was queued."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def query(self, sql, params=None):
        self.calls.append({"sql": " ".join(sql.split()), "params": params or {}})
        return self.results.pop(0) if self.results else pd.DataFrame()

    def execute(self, sql, params=None):
        self.calls.append({"sql": " ".join(sql.split()), "params": params or {}})
        return []


def test_data_access_is_scoped_by_domain_not_client():
    """The whole point of the redesign: client_datasets.domain_id was
    nullable, so a row that should have been domain-scoped silently
    applied to a sibling territory. The new read takes a domain."""
    sb = FakeSb(pd.DataFrame())
    MetaClient(sb).get_data_access(domain_id=22)

    sql = sb.calls[0]["sql"]
    assert "meta.data_access" in sql
    assert "da.domain_id = %(domain_id)s" in sql
    assert sb.calls[0]["params"]["domain_id"] == 22


def test_data_access_can_be_filtered_to_one_tool():
    sb = FakeSb(pd.DataFrame())
    MetaClient(sb).get_data_access(domain_id=22, tool="ga4")

    assert sb.calls[0]["params"]["tool"] == "ga4"


def test_data_access_returns_active_rows_only_by_default():
    sb = FakeSb(pd.DataFrame())
    MetaClient(sb).get_data_access(domain_id=22)

    assert "is_active" in sb.calls[0]["sql"]


def test_get_site_reads_the_new_table():
    sb = FakeSb(pd.DataFrame([{"domain_id": 22, "engagement_status": "client"}]))
    row = MetaClient(sb).get_site(22)

    assert "meta.site" in sb.calls[0]["sql"]
    assert row["engagement_status"] == "client"


def test_get_site_returns_none_when_the_site_is_not_one_we_work_on():
    """meta.domains holds 820 domains; meta.site holds the 50 we work
    on. A domain being known is not the same as being a site."""
    sb = FakeSb(pd.DataFrame())

    assert MetaClient(sb).get_site(999) is None


def test_list_sites_can_filter_by_engagement_status():
    """Anything about to spend money on a run should be able to ask
    whether the engagement is live."""
    sb = FakeSb(pd.DataFrame())
    MetaClient(sb).list_sites(engagement_status="client")

    assert sb.calls[0]["params"]["engagement_status"] == "client"


def test_the_old_methods_are_still_present():
    """Tagging this release must not change any consumer's behaviour."""
    meta = MetaClient(FakeSb())

    assert hasattr(meta, "get_client_datasets")
    assert hasattr(meta, "get_client_domains")
