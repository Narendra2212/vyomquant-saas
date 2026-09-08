"""
backend/paper/paper_market_feed.py - real market data for a Paper_Session, or none at all.

Spec: marketplace-subscriptions-paper-trading tasks 24.1-24.4. ``design.md`` ->
"``paper/paper_market_feed.py``". Requirements 14.1-14.10, 15.5, 18.15, 26.6, 28.3.

Exposes
-------
PaperFeedConfig                 the five facts a feed needs: session, user, exchange, symbol, tf
PaperMarketDataUnavailable      409 ``PAPER_MARKET_DATA_UNAVAILABLE`` - the only refusal here
measurement_for(source, ...)    the recorded measurement for one candidate, or an unmeasured one
open_feed(config, ...)          source selection, the two refusals, and the subscription
next_validated_event(handle)    one normalised, validated, de-duplicated, ordered, recorded event
FeedHandle                      the live subscription, its LRU, its per-symbol high-water marks
FeedHandle.close()              the RELEASE: the mds unsubscribe, then the local one (Req 17.8)
mds_command(action, ex, sym)    one ``mds:commands`` message - the shape, spelled once
publish_unsubscribe(redis, ...) the ONE unsubscribe command, callable without the handle
MarketEvent                     what an accepted event is, in exact ``Decimal``
FEED_STATE_*, FEED_TRANSPORT_*  the two vocabularies stored on ``paper_sessions``
admit_execution(session_row)    THE feed gate ``paper_simulator`` must call (task 25; see below)
ExecutionAdmission              the token that gate mints, unforgeable outside this module
FeedNotHealthy                  what it raises instead
backoff_delay_seconds(attempt)  1, 2, 4, 8, 16, 30, 30, ... - jitter-free, so a replay repeats it

WHAT THIS MODULE DOES NOT CONTAIN
---------------------------------
Every one of these already exists, and a second copy would be a second answer:

* **The source decision.** ``backend/market_data_latency.choose_market_data_source`` is the
  rule, correctness floor and all, and :func:`open_feed` calls it. There is no second selection
  rule here, no second floor, and no threshold. ``FLOOR_NOT_MEASURED`` and
  ``FLOOR_METRICS_MISSING`` fail that floor *inside* ``evaluate_correctness_floor``, before the
  25 ms p99 comparison is reachable, so an unmeasured source cannot be selected - which is
  exactly Requirement 14.4's refusal, obtained without this module knowing what a p99 is.
* **The exchange feed.** ``mds/main.py`` owns the CCXT connection, the ``watch_ohlcv`` stream
  and the ``fetch_ohlcv`` REST fallback, and publishes both to ``mds:data:{exchange}:{symbol}``.
  :func:`open_feed` does the publish-then-subscribe handshake
  ``data_seeking_engine.DataEngine.stream_live_ohlcv`` already established against that service.
  No ``fetch_ohlcv`` loop, no second websocket, no third market-data path (Requirement 14.2).
* **Normalisation.** ``market_data_contract.normalise_candle`` reads the payload into
  ``(open_time, [o, h, l, c, v], is_closed)``. It was extracted from that module's own
  ``ClosedBarIngest._parse`` by this task so both callers read one shape rule.
* **Validation.** ``market_data_validation.StructuralValidator`` and
  ``CandleIntegrityValidator`` - see "THE VALIDATOR IS DATAFRAME-SHAPED" below.
* **The DEV_MODE mock detector.** ``asset_universe.markets_are_mocked``, promoted from
  ``_markets_are_mocked`` by this task (the old name is retained as an alias). One
  implementation, detecting by the ``__name__`` of the closure
  ``connection_engine._apply_mock_interface`` binds.
* **Storage.** ``paper_repository`` owns every statement. ``paper_market_events`` and the three
  ``paper_sessions`` feed columns are reached through the functions it gained for this task.

WHY A SESSION REFUSES TO START MORE OFTEN THAN IT STARTS
--------------------------------------------------------
:func:`measurement_for` returns an **unmeasured** ``SourceMeasurement`` for any candidate the
caller has no recorded measurement for, and an unmeasured candidate fails the correctness floor.
With no measurement for either candidate the decision is ``BLOCKED`` and :func:`open_feed`
raises. That is not a defect and it is not a placeholder: Requirement 14.4 says a session whose
market-data source is unmeasured "SHALL refuse to start and SHALL report a market-data-unavailable
condition, rather than starting on an unvalidated feed", and
``market_data_latency``'s own docstring says the same in more words - "a decision document
reporting fabricated latencies is worse than one reporting that nothing was measured".

So the measurement is an **input**, supplied by the caller, and this module will not invent one.
The measuring harness is ``tests/perf/test_market_data_latency.py``; whatever records its verdict
for a deployment is what a session-start path passes to ``measurements=``. A caller that passes
nothing gets a refusal that names ``FLOOR_ADMITTED_NOTHING`` and quotes the floor's own reason,
which is a true statement about that deployment.

THE VALIDATOR IS DATAFRAME-SHAPED, AND THAT IS RECORDED RATHER THAN WORKED AROUND
---------------------------------------------------------------------------------
``market_data_validation`` has no single-candle entry point. Its public surface is
``get_validator()`` (an ``async`` four-step pipeline over a frame) and
``validate_market_data_strict(df, symbol, timeframe, ...)`` - both ``DataFrame``-shaped. So
:func:`_validate_candle` builds a **one-row frame** and runs the two validators that
``MarketDataValidator.validate`` runs as its steps 1 and 2:

    ``StructuralValidator.validate``      required columns, a timestamp index, first-wins on
                                          duplicate timestamps
    ``CandleIntegrityValidator.validate`` ``low <= min(o, c, h)``, ``high >= max(o, c, low)``,
                                          non-negative volume, strictly positive prices

Steps 3 and 4 - ``OutlierDetector`` (a z-score) and ``GapHandler`` (a bar-interval gap) - are
**window-scale** analyses and are deliberately not run on one candle: the standard deviation of a
single sample is undefined and a gap needs two bars, so running them would produce a verdict about
nothing. That is a limitation of reusing a batch validator on a stream, and it is written here
rather than hidden: a single arriving candle is checked for internal consistency and for positive
prices, and the outlier and gap properties of the *window* are not checked by this module at all.
No parallel validator is written; the checks that do run are that module's own code.

``market_data_validation`` also holds ``_generate_sample_data``, which imports ``random`` and
fabricates candles. It is reachable only from that module's two ``@router`` demo endpoints
(``POST /validate`` and ``GET /report/{symbol}``) and from nothing else. Nothing on this path calls
it, and nothing on this path calls ``MarketDataValidator.validate`` either - whose
``_attempt_fallback`` branch would call a registered fetcher. ``random`` is imported by
``market_data_validation`` at module scope and therefore lands in ``sys.modules`` behind any use of
the platform's market-data pipeline; what tasks.md's rule forbids and what this module honours is
that **no module under** ``backend_app/backend/paper/`` **imports** ``random`` and that no paper
code path reaches a randomised value (Requirements 8.13, 14.9, 18.1, 28.3).

MONEY AND PRICES ARE EXACT DECIMAL, FROM THE WIRE
-------------------------------------------------
The payload is decoded with ``json.loads(..., parse_float=Decimal)``, so a close price is a
``Decimal`` carrying exactly the digits the feed sent - never a binary float that has already lost
them. ``market_data_contract.normalise_candle`` hands the five values back unconverted for this
reason; ``ClosedBarIngest`` still converts to ``float`` for the frame the executors read, which is
correct for that consumer and would not be correct here (Requirement 18.1). The stored
``paper_market_events.payload`` carries them as decimal **strings**, because a JSON number could not
be read back exactly and a replay priced from an inexact close is not a replay (Requirement 15.4).

IS ``FALLBACK_REST`` TRADEABLE? NO - AND HERE IS WHY, IN FULL
-------------------------------------------------------------
**No.** :data:`TRADEABLE_FEED_STATES` holds exactly ``HEALTHY``, so :func:`admit_execution`
refuses ``FALLBACK_REST`` as flatly as it refuses ``DEGRADED``. Stated here rather than left to be
inferred from a tuple, because two requirements pull in different directions and the resolution has
to be legible:

* Requirement 14.6 says a session whose WebSocket path is unavailable "SHALL fall back to the REST
  path and SHALL record the fallback in the session's feed state". That is a *working, recorded*
  feed - not an outage - and the fallback is honoured in full: the subscription stays open, events
  are validated, recorded and counted, ``feed_transport`` reads ``REST`` and ``feed_state`` reads
  ``FALLBACK_REST``. Nothing about 14.6 is skipped.
* Requirement 18.15 forbids filling, valuing or reporting at a price that is not current, and
  Requirement 14.5 forbids treating a pre-disconnection price as current.
* tasks.md task 24.4 resolves the two explicitly: "``paper_simulator`` reads ``session.feed_state``
  **inside its transaction** and, when it is not ``'HEALTHY'``, neither accepts a market order nor
  fills a resting limit order."

Neither requirement is contradicted by that resolution. 14.6 requires the fallback to happen and
to be recorded; it does not say execution continues over it, and ``mds/main.py``'s REST fallback
polls on a bar cadence rather than streaming, so its most recent candle can be a whole bar old -
which is precisely the "stale price presented as current" 18.15 rules out. So the strict reading is
the safe one **and** the one tasks.md asks for, and it is applied.

The operational consequence is stated plainly rather than buried: a session that falls back to REST
keeps recording market data and stops trading. It is visible as ``feed_state = 'FALLBACK_REST'``,
counted by ``paper.feed.state``, and is a condition an operator should act on - not a silent
degradation. If the specification later wants REST to be tradeable under a freshness bound, that is
a change to :data:`TRADEABLE_FEED_STATES` plus a bar-age test, and it belongs in the spec before it
belongs in this tuple.

WHAT IS DELIBERATELY NOT DECIDED HERE
-------------------------------------
Whether an order fills. This module records the feed's health and mints the token
:func:`admit_execution` grants; ``paper_simulator`` (task 25) is what refuses the order. See
"THE SIMULATOR'S HALF OF REQUIREMENT 14.5" at :func:`admit_execution`, which states plainly what
is and is not in force until that task lands.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Tuple

from backend_app.backend.asset_universe import markets_are_mocked
from backend_app.backend.market_data_latency import (
    OUTCOME_BLOCKED,
    SOURCE_A,
    SOURCE_B,
    SourceDecision,
    SourceMeasurement,
    choose_market_data_source,
    unmeasured,
)
from backend_app.backend.metrics import guarded_collector
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper.paper_events import (
    PAPER_EVENT_SCHEMA_VERSION,
    PaperEvent,
)
from backend_app.backend.paper.errors import (
    PAPER_MARKET_DATA_UNAVAILABLE,
    PaperError,
)
from backend_app.backend.paper.paper_accounting import to_decimal

logger = logging.getLogger("PaperMarketFeed")


# ══════════════════════════════════════════════════════════════════════════
# THE TWO CHANNELS mds/main.py ALREADY OWNS (Requirement 14.2)
# ══════════════════════════════════════════════════════════════════════════

#: The channel ``mds/main.py::handle_commands`` listens on. A ``{'action': 'subscribe', ...}``
#: message wakes the service's stream for one ``(exchange, symbol)`` pair; an
#: ``{'action': 'unsubscribe', ...}`` message releases it (task 27.4, Requirement 17.8).
MDS_COMMAND_CHANNEL = "mds:commands"

#: The two ``action`` values ``handle_commands`` branches on, spelled once each. Both are the
#: strings that separate process compares against, so they are constants rather than inline
#: literals: a session that published ``"un-subscribe"`` would be silently ignored by a service
#: whose ``elif`` never matched, and the session would report a release that never happened.
MDS_ACTION_SUBSCRIBE = "subscribe"
MDS_ACTION_UNSUBSCRIBE = "unsubscribe"


def mds_command(action: str, exchange_id: Any, symbol: Any) -> str:
    """One ``mds:commands`` message, as the JSON text that channel carries.

    Exactly the three keys ``handle_commands`` reads - ``action``, ``exchange``, ``symbol`` - and
    no fourth, because a fourth is a field nobody consumes. The exchange id is lower-cased the way
    that function lower-cases it before keying its stream table, so the ``unsubscribe`` released
    here names the same ``(exchange, symbol)`` pair the ``subscribe`` woke.

    One builder for both actions, so there is exactly ONE place that knows the shape of this wire
    contract. :meth:`FeedHandle._subscribe` and :func:`publish_unsubscribe` are its two callers.
    """
    if action not in (MDS_ACTION_SUBSCRIBE, MDS_ACTION_UNSUBSCRIBE):
        raise ValueError(
            f"{action!r} is not an mds:commands action; handle_commands branches on "
            f"{MDS_ACTION_SUBSCRIBE!r} and {MDS_ACTION_UNSUBSCRIBE!r} and ignores anything else"
        )
    return json.dumps(
        {
            "action": action,
            "exchange": str(exchange_id).strip().lower(),
            "symbol": str(symbol).strip(),
        }
    )


async def publish_unsubscribe(
    redis: Any, *, exchange_id: Any, symbol: Any, session_id: Any = None
) -> bool:
    """``PUBLISH {'action': 'unsubscribe', ...}`` to ``mds:commands``. Requirement 17.8's release.

    THE ONLY UNSUBSCRIBE COMMAND IN THE CODEBASE, AND WHY IT IS A FUNCTION
    ---------------------------------------------------------------------
    Requirement 17.8 requires a stopped Paper_Session's market-data subscription RELEASED, and the
    subscription has two halves because :meth:`FeedHandle._subscribe` opened two: the command that
    woke ``mds``'s stream for the pair, and this process's own pubsub subscription to the data
    channel. :meth:`FeedHandle.close` performs both, in that order, and it does the first by calling
    this function.

    It is a module-level function rather than a second method because the two halves do not always
    live in the same place. The pubsub handle lives in the worker that opened it; the ``mds`` stream
    is process-independent. A stop served by a worker that does not hold the handle can therefore
    still release the ``mds`` half - which is the half that costs a separate service a running
    stream - and :func:`~paper_session_service.stop_session` reports the local half as outstanding
    rather than claiming a release it could not perform.

    Returns:
        ``True`` when the command was published. ``False`` when it was not, WITH the reason logged:
        a caller that has to report Requirement 17.8's "only after those steps have committed" needs
        to know, and a teardown that raised would mask the failure that caused the stop.
    """
    if redis is None:
        logger.warning(
            "[paper-feed] session %s has no Redis client to publish the mds unsubscribe for %s %s "
            "through; the mds stream for that pair is NOT released",
            session_id,
            exchange_id,
            symbol,
        )
        return False
    try:
        await redis.publish(
            MDS_COMMAND_CHANNEL,
            mds_command(MDS_ACTION_UNSUBSCRIBE, exchange_id, symbol),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - see the docstring: reported, never raised
        logger.warning(
            "[paper-feed] the mds unsubscribe for session %s (%s %s) was not published (%s); the "
            "mds stream for that pair is NOT released",
            session_id,
            exchange_id,
            symbol,
            exc,
        )
        return False
    return True


def mds_data_channel(exchange_id: Any, symbol: Any) -> str:
    """The channel ``mds/main.py::broadcast_ohlcv`` publishes that pair's candles to.

    Spelled once, here, because it is a wire contract shared with a separate process: the
    exchange id is lower-cased exactly as ``handle_commands`` lower-cases it before keying its
    stream table, so a caller passing ``"Binance"`` subscribes to the channel the service
    actually publishes to rather than to one nobody writes.
    """
    return f"mds:data:{str(exchange_id).strip().lower()}:{str(symbol).strip()}"


# ══════════════════════════════════════════════════════════════════════════
# THE TWO VOCABULARIES STORED ON paper_sessions
# ══════════════════════════════════════════════════════════════════════════

#: ``paper_sessions.feed_transport``. Observed from the ``transport`` field ``mds/main.py``'s
#: payload carries (Requirement 14.6) - not inferred, not defaulted.
FEED_TRANSPORT_WEBSOCKET = "WEBSOCKET"
FEED_TRANSPORT_REST = "REST"

#: What a payload carrying **no** ``transport`` field means.
#:
#: ``mds/main.py`` gained that field in this task, so a deployment running an older MDS publishes
#: payloads without it, and this feed must not crash on one. The question is what to record.
#: ``WEBSOCKET`` would be wrong: the REST fallback publishes to the same channel with an otherwise
#: identical payload, so "no transport field" is *precisely* the case where the two are
#: indistinguishable, and defaulting to WEBSOCKET would report a REST-polled session as a live
#: socket - the substitution Requirement 28.5 forbids ("SHALL NOT substitute a zero, a default or a
#: previous value presented as current"). So it is recorded as UNKNOWN, and
#: :data:`FEED_STATE_TRANSPORT_UNKNOWN` is the feed state that goes with it: a state that is not
#: ``HEALTHY``, so :func:`admit_execution` refuses, because a session whose delivery path cannot be
#: identified has not demonstrated the freshness a fill is priced on.
FEED_TRANSPORT_UNKNOWN = "UNKNOWN"

FEED_TRANSPORTS: Tuple[str, ...] = (
    FEED_TRANSPORT_WEBSOCKET,
    FEED_TRANSPORT_REST,
    FEED_TRANSPORT_UNKNOWN,
)

#: ``paper_sessions.feed_state``. 009 declares the column ``TEXT NOT NULL DEFAULT 'PENDING'`` and
#: places no CHECK on it, so this tuple is the vocabulary and there is no database guard behind it;
#: :func:`_transition_feed_state` refuses a value outside it so the column cannot drift.
#:
#: ``PENDING``   009's default: a session row exists, no validated event has arrived yet.
#: ``HEALTHY``   the last event arrived over the WebSocket path, was validated and was recorded.
#: ``FALLBACK_REST``  the same, over ``mds/main.py``'s REST fallback (Requirement 14.6).
#: ``TRANSPORT_UNKNOWN``  the same, over a payload that did not say which path it came from.
#: ``DEGRADED``  the subscription dropped (Requirement 14.5). Set before the first reconnection
#:               attempt and cleared only by an event that passes validation after it.
FEED_STATE_PENDING = "PENDING"
FEED_STATE_HEALTHY = "HEALTHY"
FEED_STATE_FALLBACK_REST = "FALLBACK_REST"
FEED_STATE_TRANSPORT_UNKNOWN = "TRANSPORT_UNKNOWN"
FEED_STATE_DEGRADED = "DEGRADED"

FEED_STATES: Tuple[str, ...] = (
    FEED_STATE_PENDING,
    FEED_STATE_HEALTHY,
    FEED_STATE_FALLBACK_REST,
    FEED_STATE_TRANSPORT_UNKNOWN,
    FEED_STATE_DEGRADED,
)

#: The feed states in which an order may be accepted or a resting order filled. **Exactly one.**
#:
#: This tuple, and not a chain of comparisons at each call site, is the whole admission rule, and
#: :func:`admit_execution` is the one callable that reads it. Written as a whitelist rather than as
#: ``!= DEGRADED`` deliberately: a blacklist admits every state added later by default, and the
#: state added later is exactly the one nobody has thought about yet.
#:
#: See "IS ``FALLBACK_REST`` TRADEABLE?" in the module docstring for why the REST fallback is
#: **not** in this tuple even though Requirement 14.6 treats it as a working feed.
TRADEABLE_FEED_STATES: Tuple[str, ...] = (FEED_STATE_HEALTHY,)

#: The feed state one transport implies once an event over it has been validated. A mapping rather
#: than a chain of ``if``s so the three cases are one readable table and none can be forgotten.
FEED_STATE_FOR_TRANSPORT: Dict[str, str] = {
    FEED_TRANSPORT_WEBSOCKET: FEED_STATE_HEALTHY,
    FEED_TRANSPORT_REST: FEED_STATE_FALLBACK_REST,
    FEED_TRANSPORT_UNKNOWN: FEED_STATE_TRANSPORT_UNKNOWN,
}

#: The Paper_Channel record Requirement 14.5 requires on a dropped subscription, and the code it
#: carries in its payload.
#:
#: **Task 26.1 reconciled both of these rather than leaving a second spelling.** ``paper_events``
#: now owns the sixteen-type enum and the envelope version, so the event type is DERIVED from
#: :attr:`~paper_events.PaperEvent.ERROR` and the version is the imported object itself. Both names
#: are kept exactly as they were, because this module's callers and
#: ``tests/test_paper_market_feed_events.py`` use them - what changed is that neither is a literal
#: this file could get wrong on its own. ``FEED_DISCONNECTED_CODE`` stays local: it is one member of
#: the open ``paper_error`` code set (``paper_events.PAPER_ERROR_CODES``) and it is *this* module's
#: condition, not the channel's vocabulary.
FEED_DISCONNECTED_CODE = "FEED_DISCONNECTED"
PAPER_ERROR_EVENT_TYPE = PaperEvent.ERROR.value

#: ``StrategyAuditAction`` member name for Requirement 14.8's recorded refusal. Resolved by name at
#: call time, like ``marketplace/settlement_service._write_audit`` does, so this module does not
#: import ``core.audit_trail`` at module scope.
AUDIT_ACTION_REFUSED_MOCK_INTERFACE = "PAPER_FEED_REFUSED_MOCK_INTERFACE"


# ══════════════════════════════════════════════════════════════════════════
# THE BOUNDED, JITTER-FREE BACKOFF (Requirement 14.5)
# ══════════════════════════════════════════════════════════════════════════

#: The reconnection delays, in seconds, in order. The last entry is the cap: attempt 6 and every
#: attempt after it waits 30 s.
#:
#: **Jitter-free on purpose.** Jitter is the right answer when many clients reconnect to one
#: service at once, and it is the wrong answer here: Requirement 15.4 requires a session replayed
#: against the same recorded events to produce the same order states and fills, and a randomised
#: delay would move the reconnection points between the run and the replay. It would also be a
#: ``random`` draw inside ``backend_app/backend/paper/``, which Requirements 8.13 and 18.1 forbid
#: outright. The thundering-herd risk is instead bounded by the 30 s cap and by there being one
#: subscription per session rather than per symbol.
BACKOFF_SECONDS: Tuple[Decimal, ...] = (
    Decimal("1"),
    Decimal("2"),
    Decimal("4"),
    Decimal("8"),
    Decimal("16"),
    Decimal("30"),
)


def backoff_delay_seconds(attempt: int) -> Decimal:
    """The delay before reconnection attempt ``attempt``. 1-based; capped at 30 s.

    ``backoff_delay_seconds(1)`` is 1 s, ``(2)`` is 2 s, ... ``(6)`` is 30 s and every attempt
    after it is 30 s. Exact and deterministic: two runs of the same session sleep the same
    sequence, which is what makes the reconnection points reproducible under Requirement 15.4.

    Returns a ``Decimal`` for exactness at the boundary; :meth:`FeedHandle.reconnect` converts it
    to ``float`` for ``asyncio.sleep``, which is the only place a float appears on this path and is
    not a money or price computation.
    """
    index = int(attempt)
    if index < 1:
        raise ValueError(f"attempt is 1-based; got {attempt!r}")
    return BACKOFF_SECONDS[min(index, len(BACKOFF_SECONDS)) - 1]


# ══════════════════════════════════════════════════════════════════════════
# THE LRU BOUND (Requirement 14.7)
# ══════════════════════════════════════════════════════════════════════════

#: How many ``source_event_id`` values one session's hot path remembers.
#:
#: ``design.md``: "bounded LRU, 10 000 entries". Bounded because a session that runs for a month
#: would otherwise hold every event identity it ever saw, and Requirement 27.5's "SHALL bound the
#: number of retained in-memory events" applies to the server side for the same reason.
#:
#: It is a **cache**, not the arbiter. ``uq_paper_market_event UNIQUE (session_id,
#: source_event_id)`` is the durable guarantee, and an LRU miss - an evicted entry, a restarted
#: worker, a second worker on one session - is therefore *safe*: the insert is refused by the
#: database, :func:`next_validated_event` treats it as the no-op Requirement 14.7 asks for, and no
#: second row is written. That is why a miss is a metric and not an incident.
SEEN_EVENT_ID_LIMIT = 10_000


# ══════════════════════════════════════════════════════════════════════════
# REFUSALS
# ══════════════════════════════════════════════════════════════════════════


class PaperMarketDataUnavailable(PaperError):
    """409 ``PAPER_MARKET_DATA_UNAVAILABLE`` - the one refusal :func:`open_feed` makes.

    Both of Requirement 14's start-time refusals carry this code, because both are the same fact
    about the session: there is no validated market data to run it on.

    * The correctness floor admitted nothing, or the selected source is unmeasured
      (Requirement 14.4). ``rule`` is ``decision.rule`` and ``reason`` is ``decision.reason``,
      verbatim from ``market_data_latency`` - this module adds no explanation of its own to a
      verdict it did not reach.
    * The resolved exchange connection is served by ``connection_engine._apply_mock_interface``
      (Requirement 14.8). ``rule`` is :data:`RULE_MOCK_INTERFACE`.

    The status is **pinned**: ``PAPER_MARKET_DATA_UNAVAILABLE`` is absent from
    ``ALLOWED_HTTP_STATUS_FOR_CODE``, so ``StructuredError.__init__`` refuses any status other than
    the catalogue's 409 - the same pinning ``PAPER_READ_FAILED`` has and for the same reason: no
    call site can turn "there is no validated market data" into a 200.

    ``rule`` and ``reason`` travel in ``details``, which the handler puts through
    ``redact_details``; both are this module's and ``market_data_latency``'s own strings, carrying
    no identifier, path or credential. The public sentence stays the catalogue's.
    """

    #: :data:`details`' ``rule`` value for Requirement 14.8's refusal. Not a
    #: ``market_data_latency`` rule name, because the mock interface is not a floor verdict - the
    #: floor is about measured behaviour, and this is about which code is answering.
    RULE_MOCK_INTERFACE = "DEV_MODE_MOCK_INTERFACE"

    def __init__(
        self,
        rule: Any,
        reason: Any = None,
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        merged: Dict[str, Any] = {"rule": str(rule)}
        if reason is not None:
            merged["reason"] = str(reason)
        if details:
            merged.update(dict(details))
        self.rule = str(rule)
        self.reason = None if reason is None else str(reason)
        super().__init__(PAPER_MARKET_DATA_UNAVAILABLE, details=merged)


class PaperFeedError(Exception):
    """Base class for the feed's internal, non-caller-facing conditions."""


