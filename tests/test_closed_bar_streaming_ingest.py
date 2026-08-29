"""tests/test_closed_bar_streaming_ingest.py - the live streaming path serves closed bars.

Strategy-builder task 7.10. Requirements 19.2, 19.3, 19.4.

WHAT THIS FILE IS FOR
---------------------
Task 7.5 put a closed-bar gate in front of the *read* paths (node preview, data-quality
report) and disclosed, accurately, that ``dag_event_loop``'s tick path was left unrouted.
That path is the one with an order router at the end of it: ``_process_event`` fed a tick
into ``RollingWindow.add_tick``, which mutated ``highs[-1]`` / ``lows[-1]`` / ``closes[-1]``
into a **forming** candle, and handed ``to_dataframe()`` straight to the ``DAGEngine``. One
bar timestamp therefore produced a different indicator value on every tick, which is exactly
what Requirement 19.2 forbids.

So the assertions here are about the invariant, not about a row count:

* **19.2** - what an executor receives holds one row per timestamp, and that row holds the
  bar's *closed* values, no matter how many ticks or forming updates arrived for it.
* **19.3** - an arrival older than the last bar computed on is discarded and counted.
* **19.4** - a repeated timestamp keeps the **first** candle, and is counted as a duplicate
  rather than as a late event.

WHAT IS REAL HERE AND WHAT IS SUPPLIED
--------------------------------------
Real: the shipped ``DAGEventLoop``, its ``RollingWindow``, the real
``market_data_contract.ClosedBarIngest`` reached through the loop's own gate, and the real
``TIMEFRAME_MINUTES`` bar-length vocabulary. Nothing is mocked - a test that mocks the gate
proves only that the mock was called.

Supplied: the clock and the events. No exchange, no Redis and no market data socket is
reachable in this environment, so no test can watch a real tick arrive or a real bar close.
The clock is therefore **injected** (``DAGEventLoop(clock=...)``, which the ingest contract
already supported through ``now=``) so that "this bar has closed" is *stated* rather than
raced, and the events are hand-built ``MarketEvent`` objects of the shape
``WebSocketEventSource._handle_message`` constructs.

``_process_event`` itself is only exercised as far as the seam: past the seam it takes a
Redis advisory lock, and there is no Redis here. The seam is ``_ingest_event``, which is the
single place an arriving event becomes a row an executor may compute on, and it is called by
both ``DAGEventLoop._process_event`` and ``RiskIntegratedEventLoop._process_event``.

No ``hypothesis`` import, for the reason task 7.5's file gives: ``design.md`` numbers the
properties this spec generates tests for, and an unnumbered property would put a test in the
suite no design document accounts for. What stands in for it is exhaustive on a small space -
every arrival order of a three-bar batch, and the closed-bar boundary from both sides.
"""
import itertools
from datetime import datetime, timedelta

import pandas as pd
import pytest

from backend_app.backend.dag_event_loop import (
    ClosedBarGate,
    DAGEventLoop,
    EventType,
    MarketEvent,
    RollingWindow,
    TickBarBuilder,
)
from backend_app.backend.market_data_contract import (
    ClosedBarIngest,
    MarketDataContractError,
    interval_for,
    to_utc_naive,
)

# The instant every test states rather than races. A 5m bar opening at or before
# `NOW - 5min` is closed; anything later is still forming.
NOW = datetime(2024, 3, 1, 12, 0, 0)
SYMBOL = "ETH/USDT"
TIMEFRAME = "5m"
INTERVAL = timedelta(minutes=5)


def at(minutes_before_now: int) -> datetime:
    return NOW - timedelta(minutes=minutes_before_now)


def candle(moment: datetime, close: float, *, is_closed=None) -> MarketEvent:
    """One CANDLE event whose five values are all different, so a swapped column shows."""
    return MarketEvent(
        event_type=EventType.CANDLE,
        symbol=SYMBOL,
        timestamp=moment,
        open=close - 0.25,
        high=close + 1.5,
        low=close - 0.75,
        close=close,
        volume=1_000.0,
        timeframe=TIMEFRAME,
        source="task-7.10-test",
        is_closed=is_closed,
    )


def tick(moment: datetime, price: float, size: float = 0.5) -> MarketEvent:
    return MarketEvent(
        event_type=EventType.TICK,
        symbol=SYMBOL,
        timestamp=moment,
        price=price,
        size=size,
        side="buy",
        timeframe=TIMEFRAME,
        source="task-7.10-test",
    )


