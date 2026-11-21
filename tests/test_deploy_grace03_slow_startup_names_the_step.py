"""DEPLOY-GRACE-03 -- the slow-startup report names the slow step.

Production 2026-11-20/21: 101 slow-startup events (69-365 s), many at ~365 s, carrying only
the total. main.lifespan now times each step; the report must carry the breakdown.
"""
from __future__ import annotations

import ast
import pathlib

from backend_app.core import startup_timing


def test_the_report_names_the_slowest_step_and_carries_every_step(monkeypatch):
    import sentry_sdk
    sent, extras, tags = [], {}, {}

    class _Scope:
        def set_extra(self, k, v): extras[k] = v
        def set_tag(self, k, v): tags[k] = v
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(sentry_sdk, "new_scope", lambda: _Scope())
    monkeypatch.setattr(sentry_sdk, "capture_message", lambda msg, level=None: sent.append(msg))

    startup_timing.report_slow_startup(
        365.2, {"supabase": 0.4, "redis": 360.1, "order_watchdog": 2.0})

    assert sent and "slowest: redis 360.1s" in sent[0]
    assert tags["startup_slowest_step"] == "redis"
    assert extras == {"step_supabase": 0.4, "step_redis": 360.1, "step_order_watchdog": 2.0}


def test_reporting_never_raises(monkeypatch):
    import sentry_sdk

    def _boom(*a, **k):
        raise RuntimeError("sentry down")

    monkeypatch.setattr(sentry_sdk, "new_scope", _boom)
    startup_timing.report_slow_startup(100.0, {"x": 1.0})  # must not raise


def test_lifespan_times_every_awaited_startup_step():
    """Every service start awaited in lifespan's startup is followed by a _mark()."""
    src = pathlib.Path("backend_app/main.py").read_text()
    body = src[src.index("async def lifespan"):src.index("yield  #  Server is live")]
    marks = [n.args[0].value for n in ast.walk(ast.parse("if 1:\n" + "\n".join(
        "    " + line for line in body.splitlines())))
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_mark"]
    for step in ("startup_recovery", "redis", "questdb_pool", "alert_engine", "order_watchdog",
                 "paper_session_reconciliation", "asset_universe_refresher",
                 "websocket_streamer"):
        assert step in marks, step
    assert len(marks) == len(set(marks)), "a step name is used twice"
    assert "report_slow_startup(" in body
