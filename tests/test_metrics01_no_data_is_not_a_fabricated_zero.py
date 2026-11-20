"""METRICS-01 -- the one QuestDB-sourced metrics method that fabricated zeros now reports
"not available" instead.

THE DEFECT
    Six strategy-performance endpoints (performance, equity-curve, monthly-returns,
    daily-returns, execution-metrics, risk-metrics) read ``strategy_performance``/
    ``strategy_risk``/``executions`` tables in QuestDB through ``MetricsService``. Those
    tables have no writer anywhere in this codebase (confirmed by grep: nothing in
    ``backend_app/`` ever INSERTs into them) and no schema definition either, so every one
    of these calls returns empty today. Five of the six methods already handled that
    honestly (``get_equity_curve``/``get_monthly_returns``/``get_daily_returns`` return
    ``[]``; ``get_execution_metrics``/``get_risk_metrics`` return ``{}``) -- only
    ``get_strategy_performance``'s ``_empty_performance_metrics()`` fabricated a full
    dict of ``0.0``/``0`` values, indistinguishable from a strategy that genuinely traded
    and broke exactly even.

THE FIX
    Every QuestDB-sourced numeric field in ``_empty_performance_metrics()`` is now
    ``None``. ``strategy_id``/``timestamp``/``time_range``/``realtime`` are untouched --
    they were already honest (``None``/``{}}``) or are request echoes, not data.

WHAT THIS FILE PROVES
    * the empty-performance structure reports every metric as ``None``, not ``0``;
    * a genuinely zero value survives unchanged when real data IS present -- this fix
      must not turn an honest 0 into "unavailable";
    * the other five MetricsService methods are unchanged (still honestly empty, not
      fabricated) -- a regression guard, not a re-verification of their own fix.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from backend_app.backend.metrics_service import MetricsService


def _run(coro):
    return asyncio.run(coro)


class _NoDataTelemetry:
    async def execute_query(self, query, *args, **kwargs):
        return {"dataset": [], "columns": []}


class _RowTelemetry:
    def __init__(self, columns, row):
        self._columns = columns
        self._row = row

    async def execute_query(self, query, *args, **kwargs):
        return {
            "columns": [{"name": c} for c in self._columns],
            "dataset": [self._row],
        }


class TestEmptyPerformanceMetricsReportsUnavailableNotZero:
    def test_every_numeric_field_is_none_when_nothing_was_written(self):
        service = MetricsService()
        service._telemetry = _NoDataTelemetry()

        result = _run(service.get_strategy_performance(user_id="u1", strategy_id="s1"))

        numeric_fields = [
            "today_pnl", "today_return_pct", "lifetime_pnl", "unrealized_pnl",
            "available_balance", "total_equity", "roi_pct", "win_rate", "sharpe_ratio",
            "sortino_ratio", "profit_factor", "max_drawdown", "total_trades",
            "winning_trades", "losing_trades", "avg_trade", "avg_win", "avg_loss",
            "position_count", "total_volume",
        ]
        for field in numeric_fields:
            assert result[field] is None, f"{field} is {result[field]!r}, not None"

    def test_a_connection_error_also_reports_unavailable_not_zero(self):
        """NEGATIVE CONTROL. A QuestDB outage must read the same as "no data", not a
        fabricated clean sheet of zeros that looks like a strategy which broke even.
        """
        class _FailingTelemetry:
            async def execute_query(self, *a, **k):
                raise ConnectionError("questdb unreachable")

        service = MetricsService()
        service._telemetry = _FailingTelemetry()

        result = _run(service.get_strategy_performance(user_id="u1", strategy_id="s1"))

        assert result["today_pnl"] is None
        assert result["win_rate"] is None

    def test_a_genuine_zero_in_real_data_is_not_converted_to_unavailable(self):
        """A strategy that really did break exactly even must still show 0, not
        "unavailable" -- this fix must not swallow real data.
        """
        columns = [
            "timestamp", "strategy_id", "total_equity", "total_pnl", "pnl_pct",
            "available_balance", "unrealized_pnl", "position_count", "total_trades",
            "winning_trades", "losing_trades", "total_volume", "avg_trade_size",
            "max_drawdown", "sharpe_ratio", "sortino_ratio", "profit_factor",
            "avg_win", "avg_loss", "win_rate",
        ]
        row = [
            "2026-01-01T00:00:00Z", "s1", 1000.0, 0.0, 0.0,
            1000.0, 0.0, 0, 4,
            0, 0, 100.0, 25.0,
            0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0,
        ]
        service = MetricsService()
        service._telemetry = _RowTelemetry(columns, row)

        result = _run(service.get_strategy_performance(user_id="u1", strategy_id="s1"))

        assert result["today_pnl"] == 0.0
        assert result["total_trades"] == 4


class TestTheOtherFiveMethodsAreUnchanged:
    """Regression guard: this fix must not touch the five methods that were already honest."""

    def test_equity_curve_and_monthly_and_daily_returns_report_an_empty_list(self):
        service = MetricsService()
        service._telemetry = _NoDataTelemetry()

        assert _run(service.get_equity_curve(user_id="u1", strategy_id="s1")) == []
        assert _run(service.get_monthly_returns(user_id="u1", strategy_id="s1")) == []
        assert _run(service.get_daily_returns(user_id="u1", strategy_id="s1")) == []

    def test_execution_and_risk_metrics_report_an_empty_dict(self):
        service = MetricsService()
        service._telemetry = _NoDataTelemetry()

        assert _run(service.get_execution_metrics(user_id="u1", strategy_id="s1")) == {}
        assert _run(service.get_risk_metrics(user_id="u1", strategy_id="s1")) == {}