def loop(*, clock_now: datetime = NOW, timeframe: str = TIMEFRAME) -> DAGEventLoop:
    """A real event loop with one symbol, a stated clock and a trivial node list.

    ``dag_nodes`` is a single node because nothing here executes the DAG: the assertions are
    about which rows reach the window that an executor would read.
    """
    return DAGEventLoop(
        dag_nodes=[{"id": "n1", "type": "indicator", "indicator": "rsi"}],
        dag_edges=[],
        symbols=[SYMBOL],
        timeframe=timeframe,
        clock=lambda: clock_now,
    )


def window_of(instance: DAGEventLoop) -> RollingWindow:
    return instance.rolling_windows[SYMBOL]


# ---------------------------------------------------------------------------
# 1. Requirement 19.2 - a forming bar never reaches the executor's window
# ---------------------------------------------------------------------------


class TestFormingBarsNeverReachTheWindow:
    def test_the_invariant_one_timestamp_one_row_holding_the_closed_values(self):
        """The headline: ticks move a bar, and the window sees it once, closed.

        Twelve ticks land inside one 5-minute bar and the next bar's first tick closes it.
        Before task 7.10 each of those twelve ticks appended-or-mutated a row and triggered
        an evaluation, so the same timestamp carried twelve different closes. Here the window
        holds exactly one row for that bar, and its high, low and close are the aggregate of
        every tick rather than whichever tick arrived last.
        """
        instance = loop()
        bar_open = at(20)
        prices = [100.0, 101.0, 99.5, 103.0, 98.0, 102.0, 100.5, 101.5, 99.0, 104.0,
                  97.5, 100.25]
        for offset, price in enumerate(prices):
            admitted = instance._ingest_event(
                tick(bar_open + timedelta(seconds=offset * 20), price)
            )
            assert admitted is False, (
                "a tick inside a forming bar must not produce a row an indicator can be "
                f"computed on (tick {offset} at {price})"
            )

        assert len(window_of(instance)) == 0, (
            "the forming bar must be held outside the window that feeds computation"
        )

        # The next interval's first tick proves the previous bar finished.
        assert instance._ingest_event(tick(bar_open + INTERVAL, 105.0)) is True

        frame = window_of(instance).to_dataframe()
        assert len(frame.index) == 1
        assert frame.index.is_unique
        assert frame.index[0] == to_utc_naive(bar_open)
        row = frame.iloc[0]
        assert row["open"] == 100.0
        assert row["high"] == 104.0
        assert row["low"] == 97.5
        assert row["close"] == 100.25
        assert row["volume"] == pytest.approx(0.5 * len(prices))

    def test_a_candle_event_for_a_bar_still_forming_is_dropped_and_counted(self):
        """A feed that publishes the in-progress bar (Binance's `x: false` kline) is refused."""
        instance = loop()
        assert instance._ingest_event(candle(NOW, 500.0)) is False
        assert len(window_of(instance)) == 0
        counters = instance.closed_bar_gates[SYMBOL].counters
        assert counters.forming_dropped == 1
        assert counters.accepted == 0

    def test_the_bar_that_just_closed_is_admitted(self):
        """The rule is "the interval elapsed", not "one whole bar of slack".

        A gate that also refused the just-finished bar would delay every signal by a full
        interval, which is a different defect wearing the same fix.
        """
        instance = loop()
        assert instance._ingest_event(candle(NOW - INTERVAL, 42.0)) is True
        assert len(window_of(instance)) == 1

    def test_one_second_short_of_closed_is_refused(self):
        instance = loop()
        nearly = NOW - INTERVAL + timedelta(seconds=1)
        assert instance._ingest_event(candle(nearly, 42.0)) is False
        assert instance.closed_bar_gates[SYMBOL].counters.forming_dropped == 1

    def test_a_feed_saying_forming_outranks_the_clock(self):
        """``design.md``'s canonical ``Candle.is_closed``: the feed knows, inference guesses."""
        instance = loop()
        old_enough = at(60)
        assert instance._ingest_event(candle(old_enough, 42.0, is_closed=False)) is False
        assert len(window_of(instance)) == 0
        assert instance._ingest_event(candle(old_enough, 42.0, is_closed=True)) is True
        assert len(window_of(instance)) == 1

    def test_add_tick_is_refused_outright(self):
        """The forming-bar constructor is gone, and it fails loudly rather than quietly."""
        window = RollingWindow(symbol=SYMBOL, timeframe=TIMEFRAME)
        with pytest.raises(RuntimeError) as caught:
            window.add_tick(tick(at(30), 100.0))
        assert "19.2" in str(caught.value) or "closed" in str(caught.value)
        assert len(window) == 0


