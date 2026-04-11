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
    # 1. batch existing check — none exist
    fake_bq.client.queue_result(pd.DataFrame())
    # 2. get_next_id (MAX domain_id)
    fake_bq.client.queue_result(pd.DataFrame([{"max_id": 99}]))

    domain_id = hub.add_domain("newsite.com")
    assert domain_id == 100

    # Should have loaded a DataFrame into Meta.domains
    loaded = fake_bq.client.loaded_tables
    assert len(loaded) >= 1
    domains_load = loaded[0]
    assert "Meta.domains" in domains_load["table_ref"]
    df = domains_load["df"]
    assert df.iloc[0]["domain"] == "newsite.com"
    assert df.iloc[0]["domain_id"] == 100


def test_add_domain_with_client_id(hub, fake_bq):
    """add_domain with client_id also inserts into Meta.client_domains."""
    # 1. client existence check
    fake_bq.client.queue_result(pd.DataFrame([{"client_id": 7, "client_name": "Acme", "abbreviation": None, "is_active": True, "notes": None, "created_at": pd.Timestamp.now()}]))
    # 2. batch existing check — none exist
    fake_bq.client.queue_result(pd.DataFrame())
    # 3. get_next_id
    fake_bq.client.queue_result(pd.DataFrame([{"max_id": 50}]))
    # 4. existing links check — none
    fake_bq.client.queue_result(pd.DataFrame())

    domain_id = hub.add_domain("client-site.com", client_id=7, is_competitor=True, priority="HIGH")
    assert domain_id == 51

    loaded = fake_bq.client.loaded_tables
    client_domains_load = [l for l in loaded if "client_domains" in l["table_ref"]]
    assert len(client_domains_load) == 1
    cd_df = client_domains_load[0]["df"]
    assert cd_df.iloc[0]["client_id"] == 7
    assert cd_df.iloc[0]["domain_id"] == 51
    assert bool(cd_df.iloc[0]["is_competitor"]) is True
    assert cd_df.iloc[0]["priority"] == "HIGH"


def test_add_domain_already_exists(hub, fake_bq):
    """add_domain returns existing domain_id if domain is already in Meta.domains."""
    # batch existing check — domain exists
    fake_bq.client.queue_result(
        pd.DataFrame([{"domain_id": 42, "domain": "existing.com"}])
    )

    domain_id = hub.add_domain("existing.com")
    assert domain_id == 42
    # Should NOT have loaded anything new
    assert len(fake_bq.client.loaded_tables) == 0


def test_add_domain_existing_domain_new_client_link(hub, fake_bq):
    """add_domain creates the client_domains link even when the domain already exists."""
    # 1. client existence check
    fake_bq.client.queue_result(pd.DataFrame([{"client_id": 7, "client_name": "Acme", "abbreviation": None, "is_active": True, "notes": None, "created_at": pd.Timestamp.now()}]))
    # 2. batch existing check — domain exists
    fake_bq.client.queue_result(
        pd.DataFrame([{"domain_id": 42, "domain": "existing.com"}])
    )
    # 3. existing links check — none
    fake_bq.client.queue_result(pd.DataFrame())

    domain_id = hub.add_domain("existing.com", client_id=7, is_competitor=False, priority="HIGH")
    assert domain_id == 42
    loaded = fake_bq.client.loaded_tables
    cd_loads = [l for l in loaded if "client_domains" in l["table_ref"]]
    domain_loads = [l for l in loaded if "Meta.domains" in l["table_ref"] and "client_domains" not in l["table_ref"]]
    assert len(cd_loads) == 1
    assert len(domain_loads) == 0
    cd_df = cd_loads[0]["df"]
    assert cd_df.iloc[0]["client_id"] == 7
    assert cd_df.iloc[0]["domain_id"] == 42
    assert bool(cd_df.iloc[0]["is_competitor"]) is False
    assert cd_df.iloc[0]["priority"] == "HIGH"


