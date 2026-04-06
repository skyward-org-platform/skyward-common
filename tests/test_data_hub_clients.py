"""Tests for Client CRUD operations in DataHub."""
import pandas as pd
import pytest


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


def test_list_clients_counts_exclude_inactive_domains(hub, fake_bq):
    """Count subqueries should only count active domains (d.is_active = TRUE)."""
    fake_bq.client.set_next_result(pd.DataFrame({
        "client_id": [1], "client_name": ["Test"], "abbreviation": [None],
        "is_active": [True], "notes": [None], "created_at": [pd.Timestamp.now()],
        "domain_count": [2], "competitor_count": [1], "project_count": [0],
    }))
    hub.list_clients(include_counts=True)
    sql = fake_bq.client.queries[-1]["sql"]
    # The count subqueries must join domains and filter by is_active
    assert "is_active = TRUE" in sql.replace("  ", " ")


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


def test_deactivate_client_cascade(hub, fake_bq):
    fake_bq.client.set_next_result(pd.DataFrame({"domain_id": [1, 2]}))
    hub.deactivate_client(1, cascade=True)
    assert len(fake_bq.client.queries) >= 3
