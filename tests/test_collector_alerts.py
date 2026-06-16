"""Alerter: dedup/cooldown, recovery, lifecycle messages, fail-safe send, classification."""

from __future__ import annotations

from skyward.data.dataforseo.collector.alerts import Alerter, classify_failure


def _alerter(clock, **kw):
    msgs: list[str] = []
    a = Alerter(send=msgs.append, now=lambda: clock[0], hostname="testhost", cooldown_s=900, **kw)
    return a, msgs


def test_fire_alerts_once_then_suppresses_within_cooldown():
    clock = [0.0]
    a, msgs = _alerter(clock)
    a.fire("dfs", "DFS unreachable")
    a.fire("dfs", "DFS unreachable")  # within cooldown -> suppressed
    assert len(msgs) == 1
    assert "DFS unreachable" in msgs[0] and "⚠️" in msgs[0] and "testhost" in msgs[0]


def test_fire_realerts_after_cooldown():
    clock = [0.0]
    a, msgs = _alerter(clock)
    a.fire("dfs", "x")
    clock[0] = 901
    a.fire("dfs", "x")
    assert len(msgs) == 2


def test_resolve_sends_all_clear_once():
    clock = [0.0]
    a, msgs = _alerter(clock)
    a.fire("dfs", "x")
    a.resolve("dfs", "DFS recovered")
    assert len(msgs) == 2 and "✅" in msgs[1]
    a.resolve("dfs")  # not firing -> no-op
    assert len(msgs) == 2


def test_resolve_without_fire_is_noop():
    clock = [0.0]
    a, msgs = _alerter(clock)
    a.resolve("never-fired")
    assert msgs == []


def test_lifecycle_messages():
    clock = [0.0]
    a, msgs = _alerter(clock)
    a.startup()
    a.shutdown("SIGTERM")
    a.crash("service FAILED")
    assert any("🟢" in m for m in msgs)
    assert any("🔻" in m and "SIGTERM" in m for m in msgs)
    assert any("❌" in m for m in msgs)


def test_disabled_is_noop():
    a = Alerter(now=lambda: 0.0, hostname="h", enabled=False)  # no send, no webhook
    a.fire("k", "x")
    a.startup()  # must not raise / not attempt Slack


def test_post_swallows_send_errors():
    def boom(_text):
        raise RuntimeError("slack down")

    a = Alerter(send=boom, now=lambda: 0.0, hostname="h")
    a.fire("k", "x")  # must not raise


def test_channel_is_env_configurable(monkeypatch):
    monkeypatch.setenv("DFS_COLLECTOR_ALERT_CHANNEL", "ops_alerts_test")
    a = Alerter(send=lambda _t: None, now=lambda: 0.0, hostname="h")
    assert a._channel == "ops_alerts_test"
    # explicit arg still wins over env
    b = Alerter(channel="explicit", send=lambda _t: None, now=lambda: 0.0, hostname="h")
    assert b._channel == "explicit"


def test_classify_failure():
    from google.api_core import exceptions as gexc
    assert classify_failure(gexc.ServiceUnavailable("x"))[0] == "bigquery"
    assert classify_failure(ValueError("x"))[0] == "error"
