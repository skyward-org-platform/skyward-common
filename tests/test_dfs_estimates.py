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
