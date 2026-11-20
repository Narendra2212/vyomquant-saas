"""DASH-01 / DASH-02: the Dashboard read nothing on the production client, and called every
strategy active.

DASH-01. ``_execute_sb_query`` sent ``query.execute`` to ``asyncio.to_thread``. On the async
request-scoped client (``_PooledAsyncPostgrestClient``) the builder is not awaitable and
``execute`` is a coroutine function, so the thread returned an un-awaited coroutine with no
``.data`` and every dashboard read - strategies, signals, exchange keys, notifications, risk,
referrals, marketplace, usage - silently answered "nothing" (production, 2026-11-13).

DASH-02. Status came from ``is_active``, which defaults to TRUE (192 of 192 production rows).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend_app.backend import dashboard_aggregation_service as das


class _AsyncBuilder:
    """The production builder's shape: not awaitable, ``execute`` is ``async def``."""

    def __init__(self, rows):
        self.rows = rows

    def select(self, *a):
        return self

    def eq(self, *a):
        return self

    async def execute(self):
        return SimpleNamespace(data=list(self.rows))


class _SyncBuilder(_AsyncBuilder):
    def execute(self):  # noqa: D401 - the sync client's shape
        return SimpleNamespace(data=list(self.rows))


def _run(coro):
    return asyncio.run(coro)


def test_an_async_execute_is_awaited_not_leaked_as_a_coroutine():
    res = _run(das.DashboardAggregationService()._execute_sb_query(_AsyncBuilder([{"id": 1}])))
    assert res.data == [{"id": 1}]


def test_a_sync_execute_still_runs_off_the_event_loop():
    res = _run(das.DashboardAggregationService()._execute_sb_query(_SyncBuilder([{"id": 2}])))
    assert res.data == [{"id": 2}]


def _strategies(rows):
    class _SB:
        def table(self, name):
            assert name == "strategies"
            return _AsyncBuilder(rows)

    return _run(das.DashboardAggregationService().get_strategies({"id": "u"}, sb=_SB()))


def test_the_dashboard_lists_strategies_on_the_async_client():
    out = _strategies([{"id": "a", "name": "A", "status": "running"}])
    assert [s["id"] for s in out] == ["a"]


def test_status_comes_from_the_run_state_not_is_active():
    out = _strategies([
        {"id": "r", "status": "running", "is_active": True},
        {"id": "p", "status": "paused", "is_active": True},
        {"id": "d", "status": "draft", "is_active": True},
        {"id": "s", "status": "stopped", "is_active": True},
        {"id": "n", "status": None, "is_active": True},
    ])
    assert {s["id"]: s["status"] for s in out} == {
        "r": "active", "p": "paused", "d": "idle", "s": "idle", "n": "idle"}


def test_the_paused_insight_counts_paused_strategies_only():
    async def go():
        svc = das.DashboardAggregationService()

        async def strategies(user, **k):
            return [{"name": "Draft", "status": "idle"}, {"name": "Held", "status": "paused"}]

        svc.get_strategies = strategies
        return await svc.get_strategy_insights({"id": "u"})

    insights = _run(go())
    paused = [i for i in insights if i["id"] == "ins_1"]
    assert paused and paused[0]["text"].startswith("1 strategy(ies) currently paused: Held")
