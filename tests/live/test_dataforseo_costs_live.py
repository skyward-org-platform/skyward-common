"""Live v1.6.1 cost-tracking tests. Real API + real BigQuery; costs money.

Run: uv run pytest tests/live/test_dataforseo_costs_live.py -m live --run-live -s
Budget: every test asserts its max estimate is under $2; the whole file is about $3.
"""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from skyward.data.dataforseo import InsufficientBalanceError, InvalidLocationError
from skyward.functions import generate_job_id

SERVICES = ["plumber", "electrician", "roofer", "hvac repair", "locksmith", "dentist",
            "chiropractor", "lawyer", "accountant", "real estate agent", "pest control",
            "moving company", "house cleaning", "landscaping", "pool cleaning", "auto repair",
            "car wash", "tow truck", "dog grooming", "veterinarian", "hair salon", "barber shop",
            "nail salon", "massage", "yoga studio", "gym", "pizza delivery", "sushi restaurant",
            "coffee shop", "bakery", "florist", "wedding venue", "photographer", "tattoo shop",
            "daycare", "tutoring", "driving school", "storage units", "junk removal",
            "window cleaning", "gutter cleaning", "carpet cleaning", "appliance repair",
            "garage door repair", "solar installation", "fence installation",
            "painting contractor", "handyman", "charter bus", "limo service"]
CITIES = ["new york", "los angeles", "chicago", "houston", "phoenix", "philadelphia",
          "san antonio", "san diego", "dallas", "austin", "jacksonville", "fort worth",
          "columbus", "charlotte", "indianapolis", "seattle", "denver", "boston", "nashville",
          "detroit", "portland", "las vegas", "memphis", "louisville", "baltimore", "milwaukee",
          "albuquerque", "tucson", "fresno", "sacramento", "kansas city", "atlanta", "miami",
          "raleigh", "omaha", "minneapolis", "tulsa", "tampa", "new orleans", "cleveland",
          "pittsburgh", "cincinnati", "orlando", "st louis", "salt lake city", "richmond",
          "boise", "spokane", "des moines", "madison"]
PER_TEST_MAX_USD = 2.0


def _keywords(n: int) -> list[str]:
    return [f"{s} {c}" for s in SERVICES for c in CITIES][:n]


def _estimate(ep, targets, **kw):
    est = ep.estimate_cost(targets, **kw)
    print(f"\n[{ep.ENDPOINT_KEY}] estimate max=${est.max_usd:.4f} avg=${est.avg_usd:.4f} "
          f"({est.basis}) requests={est.plan.planned_requests}")
    assert est.max_usd < PER_TEST_MAX_USD, "over the per-test budget; ask Adam first"
    return est


def _costs(client, job_id: str) -> pd.DataFrame:
    from google.cloud import bigquery

    bq = client.bq_client
    sql = (f"SELECT * FROM `{bq.client.project}.DataForSEO.cost_log` WHERE job_id = @job_id")
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("job_id", "STRING", job_id)])
    return bq.client.query(sql, job_config=cfg).result().to_dataframe()


def _data_upload_ids(client, table: str, job_id: str) -> set:
    from google.cloud import bigquery

    bq = client.bq_client
    sql = (f"SELECT DISTINCT upload_id FROM `{bq.client.project}.DataForSEO.{table}` "
           f"WHERE job_id = @job_id")
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("job_id", "STRING", job_id)])
    return set(bq.client.query(sql, job_config=cfg).result().to_dataframe()["upload_id"])


def _report(est, costs):
    actual = float(costs["cost_usd"].astype(float).sum())
    print(f"  actual=${actual:.4f} over {len(costs)} billed tasks "
          f"(max estimate ${est.max_usd:.4f})")
    assert actual > 0
    assert actual <= est.max_usd * 1.01, "actual exceeded the worst-case estimate"
    return actual


def _report_logged_only(est, costs):
    """Like `_report`, but without the "actual <= max estimate" bound.

    Documented limitation (docs/v1.6.1-release-notes.md, "Known limits"):
    backlinks_bulk_pages_summary's recursive split-and-retry fallback for batches with
    URLs DataForSEO can't parse makes real billed calls outside the plan, so its actual
    cost is not bounded by the estimate the way every other endpoint's is. We only
    assert the spend was logged.
    """
    actual = float(costs["cost_usd"].astype(float).sum())
    print(f"  actual=${actual:.4f} over {len(costs)} billed tasks "
          f"(max estimate ${est.max_usd:.4f}) -- known limit: invalid-URL retry "
          "splitting is not included in the estimate, so no upper-bound check here")
    assert not costs.empty and actual > 0
    return actual


