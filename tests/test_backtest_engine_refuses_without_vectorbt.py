"""
tests/test_backtest_engine_refuses_without_vectorbt.py

With VectorBT unavailable, the backtest engine must REFUSE — not produce numbers.

WHAT WAS WRONG
--------------
``BacktestEngine._compute_vectorbt_sync`` opened with::

    try:
        import vectorbt as vbt
        has_vbt = True
    except (ImportError, ModuleNotFoundError):
        has_vbt = False

    ...
    if not has_vbt:
        # Fallback pandas calculation when vectorbt is not installed

and that fallback returned a complete, plausible, **fabricated** result set:

* ``"profit_factor": 1.5 if trades_count > 0 else 0.0`` — a literal. Not computed from
  anything.
* ``"sortino_ratio": round(sharpe * 1.1, 4)`` — the Sharpe ratio times 1.1. Sortino
  penalises downside deviation only; it is not a multiple of Sharpe, and no data was
  consulted to produce this.
* ``"total_fees_paid": trades_count * self.fees * self.initial_capital`` — an estimate
  presented in the same field the real path fills from ``portfolio.stats()``.
* ``"trades": []`` — so ``winning_trades``/``losing_trades`` were back-derived from
  ``win_rate`` rather than from per-trade P&L.
* ``win_count = int((strategy_returns > 0).sum())`` divided by ``trades_count`` — that
  numerator counts **winning BARS**, the denominator counts **trades**. The field is
  published as ``win_rate_pct``.
* No stop-loss, no take-profit, no slippage and no spread were applied at all, while the
  real path passes ``sl_stop``/``tp_stop``/``slippage`` to ``vbt.Portfolio.from_signals``.

None of it was marked. The payload has the same keys and the same types as a real result, so
once ``backtest_runtime`` persisted it into ``strategy_backtests`` nothing downstream could
tell the two apart.

WHY THIS IS WORSE THAN AN ORDINARY WRONG NUMBER
-----------------------------------------------
Those columns do not stay in the backtest. ``routers/library.py``'s publish path copies
``backtest_sharpe_ratio``, ``backtest_profit_factor``, ``backtest_win_rate_pct`` and the
rest onto the ``library_strategies`` row, and ``listing_projection`` serves them as the
listing's public ``performance_summary`` and ``risk_metrics``. So a deployment missing one
pinned dependency could publish an invented profit factor of 1.5 and a Sortino derived by
multiplication to the public marketplace, as that strategy's measured track record.

WHY REFUSING IS SAFE HERE
-------------------------
``vectorbt==0.26.2`` is pinned in ``requirements-base.txt`` and is installed. The fallback
is therefore unreachable in any correctly provisioned environment, and a deployment where it
IS reachable is one whose dependencies are broken — a case where an error is the useful
answer and a fabricated track record is the harmful one.

WHAT IS ASSERTED
----------------
1. With ``vectorbt`` unimportable, the engine raises, and the message names the dependency.
2. Nothing is returned: there is no partial or marked-up result to persist by accident.
3. The fabricated constants are gone from the module.
4. The real path is untouched — with VectorBT present, a backtest still completes and its
   figures still come from the portfolio.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.backtesting_engine import BacktestEngine

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_SOURCE = REPO_ROOT / "backend_app" / "backend" / "backtesting_engine.py"

#: Enough bars to clear the engine's own 50-bar minimum guard.
BARS = 120


def _price_series() -> pd.Series:
    index = pd.date_range("2026-01-01", periods=BARS, freq="5min", tz="UTC")
    # A deterministic, gently trending series. No randomness: a backtest assertion that
    # depends on a seed is a backtest assertion that will drift.
    values = [40_000.0 + (i * 25.0) for i in range(BARS)]
    return pd.Series(values, index=index, name="close")


def _signals() -> tuple:
    """One entry early, one exit late, so a real simulation produces exactly one trade."""
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
        "",  # no ML model
        long_mask,
        short_mask,
        dict(PARAMS),
    )


# ---------------------------------------------------------------------------
# 1 + 2. Without VectorBT the engine refuses
# ---------------------------------------------------------------------------

def test_the_engine_refuses_when_vectorbt_is_unavailable(monkeypatch):
    """The defect, stated as a test.

    Before the fix this returns a complete result dict whose ``profit_factor`` is the
    literal ``1.5`` and whose ``sortino_ratio`` is ``sharpe * 1.1``.

    ``sys.modules[name] = None`` is how CPython represents "this import must fail": the
    ``import vectorbt`` inside the method then raises ``ImportError`` without touching the
    real installed package or the import of any other test in this session.
    """
    monkeypatch.setitem(sys.modules, "vectorbt", None)
    engine = BacktestEngine(initial_capital=10_000.0)

    with pytest.raises(Exception) as excinfo:
        _run(engine)

    message = str(excinfo.value).lower()
    assert "vectorbt" in message, (
        f"the engine refused but did not name the missing dependency, so an operator "
        f"cannot act on it: {excinfo.value!r}"
    )
    # Not a bare TypeError/AttributeError from stumbling into the live path with no vbt —
    # the refusal has to be deliberate.
    assert not isinstance(excinfo.value, (TypeError, AttributeError, NameError)), (
        f"the engine failed incidentally rather than refusing deliberately: "
        f"{type(excinfo.value).__name__}: {excinfo.value}"
    )


def test_no_result_is_produced_when_vectorbt_is_unavailable(monkeypatch):
    """Assertion 2: nothing persistable may come back.

    ``backtest_runtime`` writes whatever it receives into ``strategy_backtests``, and the
    publish path copies those columns onto a public marketplace listing. A partial result —
    even a marked one — is a result something downstream will store.
    """
    monkeypatch.setitem(sys.modules, "vectorbt", None)
    engine = BacktestEngine(initial_capital=10_000.0)

    try:
        outcome = _run(engine)
    except Exception:
        return  # refused, which is the contract

    raise AssertionError(
        f"the engine returned a result without VectorBT: {str(outcome)[:400]!r}. Those "
        f"figures reach strategy_backtests and, via the publish path, a public listing's "
        f"performance_summary."
    )


# ---------------------------------------------------------------------------
# 3. The fabricated constants are gone
# ---------------------------------------------------------------------------

def test_the_fabricated_fallback_metrics_are_gone_from_the_module():
    """A static guard on the two values that were invented outright.

    ``profit_factor`` was the literal ``1.5``; ``sortino_ratio`` was ``sharpe * 1.1``.
    Neither was derived from the simulation, and both were returned in the same field the
    real path fills from ``portfolio.stats()``.
    """
    tree = ast.parse(ENGINE_SOURCE.read_text(encoding="utf-8", errors="replace"))

    offenders: List[str] = []
    for node in ast.walk(tree):
        # `"profit_factor": 1.5 if ... else 0.0` — a dict value that is a constant or a
        # conditional over constants, under a performance key.
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                if key.value not in ("profit_factor", "sortino_ratio"):
                    continue
                rendered = ast.unparse(value)
                if "1.5" in rendered or "1.1" in rendered:
                    offenders.append(f"line {node.lineno}: {key.value} = {rendered}")

    assert not offenders, (
        "the backtest engine still assigns a fabricated performance metric:\n  "
        + "\n  ".join(offenders)
        + "\nThese are published as a strategy's measured track record."
    )


def test_the_dead_slippage_and_spread_helpers_are_not_advertised_as_applied():
    """``_apply_realistic_slippage``/``_apply_spread`` are defined and never called.

    The run log nonetheless printed ``"Slippage applied: random.uniform(0.999, 1.001)"`` and
    ``"Spread applied: ..."`` — statements that were not true of the simulation that
    followed, since VectorBT applies its own deterministic slippage and no spread at all. A
    log line asserting a cost model that is not in force is the same fabrication in a
    different output stream.
    """
    source = ENGINE_SOURCE.read_text(encoding="utf-8", errors="replace")
    assert "Slippage applied: random.uniform" not in source, (
        "the engine still logs that it applied `random.uniform` slippage. That helper "
        "(`_apply_realistic_slippage`) is never called; VectorBT applies the `slippage=` "
        "argument instead."
    )


# ---------------------------------------------------------------------------
# 4. The real path still works
# ---------------------------------------------------------------------------

def test_with_vectorbt_present_a_backtest_still_completes_from_real_portfolio_figures():
    """The regression half.

    A fix that refused unconditionally would satisfy everything above and break
    backtesting. This runs the engine for real and checks the figures come from the
    simulation: one entry and one exit were seeded on a monotonically rising series, so the
    single trade is profitable and the final equity exceeds the initial capital.

    THIS TEST CARRIED AN ``xfail(strict=True)`` AND NO LONGER DOES. The marker named the
    position-filter off-by-one that discarded the seeded entry, and it recorded two
    conclusions that later measurement contradicted. Both are corrected here rather than
    dropped with the marker:

    * "Shifting the cumsum changes the result of every backtest, which is a business-logic
      change." It does not. For an ordinary long-first strategy there was no result to
      change -- the engine raised ``Strategy generated 0 signals`` and
      ``execute_backtest`` answered 422. The only signal shape that produced numbers was
      one emitting an exit before its first entry. The judgement was made about the fix's
      blast radius without first measuring the current output, which was the error.
    * "Shifting the cumsum" was not the fix either. The counter is built from the raw
      signals, so a discarded exit still moves it; shifting alone repairs the long-first
      case and breaks the exit-first case. The selection is sequential and is now written
      that way. See ``tests/test_backtest_signal_path_integrity.py``, which holds both
      that defect and the ML gate that independently discarded every exit.
    """
    pytest.importorskip("vectorbt")

    engine = BacktestEngine(initial_capital=10_000.0)
    results, equity_curve = _run(engine)

    assert results["total_trades"] >= 1, (
        f"the seeded entry/exit produced no trade: {results!r}"
    )
    assert equity_curve, "no equity curve was produced"
    assert results["final_equity"] > 10_000.0, (
        f"a long trade on a monotonically rising series lost money "
        f"({results['final_equity']}), so these figures are not coming from the simulation"
    )
    # Derived from per-trade net P&L, not back-derived from a win rate.
    assert results["winning_trades"] + results["losing_trades"] <= results["total_trades"]
    assert results["trades"], "no per-trade records were extracted"
