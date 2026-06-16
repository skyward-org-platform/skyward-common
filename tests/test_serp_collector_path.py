"""serp_google_organic collector-path wiring: post_all routes to submit-and-track when
enabled, accumulates posted task_ids, surfaces 40200 reject counts, passes domain context.
"""

from __future__ import annotations

import skyward.data.dataforseo.endpoints.serp_google_organic as serp_mod
from skyward.data.dataforseo import ClientConfig, DataForSEOClient
from skyward.functions import generate_job_id
from tests.conftest import FakeBigQueryClient


def _client():
    return DataForSEOClient(username="u", password="p", bq_client=FakeBigQueryClient(),
                            config=ClientConfig())


def _stub_post(reject_idx):
    def _post(url, payload, **kw):
        tasks = []
        for i, item in enumerate(payload):
            code = 40200 if i == reject_idx else 20100
            tasks.append({"id": f"t{i}", "status_code": code, "data": {"tag": item["keyword"]}})
        return {"tasks": tasks}
    return _post


def test_collector_path_posts_tracks_and_reports_40200(monkeypatch):
    client = _client()
    ep = client.serp_google_organic
    client._post = _stub_post(reject_idx=2)  # 3rd keyword rejected (Payment Required)

    captured = {}

    def fake_submit_and_wait(**kwargs):
        captured.update(kwargs)
        return {"job_id": kwargs["job_id"], "endpoint": kwargs["endpoint_key"],
                "total_tasks": len(kwargs["posted_tasks"]), "completion_pct": 1.0,
                "proceeded": True}

    monkeypatch.setattr(serp_mod, "submit_and_wait", fake_submit_and_wait)

    summary = ep._post_all_collector(
        ["a", "b", "c"], job_id=generate_job_id(),
        resolved={"domain_id": 7, "domain": "x.com"}, proceed_at_pct=1.0,
    )

    assert len(captured["posted_tasks"]) == 2          # t0, t1 (t2 was 40200)
    assert captured["endpoint_key"] == "serp_google_organic"
    assert captured["domain_id"] == 7 and captured["domain"] == "x.com"
    assert summary["rejected_payment"] == 1
    assert summary["rejected_other"] == 0
    assert summary["proceeded"] is True


def test_collector_path_chunks_over_100_keywords(monkeypatch):
    client = _client()
    ep = client.serp_google_organic
    calls = {"n": 0}

    def _post(url, payload, **kw):
        calls["n"] += 1
        assert len(payload) <= 100
        return {"tasks": [{"id": f"c{calls['n']}-{i}", "status_code": 20100,
                           "data": {"tag": item["keyword"]}} for i, item in enumerate(payload)]}

    client._post = _post
    monkeypatch.setattr(serp_mod, "submit_and_wait",
                        lambda **k: {"total_tasks": len(k["posted_tasks"]), "proceeded": True})

    summary = ep._post_all_collector([f"kw{i}" for i in range(250)], job_id=generate_job_id(),
                                     resolved=None, proceed_at_pct=1.0)
    assert calls["n"] == 3            # 250 -> 100 + 100 + 50
    assert summary["total_tasks"] == 250


def test_post_all_sync_routes_to_collector_when_flag_set(monkeypatch):
    client = _client()
    ep = client.serp_google_organic
    monkeypatch.setattr(ep, "_post_all_collector", lambda *a, **k: {"routed": True})
    out = ep._post_all_sync(["a"], domain=None, job_id=generate_job_id(), use_collector=True)
    assert out == {"routed": True}
