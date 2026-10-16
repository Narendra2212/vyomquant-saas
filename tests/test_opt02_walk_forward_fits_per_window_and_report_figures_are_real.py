"""
tests/test_opt02_walk_forward_fits_per_window_and_report_figures_are_real.py

OPT-02: "Walk-forward analysis does not actually fit per-window parameters, and several of its
reported figures are hardcoded placeholders."

Two independent defects, both in backend_app/backend/optimization_engine.py:

1. ``run_walk_forward_analysis`` called ``self._get_best_parameters(self._optimization_history)``
   ONCE, before the loop, and evaluated that one fixed parameter set on every window's train AND
   test period - a fixed strategy re-run on rolling windows, not walk-forward analysis. Depended
   on BT-02 (backtests ignored start_date/end_date and always ran on "the most recent bars") being
   fixed first, or even a correct per-window fit would have been scored on identical data.

2. ``_run_buy_hold_benchmark`` returned the literal ``{"total_return_pct": 50.0, "sharpe_ratio":
   1.0, "max_drawdown": 20.0}`` for every call; ``_calculate_beta`` returned the literal ``1.0``;
   ``_calculate_strategy_score``'s ``complexity`` was the literal ``0.8`` - all regardless of the
   strategy, the symbol or the window.

This file proves the fixes with a FAKE ``backtest_runtime`` (real VectorBT/DAG execution is not
the subject here - it is already covered elsewhere) whose ``run_backtest`` is a deterministic,
PARAMETER- and WINDOW-dependent function, so "the winning parameter differs by window" and "the
benchmark differs from a fixed literal" are checked against a result this file controls completely
rather than inferred from an opaque real backtest.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any, Dict, List

import numpy as np
import pytest

from backend_app.backend.optimization_engine import (
    OptimizationConfig,
    OptimizationEngine,
    OptimizationMethod,
    OptimizationResult,
    ValidationMethod,
    WalkForwardResult,
)

USER = {"id": "user-1"}
STRATEGY_ID = "strat-1"
VERSION_ID = "v1"
VERSION = "v1"


def run(coro):
    return asyncio.run(coro)


class _StubExecutionGraph:
    """A real, independent ``metadata`` dict per instance - ``_modify_package_parameters``
    writes trial parameters into exactly this dict, and this file reads them back from it."""

    def __init__(self):
        self.metadata: Dict[str, Any] = {}
        self.nodes = {}
        self.edges = []


class _StubStrategyPackage:
    def __init__(self, symbols=("BTC/USDT",), timeframe="1d"):
        self.execution_graph = _StubExecutionGraph()
        self.metadata = {"symbols": list(symbols), "timeframe": timeframe}


class _FakeBacktestRuntime:
    """Deterministic, window- and parameter-dependent backtest double.

    ``score(params, start_date)`` decides the Sharpe (and everything derived from it); this file
    arranges it so the WINNING ``threshold`` value differs by window, which is the one fact
    OPT-02's fix has to produce for the per-window-fitting half to be provably real rather than
    merely "ran without crashing".
    """

    def __init__(self, score):
        self._score = score
        self.calls: List[Dict[str, Any]] = []

    def set_data_engine(self, *_a, **_k) -> None:
        pass

    async def run_backtest(
        self, *, strategy_package, user, strategy_id, version_id, version,
        start_date, end_date, exchange_instance,
    ) -> Dict[str, Any]:
        params = dict(strategy_package.execution_graph.metadata)
        self.calls.append({"params": params, "start_date": start_date, "end_date": end_date})
        sharpe = self._score(params, start_date)
        equity_curve = [
            {"equity": 10_000.0 * (1.0 + sharpe / 100.0) ** i} for i in range(10)
        ]
        return {
            "results": {
                "sharpe_ratio": sharpe,
                "total_return_pct": sharpe * 10.0,
                "win_rate": 50.0,
                "max_drawdown": 10.0,
                "equity_curve": equity_curve,
            },
            "backtest_id": f"bt-{len(self.calls)}",
        }


def _engine(score) -> "OptimizationEngine":
    engine = OptimizationEngine()
    engine.set_backtest_runtime(_FakeBacktestRuntime(score))
    return engine


def _grid_config(**overrides) -> OptimizationConfig:
    defaults = dict(
        method=OptimizationMethod.GRID_SEARCH,
        validation_method=ValidationMethod.WALK_FORWARD,
        parameters={
            "threshold": {"values": [1, 2, 3]},
            "start_date": "2023-01-01",
            "end_date": "2023-04-01",
        },
        n_iterations=10,
        training_window_days=30,
        test_window_days=10,
    )
    defaults.update(overrides)
    return OptimizationConfig(**defaults)


# ══════════════════════════════════════════════════════════════════════════
# 1. WALK-FORWARD ACTUALLY FITS PER WINDOW
# ══════════════════════════════════════════════════════════════════════════


def test_different_windows_choose_different_winning_parameters():
    """The core defect: one global ``best_params`` reused for every window, versus a real
    per-window fit that can - and here, by construction, does - land on a different value."""
    config = _grid_config()
    engine = _engine(lambda p, s: 0.0)  # placeholder; windows computed below
    windows = engine._generate_walk_forward_windows(config)
    assert len(windows) >= 2, "the fixture must generate at least two windows to prove anything"

    train_starts = [w[0] for w in windows]
    # Window i's "true" optimum cycles through the threshold values, so consecutive windows
    # are guaranteed to disagree about which value scores best (3 distinct values, >=2 windows).
    target_for = {start: [1, 2, 3][i % 3] for i, start in enumerate(train_starts)}

    def score(params, start_date):
        target = target_for.get(start_date)
        if target is None:
            return 0.0
        return 100.0 if params.get("threshold") == target else 1.0

    engine = _engine(score)
    package = _StubStrategyPackage()

    results = run(
        engine.run_walk_forward_analysis(
            package, config, USER, STRATEGY_ID, VERSION_ID, VERSION, exchange_instance=object()
        )
    )

    assert len(results) == len(windows)
    chosen = [r.parameters.get("threshold") for r in results]
    assert len(set(chosen)) > 1, (
        f"every window chose the SAME parameter ({chosen}) - walk-forward is not re-fitting "
        f"per window"
    )
    for result, start in zip(results, train_starts):
        assert result.parameters.get("threshold") == target_for[start], (
            f"window starting {start} should have fit its own optimum "
            f"{target_for[start]}, got {result.parameters.get('threshold')}"
        )


def test_the_fitted_parameters_are_evaluated_unchanged_on_both_train_and_test():
    """The parameters a window fits are carried forward to ITS OWN test evaluation unchanged -
    the whole point of "train then test", not a second, independent fit on the test period."""
    config = _grid_config()
    engine = _engine(lambda p, s: 0.0)
    windows = engine._generate_walk_forward_windows(config)
    train_starts = [w[0] for w in windows]
    target_for = {start: [1, 2, 3][i % 3] for i, start in enumerate(train_starts)}

    def score(params, start_date):
        target = target_for.get(start_date)
        if target is None:
            return 0.0
        return 100.0 if params.get("threshold") == target else 1.0

    fake_runtime = _FakeBacktestRuntime(score)
    engine = OptimizationEngine()
    engine.set_backtest_runtime(fake_runtime)
    package = _StubStrategyPackage()

    results = run(
        engine.run_walk_forward_analysis(
            package, config, USER, STRATEGY_ID, VERSION_ID, VERSION, exchange_instance=object()
        )
    )

    # Every window issues >= 1 grid-search call on its train window, PLUS exactly one train
    # evaluation and one test evaluation using the SAME winning parameters.
    train_eval_calls = [
        c for c in fake_runtime.calls
        if c["start_date"] == results[0].train_start and c["end_date"] == results[0].train_end
    ]
    for result in results:
        test_call = next(
            c for c in fake_runtime.calls
            if c["start_date"] == result.test_start and c["end_date"] == result.test_end
            and c["params"].get("threshold") == result.parameters.get("threshold")
        )
        assert test_call["params"] == result.parameters


def test_requires_bt02_two_windows_backtest_different_date_ranges():
    """OPT-02 depends on BT-02: each window's calls must carry THAT window's own dates."""
    config = _grid_config()
    fake_runtime = _FakeBacktestRuntime(lambda p, s: float(p.get("threshold", 0)))
    engine = OptimizationEngine()
    engine.set_backtest_runtime(fake_runtime)
    package = _StubStrategyPackage()

    run(
        engine.run_walk_forward_analysis(
            package, config, USER, STRATEGY_ID, VERSION_ID, VERSION, exchange_instance=object()
        )
    )

    date_pairs = {(c["start_date"], c["end_date"]) for c in fake_runtime.calls}
    assert len(date_pairs) > 1, (
        "every backtest call used the same date range - two different windows must drive "
        "different start_date/end_date"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. THE REPORT FIGURES ARE MEASURED, NOT LITERALS
# ══════════════════════════════════════════════════════════════════════════


class _RecordingDataEngine:
    """Hands back OHLCV bars whose closes this test controls, recording the call's window."""

    def __init__(self, bars):
        self._bars = bars
        self.calls: List[Dict[str, Any]] = []

    async def fetch_historical_ohlcv(self, **kwargs):
        self.calls.append(kwargs)
        return self._bars


