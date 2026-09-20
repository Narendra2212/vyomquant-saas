"""
tests/test_backtest_key_contract.py - production-launch-hardening task 1, CLUSTER B,
and task 2's backtest preservation clause (3.4).

Requirements 1.7, 1.8, 1.9, 1.10, 3.4, 3.5. `design.md` §Hypothesized Root Cause (wave 2),
corrections 1 and 2.

WHAT THIS FILE IS
-----------------
**Bug condition exploration tests.** Every test in sections 1-6 is EXPECTED TO FAIL against the
current tree (`F`). The failure is the deliverable, and it is also **the measurement of the rename
set**: task 7.x cannot know which columns to repoint until this file has enumerated them. Nothing
here is a symptom patch, no source file is touched, and no assertion has been weakened to make a
run green. Section 7 is the preservation half and PASSES on `F`.

THE DEFECT, IN ONE SENTENCE
---------------------------
Three layers hand a results dict along, and **each layer spells the keys differently from the
one that reads them**, so the writer's `results.get(name, 0)` misses and persists its own default
while the row is rendered to a trader as a computed result.

THE THREE BOUNDARIES
--------------------
::

    backend_app/backend/backtesting_engine.py   run_backtest_async
        emits  total_return_pct, final_equity, win_rate_pct, max_drawdown_pct, sharpe_ratio,
               sortino_ratio, profit_factor, total_trades, expectancy, calmar_ratio,
               total_fees_paid, trades
        returns (results, equity_curve)          <- the curve is OUT OF BAND (:571)
            |
    backend_app/backend/backtest_runtime.py     run_backtest  (:382-390)
        builds {**stats, **performance_metrics, charts, execution_time_seconds,
                trades_count, final_capital}
        drops  equity_curve                      <- it was never a key, so nothing had to drop it
            |
    backend_app/backend/backtest_service.py     update_backtest_results  (:421-439)
        reads  results.get("total_return", 0), .get("win_rate", 0), .get("max_drawdown", 0),
               .get("final_capital", 0), .get("equity_curve", []), ...

THE PRIMARY INSTRUMENT (section 1)
----------------------------------
Not "assert `win_rate` is 0.55". The **key-set contract**: the writer's read-key set is a SUBSET
of the payload's emitted-key set, asserted per column, on BOTH engine paths. That instrument is
chosen because it fails on the *next* rename too - a future edit that renames `sharpe_ratio` to
`sharpe` reds this file without anyone remembering to add a case, which is what Requirement 2.7's
"so the mismatch cannot silently return" asks for.

THE FINDING THE REQUIREMENTS DO NOT NAME (section 2)
----------------------------------------------------
1.8 says `final_capital` "stores `0`". It does not. `backtest_runtime` reads
`stats.get("Final Equity", self.vectorbt_engine.initial_capital)` (:388) - `Final Equity` is a
VectorBT *display* name, and `stats` here is the engine's own already-normalised `results` dict,
which spells it `final_equity`. So the lookup always misses and **the default always wins**:
`final_capital` stores the STARTING capital. A trader reading a completed backtest that made or
lost money sees the number they started with, which is far more believable than a `0`. This is
`design.md` correction 1.

THE BLAST RADIUS (section 5) - THIS DECIDES TASK 7.6's SCOPE
------------------------------------------------------------
**ANSWER: the five are not the whole extent. They are five of twelve.** The same display-name
mistake is made **ten times** in `_calculate_performance_metrics` and **twice more** in
`run_backtest`'s own payload literal, and it fabricates **twelve columns**, not the nine
`tasks.md` expects - `sqn`, `trades_count` and `final_capital` are the additions. Section 5
enumerates both sets and asserts the narrow claim so that it fails and the wider count is on the
record. See :data:`DISPLAY_NAME_READS` for the full list and the per-column verdict, and
section 5's closing note for a *third* distinct cause inside the same method (a RangeIndex read
as nanoseconds, which empties `monthly_returns` and `daily_returns`) that the key-set instrument
cannot see.

WHAT IS REAL AND WHAT IS SUPPLIED
---------------------------------
**Real:** a genuine VectorBT 0.26.2 simulation. `BacktestEngine.run_backtest_async` runs
end to end over 240 bars and closes 12 trades - real `vbt.Portfolio.from_signals`, real
`portfolio.stats()`, real fees, slippage, stop-loss and take-profit, real trade-record
extraction. The fallback path at `backtesting_engine.py:230-244` is exercised by making
`import vectorbt` fail, which is the same branch a deployment without VectorBT takes. The
runtime hop runs the real `run_backtest` body, the real `_calculate_performance_metrics` and the
real `_generate_charts`. The writer is the real `update_backtest_results`.

**Supplied, and said so plainly:** the OHLCV bars. This environment has no market-data
connection, and `run_backtest_async` takes the bars as an argument rather than fetching them, so
they are generated locally - a 12-unit sine over a 0.05/bar drift, chosen because it oscillates
enough to hit both the 5% stop and the 15% target and therefore produces a run that *moved*,
which is what section 2 needs. Also supplied: the market-data feed and DAG engine the runtime
calls before the engine (doubles reused from
`tests/test_backtest_evidence_columns_regression.py`), and the PostgREST client. **No claim is
made here that a backtest over live Binance data was run.** Nothing in this file depends on the
bar values; every assertion is about key names and about which producer won.

THE ONE PROPERTY OF `F` THIS MEASUREMENT HAD TO ACCOMMODATE
-----------------------------------------------------------
`backtesting_engine.py:168-175` recomputes `position = entries_raw.cumsum() - exits_raw.cumsum()`
**inclusive of the current bar**, then keeps only `entries_raw & (position == 0)`. An entry bar
therefore always has `position >= 1` and is filtered out unless an exit signal preceded it - so a
signal series that opens with an *exit* is the minimum that reaches VectorBT at all; anything
else raises "Strategy generated 0 signals". Recorded here because it is a real property of `F`
that shaped :func:`_conditions`, not a property of this test. It is not this task's defect and no
assertion below depends on it.

HARNESS
-------
Reused from `tests/test_backtest_evidence_columns_regression.py`, which already drives
`BacktestRuntime.run_backtest` and `BacktestService.update_backtest_results` against a PostgREST
double: `_Supabase`, `_service`, `_StubDataEngine`, `_StubDagEngine`, `_StubStrategyPackage`,
`USER`, `TABLE`, `RECONCILED_FOR_BAR_COUNT`, `_running_row`. Two of its doubles are deliberately
NOT reused - `_StubVectorbtEngine` (which returns `{"Total Trades": 41, "Final Equity": 11234.5}`,
i.e. the display names `stats` does *not* carry, so it would hide the entire defect) and its
`_calculate_performance_metrics` stub. Those two are the subject here, so they run for real.

HOW TO RUN
----------
``$env:PYTHONIOENCODING="utf-8"; python -m pytest tests/test_backtest_key_contract.py -v``

The encoding is not optional: `backtesting_engine` prints money emoji to stdout, and the Windows
console default codec raises `UnicodeEncodeError` on them.
"""

import asyncio
import contextlib
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import backtest_service as bs
from backend_app.backend.backtest_runtime import BacktestRuntime
from backend_app.backend.backtesting_engine import BacktestEngine

