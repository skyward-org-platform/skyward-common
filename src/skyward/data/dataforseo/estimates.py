"""Run plans and cost estimates for DataForSEO endpoints (v1.6.1)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, fields

import pandas as pd

from skyward.data.dataforseo.cost_rates import Rate, rates_by_key

logger = logging.getLogger(__name__)

DATASET = "DataForSEO"
COST_ESTIMATES_TABLE = "cost_estimates"
ACTUALS_VIEW = "endpoint_cost_actuals"
MIN_OBSERVED_SAMPLES = 20


@dataclass(frozen=True)
class RunPlan:
    endpoint: str
    endpoint_mode: str
    planned_requests: int
    planned_items: int
    planned_max_rows: int
    targets: tuple = ()
    price_inputs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CostEstimate:
    max_usd: float
    avg_usd: float
    list_price_usd: float
    basis: str  # "observed" | "list_price" | "no_rate"
    plan: RunPlan


def list_price_usd(plan: RunPlan, rate: Rate) -> float:
    return (plan.planned_requests * rate.price_per_request_usd
            + plan.planned_items * rate.price_per_item_usd)


def price_plan(plan: RunPlan, rate: Rate, observed: dict | None = None) -> CostEstimate:
    lp = list_price_usd(plan, rate)
    max_usd = round(lp * (1 + rate.max_buffer), 6)
    if (observed
            and (observed.get("sample_requests") or 0) >= MIN_OBSERVED_SAMPLES
            and observed.get("avg_cost_per_request") is not None):
        avg = round(plan.planned_requests * float(observed["avg_cost_per_request"]), 6)
        basis = "observed"
    else:
        avg = round(lp, 6)
        basis = "list_price"
    return CostEstimate(max_usd=max_usd, avg_usd=avg, list_price_usd=round(lp, 6),
                        basis=basis, plan=plan)


class CostEstimator:
    """Prices RunPlans from DataForSEO.cost_estimates + endpoint_cost_actuals (cached)."""

    def __init__(self, client) -> None:
        self._client = client
        self._rates: dict | None = None
        self._observed: dict | None = None
        self._lock = threading.Lock()

    def rates(self) -> dict[tuple[str, str], Rate]:
        with self._lock:
            if self._rates is None:
                self._rates = self._load_rates()
            return self._rates

    def observed(self) -> dict[tuple[str, str], dict]:
        with self._lock:
            if self._observed is None:
                self._observed = self._load_observed()
            return self._observed

    def estimate(self, plan: RunPlan) -> CostEstimate:
        rate = self.rates().get((plan.endpoint, plan.endpoint_mode))
        if rate is None:
            return CostEstimate(0.0, 0.0, 0.0, "no_rate", plan)
        return price_plan(plan, rate, self.observed().get((plan.endpoint, plan.endpoint_mode)))

    def _query(self, sql: str) -> pd.DataFrame | None:
        bq = self._client.bq_client
        if bq is None:
            return None
        try:
            return bq.client.query(sql).result().to_dataframe()
        except Exception as e:  # noqa: BLE001 - estimates must never break a run
            logger.warning("DataForSEO estimate read failed (%s): %r", sql.split("`")[1], e)
            return None

    def _load_rates(self) -> dict[tuple[str, str], Rate]:
        loaded = rates_by_key()
        bq = self._client.bq_client
        if bq is None:
            return loaded
        df = self._query(f"SELECT * FROM `{bq.client.project}.{DATASET}.{COST_ESTIMATES_TABLE}`")
        if df is None or df.empty:
            return loaded
        names = {f.name for f in fields(Rate)}
        for row in df.to_dict("records"):
            kw = {k: (None if pd.isna(v) else v) for k, v in row.items() if k in names}
            try:
                rate = Rate(**kw)
            except TypeError:
                continue
            loaded[(rate.endpoint, rate.endpoint_mode)] = rate
        return loaded

    def _load_observed(self) -> dict[tuple[str, str], dict]:
        bq = self._client.bq_client
        if bq is None:
            return {}
        df = self._query(
            f"SELECT endpoint, endpoint_mode, sample_requests, avg_cost_per_request "
            f"FROM `{bq.client.project}.{DATASET}.{ACTUALS_VIEW}`"
        )
        if df is None or df.empty:
            return {}
        return {(r["endpoint"], r["endpoint_mode"]): r for r in df.to_dict("records")}
