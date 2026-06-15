"""Slack alerting for the standard-mode collector (ClickUp 86bac9q9y).

Posts to #ops_alerts via the existing `send_slack` (webhook SLACK_WEBHOOK_OPS_ALERTS).
The collector self-reports its whole lifecycle and every connection/operation failure:

- lifecycle: startup, graceful shutdown (SIGTERM), crash (systemd OnFailure calls in).
- failures: DFS unreachable, BigQuery connect/upload/tracking failures, cycle errors,
  high task-fetch failure rate.

`fire()`/`resolve()` are **deduped**: a failure alerts on the transition into the bad
state and at most once per cooldown while it persists, with an all-clear when it clears —
so an outage doesn't spam the channel. `heartbeat()` prints a parseable line each loop so a
silent VM death shows up as a stale heartbeat (see infra task 86baf4f5y).
"""

from __future__ import annotations

import os
import socket
import time
from typing import Callable

ALERT_CHANNEL = "ops_alerts"
_DEFAULT_COOLDOWN_S = 900  # re-alert at most every 15 min while a failure persists


def classify_failure(exc: Exception) -> tuple[str, str]:
    """Map an exception to (alert_key_suffix, human_label) — BigQuery vs generic."""
    mod = type(exc).__module__ or ""
    if mod.startswith("google.") or "bigquery" in mod or "google.api_core" in mod:
        return "bigquery", "BigQuery error"
    return "error", "error"


class Alerter:
    def __init__(
        self,
        *,
        channel: str = ALERT_CHANNEL,
        cooldown_s: float = _DEFAULT_COOLDOWN_S,
        send: Callable[[str], None] | None = None,
        now: Callable[[], float] = time.monotonic,
        hostname: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._channel = channel
        self._cooldown = cooldown_s
        self._now = now
        self._host = hostname or socket.gethostname()
        self._send = send or self._default_send
        self._active: dict[str, float] = {}  # firing key -> last-sent monotonic time
        if enabled is None:
            # Auto-disable (no-op) in envs without the webhook, unless a send fn is injected.
            enabled = send is not None or bool(os.environ.get(f"SLACK_WEBHOOK_{channel.upper()}"))
        self._enabled = enabled

    def _default_send(self, text: str) -> None:
        from skyward.notifications import send_slack
        send_slack(self._channel, text)

    def _post(self, text: str) -> None:
        if not self._enabled:
            return
        try:
            self._send(f"[dfs-collector · {self._host}] {text}")
        except Exception as e:  # never let alerting break the collector
            print(f"[alerts] failed to send Slack alert: {e!r}")

    # ----- deduped failure alerts -----

    def fire(self, key: str, message: str, *, severity: str = "⚠️") -> None:
        last = self._active.get(key)
        now = self._now()
        if last is not None and (now - last) < self._cooldown:
            return  # already firing and within cooldown — suppress
        self._active[key] = now
        self._post(f"{severity} {message}")

    def resolve(self, key: str, message: str | None = None) -> None:
        if key in self._active:
            del self._active[key]
            self._post(f"✅ {message or f'{key} recovered'}")

    # ----- lifecycle -----

    def startup(self) -> None:
        self._post("🟢 collector started")

    def shutdown(self, reason: str = "signal") -> None:
        self._post(f"🔻 collector shutting down ({reason})")

    def crash(self, message: str) -> None:
        self._post(f"❌ {message}")

    # ----- liveness -----

    def heartbeat(self) -> None:
        print(f"[dfs-collector] heartbeat ts={int(time.time())} host={self._host}", flush=True)