# ---------------------------------------------------------------------------
# 2. Requirement 19.4 - first wins on a duplicate, on this path too
# ---------------------------------------------------------------------------


class TestFirstWinsOnDuplicates:
    def test_a_repeated_candle_keeps_the_first_and_counts_a_duplicate(self):
        instance = loop()
        moment = at(15)
        assert instance._ingest_event(candle(moment, 100.0)) is True
        assert instance._ingest_event(candle(moment, 999.0)) is False

        frame = window_of(instance).to_dataframe()
        assert len(frame.index) == 1
        assert frame.iloc[0]["close"] == 100.0, (
            "the later arrival must not revise a bar the runtime may already have "
            "computed a signal from (Requirement 19.4)"
        )
        counters = instance.closed_bar_gates[SYMBOL].counters
        assert counters.duplicates == 1
        assert counters.late_events == 0, (
            "a re-sent bar is a duplicate, not a late event: conflating them makes both "
            "figures unreadable"
        )

    def test_the_window_itself_refuses_a_duplicate_as_a_last_line(self):
        """``RollingWindow`` is written directly by the golden-plan test, so it guards too."""
        window = RollingWindow(symbol=SYMBOL, timeframe=TIMEFRAME)
        moment = to_utc_naive(at(15))
        assert window.append_bar(moment, 1.0, 2.0, 0.5, 1.5, 10.0) is True
        assert window.append_bar(moment, 9.0, 9.0, 9.0, 9.0, 9.0) is False
        assert len(window) == 1
        assert window.to_dataframe().iloc[0]["close"] == 1.5

    def test_a_drained_gate_still_catches_the_duplicate(self):
        """The streaming gate releases its bars; the high-water mark is what keeps 19.4.

        ``ClosedBarIngest.drain`` empties the stored bars so a deployment's gate stays
        bounded. Without the high-water comparison in ``offer`` a re-sent copy of the most
        recent bar would find an empty store and be admitted a second time.
        """
        gate = ClosedBarIngest(TIMEFRAME, drop_late=True, now=NOW)
        row = {"open_time": at(15), "open": 1.0, "high": 2.0, "low": 0.5,
               "close": 1.5, "volume": 10.0}
        assert gate.offer(row) is True
        assert gate.drain()
        assert gate.offer(dict(row, close=999.0)) is False
        assert gate.counters.duplicates == 1
        assert gate.drain() == []


# ---------------------------------------------------------------------------
# 3. Requirement 19.3 - a late arrival is dropped and counted
# ---------------------------------------------------------------------------


class TestLateArrivals:
    def test_a_candle_older_than_the_last_admitted_bar_is_dropped_and_counted(self):
        instance = loop()
        assert instance._ingest_event(candle(at(10), 100.0)) is True
        assert instance._ingest_event(candle(at(25), 50.0)) is False

        frame = window_of(instance).to_dataframe()
        assert len(frame.index) == 1
        assert frame.iloc[0]["close"] == 100.0
        counters = instance.closed_bar_gates[SYMBOL].counters
        assert counters.late_events == 1
        assert counters.out_of_order == 1
        assert counters.duplicates == 0

    def test_a_tick_belonging_to_an_already_released_bar_changes_nothing(self):
        """A tick cannot revise a bar the gate has already handed to the window."""
        instance = loop()
        bar_open = at(30)
        instance._ingest_event(tick(bar_open, 100.0))
        assert instance._ingest_event(tick(bar_open + INTERVAL, 200.0)) is True
        released = window_of(instance).to_dataframe().iloc[0].to_dict()

        assert instance._ingest_event(tick(bar_open + timedelta(seconds=30), 999.0)) is False
        assert window_of(instance).to_dataframe().iloc[0].to_dict() == released

    def test_the_loops_own_out_of_order_guard_still_counts_the_event(self):
        """STEP 4.5's guard returns before the seam; the tally must not lose those events.

        Requirement 19.3 asks that a late event be discarded **and** counted. The existing
        guard did the first only, and it is kept as-is because it is a control - so it reports
        into the gate's counters rather than starting a second set of figures.
        """
        instance = loop()
        gate = instance.closed_bar_gates[SYMBOL]
        instance._last_event_timestamp = {SYMBOL: at(5)}

        import asyncio

        asyncio.run(instance._process_event(candle(at(40), 100.0)))

        assert gate.counters.late_events == 1
        assert gate.counters.out_of_order == 1
        assert len(window_of(instance)) == 0


# ---------------------------------------------------------------------------
# 4. Ordering: what an executor reads is monotonic whatever order arrived
# ---------------------------------------------------------------------------


