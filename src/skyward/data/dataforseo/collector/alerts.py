"""Slack alerting for the standard-mode collector (ClickUp 86bac9q9y).

Posts to the configured channel (DFS_COLLECTOR_ALERT_CHANNEL -> SLACK_WEBHOOK_<CHANNEL>)
via the existing `send_slack`. Message style mirrors the SF crawler alerts:

    <emoji> *DFS Collector: <Title>*
    VM: <vm-name>
    <Key>: <Value>
    ...

The collector self-reports its whole lifecycle and every connection/operation failure:
- lifecycle: VM started (🟢), shutting down (🔴, with uptime), service crash (❌).
- failures (deduped, with ✅ recovery): DFS unreachable, BigQuery errors, cycle errors,
  high task-fetch failure rate.

`fire()`/`resolve()` are deduped: a failure alerts on the transition into the bad state and
at most once per cooldown while it persists, with an all-clear when it clears. `heartbeat()`
prints a parseable line each loop so a silent VM death shows as a stale heartbeat (infra
task 86baf4f5y).
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
        return "bigquery", "BigQuery Error"
    return "error", "Cycle Error"


def _memory_snapshot() -> str:
    """Compact current-memory string for the heartbeat: process RSS + system availability.

    Reads /proc (Linux — the collector's deploy target) with no extra dependency. Returns ''
    when unavailable (e.g. a non-Linux dev box) so the heartbeat never breaks on it. Format:
    'rss_mb=<proc RSS> avail_mb=<system MemAvailable> mem_used_pct=<system %>'. The OOM that
    killed the collector is exactly this class of signal, so it belongs in the constant log.
    """
    try:
        rss_kb = None
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    break
        if rss_kb is None:
            return ""
        total_kb = avail_kb = None
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail_kb = int(line.split()[1])
                if total_kb is not None and avail_kb is not None:
                    break
        parts = [f"rss_mb={rss_kb / 1024:.0f}"]
        if avail_kb is not None:
            parts.append(f"avail_mb={avail_kb / 1024:.0f}")
        if total_kb and avail_kb is not None:
            parts.append(f"mem_used_pct={100 * (total_kb - avail_kb) / total_kb:.0f}")
        return " ".join(parts)
    except Exception:
        return ""


def _vm_name() -> str:
    """The GCE instance name (matches the SF alerts), falling back to the hostname."""
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/name",
            headers={"Metadata-Flavor": "Google"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.read().decode().strip()
    except Exception:
        return socket.gethostname()


def _fmt_uptime(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60):02d}m"


class Alerter:
    def __init__(
        self,
        *,
        channel: str | None = None,
        cooldown_s: float = _DEFAULT_COOLDOWN_S,
        send: Callable[[str], None] | None = None,
        now: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        vm_name: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        # Channel is env-configurable (DFS_COLLECTOR_ALERT_CHANNEL); webhook SLACK_WEBHOOK_<CHANNEL>.
        channel = channel or os.environ.get("DFS_COLLECTOR_ALERT_CHANNEL") or ALERT_CHANNEL
        self._channel = channel
        self._cooldown = cooldown_s
        self._now = now
        self._wall = wall
        self._start_epoch = wall()  # when this collector instance started (for uptime)
        self._vm = vm_name or _vm_name()
        self._send = send or self._default_send
        self._active: dict[str, float] = {}  # firing key -> last-sent monotonic time
        if enabled is None:
            enabled = send is not None or bool(os.environ.get(f"SLACK_WEBHOOK_{channel.upper()}"))
        self._enabled = enabled

    def _default_send(self, text: str) -> None:
        from skyward.notifications import send_slack
        send_slack(self._channel, text)

    def _post(self, text: str) -> None:
        if not self._enabled:
            return
        try:
            self._send(text)
        except Exception as e:  # never let alerting break the collector
            print(f"[alerts] failed to send Slack alert: {e!r}")

    def _format(self, emoji: str, title: str, fields: dict | None = None) -> str:
        lines = [f"{emoji} *DFS Collector: {title}*", f"VM: {self._vm}"]
        for k, v in (fields or {}).items():
            lines.append(f"{k}: {v}")
        return "\n".join(lines)

    # ----- deduped failure alerts -----

    def fire(self, key: str, *, title: str, fields: dict | None = None, emoji: str = "⚠️") -> None:
        last = self._active.get(key)
        now = self._now()
        if last is not None and (now - last) < self._cooldown:
            return  # already firing and within cooldown — suppress
        self._active[key] = now
        self._post(self._format(emoji, title, fields))

    def resolve(self, key: str, *, title: str = "Recovered", fields: dict | None = None) -> None:
        if key in self._active:
            del self._active[key]
            self._post(self._format("✅", title, fields))

    def job_complete(self, *, job_id: str, endpoint: str, succeeded: int, failed: int) -> None:
        """One-shot 'Job Complete' alert (claimed via notified_at, so no dedup needed).

        ✅ when all tasks succeeded; ⚠️ when any failed, so degraded jobs stand out.
        """
        emoji = "⚠️" if failed else "✅"
        self._post(self._format(emoji, "Job Complete", {
            "Job ID": job_id,
            "Endpoint": endpoint,
            "Succeeded": succeeded,
            "Failed": failed,
        }))

    # ----- lifecycle (SF style) -----

    def startup(self) -> None:
        self._post(f"\U0001f7e2 *DFS Collector VM Started*\nVM: {self._vm}")

    def shutdown(self, reason: str = "signal") -> None:
        uptime = _fmt_uptime(self._wall() - self._start_epoch)
        self._post(
            f"\U0001f534 *DFS Collector VM Shutting Down*\nVM: {self._vm}\nUptime: {uptime}"
        )

    def crash(self, message: str) -> None:
        self._post(self._format("❌", "Service Crashed", {"Detail": message}))

    # ----- liveness -----

    def heartbeat(self) -> None:
        mem = _memory_snapshot()
        suffix = f" {mem}" if mem else ""
        print(f"[dfs-collector] heartbeat ts={int(time.time())} host={self._vm}{suffix}", flush=True)
