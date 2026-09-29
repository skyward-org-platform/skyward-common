import pandas as pd

from skyward.data.dataforseo import DataForSEOClient
from skyward.data.dataforseo.cost_rates import rates_by_key
from skyward.data.dataforseo.estimates import (
    CostEstimator, RunPlan, list_price_usd, price_plan,
)
from tests.conftest import FakeBigQueryClient


def _plan(**kw):
    base = dict(endpoint="dataforseo_labs_google_ranked_keywords", endpoint_mode="live",
                planned_requests=3, planned_items=3000, planned_max_rows=3000)
    base.update(kw)
    return RunPlan(**base)


def test_list_price_is_requests_plus_items():
    rate = rates_by_key()[("dataforseo_labs_google_ranked_keywords", "live")]
    assert round(list_price_usd(_plan(), rate), 6) == round(3 * 0.012 + 3000 * 0.00012, 6)


def test_max_adds_ten_percent_and_avg_uses_list_price_without_observations():
    rate = rates_by_key()[("dataforseo_labs_google_ranked_keywords", "live")]
    est = price_plan(_plan(), rate)
    assert est.list_price_usd == 0.396
    assert est.max_usd == round(0.396 * 1.1, 6)
    assert est.avg_usd == 0.396
    assert est.basis == "list_price"


def test_avg_uses_observed_when_enough_samples():
    rate = rates_by_key()[("dataforseo_labs_google_ranked_keywords", "live")]
    est = price_plan(_plan(), rate, {"sample_requests": 50, "avg_cost_per_request": 0.05})
    assert est.basis == "observed"
    assert est.avg_usd == 0.15


def test_observed_ignored_below_minimum_samples():
    rate = rates_by_key()[("dataforseo_labs_google_ranked_keywords", "live")]
    est = price_plan(_plan(), rate, {"sample_requests": 3, "avg_cost_per_request": 0.05})
    assert est.basis == "list_price"