class PaperFeedDisconnected(PaperFeedError):
    """The market-data subscription dropped (Requirement 14.5).

    Internal: it never reaches an HTTP response. :func:`next_validated_event` catches it, marks the
    session ``DEGRADED``, writes the ``paper_error`` record and performs one bounded-backoff
    reconnection attempt. A caller that wants to know it happened reads
    :attr:`FeedHandle.feed_state` or the metric.
    """


class FeedNotHealthy(PaperFeedError):
    """:func:`admit_execution` refused: the session's feed state is not ``HEALTHY``.

    Carries the state it read, so the refusal names the condition rather than describing it.
    """

    def __init__(self, session_id: Any, feed_state: Any) -> None:
        self.session_id = str(session_id)
        self.feed_state = str(feed_state)
        super().__init__(
            f"session {self.session_id!r} has feed_state {self.feed_state!r}, not "
            f"{FEED_STATE_HEALTHY!r}, so no order may be accepted and no resting order filled: "
            f"a price observed before the feed stopped being current is not a current price "
            f"(Requirements 14.5, 18.15)"
        )


# ══════════════════════════════════════════════════════════════════════════
# THE SESSION FACTS A FEED NEEDS
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PaperFeedConfig:
    """The five facts, and nothing else. Frozen, so a running feed cannot be repointed.

    ``exchange_id``, ``symbol`` and ``timeframe`` are the three Requirement 14.3 requires recorded
    per session, and they are the three the ``mds`` subscription is keyed on. ``session_id`` and
    ``user_id`` are what scopes every statement this module issues (Requirements 21.2, 21.5) - and
    they are required rather than optional for that reason: there is no unscoped read here and no
    default tenant to fall back on.

    Deliberately **not** here: the session's frozen fee and slippage configuration, its capital, or
    its strategy. The feed prices nothing and executes nothing, so carrying them would be carrying
    what this module has no use for.
    """

    session_id: str
    user_id: str
    exchange_id: str
    symbol: str
    timeframe: str

    def __post_init__(self) -> None:
        for name in ("session_id", "user_id", "exchange_id", "symbol", "timeframe"):
            value = getattr(self, name)
            if value is None or not str(value).strip():
                raise ValueError(f"PaperFeedConfig.{name} is required")

    @property
    def channel(self) -> str:
        """The ``mds:data:*`` channel this session's candles arrive on."""
        return mds_data_channel(self.exchange_id, self.symbol)

    @property
    def normalised_exchange_id(self) -> str:
        """The exchange id as ``mds/main.py`` keys it: stripped and lower-cased."""
        return str(self.exchange_id).strip().lower()


