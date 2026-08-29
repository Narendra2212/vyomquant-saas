"""backend/feed_state.py — Feed liveness, measured rather than assumed.

Spec: strategy-builder task 7.4. ``design.md`` -> Data preview and honesty ("Feed state is
reported literally"). Requirements 19.6, 19.7, 19.8, 19.9, 19.10, and 22.4's
``DISCONNECTED`` mapping.

WHAT THIS MODULE IS FOR
-----------------------
One question: *how old is the newest thing this feed delivered, measured against how often
it is supposed to deliver?* Every state below is a consequence of that measurement plus two
other measured facts (is the transport up, are there enough bars for the compiled warmup).
There is no timer that decays a state, no optimistic default, and no "assume ``LIVE`` until
proven otherwise". A feed nobody has measured is never reported ``LIVE``.

That is the whole point of the task: **stale data must never be labelled ``LIVE``**. The
failure mode being designed out is the common one - a UI that shows a green dot because a
socket object exists, while the last candle arrived forty minutes ago.

THE STATE VOCABULARY IS CLOSED
------------------------------
Requirement 19.6 fixes it at exactly five: ``LIVE``, ``DELAYED``, ``STALE``,
``DISCONNECTED``, ``INSUFFICIENT_DATA``. Nothing here invents a sixth, and nothing collapses
two into one: ``DISCONNECTED`` (nothing can arrive) and ``INSUFFICIENT_DATA`` (things are
arriving, there just are not enough of them yet) are different answers and stay different.
Because the vocabulary is closed, an unmeasurable feed has to be reported *as* one of the
five - see "When the measurement cannot be made" below for which one and why.

THE BOUNDARIES, AND WHICH SIDE EACH ONE FALLS
---------------------------------------------
Let ``i`` be the expected bar interval in seconds and ``a`` the age of the last event.

===================  ==========================================================
``LIVE``             ``a < 1.5 i``   (strict; 19.7 "newer than 1.5 times")
``DELAYED``          ``1.5 i <= a < 3 i``  (19.8 "older than 1.5 times")
``STALE``            ``3 i <= a``    (19.9 "older than 3 times")
===================  ==========================================================

Both boundaries are closed on the *worse* side deliberately. Exactly ``1.5 i`` reads
``DELAYED``, not ``LIVE``; exactly ``3 i`` reads ``STALE``, not ``DELAYED``. That makes
Property 26 - "any feed reported ``LIVE`` has a last-event age below 1.5 x the expected
interval" - true **by construction** rather than true by a rounding accident, and it means
every ambiguous instant is resolved against the feed rather than in its favour.

PRECEDENCE, WHEN MORE THAN ONE CONDITION HOLDS
----------------------------------------------
Requirements 19.7-19.10 are all ``WHILE`` clauses over overlapping conditions: a feed can be
three intervals old *and* short of warmup at the same time. One label has to be chosen, so
the order is written down here rather than left to the order of a chain of ``if``\\ s:

1. ``DISCONNECTED`` - the transport is not up. Nothing can arrive, which makes every
   age-derived and bar-derived reading provisional (Requirement 22.4).
2. ``STALE`` - a *measured* age of ``3 i`` or more. Data has stopped. Reporting a bar
   shortfall here would read as "keep waiting, bars are accumulating" when they demonstrably
   are not, and that optimistic reading is the defect this task exists to remove.
3. ``INSUFFICIENT_DATA`` - fewer available bars than the compiled ``warmup_bars``. Nothing
   can compute yet even though data is arriving (Requirement 19.10).
4. ``STALE`` again, as the fail-closed answer when the age cannot be measured at all - see
   below. It sits *below* the bar check on purpose: a known bar shortfall is an observation,
   an unmeasurable age is the absence of one, and a real observation outranks a guess.
5. ``DELAYED`` - a measured age in ``[1.5 i, 3 i)``.
6. ``LIVE`` - transport up, age measured, ``a < 1.5 i``, warmup satisfied (or uncountable).

Choosing a precedence means one requirement's letter yields to another's in the overlap - a
feed that is both delayed and short of warmup is reported ``INSUFFICIENT_DATA``, not
``DELAYED``. Nothing is lost by that, because the report carries **every** input alongside
the label: ``age_seconds``, ``expected_interval_seconds``, ``age_state`` (the pure
age-derived classification, which is still ``DELAYED`` in that example), ``connected``,
``available_bars``, ``warmup_bars`` and ``bars_missing``. A client that wants the age answer
rather than the precedence answer reads ``age_state``; it does not have to re-derive it.

WHEN THE MEASUREMENT CANNOT BE MADE
-----------------------------------
Two ways it fails, both fail closed to ``STALE`` (reason recorded, numbers ``null``):

* **No last event observed.** No candle has been seen, so there is no age. ``null`` is
  reported as ``null`` - never as zero, which would read as "just arrived".
* **No expected interval published for this bar label.** ``expected_interval_seconds``
  reads :data:`market_data_validation.TIMEFRAME_MINUTES`, the market-data path's own
  statement of which intervals it can measure. A label absent from it is a real limitation
  of the pipeline for that interval (recorded at that constant by task 7.2), so freshness
  for it cannot be measured, and *guessing* an hour - which is what the row-coverage gate's
  ``.get(timeframe, 60)`` default does - would let a 3m feed sit twenty minutes stale and
  still read ``LIVE``. ``/registry/timeframes`` publishes only labels the whole pipeline
  supports, so a timeframe an author can actually select is always measurable here.

WHY THE INTERVAL IS NOT PARSED OUT OF THE LABEL
-----------------------------------------------
Arithmetic on the string ("5m" -> 300) would accept any label at all, including ones no
stage of the pipeline can process, and it would answer for them confidently. The interval is
therefore a **table lookup against a pipeline vocabulary**, and the lookup is
case-sensitive: CCXT writes a month as ``1M`` and a minute as ``1m``, so lower-casing the
key before the lookup would turn a month-old candle into a one-minute one - a 43,200x error,
in the direction of reporting stale data as ``LIVE``.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not ingest, count, de-duplicate, order or validate anything. The ingest side -
closed-bar-only computation and the late / duplicate / out-of-order / integrity counters -
is task 7.5's, and the validators are ``market_data_validation``'s, reused as they are. This
module reads observations and classifies them.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("FeedState")


def _metrics() -> Any:
    """``backend/metrics.py``'s collector, or ``None``. Lazy and guarded (task 9.1).

    Lazy for the same reason ``market_data_validation`` is imported inside
    :func:`expected_interval_seconds`: this module is a pure classifier and importing it
    must stay cheap. Guarded because :func:`evaluate_feed_state` documents that it *never
    raises*, and instrumentation is not permitted to be the exception to that.
    """
    try:
        from backend_app.backend.metrics import metrics_collector

        return metrics_collector
    except Exception:  # noqa: BLE001 - instrumentation never breaks its caller
        return None


def _builder_alerts() -> Any:
    """``backend/builder_alerts.py``, or ``None``. Lazy and guarded (task 9.2).

    Requirement 24.5's sustained-``STALE`` alert. Kept separate from :func:`_metrics`
    because the alert module reaches the platform's dispatcher and therefore ``aiohttp`` and
    Redis, and this module is a pure classifier whose import must stay cheap. Guarded for the
    same reason :func:`_metrics` is: :func:`evaluate_feed_state` never raises, and an alert is
    not an exception to that.
    """
    try:
        from backend_app.backend import builder_alerts

        return builder_alerts
    except Exception:  # noqa: BLE001 - an alert never breaks the act it observes
        return None


class FeedState(Enum):
    """The five states Requirement 19.6 permits, and no others."""

    LIVE = "LIVE"
    DELAYED = "DELAYED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


#: Multiple of the expected interval at which a feed stops being ``LIVE`` (Requirement 19.7,
#: 19.8). ``LIVE`` is ``age < 1.5 x interval`` **strictly**, so Property 26 holds by
#: construction.
DELAYED_AT_INTERVALS = 1.5

#: Multiple of the expected interval at which a feed becomes ``STALE`` (Requirement 19.9).
STALE_AT_INTERVALS = 3.0

# ── Reasons. The state says what to do; the reason says what was measured. ─────────────
REASON_FRESH = "AGE_WITHIN_EXPECTED_INTERVAL"
REASON_AGE_OVER_DELAYED = "AGE_AT_OR_OVER_1_5_INTERVALS"
REASON_AGE_OVER_STALE = "AGE_AT_OR_OVER_3_INTERVALS"
REASON_TRANSPORT_DOWN = "TRANSPORT_NOT_CONNECTED"
REASON_TRANSPORT_UNKNOWN = "TRANSPORT_STATE_UNKNOWN"
REASON_WARMUP_UNFILLED = "FEWER_BARS_THAN_COMPILED_WARMUP"
REASON_INTERVAL_UNKNOWN = "EXPECTED_INTERVAL_NOT_PUBLISHED"
REASON_AGE_UNKNOWN = "NO_EVENT_OBSERVED"

#: ``observation_source`` values. ``NO_FEED`` and ``UNAVAILABLE`` are different facts: no
#: connection is registered for this market, versus the monitor itself could not be read.
OBSERVATION_MONITOR = "websocket_monitor"
OBSERVATION_NO_FEED = "websocket_monitor:no_connection_for_market"
OBSERVATION_UNAVAILABLE = "unavailable"
OBSERVATION_SUPPLIED = "supplied"


def expected_interval_seconds(timeframe: Any) -> Optional[int]:
    """Seconds in one bar of ``timeframe``, or ``None`` when the pipeline publishes none.

    Read from :data:`market_data_validation.TIMEFRAME_MINUTES` - the market-data path's own
    vocabulary, hoisted into view by task 7.2 - and **not** parsed out of the label. See the
    module docstring: parsing would answer for intervals no stage can process, and
    lower-casing the key would confuse ``1M`` (a month) with ``1m`` (a minute).

    ``None`` is a real answer here, and its consequence is that freshness cannot be
    measured for that label. It is never substituted with a default; the gate in
    ``_validate_row_count`` defaults an unknown label to 60 minutes, and that default is the
    reason an unknown label must not be measured at all rather than measured as an hour.
    """
    from backend_app.backend.market_data_validation import TIMEFRAME_MINUTES

    if not isinstance(timeframe, str):
        return None
    minutes = TIMEFRAME_MINUTES.get(timeframe.strip())
    if minutes is None:
        return None
    try:
        seconds = int(minutes) * 60
    except (TypeError, ValueError):  # pragma: no cover - the table holds ints
        return None
    return seconds if seconds > 0 else None


def format_age(seconds: Optional[float]) -> Optional[str]:
    """``252.0`` -> ``"4m 12s"``. ``None`` -> ``None``, never ``"0s"``.

    An unknown age must not render as a fresh one, so the absence propagates as an absence
    all the way to the string a panel prints.
    """
    if seconds is None:
        return None
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return None
    if total != total or total in (float("inf"), float("-inf")):  # NaN / infinities
        return None
    total = max(0.0, total)
    whole = int(total)
    if whole < 60:
        return f"{whole}s"
    minutes, secs = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"


@dataclass(frozen=True)
class FeedObservation:
    """What was *observed* about one market's feed, before any classification.

    Deliberately separate from :class:`FeedStateReport`: the observation is a reading, the
    report is a verdict about a reading. Keeping them apart is what lets the classifier be
    tested over injected ages - including ages no live environment would produce - without a
    socket, a Redis or a venue anywhere near it.

    ``connected=None`` means the transport state could not be read. It is **not** treated as
    connected (see :func:`evaluate_feed_state`).
    """

    connected: Optional[bool] = None
    age_seconds: Optional[float] = None
    last_event_at: Optional[str] = None
    source: str = OBSERVATION_UNAVAILABLE
    connections_matched: int = 0
    detail: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class FeedStateReport:
    """One feed state, plus every number the state was derived from.

    Requirement 19.8 asks for the age of the last event *together with* the expected
    interval, and the task asks for the concrete figures rather than a colour. So the
    figures are not optional extras on this structure: ``age_seconds`` and
    ``expected_interval_seconds`` are always present as keys, ``null`` when unmeasured, and
    :attr:`display` renders both into the sentence ``design.md`` specifies ("last candle
    4m 12s ago, expected every 1m"). A client cannot render a colour from this payload
    without the numbers, because the numbers are what it has.
    """

    state: FeedState
    reason: str
    timeframe: Optional[str] = None
    expected_interval_seconds: Optional[int] = None
    age_seconds: Optional[float] = None
    last_event_at: Optional[str] = None
    age_state: Optional[FeedState] = None
    connected: Optional[bool] = None
    available_bars: Optional[int] = None
    warmup_bars: Optional[int] = None
    observation_source: str = OBSERVATION_UNAVAILABLE
    observed_at: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None

    @property
    def measured(self) -> bool:
        """Whether the state rests on a real age against a real interval."""
        return self.age_seconds is not None and self.expected_interval_seconds is not None

    @property
    def delayed_after_seconds(self) -> Optional[float]:
        if self.expected_interval_seconds is None:
            return None
        return round(self.expected_interval_seconds * DELAYED_AT_INTERVALS, 3)

    @property
    def stale_after_seconds(self) -> Optional[float]:
        if self.expected_interval_seconds is None:
            return None
        return round(self.expected_interval_seconds * STALE_AT_INTERVALS, 3)

    @property
    def bars_missing(self) -> Optional[int]:
        if self.available_bars is None or self.warmup_bars is None:
            return None
        return max(0, int(self.warmup_bars) - int(self.available_bars))

    @property
    def display(self) -> str:
        """The sentence ``design.md`` asks the panel to show. Numbers, not a colour."""
        age_text = format_age(self.age_seconds)
        label = self.timeframe or "?"
        if self.expected_interval_seconds is None:
            if age_text is None:
                return (
                    f"No candle observed, and no bar interval is published for "
                    f"'{label}', so freshness cannot be measured."
                )
            return (
                f"Last candle {age_text} ago; no bar interval is published for "
                f"'{label}', so freshness cannot be measured."
            )
        every = f"expected every {label}"
        if age_text is None:
            return f"No candle observed, {every}."
        if self.state is FeedState.INSUFFICIENT_DATA:
            missing = self.bars_missing
            counted = (
                f" {self.available_bars} of {self.warmup_bars} warmup bars available"
                if self.available_bars is not None and self.warmup_bars is not None
                else ""
            )
            shortfall = f", {missing} short" if missing else ""
            return f"Last candle {age_text} ago, {every}.{counted}{shortfall}."
        return f"Last candle {age_text} ago, {every}."

    def to_dict(self) -> Dict[str, Any]:
        """The wire form. Every derivation input is on it, ``null`` where unmeasured."""
        return {
            "state": self.state.value,
            "reason": self.reason,
            "display": self.display,
            # ── the measurement (Requirement 19.8: age together with the interval) ──
            "age_seconds": (
                None if self.age_seconds is None else round(float(self.age_seconds), 3)
            ),
            "age_text": format_age(self.age_seconds),
            "last_event_at": self.last_event_at,
            "timeframe": self.timeframe,
            "expected_interval_seconds": self.expected_interval_seconds,
            "measured": self.measured,
            # ── the thresholds, so a client can check the label against the numbers ──
            "delayed_at_intervals": DELAYED_AT_INTERVALS,
            "stale_at_intervals": STALE_AT_INTERVALS,
            "delayed_after_seconds": self.delayed_after_seconds,
            "stale_after_seconds": self.stale_after_seconds,
            # ── the other two facts, kept distinct from the age ──
            "connected": self.connected,
            "available_bars": self.available_bars,
            "warmup_bars": self.warmup_bars,
            "bars_missing": self.bars_missing,
            # ── the age-only classification, so precedence hides nothing ──
            "age_state": None if self.age_state is None else self.age_state.value,
            # ── provenance ──
            "observation_source": self.observation_source,
            "observed_at": self.observed_at,
            "detail": dict(self.detail or {}),
        }


def classify_age(
    age_seconds: Optional[float], interval_seconds: Optional[int]
) -> Optional[FeedState]:
    """``LIVE`` / ``DELAYED`` / ``STALE`` from the age alone, or ``None`` if unmeasurable.

    The one place the two thresholds are compared, so the endpoint, the display sentence and
    the property test cannot disagree about where a boundary falls. ``LIVE`` is strict
    ``<``; both boundaries fall on the worse side (see the module docstring).
    """
    if age_seconds is None or interval_seconds is None:
        return None
    try:
        age = float(age_seconds)
        interval = float(interval_seconds)
    except (TypeError, ValueError):
        return None
    if age != age or interval != interval or interval <= 0:  # NaN or nonsense interval
        return None
    if age < 0:
        # A last event in the future is not a fresh one: it is a clock or a timestamp
        # problem, and reporting it as `LIVE` would be reporting an unmeasured feed as
        # live. Treated as unmeasurable.
        return None
    if age < DELAYED_AT_INTERVALS * interval:
        return FeedState.LIVE
    if age < STALE_AT_INTERVALS * interval:
        return FeedState.DELAYED
    return FeedState.STALE


def evaluate_feed_state(
    *,
    timeframe: Optional[str],
    connected: Optional[bool],
    age_seconds: Optional[float] = None,
    available_bars: Optional[int] = None,
    warmup_bars: Optional[int] = None,
    last_event_at: Optional[str] = None,
    interval_seconds: Optional[int] = None,
    observation_source: str = OBSERVATION_SUPPLIED,
    observed_at: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    deployment_mode: Optional[str] = None,
) -> FeedStateReport:
    """Classify one feed from measured facts. Never raises, never defaults optimistically.

    Parameters
        ``timeframe`` the DATA block's own bar label; the expected interval is looked up
        from it unless ``interval_seconds`` is supplied directly (which is how a test
        injects an interval without pinning a label).
        ``connected`` ``True`` only when the transport is known to be up. ``False`` and
        ``None`` both produce ``DISCONNECTED``, with different reasons: a transport whose
        state cannot be read is not a transport that is working.
        ``age_seconds`` age of the newest delivered event. ``None`` means *unknown*, and
        unknown is never treated as zero.
        ``available_bars`` / ``warmup_bars`` Requirement 19.10's comparison. Either being
        ``None`` means the comparison cannot be made, and it is then not asserted - the
        caller is expected to pass ``None`` rather than a truncated count (see the
        endpoint's ``window_covers_warmup``).
        ``deployment_mode`` (task 9.2) which deployment, if any, this reading is for -
        ``market_data_contract``'s ``MODE_PAPER`` / ``MODE_LIVE`` vocabulary. It changes
        **no** classification: the state, the reason and every figure on the report are
        identical whatever is passed. It exists only because Requirement 24.5's alert is
        qualified "for a live deployment", and the mode is a fact about the caller that the
        classifier cannot measure. ``None`` - the default, and what a data preview passes -
        raises no alert, and neither does ``paper``.

    Postconditions
        ``report.state`` is one of the five members of :class:`FeedState`.
        ``report.state is FeedState.LIVE`` implies ``connected is True``,
        ``age_seconds is not None``, an interval is known, and
        ``age_seconds < 1.5 * interval`` - Property 26, by construction.
    """
    interval = (
        interval_seconds
        if interval_seconds is not None
        else expected_interval_seconds(timeframe)
    )
    if interval is not None:
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            interval = None
        if interval is not None and interval <= 0:
            interval = None

    age = None
    if age_seconds is not None:
        try:
            candidate = float(age_seconds)
        except (TypeError, ValueError):
            candidate = None
        if candidate is not None and candidate == candidate and candidate >= 0.0:
            age = candidate

    bars = None
    if available_bars is not None:
        try:
            bars = int(available_bars)
        except (TypeError, ValueError):
            bars = None
    warmup = None
    if warmup_bars is not None:
        try:
            warmup = int(warmup_bars)
        except (TypeError, ValueError):
            warmup = None

    age_state = classify_age(age, interval)

    def report(state: FeedState, reason: str) -> FeedStateReport:
        # Requirement 24.3's `market_data.feed_state`, recorded in the one closure every
        # return path below goes through - so a sixth classification added later is counted
        # without anybody remembering to. State and reason are both labelled: the state says
        # what to do and the reason says what was measured, and task 9.2's sustained-STALE
        # alert needs to tell a measured stop from an unmeasurable one. No symbol label: a
        # market is chosen by an author, and one series per market per state per reason is
        # how a counter becomes unbounded.
        collector = _metrics()
        if collector is not None:
            collector.record_feed_state(state, reason, timeframe)
        built = FeedStateReport(
            state=state,
            reason=reason,
            timeframe=timeframe,
            expected_interval_seconds=interval,
            age_seconds=age,
            last_event_at=last_event_at,
            age_state=age_state,
            connected=connected,
            available_bars=bars,
            warmup_bars=warmup,
            observation_source=observation_source,
            observed_at=observed_at or datetime.now(timezone.utc).isoformat(),
            detail=dict(detail or {}),
        )
        # Requirement 24.5's alert, raised from the same closure the metric is recorded in
        # and **after** the report exists, so the alert reads the classifier's verdict rather
        # than re-deriving one. `builder_alerts.feed_state_condition` decides: it fires only
        # on a live deployment and only on the measured-stop arm
        # (`REASON_AGE_OVER_STALE`), never on the two fail-closed STALE arms, which are a
        # feed that never started and an interval the pipeline does not publish. Synchronous,
        # total and non-blocking - this classifier still never raises and still returns
        # promptly. Nothing below reads `built` again, so the alert cannot change the verdict.
        alerts = _builder_alerts()
        if alerts is not None:
            try:
                alerts.notice_feed_state(built, deployment_mode)
            except Exception:  # noqa: BLE001 - this function never raises. No exceptions.
                # `notice_feed_state` is itself total; this is the second of two independent
                # guards, so the "never raises" postcondition does not depend on a decorator
                # in another module staying applied.
                logger.debug("The stale-feed alert was not raised.", exc_info=True)
        return built

    # 1. Nothing can arrive (Requirement 22.4).
    if connected is not True:
        return report(
            FeedState.DISCONNECTED,
            REASON_TRANSPORT_DOWN if connected is False else REASON_TRANSPORT_UNKNOWN,
        )

    # 2. A measured stop. Outranks the bar shortfall it usually causes.
    if age_state is FeedState.STALE:
        return report(FeedState.STALE, REASON_AGE_OVER_STALE)

    # 3. Arriving, but not enough of it yet (Requirement 19.10).
    if bars is not None and warmup is not None and bars < warmup:
        return report(FeedState.INSUFFICIENT_DATA, REASON_WARMUP_UNFILLED)

    # 4. Fail closed: an age that cannot be measured is not a fresh age.
    if age_state is None:
        return report(
            FeedState.STALE,
            REASON_INTERVAL_UNKNOWN if interval is None else REASON_AGE_UNKNOWN,
        )

    # 5. / 6. The measured age decides (Requirements 19.7, 19.8).
    if age_state is FeedState.DELAYED:
        return report(FeedState.DELAYED, REASON_AGE_OVER_DELAYED)
    return report(FeedState.LIVE, REASON_FRESH)


def _normalise_symbol(symbol: Any) -> str:
    """``"ETH/USDT"``, ``"eth-usdt"`` and ``"ETHUSDT"`` compared as one thing.

    Only separators and case are normalised. Nothing is *translated*: ``ETH-USD`` and
    ``ETH-USDT`` stay different markets, so a connection to a different quote currency
    cannot lend its freshness to this one.
    """
    text = str(symbol or "")
    return "".join(ch for ch in text if ch.isalnum()).upper()


def observe_feed(symbol: str) -> FeedObservation:
    """Read the platform's existing connection monitor for ``symbol``'s feed.

    ``websocket_monitor`` already tracks, per connection, the state and the timestamp of the
    last message it received (STEP 8.7). That is the platform's own observation of feed
    liveness, so this reads it rather than starting a second one. Nothing here writes to the
    monitor, changes its thresholds or touches the ingest path (task 7.5's).

    Three honest outcomes, kept distinct
        * a matching connection: its state and its last-message age;
        * **no** connection registered for this market - ``connected=False``,
          ``OBSERVATION_NO_FEED``. Nothing is arriving for this symbol, which is what
          ``DISCONNECTED`` means; the reason says it is because there is no subscription
          rather than because one dropped;
        * the monitor could not be read at all - ``connected=None``,
          ``OBSERVATION_UNAVAILABLE``, which also lands on ``DISCONNECTED`` but says that
          the platform could not answer rather than that the feed is down.

    Which connection, when several match
        The one on the server's ``DEFAULT_EXCHANGE`` when there is one, because that is the
        feed the platform actually reads. Otherwise the **least fresh** match - never the
        freshest. A second venue's healthy socket must not make this market look live.

    The venue is never returned. It is a deployment fact, not a response field (SB-06,
    Requirement 12.1); the monitor's ``exchange_id`` is used for the preference above and
    stays out of the observation.
    """
    try:
        from backend_app.backend.websocket_monitor import get_websocket_monitor

        statuses = get_websocket_monitor().get_all_status() or {}
    except Exception as e:  # the monitor is infrastructure; its absence is not a 500
        logger.warning("Feed observation unavailable for %s: %s", symbol, e)
        return FeedObservation(
            connected=None,
            source=OBSERVATION_UNAVAILABLE,
            detail={"error": type(e).__name__},
        )

    wanted = _normalise_symbol(symbol)
    matches = [
        status
        for status in statuses.values()
        if isinstance(status, dict) and _normalise_symbol(status.get("symbol")) == wanted
    ]
    if not matches:
        return FeedObservation(
            connected=False,
            source=OBSERVATION_NO_FEED,
            connections_matched=0,
            detail={"monitored_connections": len(statuses)},
        )

    venue = (os.getenv("DEFAULT_EXCHANGE") or "").strip().lower()
    preferred = [
        status
        for status in matches
        if str(status.get("exchange_id") or "").strip().lower() == venue
    ]
    pool = preferred if (venue and preferred) else matches

    def staleness(status: Dict[str, Any]) -> tuple:
        age = status.get("seconds_since_last_message")
        if age is None:
            return (1, 0.0)  # an unmeasured connection sorts as the worst one
        try:
            return (0, float(age))
        except (TypeError, ValueError):
            return (1, 0.0)

    chosen = max(pool, key=staleness)
    state = str(chosen.get("state") or "").strip().lower()
    # `stale` is the monitor's own 10-second flag, which is not a bar-interval judgement and
    # is not this module's threshold: the socket is still up, so the age decides. Only a
    # transport that is down or re-establishing is reported as not connected.
    connected = state == "connected" or state == "stale"
    age = chosen.get("seconds_since_last_message")
    if age is not None:
        try:
            age = float(age)
        except (TypeError, ValueError):
            age = None

    return FeedObservation(
        connected=connected,
        age_seconds=age,
        last_event_at=chosen.get("last_message_at"),
        source=OBSERVATION_MONITOR,
        connections_matched=len(matches),
        detail={
            "monitor_state": state or None,
            "monitor_is_stale": bool(chosen.get("is_stale")),
            "monitor_stale_threshold_seconds": chosen.get("stale_threshold_seconds"),
            "total_messages": chosen.get("total_messages"),
            "disconnect_count": chosen.get("disconnect_count"),
        },
    )
