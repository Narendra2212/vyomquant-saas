"""
tests/test_backtest_signal_path_integrity.py

The backtester discards the strategy it was asked to simulate.

``BacktestEngine._compute_vectorbt_sync`` puts the DAG's signals through two filters
before they reach ``vbt.Portfolio.from_signals``. Each one is independently total: the
first removes every entry, the second removes every exit. Measured, not inferred --
``_conditions`` in ``tests/test_backtest_key_contract.py`` already documents the first as
a property it had to work around, and the numbers below were taken from the real engine
on VectorBT 0.26.2.

DEFECT 1 -- THE POSITION FILTER COUNTS THE CURRENT BAR
``backtesting_engine.py`` (the ``entries_raw``/``exits_raw`` block)::

    position = entries_raw.cumsum() - exits_raw.cumsum()
    tech_long  = entries_raw & (position == 0)   # "Valid entries: only when no position open"
    tech_short = exits_raw   & (position > 0)    # "Valid exits: only when position is open"

``cumsum`` at bar *i* includes bar *i*. On the bar an entry fires, the entry has already
been counted, so ``position >= 1`` and ``position == 0`` is false -- the entry is
discarded. On the bar an exit fires against one open position the exit has also been
counted, so ``position == 0`` and ``position > 0`` is false -- the exit is discarded too.
The comments state the intent correctly; the expression does not implement it.

Over 240 bars with a long every 8th bar and a short on the bar after:

    raw from the DAG            entries 30   exits 30
    after the position filter   entries  0   exits  0

SHIFTING THE CUMSUM BY ONE BAR IS NOT THE FIX, and this is worth recording because it is
the obvious repair and it is wrong. The counter is built from the RAW signals, so a signal
that gets discarded still moves it. An exit arriving while flat is dropped, yet still
decrements the running total, biasing it to ``-1`` for the remainder of the series so that
``position == 0`` never holds again. Measured: with the cumsum shifted, the ordinary
long-first strategy simulates correctly while the exit-first shape below starts raising
``0 signals`` -- the defect moves rather than closing. Whether a signal is acted on
depends on the position, and the position depends on which signals were acted on, so the
selection is sequential by nature and cannot be expressed as a cumulative sum over the
raw inputs.

With nothing surviving, ``np.all(np.isnan(sizes))`` holds and the engine raises
``ValueError("Strategy generated 0 signals. Try lowering the ML threshold or relaxing
technical conditions.")``. ``execute_backtest`` maps ``ValueError`` to **HTTP 422
BACKTEST_NOT_RUNNABLE**, so the canonical backtest route cannot return a result for a
strategy that opens with an entry -- which is every ordinary long strategy. The advice in
the message is misleading as well: no threshold and no loosening of conditions can help,
because the signals are removed after the conditions are evaluated.

A signal series that emits an *exit before its first entry* is the one shape that gets
through, because the leading exit makes the cumsums equal at the first entry bar. That is
why ``test_backtest_key_contract.py::_conditions`` shorts on every 8th bar and longs on
the bar after, and why its docstring calls that ordering "mandatory, not stylistic".

DEFECT 2 -- THE ML GATE VETOES EVERY EXIT WHEN THERE IS NO MODEL
With no model path, the engine substitutes ``ml_predictions = np.ones(n)`` -- a sentinel
meaning "no ML opinion". The gate then reads::

    ml_threshold = float(np.clip(params.get("ml_threshold", 0.80), 0.5, 0.99))
    long_mask  = tech_long  & (ml_predictions >= ml_threshold)
    short_mask = tech_short & (ml_predictions <= (1.0 - ml_threshold))

``long_mask`` passes, because ``1.0 >= 0.8``. ``short_mask`` requires
``1.0 <= 1 - threshold``, i.e. ``threshold <= 0`` -- and the clip floor is ``0.5``. **No
permitted value of ml_threshold can let an exit through**, so this is not a tuning
problem that the error message's "try lowering the ML threshold" could address. Measured
at the clip bounds and at the production default, on signals that survive defect 1:

    ml_threshold 0.50 -> entries 30, exits 0
    ml_threshold 0.80 -> entries 30, exits 0
    ml_threshold 0.99 -> entries 30, exits 0

A sentinel for "no information" must not act as a veto. The gate is only meaningful when
there are predictions to compare against.

WHAT THIS MEANS FOR THE ONE SHAPE THAT DOES RUN. The exit-first series above completes and
reports plausible figures -- 16 trades, final equity 10,440.82, +4.41%, win rate 37.5% --
and every one of those trades was closed by the hardcoded 5% stop-loss or 15% take-profit,
because all 30 of the strategy's own exit signals were discarded. The run is a simulation
of a different strategy than the one submitted, and nothing in the payload says so.

SCOPE. These two defects are the reason a backtest either 422s or ignores its own sell
logic. Separate from them, and not asserted here: ``trade_size_pct``, ``stop_loss_pct``
and ``take_profit_pct`` are hardcoded in ``backtest_runtime`` rather than taken from the
strategy, while the live path reads them off the blueprint.
"""