# The harness this file reuses rather than re-standing-up. See HARNESS above.
from tests.test_backtest_evidence_columns_regression import (  # noqa: E402
    RECONCILED_FOR_BAR_COUNT,
    STRATEGY_ID,
    TABLE,
    USER,
    USER_ID,
    VERSION_ID,
    _running_row,
    _service,
    _StubDagEngine,
    _StubDataEngine,
    _StubStrategyPackage,
    _Supabase,
)


# ══════════════════════════════════════════════════════════════════════════
#  THE TWO KEY SETS UNDER CONTRACT
# ══════════════════════════════════════════════════════════════════════════

#: Every key `update_backtest_results` reads out of `results`, in source order
#: (`backtest_service.py:421-439`). This is the WRITER'S READ-KEY SET, and the contract in
#: section 1 is that it is a subset of whatever the payload carries. Read off the source rather
#: than hand-curated, so a column added to the writer without a matching emit reds this file.
READ_KEYS = (
    "total_return",              # :424  <- engine emits total_return_pct
    "total_return_pct",          # :425
    "win_rate",                  # :426  <- engine emits win_rate_pct
    "max_drawdown",              # :427  <- engine emits max_drawdown_pct
    "sharpe_ratio",              # :428
    "sortino_ratio",             # :429
    "profit_factor",             # :430
    "total_trades",              # :431
    "winning_trades",            # :432  <- emitted by NOTHING on either path
    "losing_trades",             # :433  <- emitted by NOTHING on either path
    "equity_curve",              # :434  <- out of band: the engine's 2nd tuple element
    "monthly_returns",           # :435
    "daily_returns",             # :436
    "execution_time_seconds",    # :437
    "final_capital",             # :438  <- see section 2: NOT zero, the STARTING capital
    "trades",                    # :439
)

#: The four columns Requirements 1.7 and 1.8 name. Section 1 asserts every member of
#: :data:`READ_KEYS`, but these four are parametrised by name so a partial fix - repointing
#: `win_rate` and forgetting `max_drawdown` - reports which one is still missing.
REQUIREMENT_NAMED_COLUMNS = ("total_return", "win_rate", "max_drawdown", "final_capital")

#: Every `stats.get("<VectorBT display name>")` in `backtest_runtime`, with the column(s) that
#: collapse when it misses. `stats` at that point is the ENGINE'S `results` dict, which has
#: already been normalised to snake_case, so **every one of these reads misses on every run**.
#:
#: `tasks.md` names five of them. There are TEN in `_calculate_performance_metrics` and two more
#: in `run_backtest`. That difference is the scope of task 7.6 and is asserted in section 5.
DISPLAY_NAME_READS = {
    # display name           (source line, columns fabricated when the read misses)
    "Max Drawdown [%]":      ("backtest_runtime.py:473", ("calmar_ratio", "recovery_factor")),
    "Net Profit":            ("backtest_runtime.py:480", ("recovery_factor", "average_trade")),
    "Total Trades":          ("backtest_runtime.py:487", ("average_trade", "sqn")),
    "Best Trade":            ("backtest_runtime.py:494", ("largest_win",)),
    "Worst Trade":           ("backtest_runtime.py:495", ("largest_loss",)),
    "Win Streak":            ("backtest_runtime.py:498", ("consecutive_wins",)),
    "Loss Streak":           ("backtest_runtime.py:499", ("consecutive_losses",)),
    "Win Rate [%]":          ("backtest_runtime.py:502", ("expectancy", "kelly")),
    "Avg Winning Trade":     ("backtest_runtime.py:503", ("expectancy", "kelly")),
    "Avg Losing Trade":      ("backtest_runtime.py:504", ("expectancy", "kelly")),
    # …and outside _calculate_performance_metrics, in run_backtest's own payload literal:
    "Total Trades (payload)": ("backtest_runtime.py:387", ("trades_count",)),
    "Final Equity":          ("backtest_runtime.py:388", ("final_capital",)),
}

#: The five `tasks.md` names, kept separate so section 5 can report the difference honestly
#: rather than quietly widening the list.
DISPLAY_NAMES_TASKS_MD_NAMES = (
    "Max Drawdown [%]",
    "Net Profit",
    "Win Rate [%]",
    "Best Trade",
    "Avg Winning Trade",
)

#: Every column `tasks.md` expects to be fabricated by the display-name cause, plus the two this
#: measurement adds. `sqn` and `trades_count` are the additions: `sqn` reads `Total Trades` (:487)
#: and `trades_count` reads it again at :387, and neither is on `tasks.md`'s list.
EXPECTED_FABRICATED = (
    "calmar_ratio",
    "recovery_factor",
    "average_trade",
    "largest_win",
    "largest_loss",
    "consecutive_wins",
    "consecutive_losses",
    "expectancy",
    "kelly",
)

#: `sqn` and `trades_count` are the two this measurement adds to `tasks.md`'s nine.
#: `final_capital` is a third, and it is deliberately NOT in this tuple: it fabricates
#: `10000.0` rather than `0`, so the "is not zero" assertion below would PASS on it and report
#: a fabricated column as clean. Section 2 asserts it with the comparison that can see it.
FABRICATED_BEYOND_TASKS_MD = ("sqn", "trades_count")


# ══════════════════════════════════════════════════════════════════════════
#  THE REAL RUN
# ══════════════════════════════════════════════════════════════════════════

#: Comfortably over `backtesting_engine.py:189`'s 50-bar guard, and long enough that the sine
#: below completes ~4 cycles, so the run closes trades on both the stop and the target.
BAR_COUNT = 240

#: Deliberately not 100_000 - cluster A's fabricated figure - and not the runtime default of
#: 10_000 either... except that it IS the runtime default, on purpose: section 2's assertion is
#: `final_capital != initial_capital`, and it has to be reading the same engine the runtime
#: constructed. Named once so the assertion compares against a symbol, not a literal.
INITIAL_CAPITAL = 10_000.0

BASE_MS = 1_700_000_000_000  # 2023-11-14T22:13:20Z, an arbitrary fixed epoch


def _bars(count=BAR_COUNT):
    """`count` OHLCV bars in the shape `fetch_historical_ohlcv` returns.

    SUPPLIED, NOT FETCHED. A 12-unit sine over a 0.05/bar upward drift. The amplitude is chosen
    so the 15% take-profit and the 5% stop-loss `run_backtest` passes to VectorBT are both
    reachable, which is what makes this "a run that moved" - the premise section 2 needs. High
    and low straddle the close by 0.4% so VectorBT's intrabar stop checks have something to
    work with.
    """
    bars = []
    for i in range(count):
        close = 100.0 + 12.0 * math.sin(i / 9.0) + i * 0.05
        bars.append(
            [BASE_MS + i * 60_000, close, close * 1.004, close * 0.996, close, 10.0 + i]
        )
    return bars


def _conditions(count=BAR_COUNT):
    """Long/short condition arrays that survive `backtesting_engine.py:168-175`.

    Short on every 8th bar, long on the bar after it. The *exit-before-entry* ordering is
    mandatory, not stylistic - see THE ONE PROPERTY OF `F` in the module docstring. Anything
    else reaches `raise ValueError("Strategy generated 0 signals")` and no payload exists to
    measure.
    """
    longs = np.zeros(count, dtype=bool)
    shorts = np.zeros(count, dtype=bool)
    for i in range(count):
        if i % 8 == 0:
            shorts[i] = True
        elif i % 8 == 1:
            longs[i] = True
    return longs, shorts


