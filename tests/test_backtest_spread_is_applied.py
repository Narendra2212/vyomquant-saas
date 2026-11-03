"""The "Spread" cost control must genuinely change the simulated fill price.

WHAT WAS WRONG
--------------
``BacktestEngine.__init__`` stored ``self.spread`` and ``_apply_spread`` existed to apply
it to a price — but nothing ever called ``_apply_spread``, and ``self.spread`` was never
passed to ``vbt.Portfolio.from_signals``. The engine's own log line admitted it outright:
``"Spread: NOT modelled on this path (self.spread reaches no simulation)"``. Meanwhile
``algo22-terminal/src/pages/Backtester.jsx``'s Advanced accordion exposes a "Spread" field
whose copy ("The quoted spread the simulator crosses") implies it IS applied, and the
field's value reaches ``BacktestEngine(spread=...)`` end to end
(``strategy_operations.py`` -> ``BacktestRuntime`` -> ``BacktestEngine``).

THE FIX
-------
VectorBT's ``Portfolio.from_signals`` exposes exactly one fill-cost knob for this,
``slippage=``, which already widens the executed price the same direction a spread does.
Rather than leave the dial dead or silently rename ``slippage`` to mean "spread", the two
are combined additively into the one knob VectorBT accepts:
``effective_slippage = self.slippage + self.spread``. The two remain independent inputs
and are logged independently; only the mechanical application is folded together.

WHAT IS ASSERTED
----------------
1. ``vbt.Portfolio.from_signals`` is called with a ``slippage`` that is strictly larger
   than the base ``self.slippage`` whenever ``self.spread > 0`` — i.e. the dial has an
   effect on the actual simulation call, not just on a log line or an unused helper.
2. The combination is exactly additive: ``effective_slippage == slippage + spread``.
3. The dead ``_apply_spread`` method is gone (superseded by the fix above, not left
   around as a second, uncalled implementation of the same idea).
4. The log output no longer admits the cost is unmodelled.
5. End-to-end (gated on a real VectorBT being importable): two otherwise identical runs
   that differ only in ``spread`` produce different final equity, proving the parameter
   reaches the actual simulated fills and not just a kwarg that is passed and ignored.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend_app.backend.backtesting_engine import BacktestEngine

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_SOURCE = REPO_ROOT / "backend_app" / "backend" / "backtesting_engine.py"

BARS = 120


def _price_series() -> pd.Series:
    index = pd.date_range("2026-01-01", periods=BARS, freq="5min", tz="UTC")
    values = [40_000.0 + (i * 25.0) for i in range(BARS)]
    return pd.Series(values, index=index, name="close")


def _signals() -> tuple:
    long_mask = np.zeros(BARS, dtype=bool)
    short_mask = np.zeros(BARS, dtype=bool)
    long_mask[10] = True
    short_mask[80] = True
    return long_mask, short_mask


PARAMS = {
    "timeframe": "5m",
    "_vbt_freq": "5T",
    "trade_size_pct": 0.10,
    "ml_threshold": 0.80,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.15,
}


def _run(engine: BacktestEngine):
    long_mask, short_mask = _signals()
    return engine._compute_vectorbt_sync(
        _price_series(),
        np.zeros((BARS, 1)),
        "",
        long_mask,
        short_mask,
        dict(PARAMS),
    )


class _FakeTrades:
    def count(self):
        return 0


class _FakePortfolio:
    """Just enough of VectorBT's ``Portfolio`` surface for the code after
    ``from_signals`` to run to completion with zero trades, so the test can isolate the
    ``slippage=`` kwarg without simulating a real fill."""

    def __init__(self):
        self.trades = _FakeTrades()

    def stats(self):
        return {}

    def value(self):
        return pd.Series([10_000.0, 10_000.0], index=pd.date_range(
            "2026-01-01", periods=2, freq="5min", tz="UTC"
        ))

    def returns(self):
        return pd.Series([0.0, 0.0])


def _install_fake_vectorbt(monkeypatch) -> MagicMock:
    """Injects a stub ``vectorbt`` module whose ``Portfolio.from_signals`` is a
    MagicMock recording its call, so the kwargs the engine actually passes can be
    inspected without needing a real VectorBT install or a real simulated fill."""
    from_signals = MagicMock(return_value=_FakePortfolio())
    fake_module = ModuleType("vectorbt")
    fake_module.Portfolio = SimpleNamespace(from_signals=from_signals)
    monkeypatch.setitem(sys.modules, "vectorbt", fake_module)
    return from_signals


# ---------------------------------------------------------------------------
# 1 + 2. Spread reaches the actual simulation call, additively
# ---------------------------------------------------------------------------


def test_spread_widens_the_slippage_actually_passed_to_vectorbt(monkeypatch):
    from_signals = _install_fake_vectorbt(monkeypatch)

    engine = BacktestEngine(initial_capital=10_000.0, slippage=0.0005, spread=0.0002)
    _run(engine)

    assert from_signals.called, "vbt.Portfolio.from_signals was never called"
    passed_slippage = from_signals.call_args.kwargs["slippage"]

    assert passed_slippage == pytest.approx(0.0005 + 0.0002)
    assert passed_slippage > 0.0005, (
        "spread did not widen the slippage passed to the simulator - it still reaches "
        "no simulation"
    )


def test_zero_spread_leaves_slippage_unchanged(monkeypatch):
    """A control group: with spread=0 the behaviour must be identical to before, so the
    fix cannot be read as having silently changed the meaning of `slippage` alone."""
    from_signals = _install_fake_vectorbt(monkeypatch)

    engine = BacktestEngine(initial_capital=10_000.0, slippage=0.0005, spread=0.0)
    _run(engine)

    passed_slippage = from_signals.call_args.kwargs["slippage"]
    assert passed_slippage == pytest.approx(0.0005)


@pytest.mark.parametrize(
    "slippage,spread",
    [(0.0005, 0.0002), (0.001, 0.0), (0.0, 0.003), (0.002, 0.002)],
)
def test_the_combination_is_exactly_additive(monkeypatch, slippage, spread):
    from_signals = _install_fake_vectorbt(monkeypatch)

    engine = BacktestEngine(initial_capital=10_000.0, slippage=slippage, spread=spread)
    _run(engine)

    passed_slippage = from_signals.call_args.kwargs["slippage"]
    assert passed_slippage == pytest.approx(slippage + spread)


# ---------------------------------------------------------------------------
# 3 + 4. The dead helper and the false log admission are gone
# ---------------------------------------------------------------------------


def test_the_dead_apply_spread_helper_is_removed():
    """``_apply_spread`` operated on a single scalar price and was never called from the
    vectorized VectorBT path. Now that spread is genuinely modelled (via the combined
    slippage kwarg), leaving this uncalled method around would be a second, divergent,
    unused implementation of the same cost — exactly what the audit flagged for the UI
    field. It must be gone, not merely still-unused."""
    assert not hasattr(BacktestEngine, "_apply_spread"), (
        "BacktestEngine._apply_spread still exists; it is unused now that spread is "
        "folded into the slippage passed to vbt.Portfolio.from_signals"
    )


def test_the_log_no_longer_claims_spread_is_unmodelled():
    source = ENGINE_SOURCE.read_text(encoding="utf-8", errors="replace")
    assert "NOT modelled" not in source, (
        "the engine still logs that spread is not modelled, which is no longer true"
    )


def test_the_simulation_call_site_uses_a_combined_slippage_variable():
    """Pins the wiring, not just the behaviour: the call site must read a variable that
    is visibly the sum of both inputs, not pass `self.slippage` alone."""
    tree_source = inspect.getsource(BacktestEngine._compute_vectorbt_sync)
    assert "self.slippage" in tree_source and "self.spread" in tree_source
    assert "effective_slippage" in tree_source, (
        "expected a combined slippage+spread value to be computed and passed to "
        "vbt.Portfolio.from_signals"
    )


# ---------------------------------------------------------------------------
# 5. End-to-end: spread changes the real simulated outcome
# ---------------------------------------------------------------------------


def test_spread_changes_final_equity_end_to_end():
    """With a real VectorBT, two runs identical except for `spread` must simulate
    different fills. Gated with importorskip: this proves the fix works where VectorBT
    is installed (the pinned production dependency) without failing a sandbox that
    lacks it, matching the convention `test_backtest_engine_refuses_without_vectorbt.py`
    already uses for its own end-to-end case."""
    pytest.importorskip("vectorbt")

    no_spread_engine = BacktestEngine(initial_capital=10_000.0, slippage=0.0005, spread=0.0)
    wide_spread_engine = BacktestEngine(initial_capital=10_000.0, slippage=0.0005, spread=0.05)

    results_a, _ = _run(no_spread_engine)
    results_b, _ = _run(wide_spread_engine)

    assert results_a["final_equity"] != pytest.approx(results_b["final_equity"]), (
        "a backtest run with a materially wider spread produced the identical final "
        "equity of one with none - spread is still not reaching the simulated fills"
    )