@pytest.mark.live
def test_keyword_overview_windows_link_cost_to_data(dfs_client_live):
    ep = dfs_client_live.dataforseo_labs_google_keyword_overview
    kws = _keywords(2500)
    est = _estimate(ep, kws)
    job = generate_job_id()
    df = asyncio.run(ep.live_all(kws, domain=None, job_id=job, batch_delay=0,
                                 upload_batch_rows=500))
    costs = _costs(dfs_client_live, job)
    _report(est, costs)
    assert len(costs) >= 4
    assert set(costs["upload_id"]) <= _data_upload_ids(dfs_client_live, ep.TABLE_NAME, job)
    progress = dfs_client_live.get_job_progress(job)
    assert progress["status"].iloc[0] == "completed"
    assert float(progress["pct_complete"].iloc[0]) == 1.0
    assert len(df) > 0


@pytest.mark.live
def test_search_intent_upload_false(dfs_client_live):
    ep = dfs_client_live.dataforseo_labs_google_search_intent
    kws = _keywords(2500)
    est = _estimate(ep, kws)
    job = generate_job_id()
    asyncio.run(ep.live_all(kws, domain=None, job_id=job, batch_delay=0, upload=False))
    costs = _costs(dfs_client_live, job)
    _report(est, costs)
    assert costs["upload_id"].isna().all()


@pytest.mark.live
def test_concurrent_jobs_never_mix(dfs_client_live):
    sugg = dfs_client_live.dataforseo_labs_google_keyword_suggestions
    rel = dfs_client_live.dataforseo_labs_google_related_keywords
    seeds = ["charter bus", "party bus", "shuttle service", "coach bus rental", "bus rental"]
    _estimate(sugg, seeds, limit=20)
    _estimate(rel, seeds, limit=20)
    job_a, job_b = generate_job_id(), generate_job_id()

    async def both():
        await asyncio.gather(
            sugg.live_all(seeds, domain=None, job_id=job_a, batch_size=5, batch_delay=0,
                          upload=False, limit=20),
            rel.live_all(seeds, domain=None, job_id=job_b, batch_size=5, batch_delay=0,
                         upload=False, limit=20),
        )

    asyncio.run(both())
    a, b = _costs(dfs_client_live, job_a), _costs(dfs_client_live, job_b)
    assert set(a["endpoint"]) == {"dataforseo_labs_google_keyword_suggestions"}
    assert set(b["endpoint"]) == {"dataforseo_labs_google_related_keywords"}
    assert len(a) >= 5 and len(b) >= 5


@pytest.mark.live
def test_same_endpoint_twice_under_one_job(dfs_client_live):
    ep = dfs_client_live.dataforseo_labs_google_domain_rank_overview
    job = generate_job_id()
    _estimate(ep, ["busbank.com", "gotobus.com"])
    ep.live("busbank.com", domain=None, job_id=job, upload=False)
    ep.live("gotobus.com", domain=None, job_id=job, upload=False)
    assert len(_costs(dfs_client_live, job)) == 2
    progress = dfs_client_live.get_job_progress(job)
    assert int(progress["planned_requests"].iloc[0]) == 2


@pytest.mark.live
def test_ranked_keywords_pagination(dfs_client_live):
    ep = dfs_client_live.dataforseo_labs_google_ranked_keywords
    est = _estimate(ep, ["busbank.com"], limit_per_domain=3000, page_size=1000)
    job = generate_job_id()
    asyncio.run(ep.live_all(["busbank.com"], domain=None, job_id=job, upload=False,
                            limit_per_domain=3000, page_size=1000))
    costs = _costs(dfs_client_live, job)
    _report(est, costs)
    assert set(costs["call_type"]) == {"live_page"}


@pytest.mark.live
def test_backlinks_over_1k_and_bulk_summary(dfs_client_live):
    bl = dfs_client_live.backlinks_backlinks
    est = _estimate(bl, ["nytimes.com"], limit=2500)
    job = generate_job_id()
    df = bl.live("nytimes.com", domain=None, job_id=job, upload=False, limit=2500)
    assert len(df) > 1000, "backlinks pagination did not pass 1,000 rows (ClickUp 86b9nzvbq)"
    assert not df.duplicated(subset=bl._get_dedupe_keys()).any()
    costs = _costs(dfs_client_live, job)
    _report(est, costs)
    assert len(costs) == 3

    bulk = dfs_client_live.backlinks_bulk_pages_summary
    urls = [u for u in df["url_from"].dropna().unique().tolist() if u.startswith("http")][:1000]
    urls += ["not a url", "https://(not set)"]
    est2 = _estimate(bulk, urls)
    job2 = generate_job_id()
    asyncio.run(bulk.live_all(urls, domain=None, job_id=job2, upload=False, batch_delay=0))
    _report_logged_only(est2, _costs(dfs_client_live, job2))


