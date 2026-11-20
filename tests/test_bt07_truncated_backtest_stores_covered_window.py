"""BT-07: a backtest that did not cover its requested window is stored as what it covered.

Production, 2026-11-11: a 1m backtest requested for 2025-07-01..2025-09-30 simulated the
10,000-bar cap (~7 days) and the row kept the requested quarter - which the Marketplace
evidence gate (>= 90-day window) reads as fact.
"""

from __future__ import annotations

import inspect

import pytest

from backend_app.backend import backtest_runtime
from tests.test_backtest_evidence_columns_regression import (
    BAR_COUNT,
    STRATEGY_ID,
    USER,
    VERSION_ID,
    _definition,
    _migration_006_columns,
    _results,
    _service,
    _Supabase,
)


async def _created(service):
    return (
        await service.create_backtest(
            user=USER, strategy_id=STRATEGY_ID, version="v1",
            blueprint={"schema_version": "2.0", "metadata": {"dag_hash": "cafebabe12345678"}},
            dataset="BTC/USDT", start_date="2025-07-01", end_date="2025-09-30",
            initial_capital=10_000.0, commission=0.001, slippage=0.0005, version_id=VERSION_ID,
        )
    )["id"]


@pytest.mark.asyncio
async def test_a_truncated_run_is_stored_with_the_dates_it_simulated():
    service = _service(_Supabase(_definition("A") | _migration_006_columns()))
    backtest_id = await _created(service)
    await service.update_backtest_results(
        user=USER, backtest_id=backtest_id, results=_results(), executed_bar_count=BAR_COUNT,
        covered_window=("2025-07-01T00:00:00Z", "2025-07-07T22:39:00Z"),
    )
    row = await service.get_backtest(USER, backtest_id)
    assert str(row["start_date"]).startswith("2025-07-01")
    assert str(row["end_date"]).startswith("2025-07-07"), "the row still claims the full quarter"


@pytest.mark.asyncio
async def test_a_full_coverage_run_keeps_its_requested_dates():
    service = _service(_Supabase(_definition("A") | _migration_006_columns()))
    backtest_id = await _created(service)
    await service.update_backtest_results(
        user=USER, backtest_id=backtest_id, results=_results(), executed_bar_count=BAR_COUNT,
    )
    row = await service.get_backtest(USER, backtest_id)
    assert str(row["end_date"]).startswith("2025-09-30")


def test_the_runtime_passes_the_covered_window_only_when_coverage_fell_short():
    src = inspect.getsource(backtest_runtime.BacktestRuntime)
    assert "covered_window=" in src and "truncated_by_cap" in src and "starts_after_request" in src
