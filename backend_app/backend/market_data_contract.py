"""backend/market_data_contract.py - the INGEST side of the Builder's market data contract.

Strategy-builder task 7.5, extended by task 7.10. Requirements 19.1, 19.2, 19.3, 19.4,
19.5 and 13.7; ``design.md`` -> "Market data: canonical objects, validation, and the latency
decision".

Task 7.10 added exactly two things, both for a gate that outlives one batch:
:meth:`ClosedBarIngest.advance_to` (a monotonic clock, so a live deployment's gate can be
long-lived instead of one-shot) and :meth:`ClosedBarIngest.drain` (release the admitted bars
and keep the high-water mark, so the gate stays bounded on a stream). The rules themselves -
what a closed bar is, first-wins on duplicates, drop-late in streaming modes - are unchanged
and are still defined only here. ``dag_event_loop`` routes the live path through this module
rather than carrying a second gate of its own.

WHAT THIS MODULE IS
-------------------
The seam between a raw candle feed and indicator computation. Three jobs, and nothing
else:

1. **Closed bars only** (Requirement 19.2). A forming bar never reaches an executor, so
   one bar timestamp can never produce two different indicator values.
2. **Ordering, duplicates and late events** (19.3, 19.4). First candle wins on a
   duplicate; a live event older than what has already been computed on is dropped and
   counted.
3. **The gap strategy, pinned per environment** (13.7, and ``design.md``'s "never
   ``SYNTHETIC_FILL`` on a live deployment"). Resolved *before* any candle is read, and
   the permission carries a disclosure that travels to the caller.

WHAT THIS MODULE IS NOT
-----------------------
It is **not a validator**. ``market_data_validation.py`` is REUSED AS-IS: required
columns, the OHLC relationships, positive prices, non-negative volume, the outlier filter,
the gap gate and ``DataQualityReport`` are its rules and its counters. Nothing here
recomputes, re-grades or re-counts any of them; :func:`quality_report` calls it and
:meth:`IngestResult.to_dict` republishes ``DataQualityReport.to_dict()`` verbatim.

It is also **not a second indicator path**. No rolling window, no smoothing, no
arithmetic on a price. Task 5.4 had to re-record golden files because two ``_calculate_*``
implementations genuinely disagreed (a simple rolling mean against Wilder smoothing); the
lesson is that a module which touches bars must hand them on unchanged.

It computes no feed state and serves no endpoint - task 7.4 owns the ``LIVE`` /
``DELAYED`` / ``STALE`` / ``DISCONNECTED`` / ``INSUFFICIENT_DATA`` vocabulary and the
``/data-quality`` route. This module is the ingest half of the same requirement.

WHICH COUNTER COMES FROM WHERE - said plainly, because two of them did not exist
-------------------------------------------------------------------------------
``market_data_validation.py`` already counts, in ``DataQualityReport``:
``duplicate_timestamps``, ``ohlc_violations``, ``outliers_detected``, ``gaps_found``,
``missing_values``, ``invalid_candles`` and ``max_gap_duration_minutes``. Those are
surfaced, never re-derived.

It counts **no late events and no out-of-order arrivals**, and it has no per-candle
integrity *drop* counter - by design, because its posture is to reject a whole batch
(``DataValidationError``) rather than to drop a candle and carry on. So:

* ``IngestCounters.late_events`` and ``.out_of_order`` are counted **here**, because
  Requirements 19.3 and 19.5 place them on the *pipeline* and there is no existing tally
  for them to be parallel to. Arrival order is only observable at the arrival seam; a
  batch validator that receives a sorted frame cannot see it at all.
* ``IngestCounters.duplicates`` is also counted here, and the validator's own
  ``duplicate_timestamps`` will read **0** on any window that came through this module -
  first-wins is applied at ingest, so the validator is handed a frame with nothing left to
  de-duplicate. The two figures are published side by side and are never summed.
* Integrity drops are the validator's business and are reported as it reports them: a
  refusal (``DataValidationError``, surfaced as a ``DATA_QUALITY`` contract error carrying
  the validator's own message) or the graded counts in the report. This module drops no
  candle for integrity reasons and therefore publishes no integrity-drop tally of its own.

FIRST-WINS ON DUPLICATES - where it is enforced
-----------------------------------------------
:meth:`ClosedBarIngest.offer`. A bar whose open time is already held is counted and
discarded; the stored candle is never overwritten. That is the only place in this path
where a duplicate is resolved, and it resolves in favour of the first arrival
(Requirement 19.4, ``design.md``'s "Duplicate candle | first wins; duplicate counted").
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from backend_app.backend.market_data_validation import (
    TIMEFRAME_MINUTES,
    DataQualityReport,
    DataValidationError,
    GapHandlingStrategy,
    get_validator,
)

logger = logging.getLogger("MarketDataContract")


def _metrics() -> Any:
    """``backend/metrics.py``'s collector, or ``None``. Lazy and guarded (task 9.1).

    Requirement 24.3's ``market_data.latency_ms`` and ``market_data.quality_score``. Guarded
    because this module is the arrival gate for both live event loops: instrumentation must
    not be what drops a bar, and must not change whether one is admitted.
    """
    try:
        from backend_app.backend.metrics import metrics_collector

        return metrics_collector
    except Exception:  # noqa: BLE001 - instrumentation never breaks its caller
        return None


# ══════════════════════════════════════════════════════════════════════════
# MODES
# ══════════════════════════════════════════════════════════════════════════

#: A bounded historical read: node preview, backtest, training window. No order router is
#: downstream of it.
MODE_BACKTEST = "backtest"

#: A running deployment with simulated fills. ``chk_sd_mode`` (design.md) admits this as a
#: deployment mode, and its fills are priced from the feed, so a fabricated bar becomes a
#: fabricated fill and then a figure the author uses to decide whether to go live.
MODE_PAPER = "paper"

#: A running deployment with real fills. Requirement 13.7's named case.
MODE_LIVE = "live"

MODES: Tuple[str, ...] = (MODE_BACKTEST, MODE_PAPER, MODE_LIVE)

#: Modes whose bars reach an execution path, so arrival order is a live-stream fact and a
#: late arrival is dropped rather than re-sorted (``design.md``: "re-sort historical; for
#: live, drop late events older than the last closed bar and count them").
_STREAMING_MODES: Tuple[str, ...] = (MODE_PAPER, MODE_LIVE)


class MarketDataContractError(Exception):
    """A classified refusal from the ingest contract.

    Carries a stable ``code`` so a caller can map it to its own error contract without
    matching on prose. Every raise site in this module names a code that appears in the
    module docstring's requirement list.
    """

    def __init__(self, code: str, message: str, details: Optional[Mapping[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Dict[str, Any] = dict(details or {})

    def to_dict(self) -> Dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.details}


# ══════════════════════════════════════════════════════════════════════════
# GAP STRATEGY, PINNED PER ENVIRONMENT (Requirement 13.7)
# ══════════════════════════════════════════════════════════════════════════

#: Strategies that write a price no exchange ever printed.
#:
#: ``SYNTHETIC_FILL`` invents a candle from recent volatility and a random draw.
#: ``LINEAR_INTERPOLATE`` / ``SPLINE_INTERPOLATE`` invent one between two real ones. The
#: distinction does not matter to an order router: all three produce a number that no
#: venue quoted, and Requirement 19.11 admits only a source delivering "zero delivered
#: synthetic candles" with no mode qualifier on it. So all three are refused in **every**
#: mode, which is stricter than Requirement 13.7's ``live``-only floor and matches what
#: ``market_data_validation`` already does in code: ``GapHandler._synthetic_fill`` and
#: ``GapHandler._interpolate`` both raise ``RuntimeError`` on the first line.
FABRICATING_STRATEGIES = frozenset(
    {
        GapHandlingStrategy.SYNTHETIC_FILL,
        GapHandlingStrategy.LINEAR_INTERPOLATE,
        GapHandlingStrategy.SPLINE_INTERPOLATE,
    }
)


@dataclass(frozen=True)
class GapPolicy:
    """The gap strategy this environment is allowed, and what it actually does.

    ``requested`` is what the configuration asked for; ``effective`` is what the pipeline
    will do. They differ in exactly one case, and :func:`resolve_gap_policy` explains it in
    ``disclosure`` rather than letting the caller assume the request was honoured.
    """

    mode: str
    requested: GapHandlingStrategy
    effective: GapHandlingStrategy
    #: Whether the configuration's request was permitted at all. A refused request never
    #: produces a ``GapPolicy``; this is ``True`` on every instance that exists, and is
    #: published so a consumer reading the dict form does not have to infer it.
    permitted: bool
    #: Whether any bar in the served window may be a fill rather than an exchange print.
    fills_gaps: bool
    #: The sentence a caller must be able to show the author. ``None`` only when there is
    #: genuinely nothing to disclose: no fill is permitted and none was performed.
    disclosure: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "requested": self.requested.value,
            "effective": self.effective.value,
            "permitted": self.permitted,
            "fills_gaps": self.fills_gaps,
            "disclosure": self.disclosure,
        }


def resolve_gap_policy(
    mode: str,
    requested: Optional[GapHandlingStrategy] = None,
) -> GapPolicy:
    """The gap strategy ``mode`` may use, or a refusal.

    Requirement 13.7 and ``design.md``: "Gap strategy is pinned per environment -
    ``FORWARD_FILL`` allowed for backtests with disclosure, never ``SYNTHETIC_FILL`` on a
    live deployment, because a synthetic candle is an invented price and invented prices
    reach the order router."

    The pinning
        ==============  =========================================================
        mode            permitted
        ==============  =========================================================
        ``backtest``    ``SKIP_EXECUTION``; ``FORWARD_FILL`` **with disclosure**
        ``paper``       ``SKIP_EXECUTION``
        ``live``        ``SKIP_EXECUTION``
        ==============  =========================================================

    Fail-closed on the mode itself: an unrecognised mode is refused rather than defaulted,
    because the safe default and the permissive default are the same string away and a
    typo must not buy a fill permission.

    The one case where ``requested`` and ``effective`` differ
        A backtest may ask for ``FORWARD_FILL``, and this permits it - but
        ``market_data_validation.GapHandler.handle`` **rejects any gap outright** and never
        branches on the strategy it is passed, and ``GapHandler._forward_fill`` raises
        ``RuntimeError`` on its first line. So no forward fill happens, the effective
        behaviour is ``SKIP_EXECUTION``, and ``disclosure`` says exactly that. Reporting
        ``FORWARD_FILL`` as effective would be this function claiming a repair the pipeline
        does not perform.

    Raises
        :class:`MarketDataContractError` with code ``MODE_UNRECOGNISED``,
        ``SYNTHETIC_FILL_FORBIDDEN_LIVE``, ``SYNTHETIC_FILL_FORBIDDEN``,
        ``GAP_INTERPOLATION_FORBIDDEN`` or ``GAP_FILL_FORBIDDEN_LIVE``.
    """
    normalised = str(mode or "").strip().lower()
    if normalised not in MODES:
        raise MarketDataContractError(
            "MODE_UNRECOGNISED",
            f"'{mode}' is not a market data mode this platform pins a gap strategy for, "
            f"so no gap policy can be resolved for it.",
            {"mode": mode, "recognised_modes": list(MODES)},
        )

    strategy = requested if requested is not None else GapHandlingStrategy.SKIP_EXECUTION
    if not isinstance(strategy, GapHandlingStrategy):
        raise MarketDataContractError(
            "GAP_STRATEGY_UNRECOGNISED",
            f"'{strategy}' is not a gap handling strategy this platform recognises.",
            {"mode": normalised, "requested": str(strategy)},
        )

    if strategy is GapHandlingStrategy.SYNTHETIC_FILL:
        # Requirement 13.7's named case gets its own code, so the live refusal is
        # separately identifiable from the same refusal in a backtest.
        code = (
            "SYNTHETIC_FILL_FORBIDDEN_LIVE"
            if normalised == MODE_LIVE
            else "SYNTHETIC_FILL_FORBIDDEN"
        )
        raise MarketDataContractError(
            code,
            "Synthetic candle fill is refused: a synthetic candle is a price no exchange "
            "quoted, and this platform admits no source that delivers one.",
            {"mode": normalised, "requested": strategy.value},
        )

    if strategy in FABRICATING_STRATEGIES:
        raise MarketDataContractError(
            "GAP_INTERPOLATION_FORBIDDEN",
            "Interpolating across a market data gap is refused: an interpolated candle is "
            "a price no exchange quoted.",
            {"mode": normalised, "requested": strategy.value},
        )

    if strategy is GapHandlingStrategy.FORWARD_FILL:
        if normalised in _STREAMING_MODES:
            raise MarketDataContractError(
                "GAP_FILL_FORBIDDEN_LIVE",
                f"Forward filling a market data gap is refused in {normalised} mode: "
                f"republishing the last close as a new bar is a fabricated price, and in "
                f"this mode a fabricated price reaches the execution path.",
                {"mode": normalised, "requested": strategy.value},
            )
        return GapPolicy(
            mode=normalised,
            requested=strategy,
            effective=GapHandlingStrategy.SKIP_EXECUTION,
            permitted=True,
            fills_gaps=False,
            disclosure=(
                "Forward fill is permitted for backtests, and this window was NOT forward "
                "filled: the platform's gap handler rejects a gapped window outright "
                "rather than persisting the last known price, so a gap is reported as a "
                "refusal instead of being repaired. Every bar served here is an exchange "
                "print."
            ),
        )

    return GapPolicy(
        mode=normalised,
        requested=strategy,
        effective=GapHandlingStrategy.SKIP_EXECUTION,
        permitted=True,
        fills_gaps=False,
        disclosure=None,
    )


# ══════════════════════════════════════════════════════════════════════════
# CLOSED BARS ONLY (Requirements 19.2, 19.3, 19.4)
# ══════════════════════════════════════════════════════════════════════════


def interval_for(timeframe: str) -> timedelta:
    """``timeframe``'s bar length, from the pipeline's own vocabulary.

    ``market_data_validation.TIMEFRAME_MINUTES`` is REUSED - the same table
    ``_validate_row_count`` measures coverage against and ``/registry/timeframes``
    intersects (task 7.2). There is deliberately no second table here.

    Unlike the coverage gate, an absent interval is **refused** rather than defaulted to an
    hour. Defaulting there merely disarms a check; defaulting here would decide whether a
    bar is closed using the wrong bar length, which either admits a forming bar or discards
    a finished one.

    Raises
        :class:`MarketDataContractError` (``TIMEFRAME_UNSUPPORTED``).
    """
    minutes = TIMEFRAME_MINUTES.get(str(timeframe))
    if minutes is None:
        raise MarketDataContractError(
            "TIMEFRAME_UNSUPPORTED",
            f"'{timeframe}' is not a bar interval this market data pipeline can measure, "
            f"so it cannot tell a closed bar from a forming one.",
            {"timeframe": timeframe, "supported": sorted(TIMEFRAME_MINUTES)},
        )
    return timedelta(minutes=int(minutes))


def _utc_now_naive() -> "Any":
    """Now, as the tz-naive UTC instant the platform's frames are indexed in."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(stamp: Any) -> Optional[Any]:
    """``stamp`` as a tz-naive UTC ``pandas.Timestamp``, or ``None`` if unreadable.

    The single interpretation of "when" on this path, and public since task 7.10 so a caller
    that has to align an arriving timestamp with this module's frames (``dag_event_loop``'s
    tick bucketing) reads the same rule instead of writing a second one. A tz-aware instant
    is converted to UTC; a naive one is taken as UTC already, which is what every frame the
    platform indexes assumes; a bare number is epoch milliseconds, the CCXT convention.
    ``None`` rather than an exception, because the callers here classify an unreadable row as
    malformed and count it.
    """
    import pandas as pd

    try:
        if isinstance(stamp, bool):
            return None
        if isinstance(stamp, (int, float)):
            if stamp != stamp:
                return None
            moment = pd.Timestamp(int(stamp), unit="ms")
        else:
            moment = pd.Timestamp(stamp)
    except (TypeError, ValueError, OverflowError):
        return None
    if moment is None or pd.isna(moment):
        return None
    if moment.tzinfo is not None:
        moment = moment.tz_convert("UTC").tz_localize(None)
    return moment