from __future__ import annotations

import asyncio
import math

import numpy as np
import pandas as pd
import pytest

from backend_app.backend.backtesting_engine import BacktestEngine

BAR_COUNT = 240
INITIAL_CAPITAL = 10_000.0
PERIOD = 8

# The production literals, verbatim from backtest_runtime.py's run_backtest_async call.
PARAMS = {
    "timeframe": "1m",
    "trade_size_pct": 0.10,
    "ml_threshold": 0.80,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.15,
}

# Stops that cannot be reached by the bars below, so a trade that closes can only have
# been closed by an exit signal. The series runs roughly 88..124, so a 95% fall or a 500%
# rise is unreachable.
UNREACHABLE_STOPS = {**PARAMS, "stop_loss_pct": 0.95, "take_profit_pct": 5.0}


def _bars(count: int = BAR_COUNT) -> list:
    """A 12-unit sine over a 0.05/bar drift -- the contract test's own series.

    Supplied, and said so plainly: this environment has no market-data connection and
    ``run_backtest_async`` takes bars as an argument. No claim is made that a backtest
    over live exchange data was run. Nothing asserted below depends on the bar values
    beyond the stops being reachable or not, which is stated where it matters.
    """
    out = []
    ts = 1_700_000_000_000
    for i in range(count):
        close = 100.0 + 0.05 * i + 12.0 * math.sin(i / 6.0)
        out.append([ts + i * 60_000, close, close * 1.01, close * 0.99, close, 100.0])
    return out


def _entry_first(count: int = BAR_COUNT) -> tuple[np.ndarray, np.ndarray]:
    """An ordinary long strategy: buy every 8th bar, sell the bar after."""
    longs = np.zeros(count, dtype=bool)
    shorts = np.zeros(count, dtype=bool)
    for i in range(count):
        if i % PERIOD == 0:
            longs[i] = True
        elif i % PERIOD == 1:
            shorts[i] = True
    return longs, shorts


def _exit_first(count: int = BAR_COUNT) -> tuple[np.ndarray, np.ndarray]:
    """The contract test's shape: sell every 8th bar, buy the bar after.

    Only the leading exit distinguishes it from :func:`_entry_first`, and that is the
    whole reason it reaches VectorBT today.
    """
    longs = np.zeros(count, dtype=bool)
    shorts = np.zeros(count, dtype=bool)
    for i in range(count):
        if i % PERIOD == 0:
            shorts[i] = True
        elif i % PERIOD == 1:
            longs[i] = True
    return longs, shorts


def _single_entry(count: int = BAR_COUNT) -> tuple[np.ndarray, np.ndarray]:
    """One entry, no exits. The simplest thing a backtester must be able to simulate."""
    longs = np.zeros(count, dtype=bool)
    longs[50] = True
    return longs, np.zeros(count, dtype=bool)


