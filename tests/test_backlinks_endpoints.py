"""Unit tests for Backlinks endpoints, `subject` tagging, and generic batch runner."""

from __future__ import annotations

import asyncio
import itertools
import json

import pandas as pd
import pytest

from skyward.data.dataforseo import DataForSEOClient
from skyward.data.dataforseo import backlinks_batch as batch
from skyward.functions import generate_job_id
from tests.conftest import FakeBigQueryClient


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeMetaClient:
    def __init__(self, domains: dict[int, str]):
        self._domains = dict(domains)

    def get_domain_by_id(self, domain_id: int) -> dict | None:
        name = self._domains.get(int(domain_id))
        if name is None:
            return None
        return {"domain_id": int(domain_id), "domain": name}


class _BacklinksFakeDfsSession:
    """Fake requests session answering all five Backlinks endpoints."""

    def __init__(self, *, total_count_override: dict[str, int] | None = None, fail_40501_on: str | None = None):
        self.calls: list[tuple[str, list[dict]]] = []
        self._ids = itertools.count(1)
        self._total_count_override = total_count_override or {}
        self._fail_40501_on = fail_40501_on

    def post(self, url: str, json: list[dict] | None = None, timeout=None):  # noqa: A002
        payload = json or []
        self.calls.append((url, payload))
        tasks = []
        for task in payload:
            tid = f"bl-task-{next(self._ids)}"
            if url.endswith("backlinks/bulk_pages_summary/live"):
                targets = task.get("targets") or []
                if (
                    self._fail_40501_on
                    and len(targets) > 1
                    and self._fail_40501_on in targets
                ):
                    tasks.append({
                        "id": tid,
                        "status_code": 40501,
                        "status_message": "Invalid Field: 'targets'",
                        "cost": 0.0,
                        "data": task,
                        "result": None,
                    })
                    continue
                if targets == [self._fail_40501_on]:
                    tasks.append({
                        "id": tid,
                        "status_code": 40501,
                        "status_message": f"Invalid Field: 'target' ({self._fail_40501_on}).",
                        "cost": 0.0,
                        "data": task,
                        "result": None,
                    })
                    continue
                items = [
                    {
                        "url": u,
                        "backlinks": 12,
                        "referring_domains": 4,
                        "backlinks_spam_score": 5.0,
                        "referring_links_attributes": {"nofollow": 2},
                    }
                    for u in targets
                ]
                tasks.append({
                    "id": tid,
                    "status_code": 20000,
                    "cost": 0.02,
                    "data": task,
                    "result": [{"items": items}],
                })
                continue

            if url.endswith("backlinks/summary/live"):
                t = task["target"]
                tasks.append({
                    "id": tid,
                    "status_code": 20000,
                    "cost": 0.02,
                    "data": task,
                    "result": [{
                        "target": t,
                        "rank": 350,
                        "backlinks": 120,
                        "info": {"target_spam_score": 4},
                        "referring_domains": 25,
                    }],
                })
                continue

            if url.endswith("backlinks/history/live"):
                t = task["target"]
                tasks.append({
                    "id": tid,
                    "status_code": 20000,
                    "cost": 0.02,
                    "data": task,
                    "result": [{
                        "target": t,
                        "items": [{
                            "date": "2026-10-01 00:00:00 +00:00",
                            "rank": 340,
                            "backlinks": 115,
                            "referring_domains": 24,
                        }],
                    }],
                })
                continue

            if url.endswith("backlinks/referring_domains/live") or url.endswith("backlinks/anchors/live"):
                t = task["target"]
                offset = int(task.get("offset", 0))
                limit = int(task.get("limit", 1000))
                total = self._total_count_override.get(t, 2)
                remaining = max(0, min(limit, total - offset))
                is_ref = url.endswith("referring_domains/live")
                items = []
                for i in range(remaining):
                    idx = offset + i
                    item = {
                        "type": "backlinks_referring_domain" if is_ref else "backlinks_anchor",
                        "rank": 100 + idx,
                        "backlinks": 10 + idx,
                        "referring_domains": 2,
                        "first_seen": "2026-01-15 00:00:00 +00:00",
                        "referring_links_tld": {"com": 10 + idx},
                    }
                    if is_ref:
                        item["domain"] = f"ref-{idx}.{t}"
                    else:
                        item["anchor"] = f"anchor-{idx}"
                    items.append(item)
                tasks.append({
                    "id": tid,
                    "status_code": 20000,
                    "cost": 0.024 + len(items) * 0.000036,
                    "data": task,
                    "result": [{"target": t, "total_count": total, "items": items}],
                })
                continue

            raise AssertionError(f"Unexpected URL: {url}")

        return _FakeResponse({"status_code": 20000, "tasks": tasks})


