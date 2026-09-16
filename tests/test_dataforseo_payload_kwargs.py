"""Every kwarg an endpoint advertises must reach the outbound payload (ClickUp 86bad989k)."""

import pytest

from skyward.data.dataforseo import ClientConfig, DataForSEOClient


@pytest.fixture
def client():
    return DataForSEOClient(username="u", password="p", bq_client=None,
                            config=ClientConfig(language_code="en", location_code=2840))


@pytest.mark.parametrize("prop, target", [
    ("dataforseo_labs_google_domain_rank_overview", "a.com"),
    ("dataforseo_labs_google_keyword_overview", ["a"]),
    ("dataforseo_labs_google_keyword_suggestions", "a"),
    ("dataforseo_labs_google_related_keywords", "a"),
    ("dataforseo_labs_google_ranked_keywords", "a.com"),
    ("keywords_data_google_ads_search_volume", ["a"]),
    ("serp_google_organic", "a"),
])
def test_location_and_language_reach_payload(client, prop, target):
    payload = getattr(client, prop)._build_payload(target, location_code=1234, language_code="es")[0]
    assert payload["location_code"] == 1234
    assert payload["language_code"] == "es"
    assert "language_name" not in payload


def test_search_intent_language(client):
    payload = client.dataforseo_labs_google_search_intent._build_payload(["a"], language_code="es")[0]
    assert payload["language_code"] == "es"


def test_search_volume_defaults_to_config_language(client):
    payload = client.keywords_data_google_ads_search_volume._build_payload(["a"])[0]
    assert payload["language_code"] == "en"


def test_search_volume_language_name_still_supported(client):
    payload = client.keywords_data_google_ads_search_volume._build_payload(["a"], language_name="Spanish")[0]
    assert payload["language_name"] == "Spanish" and "language_code" not in payload


def test_search_volume_task_post_sends_language_code(client):
    sent = {}

    def _post(url, payload, **kw):
        sent["payload"] = payload
        return {"tasks": [{"id": "t1"}]}

    client._post = _post
    assert client.keywords_data_google_ads_search_volume._task_post(
        ["a"], location_code=1, language_code="es") == ["t1"]
    assert sent["payload"][0]["language_code"] == "es"
    assert sent["payload"][0]["location_code"] == 1


def test_other_advertised_kwargs(client):
    bb = client.backlinks_backlinks._build_payload("a", limit=5, offset=10, filters=[["x", "=", 1]])[0]
    assert (bb["limit"], bb["offset"], bb["filters"]) == (5, 10, [["x", "=", 1]])
    bs = client.backlinks_summary._build_payload("a", internal_list_limit=3,
                                                 include_subdomains=False,
                                                 backlinks_status_type="all")[0]
    assert (bs["internal_list_limit"], bs["include_subdomains"], bs["backlinks_status_type"]) == (3, False, "all")
    rk = client.dataforseo_labs_google_related_keywords._build_payload("a", depth=2, limit=7)[0]
    assert (rk["depth"], rk["limit"]) == (2, 7)
    ks = client.dataforseo_labs_google_keyword_suggestions._build_payload("a", limit=9)[0]
    assert ks["limit"] == 9
    rkw = client.dataforseo_labs_google_ranked_keywords._build_payload("a", limit=11, offset=22)[0]
    assert (rkw["limit"], rkw["offset"]) == (11, 22)
    serp = client.serp_google_organic._build_payload("a", depth=30)[0]
    assert serp["depth"] == 30