@pytest.mark.live
def test_backlinks_summary(dfs_client_live):
    summ = dfs_client_live.backlinks_summary
    targets = ["busbank.com", "gotobus.com", "coachusa.com", "greyhound.com", "megabus.com"]
    est = _estimate(summ, targets)
    job = generate_job_id()
    asyncio.run(summ.live_all(targets, domain=None, job_id=job, upload=False, batch_delay=0))
    _report(est, _costs(dfs_client_live, job))


@pytest.mark.live
def test_serp_live_depth_pricing(dfs_client_live):
    ep = dfs_client_live.serp_google_organic
    kws = _keywords(20)
    job = generate_job_id()
    est10 = _estimate(ep, kws, depth=10)
    est30 = _estimate(ep, kws, depth=30)
    asyncio.run(ep.live_all(kws, domain=None, job_id=job, upload=False, depth=10, batch_delay=0))
    asyncio.run(ep.live_all(kws, domain=None, job_id=job, upload=False, depth=30, batch_delay=0))
    actual = float(_costs(dfs_client_live, job)["cost_usd"].astype(float).sum())
    print(f"  serp live actual=${actual:.4f} vs list ${est10.list_price_usd + est30.list_price_usd:.4f}")
    assert actual <= (est10.max_usd + est30.max_usd) * 1.01


@pytest.mark.live
def test_serp_standard_counts_task_post_once(dfs_client_live):
    ep = dfs_client_live.serp_google_organic
    kws = _keywords(10)
    est = _estimate(ep, kws, endpoint_mode="standard")
    job = generate_job_id()
    ep.post(kws, domain=None, job_id=job, upload=False, max_wait=900)
    costs = _costs(dfs_client_live, job)
    assert set(costs["call_type"]) == {"task_post"} and len(costs) == 10
    _report(est, costs)


@pytest.mark.live
def test_serp_collector_submit_cost(dfs_client_live):
    ep = dfs_client_live.serp_google_organic
    kws = _keywords(300)
    est = _estimate(ep, kws, endpoint_mode="standard")
    job = generate_job_id()
    asyncio.run(ep.post_all(kws, domain=None, job_id=job, use_collector=True, proceed_at_pct=0.0))
    costs = _costs(dfs_client_live, job)
    assert len(costs) == 300 and costs["upload_id"].isna().all()
    _report(est, costs)


@pytest.mark.live
def test_search_volume_language_and_standard(dfs_client_live):
    ep = dfs_client_live.keywords_data_google_ads_search_volume
    kws = _keywords(2000)
    est = _estimate(ep, kws)
    job = generate_job_id()
    asyncio.run(ep.live_all(kws, domain=None, job_id=job, upload=False, batch_delay=0,
                            language_code="es"))
    costs = _costs(dfs_client_live, job)
    _report(est, costs)
    assert costs["price_inputs"].astype(str).str.contains('"es"').all()

    std_kws = _keywords(1000)
    est2 = _estimate(ep, std_kws, endpoint_mode="standard")
    job2 = generate_job_id()
    asyncio.run(ep.post_all(std_kws, job_id=job2, use_collector=True, proceed_at_pct=0.0))
    costs2 = _costs(dfs_client_live, job2)
    assert len(costs2) == 1
    _report(est2, costs2)


@pytest.mark.live
def test_fail_fast_guards(dfs_client_live, capsys):
    ep = dfs_client_live.dataforseo_labs_google_domain_rank_overview
    job = generate_job_id()
    with pytest.raises(InsufficientBalanceError):
        ep.live("busbank.com", domain=None, job_id=job, upload=False, balance_buffer=1e6)
    assert _costs(dfs_client_live, job).empty

    job2 = generate_job_id()
    ep.live("busbank.com", domain=None, job_id=job2, upload=False, balance_buffer=1e6,
            ignore_balance_check=True)
    assert "ignore_balance_check=True" in capsys.readouterr().out
    assert len(_costs(dfs_client_live, job2)) == 1

    job3 = generate_job_id()
    with pytest.raises(InvalidLocationError):
        dfs_client_live.dataforseo_labs_google_keyword_overview.live(
            ["charter bus"], domain=None, job_id=job3, upload=False, location_code=200528)
    assert _costs(dfs_client_live, job3).empty
