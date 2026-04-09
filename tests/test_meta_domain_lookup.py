"""Tests for MetaClient domain lookup methods."""

from __future__ import annotations

import pandas as pd


def test_get_domain_exact_match(hub, fake_bq):
    """get_domain returns dict when domain exists in Meta.domains."""
    fake_bq.client.set_next_result(
        pd.DataFrame(
            [{"domain_id": 42, "domain": "example.com", "domain_name": "Example", "is_active": True}]
        )
    )
    result = hub.get_domain("example.com")
    assert result == {"domain_id": 42, "domain": "example.com", "domain_name": "Example", "is_active": True}
    sql = fake_bq.client.queries[-1]["sql"]
    assert "Meta.domains" in sql


def test_get_domain_not_found(hub, fake_bq):
    """get_domain returns None when domain doesn't exist."""
    fake_bq.client.set_next_result(pd.DataFrame())
    result = hub.get_domain("nonexistent.com")
    assert result is None


def test_get_domain_preserves_path(hub, fake_bq):
    """get_domain preserves paths like kitchenguard.com/fw."""
    fake_bq.client.set_next_result(
        pd.DataFrame(
            [{"domain_id": 10, "domain": "kitchenguard.com/fw", "domain_name": "Kitchenguard", "is_active": True}]
        )
    )
    result = hub.get_domain("kitchenguard.com/fw")
    assert result["domain"] == "kitchenguard.com/fw"
    assert result["domain_id"] == 10


def test_clean_domain_preserve_path_true():
    """_clean_domain with preserve_path=True keeps the path."""
    from skyward.data.meta.client import MetaClient
    assert MetaClient._clean_domain("https://www.kitchenguard.com/fw", preserve_path=True) == "kitchenguard.com/fw"
    assert MetaClient._clean_domain("example.com/fw/", preserve_path=True) == "example.com/fw"
    assert MetaClient._clean_domain("https://www.example.com:8080/path?q=1#frag", preserve_path=True) == "example.com/path"


def test_clean_domain_preserve_path_false_unchanged():
    """_clean_domain with preserve_path=False (default) still strips paths."""
    from skyward.data.meta.client import MetaClient
    assert MetaClient._clean_domain("https://www.kitchenguard.com/fw") == "kitchenguard.com"
    assert MetaClient._clean_domain("example.com/page") == "example.com"


def test_search_domains_fuzzy_match(hub, fake_bq):
    """search_domains returns DataFrame of partial matches."""
    fake_bq.client.set_next_result(
        pd.DataFrame(
            [
                {"domain_id": 1, "domain": "buscharter.com.au", "domain_name": "Buscharter", "is_active": True},
                {"domain_id": 2, "domain": "buscharter.co.nz", "domain_name": "Buscharter", "is_active": True},
            ]
        )
    )
    result = hub.search_domains("buscharter")
    assert len(result) == 2
    assert "buscharter.com.au" in result["domain"].values
    sql = fake_bq.client.queries[-1]["sql"]
    assert "LIKE" in sql


def test_search_domains_no_results(hub, fake_bq):
    """search_domains returns empty DataFrame when no matches."""
    fake_bq.client.set_next_result(pd.DataFrame())
    result = hub.search_domains("zzzznotreal")
    assert len(result) == 0


def test_get_domain_passes_path_to_query(hub, fake_bq):
    """get_domain passes the full path (e.g. kitchenguard.com/fw) as the query parameter."""
    fake_bq.client.set_next_result(pd.DataFrame())
    hub.get_domain("https://www.kitchenguard.com/fw")
    job_config = fake_bq.client.queries[-1]["job_config"]
    param_value = job_config.query_parameters[0].value
    assert param_value == "kitchenguard.com/fw"


def test_search_domains_normalizes_input(hub, fake_bq):
    """search_domains extracts bare domain name for LIKE pattern (e.g. 'buscharter' from 'buscharter.com.au')."""
    fake_bq.client.set_next_result(pd.DataFrame())
    hub.search_domains("buscharter.com.au")
    job_config = fake_bq.client.queries[-1]["job_config"]
    pattern = job_config.query_parameters[0].value
    assert pattern == "%buscharter%"


def test_add_domain_basic(hub, fake_bq):
    """add_domain inserts into Meta.domains and returns new domain_id."""
    # First query: get_next_id (MAX domain_id)
    fake_bq.client.queue_result(pd.DataFrame([{"max_id": 99}]))
    # Second query: check if domain already exists (get_domain call)
    fake_bq.client.queue_result(pd.DataFrame())

    domain_id = hub.add_domain("newsite.com")
    assert domain_id == 100

    # Should have loaded a DataFrame into Meta.domains
    loaded = fake_bq.client.loaded_tables
    assert len(loaded) >= 1
    domains_load = loaded[-1]
    assert "Meta.domains" in domains_load["table_ref"]
    df = domains_load["df"]
    assert df.iloc[0]["domain"] == "newsite.com"
    assert df.iloc[0]["domain_id"] == 100
    assert df.iloc[0]["is_active"] is True

def test_add_domain_with_client_id(hub, fake_bq):
    """add_domain with client_id also inserts into Meta.client_domains."""
    # get_next_id for domains
    fake_bq.client.queue_result(pd.DataFrame([{"max_id": 50}]))
    # check if domain already exists
    fake_bq.client.queue_result(pd.DataFrame())

    domain_id = hub.add_domain("client-site.com", client_id=7, is_competitor=True, priority="HIGH")
    assert domain_id == 51

    # Should have two loads: Meta.domains and Meta.client_domains
    loaded = fake_bq.client.loaded_tables
    assert len(loaded) >= 2
    client_domains_load = [l for l in loaded if "client_domains" in l["table_ref"]]
    assert len(client_domains_load) == 1
    cd_df = client_domains_load[0]["df"]
    assert cd_df.iloc[0]["client_id"] == 7
    assert cd_df.iloc[0]["domain_id"] == 51
    assert cd_df.iloc[0]["is_competitor"] is True
    assert cd_df.iloc[0]["priority"] == "HIGH"

def test_add_domain_already_exists(hub, fake_bq):
    """add_domain returns existing domain_id if domain is already in Meta.domains."""
    # get_next_id (won't be used)
    fake_bq.client.queue_result(pd.DataFrame([{"max_id": 10}]))
    # check if domain exists — it does
    fake_bq.client.queue_result(
        pd.DataFrame([{"domain_id": 42, "domain": "existing.com", "domain_name": "Existing", "is_active": True}])
    )

    domain_id = hub.add_domain("existing.com")
    assert domain_id == 42
    # Should NOT have loaded anything new into Meta.domains
    assert len(fake_bq.client.loaded_tables) == 0