def _make_client(bq: FakeBigQueryClient, session: _BacklinksFakeDfsSession, domains: dict[int, str]):
    c = DataForSEOClient(username="u", password="p", bq_client=bq)
    c._session = session
    c._meta_client = _FakeMetaClient(domains)
    return c


def test_referring_domains_and_anchors_preserve_client_domain_and_subject():
    bq = FakeBigQueryClient()
    session = _BacklinksFakeDfsSession(total_count_override={"rival.example": 5})
    client = _make_client(bq, session, {10: "client.example"})
    jid = generate_job_id()

    ref_df = client.backlinks_referring_domains.live(
        "rival.example",
        domain_id=10,
        job_id=jid,
        subject="rival",
        limit=5,
        page_size=2,
    )
    assert len(ref_df) == 5
    # 5 items at page_size=2 -> pages of sizes 2, 2, 1 => 3 HTTP calls
    ref_calls = [c for c in session.calls if c[0].endswith("referring_domains/live")]
    assert len(ref_calls) == 3
    for _, payload in ref_calls:
        assert "subject" not in payload[0]
    assert list(ref_df["referring_domain"]) == [f"ref-{i}.rival.example" for i in range(5)]
    assert set(ref_df["domain"]) == {"client.example"}
    assert set(ref_df["domain_id"]) == {10}
    assert set(ref_df["subject"]) == {"rival"}
    assert json.loads(ref_df.iloc[0]["referring_links_tld"]) == {"com": 10}

    anch_df = client.backlinks_anchors.live(
        "client.example",
        domain_id=10,
        job_id=jid,
        page_size=2,
    )
    # Default total_count=2 with page_size=2, hits total_count early-stop on 1st call
    anch_calls = [c for c in session.calls if c[0].endswith("anchors/live")]
    assert len(anch_calls) == 1
    assert "subject" not in anch_calls[0][1][0]
    assert list(anch_df["anchor"]) == ["anchor-0", "anchor-1"]
    assert set(anch_df["subject"]) == {"client"}

    with pytest.raises(ValueError, match="subject"):
        client.backlinks_referring_domains.live(
            "rival.example", domain_id=10, job_id=jid, subject="competitor"
        )


def test_backlinks_summary_and_bulk_pages_summary_subject_tagging():
    bq = FakeBigQueryClient()
    session = _BacklinksFakeDfsSession(fail_40501_on="https://client.example/bad")
    client = _make_client(bq, session, {10: "client.example"})
    jid = generate_job_id()

    sum_df = client.backlinks_summary.live(
        "rival.example", domain_id=10, job_id=jid, subject="rival"
    )
    assert len(sum_df) == 1
    assert sum_df.iloc[0]["subject"] == "rival"
    assert sum_df.iloc[0]["domain_id"] == 10
    assert sum_df.iloc[0]["domain"] == "client.example"
    sum_payload = [c[1][0] for c in session.calls if c[0].endswith("backlinks/summary/live")][0]
    assert "subject" not in sum_payload

    with pytest.raises(ValueError, match="subject"):
        client.backlinks_summary.live("rival.example", domain_id=10, job_id=jid, subject="bad")

    # Single live() with default subject='client'
    single_bulk = client.backlinks_bulk_pages_summary.live(
        "https://client.example/", domain_id=10, job_id=jid
    )
    assert list(single_bulk["subject"]) == ["client"]

    # Async live_all() including a 40501 split-and-retry path preserves subject
    bulk_df = asyncio.run(
        client.backlinks_bulk_pages_summary.live_all(
            ["https://client.example/a", "https://client.example/bad", "https://client.example/b"],
            domain_id=10,
            job_id=jid,
            subject="rival",
            batch_delay=0,
        )
    )
    assert set(bulk_df["url"]) == {"https://client.example/a", "https://client.example/b"}
    assert set(bulk_df["subject"]) == {"rival"}

    with pytest.raises(ValueError, match="subject"):
        asyncio.run(
            client.backlinks_bulk_pages_summary.live_all(
                ["https://client.example/a"], domain_id=10, job_id=jid, subject="bad"
            )
        )