@dataclass(frozen=True)
class MarketEvent:
    """One accepted market-data event: normalised, validated, de-duplicated, ordered, recorded.

    Every price and the volume are exact ``Decimal`` - decoded from the feed's JSON with
    ``parse_float=Decimal`` and never passed through a binary float (Requirement 18.1). A consumer
    that prices a fill from :attr:`close` is therefore pricing it from the digits the exchange
    published.

    :attr:`payload` is the JSON-safe mapping that was written to ``paper_market_events.payload``,
    with the five values as decimal strings - which is what makes the row the replay input
    Requirement 15.5 asks for.
    """

    session_id: str
    user_id: str
    sequence: int
    source_event_id: str
    exchange_id: str
    symbol: str
    timeframe: str
    #: The market instant the candle opened at, tz-aware UTC.
    event_timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    #: One of :data:`FEED_TRANSPORTS`.
    transport: str
    #: Processing instant minus :attr:`event_timestamp`, in milliseconds (Requirement 14.10).
    latency_ms: Decimal
    #: The instant this process finished validating the event, tz-aware UTC.
    received_at: datetime
    payload: Mapping[str, Any]
    #: The feed state this event put the session into.
    feed_state: str


# ══════════════════════════════════════════════════════════════════════════
# 24.1 - SOURCE SELECTION, THROUGH THE EXISTING RULE (Requirements 14.1, 14.3, 14.4)
# ══════════════════════════════════════════════════════════════════════════

#: Whether each candidate carries a validation layer. Not a measurement - it is a property of the
#: code path, and Requirement 19.12 makes it the tie-break when the p99 margin is not met. Taken
#: from ``market_data_latency``'s own descriptions of ``SOURCE_A`` (the direct CCXT stream, no
#: closed-bar gate, no de-duplication, no OHLC validation) and ``SOURCE_B`` (the validated OHLCV
#: pipeline). Stated here rather than guessed at each call site.
HAS_VALIDATION_LAYER: Dict[str, bool] = {SOURCE_A: False, SOURCE_B: True}

#: The reason an unmeasured candidate carries into the floor's ``rejection_detail``. One sentence,
#: naming what would have to happen for the answer to change.
UNMEASURED_REASON = (
    "No latency or correctness measurement was supplied for this candidate, so it has "
    "demonstrated none of the Requirement 19.11 admission criteria. Run the market-data "
    "measurement harness for this deployment and pass its result to open_feed(measurements=...)."
)


def measurement_for(
    source: str,
    *,
    measurements: Optional[Mapping[str, SourceMeasurement]] = None,
) -> SourceMeasurement:
    """The recorded ``SourceMeasurement`` for one candidate, or an explicitly unmeasured one.

    A **thin adapter**, and nothing more. It does not measure, does not estimate, does not carry a
    default figure and does not fill a missing field with a zero. Its whole job is to answer the
    question ``choose_market_data_source`` asks - "what was measured for this candidate?" - with
    either the caller's recorded answer or ``market_data_latency.unmeasured(...)``, which is that
    module's own representation of "nobody measured this".

    That distinction is the one Requirement 14.4 turns on. An unmeasured candidate fails
    ``evaluate_correctness_floor`` with ``NOT_MEASURED`` **before** any latency is read, so it
    cannot be selected at any latency, and a deployment with no measurements is refused rather than
    started on an unvalidated feed. Substituting a plausible measurement here would defeat the
    entire mechanism from the one place nothing downstream could detect it.

    Args:
        source: ``market_data_latency.SOURCE_A`` or ``SOURCE_B``. Anything else is a programming
            error and is refused, because ``HAS_VALIDATION_LAYER`` has no entry for it and
            inventing one would state a property of a code path this module has never seen.
        measurements: what the caller recorded, keyed by source identity. ``None`` and an empty
            mapping are the same thing and both mean "nothing was measured".

    Raises:
        ValueError: ``source`` is not a known candidate, or the mapping's entry for it is not a
            ``SourceMeasurement``, or it is a measurement OF A DIFFERENT SOURCE. The last is
            refused rather than relabelled: attributing one candidate's figures to another would
            admit a source on evidence that is not about it.
    """
    if source not in HAS_VALIDATION_LAYER:
        raise ValueError(
            f"{source!r} is not a market-data candidate this rule knows about; expected one of "
            f"{sorted(HAS_VALIDATION_LAYER)}"
        )
    has_validation_layer = HAS_VALIDATION_LAYER[source]

    recorded = None if not measurements else measurements.get(source)
    if recorded is None:
        return unmeasured(
            source, has_validation_layer=has_validation_layer, reason=UNMEASURED_REASON
        )
    if not isinstance(recorded, SourceMeasurement):
        raise ValueError(
            f"measurements[{source!r}] is a {type(recorded).__name__}; a "
            f"market_data_latency.SourceMeasurement is the only shape the selection rule reads"
        )
    if str(recorded.source) != str(source):
        raise ValueError(
            f"measurements[{source!r}] carries source={recorded.source!r}; a measurement of one "
            f"candidate is not evidence about another and is refused rather than relabelled"
        )
    return recorded


def select_market_data_source(
    measurements: Optional[Mapping[str, SourceMeasurement]] = None,
) -> SourceDecision:
    """``choose_market_data_source`` over both candidates' measurements. No second rule.

    Separated from :func:`open_feed` only so the decision is testable without a Redis handle and so
    a session-start path can show a would-be refusal before it creates a session row. It adds
    nothing: the returned :class:`~market_data_latency.SourceDecision` is that function's, with its
    rule, its reason, both floor verdicts and both measurements intact (Requirement 19.13).
    """
    return choose_market_data_source(
        measurement_for(SOURCE_A, measurements=measurements),
        measurement_for(SOURCE_B, measurements=measurements),
    )


