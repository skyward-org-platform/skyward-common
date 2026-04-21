"""Fixtures + configuration for live DataForSEO integration tests.

These tests hit the REAL DataForSEO API and cost money. They are skipped by
default. Run with:

    uv run pytest tests/live/ -m live --run-live

Each test:
  1. Uses `dfs_client_live` fixture (real creds + real BQ).
  2. Makes a minimum-viable API call (small limit, 1-3 targets, cheap location).
  3. Writes the raw response to tests/live/responses/ for Layer B fidelity audit.
  4. Reports the cost via `cost_tracker` fixture.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

RESPONSES_DIR = Path(__file__).parent / "responses"
RESPONSES_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# CLI flag to gate live tests
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked with @pytest.mark.live (real API calls, costs money).",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="Need --run-live to run (costs money)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# ---------------------------------------------------------------------------
# Session-scoped cost tracker
# ---------------------------------------------------------------------------

class _CostTracker:
    def __init__(self):
        self.per_endpoint: dict[str, list[float]] = {}

    def record(self, endpoint: str, cost: float | None):
        if cost is None:
            return
        self.per_endpoint.setdefault(endpoint, []).append(float(cost))

    def total(self) -> float:
        return sum(c for costs in self.per_endpoint.values() for c in costs)

    def summary_text(self) -> str:
        lines = ["", "============ Live test cost summary ============"]
        lines.append(f"Total cost for this run: ${self.total():.4f} USD")
        if self.per_endpoint:
            lines.append("Per-endpoint breakdown:")
            for ep, costs in sorted(self.per_endpoint.items()):
                lines.append(f"  {ep:60s} ${sum(costs):.4f} ({len(costs)} calls)")
        return "\n".join(lines)


@pytest.fixture(scope="session")
def cost_tracker():
    tracker = _CostTracker()
    yield tracker
    print(tracker.summary_text())


# ---------------------------------------------------------------------------
# Real DataForSEOClient with real BQ
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def dfs_client_live():
    """Real DataForSEOClient + real BigQueryClient. Session-scoped."""
    from skyward.config import load_config
    from skyward.data.bigquery import BigQueryClient
    from skyward.data.dataforseo import DataForSEOClient, ClientConfig

    cfg = load_config()
    if not cfg.dataforseo_username or not cfg.dataforseo_password:
        pytest.skip("DATAFORSEO_API_LOGIN/PASSWORD not set")

    bq = BigQueryClient(project_id=cfg.datahub_project_id)
    return DataForSEOClient(
        username=cfg.dataforseo_username,
        password=cfg.dataforseo_password,
        bq_client=bq,
        config=ClientConfig(debug=True),
    )


# ---------------------------------------------------------------------------
# Raw response writer
# ---------------------------------------------------------------------------

def write_raw_response(endpoint: str, mode: str, payload: dict) -> Path:
    """Write a raw DataForSEO response to tests/live/responses/ for audit."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fn = f"{endpoint}_{mode}_{ts}.json"
    path = RESPONSES_DIR / fn
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


# ---------------------------------------------------------------------------
# Seeded test domain — guaranteed to exist in Meta.domains
# ---------------------------------------------------------------------------

SEEDED_TEST_DOMAIN = "example.com"


@pytest.fixture(scope="session")
def seeded_domain(dfs_client_live):
    """Ensure `example.com` exists in Meta.domains. Returns the domain_id."""
    meta = dfs_client_live.meta_client
    existing = meta.get_domain(SEEDED_TEST_DOMAIN)
    if existing is None:
        added = meta.add_domains([SEEDED_TEST_DOMAIN])
        return added[0]["domain_id"]
    return existing["domain_id"]


# ---------------------------------------------------------------------------
# Auto-capture: every HTTP response the live client sees gets dumped to disk
# ---------------------------------------------------------------------------

def _endpoint_slug_from_url(url: str) -> str:
    """Pull a compact endpoint label out of a DataForSEO URL.

    'https://api.dataforseo.com/v3/backlinks/backlinks/live' → 'backlinks-backlinks-live'
    """
    after_v3 = url.split("/v3/", 1)[-1]
    # Strip trailing task_id suffix for GET endpoints like /task_get/<id>
    parts = [p for p in after_v3.split("/") if p]
    # Keep the path segments, drop anything that looks like a UUID (task IDs)
    kept = []
    for p in parts:
        # simple UUID-ish detection: 36 chars with dashes
        if len(p) == 36 and p.count("-") == 4:
            break
        kept.append(p)
    return "-".join(kept) or "unknown"


@pytest.fixture(autouse=True)
def _capture_responses(request, monkeypatch):
    """Wrap DataForSEOClient._post/_get to dump every response JSON.

    Activates for any test marked `live`. File per HTTP call:
        tests/live/responses/<test_name>__<endpoint_slug>__<timestamp>.json
    """
    if "live" not in request.keywords:
        yield
        return

    # dfs_client_live may or may not be used by this test — resolve lazily.
    # If the test doesn't request it, there's nothing to wrap.
    if "dfs_client_live" not in request.fixturenames:
        yield
        return

    client = request.getfixturevalue("dfs_client_live")
    test_name = request.node.name
    counter = {"n": 0}

    orig_post = client._post
    orig_get = client._get

    def _dump(method: str, url: str, resp: dict | None) -> None:
        if resp is None:
            return
        counter["n"] += 1
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = _endpoint_slug_from_url(url)
        fn = f"{test_name}__{slug}__{ts}_{counter['n']:03d}.json"
        (RESPONSES_DIR / fn).write_text(json.dumps(resp, indent=2, default=str))

    def wrapped_post(endpoint, payload, *args, **kwargs):
        resp = orig_post(endpoint, payload, *args, **kwargs)
        _dump("POST", endpoint, resp)
        return resp

    def wrapped_get(endpoint, *args, **kwargs):
        resp = orig_get(endpoint, *args, **kwargs)
        _dump("GET", endpoint, resp)
        return resp

    monkeypatch.setattr(client, "_post", wrapped_post)
    monkeypatch.setattr(client, "_get", wrapped_get)

    yield
