"""Tests for the ingest side of the Builder's market data contract (task 7.5).

Requirements 19.1, 19.2, 19.3, 19.4, 19.5 and 13.7.

What these tests are shaped to catch
------------------------------------
Three defects, each of which has a real cost:

1. **A forming bar reaching an indicator.** One bar timestamp then produces one value now
   and a different one when the bar finishes - two contradictory signals for one instant.
   The tests below assert the *invariant* (one timestamp, one row, one set of values) and
   not merely that a row count changed.
2. **Last-wins on a duplicate.** A duplicated timestamp resolved in favour of the later
   arrival silently revises a bar the pipeline may already have computed on.
3. **A fabricated candle reaching an order router.** ``SYNTHETIC_FILL`` and the two
   interpolating strategies write prices no exchange quoted.

Nothing here re-implements a validator or an indicator. Where the platform's existing
``market_data_validation`` is involved, the real class is called - there is no fake report,
no stubbed score and no mocked validator, because a test that mocks the validator proves
only that the mock was called.

There is no ``hypothesis`` import in this file, deliberately. ``design.md`` numbers the
properties this spec generates tests for, task 7.7 owns the next one (Property 26, feed
state honesty) and adding an unnumbered property here would put a test in the suite that no
design document accounts for. What stands in for it is table-driven: the ordering invariant
is checked over every permutation of a small batch, so the "any input order" claim is
exhausted rather than sampled.
"""
import itertools
from datetime import datetime, timedelta

import pandas as pd
import pytest

from backend_app.backend.market_data_contract import (
    FABRICATING_STRATEGIES,
    MODE_BACKTEST,
    MODE_LIVE,
    MODE_PAPER,
    MODES,
    OHLCV_COLUMNS,
    ClosedBarIngest,
    IngestCounters,
    MarketDataContractError,
    closed_bar_frame,
    interval_for,
    quality_report,
    resolve_gap_policy,
    validated_window,
)
from backend_app.backend.market_data_validation import (
    TIMEFRAME_MINUTES,
    DataQualityLevel,
    GapHandlingStrategy,
)

# The clock every test states rather than races. Bars before `NOW - interval` are closed.
NOW = datetime(2024, 3, 1, 12, 0, 0)