def _signal_series(index):
    """The same pattern as :func:`_conditions`, as the `signals` Series the DAG engine returns.

    `run_backtest` turns this into `entries = signals > 0` / `exits = signals < 0` (:348-349)
    and hands the two `.values` to the engine, so the runtime hop and the direct engine hop are
    driven by one signal definition rather than two that could drift.
    """
    values = []
    for i in range(len(index)):
        if i % 8 == 0:
            values.append(-1)
        elif i % 8 == 1:
            values.append(1)
        else:
            values.append(0)
    return pd.Series(values, index=index)


@contextlib.contextmanager
def _without_vectorbt():
    """Make `import vectorbt` raise, driving the engine into its fallback at :189-245.

    `None` in `sys.modules` is what the interpreter itself uses to mark a failed import: the
    next `import vectorbt as vbt` raises `ImportError`, which is exactly one of the two
    exception types `_compute_vectorbt_sync` catches at :180. This is the branch a deployment
    without VectorBT installed takes, not a synthetic stand-in for it.
    """
    sentinel = object()
    saved = sys.modules.get("vectorbt", sentinel)
    sys.modules["vectorbt"] = None
    try:
        yield
    finally:
        if saved is sentinel:
            sys.modules.pop("vectorbt", None)
        else:
            sys.modules["vectorbt"] = saved


def _run_engine():
    """One real `run_backtest_async`, returned as the raw tuple the runtime unpacks.

    `asyncio.run` rather than an async fixture so the expensive VectorBT simulation can be
    module-scoped without pinning an event loop across tests. `run_backtest_async` dispatches
    through `asyncio.to_thread`, which is fine under a fresh loop.
    """
    engine = BacktestEngine(initial_capital=INITIAL_CAPITAL)
    longs, shorts = _conditions()
    return asyncio.run(
        engine.run_backtest_async(
            ohlcv_list=_bars(),
            feature_matrix=np.zeros((BAR_COUNT, 1)),
            model_path="",
            tech_long_condition=longs,
            tech_short_condition=shorts,
            params={
                "timeframe": "1m",
                "trade_size_pct": 0.10,
                "ml_threshold": 0.80,
                "stop_loss_pct": 0.05,
                "take_profit_pct": 0.15,
            },
        )
    )


@pytest.fixture(scope="module")
def vectorbt_engine_run():
    """`(results, equity_curve)` from a real VectorBT simulation. The primary path."""
    return _run_engine()


@pytest.fixture(scope="module")
def fallback_engine_run():
    """`(results, equity_curve)` from the pandas fallback at `backtesting_engine.py:230-244`."""
    with _without_vectorbt():
        return _run_engine()


@pytest.fixture(params=["vectorbt", "fallback"])
def engine_run(request, vectorbt_engine_run, fallback_engine_run):
    """Both engine paths, as Requirement 2.7 requires ("run against both engine paths").

    Parametrised rather than looped so a path that is fixed while the other is not reports as
    one passing case and one failing case.
    """
    payload = vectorbt_engine_run if request.param == "vectorbt" else fallback_engine_run
    return request.param, payload[0], payload[1]


def _runtime(sb):
    """The real `BacktestRuntime`, with the REAL engine, metrics and charts.

    Only the three collaborators this environment cannot supply are doubled: the market-data
    feed, the DAG engine, and the PostgREST client. Deliberately NOT doubled - and this is the
    difference from `test_backtest_evidence_columns_regression._runtime` - are
    `vectorbt_engine`, `_calculate_performance_metrics` and `_generate_charts`. Those three are
    the subject of this file; stubbing them would hide the defect being measured.
    """
    runtime = BacktestRuntime(initial_capital=INITIAL_CAPITAL)
    runtime.set_data_engine = lambda *_a, **_k: None
    runtime.data_engine = _StubDataEngine(_bars())
    runtime.dag_engine = _SignalDagEngine()
    runtime.backtest_service = _service(sb)
    return runtime


class _SignalDagEngine(_StubDagEngine):
    """`_StubDagEngine` with the one change the engine's entry filter forces.

    The reused double returns `pd.Series(1, ...)` - all-long, no exits - which the position-aware
    filter at :168-175 reduces to zero valid entries, so the engine raises before any payload
    exists. Overridden to return :func:`_signal_series` instead. The override is the signal
    values only; the interface is the parent's.
    """

    def execute(self, nodes, edges, market_data):
        return {"signals": _signal_series(market_data.index)}