@dataclass
class IngestCounters:
    """What the arrival seam saw. Requirements 19.3, 19.4, 19.5.

    Every field is a count of events **this module** observed at the arrival boundary. See
    the module docstring for why ``late_events`` and ``out_of_order`` are counted here and
    why ``duplicates`` is not added to the validator's ``duplicate_timestamps``.
    """

    offered: int = 0
    accepted: int = 0
    forming_dropped: int = 0
    late_events: int = 0
    out_of_order: int = 0
    duplicates: int = 0
    malformed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "offered": int(self.offered),
            "accepted": int(self.accepted),
            "forming_dropped": int(self.forming_dropped),
            "late_events": int(self.late_events),
            "out_of_order": int(self.out_of_order),
            "duplicates": int(self.duplicates),
            "malformed": int(self.malformed),
        }


#: The columns an executor reads, in the order ``dag_engine`` and the golden-plan contract
#: expect them.
OHLCV_COLUMNS: Tuple[str, ...] = ("open", "high", "low", "close", "volume")

_TIMESTAMP_KEYS = ("open_time", "timestamp", "time", "t")


class ClosedBarIngest:
    """The arrival seam: closed bars in timestamp order, first-wins on duplicates.

    Requirement 19.2 - "THE Market_Data_Pipeline SHALL supply indicator computation with
    closed bars" - is the correctness property this class exists for. A forming bar is
    dropped at :meth:`offer` and therefore never reaches an executor, so a single bar
    timestamp cannot produce one indicator value now and a different one when the bar
    finishes.

    Parameters
        timeframe
            The bar interval. Refused if the pipeline has no length for it.
        drop_late
            ``True`` for streaming modes: a bar arriving with a timestamp below the highest
            already accepted is discarded and counted as a late event (Requirement 19.3).
            ``False`` for a historical read: the same arrival is counted as out-of-order
            and kept, and :meth:`frame` sorts it into place. This is ``design.md``'s
            "re-sort historical; for live, drop late events older than the last closed bar
            and count them", not a preference.
        now
            The instant "closed" is measured against, tz-naive UTC. Injectable so a test
            states the clock instead of racing it.

    Accepted candle shapes
        A CCXT row ``[open_time_ms, open, high, low, close, volume]``, or a mapping with
        those five names plus one of ``open_time`` / ``timestamp`` / ``time`` / ``t``. A
        mapping may carry ``is_closed`` - the design's canonical ``Candle`` field - and
        ``is_closed=False`` is honoured as authoritative: the feed saying the bar is
        forming outranks any inference from the clock.
    """

    def __init__(
        self,
        timeframe: str,
        *,
        drop_late: bool = False,
        now: Optional[datetime] = None,
    ):
        self.timeframe = str(timeframe)
        self.interval = interval_for(self.timeframe)
        self.drop_late = bool(drop_late)
        self._now = self._normalise_now(now) if now is not None else _utc_now_naive()
        self._bars: Dict[Any, List[float]] = {}
        self._high_water: Optional[Any] = None
        self.counters = IngestCounters()

    # -- the closed-bar rule ------------------------------------------------

    @property
    def closed_before(self) -> "Any":
        """The first open time that is NOT yet closed at this instance's clock.

        A bar opening at ``t`` is closed once ``t + interval <= now``. Expressed as a
        boundary so the rule is readable and testable on its own, and deliberately not as
        an epoch-aligned floor: alignment differs by venue and interval (``3d`` and ``1w``
        especially), while "has this bar's interval elapsed" needs no alignment at all.
        """
        import pandas as pd

        return pd.Timestamp(self._now) - self.interval

    def _is_closed(self, open_time: "Any") -> bool:
        return open_time <= self.closed_before

    # -- the clock, for a gate that outlives one batch (task 7.10) ----------

    @property
    def now(self) -> "Any":
        """The instant this gate currently measures "closed" against."""
        return self._now

    def advance_to(self, now: Any) -> "Any":
        """Move this gate's clock to ``now``, never backwards. Returns the clock.

        A batch read constructs a gate, offers a window and throws it away, so the instant
        fixed at construction is the right one for its whole life. A **stream** does not:
        one gate per symbol lives for the whole deployment, and a bar that was forming when
        the gate was built closes while the same gate is still running. So the clock has to
        move, and it moves through here rather than by a caller reaching into the instance.

        The move is monotonic on purpose. A bar this gate has already judged closed must not
        become forming again - if it could, one bar timestamp would produce two indicator
        values, which is exactly what Requirement 19.2 forbids and exactly the defect the
        gate exists to prevent. So a clock correction that runs backwards (NTP, a caller
        passing a stale instant) is **ignored**, not honoured.

        Raises
            :class:`MarketDataContractError` (``CLOCK_UNREADABLE``) when ``now`` cannot be
            read as an instant. Defaulting there would decide "closed" against a clock
            nobody supplied.
        """
        moment = self._normalise_now(now)
        if moment > self._now:
            self._now = moment
        return self._now

    @staticmethod
    def _normalise_now(now: Any) -> "Any":
        """``now`` as the tz-naive UTC instant this module's timestamps are compared in."""
        moment = ClosedBarIngest._parse_timestamp(now)
        if moment is None:
            raise MarketDataContractError(
                "CLOCK_UNREADABLE",
                f"'{now}' cannot be read as an instant, so a closed bar cannot be told "
                f"from a forming one.",
                {"now": str(now)},
            )
        return moment

    # -- arrival ------------------------------------------------------------

    def offer(self, candle: Any) -> bool:
        """Admit ``candle`` or drop it, counting the reason. ``True`` when admitted.

        The order of the four gates is the contract:

        1. **Unreadable** -> ``malformed``. A row whose timestamp or prices cannot be read
           is dropped here rather than becoming a NaN that survives into an indicator.
        2. **Forming** -> ``forming_dropped`` (Requirement 19.2).
        3. **Duplicate** -> ``duplicates``, and **the first candle is kept**
           (Requirement 19.4). This is the single place first-wins is enforced on this
           path; the stored row is never overwritten.
        4. **Below the high-water mark** -> ``out_of_order``, plus ``late_events`` and a
           drop when ``drop_late`` (Requirement 19.3).

        A duplicate is checked before ordering so a re-sent bar is counted as the duplicate
        it is rather than as a late event, which would make the late-event figure a mixture
        of two different feed faults.
        """
        self.counters.offered += 1

        parsed = self._parse(candle)
        if parsed is None:
            self.counters.malformed += 1
            return False
        open_time, values, feed_says_closed = parsed

        if feed_says_closed is False or not self._is_closed(open_time):
            self.counters.forming_dropped += 1
            return False

        if open_time in self._bars or open_time == self._high_water:
            # The high-water comparison matters after :meth:`drain`, which releases the
            # stored bars but keeps the mark: without it, a re-sent copy of the most
            # recently accepted bar would find an empty ``_bars`` and be admitted a second
            # time. For an instance that is never drained the two conditions are the same
            # test, because the highest accepted open time is always still held.
            self.counters.duplicates += 1
            return False

        if self._high_water is not None and open_time < self._high_water:
            self.counters.out_of_order += 1
            if self.drop_late:
                self.counters.late_events += 1
                return False

        self._bars[open_time] = values
        if self._high_water is None or open_time > self._high_water:
            self._high_water = open_time
        self.counters.accepted += 1
        self._record_admission_latency(open_time)
        return True

    def _record_admission_latency(self, open_time: Any) -> None:
        """``market_data.latency_ms`` for one admitted bar (Requirement 24.3).

        The figure is the delay between the bar **closing** and this gate admitting it:
        ``closed_before - open_time``, where :attr:`closed_before` is the boundary this gate
        already computes to decide "closed" (``now - interval``). So it is derived from the
        same arithmetic the closed-bar rule uses rather than from a second clock, and a bar
        admitted the instant it closed reads as zero.

        **Only on the streaming path** (``drop_late``). A historical read fixes its clock at
        construction and never advances it, so every bar in a fetched window would report
        its age rather than its delivery delay - a figure with a real meaning, but not this
        one, and mixing the two would make the percentiles describe neither. See
        :meth:`advance_to` for why the streaming clock is the one that tracks real time.

        A :class:`~metrics.Summary`, not a ``Histogram``: Requirement 19.12's margin is 25
        ms and a bucket edge near it would decide the comparison by the bucket layout.
        """
        if not self.drop_late:
            return
        collector = _metrics()
        if collector is None:
            return
        try:
            delay = self.closed_before - open_time
            latency_ms = delay.total_seconds() * 1000.0
        except Exception:  # noqa: BLE001 - an unmeasurable delay is simply not recorded
            return
        collector.record_market_data_latency(self.timeframe, latency_ms)

    def extend(self, candles: Iterable[Any]) -> "ClosedBarIngest":
        """Offer every candle in ``candles``, in the order given. Returns ``self``."""
        for candle in candles or ():
            self.offer(candle)
        return self

    # -- the frame ----------------------------------------------------------

    def frame(self) -> "Any":
        """The admitted bars as the OHLCV frame the executors read.

        Timestamp-indexed, sorted, tz-naive UTC, five float columns. Sorting is what makes
        ``drop_late=False`` honest: an out-of-order historical arrival was counted and is
        placed correctly, so the series a downstream indicator sees is monotonic either
        way (Requirement 19.1's timestamp ordering).
        """
        import pandas as pd

        if not self._bars:
            return pd.DataFrame(
                columns=list(OHLCV_COLUMNS),
                index=pd.DatetimeIndex([], name="timestamp"),
            )
        index = pd.DatetimeIndex(list(self._bars.keys()), name="timestamp")
        frame = pd.DataFrame(list(self._bars.values()), columns=list(OHLCV_COLUMNS), index=index)
        return frame.sort_index()

    def drain(self) -> List[Tuple[Any, List[float]]]:
        """Release the accepted bars, oldest first, and clear them from this gate.

        The streaming counterpart of :meth:`frame` (task 7.10). A batch caller wants the
        whole window as a frame; a stream wants each admitted bar once, because the window an
        executor reads is maintained downstream and a gate that also kept every bar for the
        life of a deployment would grow without bound.

        The high-water mark survives the drain, which is what keeps the drop rules intact on
        an emptied gate: a re-sent copy of the most recent bar is still counted as a
        duplicate (:meth:`offer` compares the mark as well as the stored bars) and anything
        older is still counted late and dropped. Each pair is ``(open_time, [open, high,
        low, close, volume])`` - the same values :meth:`frame` would place in a row, in the
        same column order.

        Refused unless ``drop_late`` is set, because on a re-sorting historical gate the
        stored bars are the only record of what has been seen and draining them would let a
        duplicate through as a new bar.

        Raises
            :class:`MarketDataContractError` (``DRAIN_REQUIRES_STREAMING``).
        """
        if not self.drop_late:
            raise MarketDataContractError(
                "DRAIN_REQUIRES_STREAMING",
                "Draining a historical gate is refused: it re-sorts rather than dropping "
                "late arrivals, so its stored bars are the only record of what it has "
                "already admitted. Read `frame()` instead.",
                {"timeframe": self.timeframe, "drop_late": self.drop_late},
            )
        released = [(open_time, self._bars[open_time]) for open_time in sorted(self._bars)]
        self._bars = {}
        return released

    # -- parsing ------------------------------------------------------------

    @staticmethod
    def _parse(candle: Any) -> Optional[Tuple[Any, List[float], Optional[bool]]]:
        """``(open_time, [o, h, l, c, v], is_closed_flag)`` or ``None`` if unreadable."""
        if isinstance(candle, Mapping):
            stamp = None
            for key in _TIMESTAMP_KEYS:
                if key in candle:
                    stamp = candle[key]
                    break
            if stamp is None:
                return None
            try:
                raw = [candle[name] for name in OHLCV_COLUMNS]
            except KeyError:
                return None
            flag = candle.get("is_closed")
            feed_says_closed = None if flag is None else bool(flag)
        else:
            try:
                row = list(candle)
            except TypeError:
                return None
            if len(row) < 6:
                return None
            stamp, raw = row[0], row[1:6]
            feed_says_closed = None

        open_time = ClosedBarIngest._parse_timestamp(stamp)
        if open_time is None:
            return None

        values: List[float] = []
        for item in raw:
            try:
                number = float(item)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(number):
                # NaN or infinity in a price or a volume. Dropped as unreadable rather
                # than handed on: `dag_engine`'s arithmetic firewall is a last line of
                # defence, not a reason to feed it a value that is already known bad.
                return None
            values.append(number)
        return open_time, values, feed_says_closed

    @staticmethod
    def _parse_timestamp(stamp: Any) -> Optional[Any]:
        return to_utc_naive(stamp)