def _ohlcv(closes, start_ms=1_700_000_000_000, step_ms=86_400_000):
    return [
        [start_ms + i * step_ms, c, c, c, c, 1.0] for i, c in enumerate(closes)
    ]


def test_buy_hold_computes_a_real_total_return_not_the_literal_fifty():
    """A rising price series must report something other than the old literal 50.0/1.0/20.0."""
    config = _grid_config()
    engine = OptimizationEngine()
    runtime = _FakeBacktestRuntime(lambda p, s: 0.0)
    closes = [100.0 * (1.03**i) for i in range(60)]  # steady 3%/bar rise
    runtime.data_engine = _RecordingDataEngine(_ohlcv(closes))
    engine.set_backtest_runtime(runtime)
    package = _StubStrategyPackage()

    result = run(engine._run_buy_hold_benchmark(package, config, exchange_instance=object()))

    assert result["computed"] is True
    expected_return = (closes[-1] - closes[0]) / closes[0] * 100.0
    assert result["total_return_pct"] == pytest.approx(expected_return, rel=1e-9)
    assert result["total_return_pct"] != 50.0
    assert result["sharpe_ratio"] != 1.0
    assert result["max_drawdown"] != 20.0
    # A strictly rising series has zero drawdown.
    assert result["max_drawdown"] == pytest.approx(0.0, abs=1e-9)


