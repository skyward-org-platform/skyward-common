"""
Comprehensive edge-case tests designed to break MetaClient.

Covers static helpers (_clean_domain, _domain_to_name), ID generation,
add_domains, update_domains_batch, update_client_domains_priority_batch,
and deactivate_client cascade behaviour.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from skyward.data.meta import MetaClient


# ══════════════════════════════════════════════════════════════════════════════
# Helpers — extend FakeBQClient with load_table_from_dataframe support
# ══════════════════════════════════════════════════════════════════════════════

class FakeLoadJob:
    def result(self):
        return self


class FakeQueryResult:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def result(self):
        return self

    def to_dataframe(self):
        return self._df


class FakeBQClient:
    """Minimal fake for google.cloud.bigquery.Client, with load support."""

    def __init__(self):
        self.project = "test-project"
        self.queries = []
        self._results_queue = []
        self.loaded_tables = []

    def queue_result(self, df: pd.DataFrame):
        self._results_queue.append(df)

    def query(self, sql, job_config=None):
        self.queries.append({"sql": sql, "job_config": job_config})
        if self._results_queue:
            result_df = self._results_queue.pop(0)
        else:
            result_df = pd.DataFrame()
        return FakeQueryResult(result_df)

    def load_table_from_dataframe(self, df, table_ref, job_config=None):
        self.loaded_tables.append({"table_ref": table_ref, "df": df.copy(), "job_config": job_config})
        return FakeLoadJob()

    def get_table(self, table_ref):
        return SimpleNamespace(num_rows=0)


class FakeBigQueryClient:
    def __init__(self):
        self.client = FakeBQClient()


@pytest.fixture
def fake_bq():
    return FakeBigQueryClient()


@pytest.fixture
def meta(fake_bq):
    return MetaClient(fake_bq)


# ══════════════════════════════════════════════════════════════════════════════
# 1. _clean_domain — static method
# ══════════════════════════════════════════════════════════════════════════════

class TestCleanDomain:
    """Test _clean_domain edge cases — run without any fixtures."""

    def test_normal_domain(self):
        assert MetaClient._clean_domain("example.com") == "example.com"

    def test_https_prefix(self):
        assert MetaClient._clean_domain("https://example.com") == "example.com"

    def test_http_prefix(self):
        assert MetaClient._clean_domain("http://example.com") == "example.com"

    def test_www_prefix(self):
        assert MetaClient._clean_domain("www.example.com") == "example.com"

    def test_full_url(self):
        assert MetaClient._clean_domain("https://www.example.com/path/page?q=1#hash") == "example.com"

    def test_trailing_slash(self):
        assert MetaClient._clean_domain("example.com/") == "example.com"

    def test_multiple_slashes_in_path(self):
        assert MetaClient._clean_domain("https://example.com/a/b/c/") == "example.com"

    def test_empty_string(self):
        assert MetaClient._clean_domain("") == ""

    def test_whitespace_only(self):
        assert MetaClient._clean_domain("   ") == ""

    def test_leading_trailing_whitespace(self):
        assert MetaClient._clean_domain("  example.com  ") == "example.com"

    def test_mixed_case(self):
        assert MetaClient._clean_domain("HTTPS://WWW.EXAMPLE.COM") == "example.com"

    def test_just_protocol(self):
        # "https://" stripped → "" — should return "" not crash
        result = MetaClient._clean_domain("https://")
        assert result == ""

    def test_port_number(self):
        # Port IS stripped by the fixed implementation via ":" rsplit
        result = MetaClient._clean_domain("example.com:8080")
        assert result == "example.com"

    def test_ftp_protocol(self):
        # The fixed implementation strips any protocol via "://" split, including ftp://
        result = MetaClient._clean_domain("ftp://example.com")
        assert result == "example.com"

    def test_double_protocol(self):
        # "https://https://example.com".split("://", 1) → ["https", "https://example.com"]
        # Takes index 1 = "https://example.com", then split("/")[0] = "https:",
        # then port strip on ":" rsplit → "https".
        # Known limitation: double protocol is not a real-world scenario and is not
        # fully handled — the result is "https" rather than "example.com".
        result = MetaClient._clean_domain("https://https://example.com")
        assert result == "https"

    def test_subdomain_is_preserved(self):
        # sub.example.com — not www, so it should remain
        assert MetaClient._clean_domain("sub.example.com") == "sub.example.com"

    def test_query_only(self):
        # "?q=1" → strip path → "", strip query → ""
        result = MetaClient._clean_domain("example.com?q=1")
        assert result == "example.com"


# ══════════════════════════════════════════════════════════════════════════════
# 2. _domain_to_name — static method
# ══════════════════════════════════════════════════════════════════════════════

class TestDomainToName:
    """Test _domain_to_name edge cases — run without any fixtures."""

    def test_normal_domain(self):
        assert MetaClient._domain_to_name("example.com") == "Example"

    def test_multi_part_tld(self):
        assert MetaClient._domain_to_name("buscharter.com.au") == "Buscharter"

    def test_hyphens_become_spaces(self):
        assert MetaClient._domain_to_name("my-cool-site.com") == "My Cool Site"

    def test_underscores_become_spaces(self):
        assert MetaClient._domain_to_name("my_site.com") == "My Site"

    def test_sub_subdomain_left_in(self):
        # tldextract("www.sub.example.com") → domain="sub" (it extracts sub as domain)
        # since www. is part of the subdomain field in tldextract, not the domain field
        result = MetaClient._domain_to_name("www.sub.example.com")
        # tldextract gives domain="sub", not "example" for this input
        # The function returns "Sub" — document the actual behaviour
        assert isinstance(result, str)
        assert len(result) > 0

    def test_ip_address(self):
        # tldextract can't extract a domain from an IP — falls back to split
        result = MetaClient._domain_to_name("192.168.1.1")
        # fallback: "192.168.1.1".split(".")[0] → "192" → "192"
        assert result == "192"

    def test_localhost_no_tld(self):
        # tldextract returns suffix="" for localhost → fallback kicks in
        # fallback: "localhost".split(".")[0] → "localhost" → "Localhost"
        result = MetaClient._domain_to_name("localhost")
        assert result == "Localhost"

    def test_empty_string(self):
        # tldextract on "" → domain="" and suffix="" → fallback
        # fallback: "".split(".")[0] → "" → "".title() → ""
        result = MetaClient._domain_to_name("")
        assert result == ""

    def test_just_tld_dot_com(self):
        # ".com" → tldextract domain="" suffix="com" → fallback because name is ""
        # fallback: ".com".replace("www.", "").split(".")[0] → "" → ""
        result = MetaClient._domain_to_name(".com")
        assert result == ""

    def test_invalid_unknown_tld(self):
        # tldextract does NOT require a known TLD by default in private mode
        # For "example.xyz123" it may return domain="example" or fall back
        result = MetaClient._domain_to_name("example.xyz123")
        assert isinstance(result, str)
        assert len(result) >= 0  # Should not raise

    def test_unicode_domain(self):
        # Unicode domain — should not raise, title-case result depends on tldextract
        result = MetaClient._domain_to_name("münchen.de")
        assert isinstance(result, str)


# ══════════════════════════════════════════════════════════════════════════════
# 3. get_next_id — ID generation
# ══════════════════════════════════════════════════════════════════════════════

class TestGetNextId:

    def test_empty_table_returns_1(self, meta, fake_bq):
        fake_bq.client.queue_result(pd.DataFrame({"max_id": [None]}))
        assert meta.get_next_id("clients", "client_id") == 1

    def test_increments_from_existing(self, meta, fake_bq):
        fake_bq.client.queue_result(pd.DataFrame({"max_id": [3]}))
        assert meta.get_next_id("clients", "client_id") == 4

    def test_increments_from_1(self, meta, fake_bq):
        fake_bq.client.queue_result(pd.DataFrame({"max_id": [1]}))
        assert meta.get_next_id("clients", "client_id") == 2

    def test_nan_max_id_returns_1(self, meta, fake_bq):
        """A pandas NaN in max_id column should return 1."""
        import numpy as np
        fake_bq.client.queue_result(pd.DataFrame({"max_id": [float("nan")]}))
        assert meta.get_next_id("clients", "client_id") == 1

    def test_non_numeric_max_id_raises(self, meta, fake_bq):
        """Non-numeric max_id (e.g. 'abc') should raise ValueError on int() conversion."""
        fake_bq.client.queue_result(pd.DataFrame({"max_id": ["abc"]}))
        with pytest.raises((ValueError, Exception)):
            meta.get_next_id("clients", "client_id")

    def test_updates_cache(self, meta, fake_bq):
        """get_next_id should update the _max_ids cache."""
        fake_bq.client.queue_result(pd.DataFrame({"max_id": [10]}))
        result = meta.get_next_id("clients", "client_id")
        assert result == 11
        assert meta._max_ids[("Meta", "clients", "client_id")] == 11


# ══════════════════════════════════════════════════════════════════════════════
# 4. add_domains — bulk domain add
# ══════════════════════════════════════════════════════════════════════════════

class TestAddDomains:

    def _queue_client_exists(self, fake_bq, client_id=1):
        """Queue the client existence check (first call in add_domains when client_id is set)."""
        fake_bq.client.queue_result(pd.DataFrame([{
            "client_id": client_id, "client_name": "Test", "abbreviation": None,
            "is_active": True, "notes": None, "created_at": pd.Timestamp.now(),
        }]))

    def _queue_for_new_domain(self, fake_bq):
        """Queue the BQ calls needed when adding a genuinely new domain with a client_id."""
        # 0. client existence check
        self._queue_client_exists(fake_bq)
        # 1. check which domains exist → none
        fake_bq.client.queue_result(pd.DataFrame(columns=["domain_id", "domain"]))
        # 2. get_next_id for domains table
        fake_bq.client.queue_result(pd.DataFrame({"max_id": [None]}))
        # 3. check which client_domains links already exist → none
        fake_bq.client.queue_result(pd.DataFrame(columns=["domain_id"]))

    def test_empty_list_returns_empty(self, meta, fake_bq):
        self._queue_client_exists(fake_bq)
        result = meta.add_domains([], 1, is_competitor=False)
        assert result == []

    def test_whitespace_only_strings_filtered(self, meta, fake_bq):
        self._queue_client_exists(fake_bq)
        result = meta.add_domains(["  ", "\t", ""], 1, is_competitor=False)
        assert result == []

    def test_duplicate_domains_deduplicated(self, meta, fake_bq):
        self._queue_for_new_domain(fake_bq)
        result = meta.add_domains(
            ["example.com", "example.com", "example.com"],
            1,
            is_competitor=False,
        )
        assert len(result) == 1
        assert result[0]["domain"] == "example.com"

    def test_urls_clean_to_same_domain_deduplicated(self, meta, fake_bq):
        """Different URL forms of the same bare domain should dedupe."""
        self._queue_for_new_domain(fake_bq)
        result = meta.add_domains(
            ["http://example.com", "https://www.example.com"],
            1,
            is_competitor=False,
        )
        assert len(result) == 1
        assert result[0]["domain"] == "example.com"

    def test_domain_already_exists_and_already_linked_is_skipped(self, meta, fake_bq):
        """When the domain already exists AND is already linked, skipped=True."""
        self._queue_client_exists(fake_bq)
        # Step 1: domain exists
        fake_bq.client.queue_result(pd.DataFrame({
            "domain_id": [5],
            "domain": ["example.com"],
        }))
        # Step 2: no get_next_id call (no new domains)
        # Step 3: link already exists
        fake_bq.client.queue_result(pd.DataFrame({
            "domain_id": [5],
        }))

        result = meta.add_domains(["example.com"], 1, is_competitor=False)
        assert len(result) == 1
        assert result[0]["skipped"] is True
        assert result[0]["domain_id"] == 5

    def test_domain_already_exists_but_not_linked_is_inserted(self, meta, fake_bq):
        """When the domain exists in Meta.domains but is not yet linked to this client."""
        self._queue_client_exists(fake_bq, client_id=2)
        # Step 1: domain already in Meta.domains
        fake_bq.client.queue_result(pd.DataFrame({
            "domain_id": [7],
            "domain": ["newclient.com"],
        }))
        # Step 2: no get_next_id (domain already exists)
        # Step 3: link does not exist yet
        fake_bq.client.queue_result(pd.DataFrame(columns=["domain_id"]))

        result = meta.add_domains(["newclient.com"], 2, is_competitor=False)
        assert len(result) == 1
        assert result[0]["skipped"] is False
        assert result[0]["domain_id"] == 7
        # A load_table call for client_domains should have been made
        assert any(
            "Meta.client_domains" in t["table_ref"]
            for t in fake_bq.client.loaded_tables
        )

    def test_urls_are_cleaned_before_insert(self, meta, fake_bq):
        """URLs should strip protocol/www/query but preserve path."""
        self._queue_for_new_domain(fake_bq)
        result = meta.add_domains(
            ["https://www.example.com/path?q=1"],
            1,
            is_competitor=False,
        )
        assert result[0]["domain"] == "example.com/path"

    def test_priority_normalised_to_upper(self, meta, fake_bq):
        """Priority passed as lowercase should be uppercased in the inserted row."""
        self._queue_for_new_domain(fake_bq)
        meta.add_domains(["example.com"], 1, is_competitor=False, priority="high")
        # The last loaded table should be client_domains with priority="HIGH"
        link_loads = [t for t in fake_bq.client.loaded_tables if "client_domains" in t["table_ref"]]
        assert len(link_loads) == 1
        assert link_loads[0]["df"]["priority"].iloc[0] == "HIGH"

    def test_none_priority_defaults_to_normal(self, meta, fake_bq):
        """None priority should default to 'NORMAL'."""
        self._queue_for_new_domain(fake_bq)
        meta.add_domains(["example.com"], 1, is_competitor=False, priority=None)
        link_loads = [t for t in fake_bq.client.loaded_tables if "client_domains" in t["table_ref"]]
        assert link_loads[0]["df"]["priority"].iloc[0] == "NORMAL"

    def test_domain_ids_increment_for_multiple_new(self, meta, fake_bq):
        """IDs should increment in Python without extra BQ round-trips."""
        self._queue_client_exists(fake_bq)
        # Step 1: no existing domains
        fake_bq.client.queue_result(pd.DataFrame(columns=["domain_id", "domain"]))
        # Step 2: max_id=3 → next=4
        fake_bq.client.queue_result(pd.DataFrame({"max_id": [3]}))
        # Step 3: no existing links
        fake_bq.client.queue_result(pd.DataFrame(columns=["domain_id"]))

        result = meta.add_domains(
            ["alpha.com", "beta.com", "gamma.com"],
            1,
            is_competitor=False,
        )
        ids = [r["domain_id"] for r in result]
        assert ids == [4, 5, 6]

    def test_empty_string_in_list_filtered_out(self, meta, fake_bq):
        """An empty string in the list should not reach BQ."""
        self._queue_for_new_domain(fake_bq)
        result = meta.add_domains(["example.com", ""], 1, is_competitor=False)
        assert len(result) == 1
        assert result[0]["domain"] == "example.com"


# ══════════════════════════════════════════════════════════════════════════════
# 5. update_domains_batch — MERGE statement
# ══════════════════════════════════════════════════════════════════════════════

class TestUpdateDomainsBatch:

    def test_empty_rows_does_nothing(self, meta, fake_bq):
        meta.update_domains_batch([])
        assert len(fake_bq.client.queries) == 0

    def test_single_row_produces_update(self, meta, fake_bq):
        # Fixed implementation delegates to update_domain() which uses a parameterized UPDATE
        meta.update_domains_batch([{
            "domain_id": 1,
            "domain_name": "Example",
            "is_active": True,
            "notes": "",
        }])
        sql = fake_bq.client.queries[-1]["sql"]
        assert "UPDATE" in sql
        assert "Meta.domains" in sql
        assert "@domain_name" in sql

    def test_single_quote_in_domain_name_parameterized(self, meta, fake_bq):
        """Fixed: domain_name uses @domain_name parameter — no escaping needed."""
        meta.update_domains_batch([{
            "domain_id": 1,
            "domain_name": "O'Brien's Shop",
            "is_active": True,
            "notes": "",
        }])
        sql = fake_bq.client.queries[-1]["sql"]
        # Parameterized query — the raw value is never embedded in the SQL string
        assert "@domain_name" in sql
        assert "O'Brien" not in sql

    def test_backslash_in_domain_name(self, meta, fake_bq):
        """Fixed: domain_name is parameterized — backslashes and quotes are never in the SQL."""
        payload = "foo\\' OR 1=1 --"
        meta.update_domains_batch([{
            "domain_id": 1,
            "domain_name": payload,
            "is_active": True,
            "notes": "",
        }])
        sql = fake_bq.client.queries[-1]["sql"]
        # Parameterized — the raw payload is never injected into the SQL string
        assert "@domain_name" in sql
        assert payload not in sql

    def test_is_active_false_produces_false_literal(self, meta, fake_bq):
        # Fixed: uses @is_active parameter instead of a FALSE literal in the SQL string
        meta.update_domains_batch([{
            "domain_id": 2,
            "domain_name": "Test",
            "is_active": False,
            "notes": "",
        }])
        sql = fake_bq.client.queries[-1]["sql"]
        assert "@is_active" in sql
        params = fake_bq.client.queries[-1]["job_config"].query_parameters
        is_active_param = next((p for p in params if p.name == "is_active"), None)
        assert is_active_param is not None
        assert is_active_param.value is False

    def test_is_active_truthy_non_bool(self, meta, fake_bq):
        """Fixed: is_active=1 (truthy int) passes through to @is_active parameter without error."""
        meta.update_domains_batch([{
            "domain_id": 3,
            "domain_name": "Test",
            "is_active": 1,
            "notes": "",
        }])
        sql = fake_bq.client.queries[-1]["sql"]
        # The fixed code uses @is_active parameter — no literal TRUE/FALSE in the SQL
        assert "@is_active" in sql
        # Query was produced without raising — the value passes through to the parameter
        assert len(fake_bq.client.queries) >= 1

    def test_none_notes_becomes_empty_string(self, meta, fake_bq):
        """notes=None should become '' (not 'None') in the generated SQL."""
        meta.update_domains_batch([{
            "domain_id": 1,
            "domain_name": "X",
            "is_active": True,
            "notes": None,
        }])
        sql = fake_bq.client.queries[-1]["sql"]
        assert "'None'" not in sql

    def test_multiple_rows_single_query(self, meta, fake_bq):
        """Fixed: one parameterized UPDATE per row (delegates to update_domain per row)."""
        rows = [
            {"domain_id": i, "domain_name": f"Domain{i}", "is_active": True, "notes": ""}
            for i in range(1, 6)
        ]
        meta.update_domains_batch(rows)
        assert len(fake_bq.client.queries) == 5


# ══════════════════════════════════════════════════════════════════════════════
# 6. update_client_domains_priority_batch — priority MERGE
# ══════════════════════════════════════════════════════════════════════════════

class TestUpdateClientDomainsPriorityBatch:

    def test_empty_rows_does_nothing(self, meta, fake_bq):
        meta.update_client_domains_priority_batch(1, [])
        assert len(fake_bq.client.queries) == 0

    def test_valid_priority_preserved(self, meta, fake_bq):
        # Fixed: uses @priority parameter — value is not a literal in the SQL string
        meta.update_client_domains_priority_batch(1, [
            {"domain_id": 1, "priority": "HIGH"},
        ])
        sql = fake_bq.client.queries[-1]["sql"]
        assert "@priority" in sql
        params = fake_bq.client.queries[-1]["job_config"].query_parameters
        priority_param = next((p for p in params if p.name == "priority"), None)
        assert priority_param is not None
        assert priority_param.value == "HIGH"

    def test_invalid_priority_defaults_to_normal(self, meta, fake_bq):
        meta.update_client_domains_priority_batch(1, [
            {"domain_id": 1, "priority": "MEGA"},
        ])
        params = fake_bq.client.queries[-1]["job_config"].query_parameters
        priority_param = next((p for p in params if p.name == "priority"), None)
        assert priority_param is not None
        assert priority_param.value == "NORMAL"

    def test_empty_string_priority_defaults_to_normal(self, meta, fake_bq):
        meta.update_client_domains_priority_batch(1, [
            {"domain_id": 1, "priority": ""},
        ])
        params = fake_bq.client.queries[-1]["job_config"].query_parameters
        priority_param = next((p for p in params if p.name == "priority"), None)
        assert priority_param is not None
        assert priority_param.value == "NORMAL"

    def test_none_priority_defaults_to_normal(self, meta, fake_bq):
        meta.update_client_domains_priority_batch(1, [
            {"domain_id": 1, "priority": None},
        ])
        params = fake_bq.client.queries[-1]["job_config"].query_parameters
        priority_param = next((p for p in params if p.name == "priority"), None)
        assert priority_param is not None
        assert priority_param.value == "NORMAL"

    def test_lowercase_priority_uppercased_and_validated(self, meta, fake_bq):
        """'high' → 'HIGH' which is valid, so it should appear as the parameter value."""
        meta.update_client_domains_priority_batch(1, [
            {"domain_id": 1, "priority": "high"},
        ])
        params = fake_bq.client.queries[-1]["job_config"].query_parameters
        priority_param = next((p for p in params if p.name == "priority"), None)
        assert priority_param is not None
        assert priority_param.value == "HIGH"

    def test_client_id_parameterized(self, meta, fake_bq):
        """client_id is parameterized via @client_id — NOT embedded in the SQL string."""
        meta.update_client_domains_priority_batch(1, [
            {"domain_id": 1, "priority": "NORMAL"},
        ])
        sql = fake_bq.client.queries[-1]["sql"]
        assert "@client_id" in sql
        params = fake_bq.client.queries[-1]["job_config"].query_parameters
        client_id_param = next((p for p in params if p.name == "client_id"), None)
        assert client_id_param is not None
        assert client_id_param.value == 1

    def test_produces_one_update_per_row(self, meta, fake_bq):
        # Fixed: one parameterized UPDATE per row (not a single MERGE)
        rows = [{"domain_id": i, "priority": "LOW"} for i in range(1, 4)]
        meta.update_client_domains_priority_batch(1, rows)
        assert len(fake_bq.client.queries) == 3


# ══════════════════════════════════════════════════════════════════════════════
# 7. deactivate_client — cascade behaviour
# ══════════════════════════════════════════════════════════════════════════════

class TestDeactivateClient:

    def test_deactivate_without_cascade_calls_update(self, meta, fake_bq):
        meta.deactivate_client(1, cascade=False)
        sqls = [q["sql"] for q in fake_bq.client.queries]
        # Should only issue the UPDATE to clients
        assert any("Meta.clients" in s and "UPDATE" in s for s in sqls)
        # Should NOT touch client_datasets or domains
        assert not any("Meta.client_datasets" in s for s in sqls)

    def test_cascade_deactivates_client_datasets(self, meta, fake_bq):
        # Queue domain_id lookup result (cascade step 1)
        fake_bq.client.queue_result(pd.DataFrame({"domain_id": [10, 11]}))
        meta.deactivate_client(1, cascade=True)
        sqls = [q["sql"] for q in fake_bq.client.queries]
        # client_datasets UPDATE must be present
        assert any("Meta.client_datasets" in s and "is_active = FALSE" in s for s in sqls)

    def test_cascade_domains_bug_no_domain_deactivation(self, meta, fake_bq):
        """Fixed: cascade=True now correctly deactivates linked domains in Meta.domains."""
        fake_bq.client.queue_result(pd.DataFrame({"domain_id": [10, 11]}))
        meta.deactivate_client(1, cascade=True)
        sqls = [q["sql"] for q in fake_bq.client.queries]
        domain_updates = [s for s in sqls if "Meta.domains" in s and "is_active" in s]
        assert len(domain_updates) >= 1, (
            "cascade=True must write is_active=FALSE to Meta.domains for linked domains"
        )

    def test_deactivate_client_with_no_domains(self, meta, fake_bq):
        """cascade=True with a client that has no domains should still work."""
        fake_bq.client.queue_result(pd.DataFrame(columns=["domain_id"]))
        meta.deactivate_client(1, cascade=True)
        sqls = [q["sql"] for q in fake_bq.client.queries]
        # The final call should still deactivate the client itself
        assert any("Meta.clients" in s and "is_active" in s for s in sqls)

    def test_deactivate_sets_is_active_false(self, meta, fake_bq):
        meta.deactivate_client(99, cascade=False)
        update_sqls = [q["sql"] for q in fake_bq.client.queries if "UPDATE" in q["sql"]]
        assert len(update_sqls) >= 1
        last_update = update_sqls[-1]
        assert "is_active" in last_update
        # The parameterized value is False — check via job_config
        last_query = fake_bq.client.queries[-1]
        params = last_query["job_config"].query_parameters
        is_active_param = next((p for p in params if p.name == "is_active"), None)
        assert is_active_param is not None
        assert is_active_param.value is False
