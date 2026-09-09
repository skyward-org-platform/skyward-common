"""Tests for Client CRUD operations in DataHub."""
import pandas as pd
import pytest


# ─── get_client by ID ──────────────────────────────────────────────────────

def test_get_client_by_id(hub, fake_bq):
    """get_client queries by client_id, not fetching all clients."""
    fake_bq.client.set_next_result(pd.DataFrame({
        "client_id": [1], "client_name": ["Acme"], "abbreviation": ["ACM"],
        "is_active": [True], "notes": [None], "created_at": [pd.Timestamp.now()],
    }))
    result = hub.get_client(client_id=1)
    sql = fake_bq.client.queries[-1]["sql"]
    assert "@client_id" in sql
    assert result is not None
    assert result["client_name"] == "Acme"


# ─── Test 1: list_clients includes is_active ────────────────────────────────

def test_list_clients_includes_is_active(hub, fake_bq):
    fake_bq.client.set_next_result(pd.DataFrame({
        "client_id": [1],
        "client_name": ["Test Client"],
        "abbreviation": [None],
        "is_active": [True],
        "notes": [None],
        "created_at": [pd.Timestamp.now()],
    }))
    result = hub.list_clients()
    assert "is_active" in result.columns
    # Verify the SQL query selects is_active
    query_sql = fake_bq.client.queries[-1]["sql"]
    assert "is_active" in query_sql


def test_list_clients_with_search(hub, fake_bq):
    fake_bq.client.set_next_result(pd.DataFrame({
        "client_id": [1],
        "client_name": ["Acme Corp"],
        "abbreviation": ["ACM"],
        "is_active": [True],
        "notes": [None],
        "created_at": [pd.Timestamp.now()],
    }))
    result = hub.list_clients(search="acme")
    sql = fake_bq.client.queries[-1]["sql"]
    assert "LIKE" in sql
    assert "@search" in sql


def test_list_clients_with_counts(hub, fake_bq):
    fake_bq.client.set_next_result(pd.DataFrame({
        "client_id": [1],
        "client_name": ["Test"],
        "abbreviation": [None],
        "is_active": [True],
        "notes": [None],
        "created_at": [pd.Timestamp.now()],
        "domain_count": [3],
        "competitor_count": [2],
        "project_count": [1],
    }))
    result = hub.list_clients(include_counts=True)
    sql = fake_bq.client.queries[-1]["sql"]
    assert "domain_count" in sql or "COUNT" in sql


def test_list_clients_counts_come_from_site_not_client_domains(hub, fake_bq):
    """domain_count is meta.site; competitor_count is meta.site_competitors.

    Asserts behaviour against real rows rather than matching SQL text: the
    old version of this test checked a BigQuery string for a method that
    has run on Supabase since v1.5.0, so it could only ever pass by being
    skipped.
    """
    cid = hub.add_client("CountMe")
    did = hub.add_domain("countme.com")
    hub.upsert_site(domain_id=did, client_id=cid,
                    engagement_status="client", source="test")
    rival = hub.add_domain("countme-rival.com")
    hub.add_site_competitor(did, rival)

    df = hub.list_clients(include_counts=True)
    row = df[df["client_id"] == cid].iloc[0]
    assert int(row["domain_count"]) == 1
    assert int(row["competitor_count"]) == 1


def test_list_clients_counts_a_shared_competitor_once(hub, fake_bq):
    """Competitors are per-site now, so one rival on two sites counts once."""
    cid = hub.add_client("SharedRival")
    a = hub.add_domain("shared-a.com")
    b = hub.add_domain("shared-b.com")
    for d in (a, b):
        hub.upsert_site(domain_id=d, client_id=cid,
                        engagement_status="client", source="test")
    rival = hub.add_domain("shared-rival.com")
    hub.add_site_competitor(a, rival)
    hub.add_site_competitor(b, rival)

    df = hub.list_clients(include_counts=True)
    row = df[df["client_id"] == cid].iloc[0]
    assert int(row["domain_count"]) == 2
    assert int(row["competitor_count"]) == 1


# ─── Test 2: add_client generates auto-ID ───────────────────────────────────

def test_add_client_generates_id(hub, fake_bq):
    fake_bq.client.set_next_result(pd.DataFrame({"max_id": [None]}))
    result = hub.add_client("Test Client", notes="A test")
    assert result == 1
    insert_query = fake_bq.client.queries[-1]["sql"]
    assert "INSERT INTO" in insert_query
    assert "Meta.clients" in insert_query


def test_add_client_with_abbreviation(hub, fake_bq):
    fake_bq.client.set_next_result(pd.DataFrame({"max_id": [5]}))
    result = hub.add_client("Test Client", abbreviation="TST", notes="A test")
    assert result == 6
    insert_query = fake_bq.client.queries[-1]["sql"]
    assert "abbreviation" in insert_query


# ─── Test 3: update_client ───────────────────────────────────────────────────

def test_update_client(hub, fake_bq):
    hub.update_client(1, client_name="New Name", is_active=False)
    update_query = fake_bq.client.queries[-1]["sql"]
    assert "UPDATE" in update_query
    assert "Meta.clients" in update_query


# ─── Test 4: deactivate_client ───────────────────────────────────────────────

def test_deactivate_client_no_cascade(hub, fake_bq):
    hub.deactivate_client(1, cascade=False)
    assert len(fake_bq.client.queries) == 1
    assert "UPDATE" in fake_bq.client.queries[0]["sql"]
    assert "Meta.clients" in fake_bq.client.queries[0]["sql"]


def test_deactivate_client_cascade_reaches_sites_and_tool_access(hub, fake_bq):
    """Cascade now walks meta.site and deactivates meta.data_access.

    The old version counted BigQuery statements, which says nothing about
    whether the right rows were touched.
    """
    cid = hub.add_client("Cascade")
    did = hub.add_domain("cascade.com")
    hub.upsert_site(domain_id=did, client_id=cid,
                    engagement_status="client", source="test")
    hub.add_data_access(did, "ga4", "test", account_identifier="a1")

    hub.deactivate_client(cid, cascade=True)

    assert hub.get_client(cid)["is_active"] is False
    assert hub.get_domain_by_id(did)["is_active"] is False
    access = hub.get_data_access(did, active_only=False)
    assert len(access) == 1 and bool(access.iloc[0]["is_active"]) is False


def test_deactivate_client_cascade_spares_another_clients_site(hub, fake_bq):
    """A domain that is also an active client's site must stay active."""
    keep = hub.add_client("Keeper")
    drop = hub.add_client("Dropper")
    shared = hub.add_domain("shared-site.com")
    hub.upsert_site(domain_id=shared, client_id=keep,
                    engagement_status="client", source="test")

    hub.deactivate_client(drop, cascade=True)

    assert hub.get_domain_by_id(shared)["is_active"] is True