@pytest.fixture(scope="module")
def runtime_payload():
    """The `results` dict `run_backtest` (:382-390) hands to `update_backtest_results`.

    Captured off the real call rather than reconstructed from the source expression, so a
    change to that literal is observed instead of mirrored.
    """
    captured = {}

    class _CapturingService:
        def __init__(self, inner):
            self._inner = inner

        async def create_backtest(self, **kwargs):
            return _running_row()

        async def update_backtest_results(self, **kwargs):
            captured["results"] = kwargs["results"]
            captured["executed_bar_count"] = kwargs.get("executed_bar_count")
            return await self._inner.update_backtest_results(**kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    sb = _Supabase(RECONCILED_FOR_BAR_COUNT, rows={TABLE: [_running_row()]})
    runtime = _runtime(sb)
    runtime.backtest_service = _CapturingService(_service(sb))

    async def _go():
        return await runtime.run_backtest(
            strategy_package=_StubStrategyPackage(),
            user=USER,
            strategy_id=STRATEGY_ID,
            version_id=VERSION_ID,
            version="v1",
            start_date="2024-01-01",
            end_date="2024-06-30",
            exchange_instance=object(),
        )

    outcome = asyncio.run(_go())
    assert outcome["status"] == "completed", (
        "the runtime did not complete, so there is no payload to measure. This is a harness "
        f"failure, not the defect under test: {outcome}"
    )
    assert "results" in captured, "update_backtest_results was never called"
    return captured["results"], sb


@pytest.fixture(autouse=True)
def _clean_module_state():
    """The `executed_bar_count` support memo is process-global; reset it around every test.

    Same fixture `test_backtest_evidence_columns_regression` declares. Repeated rather than
    imported because an autouse fixture does not travel with a `from … import`.
    """
    bs.reset_executed_bar_count_support()
    yield
    bs.reset_executed_bar_count_support()


def _missing(read_keys, payload_keys):
    return tuple(k for k in read_keys if k not in payload_keys)


# ══════════════════════════════════════════════════════════════════════════
# 1. THE KEY-SET CONTRACT - THE PRIMARY INSTRUMENT
#    (Requirements 1.7, 1.8, 2.7, 2.8)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("read_key", READ_KEYS)
def test_every_key_the_writer_reads_is_a_key_the_engine_emits(engine_run, read_key):
    """The whole contract, one column per case, on both engine paths.

    This is the instrument Requirement 2.7 asks for - "the writer's read-key set is a subset of
    the engine's emitted-key set … so the mismatch cannot silently return". It is asserted per
    column and per path rather than as one set comparison so the failure output IS the rename
    set: every red case names a column that task 7.x has to repoint.

    COUNTEREXAMPLES OBSERVED ON `F`.

    vectorbt path - `backtesting_engine.py:483-494` emits 12 keys::

        {calmar_ratio, expectancy, final_equity, max_drawdown_pct, profit_factor,
         sharpe_ratio, sortino_ratio, total_fees_paid, total_return_pct, total_trades,
         trades, win_rate_pct}

    fallback path - `:229-244` emits those 12 plus `equity_curve`.

    The writer reads 16. **Ten of the 16 are absent from the vectorbt payload**, and the
    ten split into two groups::

      SEVEN are real gaps - nothing downstream supplies them either:
        total_return     <- engine spells it total_return_pct     -> stored 0
        win_rate         <- engine spells it win_rate_pct         -> stored 0
        max_drawdown     <- engine spells it max_drawdown_pct     -> stored 0
        final_capital    <- engine spells it final_equity         -> see section 2
        winning_trades   <- emitted by NOTHING, on either path    -> stored 0
        losing_trades    <- emitted by NOTHING, on either path    -> stored 0
        equity_curve     <- out of band, the 2nd tuple element    -> see section 3

      THREE are legitimately not the engine's to produce, and the runtime adds them one hop
      later (`backtest_runtime.py:386-389` and `:523-529`), so they are cleared by
      `test_the_read_key_set_and_the_payload_key_set_are_not_disjoint_where_it_matters`
      rather than here:
        monthly_returns, daily_returns, execution_time_seconds

    The three are deliberately left in :data:`READ_KEYS` and left failing at this boundary. The
    alternative - curating them out - would encode today's division of labour between the two
    modules into the instrument, and the point of the instrument is to survive a change to that
    division. A red case here that the runtime-hop test clears is a *located* key, not a lost
    one, and the two tests read together say which hop owns each column.

    `winning_trades` and `losing_trades` are this measurement's addition to the rename set, and
    they are not renames at all: no producer anywhere computes them, so repointing a key cannot
    fix them. `tasks.md`'s preservation list expects them preserved; they are defaulted
    (section 7 records that). They need the engine to emit a win/loss split - a larger change
    than a rename, and one that belongs in task 7.6's scope decision alongside section 5. The
    per-trade `net_pnl` values the split would be derived from are already on the row, in
    `trades`.

    On the fallback path the same ten are missing except `equity_curve`, which that path *does*
    emit (:243) - see section 3.
    """
    path, results, _curve = engine_run
    assert read_key in results, (
        f"{path} path: update_backtest_results reads results.get({read_key!r}) at "
        f"backtest_service.py, and the engine's payload has no such key. Its 12 keys are "
        f"{sorted(results)}. The writer persists its own default instead, and the column is "
        f"rendered to the trader as a computed result."
    )


@pytest.mark.parametrize("column", REQUIREMENT_NAMED_COLUMNS)
def test_the_four_named_columns_are_not_persisted_as_the_writers_default(
    runtime_payload, column
):
    """The four columns 1.7 and 1.8 name, measured at the hop that actually writes them.

    Section 1's first test asserts the contract at the engine boundary. This one asserts it
    where the damage lands: the payload the writer receives, after `{**stats,
    **performance_metrics}` and the four literal keys at `backtest_runtime.py:386-389` have had
    their say. A fix that repointed the engine but not the runtime would pass the first and fail
    this.

    COUNTEREXAMPLE OBSERVED ON `F` - the runtime payload carries none of the four::

        total_return   absent -> stored 0
        win_rate       absent -> stored 0
        max_drawdown   absent -> stored 0
        final_capital  PRESENT, and equal to 10000.0 = the STARTING capital (section 2)
    """
    payload, _sb = runtime_payload
    assert column in payload, (
        f"the runtime payload handed to update_backtest_results has no {column!r}. Its keys "
        f"are {sorted(payload)}. backtest_service.py reads results.get({column!r}, 0) and "
        f"persists 0."
    )


def test_the_read_key_set_and_the_payload_key_set_are_not_disjoint_where_it_matters(
    runtime_payload,
):
    """One assertion over the whole set, so the *size* of the mismatch is on the record.

    The per-column tests above name each offender; this one pins the count, which is what
    detects a fix that repoints five of six columns and calls the wave done.

    COUNTEREXAMPLE OBSERVED ON `F`::

        6 of the writer's 16 read keys are absent from the payload it is handed:
        ['total_return', 'win_rate', 'max_drawdown', 'winning_trades', 'losing_trades',
         'equity_curve']

    Down from ten at the engine boundary: the runtime supplies `monthly_returns`,
    `daily_returns` and `execution_time_seconds`, so those three were located rather than
    missing. The six above are the rename set, and `final_capital` is a seventh that no set
    comparison can see because it is present-but-wrong (section 2).

    The payload the writer receives carries 26 keys, and `final_equity` is one of them - the
    correct value for `final_capital` is sitting in the same dict, under the name the engine
    gives it, two keys away from the column that defaults instead.
    """
    payload, _sb = runtime_payload
    missing = _missing(READ_KEYS, payload.keys())
    assert missing == (), (
        f"{len(missing)} of the writer's {len(READ_KEYS)} read keys are absent from the "
        f"payload it is handed: {list(missing)}. Each one persists a default that is displayed "
        f"as a measured result."
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. `final_capital` STORES THE STARTING CAPITAL, NOT ZERO
#    (`design.md` correction 1 - the finding Requirement 1.8 does not name)
# ══════════════════════════════════════════════════════════════════════════


def test_the_run_actually_moved_so_section_2_has_a_premise(vectorbt_engine_run):
    """Guard, not a defect: `final_capital != initial_capital` only means something if it moved.

    PASSES on `F`. If this ever fails, the simulation stopped trading and every assertion below
    it is vacuous rather than wrong - which is exactly the failure mode a test that asserts
    "not equal to the start" has, and the reason it is pinned separately.

    OBSERVED ON `F`: 12 closed trades, `final_equity` 10262.8346 against a 10000.0 start.
    """
    results, _curve = vectorbt_engine_run
    assert results["total_trades"] > 0, "the simulation closed no trades"
    assert results["final_equity"] != INITIAL_CAPITAL, (
        f"the simulation ended exactly where it started ({results['final_equity']}), so "
        f"'final_capital stores the starting capital' is not a distinguishable claim"
    )


def test_final_capital_is_the_equity_the_run_ended_on_not_the_capital_it_started_with(
    runtime_payload, vectorbt_engine_run
):
    """`backtest_runtime.py:388` reads a display name `stats` was already normalised away from.

    COUNTEREXAMPLE OBSERVED ON `F`::

        results["final_capital"] == 10000.0        # == initial_capital, the DEFAULT
        engine results["final_equity"] == 10262.8346   # what the run actually ended on

    `stats.get("Final Equity", self.vectorbt_engine.initial_capital)`. `Final Equity` is
    VectorBT's *display* spelling; `stats` at this point is the engine's own `results` dict,
    which normalised it to `final_equity` at `backtesting_engine.py:484`. The lookup therefore
    **misses on every single run** and the default wins unconditionally.

    Requirement 1.8 says this column "stores `0`". It does not, and the difference matters: a
    `0` final capital on a results screen is implausible enough that a trader questions it,
    whereas the starting capital is a number they recognise and will read as "this strategy
    broke exactly even". A profitable backtest and a ruinous one both render identically.
    `design.md` correction 1.
    """
    payload, _sb = runtime_payload
    engine_results, _curve = vectorbt_engine_run

    assert payload["final_capital"] != INITIAL_CAPITAL, (
        f"final_capital is {payload['final_capital']}, which is exactly initial_capital "
        f"({INITIAL_CAPITAL}). The run ended on {engine_results['final_equity']}. "
        f"stats.get('Final Equity', ...) at backtest_runtime.py:388 never matches, because the "
        f"engine emits 'final_equity' (backtesting_engine.py:484), so the default wins on "
        f"every run."
    )


def test_the_persisted_final_capital_is_not_the_writers_zero_either(runtime_payload):
    """And the column as the WRITER leaves it, which is where 1.8's `0` claim would show up.

    COUNTEREXAMPLE OBSERVED ON `F`: the persisted row carries `final_capital: 10000.0`. Not
    `0` - the writer's `results.get("final_capital", 0)` default is never reached, because the
    runtime *does* supply the key. It supplies the wrong value. Asserted separately from the
    test above so the record shows which of the two defaults won.
    """
    payload, sb = runtime_payload
    row = sb.row()
    assert row.get("final_capital") not in (0, 0.0, None), (
        f"final_capital persisted as {row.get('final_capital')!r}"
    )
    assert row.get("final_capital") != INITIAL_CAPITAL, (
        f"final_capital persisted as {row.get('final_capital')!r}, the starting capital, on a "
        f"run that moved"
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. `equity_curve` - PRESENT IN THE ENGINE'S RETURN, ABSENT FROM THE RUNTIME'S RESULTS
#    (Requirements 1.9, 2.9)
# ══════════════════════════════════════════════════════════════════════════


def test_the_engine_does_produce_a_curve(engine_run):
    """PASSES on `F`, on both paths. The curve exists; nothing below is about it being absent.

    OBSERVED ON `F`: 240 points, one per bar, `[{"timestamp": "2023-11-14T22:13:20Z",
    "equity": 10000.0}, …]`. Pinned first so the drop below cannot be misread as "the engine
    computed nothing".
    """
    path, _results, curve = engine_run
    assert len(curve) == BAR_COUNT, (
        f"{path} path: expected one equity point per bar; got {len(curve)} for {BAR_COUNT} bars"
    )
    assert set(curve[0]) == {"timestamp", "equity"}


def test_the_curve_is_in_the_vectorbt_payload_not_only_in_the_return_tuple(
    vectorbt_engine_run,
):
    """WHICH HOP DROPS IT, part 1: the engine's own payload never carries it on this path.

    COUNTEREXAMPLE OBSERVED ON `F` (`backtesting_engine.py:571`)::

        return results, eq_df.to_dict(orient="records")
               ^^^^^^^  no "equity_curve" key      ^^^ 240 points, out of band

    The curve is the SECOND TUPLE ELEMENT and was never a key of `results`. So nothing "drops"
    it at this hop - it is out of band from the start, and the drop happens at the next one
    (see below) purely by never being picked up.
    """
    results, curve = vectorbt_engine_run
    assert "equity_curve" in results, (
        f"the vectorbt path returns the {len(curve)}-point curve as the second tuple element "
        f"only; results has keys {sorted(results)}. backtest_service.py:434 reads "
        f"results.get('equity_curve', []) and persists []."
    )


def test_the_fallback_path_already_puts_the_curve_in_its_payload(fallback_engine_run):
    """PASSES on `F`. The two engine paths disagree, and the fallback is the correct one.

    `backtesting_engine.py:243` does `results["equity_curve"] = equity_curve` before returning
    the same value as the tuple element. So the curve's fate is **path-dependent**: a
    deployment without VectorBT persists a curve and a deployment with it does not. That is
    worth recording because it means 1.9's symptom ("the chart renders empty") reproduces only
    on the primary path, and a fix verified against the fallback would look green while
    changing nothing.
    """
    results, curve = fallback_engine_run
    assert results["equity_curve"] == curve, (
        "the fallback path is expected to carry the curve in its payload at :243"
    )


def test_the_runtime_carries_the_curve_through_to_the_writer(runtime_payload):
    """WHICH HOP DROPS IT, part 2 - and this is the hop that does.

    COUNTEREXAMPLE OBSERVED ON `F` (`backtest_runtime.py:353` and `:382-390`)::

        stats, equity_curve = await self.vectorbt_engine.run_backtest_async(...)
        ...
        results = {**stats, **performance_metrics, "charts": charts,
                   "execution_time_seconds": ..., "trades_count": ..., "final_capital": ...}
                   #  `equity_curve` is bound, used twice, and never put in `results`

    `run_backtest` **unpacks** the curve into a local, hands it to
    `_calculate_performance_metrics` (:369) and `_generate_charts` (:375), and then assembles
    `results` without it. The local goes out of scope. So the curve survives the engine, is read
    twice by the runtime, and is dropped at the assembly literal - one line away from the
    writer that asks for it.

    It is not lost data: `results["charts"]["equity_curve"]["values"]` carries the same series
    (:581-584) under a different shape, for the chart. The column the writer persists is empty
    while the payload it was persisted from contains the curve twice over.
    """
    payload, _sb = runtime_payload
    assert "equity_curve" in payload, (
        f"backtest_runtime.py:382-390 assembles results without the equity_curve it unpacked "
        f"at :353. Payload keys: {sorted(payload)}. "
        f"charts.equity_curve.values holds "
        f"{len(payload.get('charts', {}).get('equity_curve', {}).get('values', []))} points, "
        f"so the series is in the payload - just not under the key the writer reads."
    )


def test_the_persisted_curve_matches_the_executed_bar_count(runtime_payload):
    """Requirement 2.9's own wording: "non-empty and its length matches the executed bar count".

    COUNTEREXAMPLE OBSERVED ON `F`: the row's `equity_curve` is `[]` against an
    `executed_bar_count` of 240. The chart renders empty for a run that produced 240 points.
    """
    _payload, sb = runtime_payload
    row = sb.row()
    curve = row.get("equity_curve")
    assert curve, f"strategy_backtests.equity_curve persisted as {curve!r}"
    assert len(curve) == BAR_COUNT, (
        f"persisted curve has {len(curve)} points; the run executed {BAR_COUNT} bars"
    )


# ══════════════════════════════════════════════════════════════════════════
# 4. `total_pnl` IS COMPUTED INTO A LOG LINE AND NEVER EMITTED
#    (Requirements 1.10, 2.10)
# ══════════════════════════════════════════════════════════════════════════


def test_a_completed_backtest_yields_a_numeric_total_pnl(runtime_payload):
    """Net P&L on the Backtester has no producer anywhere in the chain.

    COUNTEREXAMPLE OBSERVED ON `F` (`backtesting_engine.py:405-410`)::

        total_pnl = final_equity - initial_equity_logged
        logger.info(f"[CAPITAL] Total PnL: ${total_pnl:.2f}")

    It is computed, formatted, written to a log, and discarded. `total_pnl` is not a key of the
    engine's `results` (:483-494), not a key of `performance_metrics` (:466-531), and not one of
    the four literals the runtime adds (:386-389). The writer never reads it, so no column
    holds it, so the field on the results screen has nothing behind it on any code path.

    Requirement 2.10 accepts either emitting it or removing the field from the UI, and is
    explicit that a permanently not-available metric on a results screen is not an acceptable
    resting state. This assertion encodes the first option; if task 7.x takes the second, this
    test is replaced by the vitest case 2.10 names, not deleted.
    """
    payload, _sb = runtime_payload
    assert "total_pnl" in payload, (
        f"no producer emits total_pnl. It exists only as the local at "
        f"backtesting_engine.py:405, consumed by the log line at :409. Payload keys: "
        f"{sorted(payload)}."
    )
    assert isinstance(payload["total_pnl"], (int, float)) and not isinstance(
        payload["total_pnl"], bool
    ), f"total_pnl is {payload['total_pnl']!r}"


# ══════════════════════════════════════════════════════════════════════════
# 5. THE BLAST RADIUS - THIS DECIDES TASK 7.6's SCOPE
# ══════════════════════════════════════════════════════════════════════════


def test_the_display_name_reads_in_the_runtime_are_the_five_tasks_md_names(runtime_payload):
    """THE MEASUREMENT `tasks.md` ASKS FOR: are those five the full extent, or only part of it?

    **ANSWER: only part of it. They are five of twelve.**

    `_calculate_performance_metrics` makes **ten** `stats.get("<VectorBT display name>")` reads,
    and `run_backtest`'s own payload literal makes **two more**. `stats` at that point is the
    engine's `results` dict, which `backtesting_engine.py:483-494` already normalised to
    snake_case, so every one of the twelve misses on every run::

        :473  Max Drawdown [%]     *  :494  Best Trade           *  :498  Win Streak
        :480  Net Profit           *  :495  Worst Trade             :499  Loss Streak
        :487  Total Trades            :502  Win Rate [%]         *  :387  Total Trades (again)
        :503  Avg Winning Trade    *  :504  Avg Losing Trade        :388  Final Equity

        (* = the five `tasks.md` names)

    The seven `tasks.md` does not name are `Total Trades` (twice), `Worst Trade`, `Win Streak`,
    `Loss Streak`, `Avg Losing Trade` and `Final Equity`. **Task 7.6 must repoint twelve reads,
    not five.** `Final Equity` is the one that also has its own requirement clause (1.8), so
    fixing it as part of 7.6 and as part of the rename set would be the same change made twice.

    This test asserts the narrow claim - that the five are the whole set - so that it FAILS and
    the wider count is on the record. It is the measurement, not an expectation.
    """
    payload, _sb = runtime_payload
    del payload  # the claim is about the source, not the payload; the fixture pins the run
    observed = tuple(sorted(DISPLAY_NAME_READS))
    named = tuple(sorted(DISPLAY_NAMES_TASKS_MD_NAMES))
    assert observed == named, (
        f"tasks.md names {len(named)} display-name reads; there are {len(observed)}. "
        f"The {len(observed) - len(named)} it does not name: "
        f"{sorted(set(observed) - set(named))}. Task 7.6's scope is the larger set."
    )


@pytest.mark.parametrize(
    "column", EXPECTED_FABRICATED + FABRICATED_BEYOND_TASKS_MD
)
def test_no_column_is_fabricated_by_a_display_name_read_that_never_matches(
    runtime_payload, column
):
    """Every column that collapses because its `stats` lookup missed. One case each.

    COUNTEREXAMPLES OBSERVED ON `F` - each of these is `0`/`0.0` on a run that closed 12 trades
    and made 262.83::

        calmar_ratio        0.0   <- Max Drawdown [%] missed -> max_dd = 0 -> else branch
        recovery_factor     0.0   <- Net Profit + Max Drawdown [%] both missed
        average_trade       0.0   <- Net Profit + Total Trades both missed
        largest_win         0     <- Best Trade missed
        largest_loss        0     <- Worst Trade missed
        consecutive_wins    0     <- Win Streak missed
        consecutive_losses  0     <- Loss Streak missed
        expectancy          0.0   <- Win Rate [%] / Avg Winning Trade / Avg Losing Trade missed
        kelly               0.0   <- same three
        sqn                 0.0   <- Total Trades missed  (NOT on tasks.md's list)
        trades_count        0     <- Total Trades missed again, at :387  (NOT on tasks.md's list)
        final_capital  10000.0    <- Final Equity missed. NOT parametrised here: it fabricates
                                     a non-zero, so this assertion cannot see it. Section 2.

    `sqn`, `trades_count` and `final_capital` are this measurement's three additions to
    `tasks.md`'s expected nine, so the blast radius is **twelve columns, not nine**.
    `trades_count` is the one worth noting twice: the engine emits a real `total_trades` of 12,
    and the runtime publishes `trades_count: 0` **alongside it** in the same dict, so the
    payload contradicts itself about how many trades the run made.

    `calmar_ratio` and `expectancy` are worse than fabricated - they are real values
    OVERWRITTEN by these zeros. Section 6 owns that.

    Note what is NOT here: `sortino_ratio` is derived from the equity series rather than from
    `stats`, so it has a genuine producer in `_calculate_performance_metrics` and is outside
    this cause's blast radius.

    ADJACENT FINDING, RECORDED AND NOT ASSERTED - `monthly_returns` and `daily_returns` are
    `[]` on every run, and the cause is NOT a display name. `_calculate_performance_metrics`
    builds `equity_series` from the list-of-dicts with a default RangeIndex (:449), then does
    `equity_series.index = pd.to_datetime(equity_series.index)` (:524) - which reads `0..239`
    as **nanoseconds since the epoch**. All 240 points land inside one 240-nanosecond window in
    January 1970, so `resample('M')` and `resample('D')` each yield a single bucket and
    `pct_change().dropna()` yields nothing. Both columns persist empty, and both are in the
    writer's read-key set, so the key-set instrument reports them present and correct. Out of
    scope for task 1's six items and deliberately left unasserted here, but it is a third
    distinct cause inside the same method and task 7.6's scope decision should account for it.
    """
    payload, _sb = runtime_payload
    value = payload.get(column)
    assert value not in (0, 0.0), (
        f"{column} is {value!r} on a run that closed 12 trades and moved 262.83. It is "
        f"fabricated by a stats.get('<display name>') that cannot match, because the engine "
        f"normalised those names away at backtesting_engine.py:483-494."
    )


# ══════════════════════════════════════════════════════════════════════════
# 6. `{**stats, **performance_metrics}` OVERWRITES THE ENGINE'S REAL VALUES
#    (`design.md` correction 2)
# ══════════════════════════════════════════════════════════════════════════


def test_the_engines_computed_expectancy_survives_the_dict_merge(
    runtime_payload, vectorbt_engine_run
):
    """The merge order at `backtest_runtime.py:383-384` silently destroys a real figure.

    COUNTEREXAMPLE OBSERVED ON `F`::

        engine   expectancy  21.9029   # (win_rate * avg_win) - ((1 - win_rate) * avg_loss),
                                       # from portfolio.trades.pnl - backtesting_engine.py:456-465
        payload  expectancy   0.0       # performance_metrics' else branch, because
                                       # stats.get("Avg Losing Trade", 0) == 0

    `{**stats, **performance_metrics}` puts `performance_metrics` SECOND, so on every key the
    two share, the runtime's value wins. The engine computed `expectancy` from the actual trade
    P&L array; the runtime computed it from three display names that do not exist; the second
    one is what is persisted.

    This is `design.md` correction 2, and it contradicts Requirement 3.4's premise. 3.4 lists
    `expectancy` among the columns that are "correctly persisted today" and asks that they be
    preserved. They are not correctly persisted - and worse, the correct value is *present in
    the same dict* right up until the merge overwrites it. Preserving today's stored value would
    preserve the bug, which is why section 7 captures from the engine's output instead.
    """
    payload, _sb = runtime_payload
    engine_results, _curve = vectorbt_engine_run
    assert payload["expectancy"] == engine_results["expectancy"], (
        f"the engine computed expectancy {engine_results['expectancy']} from the real trade "
        f"P&L; the payload carries {payload['expectancy']}. performance_metrics' expectancy "
        f"(backtest_runtime.py:502-509) overwrote it because it is second in "
        f"{{**stats, **performance_metrics}} at :383-384."
    )


def test_the_engines_computed_calmar_ratio_survives_the_dict_merge(
    runtime_payload, vectorbt_engine_run
):
    """`calmar_ratio` is overwritten the same way, and `tasks.md` does not name it as such.

    COUNTEREXAMPLE OBSERVED ON `F`::

        engine   calmar_ratio  2.7933492465330715e+26   # VectorBT's own Calmar Ratio stat,
                                                        # backtesting_engine.py:491
        payload  calmar_ratio  0.0                      # stats.get("Max Drawdown [%]", 0)
                                                        # missed -> max_dd 0 -> else branch

    So `calmar_ratio` belongs to BOTH the section 5 fabrication list and this section's
    overwrite list: the value thrown away is a measured one. Recorded separately because a task
    7.6 that repointed all twelve display names would still have the merge order backwards.

    The magnitude is an artifact of the supplied bars, not a finding: VectorBT annualises Calmar
    against the declared `1m` frequency, and this run's max drawdown is 1.6956%, so the ratio
    explodes. That the number is implausible is beside the point - it is VectorBT's answer, it
    is the one the engine chose to emit, and it is discarded. If task 7.x decides the runtime's
    Calmar is the better definition, then the engine should stop emitting one; two producers
    silently racing on one column is the defect either way.
    """
    payload, _sb = runtime_payload
    engine_results, _curve = vectorbt_engine_run
    assert payload["calmar_ratio"] == engine_results["calmar_ratio"], (
        f"engine calmar_ratio {engine_results['calmar_ratio']} was overwritten with "
        f"{payload['calmar_ratio']} by the merge at backtest_runtime.py:383-384"
    )


def test_sortino_ratio_comes_from_the_runtime_not_the_engine(
    runtime_payload, vectorbt_engine_run
):
    """PASSES on `F`. Not a defect - a fact about the producer, pinned so section 7 is honest.

    Both layers emit `sortino_ratio`: the engine from VectorBT's `Sortino Ratio` stat
    (`backtesting_engine.py:490`), the runtime from the equity series' downside deviation
    (`backtest_runtime.py:467-471`). The merge gives the runtime's. **Both are real
    measurements** - unlike `expectancy` and `calmar_ratio`, nothing is fabricated here - but
    they are different numbers from different methods.

    That is why `sortino_ratio` is excluded from section 7's preservation list even though
    Requirement 3.4 includes it: "preserve the engine's value" is not a meaningful instruction
    for a column the engine does not produce. `design.md` correction 2.

    OBSERVED ON `F`::

        engine   sortino_ratio  163.8778             # VectorBT's Sortino Ratio stat
        payload  sortino_ratio    4.2796064602738…   # returns.mean() / downside.std() * √252

    Two producers, two answers 38x apart, and no way to tell from the persisted column which
    one wrote it.
    """
    payload, _sb = runtime_payload
    engine_results, _curve = vectorbt_engine_run
    assert isinstance(payload["sortino_ratio"], (int, float))
    assert payload["sortino_ratio"] != engine_results["sortino_ratio"], (
        "the two producers happened to agree on this run, which makes this file unable to "
        "show that sortino_ratio has two of them. Not a defect - but design.md correction 2 "
        "is then unevidenced here and should be re-measured on a run where they differ."
    )


# ══════════════════════════════════════════════════════════════════════════
# 7. PRESERVATION  (task 2, Requirements 3.4 and 3.5)
#    EXPECTED TO PASS ON `F`. This is the baseline the wave must not disturb.
# ══════════════════════════════════════════════════════════════════════════

#: The columns that genuinely round-trip from the engine's output to the persisted row, captured
#: **from the engine's output rather than from today's stored values** exactly as task 2
#: requires. `tasks.md`'s list is ten names; two of them are not here, and each exclusion is a
#: finding rather than an omission:
#:
#:   expectancy      - overwritten with 0.0 by the merge (section 6). Nothing to preserve.
#:   sortino_ratio   - produced by the runtime, not the engine (section 6). Not the engine's to
#:                     preserve.
#:
#: Capturing those two from today's row would pin the bug in place, which is the failure mode
#: `tasks.md`'s "from the engine's output, not from today's stored values" exists to prevent.
#:
#: TASK 7.6 UPDATE - `winning_trades` and `losing_trades` were excluded here for the same
#: reason, and are now included: they had no producer on either path and persisted as the
#: writer's `0`, and task 7.6 gave them one. `backtesting_engine` derives the split from the
#: per-trade `net_pnl` on the `trades` rows it already emits, so the same assertion that covers
#: the other four columns covers these two - the engine's count, byte for byte, on the row. This
#: is the inversion the stale-baseline test below asked for.
PRESERVED_FROM_ENGINE = (
    "total_return_pct",
    "sharpe_ratio",
    "profit_factor",
    "total_trades",
    "winning_trades",
    "losing_trades",
)


@pytest.mark.parametrize("column", PRESERVED_FROM_ENGINE)
def test_preserved_engine_metric_reaches_the_row_unchanged(
    runtime_payload, vectorbt_engine_run, column
):
    """The engine's own figure, byte for byte, in `strategy_backtests`.

    PASSES on `F` and must keep passing through every task-7 change. These four are the columns
    whose name the engine and the writer already agree on and whose value no later layer
    overwrites, so they are the real content of Requirement 3.4.

    OBSERVED ON `F`: `total_return_pct` 2.6283, `sharpe_ratio` 0.0, `profit_factor` 1.6471,
    `total_trades` 12.

    `sharpe_ratio` is `0.0` here and that is CORRECT, not absent: `backtesting_engine.py:420`
    only computes a Sharpe once there are at least 20 trades for statistical significance, and
    this run closed 12. A genuine `0.0` stays `0.0` - the same rule wave 1 turns on, asserted
    here so a task-7 change cannot "fix" this column into a `None`.
    """
    _payload, sb = runtime_payload
    engine_results, _curve = vectorbt_engine_run
    row = sb.row()
    assert row[column] == engine_results[column], (
        f"{column} was {engine_results[column]!r} at the engine and is {row.get(column)!r} on "
        f"the row"
    )


def test_preserved_execution_envelope_is_recorded(runtime_payload):
    """`status`, `completed_at`, `execution_time_seconds`, `trades` and the bar count.

    PASSES on `F`. These five are set by the writer and the runtime rather than measured by the
    engine, so they are pinned by shape and provenance rather than by value.

    OBSERVED ON `F`: `status` "completed", `completed_at` an ISO-8601 instant,
    `execution_time_seconds` 0.184, `trades` 12 records each carrying
    `entry_price`/`exit_price`/`net_pnl`/`status`, `executed_bar_count` 240.

    `trades` is the one that matters most: it is the only column on this row that carries
    per-trade detail, it survives both the merge and the key mismatch, and
    `backtesting_engine.py:504-565` is a recently-repaired path
    (`portfolio.trades.records_readable`). A task-7 rename that touched the payload assembly
    could plausibly drop it.
    """
    _payload, sb = runtime_payload
    row = sb.row()

    assert row["status"] == "completed"
    assert row["completed_at"], "the row is timestamped"
    assert isinstance(row["execution_time_seconds"], float)
    assert row["execution_time_seconds"] > 0
    assert row.get("executed_bar_count") == BAR_COUNT, (
        "marketplace task 12.1's bar count must keep landing; wave 2 touches the same write"
    )

    trades = row["trades"]
    assert len(trades) == row["total_trades"] > 0
    assert {"entry_price", "exit_price", "net_pnl", "status"} <= set(trades[0])


@pytest.mark.parametrize(
    "split_column, counts",
    (("winning_trades", "wins"), ("losing_trades", "losses")),
)
def test_preserved_win_loss_split_is_derived_from_the_trade_rows(
    runtime_payload, split_column, counts
):
    """The inversion the stale baseline asked for: the split against the rows it comes from.

    WHAT THIS REPLACED, and why the replacement is not a weakening.
        Until task 7.6 this was `test_preserved_nothing_is_being_preserved_for_the_unproduced_
        columns`, and it asserted `winning_trades not in payload` and `row[column] == 0`. That
        was the honest baseline at the time: Requirement 3.4 lists both columns among the
        "correctly persisted" ones to preserve, and both were in fact the writer's `0` default
        on a run that closed 12 trades. The old test's own instruction was "this is the
        assertion to invert when task 7.x gives them a producer".

        Task 7.6 gave them one, so the `== 0` half is now the wrong assertion to hold. It is
        replaced by a *stronger* one rather than deleted:
        :data:`PRESERVED_FROM_ENGINE` now covers both columns, which pins each against the
        engine's own count byte for byte, and this test pins that count against the `trades`
        rows on the persisted row - so the split cannot drift from the per-trade detail a
        trader can expand and check, in either direction.

    WHY `net_pnl` AND NOT THE GROSS FIGURE
        A trade whose fees exceed its gross profit is a loss. Deriving the split from
        `gross_pnl` would count it as a win, which is how a fee-heavy strategy comes to read
        as profitable. The engine derives from `net_pnl`; this asserts against the same field.

    Break-even trades (`net_pnl == 0`) are in neither count, so the two need not sum to
    `total_trades` and this deliberately does not assert that they do.
    """
    _payload, sb = runtime_payload
    row = sb.row()

    net_pnls = [float(trade["net_pnl"]) for trade in row["trades"]]
    assert net_pnls, "no trade rows on the persisted row, so there is no split to check"

    if counts == "wins":
        expected = sum(1 for pnl in net_pnls if pnl > 0)
    else:
        expected = sum(1 for pnl in net_pnls if pnl < 0)

    assert row[split_column] == expected, (
        f"{split_column} persisted as {row[split_column]!r}; the {len(net_pnls)} net_pnl "
        f"values on the same row give {expected}"
    )
    assert row["winning_trades"] + row["losing_trades"] <= row["total_trades"], (
        f"the split ({row['winning_trades']} + {row['losing_trades']}) exceeds "
        f"total_trades ({row['total_trades']})"
    )


# ── The ownership predicate. It must not regress. (Requirement 3.5) ──────


def _owned_row():
    return _running_row(id="44444444-4444-4444-8444-444444444444", user_id=USER_ID)


@pytest.mark.asyncio
async def test_preserved_update_is_scoped_by_user_id_as_well_as_id():
    """Both `.eq` filters reach PostgREST. This predicate is why 1.14 cites it as precedent.

    PASSES on `F`. `update_backtest_results` filtered on `id` alone until it was found, which
    made it a cross-tenant WRITE - a non-owner's metrics landed on the owner's row - and an
    existence oracle, because the response echoed the row it had just overwritten. Wave 2
    rewrites this method's `update_data`, so the predicate sits one line from the change and is
    the easiest thing in the wave to lose by accident.

    Asserted on the filters the client received, not on the outcome, so the test fails on the
    predicate's removal rather than on a fixture that happens not to exercise it.
    """
    sb = _Supabase(RECONCILED_FOR_BAR_COUNT, rows={TABLE: [_owned_row()]})
    service = _service(sb)

    result = await service.update_backtest_results(
        user=USER,
        backtest_id="44444444-4444-4444-8444-444444444444",
        results={"total_return_pct": 1.0},
    )

    assert result, "the owner's own row must be updated"
    (_table, _payload, filters), = [u for u in sb.updates if u[0] == TABLE]
    assert filters == {
        "id": "44444444-4444-4444-8444-444444444444",
        "user_id": USER_ID,
    }, f"the UPDATE was scoped by {sorted(filters)} - Requirement 20.1 needs both"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "backtest_id, why",
    [
        ("44444444-4444-4444-8444-444444444444", "the row exists but belongs to another user"),
        ("99999999-9999-4999-8999-999999999999", "no row anywhere carries this id"),
    ],
)
async def test_preserved_non_owned_and_nonexistent_are_indistinguishable(backtest_id, why):
    """`{}` for both, and no write in either case. The existence oracle stays closed.

    PASSES on `F`. Requirement 20.2 / 3.5: a caller must not be able to learn that a backtest
    exists by observing a different answer for "not yours" than for "not there". Both cases are
    parametrised into one test so a fix that distinguishes them - a 404 for one and a 403 for
    the other - fails on exactly one of the two and names which.

    The `no write` half is asserted as hard as the `{}` half: an UPDATE that matched nothing
    still tells the caller nothing, but an UPDATE that matched a foreign row tells them
    everything, and the return value alone cannot tell those apart.
    """
    foreign = _running_row(
        id="44444444-4444-4444-8444-444444444444", user_id="someone-else-entirely"
    )
    sb = _Supabase(RECONCILED_FOR_BAR_COUNT, rows={TABLE: [foreign]})
    service = _service(sb)

    result = await service.update_backtest_results(
        user=USER, backtest_id=backtest_id, results={"total_return_pct": 99.0}
    )

    assert result == {}, f"{why}: expected an indistinguishable empty answer, got {result!r}"
    assert sb.row()["status"] == "running", (
        f"{why}: the foreign row was written to. This is the cross-tenant write Requirement "
        f"1.14 names as the precedent for the whole IDOR sweep."
    )
