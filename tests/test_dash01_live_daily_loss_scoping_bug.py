"""DASH-01 -- the live daily-loss figure no longer silently reads zero.

THE DEFECT
    ``DashboardAggregationService.get_risk_data``'s ``environment == "paper"`` branch did
    ``from datetime import timezone`` INSIDE itself. Python binds an import anywhere in a
    function body to that function's local scope for the WHOLE function body, not just the
    branch it appears in -- so when ``environment != "paper"`` (the live branch), the `else`
    arm's ``datetime.now(timezone.utc)`` referenced a name that line never executed an
    import for, raising ``UnboundLocalError``. That was caught by the branch's own bare
    ``except Exception: daily_loss_utilized = 0.0`` and silently reported as "no loss today"
    for every single live dashboard read -- the safest-looking, and therefore most
    dangerous, wrong answer a risk figure can report (``risk_score`` weights it 60%).

THE FIX
    ``from datetime import timezone`` moved to this module's top-level imports (alongside
    the pre-existing ``from datetime import datetime``); the three other call sites that
    already did an unconditional, top-of-function local import of the same name had that
    import removed as newly-redundant.

WHAT THIS FILE PROVES (a fake TelemetryEngine; no real QuestDB)
    * the live branch's QuestDB query actually executes and its result is read, rather than
      silently swallowed by the (now impossible) UnboundLocalError;
    * a negative-pnl dataset produces a non-zero ``daily_loss_utilized`` and a correspondingly
      non-zero contribution to ``risk_score`` -- not the "safe" zero the bug always produced;
    * the sibling paper branch (never broken) still works, proving this is an additive fix,
      not a behavior change to the branch that was already correct.
"""
from __future__ import annotations

import asyncio

import pytest

from backend_app.backend.dashboard_aggregation_service import \
    DashboardAggregationService

UID = "dash01testuser00000000000000"


class _FakeTelemetry:
    def __init__(self, dataset):
        self._dataset = dataset
        self.queries = []

    async def execute_query(self, query):
        self.queries.append(query)
        return {"dataset": self._dataset}


def _service_with_telemetry(dataset):
    svc = DashboardAggregationService()
    svc._telemetry = _FakeTelemetry(dataset)
    return svc


def _user():
    return {"id": UID, "access_token": "t"}


class TestLiveDailyLossReadsTheRealFigure:
    def test_a_negative_pnl_reading_is_not_swallowed_to_zero(self):
        svc = _service_with_telemetry([[-250.0]])

        result = asyncio.run(
            svc.get_risk_data(_user(), environment="live", positions=[])
        )

        assert result["daily_loss_utilized"] == 250.0, (
            "the old bug's UnboundLocalError, caught by a bare except, always produced "
            "0.0 here regardless of what the telemetry read -- this pins that the real "
            "figure now reaches the response"
        )
        assert result["risk_score"] is not None and result["risk_score"] > 0

    def test_the_live_branch_actually_queries_telemetry(self):
        svc = _service_with_telemetry([[-100.0]])
        fake = svc._telemetry

        asyncio.run(svc.get_risk_data(_user(), environment="live", positions=[]))

        assert len(fake.queries) == 1
        assert "executions" in fake.queries[0]
        assert UID in fake.queries[0]

    def test_a_query_failure_is_still_an_honest_zero_not_a_crash(self):
        class _BoomTelemetry:
            async def execute_query(self, query):
                raise RuntimeError("questdb unreachable")

        svc = DashboardAggregationService()
        svc._telemetry = _BoomTelemetry()

        result = asyncio.run(
            svc.get_risk_data(_user(), environment="live", positions=[])
        )
        assert result["daily_loss_utilized"] == 0.0

    def test_the_paper_branch_still_works_unchanged(self):
        svc = DashboardAggregationService()

        class _FakePaperService:
            def get_trades(self, uid):
                return []

        import unittest.mock as mock

        import backend_app.backend.paper_trading_service as pts

        with mock.patch.object(pts, "get_paper_trading_service", lambda: _FakePaperService()):
            result = asyncio.run(
                svc.get_risk_data(_user(), environment="paper", positions=[])
            )

        assert result["daily_loss_utilized"] == 0.0