def _run(longs, shorts, params=None) -> dict:
    """One real VectorBT simulation. Returns the engine's ``results`` payload."""
    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
    stats, _curve = asyncio.run(
        engine.run_backtest_async(
            ohlcv_list=_bars(len(longs)),
            feature_matrix=np.zeros((len(longs), 1)),
            model_path="",  # no ML model -- the only configuration the runtime ever uses
            tech_long_condition=longs,
            tech_short_condition=shorts,
            params=params or PARAMS,
        )
    )
    return stats


def _reaching_vectorbt(longs, shorts, threshold: float | None = None) -> tuple[int, int]:
    """How many signals actually arrive at ``vbt.Portfolio.from_signals``.

    An OBSERVATION, not a replay. An earlier version of this helper recomputed the
    engine's two filters in the test, which made it track whatever the engine happened to
    do and therefore unable to contradict it. It now intercepts the real
    ``vbt.Portfolio.from_signals`` and counts the ``entries`` and ``exits`` it is handed,
    so the number is the engine's own output and a wrong filter shows up here.

    Returns ``(entries, exits)``. Raises whatever the engine raises -- a refusal is a
    result worth seeing rather than something to convert into a zero.
    """
    import vectorbt as vbt

    captured: dict[str, int] = {}
    original = vbt.Portfolio.from_signals

    def _spy(*args, **kwargs):
        entries = kwargs.get("entries")
        exits = kwargs.get("exits")
        captured["entries"] = int(np.asarray(entries, dtype=bool).sum())
        captured["exits"] = int(np.asarray(exits, dtype=bool).sum())
        return original(*args, **kwargs)

    params = dict(PARAMS)
    if threshold is not None:
        params["ml_threshold"] = threshold

    vbt.Portfolio.from_signals = _spy
    try:
        _run(longs, shorts, params)
    finally:
        vbt.Portfolio.from_signals = original

    assert captured, "from_signals was never reached, so nothing was simulated"
    return captured["entries"], captured["exits"]


# ---------------------------------------------------------------------------
# 1. Defect 1 -- the position filter counts the current bar
# ---------------------------------------------------------------------------


class TestAnOrdinaryStrategyCanBeBacktested:
    def test_a_long_first_strategy_produces_a_result_rather_than_a_refusal(self):
        """The headline. Buy every 8th bar, sell the bar after -- nothing exotic.

        Today this raises ``Strategy generated 0 signals``, which ``execute_backtest``
        answers as 422 BACKTEST_NOT_RUNNABLE. A backtester that cannot simulate this is
        not a backtester.
        """
        longs, shorts = _entry_first()
        assert longs.sum() == 30 and shorts.sum() == 30, "fixture drifted"

        stats = _run(longs, shorts)

        assert stats.get("total_trades", 0) > 0, (
            f"a long-first strategy with {int(longs.sum())} entry signals closed no "
            f"trades; payload keys {sorted(stats)}"
        )

    def test_one_entry_and_no_exits_is_simulable(self):
        """The degenerate case: a single buy, held to the end of the series."""
        longs, shorts = _single_entry()

        stats = _run(longs, shorts)

        assert stats.get("total_trades", 0) >= 1, (
            "a single entry signal produced no trade at all"
        )

    def test_the_only_difference_that_matters_is_not_which_signal_comes_first(self):
        """``_entry_first`` and ``_exit_first`` differ only in ordering.

        One of them runs and the other is refused, which is the clearest statement of
        defect 1 available: the filter is deciding on the leading signal's direction, not
        on whether a position is open.
        """
        entry_first_trades = _run(*_entry_first()).get("total_trades", 0)
        exit_first_trades = _run(*_exit_first()).get("total_trades", 0)

        assert entry_first_trades > 0 and exit_first_trades > 0, (
            f"both orderings must be simulable; entry-first closed "
            f"{entry_first_trades} trades and exit-first closed {exit_first_trades}"
        )

    def test_every_entry_arriving_flat_survives_the_position_filter(self):
        """Counted at the filter, so the failure names the filter rather than the refusal."""
        longs, shorts = _entry_first()

        kept_entries, _ = _reaching_vectorbt(longs, shorts, PARAMS["ml_threshold"])

        assert kept_entries == int(longs.sum()), (
            f"{int(longs.sum()) - kept_entries} of {int(longs.sum())} entry signals were "
            f"removed before reaching VectorBT. `position` is inclusive of the current "
            f"bar, so an entry bar always reads `position >= 1` and never satisfies "
            f"`position == 0`."
        )


