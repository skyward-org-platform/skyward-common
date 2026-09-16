"""ranked_keywords must count a domain "completed" only once its pagination finishes.

Before this fix, `_fetch_domain_keywords` called `_in_unit(run, domain, ...)` once per
page, so `RunContext._completed` got the domain after page one (and again on every later
page). A mid-run balance stop would then report a partially-fetched domain as done, and
the release notes tell callers to rerun only `remaining_targets` — so a wrongly-"done"
domain would silently never be retried.
"""
import asyncio

import pandas as pd
import pytest

from skyward.data.dataforseo import DataForSEOClient
from skyward.data.dataforseo.estimates import CostEstimate, RunPlan
from skyward.data.dataforseo.run import RunContext
from skyward.functions import generate_job_id
from tests.conftest import FakeBigQueryClient


def _make_run(bq, targets=("a.com",)):
    client = DataForSEOClient(username="u", password="p", bq_client=bq)
    plan = RunPlan("dataforseo_labs_google_ranked_keywords", "live",
                    len(targets), len(targets), len(targets), tuple(targets))
    est = CostEstimate(0.0, 0.0, 0.0, "list_price", plan)
    run = RunContext(
        client=client, endpoint_key="dataforseo_labs_google_ranked_keywords",
        job_id=generate_job_id(), plan=plan, estimate=est, endpoint_mode="live",
        upload=True, write=lambda df, uid: None, stamp=lambda df: df,
        empty_columns=["keyword", "task_id"],
    )
    return client, run


def test_domain_marked_complete_only_once_after_all_pages(monkeypatch):
    bq = FakeBigQueryClient()
    client, run = _make_run(bq)
    ep = client.dataforseo_labs_google_ranked_keywords

    offsets_seen = []

    def fake_fetch_live(target, **kwargs):
        offsets_seen.append(kwargs.get("offset"))
        return pd.DataFrame([{"keyword": f"kw{kwargs.get('offset')}", "task_id": "t"}])

    monkeypatch.setattr(ep, "_fetch_live", fake_fetch_live)

    df = asyncio.run(ep._fetch_domain_keywords("a.com", 6, page_size=3, _run=run))

    assert offsets_seen == [0, 3]          # two pages fetched
    assert len(df) == 2
    # Marked complete exactly once, and only after both pages finished — not once per page.
    assert run.completed_targets() == ["a.com"]


def test_domain_not_marked_complete_when_pagination_is_cut_short(monkeypatch):
    bq = FakeBigQueryClient()
    client, run = _make_run(bq)
    ep = client.dataforseo_labs_google_ranked_keywords

    def fake_fetch_live(target, **kwargs):
        if kwargs.get("offset") == 3:
            raise RuntimeError("network blew up mid-pagination")
        return pd.DataFrame([{"keyword": "kw0", "task_id": "t"}])

    monkeypatch.setattr(ep, "_fetch_live", fake_fetch_live)

    with pytest.raises(RuntimeError, match="network blew up"):
        asyncio.run(ep._fetch_domain_keywords("a.com", 6, page_size=3, _run=run))

    # The domain only had its first page fetched — it must still show as remaining, not
    # completed, so a caller rerunning `remaining_targets` actually retries it.
    assert run.completed_targets() == []
    assert run.remaining_targets() == ["a.com"]