async def _audit_mock_interface_refusal(config: PaperFeedConfig) -> None:
    """Write Requirement 14.8's ``PAPER_FEED_REFUSED_MOCK_INTERFACE`` record.

    Through the existing ``core.audit_trail.StrategyAuditLogger``, which already carries the enum
    member - no second audit facility and no new member. Imported at call time, the way
    ``marketplace/settlement_service._write_audit`` and ``marketplace/expiry_sweep`` do, so this
    module does not pull ``core.audit_trail`` (and its Redis handle) into every import of the paper
    package.

    ``log`` rather than ``record_or_raise``: the refusal itself is the guarantee - the session does
    not start whether or not the record lands - and a Redis outage must not turn "we refused to run
    on a mock feed" into a 500 that hides why. A failed write is logged by
    ``StrategyAuditLogger.log`` at warning level, so the gap is visible where the record would have
    been. The refusal is *also* logged here at error level, unconditionally, so Requirement 26.5's
    "SHALL log an error for every failure on a correctness-critical path" holds even if the audit
    facility is entirely absent.

    The metadata carries the exchange id, the symbol, the timeframe and the session id. No
    credential, no market map and no other user's identifier: the mock interface's contents are not
    evidence anybody needs, only the fact that it was installed.
    """
    logger.error(
        "[paper-feed] refused session %s: the resolved connection for %s is served by the "
        "DEV_MODE mock interface (connection_engine._apply_mock_interface), so its prices are a "
        "hardcoded constant and not a market (Requirement 14.8)",
        config.session_id,
        config.exchange_id,
    )
    try:
        from backend_app.core.audit_trail import (
            StrategyAuditAction,
            get_strategy_audit_logger,
        )
    except Exception as exc:  # noqa: BLE001 - a defined outcome, and already logged above
        logger.warning(
            "[paper-feed] the audit facility is unavailable, so the "
            "PAPER_FEED_REFUSED_MOCK_INTERFACE record for session %s was not written: %s",
            config.session_id,
            exc,
        )
        return

    action = getattr(StrategyAuditAction, AUDIT_ACTION_REFUSED_MOCK_INTERFACE, None)
    if action is None:  # pragma: no cover - the member exists; this is the rename guard
        logger.warning(
            "[paper-feed] core.audit_trail.StrategyAuditAction has no %s member, so the "
            "Requirement 14.8 refusal record for session %s was not written",
            AUDIT_ACTION_REFUSED_MOCK_INTERFACE,
            config.session_id,
        )
        return

    await get_strategy_audit_logger().log(
        action,
        actor_id=config.user_id,
        resource_type="paper_session",
        resource_id=config.session_id,
        reason=(
            "The Paper_Session was refused because the resolved market-data connection is "
            "served by the DEV_MODE mock interface, whose market map and prices are hardcoded "
            "constants. Paper trading never runs on invented prices."
        ),
        metadata={
            "exchange_id": config.exchange_id,
            "symbol": config.symbol,
            "timeframe": config.timeframe,
            "detector": "asset_universe.markets_are_mocked",
        },
    )


async def open_feed(
    config: PaperFeedConfig,
    *,
    exchange: Any,
    redis: Any = None,
    supabase: Any = None,
    measurements: Optional[Mapping[str, SourceMeasurement]] = None,
) -> "FeedHandle":
    """Select the market-data source, refuse if it cannot be trusted, and subscribe.

    ``design.md`` -> ``ASYNC FUNCTION open_feed``, in its order, and **the order is the contract**:

    1. :func:`select_market_data_source`, which is ``choose_market_data_source`` (Requirement 14.1).
    2. ``outcome == OUTCOME_BLOCKED`` raises :class:`PaperMarketDataUnavailable` (Requirement 14.4).
    3. A mock-served connection raises the same code plus an audit record (Requirement 14.8).
    4. Only then: publish ``subscribe`` to ``mds:commands`` and subscribe to the data channel
       (Requirement 14.2).
    5. Record ``paper_sessions.market_data_source`` (Requirement 14.3).

    Steps 4 and 5 are unreachable from 2 and 3, which is what makes "the session stays ``CREATED``
    and no subscription is opened" a property of the code rather than of the caller's exception
    handling: nothing is published, no channel is subscribed and no session column is written
    before both refusals have passed. The ``redis`` handle is not even resolved until step 4, so a
    refusal cannot touch the pipeline by way of an import side effect.

    Args:
        config: the session's five facts.
        exchange: the resolved exchange connection for ``config.exchange_id``, or ``None`` when the
            session has none. **Required keyword with no default**, deliberately: Requirement 14.8's
            refusal is a property of the object that was resolved, so a caller must state what it
            resolved rather than being allowed to omit it and silently skip the check. ``None`` is a
            legitimate answer - Requirement 15.3 lets a session start with no exchange credentials
            and therefore no connection - and ``None`` is not mocked.
        redis: the Redis handle. Defaults to the platform's ``core.cache.redis_manager``, which is
            the handle ``mds/main.py`` and ``data_seeking_engine`` already speak through.
        supabase: the caller's RLS-scoped Persistence_Layer handle. When given,
            ``market_data_source`` is written to the session row here and the feed state is
            maintained on it thereafter. When ``None`` the handle keeps its state in process only,
            which is what a property test wants and what a production caller must not do -
            Requirement 14.3 requires the source recorded per session.
        measurements: the recorded measurements, keyed by source identity. See
            :func:`measurement_for` for why this module will not invent one.

    Raises:
        PaperMarketDataUnavailable: 409, for either refusal.
        PaperError: 503 ``PAPER_PERSISTENCE_UNAVAILABLE``, from ``paper_repository`` when
            ``supabase`` is given and 009 is not applied. Not caught here: a session that cannot
            record its market-data source has not satisfied Requirement 14.3, and starting it
            anyway would make that requirement read as true while being false.
    """
    if not isinstance(config, PaperFeedConfig):
        raise ValueError(
            f"open_feed takes a PaperFeedConfig, got {type(config).__name__}; the five session "
            f"facts are required and none of them has a default"
        )

    # 1 + 2. The existing rule, and Requirement 14.4's refusal. Nothing has been touched yet.
    decision = select_market_data_source(measurements)
    if decision.outcome == OUTCOME_BLOCKED:
        logger.error(
            "[paper-feed] refused session %s: %s - %s",
            config.session_id,
            decision.rule,
            decision.reason,
        )
        _record_feed_state_metric(FEED_STATE_PENDING, reason=decision.rule)
        raise PaperMarketDataUnavailable(decision.rule, decision.reason)

    # 3. Requirement 14.8. Still nothing published and nothing written.
    if markets_are_mocked(exchange):
        await _audit_mock_interface_refusal(config)
        _record_feed_state_metric(
            FEED_STATE_PENDING, reason=PaperMarketDataUnavailable.RULE_MOCK_INTERFACE
        )
        raise PaperMarketDataUnavailable(
            PaperMarketDataUnavailable.RULE_MOCK_INTERFACE,
            "The resolved market-data connection for this exchange is served by the DEV_MODE "
            "mock interface, whose market map and prices are hardcoded constants rather than "
            "measurements. Paper trading never runs on invented prices.",
            details={"exchange_id": config.exchange_id},
        )

    selected = str(decision.selected)
    handle = FeedHandle(
        config=config,
        decision=decision,
        market_data_source=selected,
        redis=_resolve_redis(redis),
        supabase=supabase,
    )

    # 4. The existing pipeline (Requirement 14.2).
    await handle._subscribe()

    # 5. Requirement 14.3, and the sequence this session's log continues from.
    if supabase is not None:
        repo.update_session_feed(
            supabase,
            user_id=config.user_id,
            session_id=config.session_id,
            market_data_source=selected,
            feed_transport=None,
            feed_state=None,
        )
        handle.sequence = (
            repo.next_market_event_sequence(
                supabase, config.user_id, config.session_id
            )
            - 1
        )

    logger.info(
        "[paper-feed] session %s subscribed to %s over source %s (%s)",
        config.session_id,
        handle.channel,
        selected,
        decision.rule,
    )
    return handle


def _resolve_redis(redis: Any) -> Any:
    """The Redis handle, or the platform's singleton. Never a stub and never ``None``.

    Imported at call time rather than at module scope so importing this module does not construct
    a Redis manager - the same reason ``data_seeking_engine.stream_live_ohlcv`` imports it inside
    the generator. A handle that cannot be resolved is an error, not a degradation: a feed with no
    transport would silently deliver nothing while the session read as running.
    """
    if redis is not None:
        return redis
    from backend_app.core.cache import redis_manager

    handle = getattr(redis_manager, "redis", redis_manager)
    if handle is None:
        raise PaperFeedError(
            "no Redis handle is available, so the mds subscription cannot be opened; a feed "
            "with no transport would deliver nothing while the session read as running"
        )
    return handle


# ══════════════════════════════════════════════════════════════════════════
# INSTRUMENTATION (Requirements 14.10, 26.6)
# ══════════════════════════════════════════════════════════════════════════


#: The six ``paper.feed.*`` recorders below all reach the collector through
#: :func:`backend_app.backend.metrics.guarded_collector`, which yields ``None`` when it is
#: unavailable. Each of them then records nothing and returns: instrumentation must never be what
#: drops a market event, so the event is still processed and recorded either way.


def _record_feed_state_metric(state: str, *, reason: str = "") -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_feed_state(state, reason=reason)


def _record_invalid(symbol: str, reason: str) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_feed_invalid(symbol=symbol, reason=reason)


def _record_duplicate(symbol: str, arbiter: str) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_feed_duplicate(symbol=symbol, arbiter=arbiter)


def _record_out_of_order(symbol: str) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_feed_out_of_order(symbol=symbol)


def _record_accepted(symbol: str, transport: str, timeframe: str, latency_ms: Decimal) -> None:
    collector = guarded_collector()
    if collector is None:
        return
    collector.record_paper_feed_event(symbol=symbol, transport=transport)
    collector.record_paper_feed_latency(latency_ms, symbol=symbol, timeframe=timeframe)


def _record_reconnect(outcome: str) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_feed_reconnect(outcome=outcome)


# ══════════════════════════════════════════════════════════════════════════
# THE VALUE HELPERS - EXACT, OR ABSENT
# ══════════════════════════════════════════════════════════════════════════

#: The five OHLCV names, in the order ``market_data_contract.normalise_candle`` returns them.
OHLCV_NAMES: Tuple[str, ...] = ("open", "high", "low", "close", "volume")

#: Why one event was dropped. Stable strings, because they are metric label values and a runbook
#: matches on a code rather than on prose.
INVALID_UNDECODABLE = "UNDECODABLE_PAYLOAD"
INVALID_NOT_A_MAPPING = "PAYLOAD_NOT_AN_OBJECT"
INVALID_WRONG_MARKET = "WRONG_EXCHANGE_SYMBOL_OR_TIMEFRAME"
INVALID_UNREADABLE_SHAPE = "UNREADABLE_CANDLE_SHAPE"
INVALID_INEXACT_VALUE = "VALUE_NOT_EXACT_DECIMAL"
INVALID_UNREADABLE_TIMESTAMP = "UNREADABLE_TIMESTAMP"
INVALID_VALIDATOR_REJECTED = "VALIDATOR_REJECTED"

#: Which arbiter caught a duplicate. See ``MetricsCollector.record_paper_feed_duplicate``.
DUPLICATE_ARBITER_LRU = "LRU"
DUPLICATE_ARBITER_UNIQUE_INDEX = "UNIQUE_INDEX"


