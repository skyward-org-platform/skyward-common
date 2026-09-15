"""DataForSEO list-price rate card (v1.6.1).

Each Rate prices one endpoint + mode as

    cost = planned_requests * price_per_request_usd + planned_items * price_per_item_usd

where "items" are counted in `item_unit`. These constants seed the
`DataForSEO.cost_estimates` table (scripts/seed_dfs_cost_estimates.py); the estimator
reads that table and falls back to these values when it can't.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

VERIFIED_ON = "2026-09-15"
DEFAULT_MAX_BUFFER = 0.10

LABS_URL = "https://dataforseo.com/pricing/dataforseo-labs/dataforseo-google-api"
BACKLINKS_URL = "https://dataforseo.com/pricing/backlinks/backlinks"
SERP_URL = "https://dataforseo.com/pricing/serp/google-organic-serp-api"
ADS_URL = "https://dataforseo.com/pricing/keywords-data/google-ads"


@dataclass(frozen=True)
class Rate:
    endpoint: str
    endpoint_mode: str
    price_per_request_usd: float
    price_per_item_usd: float
    item_unit: str  # "result_row" | "keyword_sent" | "serp_page" | "none"
    max_items_per_request: int | None
    source_url: str
    verified_on: str = VERIFIED_ON
    max_buffer: float = DEFAULT_MAX_BUFFER
    notes: str = ""

    def to_row(self) -> dict:
        return asdict(self)


def _labs(endpoint: str, max_items: int) -> Rate:
    return Rate(endpoint, "live", 0.012, 0.00012, "result_row", max_items, LABS_URL)


def _backlinks(endpoint: str, max_items: int) -> Rate:
    return Rate(endpoint, "live", 0.024, 0.000036, "result_row", max_items, BACKLINKS_URL)


DEFAULT_RATES: tuple[Rate, ...] = (
    _backlinks("backlinks_backlinks", 1000),
    _backlinks("backlinks_bulk_pages_summary", 1000),
    _backlinks("backlinks_summary", 1),
    _labs("dataforseo_labs_google_domain_rank_overview", 1),
    _labs("dataforseo_labs_google_keyword_overview", 700),
    _labs("dataforseo_labs_google_keyword_suggestions", 1000),
    _labs("dataforseo_labs_google_ranked_keywords", 1000),
    _labs("dataforseo_labs_google_related_keywords", 1000),
    Rate("dataforseo_labs_google_search_intent", "live", 0.0012, 0.00012, "keyword_sent",
         1000, LABS_URL, notes="Billed per task plus per keyword sent."),
    Rate("keywords_data_google_ads_search_volume", "live", 0.09, 0.0, "none",
         1000, ADS_URL, notes="Flat per task of up to 1,000 keywords."),
    Rate("keywords_data_google_ads_search_volume", "standard", 0.06, 0.0, "none",
         1000, ADS_URL, notes="Flat per task of up to 1,000 keywords."),
    Rate("serp_google_organic", "live", 0.0, 0.002, "serp_page",
         None, SERP_URL, notes="Per 10 results of depth."),
    Rate("serp_google_organic", "standard", 0.0, 0.0006, "serp_page",
         None, SERP_URL, notes="Per 10 results of depth, normal priority."),
)


def rates_by_key(rates: tuple[Rate, ...] = DEFAULT_RATES) -> dict[tuple[str, str], Rate]:
    return {(r.endpoint, r.endpoint_mode): r for r in rates}
