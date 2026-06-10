"""Regression tests for the v1.5.0 DataForSEO->Meta Supabase bug.

The DFS client must resolve domains via the Supabase-backed MetaClient, never via
BigQuery Meta (bug: meta_client was built from bq_client; the domain_id path read
`{project}.Meta.domains` over BigQuery).
"""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from skyward.data.dataforseo.client import DataForSEOClient
from skyward.data.dataforseo.base import BaseEndpoint, _UNSET
from skyward.data.meta import MetaClient
from tests.conftest_pg import requires_pg


def _client():
    return DataForSEOClient(username="u", password="p")


def test_meta_client_built_from_supabase():
    """meta_client wraps a SupabaseClient built from SUPABASE_DB_URL — not bq_client."""
    c = _client()
    fake_sb = object()
    with patch("skyward.config.load_config") as lc, \
         patch("skyward.data.supabase.SupabaseClient", return_value=fake_sb) as SB:
        lc.return_value.supabase_db_url = "postgresql://u:p@h:6543/postgres"
        meta = c.meta_client
    assert isinstance(meta, MetaClient)
    assert meta.sb is fake_sb          # built from Supabase, stored as .sb
    SB.assert_called_once_with("postgresql://u:p@h:6543/postgres")


def test_meta_client_none_when_no_url():
    c = _client()
    with patch("skyward.config.load_config") as lc:
        lc.return_value.supabase_db_url = None
        assert c.meta_client is None


class _Ep(BaseEndpoint):
    LIVE_URL = "test/ep"
    def _build_payload(self, target, **k): return []
    def _parse_response(self, response, target): return pd.DataFrame()
    def _get_schema(self): return []
    def _get_dedupe_keys(self): return []
    def _cast_types(self, df): return df
    def _fetch_live(self, target, **k): return pd.DataFrame()


def test_resolve_domain_by_id_uses_supabase_metaclient():
    meta = MagicMock()
    meta.get_domain_by_id.return_value = {"domain_id": 29, "domain": "thedentalshop.com"}
    ep = _Ep(MagicMock(meta_client=meta))
    out = ep._resolve_domain(_UNSET, 29, False)
    assert out == {"domain_id": 29, "domain": "thedentalshop.com"}
    meta.get_domain_by_id.assert_called_once_with(29)


def test_resolve_domain_by_id_missing_raises():
    meta = MagicMock()
    meta.get_domain_by_id.return_value = None
    ep = _Ep(MagicMock(meta_client=meta))
    with pytest.raises(ValueError, match="not found in meta.domains"):
        ep._resolve_domain(_UNSET, 99999, False)


def test_no_bigquery_meta_reference_in_dataforseo():
    """Guard: no backtick-wrapped BigQuery Meta table reference under dataforseo/."""
    root = Path(__file__).resolve().parent.parent / "src" / "skyward" / "data" / "dataforseo"
    offenders = []
    for py in root.rglob("*.py"):
        text = py.read_text()
        if "Meta.domains`" in text or "._project_id}.Meta" in text or "MetaClient(self.bq" in text:
            offenders.append(py.name)
    assert not offenders, f"BigQuery Meta references still present in: {offenders}"


@requires_pg
def test_get_domain_by_id_live(pg_client):
    meta = MetaClient(pg_client)
    rows = pg_client.execute(
        "insert into meta.domains (domain, domain_name) values ('zzdfs.com','ZZ DFS') returning domain_id"
    )
    did = int(rows[0][0])
    got = meta.get_domain_by_id(did)
    assert got["domain"] == "zzdfs.com" and got["domain_id"] == did
    assert meta.get_domain_by_id(987654) is None
