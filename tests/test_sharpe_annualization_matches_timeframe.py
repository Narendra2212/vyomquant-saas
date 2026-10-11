"""The Sharpe ratio must be annualized by the bar size it was computed from.

WHAT THIS GUARDS
----------------
``backtesting_engine`` computed::

    sharpe = (returns.mean() / returns.std()) * (252 ** 0.5)

252 is the number of trading days in an equities year. The annualization factor for a
return series is ``sqrt(observations per year)``, so that constant is correct only if a
bar is one trading day — and this engine accepts fifteen timeframes, defaulting to
``5m``.

The consequences are multiplicative and large:

* hourly bars: true factor ``sqrt(8760) ~ 93.6`` vs ``sqrt(252) ~ 15.9`` — Sharpe
  understated about 5.9x
* 5-minute bars: true factor ``sqrt(105120) ~ 324`` — understated about 20x

It mattered beyond cosmetics because the figure is comparable: the marketplace ranks and
displays strategies on it, so intraday strategies were systematically mis-ranked against
daily ones.

Crypto also trades continuously, so a year is 365x24 hours with no weekend gap and no
exchange close. Daily bars are 365 observations, not 252 — the equities convention never
applied to this asset class at all.
"""

import math

import pytest

from backend_app.backend.backtesting_engine import (BARS_PER_YEAR,
                                                    VALID_FREQ_MAP,
                                                    annualization_factor)


def test_every_supported_timeframe_has_an_annualization_factor():
    """A timeframe the engine accepts but cannot annualize would silently fall back to
    a wrong constant, which is the original defect."""
    missing = set(VALID_FREQ_MAP) - set(BARS_PER_YEAR)
    assert not missing, f"timeframes accepted but not annualizable: {sorted(missing)}"


def test_an_unknown_timeframe_raises_rather_than_defaulting():
    """A silent default is how one hardcoded constant survived: a wrong factor still
    produces a plausible number, so nothing downstream can detect it."""
    with pytest.raises(ValueError, match="No annualization factor"):
        annualization_factor("7s")


@pytest.mark.parametrize(
    "timeframe,expected_bars",
    [
        ("1m", 525600),
        ("5m", 105120),
        ("15m", 35040),
        ("1h", 8760),
        ("4h", 2190),
        ("12h", 730),
        ("1d", 365),
        ("1w", 52),
    ],
)
def test_bars_per_year_counts_a_continuous_market(timeframe, expected_bars):
    """Crypto has no weekends and no close, so the year is 365x24."""
    assert BARS_PER_YEAR[timeframe] == expected_bars
    assert annualization_factor(timeframe) == pytest.approx(math.sqrt(expected_bars))


def test_daily_is_365_not_the_equities_252():
    """Pins the specific wrong constant so it cannot come back."""
    assert BARS_PER_YEAR["1d"] == 365
    assert annualization_factor("1d") != pytest.approx(math.sqrt(252))


def test_hourly_is_not_annualized_as_if_it_were_daily():
    """The headline consequence, asserted as a ratio rather than a magic number."""
    hourly = annualization_factor("1h")
    old_constant = math.sqrt(252)

    assert hourly / old_constant == pytest.approx(math.sqrt(8760 / 252), rel=1e-6)
    assert hourly > old_constant * 5


def test_a_shorter_bar_always_annualizes_by_more():
    """Monotonicity: more observations per year means a larger factor. Catches an
    inverted or transposed table, which a per-value test would not."""
    ordered = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"]
    factors = [annualization_factor(tf) for tf in ordered]

    assert factors == sorted(factors, reverse=True), (
        f"annualization factors are not monotonically decreasing with bar size: "
        f"{list(zip(ordered, factors))}"
    )


def test_the_engine_no_longer_contains_a_hardcoded_252():
    """The defect was one literal. Parsed rather than grepped so the explanatory
    comments that necessarily mention 252 do not match."""
    import ast
    import inspect

    from backend_app.backend import backtesting_engine

    tree = ast.parse(inspect.getsource(backtesting_engine))

    sharpe_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_compute_vectorbt_sync":
            sharpe_fn = node
            break
    assert sharpe_fn is not None, "_compute_vectorbt_sync not found"

    literals = {
        node.value
        for node in ast.walk(sharpe_fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
    }

    assert 252 not in literals, (
        "the backtest body still contains the literal 252; the Sharpe annualization "
        "factor must be derived from the timeframe"
    )


def test_the_sharpe_computation_calls_the_timeframe_aware_factor():
    """Pins the wiring, not just the helper: the helper being correct is useless if the
    computation does not call it."""
    import ast
    import inspect

    from backend_app.backend import backtesting_engine

    tree = ast.parse(inspect.getsource(backtesting_engine))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "annualization_factor" in called, (
        "annualization_factor is defined but never called; the Sharpe ratio is still "
        "annualized by a constant"
    )
