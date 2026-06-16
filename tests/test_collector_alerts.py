"""Alerter: dedup/cooldown, recovery, SF-style lifecycle messages, fail-safe send, classify."""

from __future__ import annotations

from skyward.data.dataforseo.collector.alerts import Alerter, classify_failure


def _alerter(clock, **kw):
    msgs: list[str] = []
    a = Alerter(send=msgs.append, now=lambda: clock[0], vm_name="testvm", cooldown_s=900, **kw)
    return a, msgs


def test_fire_alerts_once_then_suppresses_within_cooldown():
    clock = [0.0]
    a, msgs = _alerter(clock)
    a.fire("dfs", title="DataForSEO Unreachable", fields={"Endpoint": "serp"})
    a.fire("dfs", title="DataForSEO Unreachable")  # within cooldown -> suppressed
    assert len(msgs) == 1
    m = msgs[0]
    assert "⚠️" in m and "*DFS Collector: DataForSEO Unreachable*" in m
    assert "VM: testvm" in m and "Endpoint: serp" in m


def test_fire_realerts_after_cooldown():
    clock = [0.0]
    a, msgs = _alerter(clock)
    a.fire("dfs", title="x")
    clock[0] = 901
    a.fire("dfs", title="x")
    assert len(msgs) == 2


def test_resolve_sends_all_clear_once():
    clock = [0.0]
    a, msgs = _alerter(clock)
    a.fire("dfs", title="x")
    a.resolve("dfs", title="DataForSEO Reachable Again")
    assert len(msgs) == 2 and "✅" in msgs[1] and "Reachable Again" in msgs[1]
    a.resolve("dfs")  # not firing -> no-op
    assert len(msgs) == 2


def test_resolve_without_fire_is_noop():
    clock = [0.0]
    a, msgs = _alerter(clock)
    a.resolve("never-fired")
    assert msgs == []


def test_lifecycle_messages_sf_style():
    clock = [0.0]
    a, msgs = _alerter(clock)
    a.startup()
    a.shutdown("SIGTERM")
    a.crash("traceback...")
    assert any("🟢" in m and "VM Started" in m for m in msgs)
    assert any("🔴" in m and "Shutting Down" in m and "Uptime:" in m for m in msgs)
    assert any("❌" in m and "Service Crashed" in m for m in msgs)
    assert all("VM: testvm" in m for m in msgs)


def test_disabled_is_noop():
    a = Alerter(now=lambda: 0.0, vm_name="h", enabled=False)  # no send, no webhook
    a.fire("k", title="x")
    a.startup()  # must not raise / not attempt Slack


def test_post_swallows_send_errors():
    def boom(_text):
        raise RuntimeError("slack down")

    a = Alerter(send=boom, now=lambda: 0.0, vm_name="h")
    a.fire("k", title="x")  # must not raise


def test_channel_is_env_configurable(monkeypatch):
    monkeypatch.setenv("DFS_COLLECTOR_ALERT_CHANNEL", "ops_testing")
    a = Alerter(send=lambda _t: None, now=lambda: 0.0, vm_name="h")
    assert a._channel == "ops_testing"
    b = Alerter(channel="explicit", send=lambda _t: None, now=lambda: 0.0, vm_name="h")
    assert b._channel == "explicit"


def test_job_complete_success_vs_degraded():
    clock = [0.0]
    a, msgs = _alerter(clock)
    a.job_complete(job_id="j1", endpoint="serp_google_organic", succeeded=10, failed=0)
    a.job_complete(job_id="j2", endpoint="serp_google_organic", succeeded=8, failed=2)
    assert "✅" in msgs[0] and "Job Complete" in msgs[0]
    assert "Job ID: j1" in msgs[0] and "Succeeded: 10" in msgs[0] and "Failed: 0" in msgs[0]
    assert "⚠️" in msgs[1] and "Failed: 2" in msgs[1]  # any failure -> degraded


def test_classify_failure():
    from google.api_core import exceptions as gexc
    assert classify_failure(gexc.ServiceUnavailable("x"))[0] == "bigquery"
    assert classify_failure(ValueError("x"))[0] == "error"