def test_add_domain_existing_link_skipped(hub, fake_bq):
    """add_domain does nothing if the domain is already linked to this client."""
    # client existence check
    fake_bq.client.queue_result(pd.DataFrame([{"client_id": 7, "client_name": "Acme", "abbreviation": None, "is_active": True, "notes": None, "created_at": pd.Timestamp.now()}]))
    # domain exists check
    fake_bq.client.queue_result(
        pd.DataFrame([{"domain_id": 42, "domain": "existing.com", "domain_name": "Existing", "is_active": True}])
    )
    # link check — link already exists
    fake_bq.client.queue_result(pd.DataFrame([{"domain_id": 42}]))

    domain_id = hub.add_domain("existing.com", client_id=7)
    assert domain_id == 42
    # Should NOT have inserted anything
    assert len(fake_bq.client.loaded_tables) == 0


def test_add_domains_rejects_invalid_priority(hub, fake_bq):
    """add_domains should raise ValueError for an invalid priority."""
    import pytest as _pytest
    with _pytest.raises(ValueError, match="Invalid priority"):
        hub.add_domains(domains=["test-bad.com"], client_id=1, priority="FLAMINGO")


def test_add_domains_rejects_nonexistent_client(hub, fake_bq):
    """add_domains should raise RuntimeError when client_id doesn't exist."""
    import pytest as _pytest
    # client existence check — returns empty (client not found)
    fake_bq.client.queue_result(pd.DataFrame())
    with _pytest.raises(RuntimeError, match="Client 99999 not found"):
        hub.add_domains(domains=["test-orphan.com"], client_id=99999)


def test_add_domain_rejects_empty_string(hub, fake_bq):
    """add_domain should raise ValueError for an empty domain string."""
    import pytest as _pytest
    with _pytest.raises(ValueError, match="Domain cannot be empty"):
        hub.add_domain("")


def test_add_domain_rejects_whitespace(hub, fake_bq):
    """add_domain should raise ValueError for whitespace-only input."""
    import pytest as _pytest
    with _pytest.raises(ValueError, match="Domain cannot be empty"):
        hub.add_domain("   ")


def test_add_domains_normalizes_priority(hub, fake_bq):
    """add_domains should normalize lowercase 'very high' to 'VERY HIGH'."""
    # client existence check
    fake_bq.client.queue_result(pd.DataFrame([{"client_id": 1, "client_name": "Acme", "abbreviation": None, "is_active": True, "notes": None, "created_at": pd.Timestamp.now()}]))
    # batch existing check — none exist
    fake_bq.client.queue_result(pd.DataFrame())
    # get_next_id
    fake_bq.client.queue_result(pd.DataFrame([{"max_id": 10}]))
    # existing links check — none
    fake_bq.client.queue_result(pd.DataFrame())

    hub.add_domains(domains=["test-vhigh.com"], client_id=1, priority="very high")
    link_loads = [t for t in fake_bq.client.loaded_tables if "client_domains" in t["table_ref"]]
    assert len(link_loads) == 1
    assert link_loads[0]["df"]["priority"].iloc[0] == "VERY HIGH"


def test_add_domains_without_client_id(hub, fake_bq):
    """add_domains should work without a client_id (just inserts domains, no links)."""
    # existing check — none exist
    fake_bq.client.queue_result(pd.DataFrame())
    # get_next_id
    fake_bq.client.queue_result(pd.DataFrame([{"max_id": 10}]))

    result = hub.add_domains(domains=["test-standalone.com"])
    assert len(result) == 1
    assert result[0]["domain"] == "test-standalone.com"
    # Should have inserted into Meta.domains but NOT into client_domains
    loaded = fake_bq.client.loaded_tables
    cd_loads = [l for l in loaded if "client_domains" in l["table_ref"]]
    assert len(cd_loads) == 0


def test_add_domain_skips_get_next_id_when_exists(hub, fake_bq):
    """add_domain should check for existing domain BEFORE calling get_next_id."""
    # Only queue the get_domain check result — no get_next_id result needed
    fake_bq.client.queue_result(
        pd.DataFrame([{"domain_id": 42, "domain": "existing.com", "domain_name": "Existing", "is_active": True}])
    )
    domain_id = hub.add_domain("existing.com")
    assert domain_id == 42
    # Only ONE query should have been made (the get_domain lookup)
    assert len(fake_bq.client.queries) == 1
    assert "Meta.domains" in fake_bq.client.queries[0]["sql"]
