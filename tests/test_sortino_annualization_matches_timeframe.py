"""The Sortino ratio must be annualized by the bar size it was computed from.

WHAT THIS GUARDS
----------------
``BacktestRuntime._calculate_performance_metrics`` computed::

    metrics["sortino_ratio"] = (returns.mean() / downside_returns.std()) * (252 ** 0.5)

252 is the number of trading days in an equities year — the wrong constant for every
timeframe this platform accepts except daily, and wrong even for daily since crypto has
no weekends. This is exactly the defect that was already found and fixed on the Sharpe
ratio in the sibling file ``backtesting_engine.py`` (see
``tests/test_sharpe_annualization_matches_timeframe.py``), but it was never applied to
Sortino here — and ``sortino_ratio`` is in ``RUNTIME_OWNED_METRICS``, so this runtime
computation (not the engine's own VectorBT Sortino) is the value that wins
``_merge_contested_metrics`` on every run and is what a trader actually sees.

On the platform's default 5-minute timeframe the true annualization factor is
``sqrt(105120) ~ 324`` versus the hardcoded ``sqrt(252) ~ 15.9`` — an understatement of
about 20x.
"""

import math

import pandas as pd
import pytest

from backend_app.backend.backtest_runtime import BacktestRuntime
from backend_app.backend.backtesting_engine import BARS_PER_YEAR, annualization_factor


def _runtime():
    # The constructor only wires up collaborators (engine, risk engine, DAG engine,
    # service); none of them are touched by `_calculate_performance_metrics`, which is a
    # pure function of its arguments once an instance exists.
    return BacktestRuntime()


def _equity_curve_with_losses(n: int = 60):
    """A synthetic equity curve with enough downside bars for a non-zero Sortino."""
    curve = []
    equity = 10_000.0
    for i in range(n):
        # Alternating small gains and slightly larger losses, so downside_returns.std() > 0.
        equity *= 1.01 if i % 3 != 0 else 0.985
        curve.append(equity)
    return curve


@pytest.mark.parametrize("timeframe", ["5m", "1h", "1d"])
def test_sortino_is_annualized_by_the_actual_timeframe(timeframe):
    runtime = _runtime()
    equity_curve = _equity_curve_with_losses()
    price_data = pd.DataFrame({"close": [1.0] * len(equity_curve)})

    metrics = runtime._calculate_performance_metrics(
        stats={}, equity_curve=equity_curve, price_data=price_data, timeframe=timeframe
    )

    returns = pd.Series(equity_curve).pct_change().dropna()
    downside = returns[returns < 0]
    expected = (returns.mean() / downside.std()) * annualization_factor(timeframe)

    assert metrics["sortino_ratio"] == pytest.approx(expected, rel=1e-9)


def test_sortino_on_5m_is_not_the_old_daily_constant():
    """Pins the headline bug: 5-minute Sortino must differ from the equities-252 figure
    by (approximately) the documented ~20x factor, not match it."""
    runtime = _runtime()
    equity_curve = _equity_curve_with_losses()
    price_data = pd.DataFrame({"close": [1.0] * len(equity_curve)})

    metrics = runtime._calculate_performance_metrics(
        stats={}, equity_curve=equity_curve, price_data=price_data, timeframe="5m"
    )

    returns = pd.Series(equity_curve).pct_change().dropna()
    downside = returns[returns < 0]
    old_wrong_value = (returns.mean() / downside.std()) * (252 ** 0.5)

    assert metrics["sortino_ratio"] != pytest.approx(old_wrong_value)
    ratio = metrics["sortino_ratio"] / old_wrong_value
    expected_ratio = math.sqrt(BARS_PER_YEAR["5m"] / 252)
    assert ratio == pytest.approx(expected_ratio, rel=1e-6)


def test_sortino_matches_sharpes_own_annualization_approach():
    """Sortino and Sharpe must use the SAME timeframe-derived factor - this test fails
    if a future edit reintroduces a second, divergent annualization scheme for Sortino."""
    runtime = _runtime()
    equity_curve = _equity_curve_with_losses()
    price_data = pd.DataFrame({"close": [1.0] * len(equity_curve)})

    metrics = runtime._calculate_performance_metrics(
        stats={}, equity_curve=equity_curve, price_data=price_data, timeframe="1h"
    )

    returns = pd.Series(equity_curve).pct_change().dropna()
    downside = returns[returns < 0]
    sortino_factor_used = metrics["sortino_ratio"] / (returns.mean() / downside.std())

    assert sortino_factor_used == pytest.approx(annualization_factor("1h"), rel=1e-9)


def test_the_runtime_no_longer_hardcodes_252_in_performance_metrics():
    """Parsed rather than grepped, like the sibling Sharpe regression test, so
    explanatory comments mentioning 252 do not cause a false failure."""
    import ast
    import inspect

    from backend_app.backend import backtest_runtime

    tree = ast.parse(inspect.getsource(backtest_runtime))

    target_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_calculate_performance_metrics":
            target_fn = node
            break
    assert target_fn is not None, "_calculate_performance_metrics not found"

    literals = {
        node.value
        for node in ast.walk(target_fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
    }

    assert 252 not in literals, (
        "_calculate_performance_metrics still contains the literal 252; Sortino (and "
        "Calmar) must be annualized from the actual timeframe"
    )


def test_calmar_local_computation_also_uses_the_real_annualization_factor():
    """Calmar's own local computation in this function is dead (ENGINE_OWNED_METRICS
    always wins the merge), but it carried the identical 252-based bug. Fixed so a
    future refactor cannot silently make it live again with the wrong factor."""
    runtime = _runtime()
    equity_curve = _equity_curve_with_losses()
    price_data = pd.DataFrame({"close": [1.0] * len(equity_curve)})

    metrics = runtime._calculate_performance_metrics(
        stats={"max_drawdown_pct": 10.0}, equity_curve=equity_curve, price_data=price_data,
        timeframe="5m",
    )

    bars_per_year = annualization_factor("5m") ** 2
    expected_annual_return = (
        equity_curve[-1] / equity_curve[0] - 1
    ) * (bars_per_year / len(equity_curve))
    expected_calmar = expected_annual_return / 0.10

    assert metrics["calmar_ratio"] == pytest.approx(expected_calmar, rel=1e-9)
