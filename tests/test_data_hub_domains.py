"""Tests for Domain CRUD operations in DataHub."""
import pandas as pd
import pytest


# ─── Test: add_domains ──────────────────────────────────────────────────────

def test_add_domains_bulk(hub, fake_bq):
    fake_bq.client.queue_result(pd.DataFrame(columns=["domain_id"]))
    fake_bq.client.queue_result(pd.DataFrame({"max_id": [None]}))
    result = hub.add_domains(["example.com"], 1, is_competitor=False)
    assert len(result) == 1
    assert result[0]["domain_id"] == 1
    assert result[0]["domain"] == "example.com"
    assert result[0]["domain_name"] is not None


# ─── Test: remove_client_domain ─────────────────────────────────────────────

def test_remove_client_domain(hub, fake_bq):
    hub.remove_client_domain(1, 2)
    delete_query = fake_bq.client.queries[-1]["sql"]
    assert "DELETE" in delete_query
    assert "Meta.client_domains" in delete_query


# ─── Test: update_domain ────────────────────────────────────────────────────

def test_update_domain(hub, fake_bq):
    hub.update_domain(1, domain_name="New Name", is_active=False)
    update_query = fake_bq.client.queries[-1]["sql"]
    assert "UPDATE" in update_query
    assert "Meta.domains" in update_query


# ─── Test: _domain_to_name ──────────────────────────────────────────────────

def test_domain_to_name():
    from skyward.data.hub import DataHub
    assert DataHub._domain_to_name("searspartsdirect.com") == "Searspartsdirect"
    assert DataHub._domain_to_name("www.my-cool-site.org") == "My Cool Site"
    assert DataHub._domain_to_name("hello_world.net") == "Hello World"


# ─── Test 1: get_client_domains ─────────────────────────────────────────────

def test_get_client_domains_queries_correct_tables(hub, fake_bq):
    hub.get_client_domains(1)
    sql = fake_bq.client.queries[-1]["sql"]
    assert "Meta.client_domains" in sql
    assert "Meta.domains" in sql
    assert "is_competitor" in sql


def test_get_client_domains_returns_domains_with_competitor_flag(hub, fake_bq):
    fake_bq.client.set_next_result(pd.DataFrame({
        "domain_id": [1, 2],
        "domain": ["example.com", "rival.com"],
        "domain_name": ["Example", "Rival"],
        "is_active": [True, True],
        "is_competitor": [False, True],
        "notes": [None, None],
    }))
    result = hub.get_client_domains(1)
    assert len(result) == 2
    assert "is_competitor" in result.columns
    assert "domain" in result.columns


# ─── Test: old company methods removed ─────────────────────────────────────

def test_old_company_methods_removed():
    """Old company-based methods must not exist; they reference dropped tables."""
    from skyward.data.hub import DataHub
    assert not hasattr(DataHub, "list_companies"), "list_companies() should be removed"
    assert not hasattr(DataHub, "get_project_companies"), "get_project_companies() should be removed"
    assert not hasattr(DataHub, "add_company"), "add_company() should be removed"
    assert not hasattr(DataHub, "add_project_company"), "add_project_company() should be removed"


# ─── Test: list_project_domains ─────────────────────────────────────────────

def test_list_project_domains(hub, fake_bq):
    fake_bq.client.set_next_result(pd.DataFrame({
        "domain_id": [1, 2],
        "domain": ["example.com", "rival.com"],
        "domain_name": ["Example", "Rival"],
        "role": ["client", "competitor"],
        "priority": [None, "high"],
    }))
    result = hub.list_project_domains(1)
    assert len(result) == 2
    assert "role" in result.columns


# ─── Test: get_client_data domain subquery uses new schema ──────────────────

def test_get_client_data_domain_lookup_uses_new_schema(hub, fake_bq):
    """get_client_data domain lookup must reference Meta.domains/client_domains, not old tables."""
    hub.get_client_data("000001", "dataforseo_labs-google-ranked_keywords", use_domain_lookup=True)
    sql = fake_bq.client.queries[-1]["sql"]
    assert "Meta.company_domains" not in sql, "Must not reference old Meta.company_domains"
    assert "Meta.companies" not in sql, "Must not reference old Meta.companies"
    assert "Meta.domains" in sql
    assert "Meta.client_domains" in sql
    assert "is_competitor" in sql