def closed_bar_frame(
    rows: Any,
    timeframe: str,
    *,
    drop_late: bool = False,
    now: Optional[datetime] = None,
) -> Tuple[Any, IngestCounters]:
    """``(frame, counters)``: ``rows`` as closed bars only.

    The batch form of :class:`ClosedBarIngest`, which it delegates to rather than
    reimplementing, so a batch read and a live stream cannot disagree about what a closed
    bar is.
    """
    ingest = ClosedBarIngest(timeframe, drop_late=drop_late, now=now).extend(rows)
    return ingest.frame(), ingest.counters


# ══════════════════════════════════════════════════════════════════════════
# THE EXISTING VALIDATOR, SURFACED (Requirements 19.1, 19.5)
# ══════════════════════════════════════════════════════════════════════════


async def quality_report(frame: Any, symbol: str, timeframe: str) -> DataQualityReport:
    """The platform validator's report for ``frame``. Nothing computed here.

    ``market_data_validation.MarketDataValidator`` is REUSED AS-IS via ``get_validator()``:
    the required columns, the OHLC relationships, positive prices, non-negative volume,
    duplicate timestamps, ordering, the outlier filter and the gap gate are its checks
    (Requirement 19.1), and ``DataQualityReport`` is its report.

    Its posture is strict and is deliberately not softened here. ``OutlierDetector``
    refuses a window holding a close beyond three standard deviations, and
    ``GapHandler.handle`` refuses any window with a gap wider than 1.5 bars. Both mean
    "this window cannot be computed on", so both are reported as one classified
    ``DATA_QUALITY`` refusal carrying the validator's own message - the same mapping
    ``strategy_service.quality_check_training_frame`` makes for the training window
    (Phase 6). Relaxing a threshold from this module would be the ingest layer quietly
    overriding a data-integrity rule the rest of the platform is held to.

    Raises
        :class:`MarketDataContractError` (``DATA_QUALITY``) when the validator rejects the
        window outright.
    """
    try:
        _cleaned, report = await get_validator().validate(frame, symbol, timeframe)
    except DataValidationError as exc:
        raise MarketDataContractError(
            "DATA_QUALITY",
            f"The market data for {symbol} at {timeframe} was rejected by the platform's "
            f"data validator: {exc}",
            {"symbol": symbol, "timeframe": timeframe, "validator_error": str(exc)},
        ) from exc
    # Requirement 24.3's `market_data.quality_score`. The validator's own
    # `DataQualityReport.quality_score` is republished, not recomputed - this module grades
    # nothing, and a second scoring arithmetic here would be the defect the module docstring
    # forbids. A window the validator *refused* records nothing, because the raise above
    # means there is no graded score to record.
    collector = _metrics()
    if collector is not None:
        collector.set_market_data_quality_score(
            symbol, timeframe, getattr(report, "quality_score", None)
        )
    return report


