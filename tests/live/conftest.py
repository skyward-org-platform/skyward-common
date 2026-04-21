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

    bq = BigQueryClient(project_id=cfg.gcp_project_id)
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
