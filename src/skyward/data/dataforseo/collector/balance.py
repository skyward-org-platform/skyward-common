"""Low-balance alerting for the DataForSEO collector VM (ClickUp 86bb6m28m).

Checks the account balance at most once per `interval_s` from the collector's poll loop and
posts to the collector's Slack channel when the balance crosses into a worse level
(warn or critical), plus one recovery message when it climbs back above the warn threshold.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Callable

_RANK = {"ok": 0, "warn": 1, "critical": 2}


class BalanceMonitor:
    def __init__(
        self,
        client,
        alerter,
        *,
        warn_usd: float,
        critical_usd: float,
        interval_s: float = 3600.0,
        now: Callable[[], float] = time.monotonic,
        wall: Callable = datetime.now,
    ) -> None:
        if critical_usd > warn_usd:
            raise ValueError("critical_usd must be less than or equal to warn_usd")
        self._client = client
        self._alerter = alerter
        self._warn = float(warn_usd)
        self._critical = float(critical_usd)
        self._interval = float(interval_s)
        self._now = now
        self._wall = wall
        self._last_check: float | None = None
        self._level = "ok"

    @classmethod
    def from_env(cls, client, alerter, env=os.environ) -> "BalanceMonitor | None":
        warn = env.get("DFS_COLLECTOR_BALANCE_WARN_USD")
        critical = env.get("DFS_COLLECTOR_BALANCE_CRITICAL_USD")
        if not warn or not critical:
            return None
        return cls(client, alerter, warn_usd=float(warn), critical_usd=float(critical),
                   interval_s=float(env.get("DFS_COLLECTOR_BALANCE_INTERVAL_S", 3600)))

    def _level_for(self, balance: float) -> str:
        if balance < self._critical:
            return "critical"
        if balance < self._warn:
            return "warn"
        return "ok"

    def check(self) -> str | None:
        t = self._now()
        if self._last_check is not None and (t - self._last_check) < self._interval:
            return None
        self._last_check = t

        info = self._client.get_balance()
        if not info.get("raw"):
            return None
        balance = float(info["balance"])
        level = self._level_for(balance)
        previous, self._level = self._level, level
        stamp = self._wall(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        if _RANK[level] > _RANK[previous]:
            threshold = self._critical if level == "critical" else self._warn
            self._alerter.notify(
                "\U0001f6a8" if level == "critical" else "⚠️",
                f"DataForSEO Balance {level.title()}",
                {"Balance": f"${balance:,.2f}", "Threshold": f"${threshold:,.2f}", "Time": stamp},
            )
        elif level == "ok" and previous != "ok":
            self._alerter.notify(
                "✅", "DataForSEO Balance Recovered",
                {"Balance": f"${balance:,.2f}", "Warn threshold": f"${self._warn:,.2f}", "Time": stamp},
            )
        return level