def _exact_decimal(value: Any) -> Optional[Decimal]:
    """``value`` as an exact ``Decimal``, or ``None`` when it is not one.

    Routed through ``paper_accounting.to_decimal``, which **refuses a float outright**
    (Requirement 18.1). A ``float`` reaching here means the payload was decoded without
    ``parse_float=Decimal`` and the digits the exchange sent are already gone; the event is dropped
    as invalid rather than stored at whatever ``str(0.07)`` produces. ``None`` rather than an
    exception, because the caller classifies an unreadable value as invalid and counts it.
    """
    try:
        return to_decimal(value, "market data value")
    except Exception:  # noqa: BLE001 - classified by the caller as an invalid event
        return None


def canonical_number(value: Decimal) -> str:
    """One ``Decimal`` rendered as the canonical text the event identity is built from.

    ``format(value.normalize(), 'f')``: trailing fractional zeros removed, never exponential.
    ``60000``, ``60000.0``, ``6E+4`` and ``60000.00`` therefore all render ``'60000'``, and
    ``1.50`` renders ``'1.5'``.

    That canonicalisation is what makes :func:`source_event_id` stable "for a republished candle
    and different for a revised one" (Requirement 14.7). ``mds/main.py`` re-serialises the value
    CCXT handed it on every socket update, and a feed that sends ``60000`` once and ``60000.0``
    the next time has republished the *same* candle - a raw string hash would call those two
    different events and store the candle twice. A genuinely revised close is a different number
    and renders differently, so the distinction the requirement needs survives.
    """
    return format(value.normalize(), "f")


