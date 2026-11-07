"""BT-03 — a failed backtest must be stored as failed, not as a completed run with no results.

`BacktestRuntime.run_backtest`'s failure path calls `update_backtest_results(results={"status":
"failed", "error": ...})`. That method ignored both keys: it wrote status='completed' and NULLed every
metric, so every failed run looked like a finished one (production: three 'completed' rows with no
trades, return or equity curve, all of them the failed runs).
"""
from __future__ import annotations

import pytest

from tests.test_backtest_evidence_columns_regression import (
    BAR_COUNT,
    STRATEGY_ID,
    TABLE,
    USER,
    USER_ID,
    VERSION_ID,
    _Supabase,
    _definition,
    _migration_006_columns,
    _results,
    _service,
)


async def _created(service):
    return (
        await service.create_backtest(
            user=USER, strategy_id=STRATEGY_ID, version="v1",
            blueprint={"schema_version": "2.0", "metadata": {"dag_hash": "cafebabe12345678"}},
            dataset="BTC/USDT", start_date="2024-01-01", end_date="2024-06-30",
            initial_capital=10_000.0, commission=0.001, slippage=0.0005, version_id=VERSION_ID,
        )
    )["id"]


@pytest.mark.asyncio
async def test_a_failed_run_is_stored_as_failed_with_its_reason_and_no_invented_metrics():
    sb = _Supabase(_definition("A") | _migration_006_columns())
    service = _service(sb)
    backtest_id = await _created(service)

    await service.update_backtest_results(
        user=USER, backtest_id=backtest_id,
        results={
            "status": "failed",
            "error": "Node 'n-2': port 'series' holds 43200 values for 8640 bars of market data.",
            "execution_time_seconds": 2.3,
        },
    )

    row = await service.get_backtest(USER, backtest_id)
    assert row["status"] == "failed", "a failed run was recorded as completed"
    assert "43200 values" in row["error"]
    assert row["error_message"] == row["error"]
    assert row["completed_at"] is not None
    assert row["execution_time_seconds"] == 2.3
    # No metric was invented for a run that produced none.
    for column in ("total_return", "win_rate", "sharpe_ratio", "final_capital"):
        assert row.get(column) is None, f"{column} holds an invented value after a failed run"


@pytest.mark.asyncio
async def test_a_failure_does_not_erase_a_previous_measurement():
    sb = _Supabase(_definition("A") | _migration_006_columns())
    service = _service(sb)
    backtest_id = await _created(service)
    await service.update_backtest_results(
        user=USER, backtest_id=backtest_id, results=_results(), executed_bar_count=BAR_COUNT
    )
    await service.update_backtest_results(
        user=USER, backtest_id=backtest_id, results={"status": "failed", "error": "late failure"}
    )
    row = await service.get_backtest(USER, backtest_id)
    assert row["status"] == "failed"
    assert row["final_capital"] == 11234.5, "a failure write erased the stored result"


@pytest.mark.asyncio
async def test_a_successful_run_is_still_completed():
    sb = _Supabase(_definition("A") | _migration_006_columns())
    service = _service(sb)
    backtest_id = await _created(service)
    await service.update_backtest_results(
        user=USER, backtest_id=backtest_id, results=_results(), executed_bar_count=BAR_COUNT
    )
    row = await service.get_backtest(USER, backtest_id)
    assert row["status"] == "completed"
    assert row["final_capital"] == 11234.5
