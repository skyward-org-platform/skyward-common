import pytest
import pandas as pd
from skyward.data.dataforseo import DataForSEOClient
from skyward.data.dataforseo.endpoints.backlinks_history import BacklinksHistory


@pytest.fixture
def client():
    return DataForSEOClient(username="u", password="p", bq_client=None)


@pytest.fixture
def endpoint(client):
    return client.backlinks_history


def test_endpoint_properties(endpoint):
    assert endpoint.LIVE_URL == "backlinks/history/live"
    assert endpoint.TABLE_NAME == "backlinks-history"
    assert endpoint._get_dedupe_keys() == ["target", "date"]


def test_build_payload(endpoint):
    # Default payload
    payload = endpoint._build_payload("busbank.com")
    assert payload == [{"target": "busbank.com"}]

    # With date_from and date_to
    payload2 = endpoint._build_payload("busbank.com", date_from="2026-01-01", date_to="2026-09-30")
    assert payload2 == [{"target": "busbank.com", "date_from": "2026-01-01", "date_to": "2026-09-30"}]


def test_plan(endpoint):
    plan = endpoint.plan(["domain1.com", "domain2.com"])
    assert plan.planned_requests == 2
    assert plan.planned_items == 2
    assert plan.planned_max_rows == 240


def test_parse_response_with_items(endpoint):
    sample_response = {
        "tasks": [{
            "id": "task_12345",
            "result": [{
                "target": "busbank.com",
                "items": [
                    {
                        "type": "backlinks_history",
                        "date": "2026-01-31 00:00:00 +00:00",
                        "rank": 248,
                        "backlinks": 2298,
                        "new_backlinks": 36,
                        "lost_backlinks": 48,
                        "new_referring_domains": 17,
                        "lost_referring_domains": 29,
                        "crawled_pages": 713,
                        "internal_links_count": 33133,
                        "external_links_count": 4342,
                        "broken_backlinks": 35,
                        "broken_pages": 56,
                        "referring_domains": 724,
                        "referring_domains_nofollow": 159,
                        "referring_main_domains": 700,
                        "referring_main_domains_nofollow": 154,
                        "referring_ips": 425,
                        "referring_subnets": 342,
                        "referring_pages": 2159,
                        "referring_pages_nofollow": 469,
                    },
                    {
                        "type": "backlinks_history",
                        "date": "2026-02-28 00:00:00 +00:00",
                        "rank": 247,
                        "backlinks": 2072,
                        "new_backlinks": 38,
                        "lost_backlinks": 258,
                        "new_referring_domains": 12,
                        "lost_referring_domains": 19,
                        "crawled_pages": 742,
                        "internal_links_count": 35686,
                        "external_links_count": 4894,
                        "broken_backlinks": 36,
                        "broken_pages": 47,
                        "referring_domains": 711,
                        "referring_domains_nofollow": 153,
                        "referring_main_domains": 686,
                        "referring_main_domains_nofollow": 149,
                        "referring_ips": 413,
                        "referring_subnets": 329,
                        "referring_pages": 1935,
                        "referring_pages_nofollow": 461,
                    }
                ]
            }]
        }]
    }

    df = endpoint._parse_response(sample_response, "busbank.com", subject="client")
    assert len(df) == 2
    assert list(df["target"]) == ["busbank.com", "busbank.com"]
    assert list(df["target_type"]) == ["domain", "domain"]
    assert list(df["subject"]) == ["client", "client"]
    assert list(df["task_id"]) == ["task_12345", "task_12345"]
    assert list(df["rank"]) == [248, 247]
    assert list(df["new_referring_domains"]) == [17, 12]

    # Test rival subject
    df_rival = endpoint._parse_response(sample_response, "busbank.com", subject="rival")
    assert list(df_rival["subject"]) == ["rival", "rival"]


def test_target_type_derivation(endpoint):
    sample_response_url = {
        "tasks": [{
            "id": "t1",
            "result": [{
                "target": "busbank.com/charter-bus",
                "items": [{"date": "2026-01-31 00:00:00 +00:00", "backlinks": 10}]
            }]
        }]
    }
    df = endpoint._parse_response(sample_response_url, "busbank.com/charter-bus")
    assert df["target_type"].iloc[0] == "url"


def test_cast_types(endpoint):
    df_raw = pd.DataFrame([{
        "target": "busbank.com",
        "target_type": "domain",
        "subject": "client",
        "date": "2026-01-31 00:00:00 +00:00",
        "rank": "248",
        "backlinks": "2298",
        "new_backlinks": "36",
        "lost_backlinks": "48",
        "new_referring_domains": "17",
        "lost_referring_domains": "29",
        "crawled_pages": "713",
        "internal_links_count": "33133",
        "external_links_count": "4342",
        "broken_backlinks": "35",
        "broken_pages": "56",
        "referring_domains": "724",
        "referring_domains_nofollow": "159",
        "referring_main_domains": "700",
        "referring_main_domains_nofollow": "154",
        "referring_ips": "425",
        "referring_subnets": "342",
        "referring_pages": "2159",
        "referring_pages_nofollow": "469",
    }])
    casted = endpoint._cast_types(df_raw)
    assert casted["rank"].dtype.name == "Int64"
    assert casted["backlinks"].dtype.name == "Int64"
    assert pd.api.types.is_datetime64_any_dtype(casted["date"])


def test_empty_response(endpoint):
    df = endpoint._parse_response({}, "busbank.com")
    assert df.empty
    assert "task_id" in df.columns


def test_valid_empty_history_does_not_retry(client, endpoint):
    calls = []

    def fake_post(url, payload, max_retries=1, retry_delay=0):
        calls.append((url, payload))
        return {
            "status_code": 20000,
            "tasks": [{
                "id": f"task_{len(calls)}",
                "status_code": 20000,
                "cost": 0.02,
                "data": payload[0],
                "result": [{
                    "target": payload[0]["target"],
                    "items_count": 0,
                    "items": None,
                }],
            }],
        }

    client._post = fake_post
    df = endpoint._fetch_live("nodata-domain.com", max_retries=5)
    assert df.empty
    assert "task_id" in df.columns
    # Must only be called once — no retry when DataForSEO returns a valid empty result
    assert len(calls) == 1


def test_failed_task_retries_history(client, endpoint):
    calls = []

    def fake_post(url, payload, max_retries=1, retry_delay=0):
        calls.append((url, payload))
        if len(calls) < 3:
            return {
                "status_code": 20000,
                "tasks": [{
                    "id": f"task_{len(calls)}",
                    "status_code": 50000,
                    "status_message": "Internal error",
                    "cost": 0.0,
                    "data": payload[0],
                    "result": None,
                }],
            }
        return {
            "status_code": 20000,
            "tasks": [{
                "id": f"task_{len(calls)}",
                "status_code": 20000,
                "cost": 0.02,
                "data": payload[0],
                "result": [{
                    "target": payload[0]["target"],
                    "items": [{"date": "2026-01-31 00:00:00 +00:00", "backlinks": 5}],
                }],
            }],
        }

    client._post = fake_post
    df = endpoint._fetch_live("eventual-domain.com", max_retries=5, retry_delay=0)
    assert len(df) == 1
    assert len(calls) == 3

