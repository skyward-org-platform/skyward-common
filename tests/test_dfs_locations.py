import json
from unittest.mock import MagicMock

import pandas as pd
import pytest

from skyward.data.dataforseo import DataForSEOClient
from skyward.data.dataforseo.locations import LOCATION_COLUMNS, merge_location_lists
from tests.conftest import FakeBigQueryClient

SERP = [{"location_code": 2840, "location_name": "United States", "location_code_parent": None,
         "country_iso_code": "US", "location_type": "Country"},
        {"location_code": 200528, "location_name": "Miami-Ft. Lauderdale FL",
         "location_code_parent": 21142, "country_iso_code": "US", "location_type": "DMA Region"}]
ADS = [{"location_code": 2840, "location_name": "United States", "location_code_parent": None,
        "country_iso_code": "US", "location_type": "Country"}]
LABS = [{"location_code": 2840, "location_name": "United States", "location_code_parent": None,
         "country_iso_code": "US", "location_type": "Country",
         "available_languages": [{"language_code": "en"}]}]


def _client(bq=None):
    return DataForSEOClient(username="u", password="p", bq_client=bq)


def test_merge_sets_flags_and_languages():
    df = merge_location_lists({"in_serp": SERP, "in_google_ads": ADS, "in_labs": LABS},
                              pd.Timestamp("2026-09-15", tz="UTC"))
    assert list(df.columns) == LOCATION_COLUMNS
    us = df[df["location_code"] == 2840].iloc[0]
    assert (bool(us["in_serp"]), bool(us["in_google_ads"]), bool(us["in_labs"])) == (True, True, True)
    assert json.loads(us["available_languages"]) == [{"language_code": "en"}]
    dma = df[df["location_code"] == 200528].iloc[0]
    assert (bool(dma["in_serp"]), bool(dma["in_google_ads"]), bool(dma["in_labs"])) == (True, False, False)
    assert dma["location_code_parent"] == 21142


def _stub_lists(client, lists):
    urls = {"serp/google/locations": lists[0], "keywords_data/google_ads/locations": lists[1],
            "dataforseo_labs/locations_and_languages": lists[2]}

    def _get(url, *a, **k):
        for path, rows in urls.items():
            if url.endswith(path):
                return {"tasks": [{"result": rows}]}
        return None

    client._get = _get


def test_refresh_truncates_and_loads():
    bq = FakeBigQueryClient()
    bq.log_upload_event = MagicMock()
    client = _client(bq)
    _stub_lists(client, (SERP, ADS, LABS))
    n = client.refresh_locations()
    assert n == 2
    load = bq.client.loaded_tables[-1]
    assert load["table_ref"] == "data-hub-468216.DataForSEO.locations"
    assert load["job_config"].write_disposition == "WRITE_TRUNCATE"
    bq.log_upload_event.assert_called_once()
    call_kwargs = bq.log_upload_event.call_args[1]
    assert call_kwargs["source"] == "dataforseo"
    assert call_kwargs["source_program"] == "refresh_locations"
    assert call_kwargs["dataset"] == "DataForSEO"
    assert call_kwargs["table"] == "locations"
    assert call_kwargs["row_count"] == 2
    assert call_kwargs["job_id"]
    assert call_kwargs["upload_id"]


def test_refresh_refuses_when_a_list_is_empty():
    bq = FakeBigQueryClient()
    client = _client(bq)
    _stub_lists(client, (SERP, [], LABS))
    with pytest.raises(RuntimeError, match="in_google_ads"):
        client.refresh_locations()
    assert bq.client.loaded_tables == []


def test_is_supported_reads_flags_and_caches():
    bq = FakeBigQueryClient()
    bq.client.queue_result(pd.DataFrame([{"total_rows": 270000, "in_serp": True,
                                          "in_google_ads": True, "in_labs": False}]))
    cat = _client(bq).locations
    assert cat.is_supported(200528, "in_labs") is False
    assert cat.is_supported(200528, "in_serp") is True
    assert len(bq.client.queries) == 1


def test_is_supported_unknown_code_is_false_and_empty_catalog_is_none():
    bq = FakeBigQueryClient()
    bq.client.queue_result(pd.DataFrame([{"total_rows": 10, "in_serp": None,
                                          "in_google_ads": None, "in_labs": None}]))
    bq.client.queue_result(pd.DataFrame([{"total_rows": 0, "in_serp": None,
                                          "in_google_ads": None, "in_labs": None}]))
    cat = _client(bq).locations
    assert cat.is_supported(1, "in_serp") is False
    assert cat.is_supported(2, "in_serp") is None


def test_is_supported_unreadable_is_none():
    cat = _client(FakeBigQueryClient()).locations   # fake returns an empty frame
    assert cat.is_supported(2840, "in_labs") is None
    assert _client(None).locations.is_supported(2840, "in_labs") is None


def test_get_reads_cache_with_filters():
    bq = FakeBigQueryClient()
    bq.client.queue_result(pd.DataFrame([SERP[1]]))
    rows = _client(bq).get_locations(location_type="DMA Region", country_iso_code="US",
                                     supported_by="in_serp")
    assert rows[0]["location_code"] == 200528
    sql = bq.client.queries[0]["sql"]
    assert "location_type = @location_type" in sql
    assert "country_iso_code = @country_iso_code" in sql
    assert "in_serp" in sql


def test_get_falls_back_to_live_serp_list_when_cache_empty():
    client = _client(FakeBigQueryClient())
    client.get_serp_locations = lambda: SERP
    rows = client.get_locations(location_type="DMA Region")
    assert [r["location_code"] for r in rows] == [200528]


def test_get_supported_by_does_not_fall_back_when_cache_empty():
    client = _client(FakeBigQueryClient())
    call_count = [0]
    def mock_get_serp_locations():
        call_count[0] += 1
        raise AssertionError("Should not call get_serp_locations when supported_by is set")
    client.get_serp_locations = mock_get_serp_locations
    rows = client.get_locations(supported_by="in_labs")
    assert rows == []
    assert call_count[0] == 0


def test_get_rejects_unknown_flag():
    with pytest.raises(ValueError):
        _client(FakeBigQueryClient()).get_locations(supported_by="in_bing")


def test_find_location_name_uses_cache():
    bq = FakeBigQueryClient()
    bq.client.queue_result(pd.DataFrame([SERP[0]]))
    assert _client(bq).find_location_name(2840) == "United States"