def _epoch_ms(value: Any) -> Optional[int]:
    """A candle timestamp as integer epoch milliseconds, or ``None``.

    ``mds/main.py`` publishes CCXT's ``latest[0]``, which is epoch milliseconds as an integer. It
    arrives as an ``int`` from ``json.loads``; ``parse_float=Decimal`` turns a value written as
    ``1718452800000.0`` into a ``Decimal`` instead, so an integral ``Decimal`` is accepted and
    converted here. A fractional millisecond is refused rather than truncated: truncating would
    make two distinct instants share one identity.

    This is a JSON type coercion and not market-data normalisation - the instant itself is
    interpreted by ``market_data_contract.to_utc_naive``, which is the single reading of "when" on
    this path and is reached through ``normalise_candle``.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, Decimal):
        try:
            if value != value.to_integral_value():
                return None
            return int(value)
        except (InvalidOperation, ValueError, OverflowError):
            return None
    if isinstance(value, str):
        try:
            return _epoch_ms(Decimal(value.strip()))
        except (InvalidOperation, ValueError):
            return None
    return None


def source_event_id(
    exchange_id: Any,
    symbol: Any,
    timeframe: Any,
    timestamp_ms: Any,
    close: Decimal,
    volume: Decimal,
) -> str:
    """Requirement 14.7's event identity: the design's sha256, over the design's six fields.

    ``sha256(f"{exchange}|{symbol}|{timeframe}|{timestamp}|{close}|{volume}")``, because the OHLCV
    payload ``mds/main.py`` publishes carries no id of its own and the platform therefore has to
    derive one.

    Why these six and not the whole candle: they are the fields that distinguish a *republication*
    from a *revision*. A forming candle republished on the next socket update carries the same open
    time and, if nothing traded, the same close and volume - one event, one row. A candle whose
    close or volume moved is a revision and gets a new identity, so the session processes it and the
    replay sees both. Open, high and low are excluded deliberately: including them would split a
    republication into two events whenever the high ticked without the close moving, and the
    resulting second row would price nothing differently.

    ``close`` and ``volume`` go through :func:`canonical_number` so a re-serialised number is the
    same identity. ``timestamp_ms`` is the integer epoch milliseconds of the *normalised* open
    time, so a payload that spelled its instant differently still lands on one identity.

    Public because ``paper_replay`` and any audit of a session's recorded log must be able to
    recompute it from a stored row rather than trusting the stored string.
    """
    material = (
        f"{str(exchange_id).strip().lower()}"
        f"|{str(symbol).strip()}"
        f"|{str(timeframe).strip()}"
        f"|{int(timestamp_ms)}"
        f"|{canonical_number(close)}"
        f"|{canonical_number(volume)}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalise_transport(value: Any) -> str:
    """The payload's ``transport`` field as one of :data:`FEED_TRANSPORTS`.

    An absent, blank or unrecognised value becomes :data:`FEED_TRANSPORT_UNKNOWN` - never
    ``WEBSOCKET``. See :data:`FEED_TRANSPORT_UNKNOWN` for why that matters: the REST fallback
    publishes to the same channel with an otherwise identical payload, so "the field is missing" is
    exactly the case in which the two paths cannot be told apart, and naming one of them would be
    reporting a REST-polled session as a live socket.

    An *unrecognised* value is treated the same way as an absent one rather than being stored
    verbatim: ``feed_transport`` is a closed vocabulary the simulator's gate reads, and a value
    outside it would be a state nothing downstream has a rule for.
    """
    if value is None:
        return FEED_TRANSPORT_UNKNOWN
    text = str(value).strip().upper()
    return text if text in FEED_TRANSPORTS else FEED_TRANSPORT_UNKNOWN


# ══════════════════════════════════════════════════════════════════════════
# 24.3 - NORMALISATION AND VALIDATION, THROUGH THE EXISTING PIPELINE
# ══════════════════════════════════════════════════════════════════════════


def decode_payload(raw: Any) -> Optional[Mapping[str, Any]]:
    """One ``mds:data:*`` message body as a mapping, with every number an exact ``Decimal``.

    ``json.loads(..., parse_float=Decimal)`` is the whole point: a close price decoded as a
    ``float`` has already lost the digits the exchange published, and no later care recovers them.
    Requirement 18.1 forbids the binary floating-point arithmetic that would follow, and
    Requirement 15.4's replay needs the stored value to be the delivered value.

    ``bytes`` are decoded as UTF-8, which is what ``redis.asyncio`` hands back without
    ``decode_responses``. A mapping that arrives already decoded passes through, so a caller that
    injected its own message is not forced to re-serialise it - but any ``float`` inside such a
    mapping is refused later by :func:`_exact_decimal`, so the exactness rule holds either way.

    ``None`` for anything undecodable. The caller counts it as ``paper.feed.invalid`` and drops it;
    nothing is repaired and no field is guessed (Requirement 14.9).
    """
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
    if not isinstance(raw, str):
        return None
    try:
        decoded = json.loads(raw, parse_float=Decimal)
    except (ValueError, TypeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def _decode_refusal(raw: Any) -> str:
    """Which of the two decode reasons a body :func:`decode_payload` refused actually earns.

    :data:`INVALID_NOT_A_MAPPING` when the body was **valid JSON that is not an object** - a bare
    number, a string, ``null``, or the CCXT-style array ``[ts, o, h, l, c, v]`` that a publisher
    might send without wrapping it - and :data:`INVALID_UNDECODABLE` when it was not JSON at all,
    or not text at all.

    The two are counted separately because they are different incidents with different fixes: the
    first is a publisher on this channel using a shape ``broadcast_ohlcv`` does not publish, and the
    second is a truncated frame or a transport problem. A runbook matching on
    ``PAYLOAD_NOT_AN_OBJECT`` can only fire if something emits it, so the label
    :data:`INVALID_NOT_A_MAPPING` declares is emitted rather than merely defined.

    Reached **only on the drop path**, after :func:`decode_payload` has already refused, so the
    second parse costs one event that was not going to be processed anyway - and it is a parse of
    the same bytes rather than a guess about them.
    """
    body = raw
    if isinstance(body, (bytes, bytearray)):
        try:
            body = bytes(body).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return INVALID_UNDECODABLE
    if not isinstance(body, str):
        return INVALID_NOT_A_MAPPING if body is not None else INVALID_UNDECODABLE
    try:
        json.loads(body, parse_float=Decimal)
    except (ValueError, TypeError):
        return INVALID_UNDECODABLE
    return INVALID_NOT_A_MAPPING


def _validate_candle(
    symbol: str, open_time: Any, values: Mapping[str, Decimal]
) -> Optional[str]:
    """Run the platform's OHLCV validators over one candle. ``None`` when it passes.

    Returns the validator's own refusal message when it does not, so the log line and the drop
    reason are the validator's words rather than a paraphrase.

    THE ENTRY POINT IS DATAFRAME-SHAPED, AND THAT IS THE HONEST REPORT
    ------------------------------------------------------------------
    ``market_data_validation`` exposes ``get_validator()`` (an ``async`` four-step pipeline over a
    frame) and ``validate_market_data_strict(df, ...)``. Neither validates a single candle, and
    there is no third entry point. So this builds a **one-row frame** and calls the two validators
    that ``MarketDataValidator.validate`` calls as its steps 1 and 2:

    * ``StructuralValidator.validate`` - the five required columns and a timestamp index.
    * ``CandleIntegrityValidator.validate`` - ``low <= min(open, close, high)``,
      ``high >= max(open, close, low)``, non-negative volume, and strictly positive prices. The
      first three raise ``DataValidationError``; the fourth is reported as a
      ``severity='error'`` issue, and this function treats an error-severity issue as a refusal so
      a non-positive price is dropped rather than recorded.

    Steps 3 and 4 - ``OutlierDetector`` (a z-score against the window) and ``GapHandler`` (a gap
    wider than 1.5 bars) - are **not** run, and that is a deliberate, recorded limitation rather
    than an oversight: the standard deviation of one sample is undefined and a gap needs two bars,
    so both would return a verdict about nothing. A single arriving candle is therefore checked for
    internal consistency and positive prices; the outlier and gap properties of the surrounding
    *window* are not checked on this path at all.

    ``validate_market_data_strict`` was considered and rejected as the entry point: it does not
    call ``CandleIntegrityValidator`` at all, so an ``low > high`` candle would pass it, and its
    ``StrictDataValidator`` steps are no-op stubs in this build.

    THE FRAME HOLDS ``Decimal``, NOT ``float``
    ------------------------------------------
    Built with ``dtype=object`` so the validators' comparisons - ``low > min(o, c, h)``,
    ``df[col] <= 0``, ``df['volume'] < 0`` - run on the exact decimals that arrived rather than on
    a float rounding of them. A validator that admitted a candle only after a float conversion had
    flattened its last digit would be validating something other than what gets stored.

    Nothing here reaches ``market_data_validation._generate_sample_data``, which imports ``random``
    and fabricates candles: it is called only from that module's two ``@router`` demo endpoints, and
    ``MarketDataValidator.validate`` - whose ``_attempt_fallback`` branch could call a registered
    fetcher - is not called either.
    """
    import pandas as pd

    from backend_app.backend.market_data_validation import (
        CandleIntegrityValidator,
        DataValidationError,
        StructuralValidator,
    )

    frame = pd.DataFrame(
        [[values[name] for name in OHLCV_NAMES]],
        columns=list(OHLCV_NAMES),
        index=pd.DatetimeIndex([open_time], name="timestamp"),
        dtype=object,
    )

    try:
        frame, structural_issues = StructuralValidator.validate(frame, symbol)
        frame, integrity_issues = CandleIntegrityValidator.validate(frame, symbol)
    except DataValidationError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - a validator that could not run is not a pass
        # Never swallowed and never read as "valid": Requirement 26.5 forbids swallowing an
        # exception on a correctness-critical path without a log record and a defined outcome, and
        # the defined outcome here is that the event is dropped. Admitting a candle because the
        # validator raised something unexpected would be the opposite of what the validator is for.
        logger.error(
            "[paper-feed] the market-data validator did not complete for %s, so the candle is "
            "dropped rather than admitted: %s",
            symbol,
            exc,
        )
        return f"the validator did not complete: {exc}"

    for issue in list(structural_issues) + list(integrity_issues):
        if str(getattr(issue, "severity", "")).lower() == "error":
            return str(getattr(issue, "message", issue))
    return None


# ══════════════════════════════════════════════════════════════════════════
# 24.2 / 24.4 - THE LIVE SUBSCRIPTION
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class FeedHandle:
    """One session's live market-data subscription, and the state that makes it correct.

    Created only by :func:`open_feed`, which is what guarantees the two refusals ran first.

    The three pieces of state are the three guarantees of Requirement 14.7 and 14.5:

    * :attr:`seen_event_ids` - the bounded LRU of processed identities. A **cache**; see
      :data:`SEEN_EVENT_ID_LIMIT`.
    * :attr:`last_timestamp` - the highest processed market instant **per symbol**, which is what
      makes the processed sequence non-decreasing per symbol. Per symbol and not global, because
      two symbols' feeds interleave and a global high-water mark would drop one symbol's candle
      because the other's arrived first.
    * :attr:`feed_state` - the health record, mirrored onto ``paper_sessions.feed_state`` so the
      simulator can read it inside its own transaction rather than from this object, which lives in
      one worker's memory.

    :attr:`sequence` is the number of the last event written to ``paper_market_events``. It starts
    at 0 and the first accepted event is 1, because ``chk_paper_market_event_sequence`` is
    ``CHECK (sequence >= 1)``. A resumed session continues from the log rather than restarting at 1
    - :func:`open_feed` reads that from the database.
    """

    config: PaperFeedConfig
    decision: SourceDecision
    market_data_source: str
    redis: Any
    supabase: Any = None

    feed_state: str = FEED_STATE_PENDING
    feed_transport: Optional[str] = None
    sequence: int = 0
    reconnect_attempts: int = 0

    #: ``source_event_id -> None``, oldest first. An ``OrderedDict`` and not a ``set`` because the
    #: bound has to evict the OLDEST entry, and a set has no order to evict by.
    seen_event_ids: "OrderedDict[str, None]" = field(default_factory=OrderedDict)
    last_timestamp: Dict[str, datetime] = field(default_factory=dict)

    _pubsub: Any = None
    _messages: Any = None
    closed: bool = False

    # -- identity -----------------------------------------------------------

    @property
    def channel(self) -> str:
        """The ``mds:data:*`` channel this handle is subscribed to."""
        return self.config.channel

    @property
    def session_id(self) -> str:
        return self.config.session_id

    @property
    def user_id(self) -> str:
        return self.config.user_id

    # -- the mds handshake (Requirement 14.2) -------------------------------

    async def _subscribe(self) -> None:
        """Publish ``subscribe`` to ``mds:commands``, then subscribe to the data channel.

        Exactly the handshake ``data_seeking_engine.DataEngine.stream_live_ohlcv`` already
        performs, in the same order and against the same two channels: the command wakes
        ``mds/main.py::handle_commands``, which starts ``broadcast_ohlcv`` for the pair if it is not
        already running, and the data channel is where that coroutine publishes.

        Publish first, subscribe second - which is the order the existing code uses and which
        deliberately accepts that a candle published between the two is missed. The alternative,
        subscribing first, would have the same gap at the other end and would additionally leave a
        subscription open if the command failed. A missed candle is a candle this session never saw;
        it is not interpolated, not back-filled and not counted as processed, which is what
        Requirement 14.9 requires.

        :meth:`close` is the mirror image of this method - the ``unsubscribe`` command through
        :func:`publish_unsubscribe`, then the local pubsub release - and both build their message
        with :func:`mds_command`, so the pair the release names is the pair the subscribe woke.
        """
        command = mds_command(
            MDS_ACTION_SUBSCRIBE,
            self.config.normalised_exchange_id,
            self.config.symbol,
        )
        await self.redis.publish(MDS_COMMAND_CHANNEL, command)

        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.channel)
        self._pubsub = pubsub
        self._messages = pubsub.listen().__aiter__()
        self.closed = False

    async def _receive(self) -> Mapping[str, Any]:
        """The next ``type == 'message'`` frame from the subscription.

        Subscribe and unsubscribe confirmations are skipped rather than counted as invalid market
        events: they are protocol frames, not candles, and counting them would make
        ``paper.feed.invalid`` a mixture of two unrelated facts.

        Raises:
            PaperFeedDisconnected: the iterator ended or failed. Both are the dropped subscription
                of Requirement 14.5, and both reach the caller as the same condition because the
                session's answer to them is the same.
        """
        if self.closed or self._messages is None:
            raise PaperFeedDisconnected(
                f"session {self.session_id!r} has no open subscription to {self.channel!r}"
            )
        while True:
            try:
                message = await self._messages.__anext__()
            except StopAsyncIteration as exc:
                raise PaperFeedDisconnected(
                    f"the subscription to {self.channel!r} ended"
                ) from exc
            except asyncio.CancelledError:
                # Cancellation is the caller shutting this feed down, not the feed dropping.
                raise
            except Exception as exc:  # noqa: BLE001 - re-raised as the defined condition
                raise PaperFeedDisconnected(
                    f"the subscription to {self.channel!r} failed: {exc}"
                ) from exc
            if not isinstance(message, Mapping):
                continue
            if str(message.get("type")) != "message":
                continue
            return message

    # -- disconnection and reconnection (Requirement 14.5) ------------------

    async def mark_degraded(self, reason: str) -> None:
        """Record the dropped subscription: ``feed_state = 'DEGRADED'`` plus one ``paper_error``.

        Both halves of Requirement 14.5's record, in this order, and **idempotent**: a session
        already ``DEGRADED`` writes neither again, so a feed that fails on every reconnection
        attempt produces one error record per outage rather than one per attempt. The Paper_Channel
        is a log an operator reads; a thousand identical lines would bury the one that matters.

        The persisted state is what the simulator reads, so it is written before this returns. A
        write that fails is logged and re-raised through ``paper_repository``'s own exceptions
        rather than swallowed: a session whose ``DEGRADED`` state did not reach the database would
        keep filling orders at a pre-disconnection price, which is precisely what Requirement 14.5
        forbids.
        """
        already_degraded = self.feed_state == FEED_STATE_DEGRADED
        self._transition_feed_state(FEED_STATE_DEGRADED, reason=reason)
        if already_degraded:
            return

        logger.error(
            "[paper-feed] session %s feed DEGRADED on %s: %s",
            self.session_id,
            self.channel,
            reason,
        )
        await self._emit_feed_disconnected(reason)

    async def _emit_feed_disconnected(self, reason: str) -> None:
        """Append the ``paper_error`` record carrying ``code: 'FEED_DISCONNECTED'``.

        ``event_id`` is derived from the session, the channel and the sequence, so the same outage
        cannot produce two records with different ids and ``uq_paper_event_id`` has something
        meaningful to arbitrate. A duplicate is a no-op - the record is already in the log.

        The payload names the channel and the reason and carries no credential, no endpoint and no
        other user's identifier (Requirement 26.4).
        """
        if self.supabase is None:
            return
        try:
            sequence = repo.next_session_event_sequence(
                self.supabase, self.user_id, self.session_id
            )
            event_id = (
                "feed-disconnected-"
                + hashlib.sha256(
                    f"{self.session_id}|{self.channel}|{sequence}".encode("utf-8")
                ).hexdigest()[:32]
            )
            repo.insert_session_event(
                self.supabase,
                session_id=self.session_id,
                user_id=self.user_id,
                sequence=sequence,
                event_id=event_id,
                event_type=PAPER_ERROR_EVENT_TYPE,
                schema_version=PAPER_EVENT_SCHEMA_VERSION,
                payload={
                    "code": FEED_DISCONNECTED_CODE,
                    "channel": self.channel,
                    "exchange_id": self.config.exchange_id,
                    "symbol": self.config.symbol,
                    "timeframe": self.config.timeframe,
                    "market_data_source": self.market_data_source,
                    "feed_state": FEED_STATE_DEGRADED,
                    "reason": str(reason),
                },
                emitted_at=_utc_now(),
            )
        except repo.PaperDuplicateSessionEvent:
            # The record is already in the log. Nothing to do, and not an error: the guarantee
            # Requirement 14.5 asks for is that the record exists, not that this call wrote it.
            logger.info(
                "[paper-feed] the FEED_DISCONNECTED record for session %s is already recorded",
                self.session_id,
            )
        except Exception as exc:  # noqa: BLE001 - logged, then swallowed for a stated reason
            # The DEGRADED state is already persisted by the time this runs, so the no-fill
            # guarantee of Requirement 14.5 is in force regardless of what happens here. An audit
            # line that could not be written must not roll back the state transition it describes,
            # and it must not be silent either - so it is logged at error level (Requirement 26.5)
            # and the gap is visible in the log the record would have gone to.
            logger.error(
                "[paper-feed] the FEED_DISCONNECTED record for session %s could not be written, "
                "so the Paper_Channel has a gap where it should be; the session IS degraded and "
                "no order will fill: %s",
                self.session_id,
                exc,
            )

    async def reconnect(self) -> bool:
        """Sleep the next bounded backoff delay, then re-open the subscription. One attempt.

        One attempt per call, and the caller drives the loop, for two reasons. It keeps the delay
        sequence assertable - a test can observe 1, 2, 4, 8, 16, 30, 30 without waiting 61 seconds
        - and it leaves the decision to keep trying with the session worker, which is the only
        thing that knows whether the session has since been stopped.

        ``asyncio.sleep``, never ``time.sleep``: Requirement 27.3 requires the session service not
        to block the event loop, and a blocking sleep here would stall every other session in the
        process for up to 30 seconds.

        Nothing is interpolated across the gap (Requirement 14.9). This method re-opens a
        subscription and does nothing else: it does not fetch the missed window, does not
        back-fill, does not carry the last price forward and does not touch
        :attr:`last_timestamp`. The session resumes from the first event that passes validation
        after this returns, and the candles that closed during the outage are simply candles this
        session never saw.

        Returns ``True`` when the subscription is open again. The session stays ``DEGRADED`` until
        an event **passes validation**, because a subscription that accepted the command is not yet
        evidence that data is flowing.
        """
        self.reconnect_attempts += 1
        delay = backoff_delay_seconds(self.reconnect_attempts)
        logger.info(
            "[paper-feed] session %s reconnect attempt %d in %ss",
            self.session_id,
            self.reconnect_attempts,
            delay,
        )
        # float() only here, for asyncio's clock. Not a money or price computation, and the value
        # is an exact integral Decimal, so the conversion is lossless.
        await asyncio.sleep(float(delay))

        await self._close_pubsub()
        try:
            await self._subscribe()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed attempt is a defined outcome
            _record_reconnect("failed")
            logger.warning(
                "[paper-feed] session %s reconnect attempt %d failed: %s",
                self.session_id,
                self.reconnect_attempts,
                exc,
            )
            return False
        _record_reconnect("resubscribed")
        return True

    async def _close_pubsub(self) -> None:
        """Release the current subscription, tolerating a handle that is already gone."""
        pubsub = self._pubsub
        self._pubsub = None
        self._messages = None
        if pubsub is None:
            return
        for method, args in (("unsubscribe", (self.channel,)), ("close", ())):
            call = getattr(pubsub, method, None)
            if call is None:
                continue
            try:
                result = call(*args)
                if asyncio.iscoroutine(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - teardown noise only
                logger.debug(
                    "[paper-feed] %s on %s during teardown: %s", method, self.channel, exc
                )

    async def close(self) -> bool:
        """Release the subscription - BOTH halves, in the mirror of :meth:`_subscribe`'s order.

        Requirement 17.8 requires a stopped session's market-data subscription released, and
        :meth:`_subscribe` opened two things, so this releases two:

        1. ``PUBLISH {'action': 'unsubscribe', ...}`` to ``mds:commands``, through
           :func:`publish_unsubscribe` - the ONE unsubscribe command in the codebase. This is the
           half that matters to a separate process: without it ``mds``'s ``active_streams`` keeps a
           ``broadcast_ohlcv`` task running for a pair nobody is listening to.
        2. the local pubsub unsubscribe and close, through :meth:`_close_pubsub`.

        Command first, then the local release, exactly as :meth:`_subscribe` publishes before it
        subscribes. The local release is in a ``finally``, so a command that could not be published
        still leaves this process holding no subscription: a leaked pubsub handle in the worker that
        performed the stop is the one failure the caller cannot report its way out of.

        **Idempotent, and the command is published at most once.** A second call on a closed handle
        returns ``False`` without publishing: a stop path and a shutdown path may both reach here,
        and two ``unsubscribe`` messages for one subscription would be a second unsubscribe rather
        than a repeat of the first.

        Deliberately does **not** write a feed state: closing a feed is the session lifecycle's act
        (:func:`~paper_session_service.stop_session`), and writing ``DEGRADED`` here would report a
        cleanly stopped session as a broken one.

        Returns:
            Whether the ``mds:commands`` unsubscribe was published on THIS call. The local release
            always happens; this is the half that can fail, and
            :func:`~paper_session_service.stop_session` will not report a stop complete while it is
            ``False`` on the first call (Requirement 17.8's "only after those steps have committed").
        """
        if self.closed:
            logger.debug(
                "[paper-feed] session %s has already released its subscription to %s; publishing a "
                "second mds unsubscribe would not be a repeat of the first",
                self.session_id,
                self.channel,
            )
            return False
        self.closed = True
        try:
            return await publish_unsubscribe(
                self.redis,
                exchange_id=self.config.normalised_exchange_id,
                symbol=self.config.symbol,
                session_id=self.session_id,
            )
        finally:
            await self._close_pubsub()

    # -- state ---------------------------------------------------------------

    def _transition_feed_state(
        self, state: str, *, reason: str = "", transport: Optional[str] = None
    ) -> None:
        """Move :attr:`feed_state`, mirror it onto the session row, and count the transition.

        The persisted write happens **only when something changed**, which is what keeps a healthy
        feed from issuing one UPDATE per candle. The in-process value is the same value, so a
        caller reading this handle and the simulator reading the row see the same state.
        """
        if state not in FEED_STATES:
            raise ValueError(
                f"{state!r} is not a paper feed state; expected one of {list(FEED_STATES)}"
            )
        if transport is not None and transport not in FEED_TRANSPORTS:
            raise ValueError(
                f"{transport!r} is not a paper feed transport; expected one of "
                f"{list(FEED_TRANSPORTS)}"
            )

        state_changed = state != self.feed_state
        transport_changed = transport is not None and transport != self.feed_transport
        self.feed_state = state
        if transport is not None:
            self.feed_transport = transport
        if not (state_changed or transport_changed):
            return

        _record_feed_state_metric(state, reason=reason)
        if self.supabase is None:
            return
        repo.update_session_feed(
            self.supabase,
            user_id=self.user_id,
            session_id=self.session_id,
            feed_state=state if state_changed else None,
            feed_transport=self.feed_transport if transport_changed else None,
        )

    def _remember_seen(self, event_id: str) -> None:
        """Add one identity to the bounded LRU, evicting the oldest beyond the bound."""
        self.seen_event_ids[event_id] = None
        self.seen_event_ids.move_to_end(event_id)
        while len(self.seen_event_ids) > SEEN_EVENT_ID_LIMIT:
            self.seen_event_ids.popitem(last=False)

    def has_seen(self, event_id: str) -> bool:
        """Whether this identity is in the in-process LRU. Not the durable answer.

        ``False`` means "not in the cache", never "not in the database". The durable answer is
        ``uq_paper_market_event``, and :func:`next_validated_event` is written so that a ``False``
        here followed by a unique violation is a no-op rather than an error.
        """
        return event_id in self.seen_event_ids


def _utc_now() -> datetime:
    """The processing instant, tz-aware UTC.

    The only clock this module reads, and it is read for exactly two things: the latency of
    Requirement 14.10 and the ``emitted_at`` / ``received_at`` of the two log rows. No price, no
    quantity and no market instant comes from here - those come from the event - which is what
    keeps a session replayable (Requirement 15.4).
    """
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# 24.3 - next_validated_event
# ══════════════════════════════════════════════════════════════════════════


async def next_validated_event(handle: FeedHandle) -> Optional[MarketEvent]:
    """One event: normalised, validated, de-duplicated, ordered, recorded. ``None`` when dropped.

    ``design.md`` -> ``ASYNC FUNCTION next_validated_event``, in its order, plus the disconnection
    branch Requirement 14.5 requires. Every ``None`` is a drop with a counted reason and **nothing
    is ever repaired**: a candle whose low exceeds its high is not corrected, a missing field is not
    defaulted, a late candle is not re-sorted into place and a gap is not filled (Requirement 14.9).

    The gates, in order, and why the order is what it is:

    1. **Receive.** A dropped subscription becomes ``DEGRADED`` plus one ``paper_error`` record, one
       bounded reconnection attempt is made, and ``None`` is returned. The caller calls again.
    2. **Decode** with ``parse_float=Decimal``. Undecodable -> ``paper.feed.invalid``.
    3. **The market matches.** A payload for another exchange, symbol or timeframe is refused rather
       than processed: the channel is per ``(exchange, symbol)``, so a mismatch means either a
       mis-subscription or a foreign publisher, and recording it against this session would put a
       price from a market the session is not trading into its replay log.
    4. **Normalise** through ``market_data_contract.normalise_candle``.
    5. **Exact values.** Any of the five that is not an exact decimal -> invalid. This is where a
       ``float`` is refused (Requirement 18.1).
    6. **Validate** through ``market_data_validation``'s own validators -> ``paper.feed.invalid``.
    7. **Identity**, then the LRU. A hit -> ``paper.feed.duplicates``, dropped (Requirement 14.7).
    8. **Order.** Below the symbol's high-water mark -> ``paper.feed.out_of_order``, dropped, so the
       processed sequence per symbol is non-decreasing (Requirement 14.7).
    9. **Record** to ``paper_market_events`` with its sequence, identity and payload
       (Requirement 15.5). A unique violation is a **no-op**: the LRU learns the identity, the
       duplicate is counted against the unique index rather than against the cache, and neither the
       sequence nor the high-water mark moves - so a cache miss cannot produce a second row and
       cannot corrupt the session's ordering either.
    10. **Then** the high-water mark, the sequence and the feed state move. They move *after* the
        write, so an event that failed to record has not been counted as processed.

    Step 9 before step 10 is the important one. A session that advanced its high-water mark and then
    failed to write would have silently made the candle unreplayable while treating it as processed;
    a session that writes first and then advances has a log that matches its state.
    """
    config = handle.config
    symbol = config.symbol

    # 1. Receive, or handle the drop (Requirement 14.5).
    try:
        message = await handle._receive()
    except PaperFeedDisconnected as exc:
        await handle.mark_degraded(str(exc))
        await handle.reconnect()
        return None

    # 2. Decode.
    payload = decode_payload(message.get("data"))
    if payload is None:
        reason = _decode_refusal(message.get("data"))
        _record_invalid(symbol, reason)
        logger.warning(
            "[paper-feed] session %s dropped a message on %s it could not read: %s",
            handle.session_id,
            handle.channel,
            reason,
        )
        return None

    # 3. The payload is about the market this session subscribed to.
    if not _payload_matches_market(payload, config):
        _record_invalid(symbol, INVALID_WRONG_MARKET)
        logger.warning(
            "[paper-feed] session %s dropped an event on %s that names exchange=%r symbol=%r "
            "timeframe=%r, which is not this session's market",
            handle.session_id,
            handle.channel,
            payload.get("exchange"),
            payload.get("symbol"),
            payload.get("timeframe"),
        )
        return None

    transport = normalise_transport(payload.get("transport"))

    # 4. Normalise through the existing pipeline (Requirement 14.2).
    from backend_app.backend.market_data_contract import normalise_candle

    candle = dict(payload)
    stamp_ms = _epoch_ms(payload.get("timestamp"))
    if stamp_ms is None:
        _record_invalid(symbol, INVALID_UNREADABLE_TIMESTAMP)
        return None
    candle["timestamp"] = stamp_ms

    parsed = normalise_candle(candle)
    if parsed is None:
        _record_invalid(symbol, INVALID_UNREADABLE_SHAPE)
        return None
    open_time, raw_values, _feed_says_closed = parsed

    # 5. Exact decimals, or nothing.
    values: Dict[str, Decimal] = {}
    for name, raw in zip(OHLCV_NAMES, raw_values):
        amount = _exact_decimal(raw)
        if amount is None:
            _record_invalid(symbol, INVALID_INEXACT_VALUE)
            logger.warning(
                "[paper-feed] session %s dropped an event whose %s (%r) is not an exact decimal; "
                "a paper fill is priced from this value and Requirement 18.1 forbids a binary "
                "float",
                handle.session_id,
                name,
                raw,
            )
            return None
        values[name] = amount

    # 6. Validate through the existing validators.
    refusal = _validate_candle(symbol, open_time, values)
    if refusal is not None:
        _record_invalid(symbol, INVALID_VALIDATOR_REJECTED)
        logger.warning(
            "[paper-feed] session %s dropped an event the market-data validator rejected: %s",
            handle.session_id,
            refusal,
        )
        return None

    event_timestamp = _as_utc(open_time)

    # 7. Identity, then the LRU (Requirement 14.7).
    event_id = source_event_id(
        config.exchange_id,
        config.symbol,
        config.timeframe,
        stamp_ms,
        values["close"],
        values["volume"],
    )
    if handle.has_seen(event_id):
        _record_duplicate(symbol, DUPLICATE_ARBITER_LRU)
        return None

    # 8. Order (Requirement 14.7).
    high_water = handle.last_timestamp.get(symbol)
    if high_water is not None and event_timestamp < high_water:
        _record_out_of_order(symbol)
        logger.info(
            "[paper-feed] session %s dropped an event for %s at %s, below the %s already "
            "processed for that symbol",
            handle.session_id,
            symbol,
            event_timestamp.isoformat(),
            high_water.isoformat(),
        )
        return None

    received_at = _utc_now()
    latency_ms = _latency_ms(received_at, event_timestamp)
    stored_payload = _stored_payload(
        config, stamp_ms, values, transport=transport, source_event_id=event_id
    )
    sequence = handle.sequence + 1

    # 9. Record it (Requirement 15.5). The unique index is the durable dedupe arbiter.
    if handle.supabase is not None:
        try:
            repo.insert_market_event(
                handle.supabase,
                session_id=handle.session_id,
                user_id=handle.user_id,
                sequence=sequence,
                source_event_id=event_id,
                symbol=symbol,
                timeframe=config.timeframe,
                event_timestamp=event_timestamp,
                payload=stored_payload,
                received_at=received_at,
                latency_ms=latency_ms,
            )
        except repo.PaperDuplicateMarketEvent:
            # The LRU missed and the database caught it. A NO-OP, not an error: the first record
            # stands, and nothing is surfaced to the caller because nothing went wrong - the
            # guarantee Requirement 14.7 asks for is that the event is processed at most once, and
            # it has been. The identity is added to the cache so the next copy is caught before the
            # round trip, and neither the sequence nor the high-water mark moves.
            handle._remember_seen(event_id)
            _record_duplicate(symbol, DUPLICATE_ARBITER_UNIQUE_INDEX)
            logger.info(
                "[paper-feed] session %s met %s again; uq_paper_market_event refused the second "
                "row and the in-process cache has been updated",
                handle.session_id,
                event_id[:16],
            )
            return None

    # 10. Only now does the session's processed state move.
    handle._remember_seen(event_id)
    handle.last_timestamp[symbol] = event_timestamp
    handle.sequence = sequence
    handle.reconnect_attempts = 0
    handle._transition_feed_state(
        FEED_STATE_FOR_TRANSPORT[transport],
        reason=f"validated_event:{transport}",
        transport=transport,
    )
    _record_accepted(symbol, transport, config.timeframe, latency_ms)

    return MarketEvent(
        session_id=handle.session_id,
        user_id=handle.user_id,
        sequence=sequence,
        source_event_id=event_id,
        exchange_id=config.exchange_id,
        symbol=symbol,
        timeframe=config.timeframe,
        event_timestamp=event_timestamp,
        open=values["open"],
        high=values["high"],
        low=values["low"],
        close=values["close"],
        volume=values["volume"],
        transport=transport,
        latency_ms=latency_ms,
        received_at=received_at,
        payload=stored_payload,
        feed_state=handle.feed_state,
    )


def _payload_matches_market(payload: Mapping[str, Any], config: PaperFeedConfig) -> bool:
    """Whether the payload names this session's exchange, symbol and timeframe.

    An **absent** field is treated as matching, because a payload need not restate what the channel
    already encodes and an older MDS is not required to. A field that is present and names a
    different market is refused: that is either a mis-subscription or a foreign publisher on the
    channel, and either way the price belongs to a market this session is not trading.
    """
    for key, expected in (
        ("exchange", config.normalised_exchange_id),
        ("symbol", str(config.symbol).strip()),
        ("timeframe", str(config.timeframe).strip()),
    ):
        if key not in payload or payload.get(key) is None:
            continue
        actual = str(payload.get(key)).strip()
        if key == "exchange":
            actual = actual.lower()
        if actual != expected:
            return False
    return True


def _as_utc(open_time: Any) -> datetime:
    """``market_data_contract``'s tz-naive UTC instant as a tz-aware UTC ``datetime``.

    That module indexes its frames in tz-naive UTC, which is its documented convention;
    ``paper_market_events.event_timestamp`` is ``TIMESTAMPTZ`` and 009's shape assertion refuses
    ``timestamp without time zone`` precisely so no instant is silently reinterpreted as local time.
    This is the one conversion between the two, and it attaches UTC rather than guessing a zone.
    """
    moment = open_time
    to_pydatetime = getattr(moment, "to_pydatetime", None)
    if callable(to_pydatetime):
        moment = to_pydatetime()
    if not isinstance(moment, datetime):
        moment = datetime.fromisoformat(str(moment))
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


#: The scale ``paper_market_events.latency_ms NUMERIC(10,3)`` stores. Quantized here rather than
#: letting the database round, so the recorded figure and the reported figure are one number.
LATENCY_QUANTUM = Decimal("0.001")


def _latency_ms(received_at: datetime, event_timestamp: datetime) -> Decimal:
    """Requirement 14.10's delivery latency: processing time minus the event's own timestamp.

    Exact decimal arithmetic, from the two instants' microsecond difference - not from
    ``total_seconds()``, which is a float and would put a binary rounding into a persisted
    ``NUMERIC(10,3)``. Quantized to :data:`LATENCY_QUANTUM`, the column's scale.

    It can be **negative**, and a negative value is recorded as-is rather than clamped: a candle
    whose open time is in the future relative to this process's clock means the two clocks disagree,
    and reporting that as a zero delay would hide a real condition (Requirement 28.5 - no value
    presented as a measurement that is not one). ``market_data_latency.SourceMeasurement`` has a
    ``clock_skew_ms`` field for exactly this fact.
    """
    delta = received_at - event_timestamp
    microseconds = Decimal(delta.days) * 86_400_000_000 + Decimal(
        delta.seconds
    ) * 1_000_000 + Decimal(delta.microseconds)
    return (microseconds / Decimal(1000)).quantize(LATENCY_QUANTUM)


def _stored_payload(
    config: PaperFeedConfig,
    timestamp_ms: int,
    values: Mapping[str, Decimal],
    *,
    transport: str,
    source_event_id: str,
) -> Dict[str, Any]:
    """The JSONB payload written to ``paper_market_events.payload``.

    The five values as **decimal strings**, because a JSON number cannot be read back exactly and
    ``paper_replay`` prices its ledger from this row (Requirements 15.4, 15.5, 18.1). The market,
    the instant and the transport travel with them so a replay needs no join to know what it is
    reading, and ``source_event_id`` is stored inside the payload as well as in its own column so a
    row copied out of the table carries its own identity.

    Nothing else. No credential, no endpoint, no request identifier and no other user's identifier
    (Requirement 26.4); no strategy logic (Requirement 4.x's Protected_Logic); and nothing derived,
    so every figure here is one the feed delivered.
    """
    payload: Dict[str, Any] = {
        "exchange": config.normalised_exchange_id,
        "symbol": str(config.symbol),
        "timeframe": str(config.timeframe),
        "timestamp": int(timestamp_ms),
        "transport": transport,
        "source_event_id": source_event_id,
    }
    for name in OHLCV_NAMES:
        payload[name] = str(values[name])
    return payload


# ══════════════════════════════════════════════════════════════════════════
# 24.4 - THE FEED GATE (Requirements 14.5, 18.15)
# ══════════════════════════════════════════════════════════════════════════

#: The object :class:`ExecutionAdmission` demands before it will construct itself. Module-private
#: and never exported, so the only code that can mint an admission is :func:`admit_execution`.
_ADMISSION_KEY = object()


@dataclass(frozen=True)
class ExecutionAdmission:
    """Evidence that the feed gate was consulted and admitted this session. Unforgeable.

    A token rather than a ``bool`` for one reason: a boolean return can be *skipped*. A simulator
    that forgot to call the gate would carry ``True`` by omission, and the omission would look
    exactly like a pass. A token has to be produced, and only :func:`admit_execution` can produce
    one - :data:`_ADMISSION_KEY` is module-private, so ``ExecutionAdmission(...)`` from outside this
    module raises. So task 25's simulator can require the token on the code path that fills an
    order, and "the gate was consulted" becomes a property of the type rather than of a review.

    It carries the state it was granted on, not merely the fact of the grant, so a fill record can
    say *why* it was allowed. It carries no price: the gate reports the feed's health and prices
    nothing.
    """

    session_id: str
    user_id: str
    #: The state read from the row. Always one of :data:`TRADEABLE_FEED_STATES`.
    feed_state: str
    feed_transport: Optional[str]
    #: When the gate was consulted. A caller holding a token from an earlier transaction is holding
    #: a stale answer, and this is what lets it notice.
    admitted_at: datetime
    _key: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._key is not _ADMISSION_KEY:
            raise ValueError(
                "an ExecutionAdmission cannot be constructed directly; it is minted only by "
                "paper_market_feed.admit_execution, which is what makes holding one evidence "
                "that the feed gate was consulted (Requirements 14.5, 18.15)"
            )


def admit_execution(session_row: Mapping[str, Any]) -> ExecutionAdmission:
    """THE feed gate. Admit execution for this session, or raise :class:`FeedNotHealthy`.

    One callable, one rule: the session's ``feed_state`` is in :data:`TRADEABLE_FEED_STATES` - which
    holds exactly ``HEALTHY`` - or nothing may be accepted and nothing may be filled. There is no
    second predicate, no ``force`` argument and no bypass, because a gate with an override is a gate
    that will be overridden.

    THE SIMULATOR'S HALF OF REQUIREMENT 14.5
    ----------------------------------------
    Requirement 14.5 has two halves. This module owns the first: on a dropped subscription the
    session is marked ``DEGRADED``, one ``paper_error`` record is written, reconnection is attempted
    on a bounded jitter-free backoff, and the session resumes only from an event that passes
    validation. ``paper_simulator`` (task 25) owns the second: it must call **this function** with
    the ``paper_sessions`` row it read **inside its own transaction**, and it must refuse the market
    order and leave the resting limit order unfilled when this raises.

    Until task 25 lands, that second half is **not in force**, and this docstring says so rather
    than implying otherwise: no order path calls this function yet, so nothing today is prevented
    from filling by it. What exists today is the gate, the state it reads, and the guarantee that
    the state is written before a reconnection is attempted - so the answer this function gives is
    already correct, and the outstanding work is the call site rather than the rule.

    WHY IT TAKES A ROW AND NOT A ``FeedHandle``
    -------------------------------------------
    :class:`FeedHandle` lives in one worker's memory. The simulator may be in another process, and
    Requirement 17.3 requires every instance to answer the same. So the gate reads the **persisted**
    state - the row the caller has already read under its own RLS scope, inside its own transaction -
    and this function issues no statement of its own. It cannot: a read here would be outside the
    caller's transaction and could see a state the transaction will not.

    That also means the caller's read is what enforces tenancy. This function does not re-scope the
    row and does not accept a ``user_id`` to check it against, because a row fetched with the wrong
    predicates cannot be repaired by re-inspecting it; ``paper_repository.read_session`` is the read
    that keeps it the caller's own (Requirements 21.2, 21.5).

    Args:
        session_row: a ``paper_sessions`` row, as :func:`paper_repository.read_session` returns it.
            ``feed_state`` is the field that decides; ``id``, ``user_id`` and ``feed_transport``
            travel into the token for the record. A **missing or blank** ``feed_state`` is refused,
            not defaulted: "the row did not say" is not evidence of a healthy feed.

    Raises:
        FeedNotHealthy: the state is not tradeable. Carries the state that was read.
        ValueError: ``session_row`` is not a mapping - a programming error at the call site, and
            refused rather than treated as an unhealthy feed so the two cannot be confused.
    """
    if not isinstance(session_row, Mapping):
        raise ValueError(
            f"admit_execution takes a paper_sessions row mapping, got "
            f"{type(session_row).__name__}; the gate reads the PERSISTED feed state, which is the "
            f"only one a simulator in another process can trust"
        )

    session_id = str(session_row.get("id") or "")
    user_id = str(session_row.get("user_id") or "")
    raw_state = session_row.get("feed_state")
    feed_state = "" if raw_state is None else str(raw_state).strip()

    if feed_state not in TRADEABLE_FEED_STATES:
        _record_feed_state_metric(
            feed_state or FEED_STATE_PENDING, reason="execution_refused"
        )
        logger.warning(
            "[paper-feed] execution refused for session %s: feed_state is %r, and only %s "
            "admits a fill (Requirements 14.5, 18.15)",
            session_id or "<unidentified>",
            feed_state or None,
            list(TRADEABLE_FEED_STATES),
        )
        raise FeedNotHealthy(session_id, feed_state or raw_state)

    transport = session_row.get("feed_transport")
    return ExecutionAdmission(
        session_id=session_id,
        user_id=user_id,
        feed_state=feed_state,
        feed_transport=None if transport is None else str(transport),
        admitted_at=_utc_now(),
        _key=_ADMISSION_KEY,
    )
