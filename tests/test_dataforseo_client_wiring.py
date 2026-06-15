from unittest.mock import patch

import pytest

from skyward.data.dataforseo import DataForSEOClient, ClientConfig


def test_meta_client_none_without_supabase_url():
    """v1.5.0: meta_client is None when SUPABASE_DB_URL is unset — independent of bq_client."""
    client = DataForSEOClient(username="u", password="p")
    with patch("skyward.config.load_config") as lc:
        lc.return_value.supabase_db_url = None
        assert client.meta_client is None


def test_bq_client_assignment_does_not_drive_meta(fake_bq):
    """bq_client is still stored (for uploads), but meta_client no longer derives from it."""
    client = DataForSEOClient(username="u", password="p", bq_client=fake_bq)
    assert client.bq_client is fake_bq
    # meta_client comes from Supabase now; with no URL it's None regardless of bq_client
    with patch("skyward.config.load_config") as lc:
        lc.return_value.supabase_db_url = None
        assert client.meta_client is None


def test_client_missing_credentials_raises():
    with pytest.raises(RuntimeError, match="Missing DataForSEO credentials"):
        DataForSEOClient(username="", password="")


def test_client_respects_custom_config():
    cfg = ClientConfig(location_code=2036, debug=True)
    client = DataForSEOClient(username="u", password="p", config=cfg)
    assert client.config.location_code == 2036
    assert client.config.debug is True