def ms(moment: datetime) -> int:
    return int(pd.Timestamp(moment).value // 1_000_000)


def bar(moment: datetime, close: float, *, volume: float = 1_000.0):
    """One CCXT-shaped row whose five values are all different, so a mixed-up column shows.

    ``high``/``low`` bracket ``open``/``close`` so the row also satisfies the platform
    validator's OHLC relationships - these rows are fed to the real validator later in the
    file and a row that failed integrity would confuse a closed-bar assertion with a
    quality one.
    """
    return [ms(moment), close - 0.25, close + 1.5, close - 0.75, close, volume]


def series(count: int, *, step_minutes: int = 5, end: datetime = None, base: float = 100.0):
    """``count`` consecutive closed bars ending one interval before :data:`NOW`."""
    last = (end or NOW) - timedelta(minutes=step_minutes)
    return [
        bar(last - timedelta(minutes=step_minutes * offset), base + offset)
        for offset in reversed(range(count))
    ]


# ---------------------------------------------------------------------------
# 1. The bar-length vocabulary is the pipeline's own (no second table)
# ---------------------------------------------------------------------------


class TestIntervalVocabulary:
    def test_every_interval_the_pipeline_measures_has_a_bar_length(self):
        """``TIMEFRAME_MINUTES`` is reused, not shadowed by a copy in this module."""
        for label, minutes in TIMEFRAME_MINUTES.items():
            assert interval_for(label) == timedelta(minutes=minutes)

    def test_an_unmeasurable_interval_is_refused_not_defaulted(self):
        """Defaulting here would decide "closed?" with the wrong bar length.

        ``_validate_row_count`` defaults an unknown interval to 60 minutes, which disarms a
        coverage check. Doing the same here would either admit a forming bar or discard a
        finished one, so this refuses.
        """
        with pytest.raises(MarketDataContractError) as caught:
            interval_for("7s")
        assert caught.value.code == "TIMEFRAME_UNSUPPORTED"
        assert "7s" in caught.value.message
        assert caught.value.details["supported"] == sorted(TIMEFRAME_MINUTES)

    def test_the_module_holds_no_second_interval_table(self):
        """A copied table drifts. The source must read the shared one."""
        import inspect

        import backend_app.backend.market_data_contract as MDC

        source = inspect.getsource(MDC)
        assert "TIMEFRAME_MINUTES.get(" in source
        # The tell-tale of a local copy: the vocabulary written out again.
        assert '"15m": 15' not in source
        assert "'15m': 15" not in source


# ---------------------------------------------------------------------------
# 2. Requirement 19.2 - closed bars only
# ---------------------------------------------------------------------------


class TestClosedBarsOnly:
    def test_a_forming_bar_is_dropped_and_counted(self):
        """The bar whose interval has not elapsed never reaches the frame."""
        closed = series(4)
        forming = bar(NOW, 999.0)  # opens now: its 5 minutes have not elapsed
        frame, counters = closed_bar_frame(closed + [forming], "5m", now=NOW)

        assert counters.offered == 5
        assert counters.accepted == 4
        assert counters.forming_dropped == 1
        assert len(frame.index) == 4
        assert 999.0 not in set(frame["close"])

    def test_the_bar_that_just_finished_is_admitted(self):
        """The gate is "interval elapsed", not "one whole bar of slack".

        A gate that also dropped the just-closed bar would delay every signal by a full
        interval, which is a different defect wearing the same fix.
        """
        just_closed = NOW - timedelta(minutes=5)
        frame, counters = closed_bar_frame([bar(just_closed, 42.0)], "5m", now=NOW)
        assert counters.accepted == 1
        assert counters.forming_dropped == 0
        assert frame.index[-1] == pd.Timestamp(just_closed)

    def test_one_bar_short_of_closed_is_refused(self):
        """One second before the boundary is still forming."""
        nearly = NOW - timedelta(minutes=5) + timedelta(seconds=1)
        frame, counters = closed_bar_frame([bar(nearly, 42.0)], "5m", now=NOW)
        assert counters.forming_dropped == 1
        assert frame.empty

    @pytest.mark.parametrize("timeframe", ["1m", "5m", "15m", "1h", "4h", "1d"])
    def test_the_boundary_follows_the_declared_interval(self, timeframe):
        """A bar closed at ``5m`` is still forming at ``1h``. The interval decides."""
        length = interval_for(timeframe)
        assert closed_bar_frame([bar(NOW - length, 1.0)], timeframe, now=NOW)[
            1
        ].accepted == 1
        assert closed_bar_frame(
            [bar(NOW - length + timedelta(seconds=1), 1.0)], timeframe, now=NOW
        )[1].forming_dropped == 1

    def test_a_feed_flag_saying_forming_outranks_the_clock(self):
        """``design.md``'s canonical ``Candle.is_closed`` is authoritative when present.

        The feed knows whether it sent a snapshot of a live bar; inferring from the clock is
        the fallback, not the override.
        """
        moment = NOW - timedelta(minutes=30)
        candle = {
            "timestamp": ms(moment),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10.0,
            "is_closed": False,
        }
        frame, counters = closed_bar_frame([candle], "5m", now=NOW)
        assert counters.forming_dropped == 1
        assert frame.empty

    def test_the_core_invariant_one_timestamp_never_holds_two_values(self):
        """The property the whole gate exists for (Requirement 19.2).

        The same bar is offered three times as it forms - a rising close on each tick - and
        then once as its final closed value. What reaches the frame is exactly one row for
        that timestamp, holding the closed value, whichever order the ticks arrived in.
        """
        moment = NOW - timedelta(minutes=10)
        forming_ticks = [
            {
                "timestamp": ms(moment),
                "open": 100.0,
                "high": 100.0 + tick,
                "low": 99.0,
                "close": 100.0 + tick,
                "volume": 10.0 * tick,
                "is_closed": False,
            }
            for tick in (1, 2, 3)
        ]
        final = {
            "timestamp": ms(moment),
            "open": 100.0,
            "high": 104.0,
            "low": 99.0,
            "close": 103.5,
            "volume": 40.0,
            "is_closed": True,
        }
        frame, counters = closed_bar_frame(forming_ticks + [final], "5m", now=NOW)

        assert len(frame.index) == 1
        assert frame.index.is_unique
        assert float(frame["close"].iloc[0]) == 103.5
        assert counters.forming_dropped == 3
        assert counters.accepted == 1


# ---------------------------------------------------------------------------
# 3. Requirement 19.4 - first wins on a duplicate
# ---------------------------------------------------------------------------


class TestFirstWinsOnDuplicates:
    def test_the_first_candle_is_retained_and_the_duplicate_counted(self):
        """Requirement 19.4, verbatim: retain the first, increment the count."""
        moment = NOW - timedelta(minutes=20)
        frame, counters = closed_bar_frame(
            [bar(moment, 100.0), bar(moment, 777.0)], "5m", now=NOW
        )
        assert counters.duplicates == 1
        assert counters.accepted == 1
        assert float(frame["close"].iloc[0]) == 100.0, "last-wins has come back"

    def test_a_revision_of_an_older_bar_does_not_overwrite_it(self):
        """A duplicate that is not the newest bar is still resolved first-wins."""
        rows = series(5)
        revision = list(rows[1])
        revision[4] = 5_555.0
        frame, counters = closed_bar_frame(rows + [revision], "5m", now=NOW)
        assert counters.duplicates == 1
        assert 5_555.0 not in set(frame["close"])

    def test_a_duplicate_is_counted_as_a_duplicate_not_as_a_late_event(self):
        """Two feed faults, two counters. Conflating them makes both unreadable."""
        rows = series(3)
        ingest = ClosedBarIngest("5m", drop_late=True, now=NOW).extend(rows + [rows[0]])
        assert ingest.counters.duplicates == 1
        assert ingest.counters.late_events == 0
        assert ingest.counters.out_of_order == 0

    def test_the_platform_validator_also_keeps_the_first_duplicate(self):
        """The shared validator agrees, so a caller that bypasses this gate is not surprised.

        ``design.md`` names ``MarketDataValidator`` as the owner of "keep first, count
        duplicate". Task 7.5 changed one word in ``StructuralValidator`` to make that true;
        this is the assertion that keeps it true.
        """
        from backend_app.backend.market_data_validation import StructuralValidator

        moment = pd.Timestamp("2024-01-01 00:00:00")
        frame = pd.DataFrame(
            {
                "open": [1.0, 1.0],
                "high": [2.0, 2.0],
                "low": [0.5, 0.5],
                "close": [10.0, 99.0],
                "volume": [5.0, 5.0],
            },
            index=pd.DatetimeIndex([moment, moment], name="timestamp"),
        )
        cleaned, issues = StructuralValidator.validate(frame, "TEST/USDT")
        assert len(cleaned.index) == 1
        assert float(cleaned["close"].iloc[0]) == 10.0
        assert any(issue.issue_type == "duplicate_timestamps" for issue in issues)

    def test_the_training_frame_builder_also_keeps_the_first_duplicate(self):
        """``strategy_service.training_frame`` feeds indicator computation too."""
        from backend_app.backend.strategy_service import training_frame

        moment = NOW - timedelta(minutes=60)
        frame = training_frame([bar(moment, 100.0), bar(moment, 888.0)], 10)
        assert len(frame.index) == 1
        assert float(frame["close"].iloc[0]) == 100.0


# ---------------------------------------------------------------------------
# 4. Requirement 19.3 - late and out-of-order arrivals
# ---------------------------------------------------------------------------


class TestOrderingAndLateEvents:
    def test_a_live_event_older_than_the_last_closed_bar_is_dropped_and_counted(self):
        """Requirement 19.3, verbatim, for a streaming mode."""
        rows = series(4)
        stale = bar(NOW - timedelta(minutes=60), 1.0)
        ingest = ClosedBarIngest("5m", drop_late=True, now=NOW).extend(rows + [stale])

        assert ingest.counters.late_events == 1
        assert ingest.counters.out_of_order == 1
        assert ingest.counters.accepted == 4
        assert 1.0 not in set(ingest.frame()["close"])

    def test_a_historical_out_of_order_row_is_counted_and_re_sorted(self):
        """``design.md``: "re-sort historical". Counted either way, dropped only for live."""
        rows = series(4)
        shuffled = [rows[3], rows[0], rows[2], rows[1]]
        frame, counters = closed_bar_frame(shuffled, "5m", now=NOW)

        assert counters.out_of_order == 3
        assert counters.late_events == 0
        assert counters.accepted == 4
        assert frame.index.is_monotonic_increasing

    def test_the_served_series_is_monotonic_for_every_arrival_order(self):
        """Exhaustive over a small batch: 120 orderings, one answer.

        The claim "arrival order cannot change the served window" is checked against every
        permutation rather than a sampled few, and the *values* are compared, not just the
        index - a frame that sorted its index while leaving its rows in place would pass a
        monotonicity check and still be wrong.
        """
        rows = series(5)
        expected, _ = closed_bar_frame(rows, "5m", now=NOW)
        for ordering in itertools.permutations(rows):
            frame, counters = closed_bar_frame(list(ordering), "5m", now=NOW)
            assert frame.index.is_monotonic_increasing
            assert frame.index.is_unique
            assert counters.accepted == 5
            pd.testing.assert_frame_equal(frame, expected)

    def test_dropping_late_events_still_leaves_a_monotonic_series(self):
        rows = series(5)
        shuffled = [rows[4], rows[1], rows[3], rows[0], rows[2]]
        ingest = ClosedBarIngest("5m", drop_late=True, now=NOW).extend(shuffled)
        frame = ingest.frame()
        assert frame.index.is_monotonic_increasing
        # Only the arrivals that never went backwards survive.
        assert len(frame.index) == ingest.counters.accepted
        assert ingest.counters.late_events == ingest.counters.out_of_order


# ---------------------------------------------------------------------------
# 5. Unreadable rows
# ---------------------------------------------------------------------------


class TestMalformedRows:
    @pytest.mark.parametrize(
        "row",
        [
            [ms(NOW - timedelta(minutes=10)), 1.0, 2.0, 0.5],  # short row
            [None, 1.0, 2.0, 0.5, 1.5, 10.0],  # no timestamp
            [ms(NOW - timedelta(minutes=10)), "x", 2.0, 0.5, 1.5, 10.0],  # unparseable
            [ms(NOW - timedelta(minutes=10)), 1.0, 2.0, 0.5, float("nan"), 10.0],
            [ms(NOW - timedelta(minutes=10)), 1.0, 2.0, 0.5, float("inf"), 10.0],
            None,
        ],
    )
    def test_an_unreadable_row_is_dropped_and_counted(self, row):
        """A NaN price is dropped at the seam, not handed on to become a NaN indicator."""
        frame, counters = closed_bar_frame([row], "5m", now=NOW)
        assert counters.malformed == 1
        assert counters.accepted == 0
        assert frame.empty

    def test_an_unreadable_row_does_not_take_the_batch_down_with_it(self):
        rows = series(3)
        frame, counters = closed_bar_frame(rows + [[None, 1, 2, 3, 4, 5]], "5m", now=NOW)
        assert counters.accepted == 3
        assert counters.malformed == 1
        assert len(frame.index) == 3

    def test_an_empty_batch_yields_an_empty_frame_with_the_right_columns(self):
        frame, counters = closed_bar_frame([], "5m", now=NOW)
        assert frame.empty
        assert list(frame.columns) == list(OHLCV_COLUMNS)
        assert counters.to_dict() == IngestCounters().to_dict()


# ---------------------------------------------------------------------------
# 6. Requirement 13.7 - the gap strategy, pinned per environment
# ---------------------------------------------------------------------------


class TestGapPolicy:
    def test_synthetic_fill_is_refused_for_live(self):
        """Requirement 13.7's named case, with its own code."""
        with pytest.raises(MarketDataContractError) as caught:
            resolve_gap_policy(MODE_LIVE, GapHandlingStrategy.SYNTHETIC_FILL)
        assert caught.value.code == "SYNTHETIC_FILL_FORBIDDEN_LIVE"
        assert caught.value.details["mode"] == MODE_LIVE

    @pytest.mark.parametrize("mode", MODES)
    def test_synthetic_fill_is_refused_in_every_mode(self, mode):
        """Stricter than 13.7's floor, and matching what the code already does.

        ``GapHandler._synthetic_fill`` raises ``RuntimeError`` on its first line, and
        Requirement 19.11 admits only a source delivering zero synthetic candles with no
        mode qualifier on it. A backtest built on invented prices produces invented returns,
        which is what an author decides to go live on.
        """
        with pytest.raises(MarketDataContractError) as caught:
            resolve_gap_policy(mode, GapHandlingStrategy.SYNTHETIC_FILL)
        assert caught.value.code.startswith("SYNTHETIC_FILL_FORBIDDEN")

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize(
        "strategy",
        [GapHandlingStrategy.LINEAR_INTERPOLATE, GapHandlingStrategy.SPLINE_INTERPOLATE],
    )
    def test_interpolation_is_refused_in_every_mode(self, mode, strategy):
        with pytest.raises(MarketDataContractError) as caught:
            resolve_gap_policy(mode, strategy)
        assert caught.value.code == "GAP_INTERPOLATION_FORBIDDEN"

    @pytest.mark.parametrize("mode", [MODE_PAPER, MODE_LIVE])
    def test_forward_fill_is_refused_for_a_running_deployment(self, mode):
        """Republishing the last close as a new bar is a fabricated price."""
        with pytest.raises(MarketDataContractError) as caught:
            resolve_gap_policy(mode, GapHandlingStrategy.FORWARD_FILL)
        assert caught.value.code == "GAP_FILL_FORBIDDEN_LIVE"

    def test_forward_fill_is_permitted_for_a_backtest_and_discloses_itself(self):
        """"Allowed for backtests **with disclosure**" - the disclosure is on the object.

        And it tells the truth about what happened: the platform's gap handler refuses a
        gapped window rather than filling it, so ``effective`` is ``SKIP_EXECUTION``.
        Reporting ``FORWARD_FILL`` as effective would claim a repair that does not occur.
        """
        policy = resolve_gap_policy(MODE_BACKTEST, GapHandlingStrategy.FORWARD_FILL)
        assert policy.permitted is True
        assert policy.requested is GapHandlingStrategy.FORWARD_FILL
        assert policy.effective is GapHandlingStrategy.SKIP_EXECUTION
        assert policy.fills_gaps is False
        assert policy.disclosure and "NOT forward filled" in policy.disclosure
        assert policy.to_dict()["disclosure"] == policy.disclosure

    def test_the_gap_handler_really_does_refuse_rather_than_forward_fill(self):
        """The claim in that disclosure, checked against the real handler.

        A disclosure nobody verifies is prose. This runs
        ``market_data_validation.GapHandler`` over a gapped window with ``FORWARD_FILL``
        asked for, and requires a refusal.
        """
        from backend_app.backend.market_data_validation import (
            DataValidationError,
            GapHandler,
        )

        index = pd.DatetimeIndex(
            [
                pd.Timestamp("2024-01-01 00:00"),
                pd.Timestamp("2024-01-01 00:05"),
                pd.Timestamp("2024-01-01 01:00"),  # the gap
            ],
            name="timestamp",
        )
        gapped = pd.DataFrame(
            {
                "open": [1.0, 1.0, 1.0],
                "high": [2.0, 2.0, 2.0],
                "low": [0.5, 0.5, 0.5],
                "close": [1.5, 1.5, 1.5],
                "volume": [10.0, 10.0, 10.0],
            },
            index=index,
        )
        with pytest.raises(DataValidationError):
            GapHandler.handle(gapped, "5m", GapHandlingStrategy.FORWARD_FILL, 5.0)

    @pytest.mark.parametrize("mode", MODES)
    def test_skip_execution_is_permitted_everywhere_and_discloses_nothing(self, mode):
        """Nothing to disclose because nothing was filled."""
        policy = resolve_gap_policy(mode, GapHandlingStrategy.SKIP_EXECUTION)
        assert policy.effective is GapHandlingStrategy.SKIP_EXECUTION
        assert policy.fills_gaps is False
        assert policy.disclosure is None

    def test_the_default_strategy_is_the_refusing_one(self):
        policy = resolve_gap_policy(MODE_LIVE)
        assert policy.requested is GapHandlingStrategy.SKIP_EXECUTION

    @pytest.mark.parametrize("mode", ["", None, "LIVE ", "prod", "live-ish", "backtesting"])
    def test_an_unrecognised_mode_is_refused_not_defaulted(self, mode):
        """Fail closed on the mode: a typo must not buy a fill permission."""
        if mode == "LIVE ":
            # Case and surrounding space are normalised - that is a spelling of `live`,
            # not a different environment.
            assert resolve_gap_policy(mode).mode == MODE_LIVE
            return
        with pytest.raises(MarketDataContractError) as caught:
            resolve_gap_policy(mode)
        assert caught.value.code == "MODE_UNRECOGNISED"

    def test_the_default_validator_configuration_is_permitted_for_a_live_deployment(self):
        """The shipped default must not be one this gate refuses.

        A pinning that refuses the platform's own default configuration would take every
        live deployment down, which is a different failure from the one being prevented.
        """
        from backend_app.backend.market_data_validation import ValidationConfig

        policy = resolve_gap_policy(MODE_LIVE, ValidationConfig().gap_strategy)
        assert policy.fills_gaps is False

    def test_every_fabricating_strategy_is_named_and_refused(self):
        """The set is not decoration: each member is genuinely refused for live."""
        assert GapHandlingStrategy.SYNTHETIC_FILL in FABRICATING_STRATEGIES
        for strategy in FABRICATING_STRATEGIES:
            with pytest.raises(MarketDataContractError):
                resolve_gap_policy(MODE_LIVE, strategy)


# ---------------------------------------------------------------------------
# 7. Requirements 19.1 / 19.5 - the existing validator, surfaced not re-implemented
# ---------------------------------------------------------------------------


class TestValidatorSurfaced:
    @pytest.mark.asyncio
    async def test_a_clean_window_produces_the_platform_report(self):
        """The real ``MarketDataValidator``, not a stand-in."""
        frame, _ = closed_bar_frame(series(120), "5m", now=NOW)
        report = await quality_report(frame, "ETH/USDT", "5m")

        assert report.symbol == "ETH/USDT"
        assert report.timeframe == "5m"
        assert report.total_candles == 120
        assert report.quality_level is DataQualityLevel.EXCELLENT
        # Duplicates were resolved at the seam, so the validator has none left to count.
        assert report.duplicate_timestamps == 0

    @pytest.mark.asyncio
    async def test_a_rejected_window_becomes_one_classified_refusal(self):
        """The strict posture is preserved and classified, not loosened.

        A single close far outside the window's distribution makes
        ``OutlierDetector._z_score_filter`` refuse. That threshold is the platform's and is
        not relaxed here; what this module adds is a stable code and the validator's own
        message.
        """
        rows = series(60)
        spike = list(rows[30])
        spike[4] = 100_000.0
        spike[2] = 100_001.5
        rows[30] = spike
        frame, _ = closed_bar_frame(rows, "5m", now=NOW)

        with pytest.raises(MarketDataContractError) as caught:
            await quality_report(frame, "ETH/USDT", "5m")
        assert caught.value.code == "DATA_QUALITY"
        assert caught.value.details["validator_error"]

    @pytest.mark.asyncio
    async def test_the_report_is_republished_verbatim(self):
        """``IngestResult.to_dict()['quality']`` is the report's own dict, not a rewrite."""
        result = await validated_window(
            series(80), "ETH/USDT", "5m", mode=MODE_BACKTEST, now=NOW
        )
        assert result.to_dict()["quality"] == result.report.to_dict()

    def test_the_module_defines_no_quality_score_of_its_own(self):
        """No parallel grading. The score and the level belong to the validator."""
        import inspect

        import backend_app.backend.market_data_contract as MDC

        source = inspect.getsource(MDC)
        for token in ("quality_score =", "_calculate_quality", "DataQualityLevel."):
            assert token not in source, f"the ingest layer grades data quality ({token})"

    def test_the_module_computes_no_indicator(self):
        """Task 5.4's lesson: a module that touches bars must not also compute on them."""
        import inspect

        import backend_app.backend.market_data_contract as MDC

        source = inspect.getsource(MDC)
        for token in ("rolling(", "ewm(", ".pct_change(", ".diff(", "cumsum("):
            assert token not in source, f"the ingest layer computes something ({token})"


# ---------------------------------------------------------------------------
# 8. The entry point, end to end
# ---------------------------------------------------------------------------


class TestValidatedWindow:
    @pytest.mark.asyncio
    async def test_it_resolves_the_gap_policy_before_reading_a_candle(self):
        """A fabricating configuration is refused while the refusal is still free."""
        with pytest.raises(MarketDataContractError) as caught:
            await validated_window(
                series(50),
                "ETH/USDT",
                "5m",
                mode=MODE_LIVE,
                gap_strategy=GapHandlingStrategy.SYNTHETIC_FILL,
                now=NOW,
            )
        assert caught.value.code == "SYNTHETIC_FILL_FORBIDDEN_LIVE"

    @pytest.mark.asyncio
    async def test_the_window_it_returns_holds_only_closed_bars(self):
        rows = series(60) + [bar(NOW, 999.0)]
        result = await validated_window(
            rows, "ETH/USDT", "5m", mode=MODE_BACKTEST, now=NOW
        )
        assert result.counters.forming_dropped == 1
        assert 999.0 not in set(result.frame["close"])
        assert result.frame.index.is_monotonic_increasing
        assert result.frame.index.is_unique

    @pytest.mark.asyncio
    async def test_the_tail_limit_counts_closed_bars(self):
        """``bars=N`` means N closed bars, not N rows of which one is forming."""
        rows = series(60) + [bar(NOW, 999.0)]
        result = await validated_window(
            rows, "ETH/USDT", "5m", mode=MODE_BACKTEST, bars=20, now=NOW
        )
        assert len(result.frame.index) == 20
        assert result.report.total_candles == 20

    @pytest.mark.asyncio
    async def test_a_feed_that_sent_only_a_forming_bar_says_so(self):
        """"Nothing arrived" and "only a forming bar arrived" are different facts."""
        with pytest.raises(MarketDataContractError) as caught:
            await validated_window(
                [bar(NOW, 1.0)], "ETH/USDT", "5m", mode=MODE_BACKTEST, now=NOW
            )
        assert caught.value.code == "NO_CLOSED_BARS"
        assert caught.value.details["counters"]["forming_dropped"] == 1

    @pytest.mark.asyncio
    async def test_a_streaming_mode_drops_late_arrivals_and_a_backtest_re_sorts_them(self):
        """One switch, two documented behaviours - and it follows the mode, not a flag.

        The oldest bar of a contiguous grid arrives last. That is out-of-order and it is not
        a duplicate, so it is the case the two modes genuinely disagree about. Both
        resulting windows stay contiguous, which keeps the platform validator's gap gate out
        of the assertion - this test is about ordering, not about quality.
        """
        rows = series(40)
        out_of_order = rows[1:] + [rows[0]]

        live = await validated_window(
            out_of_order, "ETH/USDT", "5m", mode=MODE_PAPER, now=NOW
        )
        assert live.counters.late_events == 1
        assert live.counters.accepted == 39
        assert float(rows[0][4]) not in set(live.frame["close"])

        back = await validated_window(
            out_of_order, "ETH/USDT", "5m", mode=MODE_BACKTEST, now=NOW
        )
        assert back.counters.late_events == 0
        assert back.counters.out_of_order == 1
        assert back.counters.accepted == 40
        assert back.frame.index.is_monotonic_increasing
        assert float(rows[0][4]) in set(back.frame["close"])

    @pytest.mark.asyncio
    async def test_the_wire_form_keeps_the_two_counter_families_apart(self):
        """Never summed: they count different things at different layers."""
        result = await validated_window(
            series(80), "ETH/USDT", "5m", mode=MODE_BACKTEST, now=NOW
        )
        payload = result.to_dict()
        assert set(payload) == {
            "mode",
            "closed_bars_only",
            "counters",
            "gap_policy",
            "quality",
        }
        assert payload["closed_bars_only"] is True
        assert payload["mode"] == MODE_BACKTEST
        assert set(payload["counters"]) == {
            "offered",
            "accepted",
            "forming_dropped",
            "late_events",
            "out_of_order",
            "duplicates",
            "malformed",
        }
        # The validator's own duplicate tally stays under `quality`, where it came from.
        assert "duplicate_timestamps" in payload["quality"]
        assert "duplicate_timestamps" not in payload["counters"]