class TestOrdering:
    @pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
    def test_every_arrival_order_of_three_bars_yields_a_monotonic_unique_frame(self, order):
        """Exhaustive over the six orders rather than sampled.

        A streaming gate drops what arrives late instead of re-sorting it (``design.md``:
        "re-sort historical; for live, drop late events older than the last closed bar and
        count them"), so the count depends on the order - but the frame an executor reads is
        strictly increasing and unique in **every** order, which is the property that keeps
        one timestamp to one value.
        """
        instance = loop()
        moments = [at(30), at(25), at(20)]
        for index in order:
            instance._ingest_event(candle(moments[index], 100.0 + index))

        frame = window_of(instance).to_dataframe()
        assert frame.index.is_monotonic_increasing
        assert frame.index.is_unique
        counters = instance.closed_bar_gates[SYMBOL].counters
        assert counters.offered == 3
        assert counters.accepted + counters.late_events + counters.duplicates == 3
        # The bar that arrived first is always kept: nothing is ever revised.
        assert frame.iloc[0]["close"] == 100.0 + order[0]


# ---------------------------------------------------------------------------
# 5. The tick builder: a tick is input for a price, not for an indicator
# ---------------------------------------------------------------------------


class TestTickBarBuilder:
    def test_the_last_price_is_updated_by_every_tick(self):
        """Ticks are not discarded - they are simply not indicator input.

        This is what keeps a stop check or position sizing on live data while indicator
        computation waits for the bar to close.
        """
        builder = TickBarBuilder(SYMBOL, TIMEFRAME, INTERVAL)
        for offset, price in enumerate((100.0, 101.0, 99.0)):
            builder.observe(price, 1.0, at(30) + timedelta(seconds=offset))
        assert builder.last_price == 99.0
        assert builder.candle()["close"] == 99.0

    def test_the_loop_exposes_the_last_price_from_a_tick_that_closed_no_bar(self):
        instance = loop()
        instance._ingest_event(tick(at(12), 123.5))
        assert instance.closed_bar_gates[SYMBOL].last_price == 123.5
        assert len(window_of(instance)) == 0

    def test_buckets_align_with_the_interval_grid_not_with_the_first_tick(self):
        """A tick-built bar carries the open time an exchange's own bar would.

        A bar opened at "whenever the first tick arrived" would look like a duplicate or a
        late arrival to the gate as soon as the same symbol also received candle events.
        """
        builder = TickBarBuilder(SYMBOL, TIMEFRAME, INTERVAL)
        moment = datetime(2024, 3, 1, 11, 43, 17)
        assert builder.bucket(moment) == pd.Timestamp("2024-03-01 11:40:00")

    def test_an_unreadable_tick_changes_nothing(self):
        builder = TickBarBuilder(SYMBOL, TIMEFRAME, INTERVAL)
        assert builder.observe(None, 1.0, at(30)) is None
        assert builder.observe(float("nan"), 1.0, at(30)) is None
        assert builder.observe(100.0, 1.0, "not-a-time") is None
        assert builder.candle() is None


# ---------------------------------------------------------------------------
# 6. The gate is the platform's one gate, in the mode the path requires
# ---------------------------------------------------------------------------


class TestItIsOneGateNotTwo:
    def test_the_streaming_gate_drops_late_arrivals_rather_than_re_sorting(self):
        """`drop_late=True` is the mode rule for a path with an order router downstream."""
        gate = ClosedBarGate(SYMBOL, TIMEFRAME, now=NOW)
        assert gate._ingest.drop_late is True
        assert gate.to_dict()["drop_late"] is True
        assert gate.to_dict()["closed_bars_only"] is True

    def test_the_bar_length_comes_from_the_pipelines_own_vocabulary(self):
        gate = ClosedBarGate(SYMBOL, "1h", now=NOW)
        assert gate.interval == interval_for("1h")

    def test_the_event_loop_reads_the_contract_and_defines_no_second_rule(self):
        """Structural: one closed-bar rule, one interval table, one duplicate policy."""
        import inspect

        import backend_app.backend.dag_event_loop as DEL

        source = inspect.getsource(DEL)
        assert "market_data_contract" in source, (
            "the streaming path must route through the platform's ingest contract"
        )
        assert "ClosedBarIngest" in source
        # The tell-tales of a second gate: a local interval table, or a closed-bar rule
        # written out again instead of asked for.
        assert '"15m": 15' not in source and "'15m': 15" not in source
        assert "closed_before" not in source, (
            "the closed-bar boundary belongs to market_data_contract; a copy here would "
            "drift from the one the preview and the backtester use"
        )

    def test_the_risk_integrated_loop_uses_the_same_seam(self):
        """`RiskIntegratedEventLoop` overrides `_process_event`, so it is checked separately."""
        import inspect

        import backend_app.backend.dag_risk_integration as DRI
        from backend_app.backend.dag_risk_integration import RiskIntegratedEventLoop

        source = inspect.getsource(RiskIntegratedEventLoop._process_event)
        assert "_ingest_event" in source
        assert "add_tick" not in inspect.getsource(DRI), (
            "the forming-bar builder must not be reachable from the risk-integrated path"
        )
        # And it is the inherited seam, not a second copy.
        assert RiskIntegratedEventLoop._ingest_event is DAGEventLoop._ingest_event

    def test_the_counters_are_the_contracts_own(self):
        instance = loop()
        state = instance.market_data_state()[SYMBOL]
        assert state["counters"] == instance.closed_bar_gates[SYMBOL].counters.to_dict()
        assert "market_data" in instance.get_stats()

    def test_an_unmeasurable_interval_refuses_every_event_rather_than_guessing(self):
        """Fail closed: no bar length means no way to tell closed from forming."""
        instance = loop(timeframe="7s")
        assert instance.closed_bar_gates[SYMBOL] is None
        assert instance._ingest_event(candle(at(30), 100.0)) is False
        assert len(window_of(instance)) == 0
        assert instance.market_data_state()[SYMBOL]["gate"] == "unavailable"