@dataclass
class IngestResult:
    """One validated, closed-bar window and the whole truth about how it was produced."""

    frame: Any
    counters: IngestCounters
    gap_policy: GapPolicy
    report: DataQualityReport

    def to_dict(self) -> Dict[str, Any]:
        """The wire form. ``quality`` is the existing report, republished verbatim.

        ``counters`` and ``quality`` are separate keys on purpose: they count different
        things at different layers and adding them would double-count duplicates. See the
        module docstring.
        """
        return {
            "mode": self.gap_policy.mode,
            "closed_bars_only": True,
            "counters": self.counters.to_dict(),
            "gap_policy": self.gap_policy.to_dict(),
            "quality": self.report.to_dict(),
        }


async def validated_window(
    rows: Any,
    symbol: str,
    timeframe: str,
    *,
    mode: str,
    gap_strategy: Optional[GapHandlingStrategy] = None,
    bars: Optional[int] = None,
    now: Optional[datetime] = None,
) -> IngestResult:
    """The one entry point: raw candles in, a validated closed-bar window out.

    Order of operations, and why it is this order:

    1. :func:`resolve_gap_policy` - **before any candle is read**. An environment
       configured to fabricate candles is refused while the refusal is still free, rather
       than after a fetch that would then have to be thrown away.
    2. :func:`closed_bar_frame` - the forming bar, the duplicate and the late arrival are
       resolved at the seam, so the validator and every executor downstream of it see one
       value per timestamp (Requirements 19.2, 19.3, 19.4).
    3. ``bars`` tail-limit, if asked. Applied after the gate so a window trimmed to N bars
       holds N *closed* bars.
    4. :func:`quality_report` - the existing validator, on the frame that will actually be
       computed on (Requirement 19.1: "before the DAG_Runtime receives that batch").

    Raises
        :class:`MarketDataContractError`. ``NO_CLOSED_BARS`` when the gate admitted
        nothing - reported distinctly from an empty feed, because "the feed sent only a
        forming bar" and "the feed sent nothing" are different facts - plus any code
        raised by the three steps above.
    """
    policy = resolve_gap_policy(mode, gap_strategy)
    frame, counters = closed_bar_frame(
        rows, timeframe, drop_late=policy.mode in _STREAMING_MODES, now=now
    )
    if bars is not None:
        frame = frame.tail(int(bars))
    if frame.empty:
        raise MarketDataContractError(
            "NO_CLOSED_BARS",
            f"The market data feed supplied no closed bar for {symbol} at {timeframe}, so "
            f"there is nothing an indicator may be computed on.",
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "counters": counters.to_dict(),
            },
        )
    report = await quality_report(frame, symbol, timeframe)
    return IngestResult(
        frame=frame, counters=counters, gap_policy=policy, report=report
    )