def test_backlinks_batch_plan_and_shared_rival_reuse(tmp_path):
    manifest_data = {
        "version": "test-v1",
        "clients": [
            {
                "domain_id": 10,
                "domain": "alpha.example",
                "rivals": [
                    {"domain_id": 100, "domain": "shared-rival.example"},
                    {"domain_id": 101, "domain": "alpha-rival.example"},
                ],
                "sitemap_urls": ["https://alpha.example/", "https://alpha.example/about"],
            },
            {
                "domain_id": 20,
                "domain": "beta.example",
                "rivals": [
                    {"domain_id": 100, "domain": "shared-rival.example"},
                    {"domain_id": 201, "domain": "beta-rival.example"},
                ],
                "sitemap_urls": ["https://beta.example/"],
            },
        ],
    }
    manifest_file = tmp_path / "targets.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    loaded = batch.load_manifest(manifest_file)
    clients = batch.filter_clients(loaded)
    endpoints = batch.resolve_endpoints()
    domain_units, bulk_units = batch.build_work_plan(clients, endpoints)

    # 2 clients x (1 client + 2 rivals) = 6 units per domain endpoint x 4 endpoints
    assert len(domain_units) == 6 * 4
    clones = [u for u in domain_units if u.clone_of_client_id is not None]
    assert len(clones) == 4
    assert {u.target for u in clones} == {"shared-rival.example"}
    assert {u.client_domain_id for u in clones} == {20}
    assert {u.clone_of_client_id for u in clones} == {10}
    assert sum(len(b.urls) for b in bulk_units) == 3

    offline_client = DataForSEOClient(username="u", password="p", bq_client=None)
    summary = batch.estimate_work_plan(offline_client, endpoints, domain_units, bulk_units)
    # 5 primary domains x (1 + 1 + 20 + 20) + 2 bulk batches = 212 requests
    assert summary["total_requests"] == 212

    bq = FakeBigQueryClient()
    session = _BacklinksFakeDfsSession()
    live_client = _make_client(bq, session, {10: "alpha.example", 20: "beta.example"})
    jid = generate_job_id()

    res = batch.execute_backlinks_batch(
        live_client,
        clients,
        endpoints,
        job_id=jid,
        limit=100,
    )
    assert res["job_id"] == jid
    assert len(res["reused_targets"]) == 4

    shared_calls = [
        (url, payload)
        for url, payload in session.calls
        for task in payload
        if task.get("target") == "shared-rival.example"
    ]
    assert len(shared_calls) == 4  # Fetched once per domain endpoint

    ref_loads = [
        load["df"]
        for load in bq.client.loaded_tables
        if str(load["table_ref"]).endswith(".backlinks-referring_domains")
    ]
    combined_ref = pd.concat(ref_loads, ignore_index=True)
    shared_ref = combined_ref[combined_ref["target"] == "shared-rival.example"]
    assert set(shared_ref["domain_id"]) == {10, 20}
    assert set(shared_ref["subject"]) == {"rival"}
    upload_ids = shared_ref.groupby("domain_id")["upload_id"].unique().to_dict()
    assert upload_ids[10][0] != upload_ids[20][0]

    bulk_loads = [
        load["df"]
        for load in bq.client.loaded_tables
        if str(load["table_ref"]).endswith(".backlinks-bulk_pages_summary")
    ]
    combined_bulk = pd.concat(bulk_loads, ignore_index=True)
    assert set(combined_bulk["subject"]) == {"client"}
    assert set(combined_bulk["domain_id"]) == {10, 20}
    assert len(combined_bulk) == 3

