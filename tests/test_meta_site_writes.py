"""Writes for meta.site and meta.data_access.

Parity is the goal: every operation the client_domains and
client_datasets methods offer must exist against the new tables, or
those tables cannot replace them. The read-only version of this work
would have left any mutating consumer with nowhere to go.
"""
import pandas as pd
import pytest

from skyward.data.meta import MetaClient


class FakeSb:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def query(self, sql, params=None):
        self.calls.append({"sql": " ".join(sql.split()), "params": params or {}})
        return self.results.pop(0) if self.results else pd.DataFrame()

    def execute(self, sql, params=None):
        self.calls.append({"sql": " ".join(sql.split()), "params": params or {}})
        return []

    @property
    def sql(self):
        return " | ".join(c["sql"] for c in self.calls)


# ── meta.site ────────────────────────────────────────────────────────

def test_upsert_site_is_idempotent_on_the_domain():
    """Re-running onboarding for a site must update it, not fail or
    duplicate. domain_id is the primary key, so the conflict target is
    unambiguous."""
    sb = FakeSb()
    MetaClient(sb).upsert_site(domain_id=22, client_id=1,
                               engagement_status="client", source="test")

    assert "insert into meta.site" in sb.sql.lower()
    assert "on conflict (domain_id) do update" in sb.sql.lower()


def test_upsert_site_rejects_an_unknown_engagement_status():
    """Fail here, naming the four values, rather than at the database
    with a constraint name the caller cannot interpret."""
    with pytest.raises(ValueError, match="canceled"):
        MetaClient(FakeSb()).upsert_site(
            domain_id=22, client_id=1, engagement_status="lapsed",
            source="test")


def test_update_site_touches_only_the_fields_given():
    sb = FakeSb()
    MetaClient(sb).update_site(22, industry="charter bus")

    sql = sb.calls[0]["sql"].lower()
    assert "update meta.site" in sql
    assert "industry" in sql
    assert "engagement_status" not in sql


def test_update_site_rejects_a_column_that_does_not_exist():
    with pytest.raises(ValueError, match="nickname"):
        MetaClient(FakeSb()).update_site(22, nickname="oops")


def test_update_site_priority_batch_writes_one_statement():
    """The old method existed because per-row UPDATEs across a client's
    domains are slow; the replacement must not regress that."""
    sb = FakeSb()
    MetaClient(sb).update_site_priority_batch(
        [{"domain_id": 22, "priority": "HIGH"},
         {"domain_id": 23, "priority": "LOW"}])

    assert len(sb.calls) == 1


def test_remove_site_requires_the_domain():
    with pytest.raises(ValueError):
        MetaClient(FakeSb()).remove_site(None)


# ── meta.data_access ─────────────────────────────────────────────────

def test_add_data_access_is_keyed_on_the_account():
    """A site can hold two Google Ads accounts, so the conflict target
    has to include account_identifier or the second is rejected."""
    sb = FakeSb()
    MetaClient(sb).add_data_access(
        domain_id=22, tool="google_ads", account_identifier="123",
        source="test")

    sql = sb.sql.lower()
    assert "on conflict (domain_id, tool, account_identifier)" in sql


def test_add_data_access_rejects_an_unknown_tool():
    with pytest.raises(ValueError, match="screaming_frog"):
        MetaClient(FakeSb()).add_data_access(
            domain_id=22, tool="semrush", source="test")


def test_update_data_access_identifies_the_row_by_its_whole_key():
    """Two rows can share a site and a tool, so updating on those two
    alone would hit both."""
    sb = FakeSb()
    MetaClient(sb).update_data_access(
        domain_id=22, tool="google_ads", account_identifier="123",
        access_status="revoked")

    sql = sb.calls[0]["sql"].lower()
    assert "account_identifier is not distinct from" in sql


def test_delete_data_access_also_matches_the_whole_key():
    sb = FakeSb()
    MetaClient(sb).delete_data_access(
        domain_id=22, tool="google_ads", account_identifier="123")

    assert "delete from meta.data_access" in sb.sql.lower()
    assert "account_identifier is not distinct from" in sb.sql.lower()


def test_deactivate_data_access_is_a_flag_not_a_delete():
    """Access ending is not the same as the record never existing."""
    sb = FakeSb()
    MetaClient(sb).deactivate_data_access(domain_id=22, tool="ga4")

    sql = sb.calls[0]["sql"].lower()
    assert "update meta.data_access" in sql
    assert "is_active" in sql
    assert "delete" not in sql


def test_which_sites_use_a_dataset():
    """The old check_dataset_assignment answered 'which client'. The
    replacement answers 'which sites', because a dataset shared across
    a client's sites is now several rows."""
    sb = FakeSb(pd.DataFrame([{"domain_id": 22}, {"domain_id": 23}]))
    df = MetaClient(sb).get_sites_using_dataset("analytics_1")

    assert len(df) == 2
    assert "meta.data_access" in sb.calls[0]["sql"]
