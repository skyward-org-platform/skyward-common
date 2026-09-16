import pytest

from skyward.data.dataforseo import DataForSEOClient


@pytest.fixture
def client():
    return DataForSEOClient(username="u", password="p", bq_client=None)


def _p(plan):
    return plan.planned_requests, plan.planned_items, plan.planned_max_rows


def test_backlinks_backlinks(client):
    ep = client.backlinks_backlinks
    assert _p(ep.plan(["a", "b"])) == (40, 40000, 40000)                 # 20 pages x 2 targets
    assert _p(ep.plan(["a"], limit=2500)) == (3, 2500, 2500)
    assert _p(ep.plan(["a"], limit=2500, page_size=500)) == (5, 2500, 2500)
    assert ep.plan(["a"], limit=2500).price_inputs == {"limit": 2500, "page_size": 1000}


def test_backlinks_bulk_pages_summary(client):
    ep = client.backlinks_bulk_pages_summary
    assert _p(ep.plan([f"u{i}" for i in range(2500)])) == (3, 2500, 2500)
    assert _p(ep.plan([f"u{i}" for i in range(2500)], batch_size=500)) == (5, 2500, 2500)


def test_backlinks_summary(client):
    assert _p(client.backlinks_summary.plan(["a", "b", "c"])) == (3, 3, 3)


def test_domain_rank_overview(client):
    assert _p(client.dataforseo_labs_google_domain_rank_overview.plan(["a", "b"])) == (2, 2, 2)


def test_keyword_overview(client):
    ep = client.dataforseo_labs_google_keyword_overview
    assert _p(ep.plan([f"k{i}" for i in range(1500)])) == (3, 1500, 1500)
    assert _p(ep.plan([f"k{i}" for i in range(1500)], batch_size=1000)) == (3, 1500, 1500)


def test_keyword_suggestions_and_related(client):
    assert _p(client.dataforseo_labs_google_keyword_suggestions.plan(["a", "b"])) == (2, 100, 100)
    assert _p(client.dataforseo_labs_google_keyword_suggestions.plan(["a"], limit=10)) == (1, 10, 10)
    assert _p(client.dataforseo_labs_google_related_keywords.plan(["a", "b"])) == (2, 40, 40)


def test_ranked_keywords(client):
    ep = client.dataforseo_labs_google_ranked_keywords
    assert _p(ep.plan(["a.com"])) == (10, 10000, 10000)
    assert _p(ep.plan(["a.com", "b.com"], limit_per_domain=3000, page_size=1000)) == (6, 6000, 6000)
    assert _p(ep.plan(["a.com"], _single_call=True, limit=500)) == (1, 500, 500)


def test_search_intent(client):
    assert _p(client.dataforseo_labs_google_search_intent.plan([f"k{i}" for i in range(2500)])) == (3, 2500, 2500)


def test_search_volume(client):
    ep = client.keywords_data_google_ads_search_volume
    assert _p(ep.plan([f"k{i}" for i in range(2500)])) == (3, 0, 2500)
    assert _p(ep.plan([f"k{i}" for i in range(2500)], endpoint_mode="standard",
                      keywords_per_task=500)) == (5, 0, 2500)


def test_serp(client):
    ep = client.serp_google_organic
    assert _p(ep.plan(["a", "b"])) == (2, 2, 40)
    assert _p(ep.plan(["a", "b"], depth=30)) == (2, 6, 120)
    assert ep.plan(["a"], endpoint_mode="standard", depth=30).endpoint_mode == "standard"
