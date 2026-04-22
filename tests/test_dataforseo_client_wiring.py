import pytest

from skyward.data.dataforseo import DataForSEOClient, ClientConfig


def test_client_accepts_no_bq_client_and_meta_is_none():
    client = DataForSEOClient(username="u", password="p")
    assert client.bq_client is None
    assert client.meta_client is None


def test_client_with_bq_client_constructs_meta_client(fake_bq):
    client = DataForSEOClient(username="u", password="p", bq_client=fake_bq)
    assert client.bq_client is fake_bq
    meta = client.meta_client
    assert meta is not None
    assert meta.bq is fake_bq


def test_client_missing_credentials_raises():
    with pytest.raises(RuntimeError, match="Missing DataForSEO credentials"):
        DataForSEOClient(username="", password="")


def test_client_respects_custom_config():
    cfg = ClientConfig(location_code=2036, debug=True)
    client = DataForSEOClient(username="u", password="p", config=cfg)
    assert client.config.location_code == 2036
    assert client.config.debug is True