# ---------------------------------------------------------------------------
# 7. The clock the streaming gate needs (contract extension, task 7.10)
# ---------------------------------------------------------------------------


class TestTheStreamingClock:
    def test_advancing_the_clock_closes_a_bar_that_was_forming(self):
        """A long-lived gate must not judge a stream against its construction instant."""
        gate = ClosedBarIngest(TIMEFRAME, drop_late=True, now=NOW)
        forming = {"open_time": NOW - timedelta(minutes=2), "open": 1.0, "high": 2.0,
                   "low": 0.5, "close": 1.5, "volume": 10.0}
        assert gate.offer(dict(forming)) is False
        assert gate.counters.forming_dropped == 1

        gate.advance_to(NOW + timedelta(minutes=4))
        assert gate.offer(dict(forming)) is True

    def test_the_clock_never_runs_backwards(self):
        """A bar already judged closed must not become forming again.

        If it could, one timestamp would produce two indicator values - the exact outcome
        Requirement 19.2 forbids - so a backwards correction is ignored rather than honoured.
        """
        gate = ClosedBarIngest(TIMEFRAME, drop_late=True, now=NOW)
        assert gate.advance_to(NOW - timedelta(hours=5)) == pd.Timestamp(NOW)
        assert gate.now == pd.Timestamp(NOW)

    def test_an_unreadable_clock_is_refused_not_defaulted(self):
        gate = ClosedBarIngest(TIMEFRAME, drop_late=True, now=NOW)
        with pytest.raises(MarketDataContractError) as caught:
            gate.advance_to("half past tuesday")
        assert caught.value.code == "CLOCK_UNREADABLE"

    def test_a_tz_aware_clock_names_the_same_instant_as_a_naive_utc_one(self):
        gate = ClosedBarIngest(TIMEFRAME, drop_late=True, now=NOW)
        aware = pd.Timestamp(NOW, tz="UTC").tz_convert("Asia/Kolkata")
        assert gate.advance_to(aware) == pd.Timestamp(NOW)

    def test_draining_a_historical_gate_is_refused(self):
        """A re-sorting gate's stored bars are its only record of what it has admitted."""
        gate = ClosedBarIngest(TIMEFRAME, drop_late=False, now=NOW)
        with pytest.raises(MarketDataContractError) as caught:
            gate.drain()
        assert caught.value.code == "DRAIN_REQUIRES_STREAMING"

    def test_the_batch_path_is_unchanged_by_the_streaming_additions(self):
        """`closed_bar_frame` still re-sorts a historical batch and keeps every bar."""
        from backend_app.backend.market_data_contract import closed_bar_frame

        rows = [
            [int(pd.Timestamp(at(minutes)).value // 1_000_000), 1.0, 2.0, 0.5, 1.5, 10.0]
            for minutes in (20, 30, 25)
        ]
        frame, counters = closed_bar_frame(rows, TIMEFRAME, now=NOW)
        assert len(frame.index) == 3
        assert frame.index.is_monotonic_increasing
        assert counters.accepted == 3
        assert counters.late_events == 0
        # The newest bar arrived first, so the other two are both below the high-water mark:
        # counted as out-of-order, kept, and sorted into place. Nothing is dropped.
        assert counters.out_of_order == 2