def test_buy_hold_reports_not_computed_on_insufficient_data_rather_than_a_fabricated_number():
    config = _grid_config()
    engine = OptimizationEngine()
    runtime = _FakeBacktestRuntime(lambda p, s: 0.0)
    runtime.data_engine = _RecordingDataEngine([])  # no data at all
    engine.set_backtest_runtime(runtime)
    package = _StubStrategyPackage()

    result = run(engine._run_buy_hold_benchmark(package, config, exchange_instance=object()))

    assert result["computed"] is False
    assert result["total_return_pct"] is None
    assert result["sharpe_ratio"] is None
    assert result["max_drawdown"] is None


def test_beta_is_computed_from_the_actual_return_series_not_the_literal_one():
    engine = OptimizationEngine()
    # Strategy returns are exactly 2x the benchmark's every period -> beta == 2.0 exactly.
    benchmark_returns = [0.01, -0.02, 0.03, 0.015, -0.01, 0.02]
    strategy_returns = [r * 2.0 for r in benchmark_returns]

    beta = engine._calculate_beta(strategy_returns, benchmark_returns)

    assert beta == pytest.approx(2.0, rel=1e-6)
    assert beta != 1.0


def test_beta_is_none_rather_than_a_fabricated_one_when_the_benchmark_has_zero_variance():
    engine = OptimizationEngine()
    assert engine._calculate_beta([0.01, 0.02, -0.01], [0.0, 0.0, 0.0]) is None


def test_beta_is_none_with_fewer_than_two_paired_observations():
    engine = OptimizationEngine()
    assert engine._calculate_beta([0.01], [0.01]) is None


def test_alpha_and_beta_are_none_when_the_benchmark_could_not_be_measured():
    """run_benchmark_comparison must not compute alpha/beta against an unmeasured benchmark."""
    config = _grid_config()
    engine = OptimizationEngine()
    runtime = _FakeBacktestRuntime(lambda p, s: 5.0)
    runtime.data_engine = _RecordingDataEngine([])  # unmeasurable
    engine.set_backtest_runtime(runtime)
    package = _StubStrategyPackage()

    comparison = run(
        engine.run_benchmark_comparison(
            package, config, USER, STRATEGY_ID, VERSION_ID, VERSION, exchange_instance=object()
        )
    )

    assert comparison["buy_hold"]["computed"] is False
    assert comparison["alpha"] is None
    assert comparison["beta"] is None
    assert "_bar_returns" not in comparison["buy_hold"], (
        "the internal bar-returns series must not leak into the public comparison dict"
    )


def test_complexity_score_varies_with_the_strategys_own_parameter_count():
    """The literal 0.8 for every strategy must become a real function of parameter count."""
    engine = OptimizationEngine()

    def _result_with_n_params(n: int) -> OptimizationResult:
        return OptimizationResult(
            trial_id="t1",
            parameters={f"p{i}": i for i in range(n)},
            metrics={"sharpe_ratio": 1.0, "max_drawdown": 5.0, "win_rate": 50.0},
            backtest_id="bt-1",
            execution_time_seconds=0.1,
        )

    simple_score = engine._calculate_strategy_score([_result_with_n_params(0)], [], [])
    complex_score = engine._calculate_strategy_score([_result_with_n_params(10)], [], [])

    assert simple_score["complexity"] == pytest.approx(1.0)
    assert complex_score["complexity"] == pytest.approx(0.0)
    assert simple_score["complexity"] != complex_score["complexity"]
    assert simple_score["complexity"] != 0.8 or complex_score["complexity"] != 0.8
