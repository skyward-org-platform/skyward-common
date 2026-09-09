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


# ── list_data_access: the client-scoped read get_data_access cannot do ──────
#
# get_data_access is domain-scoped by design. Every consumer migrating off
# get_client_datasets asks a client-scoped question instead, so the join
# through meta.site is the part that has to be right.

def test_list_data_access_reaches_client_scope_through_site():
    """data_access has no client_id, so a client scope only exists via meta.site."""
    sb = FakeSb(pd.DataFrame())
    MetaClient(sb).list_data_access(client_id=7)

    sql = sb.calls[0]["sql"]
    assert "meta.data_access" in sql
    assert "JOIN meta.site s ON da.domain_id = s.domain_id" in sql
    assert "s.client_id = %(client_id)s" in sql
    assert sb.calls[0]["params"]["client_id"] == 7
    assert "client_datasets" not in sql


def test_list_data_access_without_client_returns_every_site():
    sb = FakeSb(pd.DataFrame())
    MetaClient(sb).list_data_access()

    sql = sb.calls[0]["sql"]
    assert "client_id = %(client_id)s" not in sql
    assert "client_id" not in sb.calls[0]["params"]


def test_list_data_access_can_be_filtered_to_one_tool():
    sb = FakeSb(pd.DataFrame())
    MetaClient(sb).list_data_access(client_id=7, tool="google_ads")

    assert sb.calls[0]["params"]["tool"] == "google_ads"
    assert "da.tool = %(tool)s" in sb.calls[0]["sql"]


def test_list_data_access_hides_inactive_rows_by_default():
    sb = FakeSb(pd.DataFrame())
    MetaClient(sb).list_data_access(client_id=7)
    assert "da.is_active = TRUE" in sb.calls[0]["sql"]

    sb2 = FakeSb(pd.DataFrame())
    MetaClient(sb2).list_data_access(client_id=7, active_only=False)
    assert "da.is_active = TRUE" not in sb2.calls[0]["sql"]


# ── library internals that still read the old tables ───────────────────────
#
# deactivate_client(cascade=True) and list_clients(include_counts=True) are
# not "consumers" any sweep would list -- they are MetaClient's own SQL, and
# they break at the drops unless they move too.

def test_deactivate_client_cascade_uses_site_not_client_domains():
    sb = FakeSb(pd.DataFrame([{"domain_id": 5}, {"domain_id": 6}]))
    MetaClient(sb).deactivate_client(1, cascade=True)

    all_sql = " ".join(c["sql"] for c in sb.calls)
    assert "meta.client_domains" not in all_sql
    assert "meta.client_datasets" not in all_sql
    assert "from meta.site where client_id = %(client_id)s" in sb.calls[0]["sql"]


def test_deactivate_client_cascade_deactivates_tool_access_by_domain():
    """data_access carries no client_id, so the cascade goes by the site list."""
    sb = FakeSb(pd.DataFrame([{"domain_id": 5}, {"domain_id": 6}]))
    MetaClient(sb).deactivate_client(1, cascade=True)

    access = [c for c in sb.calls if "meta.data_access" in c["sql"]]
    assert len(access) == 1
    assert access[0]["params"]["domain_ids"] == [5, 6]
    assert "set is_active = FALSE" in access[0]["sql"]


def test_deactivate_client_cascade_with_no_sites_touches_no_access():
    sb = FakeSb(pd.DataFrame())
    MetaClient(sb).deactivate_client(1, cascade=True)

    assert not [c for c in sb.calls if "meta.data_access" in c["sql"]]


def test_list_clients_counts_sites_and_site_competitors():
    """competitor_count moved from a client-level flag to per-site rows, so a
    competitor shared by two of the client's sites must count once."""
    sb = FakeSb(pd.DataFrame())
    MetaClient(sb).list_clients(include_counts=True)

    sql = sb.calls[0]["sql"]
    assert "meta.client_domains" not in sql
    assert "is_competitor" not in sql
    assert "FROM meta.site s" in sql
    assert "COUNT(DISTINCT sc.competitor_domain_id)" in sql
    assert "meta.site_competitors" in sql