def _serp_plan(keywords: int, depth: int, mode: str = "standard") -> RunPlan:
    pages = -(-depth // 10)
    return RunPlan(endpoint="serp_google_organic", endpoint_mode=mode,
                   planned_requests=keywords, planned_items=keywords * pages,
                   planned_max_rows=keywords * depth)


# Observed standard-mode SERP spend at one (unknown) depth, as endpoint_cost_actuals
# reports it: one average per request, with no record of how many pages each one billed.
_SERP_OBSERVED = {"sample_requests": 1337, "avg_cost_per_request": 0.001887061}


def test_serp_avg_rises_with_depth_even_with_observations():
    rate = rates_by_key()[("serp_google_organic", "standard")]
    avgs = [price_plan(_serp_plan(1527, d), rate, _SERP_OBSERVED).avg_usd
            for d in (10, 30, 50)]
    assert avgs[0] < avgs[1] < avgs[2]


def test_serp_avg_never_exceeds_max():
    for mode in ("standard", "live"):
        rate = rates_by_key()[("serp_google_organic", mode)]
        for depth in (10, 30, 50, 100):
            est = price_plan(_serp_plan(1527, depth, mode), rate, _SERP_OBSERVED)
            assert est.avg_usd <= est.max_usd, (mode, depth, est)


def test_serp_avg_is_the_page_list_price():
    """SERP bills a fixed price per page of depth, so the list price already scales with
    the plan; a per-request average measured at some other depth does not."""
    rate = rates_by_key()[("serp_google_organic", "standard")]
    est = price_plan(_serp_plan(1527, 30), rate, _SERP_OBSERVED)
    assert est.basis == "list_price"
    assert est.avg_usd == round(1527 * 3 * 0.0006, 6)


def test_observed_avg_above_list_buffer_lifts_max_to_match():
    """An observed average above the buffered list price means the rate card is low; the
    upper bound follows the evidence rather than sitting under the expected figure."""
    rate = rates_by_key()[("dataforseo_labs_google_ranked_keywords", "live")]
    est = price_plan(_plan(), rate, {"sample_requests": 50, "avg_cost_per_request": 1.0})
    assert est.avg_usd == 3.0
    assert est.max_usd >= est.avg_usd


def test_estimator_falls_back_to_packaged_rates_when_table_empty():
    client = DataForSEOClient(username="u", password="p", bq_client=FakeBigQueryClient())
    est = CostEstimator(client).estimate(_plan())
    assert est.basis == "list_price"
    assert est.max_usd > 0


def test_estimator_prefers_table_rates():
    bq = FakeBigQueryClient()
    row = rates_by_key()[("dataforseo_labs_google_ranked_keywords", "live")].to_row()
    row["price_per_request_usd"] = 1.0
    bq.client.queue_result(pd.DataFrame([row]))      # cost_estimates read
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    est = CostEstimator(client).estimate(_plan(planned_items=0))
    assert est.list_price_usd == 3.0


def test_estimator_unknown_endpoint_returns_zero_no_rate():
    client = DataForSEOClient(username="u", password="p", bq_client=None)
    est = CostEstimator(client).estimate(_plan(endpoint="fake_endpoint"))
    assert (est.max_usd, est.avg_usd, est.basis) == (0.0, 0.0, "no_rate")


def test_estimator_caches_table_reads():
    bq = FakeBigQueryClient()
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    est = CostEstimator(client)
    est.estimate(_plan())
    est.estimate(_plan())
    assert len(bq.client.queries) == 2  # one rates read + one observed read, not repeated


def test_null_numeric_field_in_rate_table_falls_back_to_packaged_rate(caplog):
    # DataForSEO.cost_estimates is hand-maintained; the DDL only requires endpoint and
    # endpoint_mode to be NOT NULL. A null in price_per_request_usd/price_per_item_usd/
    # max_buffer used to construct a Rate with None and blow up later in price_plan()'s
    # arithmetic, killing the run before it spent anything. The bad row must be skipped
    # (packaged default kept) with a warning, not raise.
    bq = FakeBigQueryClient()
    row = rates_by_key()[("dataforseo_labs_google_ranked_keywords", "live")].to_row()
    row["max_buffer"] = None
    bq.client.queue_result(pd.DataFrame([row]))      # cost_estimates read
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    with caplog.at_level("WARNING"):
        est = CostEstimator(client).estimate(_plan())
    assert est.basis == "list_price"
    assert est.max_usd > 0   # packaged default rate was used, not the broken row
    assert any("null" in r.message and "max_buffer" in r.message for r in caplog.records)


def test_null_price_per_item_in_rate_table_is_skipped_not_raised(caplog):
    bq = FakeBigQueryClient()
    row = rates_by_key()[("dataforseo_labs_google_ranked_keywords", "live")].to_row()
    row["price_per_item_usd"] = None
    bq.client.queue_result(pd.DataFrame([row]))
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    with caplog.at_level("WARNING"):
        est = CostEstimator(client).estimate(_plan())
    assert est.max_usd > 0
    assert any("price_per_item_usd" in r.message for r in caplog.records)


def test_null_max_items_per_request_is_allowed_not_flagged():
    # max_items_per_request legitimately means "no cap" (e.g. serp_google_organic in the
    # packaged rates) and must not be treated as a broken row.
    bq = FakeBigQueryClient()
    row = rates_by_key()[("dataforseo_labs_google_ranked_keywords", "live")].to_row()
    row["max_items_per_request"] = None
    bq.client.queue_result(pd.DataFrame([row]))
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    estimator = CostEstimator(client)
    est = estimator.estimate(_plan())
    assert est.basis == "list_price"
    rate = estimator.rates()[("dataforseo_labs_google_ranked_keywords", "live")]
    assert rate.max_items_per_request is None   # row was accepted, not skipped
