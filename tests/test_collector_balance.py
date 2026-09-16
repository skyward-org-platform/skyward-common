from datetime import datetime, timezone

import pytest

from skyward.data.dataforseo.collector.alerts import Alerter
from skyward.data.dataforseo.collector.balance import BalanceMonitor
from skyward.data.dataforseo.collector import service


class _Client:
    def __init__(self, balances):
        self.balances = list(balances)

    def get_balance(self):
        value = self.balances.pop(0)
        if value is None:
            return {"balance": 0.0, "total": 0.0, "raw": {}}
        return {"balance": value, "total": 0.0, "raw": {"balance": value}}


def _monitor(balances, clock, msgs, **kw):
    alerter = Alerter(send=msgs.append, now=lambda: 0.0, vm_name="vm")
    return BalanceMonitor(
        _Client(balances), alerter, warn_usd=50.0, critical_usd=10.0, interval_s=3600,
        now=lambda: clock[0], wall=lambda tz=None: datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
        **kw,
    )


def test_alerts_once_per_crossing_and_recovers():
    clock, msgs = [0.0], []
    m = _monitor([100, 40, 35, 8, 30, 60], clock, msgs)
    levels = []
    for _ in range(6):
        levels.append(m.check())
        clock[0] += 3600
    assert levels == ["ok", "warn", "warn", "critical", "warn", "ok"]
    assert len(msgs) == 3
    assert "DataForSEO Balance Warn" in msgs[0] and "$40.00" in msgs[0] and "$50.00" in msgs[0]
    assert "DataForSEO Balance Critical" in msgs[1] and "$10.00" in msgs[1]
    assert "DataForSEO Balance Recovered" in msgs[2]
    assert "2026-09-15 12:00 UTC" in msgs[0]


def test_respects_interval():
    clock, msgs = [0.0], []
    m = _monitor([5, 5], clock, msgs)
    assert m.check() == "critical"
    clock[0] += 60
    assert m.check() is None
    assert len(msgs) == 1


def test_unreadable_balance_is_skipped():
    clock, msgs = [0.0], []
    m = _monitor([None], clock, msgs)
    assert m.check() is None and msgs == []


def test_rejects_inverted_thresholds():
    with pytest.raises(ValueError):
        BalanceMonitor(_Client([]), Alerter(send=lambda t: None, now=lambda: 0.0, vm_name="v"),
                       warn_usd=10.0, critical_usd=50.0)


def test_from_env():
    alerter = Alerter(send=lambda t: None, now=lambda: 0.0, vm_name="v")
    assert BalanceMonitor.from_env(_Client([]), alerter, env={}) is None
    m = BalanceMonitor.from_env(_Client([]), alerter, env={
        "DFS_COLLECTOR_BALANCE_WARN_USD": "50", "DFS_COLLECTOR_BALANCE_CRITICAL_USD": "10"})
    assert isinstance(m, BalanceMonitor)


def test_run_forever_checks_balance_each_loop():
    calls = {"n": 0}

    class _Monitor:
        def check(self):
            calls["n"] += 1
            raise RuntimeError("never kills the loop")

    class _Al:
        def fire(self, *a, **k): pass
        def resolve(self, *a, **k): pass
        def heartbeat(self): pass

    service.run_forever(None, None, {}, alerter=_Al(), poll_interval=0, max_cycles=2,
                        sleep=lambda s: None, balance_monitor=_Monitor())
    assert calls["n"] == 2
