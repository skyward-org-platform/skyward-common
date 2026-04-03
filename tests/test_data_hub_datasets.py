"""Tests for Dataset methods in DataHub."""
import pandas as pd


# ─── Test: add_client_dataset with domain_id ─────────────────────────────────

def test_add_client_dataset_with_domain_id(hub, fake_bq):
    # First query: check_dataset_assignment returns empty (not assigned)
    fake_bq.client.queue_result(pd.DataFrame(columns=["client_id", "client_name"]))
    hub.add_client_dataset(
        client_id=1, dataset_id="analytics_123",
        dataset_type="ga4", hostname="example.com", domain_id=5,
    )
    # The last query should be the INSERT into client_datasets
    insert_query = fake_bq.client.queries[-1]["sql"]
    assert "domain_id" in insert_query
    assert "Meta.client_datasets" in insert_query


def test_add_client_dataset_returns_warning_if_already_assigned(hub, fake_bq):
    # check_dataset_assignment returns an existing assignment
    fake_bq.client.queue_result(pd.DataFrame({
        "client_id": [2], "client_name": ["Other Client"],
    }))
    result = hub.add_client_dataset(
        client_id=1, dataset_id="analytics_123",
        dataset_type="ga4", hostname="example.com",
    )
    assert result["status"] == "added"
    assert result["warning"] is not None
    assert "already assigned" in result["warning"]


# ─── Test: scan_and_match_datasets uses dataset_catalog ──────────────────────

def test_scan_and_match_uses_dataset_catalog(hub, fake_bq):
    # get_client_datasets returns empty (no existing links)
    fake_bq.client.queue_result(pd.DataFrame(columns=[
        "client_id", "domain_id", "dataset_id", "dataset_type", "hostname",
        "is_active", "notes", "created_at",
    ]))
    # domain lookup returns empty
    fake_bq.client.queue_result(pd.DataFrame(columns=["domain_id", "domain", "client_id"]))
    # get_dataset_catalog returns empty
    fake_bq.client.queue_result(pd.DataFrame(columns=[
        "dataset", "dataset_type", "hostname", "is_standardized",
        "owner", "active", "updated_at",
    ]))
    result = hub.scan_and_match_datasets()
    assert "proposed" in result
    assert "unmatched" in result
    assert "already_cached" in result
    # Verify no old table references
    all_sql = " ".join(q["sql"] for q in fake_bq.client.queries)
    assert "company_domains" not in all_sql


# ─── Test: approve_scanned_datasets ─────────────────────────────────────────

def test_approve_scanned_datasets_empty(hub, fake_bq):
    result = hub.approve_scanned_datasets([])
    assert result == 0


def test_approve_scanned_datasets_bulk_insert(hub, fake_bq):
    approvals = [
        {"dataset_id": "analytics_123", "dataset_type": "ga4", "hostname": "example.com", "client_id": 1, "domain_id": 5},
        {"dataset_id": "jepto_gsc_example", "dataset_type": "gsc", "hostname": "example.com", "client_id": 1, "domain_id": 5},
    ]
    result = hub.approve_scanned_datasets(approvals)
    assert result == 2
    # Should have MERGE queries for dataset_catalog + one load_table for client_datasets
    assert len(fake_bq.client.loaded_tables) == 1
    loaded = fake_bq.client.loaded_tables[0]
    assert "Meta.client_datasets" in loaded["table_ref"]
    assert len(loaded["df"]) == 2
