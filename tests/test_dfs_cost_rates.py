from skyward.data.dataforseo.cost_rates import DEFAULT_RATES, rates_by_key

ENDPOINTS = {
    "backlinks_backlinks", "backlinks_bulk_pages_summary", "backlinks_summary",
    "dataforseo_labs_google_domain_rank_overview", "dataforseo_labs_google_keyword_overview",
    "dataforseo_labs_google_keyword_suggestions", "dataforseo_labs_google_ranked_keywords",
    "dataforseo_labs_google_related_keywords", "dataforseo_labs_google_search_intent",
    "keywords_data_google_ads_search_volume", "serp_google_organic",
}


def test_every_endpoint_has_a_live_rate():
    keys = rates_by_key()
    for ep in ENDPOINTS:
        assert (ep, "live") in keys, ep


def test_standard_rates_exist_for_standard_endpoints():
    keys = rates_by_key()
    assert ("serp_google_organic", "standard") in keys
    assert ("keywords_data_google_ads_search_volume", "standard") in keys


def test_rates_carry_source_and_verified_date():
    for r in DEFAULT_RATES:
        assert r.source_url.startswith("https://dataforseo.com/pricing/")
        assert r.verified_on == "2026-09-15"
        assert r.max_buffer == 0.10


def test_verified_list_prices():
    k = rates_by_key()
    labs = k[("dataforseo_labs_google_ranked_keywords", "live")]
    assert (labs.price_per_request_usd, labs.price_per_item_usd) == (0.012, 0.00012)
    bl = k[("backlinks_backlinks", "live")]
    assert (bl.price_per_request_usd, bl.price_per_item_usd) == (0.024, 0.000036)
    assert k[("serp_google_organic", "live")].price_per_item_usd == 0.002
    assert k[("serp_google_organic", "standard")].price_per_item_usd == 0.0006
    assert k[("keywords_data_google_ads_search_volume", "live")].price_per_request_usd == 0.09
    assert k[("keywords_data_google_ads_search_volume", "standard")].price_per_request_usd == 0.06
    si = k[("dataforseo_labs_google_search_intent", "live")]
    assert (si.price_per_request_usd, si.item_unit) == (0.0012, "keyword_sent")


def test_to_row_round_trips_fields():
    row = DEFAULT_RATES[0].to_row()
    assert set(row) == {
        "endpoint", "endpoint_mode", "price_per_request_usd", "price_per_item_usd",
        "item_unit", "max_items_per_request", "source_url", "verified_on",
        "max_buffer", "notes",
    }
