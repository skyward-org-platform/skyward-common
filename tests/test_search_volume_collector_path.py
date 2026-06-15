"""search_volume collector-path wiring: one task per ~keywords_per_task batch, tracked at
task granularity; 40200 rejects surfaced; domain context threaded through.
"""

from __future__ import annotations

import skyward.data.dataforseo.endpoints.keywords_data_google_ads_search_volume as sv_mod
from skyward.data.dataforseo import ClientConfig, DataForSEOClient
from skyward.functions import generate_job_id
from tests.conftest import FakeBigQueryClient


def _ep():
    client = DataForSEOClient(username="u", password="p", bq_client=FakeBigQueryClient(),
                              config=ClientConfig())
    return client, client.keywords_data_google_ads_search_volume


def test_sv_collector_chunks_one_task_per_batch(monkeypatch):
    client, ep = _ep()
    calls = {"n": 0}

    def _post(url, payload, **kw):
        calls["n"] += 1
        assert "keywords" in payload[0]          # search_volume posts a keyword list per task
        assert len(payload[0]["keywords"]) <= 100
        return {"tasks": [{"id": f"t{calls['n']}", "status_code": 20100, "data": {}}]}

    client._post = _post
    captured = {}
    monkeypatch.setattr(sv_mod, "submit_and_wait",
                        lambda **k: (captured.update(k) or {"total_tasks": len(k["posted_tasks"]),
                                                            "proceeded": True}))

    summary = ep._post_all_collector(
        [f"kw{i}" for i in range(250)], job_id=generate_job_id(),
        resolved={"domain_id": 3, "domain": "d.com"}, proceed_at_pct=1.0, keywords_per_task=100,
    )
    assert calls["n"] == 3                         # 250 kw / 100 = 3 batches
    assert len(captured["posted_tasks"]) == 3      # one task per batch
    assert captured["endpoint_key"] == "keywords_data_google_ads_search_volume"
    assert captured["domain_id"] == 3 and captured["domain"] == "d.com"
    assert summary["total_tasks"] == 3


def test_sv_collector_reports_40200(monkeypatch):
    client, ep = _ep()
    client._post = lambda url, payload, **kw: {"tasks": [{"id": "t", "status_code": 40200, "data": {}}]}
    monkeypatch.setattr(sv_mod, "submit_and_wait",
                        lambda **k: {"total_tasks": len(k["posted_tasks"]), "proceeded": False})

    summary = ep._post_all_collector(["a", "b"], job_id=generate_job_id(), resolved=None,
                                     keywords_per_task=100)
    assert summary["rejected_payment"] == 1
    assert summary["total_tasks"] == 0             # nothing accepted -> nothing tracked