# ---------------------------------------------------------------------------
# 2. Defect 2 -- the ML gate vetoes every exit when there is no model
# ---------------------------------------------------------------------------


class TestExitSignalsAreNotVetoedByAnAbsentModel:
    @pytest.mark.parametrize("threshold", [0.5, 0.8, 0.99, 0.0, 1.0])
    def test_no_threshold_removes_exits_when_there_is_no_model(self, threshold):
        """Parametrised across the clip bounds and outside them.

        ``ml_predictions`` is all ``1.0`` when no model is supplied, so the exit
        predicate ``1.0 <= 1 - threshold`` needs a threshold of zero or less, which the
        clip to [0.5, 0.99] forbids. Every value therefore removes every exit, including
        the ``0.0`` the error message's "try lowering the ML threshold" invites.
        """
        longs, shorts = _exit_first()  # survives defect 1, isolating defect 2

        _entries, kept_exits = _reaching_vectorbt(longs, shorts, threshold)

        assert kept_exits > 0, (
            f"ml_threshold={threshold} removed all {int(shorts.sum())} exit signals. "
            f"With no model the predictions are a 'no opinion' sentinel of 1.0 and must "
            f"not veto anything."
        )

    def test_a_strategy_with_exits_does_not_simulate_identically_to_one_without(self):
        """The behavioural form, with no replay of the engine's arithmetic.

        Two runs differing only in whether the exit signals are present. If exits reach
        the simulation the results must differ; identical results prove the exits were
        discarded.
        """
        longs, shorts = _exit_first()
        with_exits = _run(longs, shorts, UNREACHABLE_STOPS)
        without_exits = _run(longs, np.zeros_like(shorts), UNREACHABLE_STOPS)

        assert (
            with_exits.get("total_trades"),
            with_exits.get("final_equity"),
        ) != (
            without_exits.get("total_trades"),
            without_exits.get("final_equity"),
        ), (
            "removing every exit signal changed nothing about the simulation "
            f"({with_exits.get('total_trades')} trades, equity "
            f"{with_exits.get('final_equity')} either way), so the exits were never "
            "applied"
        )

    def test_trades_close_on_exit_signals_and_not_only_on_the_stops(self):
        """With unreachable stops, a closed trade can only come from an exit signal.

        The bars run roughly 88..124, so a 95% stop-loss and a 500% take-profit cannot
        trigger. Today every trade in this run is closed by the 5%/15% stops instead, so
        with those stops moved out of reach nothing closes.
        """
        longs, shorts = _exit_first()

        stats = _run(longs, shorts, UNREACHABLE_STOPS)

        assert stats.get("total_trades", 0) > 1, (
            f"with unreachable stops only {stats.get('total_trades')} trade(s) closed, "
            f"so the {int(shorts.sum())} exit signals are closing nothing. Positions are "
            f"being closed by the hardcoded stop-loss/take-profit alone."
        )


# ---------------------------------------------------------------------------
# 3. The combined consequence
# ---------------------------------------------------------------------------


class TestTheRefusalMessageIsNotMisleading:
    def test_the_zero_signal_refusal_is_not_reachable_for_an_ordinary_strategy(self):
        """``Strategy generated 0 signals`` must mean the strategy produced none.

        It currently fires for a strategy that produced sixty, and advises tuning two
        things that cannot change the outcome. Asserted by running the ordinary strategy
        and requiring that the refusal does not appear.
        """
        longs, shorts = _entry_first()
        assert longs.sum() + shorts.sum() == 60, "fixture drifted"

        try:
            _run(longs, shorts)
        except ValueError as exc:
            if "0 signals" in str(exc):
                pytest.fail(
                    f"the engine reported '0 signals' for a strategy that emitted "
                    f"{int(longs.sum())} entries and {int(shorts.sum())} exits: {exc}"
                )
            raise
