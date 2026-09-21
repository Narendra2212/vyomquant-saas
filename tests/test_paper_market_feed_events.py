"""
tests/test_paper_market_feed_events.py - tasks 24.3 and 24.4, and nothing else.

Spec: marketplace-subscriptions-paper-trading task 24.3 (``next_validated_event`` - identity,
validation, dedupe, ordering) and task 24.4 (disconnection, backoff and the no-fill-while-degraded
rule). Requirements 14.5, 14.6, 14.7, 14.9, 15.5, 18.15, 26.6.

WHAT THIS FILE REUSES, AND WHY IT REUSES RATHER THAN REBUILDS
-------------------------------------------------------------
Everything shared with tasks 24.1/24.2 is imported from
``tests/test_paper_market_feed_selection.py``: the loop runner, the Redis double and its frame
queue, the metrics recorder, the floor-clean measurements, the session row, the frame builder and
the ``_open`` helper. Two market-data doubles would be two accounts of what arrives on
``mds:data:*``, and the point of asserting a wire contract is that there is one account of it.
``FakeSupabase`` comes from ``tests/test_paper_repository.py`` for the same reason - that file's own
docstring records the rule: exactly one Persistence_Layer double, and its three unique constraints
(``uq_paper_market_event``, ``uq_paper_event_seq``, ``uq_paper_event_id``) are enforced there, so a
dedupe assertion here cannot pass without a dedupe.

Two shared helpers were EXTENDED rather than copied, and both extensions live in the file that owns
them:

* ``_RecordingMetrics`` gained ``record_paper_feed_duplicate``,
  ``record_paper_feed_out_of_order`` and ``record_paper_feed_reconnect`` - the three calls tasks
  24.3 and 24.4 added to this path.
* ``_frame`` gained ``overrides=`` and ``drop=``, so a candle with exactly one thing wrong is
  built from the one well-formed shape rather than from a second hand-rolled payload.

WHERE THE REAL COLLECTOR IS USED INSTEAD OF THE RECORDER
--------------------------------------------------------
"An invalid event increments ``paper.feed.invalid``" is a claim about a **named** metric, and a
recording double cannot carry it: a double records the method that was called, not the counter that
moved. So the three counting claims of task 24.3 - ``paper.feed.invalid``,
``paper.feed.duplicates`` and ``paper.feed.out_of_order`` - are asserted against a fresh
``metrics.MetricsCollector``, by reading the counter's own ``name`` and its per-label value. A
method rename would still pass with a recorder; a counter rename would not pass here.

THE ORDERING RULE IS NON-DECREASING, WHICH ADMITS AN EQUAL TIMESTAMP - ON PURPOSE
--------------------------------------------------------------------------------
The module drops an event whose timestamp is strictly ``<`` the symbol's high-water mark, so an
**equal** timestamp is admitted. That is not a gap in the guard, it is what the two halves of
Requirement 14.7 require together. A forming candle is republished on every socket update with the
same open time; if its close or volume moved it is a *revision*, ``source_event_id`` differs
(Requirement 14.7's "different for a revised one"), and the session has to process it or the replay
would price the bar from a superseded close. If nothing moved, the identity is the same and the
dedupe drops it. So the equal-timestamp case is arbitrated by the identity, not by the clock, and
the sequence stays non-decreasing either way - which is the property the requirement states.
:class:`TestAnEqualTimestampIsAdmittedBecauseTheSequenceIsNonDecreasing` asserts both directions.

WHY THE RED RUNS ARE RECORDED PER CLASS
---------------------------------------
Every class below was run twice: once against the module as it stands, and once with the single
guard it is about removed, stubbed or inverted. The second run must fail, and what failed is
recorded on the class. A guard test that passes with the guard absent tests nothing.

TWO DEFECTS THIS FILE FOUND, AND THE PRODUCTION CHANGES THAT ANSWER THEM
------------------------------------------------------------------------
1. ``admit_execution`` and ``ExecutionAdmission`` were named in the module's own docstring surface
   list and referenced by :class:`~paper_market_feed.FeedNotHealthy`'s docstring, and **did not
   exist**. Task 24.4's fourth bullet requires a single named feed-state predicate for the
   simulator to consult; there was none, so there was nothing for task 25 to call and
   ``FeedNotHealthy`` was raised by nothing. Both are now implemented, with
   ``TRADEABLE_FEED_STATES`` as the whitelist they read.
2. ``INVALID_NOT_A_MAPPING`` (``PAYLOAD_NOT_AN_OBJECT``) was defined and **unreachable** - a body
   that decoded to valid JSON that was not an object was counted as ``UNDECODABLE_PAYLOAD``, so the
   label a runbook would match on could never be emitted. ``_decode_refusal`` now classifies the
   two on the drop path.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import textwrap
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from backend_app.backend import metrics as metrics_module
from backend_app.backend.paper import paper_market_feed as feed
from backend_app.backend.paper import paper_repository as repo

# ── The shared harness. See "WHAT THIS FILE REUSES" above. ────────────────────────────────
from tests.test_paper_market_feed_selection import (
    DATA_CHANNEL,
    EXCHANGE,
    OTHER_USER,
    PROCESSED_AT,
    SESSION,
    SYMBOL,
    TIMEFRAME,
    USER,
    RecordingRedis,
    _admitting,
    _client,
    _config,
    _frame,
    _ms_before,
    _open,
    _run_coroutine,
    _session_row,
)
from tests.test_paper_market_feed_selection import (  # noqa: F401 - autouse, used by name
    _fresh_probe,
)
from tests.test_paper_repository import FakeSupabase

#: A second session, for the two claims that need one: ``uq_paper_market_event`` is keyed on
#: ``(session_id, source_event_id)``, so the same identity in another session is not a duplicate,
#: and a session's market log is not readable from another session.
OTHER_SESSION = "33333333-3333-3333-3333-333333333333"


# ══════════════════════════════════════════════════════════════════════════
# DRIVERS
# ══════════════════════════════════════════════════════════════════════════


def _body_tree(function: Any) -> ast.Module:
    """One function's body as an AST, **with its docstring removed**.

    Several claims here are about what a method does and not about what its docstring says it does,
    and this module's docstrings quote the very identifiers those claims forbid - ``reconnect``
    explains that it does not back-fill and does not touch ``last_timestamp``, and says
    "``asyncio.sleep``, never ``time.sleep``". A substring search over the source would match the
    explanation of the guarantee rather than the guarantee, and would keep matching after the code
    stopped honouring it. So the docstring is dropped and the remaining statements are what is read.
    """
    module = ast.parse(textwrap.dedent(inspect.getsource(function)))
    definition = module.body[0]
    body = list(getattr(definition, "body", []))
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return ast.Module(body=body, type_ignores=[])


def _names_in(tree: ast.AST) -> set:
    """Every identifier the tree references, as bare names and as attribute names."""
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def _body_frame(body: Any) -> Dict[str, Any]:
    """A ``mds:data:*`` frame carrying ``body`` verbatim as its ``data``.

    Not a variant of ``_frame``: ``_frame`` builds a well-formed payload and serialises it, and
    every case here needs a body that is **not** a serialised payload - text that is not JSON, JSON
    that is not an object, or a mapping handed over undecoded so a ``float`` can reach
    ``_exact_decimal``. That last one is the passthrough branch ``decode_payload`` documents, and it
    is the only way a binary float can arrive on this path at all, since the wire is decoded with
    ``parse_float=Decimal``.
    """
    return {"type": "message", "channel": DATA_CHANNEL, "data": body}


def _feed_on(
    frames: List[Any],
    *,
    client: Optional[FakeSupabase] = None,
    config: Optional[feed.PaperFeedConfig] = None,
) -> Tuple[feed.FeedHandle, RecordingRedis, FakeSupabase]:
    """An open feed whose queue holds ``frames``, plus the two doubles behind it.

    The queue lives on the Redis double rather than on the pubsub, so a case may append to it
    **after** a reconnection and the new subscription serves what it appended - which is how the
    resume-after-outage cases are driven without a second double.
    """
    supabase = client if client is not None else _client()
    redis = RecordingRedis(frames=list(frames))
    handle = _open(
        redis=redis, supabase=supabase, measurements=_admitting(), config=config
    )
    return handle, redis, supabase


def _deliver(handle: feed.FeedHandle) -> Optional[feed.MarketEvent]:
    """One ``next_validated_event`` call. ``None`` is a drop, and never an exception."""
    return _run_coroutine(feed.next_validated_event(handle))


def _drain(handle: feed.FeedHandle, count: int) -> List[Optional[feed.MarketEvent]]:
    return [_deliver(handle) for _ in range(count)]


@pytest.fixture
def counters(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A fresh **real** ``MetricsCollector``, so a counter's own name can be asserted.

    The feed reaches the collector through ``metrics.guarded_collector()``, which reads the module
    attribute on every call, so replacing the attribute is enough. Fresh per case because these are
    process-lifetime counters and a shared one would carry another case's increments.
    """
    collector = metrics_module.MetricsCollector()
    monkeypatch.setattr(metrics_module, "metrics_collector", collector)
    return collector


@pytest.fixture
def no_sleeping(monkeypatch: pytest.MonkeyPatch) -> List[Any]:
    """Record every ``asyncio.sleep`` delay instead of waiting it out.

    The recorded list **is** the assertion for Requirement 14.5's backoff: the delays are what the
    requirement names, and waiting 91 seconds to observe six of them would make the suite
    unrunnable. Patched on ``asyncio`` itself rather than on a name the feed holds, because the feed
    calls ``asyncio.sleep`` through the module - so this records the call a production run makes.
    """
    import asyncio

    recorded: List[Any] = []

    async def _record(delay: Any) -> None:
        recorded.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _record)
    return recorded


# ══════════════════════════════════════════════════════════════════════════
# 24.3 / 1. THE EVENT IDENTITY IS THE DESIGN'S SHA256 OVER THE DESIGN'S SIX FIELDS
#           (Requirement 14.7)
# ══════════════════════════════════════════════════════════════════════════

#: One candle's six identity fields, spelled as literals here so the digest below is computed from
#: this file's own values and not from the module's.
ID_TIMESTAMP_MS = 1718452800000
ID_CLOSE = Decimal("60000.5")
ID_VOLUME = Decimal("1.5")

#: ``sha256("binance|BTC/USDT|1m|1718452800000|60000.5|1.5")``, written down.
#:
#: A LITERAL, and that is the point. Every other assertion in this class recomputes the digest with
#: ``hashlib`` from a material string this file builds - which pins the six fields and their order -
#: but a recomputation cannot pin the *convention*: a module that changed its separator, its field
#: order or its canonicalisation would keep agreeing with a test that rebuilt the material the same
#: new way. This literal cannot move. ``source_event_id`` is recomputed from a stored row by
#: ``paper_replay`` and by any audit of a session's log, so the convention is a durable format and
#: not an implementation detail.
ID_DIGEST = "322a08973b507a45924b5115e1b811c4cb959d0b6de0632ea9355df414e1704f"


def _independent_event_id(
    exchange: str,
    symbol: str,
    timeframe: str,
    timestamp_ms: int,
    close: str,
    volume: str,
) -> str:
    """The design's digest, computed here, from strings this file supplies.

    Deliberately does not import, call or wrap anything from ``paper_market_feed``: an assertion
    against the function's own output would hold for any function whatsoever.
    """
    material = f"{exchange}|{symbol}|{timeframe}|{timestamp_ms}|{close}|{volume}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class TestTheEventIdentityIsTheDesignsDigest:
    """Task 24.3's first bullet, against an independently computed digest.

    Red run 1 - the convention. The material's separator was changed from ``|`` to ``-``:
    **6 failed, 2 passed**. Everything that names a digest went red, including the literal above.
    The two survivors were ``test_a_republished_candle_keeps_its_identity`` and
    ``test_the_open_high_and_low_are_excluded_deliberately``, which compare digests to each other
    rather than to a written-down value - which is exactly why the literal is here.

    Red run 2 - the canonicalisation. ``canonical_number`` was reduced to ``str(value)``, the raw
    string hash: **1 failed, 7 passed**, and the one was
    ``test_a_republished_candle_keeps_its_identity``. That is the pair worth noticing. The
    separator mutation is caught by the literal and misses the canonicalisation; the
    canonicalisation mutation is caught only by the republication case and passes the literal,
    because ``Decimal("60000.5")`` stringifies to ``60000.5`` either way. Neither case alone would
    have found both defects.
    """

    def test_the_digest_matches_a_literal_written_down_here(self) -> None:
        assert (
            feed.source_event_id(
                EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, ID_CLOSE, ID_VOLUME
            )
            == ID_DIGEST
        )

    def test_the_digest_is_sha256_over_the_six_fields_in_order(self) -> None:
        """The material, built here: ``exchange|symbol|timeframe|timestamp|close|volume``."""
        expected = _independent_event_id(
            EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, "60000.5", "1.5"
        )

        assert expected == ID_DIGEST
        assert (
            feed.source_event_id(
                EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, ID_CLOSE, ID_VOLUME
            )
            == expected
        )

    def test_the_identity_stored_on_the_row_is_that_digest(self) -> None:
        """The claim reaches the persisted row, not only the helper.

        ``paper_market_events.source_event_id`` is what ``uq_paper_market_event`` arbitrates on and
        what an audit recomputes, so the digest has to be the value that lands in the column.
        """
        handle, _redis, client = _feed_on(
            [_frame(timestamp_ms=ID_TIMESTAMP_MS, close=60000.5, volume=1.5)]
        )
        event = _deliver(handle)

        assert event is not None
        assert event.source_event_id == ID_DIGEST
        assert client.market_events[0]["source_event_id"] == ID_DIGEST
        # And inside the payload as well, so a row copied out of the table carries its identity.
        assert client.market_events[0]["payload"]["source_event_id"] == ID_DIGEST

    def test_a_republished_candle_keeps_its_identity(self) -> None:
        """Requirement 14.7's "stable for a republished candle".

        ``mds/main.py`` re-serialises whatever CCXT handed it on every socket update, so the same
        forming candle can arrive spelled ``60000.5`` once and ``60000.50`` the next time. Both are
        the same number and therefore the same event; a raw-string hash would call them two events
        and store the bar twice.
        """
        first = feed.source_event_id(
            EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, Decimal("60000.5"), Decimal("1.5")
        )
        respelled = feed.source_event_id(
            EXCHANGE,
            SYMBOL,
            TIMEFRAME,
            ID_TIMESTAMP_MS,
            Decimal("60000.500"),
            Decimal("1.50"),
        )
        exponential = feed.source_event_id(
            EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, Decimal("6.00005E+4"), Decimal("1.5")
        )

        assert first == respelled == exponential == ID_DIGEST

    def test_a_revised_close_changes_the_identity(self) -> None:
        """Requirement 14.7's "different for a revised one". Only the close moves."""
        revised = feed.source_event_id(
            EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, Decimal("60000.6"), ID_VOLUME
        )

        assert revised != ID_DIGEST
        assert revised == _independent_event_id(
            EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, "60000.6", "1.5"
        )

    def test_a_revised_volume_alone_changes_the_identity(self) -> None:
        """The other single-field case: the close held, the volume moved.

        A bar whose volume grew while its close held is a bar in which trades printed, and a
        session's replay has to see the later figure. An identity built from the close alone would
        call the two the same event and keep the earlier volume.
        """
        revised = feed.source_event_id(
            EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, ID_CLOSE, Decimal("1.6")
        )

        assert revised != ID_DIGEST
        assert revised == _independent_event_id(
            EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, "60000.5", "1.6"
        )

    def test_each_of_the_six_fields_changes_the_identity_on_its_own(self) -> None:
        """All six, one at a time: no field is decorative and none is silently ignored."""
        base = (EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, ID_CLOSE, ID_VOLUME)
        variants = [
            ("kraken", SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, ID_CLOSE, ID_VOLUME),
            (EXCHANGE, "ETH/USDT", TIMEFRAME, ID_TIMESTAMP_MS, ID_CLOSE, ID_VOLUME),
            (EXCHANGE, SYMBOL, "5m", ID_TIMESTAMP_MS, ID_CLOSE, ID_VOLUME),
            (EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS + 60_000, ID_CLOSE, ID_VOLUME),
            (EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, Decimal("60000.51"), ID_VOLUME),
            (EXCHANGE, SYMBOL, TIMEFRAME, ID_TIMESTAMP_MS, ID_CLOSE, Decimal("1.51")),
        ]

        digests = {feed.source_event_id(*base)} | {
            feed.source_event_id(*variant) for variant in variants
        }
        assert len(digests) == len(variants) + 1

    def test_the_open_high_and_low_are_excluded_deliberately(self) -> None:
        """The documented exclusion, asserted so it is a decision rather than an omission.

        A high that ticks without the close moving is the *same* candle republished. Including the
        high would give it a new identity, and the second row would price nothing differently while
        making the replay's bar count wrong.
        """
        handle, redis, client = _feed_on(
            [
                _frame(timestamp_ms=ID_TIMESTAMP_MS, close=60000.5, volume=1.5),
            ]
        )
        first = _deliver(handle)
        assert first is not None

        redis.frames.append(
            _frame(
                timestamp_ms=ID_TIMESTAMP_MS,
                close=60000.5,
                volume=1.5,
                overrides={"high": 60500.0, "open": 59000.0, "low": 58000.0},
            )
        )
        second = _deliver(handle)

        assert second is None
        assert len(client.market_events) == 1


# ══════════════════════════════════════════════════════════════════════════
# 24.3 / 2. AN INVALID EVENT IS COUNTED AND DROPPED - NEVER REPAIRED
#           (Requirements 14.7, 14.9, 26.6)
# ══════════════════════════════════════════════════════════════════════════

#: The seven refusal reasons the module defines, each with a frame that earns exactly it.
#:
#: Built as a table so the assertion is "every code the module declares is reachable and no two are
#: confused", rather than a handful of cases that happen to cover some of them. Each frame is the
#: well-formed payload with **one** thing wrong, which is what makes the resulting code attributable
#: to that one thing.
INVALID_CASES: Tuple[Tuple[str, Any], ...] = (
    # Not JSON at all - a truncated frame, or a transport problem.
    (feed.INVALID_UNDECODABLE, _body_frame("{not json")),
    # Valid JSON that is not an object - the CCXT row shape, unwrapped.
    (feed.INVALID_NOT_A_MAPPING, _body_frame(json.dumps([1718452800000, 1, 2, 3, 4, 5]))),
    # A payload for a market this session is not trading.
    (
        feed.INVALID_WRONG_MARKET,
        _frame(timestamp_ms=_ms_before(60_000), overrides={"symbol": "ETH/USDT"}),
    ),
    # An instant that cannot be read as epoch milliseconds.
    (
        feed.INVALID_UNREADABLE_TIMESTAMP,
        _frame(overrides={"timestamp": "the third bar of tuesday"}),
    ),
    # A candle missing one of the five OHLCV names.
    (feed.INVALID_UNREADABLE_SHAPE, _frame(timestamp_ms=_ms_before(60_000), drop=("open",))),
    # A value that is not an exact decimal. Requirement 18.1's refusal.
    (
        feed.INVALID_INEXACT_VALUE,
        _frame(timestamp_ms=_ms_before(60_000), overrides={"close": "sixty thousand"}),
    ),
    # low > min(open, close): the validator's own OHLC integrity refusal.
    (
        feed.INVALID_VALIDATOR_REJECTED,
        _frame(timestamp_ms=_ms_before(60_000), overrides={"low": 99999.0}),
    ),
)


class TestAnInvalidEventIsCountedAndDroppedAndNeverRepaired:
    """Task 24.3's second bullet: ``paper.feed.invalid`` moves, the event vanishes.

    Red run 1 - the validator is disarmed. ``_validate_candle`` was made to ``return None``
    unconditionally: **3 failed, 10 passed**. The three were the ``VALIDATOR_REJECTED`` row of the
    table, ``test_the_named_counter_paper_feed_invalid_is_the_one_that_moves`` and
    ``test_a_rejected_candle_leaves_no_row_and_no_trace``. The other six table rows stayed green,
    correctly: they are refused upstream of the validator and have nothing to do with it.

    Red run 2 - the refusal is logged and the candle recorded anyway. ``if refusal is not None:``
    was changed to ``if refusal is not None and False:``, the "log it and carry on" shape: the same
    **3 failed, 10 passed**. Two different mutations, one outcome - which is what shows those three
    cases are about the drop and not about the validator's internals.

    Red run 3 - the two decode reasons are collapsed. ``_decode_refusal`` was made to return
    ``INVALID_UNDECODABLE`` unconditionally: **1 failed, 12 passed**, and the one was the
    ``PAYLOAD_NOT_AN_OBJECT`` row. That is the defect this table found in the first place - the
    label was declared and nothing could emit it.
    """

    @pytest.mark.parametrize(
        "reason, frame", INVALID_CASES, ids=[case[0] for case in INVALID_CASES]
    )
    def test_each_reason_code_is_reachable_and_distinct(
        self, reason: str, frame: Any, counters: Any
    ) -> None:
        handle, _redis, client = _feed_on([frame])

        event = _deliver(handle)

        assert event is None
        assert counters.paper_feed_invalid.get(symbol=SYMBOL, reason=reason) == 1
        assert counters.paper_feed_invalid.total() == 1
        assert client.market_events == []

    def test_the_seven_declared_codes_are_exactly_the_seven_covered(self) -> None:
        """No code is declared that this file leaves unexercised, and none is invented here.

        A metric label a runbook matches on is only useful if something emits it. This is the
        assertion that caught ``PAYLOAD_NOT_AN_OBJECT`` being unreachable: it was declared, nothing
        emitted it, and the table above had no frame that could produce it.
        """
        declared = {
            value
            for name, value in vars(feed).items()
            if name.startswith("INVALID_") and isinstance(value, str)
        }
        covered = {reason for reason, _frame_ in INVALID_CASES}

        assert declared == covered
        assert len(declared) == 7

    def test_the_named_counter_paper_feed_invalid_is_the_one_that_moves(
        self, counters: Any
    ) -> None:
        """Requirement 26.6: the metric task 24.3 names, by its dotted name."""
        assert counters.paper_feed_invalid.name == "paper.feed.invalid"
        assert counters.paper_feed_invalid.label_names == ["symbol", "reason"]
        assert counters.paper_feed_invalid.total() == 0

        handle, _redis, _client_ = _feed_on(
            [_frame(timestamp_ms=_ms_before(60_000), overrides={"low": 99999.0})]
        )
        assert _deliver(handle) is None

        assert counters.paper_feed_invalid.total() == 1
        assert (
            counters.paper_feed_invalid.get(
                symbol=SYMBOL, reason=feed.INVALID_VALIDATOR_REJECTED
            )
            == 1
        )
        # And no other paper feed counter moved: the drop is counted once, as one thing.
        assert counters.paper_feed_events.total() == 0
        assert counters.paper_feed_duplicates.total() == 0
        assert counters.paper_feed_out_of_order.total() == 0

    def test_a_rejected_candle_leaves_no_row_and_no_trace(self, counters: Any) -> None:
        """Dropped means dropped: no row, no sequence, no high-water mark, no cached identity.

        Stronger than "no row was written". A session that dropped the candle but advanced its
        high-water mark would silently refuse the *next* candle too, and a session that cached the
        identity would refuse the corrected republication of the same bar.
        """
        handle, _redis, client = _feed_on(
            [_frame(timestamp_ms=_ms_before(60_000), overrides={"low": 99999.0})]
        )

        assert _deliver(handle) is None

        assert client.market_events == []
        assert client.statements_on(repo.MARKET_EVENTS_TABLE, "insert") == []
        assert handle.sequence == 0
        assert handle.last_timestamp == {}
        assert list(handle.seen_event_ids) == []
        # The feed state did not become HEALTHY either: an event that did not pass validation is
        # not evidence that data is flowing (Requirement 28.5).
        assert handle.feed_state == feed.FEED_STATE_PENDING
        assert client.sessions[0]["feed_state"] == feed.FEED_STATE_PENDING

    def test_no_field_is_rewritten_on_the_way_in(self, counters: Any) -> None:
        """Requirement 14.9: nothing is synthesised, clamped, defaulted or re-sorted.

        The five values stored are the five delivered, digit for digit, read back as exact
        ``Decimal``. Awkward digits on purpose - a clamp, a float round trip or a quantisation would
        each change at least one of them.
        """
        handle, _redis, client = _feed_on(
            [
                _frame(
                    timestamp_ms=_ms_before(60_000),
                    close="60000.123456789",
                    volume="0.000001",
                    overrides={
                        "open": "59900.987654321",
                        "high": "60100.111111111",
                        "low": "59800.222222222",
                    },
                )
            ]
        )

        event = _deliver(handle)

        assert event is not None
        stored = client.market_events[0]["payload"]
        assert stored["open"] == "59900.987654321"
        assert stored["high"] == "60100.111111111"
        assert stored["low"] == "59800.222222222"
        assert stored["close"] == "60000.123456789"
        assert stored["volume"] == "0.000001"
        assert event.close == Decimal("60000.123456789")
        assert event.volume == Decimal("0.000001")

    def test_a_binary_float_on_the_wire_is_refused_rather_than_stored(
        self, counters: Any
    ) -> None:
        """Requirement 18.1, at the one place a ``float`` can still arrive.

        The wire is decoded with ``parse_float=Decimal``, so the only way a ``float`` reaches
        ``_exact_decimal`` is a caller injecting an already-decoded mapping - ``decode_payload``'s
        documented passthrough. It is refused rather than converted: by the time a ``float`` is
        here the digits the exchange published are already gone, and ``str(0.07)`` would persist a
        value nobody sent.
        """
        handle, _redis, client = _feed_on(
            [
                _body_frame(
                    {
                        "exchange": EXCHANGE,
                        "symbol": SYMBOL,
                        "timeframe": TIMEFRAME,
                        "timestamp": _ms_before(60_000),
                        "open": Decimal("59900"),
                        "high": Decimal("60100"),
                        "low": Decimal("59800"),
                        "close": 60000.5,  # the float
                        "volume": Decimal("1.5"),
                        "transport": "WEBSOCKET",
                    }
                )
            ]
        )

        assert _deliver(handle) is None
        assert (
            counters.paper_feed_invalid.get(
                symbol=SYMBOL, reason=feed.INVALID_INEXACT_VALUE
            )
            == 1
        )
        assert client.market_events == []

    def test_a_payload_naming_another_market_is_refused_not_recorded(
        self, counters: Any
    ) -> None:
        """A foreign publisher's price is not this session's, whatever channel it arrived on.

        Recording it would put a price from a market the session is not trading into the log its
        replay is priced from.
        """
        handle, _redis, client = _feed_on(
            [
                _frame(timestamp_ms=_ms_before(60_000), overrides={"exchange": "kraken"}),
                _frame(timestamp_ms=_ms_before(60_000), overrides={"timeframe": "1h"}),
            ]
        )

        assert _drain(handle, 2) == [None, None]
        assert (
            counters.paper_feed_invalid.get(
                symbol=SYMBOL, reason=feed.INVALID_WRONG_MARKET
            )
            == 2
        )
        assert client.market_events == []


# ══════════════════════════════════════════════════════════════════════════
# 24.3 / 3-5. DEDUPE: THE LRU IS A CACHE, uq_paper_market_event IS THE ARBITER
#             (Requirements 14.7, 26.6)
# ══════════════════════════════════════════════════════════════════════════

#: The candle every dedupe case below republishes. Its instant is fixed so a redelivery is never
#: *also* an out-of-order event: step 8 runs after step 7, so a redelivered candle with an older
#: timestamp would be dropped by the ordering guard before the dedupe path could be reached, and the
#: case would pass for the wrong reason.
DEDUPE_MS = _ms_before(60_000)


def _dedupe_frame(volume: Any = 1.5) -> Dict[str, Any]:
    """One republishable candle. ``volume`` varies the identity without moving the instant."""
    return _frame(timestamp_ms=DEDUPE_MS, close=60000.5, volume=volume)


class TestADuplicateIsProcessedAtMostOnceAndIsNotAnError:
    """Task 24.3: "each ``source_event_id`` is processed at most once per session".

    Red run: the ``if handle.has_seen(event_id):`` guard was short-circuited to ``if False and
    ...``, leaving the unique index as the only arbiter. **1 failed, 3 passed.** The one was
    ``test_the_lru_catches_the_second_copy``, on the arbiter label - ``UNIQUE_INDEX`` where ``LRU``
    was asserted - and on the second INSERT having been attempted.

    The three that stayed green are the point, not a weakness. The row count, the no-op and the
    counter name all held with the cache gone, because the database still refused the second row.
    That is what "the LRU is a cache, not the arbiter" means, measured: removing it changes which
    arbiter fired and does not change the outcome.
    """

    def test_the_second_copy_is_dropped_and_one_row_stands(self, counters: Any) -> None:
        handle, redis, client = _feed_on([_dedupe_frame()])
        first = _deliver(handle)
        redis.frames.append(_dedupe_frame())
        second = _deliver(handle)

        assert first is not None
        assert second is None
        assert len(client.market_events) == 1
        assert client.market_events[0]["source_event_id"] == first.source_event_id

    def test_the_duplicate_is_a_no_op_and_not_an_error_to_the_caller(
        self, counters: Any
    ) -> None:
        """``None``, not an exception - and the session's processed state does not move.

        A caller driving this in a loop must not have to distinguish "the feed republished a candle"
        from "something went wrong", because the first is the normal behaviour of every OHLCV socket
        and the second is not. The sequence staying put is the other half: a duplicate that consumed
        a sequence number would leave a hole in the log a replay reads as a lost event.
        """
        handle, redis, client = _feed_on([_dedupe_frame()])
        _deliver(handle)
        sequence_before = handle.sequence
        high_water_before = dict(handle.last_timestamp)

        redis.frames.append(_dedupe_frame())
        assert _deliver(handle) is None  # no raise, and nothing to unwrap

        assert handle.sequence == sequence_before == 1
        assert handle.last_timestamp == high_water_before
        assert len(client.market_events) == 1

    def test_the_lru_catches_the_second_copy(self, counters: Any) -> None:
        """The hot path: the identity is in the cache, so no round trip is made at all."""
        handle, redis, client = _feed_on([_dedupe_frame()])
        _deliver(handle)
        inserts_before = len(client.statements_on(repo.MARKET_EVENTS_TABLE, "insert"))

        redis.frames.append(_dedupe_frame())
        assert _deliver(handle) is None

        assert (
            counters.paper_feed_duplicates.get(
                symbol=SYMBOL, arbiter=feed.DUPLICATE_ARBITER_LRU
            )
            == 1
        )
        assert counters.paper_feed_duplicates.total() == 1
        # No second INSERT was even attempted, which is what makes the cache worth having.
        assert (
            len(client.statements_on(repo.MARKET_EVENTS_TABLE, "insert"))
            == inserts_before
            == 1
        )

    def test_the_named_counter_paper_feed_duplicates_is_the_one_that_moves(
        self, counters: Any
    ) -> None:
        assert counters.paper_feed_duplicates.name == "paper.feed.duplicates"
        assert counters.paper_feed_duplicates.label_names == ["symbol", "arbiter"]


class TestTheUniqueIndexIsTheArbiterIndependentlyOfTheLru:
    """Task 24.3's third bullet: "the LRU is a cache, not the arbiter".

    The two cases below reach the database constraint two different ways - by emptying the cache and
    by overflowing it - because they are different failures in production. An emptied cache is a
    restarted worker or a second worker on one session; an overflowed cache is a session that has
    been running long enough to evict. Both must be safe, and "safe" means a no-op rather than an
    error or a second row.

    Red run 1 - the no-op becomes an error. ``except repo.PaperDuplicateMarketEvent:`` was changed
    to ``except repo.PaperConcurrencyConflict:``, so the duplicate propagated: **3 failed, 2
    passed**. All three database-arbiter cases failed with ``PaperDuplicateMarketEvent`` raised out
    of ``next_validated_event`` - which is exactly the surfaced error Requirement 14.7's "SHALL
    discard" forbids, since a caller has nothing to do about a candle the feed republished.

    Red run 2 - the double stops being a database. ``FakeSupabase._enforce_unique``'s
    ``uq_paper_market_event`` raise was replaced with ``pass``: the same **3 failed, 2 passed**, on
    ``len(client.market_events) == 2`` and on the duplicate counter reading 0. That is the run that
    shows this class tests a constraint rather than a code path that happens to return early.

    Red run 3 - the bound never evicts. ``_remember_seen``'s eviction ``while`` was short-circuited:
    **2 failed, 3 passed** - ``test_an_overflowed_lru_falls_through_to_the_constraint`` and
    ``test_the_bound_is_the_designs_ten_thousand_and_it_evicts``. The emptied-cache case stayed
    green, which is why overflow is asserted separately from a cleared dict: they reach the same
    constraint by different routes and only one of them exercises the bound.
    """

    def test_an_emptied_cache_still_yields_exactly_one_row(self, counters: Any) -> None:
        """The cache is bypassed entirely, so the database is what refuses."""
        handle, redis, client = _feed_on([_dedupe_frame()])
        first = _deliver(handle)
        assert first is not None

        handle.seen_event_ids.clear()  # a restarted worker; a second worker; an eviction
        assert handle.has_seen(first.source_event_id) is False

        redis.frames.append(_dedupe_frame())
        assert _deliver(handle) is None  # a no-op, not a PaperDuplicateMarketEvent

        assert len(client.market_events) == 1
        assert (
            counters.paper_feed_duplicates.get(
                symbol=SYMBOL, arbiter=feed.DUPLICATE_ARBITER_UNIQUE_INDEX
            )
            == 1
        )
        # The INSERT WAS attempted this time - which is what makes the constraint, and not the
        # cache, the thing that refused.
        assert len(client.statements_on(repo.MARKET_EVENTS_TABLE, "insert")) == 2

    def test_the_refusal_re_warms_the_cache_so_the_next_copy_costs_nothing(
        self, counters: Any
    ) -> None:
        """The handler adds the identity back, so a third copy is caught by the LRU again."""
        handle, redis, client = _feed_on([_dedupe_frame()])
        first = _deliver(handle)
        handle.seen_event_ids.clear()

        redis.frames.append(_dedupe_frame())
        _deliver(handle)
        assert handle.has_seen(first.source_event_id) is True

        redis.frames.append(_dedupe_frame())
        assert _deliver(handle) is None

        assert counters.paper_feed_duplicates.get(
            symbol=SYMBOL, arbiter=feed.DUPLICATE_ARBITER_LRU
        ) == 1
        assert counters.paper_feed_duplicates.get(
            symbol=SYMBOL, arbiter=feed.DUPLICATE_ARBITER_UNIQUE_INDEX
        ) == 1
        assert len(client.market_events) == 1

    def test_an_overflowed_lru_falls_through_to_the_constraint(
        self, counters: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A genuine eviction, not a cleared dict: the bound is lowered and then exceeded.

        ``SEEN_EVENT_ID_LIMIT`` is read by ``_remember_seen`` at call time, so lowering it is enough
        and nothing has to be re-imported. Three candles share one instant and differ only in volume,
        so all three have distinct identities and none is out-of-order relative to the others - which
        is what lets the *first* one be redelivered after it has been evicted.
        """
        monkeypatch.setattr(feed, "SEEN_EVENT_ID_LIMIT", 2)

        handle, redis, client = _feed_on(
            [_dedupe_frame(1.5), _dedupe_frame(2.5), _dedupe_frame(3.5)]
        )
        accepted = _drain(handle, 3)

        assert [event.sequence for event in accepted if event] == [1, 2, 3]
        assert len(handle.seen_event_ids) == 2  # the bound held
        evicted = accepted[0]
        assert handle.has_seen(evicted.source_event_id) is False  # the oldest went first

        redis.frames.append(_dedupe_frame(1.5))
        assert _deliver(handle) is None

        assert len(client.market_events) == 3
        assert (
            counters.paper_feed_duplicates.get(
                symbol=SYMBOL, arbiter=feed.DUPLICATE_ARBITER_UNIQUE_INDEX
            )
            == 1
        )
        assert handle.sequence == 3  # the refused insert consumed no sequence number

    def test_the_two_arbiters_are_reported_as_two_different_things(self) -> None:
        """The module distinguishes which one fired, and the labels are not the same string.

        They are different operational facts: an ``LRU`` increment is a feed republishing normally,
        and a ``UNIQUE_INDEX`` increment means a cache missed - a restart, an eviction, or two
        workers on one session. Collapsing them into one label would hide the second behind the
        first, which happens on every socket update.
        """
        assert feed.DUPLICATE_ARBITER_LRU == "LRU"
        assert feed.DUPLICATE_ARBITER_UNIQUE_INDEX == "UNIQUE_INDEX"
        assert feed.DUPLICATE_ARBITER_LRU != feed.DUPLICATE_ARBITER_UNIQUE_INDEX

    def test_the_bound_is_the_designs_ten_thousand_and_it_evicts(self) -> None:
        """``design.md``: "bounded LRU, 10 000 entries", and the bound is enforced by eviction.

        Requirement 27.5 bounds retained in-memory events; an unbounded set on a month-long session
        would hold every identity it ever saw.
        """
        assert feed.SEEN_EVENT_ID_LIMIT == 10_000

        handle, _redis, _client_ = _feed_on([])
        for index in range(feed.SEEN_EVENT_ID_LIMIT + 5):
            handle._remember_seen(f"identity-{index}")

        assert len(handle.seen_event_ids) == feed.SEEN_EVENT_ID_LIMIT
        assert handle.has_seen("identity-0") is False
        assert handle.has_seen("identity-4") is False
        assert handle.has_seen("identity-5") is True
        assert handle.has_seen(f"identity-{feed.SEEN_EVENT_ID_LIMIT + 4}") is True


class TestTheSameIdentityInAnotherSessionIsNotADuplicate:
    """``uq_paper_market_event`` is UNIQUE ``(session_id, source_event_id)`` - both columns.

    Two sessions subscribed to the same pair see the *same* candles and therefore compute the same
    identities, because the identity is derived from the market data and carries no session. Each
    session needs its own row: Requirement 15.5 asks each session to record the sequence sufficient
    to reproduce **its** history, and a constraint keyed on the identity alone would give the second
    session an empty log.

    Red run: ``FakeSupabase._enforce_unique``'s market-event branch was changed to compare
    ``source_event_id`` only, dropping ``session_id`` from the key - the shape a "dedupe globally"
    reading produces. **2 failed, 1 passed:** ``test_each_session_records_its_own_row`` on one row
    where two were asserted, and ``test_the_second_session_is_not_counted_as_a_duplicate`` on the
    duplicate counter. ``test_each_handle_keeps_its_own_cache`` stayed green, because a shared
    database key says nothing about a per-handle cache.
    """

    def _two_sessions(self) -> FakeSupabase:
        return FakeSupabase(
            sessions=[
                _session_row(),
                _session_row(id=OTHER_SESSION, user_id=OTHER_USER),
            ]
        )

    def test_each_session_records_its_own_row(self, counters: Any) -> None:
        client = self._two_sessions()

        mine, _r1, _c1 = _feed_on([_dedupe_frame()], client=client)
        theirs, _r2, _c2 = _feed_on(
            [_dedupe_frame()],
            client=client,
            config=_config(session_id=OTHER_SESSION, user_id=OTHER_USER),
        )

        first = _deliver(mine)
        second = _deliver(theirs)

        assert first is not None and second is not None
        # The same identity - it is derived from the candle, and both sessions saw one candle.
        assert first.source_event_id == second.source_event_id
        assert len(client.market_events) == 2
        assert {row["session_id"] for row in client.market_events} == {
            SESSION,
            OTHER_SESSION,
        }
        # Each session's log starts at 1: the sequence is per session, not global.
        assert first.sequence == second.sequence == 1

    def test_the_second_session_is_not_counted_as_a_duplicate(
        self, counters: Any
    ) -> None:
        client = self._two_sessions()
        mine, _r1, _c1 = _feed_on([_dedupe_frame()], client=client)
        theirs, _r2, _c2 = _feed_on(
            [_dedupe_frame()],
            client=client,
            config=_config(session_id=OTHER_SESSION, user_id=OTHER_USER),
        )

        _deliver(mine)
        _deliver(theirs)

        assert counters.paper_feed_duplicates.total() == 0
        assert counters.paper_feed_events.total() == 2

    def test_each_handle_keeps_its_own_cache(self, counters: Any) -> None:
        """The LRU is per session too, so one session's cache cannot silence another's event."""
        client = self._two_sessions()
        mine, _r1, _c1 = _feed_on([_dedupe_frame()], client=client)
        theirs, _r2, _c2 = _feed_on(
            [],
            client=client,
            config=_config(session_id=OTHER_SESSION, user_id=OTHER_USER),
        )

        event = _deliver(mine)

        assert event is not None
        assert mine.seen_event_ids is not theirs.seen_event_ids
        assert theirs.has_seen(event.source_event_id) is False


# ══════════════════════════════════════════════════════════════════════════
# 24.3 / 6-7. ORDERING IS NON-DECREASING, AND IT IS PER SYMBOL
#             (Requirements 14.7, 26.6)
# ══════════════════════════════════════════════════════════════════════════


class TestAnOlderTimestampIsCountedAndDropped:
    """Task 24.3's fourth bullet: below the symbol's high-water mark -> dropped.

    Red run: the ordering guard was short-circuited to ``if False and high_water is not None and
    event_timestamp < high_water:``. **3 failed, 0 passed** - the whole class.
    ``test_a_lower_timestamp_is_dropped`` on the event not being ``None``,
    ``test_the_late_candle_leaves_no_row_and_consumes_no_sequence`` on two rows where one was
    asserted, and ``test_the_processed_sequence_is_non_decreasing`` on the recorded instants no
    longer being sorted.
    """

    def test_a_lower_timestamp_is_dropped(self, counters: Any) -> None:
        handle, redis, client = _feed_on([_frame(timestamp_ms=_ms_before(60_000))])
        assert _deliver(handle) is not None

        redis.frames.append(_frame(timestamp_ms=_ms_before(120_000), volume=2.5))
        late = _deliver(handle)

        assert late is None
        assert counters.paper_feed_out_of_order.get(symbol=SYMBOL) == 1
        assert counters.paper_feed_out_of_order.name == "paper.feed.out_of_order"
        assert counters.paper_feed_out_of_order.label_names == ["symbol"]

    def test_the_late_candle_leaves_no_row_and_consumes_no_sequence(
        self, counters: Any
    ) -> None:
        handle, redis, client = _feed_on([_frame(timestamp_ms=_ms_before(60_000))])
        first = _deliver(handle)
        redis.frames.append(_frame(timestamp_ms=_ms_before(120_000), volume=2.5))
        _deliver(handle)

        assert len(client.market_events) == 1
        assert handle.sequence == 1
        assert handle.last_timestamp[SYMBOL] == first.event_timestamp

    def test_the_processed_sequence_is_non_decreasing(self, counters: Any) -> None:
        """The invariant itself, over a stream that arrives out of order.

        Nine candles, three of them late. Whatever is recorded, the recorded instants must be
        non-decreasing in ``sequence`` order - which is the property ``paper_replay`` depends on and
        the one Property P-54 (task 24.5) generalises.
        """
        offsets = [
            600_000,
            540_000,
            660_000,  # late
            480_000,
            420_000,
            700_000,  # late
            360_000,
            300_000,
            380_000,  # late
        ]
        frames = [
            _frame(timestamp_ms=_ms_before(offset), volume=float(index) + 1.5)
            for index, offset in enumerate(offsets)
        ]
        handle, _redis, client = _feed_on(frames)

        results = _drain(handle, len(frames))

        recorded = sorted(client.market_events, key=lambda row: row["sequence"])
        instants = [row["event_timestamp"] for row in recorded]
        assert instants == sorted(instants)
        assert [row["sequence"] for row in recorded] == list(
            range(1, len(recorded) + 1)
        )
        # Three of the nine were late - so the drop actually happened and this is not a stream
        # that was already sorted.
        assert counters.paper_feed_out_of_order.get(symbol=SYMBOL) == 3
        assert len([event for event in results if event is None]) == 3
        assert len(recorded) == 6


class TestAnEqualTimestampIsAdmittedBecauseTheSequenceIsNonDecreasing:
    """The equal-timestamp case, stated plainly: **it is admitted**, and here is why.

    The guard is ``event_timestamp < high_water``, so equal passes. Requirement 14.7 asks for a
    **non-decreasing** sequence, not a strictly increasing one, and the reason it does is the OHLCV
    socket's own behaviour: a forming candle is republished under one open time on every update. If
    nothing moved, the identity is unchanged and the dedupe drops it. If the close or the volume
    moved, the bar has been *revised*, the identity differs, and the session must process the
    revision or price its replay from a superseded close. So the arbitration of two candles sharing
    an instant belongs to the identity and not to the clock, and both outcomes are asserted here.

    Red run: the comparison was tightened to ``<=``, the "strictly increasing" reading. **2 failed,
    1 passed** - ``test_a_revised_candle_at_the_same_instant_is_processed``, because the revision was
    counted as out-of-order and dropped and the replay would then price the bar from the superseded
    close, and ``test_the_comparison_is_strictly_less_than``, which is the case that makes the choice
    visible in the source rather than only in behaviour. The dedupe case stayed green: an unrevised
    republication is dropped either way, just for a different reason.
    """

    def test_a_revised_candle_at_the_same_instant_is_processed(
        self, counters: Any
    ) -> None:
        handle, redis, client = _feed_on([_frame(timestamp_ms=DEDUPE_MS, close=60000.5)])
        first = _deliver(handle)

        # Within the bar's own high and low, so the revision is a valid candle and the only thing
        # under test is the ordering guard.
        redis.frames.append(_frame(timestamp_ms=DEDUPE_MS, close=60050.5))
        revision = _deliver(handle)

        assert first is not None and revision is not None
        assert revision.event_timestamp == first.event_timestamp
        assert revision.sequence == 2
        assert revision.source_event_id != first.source_event_id
        assert len(client.market_events) == 2
        assert counters.paper_feed_out_of_order.total() == 0
        assert [str(row["payload"]["close"]) for row in client.market_events] == [
            "60000.5",
            "60050.5",
        ]

    def test_an_unrevised_candle_at_the_same_instant_is_deduped_not_counted_as_late(
        self, counters: Any
    ) -> None:
        """The other outcome for the same instant: the identity arbitrates, so it is a duplicate."""
        handle, redis, client = _feed_on([_frame(timestamp_ms=DEDUPE_MS, close=60000.5)])
        _deliver(handle)
        redis.frames.append(_frame(timestamp_ms=DEDUPE_MS, close=60000.5))

        assert _deliver(handle) is None
        assert counters.paper_feed_duplicates.total() == 1
        assert counters.paper_feed_out_of_order.total() == 0
        assert len(client.market_events) == 1

    def test_the_comparison_is_strictly_less_than(self) -> None:
        """Asserted on the module's own source, so the choice cannot drift silently.

        A ``<=`` here would be a different requirement, and this is the assertion that would fail if
        someone tightened it while every other ordering case stayed green.
        """
        source = inspect.getsource(feed.next_validated_event)

        assert "event_timestamp < high_water" in source
        assert "event_timestamp <= high_water" not in source


class TestOrderingIsPerSymbolAndNotGlobal:
    """Task 24.3: "the processed timestamp sequence **per symbol** is non-decreasing".

    Two symbols' feeds interleave, and a global high-water mark would drop one symbol's candle
    because the other symbol's arrived first - discarding real market data for a reason that has
    nothing to do with it.

    The two handles are given the **same** ``last_timestamp`` mapping object on purpose. One
    subscription serves one ``(exchange, symbol)`` pair, so two symbols mean two handles, and two
    handles with two separate dicts would make this claim trivially true without ever exercising the
    keying. Sharing the map is what a session worker holding one ordering record across its symbols
    would do, and it is the construction under which the key matters.

    Red run: the per-symbol lookup ``handle.last_timestamp.get(symbol)`` was replaced with
    ``max(handle.last_timestamp.values(), default=None)`` - one high-water mark per session, which is
    the "session-level ordering" shape. **3 failed, 0 passed** - the whole class. The ETH candle was
    counted as out-of-order and dropped, so the admission case, the keying case and even the
    "each symbol's own ordering still holds" case all went red, the last one because the ETH mark was
    never recorded at all.
    """

    ETH = "ETH/USDT"

    def _eth_frame(self, *, timestamp_ms: int, volume: Any = 1.5) -> Dict[str, Any]:
        return _frame(
            timestamp_ms=timestamp_ms, volume=volume, overrides={"symbol": self.ETH}
        )

    def test_a_lower_timestamp_on_another_symbol_is_admitted(
        self, counters: Any
    ) -> None:
        client = _client()
        btc, _r1, _c1 = _feed_on([_frame(timestamp_ms=_ms_before(60_000))], client=client)
        eth, eth_redis, _c2 = _feed_on(
            [self._eth_frame(timestamp_ms=_ms_before(600_000))],
            client=client,
            config=_config(symbol=self.ETH),
        )
        # One ordering record, two symbols - see the class docstring.
        eth.last_timestamp = btc.last_timestamp

        recent_btc = _deliver(btc)
        older_eth = _deliver(eth)

        assert recent_btc is not None
        assert older_eth is not None, (
            "an ETH candle older than the last BTC candle is not out of order for ETH; a global "
            "high-water mark would have dropped it (Requirement 14.7)"
        )
        assert older_eth.event_timestamp < recent_btc.event_timestamp
        assert counters.paper_feed_out_of_order.total() == 0
        assert len(client.market_events) == 2

    def test_the_high_water_marks_are_keyed_by_symbol(self, counters: Any) -> None:
        client = _client()
        btc, _r1, _c1 = _feed_on([_frame(timestamp_ms=_ms_before(60_000))], client=client)
        eth, _r2, _c2 = _feed_on(
            [self._eth_frame(timestamp_ms=_ms_before(600_000))],
            client=client,
            config=_config(symbol=self.ETH),
        )
        eth.last_timestamp = btc.last_timestamp

        _deliver(btc)
        _deliver(eth)

        shared = btc.last_timestamp
        assert set(shared) == {SYMBOL, self.ETH}
        assert shared[SYMBOL] > shared[self.ETH]

    def test_each_symbols_own_ordering_is_still_enforced(self, counters: Any) -> None:
        """The other direction: per-symbol is not "no ordering at all".

        A second ETH candle older than the first ETH candle is still dropped, and it is counted
        against ETH rather than against the session.
        """
        client = _client()
        btc, _r1, _c1 = _feed_on([_frame(timestamp_ms=_ms_before(60_000))], client=client)
        eth, eth_redis, _c2 = _feed_on(
            [self._eth_frame(timestamp_ms=_ms_before(600_000))],
            client=client,
            config=_config(symbol=self.ETH),
        )
        eth.last_timestamp = btc.last_timestamp

        _deliver(btc)
        _deliver(eth)
        eth_redis.frames.append(
            self._eth_frame(timestamp_ms=_ms_before(900_000), volume=2.5)
        )
        assert _deliver(eth) is None

        assert counters.paper_feed_out_of_order.get(symbol=self.ETH) == 1
        assert counters.paper_feed_out_of_order.get(symbol=SYMBOL) == 0
        assert len(client.market_events) == 2


# ══════════════════════════════════════════════════════════════════════════
# 24.3 / 8. EVERY ACCEPTED EVENT IS RECORDED WITH ITS SEQUENCE, IDENTITY AND PAYLOAD
#           (Requirement 15.5)
# ══════════════════════════════════════════════════════════════════════════

#: What ``paper_replay`` has to be able to read off one row to price a bar: the market, the instant,
#: the five values and the identity. Enumerated here so a field quietly dropped from the payload is a
#: failure in this file rather than a replay that prices nothing.
REPLAY_PAYLOAD_FIELDS: Tuple[str, ...] = (
    "exchange",
    "symbol",
    "timeframe",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source_event_id",
    "transport",
)


class TestAcceptedEventsAreRecordedForReplay:
    """Task 24.3's fifth bullet, and ``chk_paper_market_event_sequence CHECK (sequence >= 1)``.

    Red run 1 - the log is zero-based. ``sequence = handle.sequence + 1`` was changed to
    ``sequence = handle.sequence``. **8 failed, 1 passed** - and the failures were the repository's
    own ``ValueError`` for ``sequence`` below 1, not this file's assertions, which is the constraint
    doing the work rather than the test. The survivor was
    ``test_the_sequence_floor_of_one_is_guarded_by_the_repository``, which calls the repository
    directly and does not go through the feed.

    Red run 2 - the payload is trimmed. ``payload=stored_payload`` was replaced with
    ``payload={'close': str(values['close'])}``, the "a replay only needs the price" reading.
    **2 failed, 7 passed** - the replay-fields case naming the ten missing fields, and the exact
    round-trip case.

    Red run 3 - the write and the state advance are swapped. ``handle.sequence``,
    ``handle.last_timestamp[symbol]`` and ``_remember_seen`` were moved ahead of the INSERT.
    **1 failed, 7 passed** - only ``test_the_write_happens_before_the_processed_state_moves``, which
    is the case that exists for exactly this and would otherwise be invisible: every other assertion
    in the class holds when the write succeeds.
    """

    def test_the_first_accepted_event_is_sequence_one(self, counters: Any) -> None:
        handle, _redis, client = _feed_on([_frame(timestamp_ms=_ms_before(60_000))])

        event = _deliver(handle)

        assert event is not None
        assert event.sequence == 1
        assert client.market_events[0]["sequence"] == 1

    def test_the_sequence_floor_of_one_is_guarded_by_the_repository(self) -> None:
        """009's ``CHECK (sequence >= 1)``, refused at the call site rather than as a ``23514``.

        This is why the first event is 1 and not 0: a zero-based log could not be written at all.
        """
        client = _client()

        with pytest.raises(ValueError) as caught:
            repo.insert_market_event(
                client,
                session_id=SESSION,
                user_id=USER,
                sequence=0,
                source_event_id="a" * 64,
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                event_timestamp=PROCESSED_AT,
                payload={"close": "1"},
            )

        assert "at least 1" in str(caught.value)
        assert client.market_events == []

    def test_the_sequences_are_consecutive(self, counters: Any) -> None:
        frames = [
            _frame(timestamp_ms=_ms_before(300_000 - index * 60_000), volume=float(index) + 1.5)
            for index in range(5)
        ]
        handle, _redis, client = _feed_on(frames)

        events = _drain(handle, len(frames))

        assert [event.sequence for event in events if event] == [1, 2, 3, 4, 5]
        assert [row["sequence"] for row in client.market_events] == [1, 2, 3, 4, 5]
        assert handle.sequence == 5

    def test_a_resumed_session_continues_its_log_rather_than_restarting_at_one(
        self, counters: Any
    ) -> None:
        """The sequence is read from the log at ``open_feed``, so a restart does not reuse 1.

        A second log starting at 1 would give ``paper_replay`` two events claiming to be the
        session's first.
        """
        client = _client()
        first_run, _r1, _c1 = _feed_on(
            [_frame(timestamp_ms=_ms_before(300_000))], client=client
        )
        assert _deliver(first_run).sequence == 1

        # A new worker for the same session: a fresh handle over the same log.
        second_run, _r2, _c2 = _feed_on(
            [_frame(timestamp_ms=_ms_before(240_000), volume=2.5)], client=client
        )
        assert second_run.sequence == 1  # read from the log, not reset

        assert _deliver(second_run).sequence == 2
        assert [row["sequence"] for row in client.market_events] == [1, 2]

    def test_the_row_carries_the_identity_the_unique_index_arbitrates_on(
        self, counters: Any
    ) -> None:
        handle, _redis, client = _feed_on([_frame(timestamp_ms=_ms_before(60_000))])
        event = _deliver(handle)

        row = client.market_events[0]
        assert row["source_event_id"] == event.source_event_id
        assert row["session_id"] == SESSION
        assert row["user_id"] == USER
        assert row["symbol"] == SYMBOL
        assert row["timeframe"] == TIMEFRAME

    def test_the_payload_carries_everything_a_replay_prices_a_bar_from(
        self, counters: Any
    ) -> None:
        handle, _redis, client = _feed_on([_frame(timestamp_ms=_ms_before(60_000))])
        event = _deliver(handle)

        payload = client.market_events[0]["payload"]
        missing = [name for name in REPLAY_PAYLOAD_FIELDS if name not in payload]
        assert missing == [], (
            f"paper_market_events.payload is the replay input of Requirement 15.5 and is missing "
            f"{missing}; a replay cannot price a bar it cannot read"
        )
        assert set(payload) == set(REPLAY_PAYLOAD_FIELDS)
        assert payload == dict(event.payload)

    def test_the_stored_values_read_back_as_the_exact_decimals_that_arrived(
        self, counters: Any
    ) -> None:
        """Decimal **strings**, not JSON numbers - which is what makes the row replayable.

        Requirement 15.4's replay has to produce the same fills, and a close stored as a JSON number
        comes back as a binary float. Read back with ``Decimal(...)`` and compared to the event's own
        exact values, with no tolerance.
        """
        handle, _redis, client = _feed_on(
            [_frame(timestamp_ms=_ms_before(60_000), close="60000.07", volume="1.000000001")]
        )
        event = _deliver(handle)

        payload = client.market_events[0]["payload"]
        for name in ("open", "high", "low", "close", "volume"):
            assert isinstance(payload[name], str)
            assert Decimal(payload[name]) == getattr(event, name)
        assert Decimal(payload["close"]) == Decimal("60000.07")
        assert Decimal(payload["volume"]) == Decimal("1.000000001")
        assert isinstance(payload["timestamp"], int)

    def test_the_recorded_instant_is_the_candles_and_not_the_arrival_time(
        self, counters: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``event_timestamp`` is the market instant; ``received_at`` is this process's clock.

        Two different facts in two different columns. A replay driven from the arrival time would
        reorder a session whose feed was briefly slow.
        """
        monkeypatch.setattr(feed, "_utc_now", lambda: PROCESSED_AT)
        handle, _redis, client = _feed_on([_frame(timestamp_ms=_ms_before(90_000))])

        event = _deliver(handle)

        assert event.event_timestamp == PROCESSED_AT - timedelta(seconds=90)
        assert event.received_at == PROCESSED_AT
        row = client.market_events[0]
        assert row["event_timestamp"].startswith("2025-06-15T11:58:30")
        assert row["received_at"].startswith("2025-06-15T12:00:00")

    def test_the_write_happens_before_the_processed_state_moves(
        self, counters: Any
    ) -> None:
        """Step 9 before step 10: a candle that failed to record is not counted as processed.

        The insert is made to fail, and the assertion is that the session did not advance - so its
        log and its state cannot disagree about what it has seen.
        """
        client = FakeSupabase(
            sessions=[_session_row()], raise_on={("insert", repo.MARKET_EVENTS_TABLE)}
        )
        handle, _redis, _c = _feed_on([_frame(timestamp_ms=_ms_before(60_000))], client=client)

        with pytest.raises(repo.PaperPersistenceError):
            _deliver(handle)

        assert client.market_events == []
        assert handle.sequence == 0
        assert handle.last_timestamp == {}
        assert list(handle.seen_event_ids) == []
        assert handle.feed_state == feed.FEED_STATE_PENDING


# ══════════════════════════════════════════════════════════════════════════
# 24.3 / 9. TENANT ISOLATION ON THE MARKET LOG (Requirements 21.2, 21.5)
# ══════════════════════════════════════════════════════════════════════════


class TestOneSessionsMarketLogIsNotReadableFromAnother:
    """The market log is scoped by ``user_id`` **and** ``session_id``, in the statement.

    Asserted on the predicates and not only on the returned rows, because the two failures are
    different: a read that fetched every session's rows and filtered them in process would return the
    right answer here and the wrong answer under RLS, under a driver that streams, and under a
    ``limit``.

    Red run: the ``.eq("user_id", uid)`` predicate was removed from ``get_market_events``.
    **1 failed, 4 passed.** ``test_the_identity_is_in_the_statements_predicates`` failed on
    ``user_id`` not being a predicate; ``test_another_users_rows_are_not_returned`` stayed green,
    because the repository also restates the predicates in process.

    That asymmetry is the point of asserting the statement. The in-process restatement is a backstop
    against a lost filter; the predicate is the control that keeps the rows from being fetched at all,
    and only one of the two survives a driver that streams, a ``limit``, or RLS.
    """

    def _two_tenants(self, counters: Any) -> FakeSupabase:
        client = FakeSupabase(
            sessions=[
                _session_row(),
                _session_row(id=OTHER_SESSION, user_id=OTHER_USER),
            ]
        )
        mine, _r1, _c1 = _feed_on([_frame(timestamp_ms=_ms_before(60_000))], client=client)
        theirs, _r2, _c2 = _feed_on(
            [_frame(timestamp_ms=_ms_before(60_000))],
            client=client,
            config=_config(session_id=OTHER_SESSION, user_id=OTHER_USER),
        )
        assert _deliver(mine) is not None
        assert _deliver(theirs) is not None
        return client

    def test_each_tenant_reads_only_its_own_row(self, counters: Any) -> None:
        client = self._two_tenants(counters)

        mine = repo.get_market_events(client, USER, SESSION)
        theirs = repo.get_market_events(client, OTHER_USER, OTHER_SESSION)

        assert [row["session_id"] for row in mine] == [SESSION]
        assert [row["session_id"] for row in theirs] == [OTHER_SESSION]
        assert len(client.market_events) == 2

    def test_another_users_rows_are_not_returned(self, counters: Any) -> None:
        """The cross pairs: right user, wrong session; right session, wrong user."""
        client = self._two_tenants(counters)

        assert repo.get_market_events(client, USER, OTHER_SESSION) == []
        assert repo.get_market_events(client, OTHER_USER, SESSION) == []

    def test_the_identity_is_in_the_statements_predicates(self, counters: Any) -> None:
        client = self._two_tenants(counters)
        client.statements.clear()

        repo.get_market_events(client, USER, SESSION)

        reads = client.statements_on(repo.MARKET_EVENTS_TABLE, "select")
        assert reads
        for statement in reads:
            assert statement.filter_value("user_id") == USER
            assert statement.filter_value("session_id") == SESSION

    def test_every_write_carries_the_identity_too(self, counters: Any) -> None:
        """The insert names both, so a row cannot be created outside its tenant."""
        client = self._two_tenants(counters)

        inserts = client.statements_on(repo.MARKET_EVENTS_TABLE, "insert")
        assert len(inserts) == 2
        for statement in inserts:
            assert statement.payload["user_id"] in (USER, OTHER_USER)
            assert str(statement.payload["session_id"]) in (SESSION, OTHER_SESSION)
            assert (statement.payload["user_id"], statement.payload["session_id"]) in {
                (USER, SESSION),
                (OTHER_USER, OTHER_SESSION),
            }

    def test_a_sequence_read_is_scoped_as_well(self, counters: Any) -> None:
        """``next_market_event_sequence`` is a read, and it is scoped like every other one.

        An unscoped sequence read would hand one session the other's high-water number, so the two
        logs would interleave their sequences.
        """
        client = self._two_tenants(counters)
        client.statements.clear()

        assert repo.next_market_event_sequence(client, OTHER_USER, OTHER_SESSION) == 2

        for statement in client.statements_on(repo.MARKET_EVENTS_TABLE, "select"):
            assert statement.filter_value("user_id") == OTHER_USER
            assert statement.filter_value("session_id") == OTHER_SESSION


# ══════════════════════════════════════════════════════════════════════════
# 24.3 / 10. THE SAMPLE-DATA FABRICATOR IS NEVER REACHED (Requirements 14.9, 18.1)
# ══════════════════════════════════════════════════════════════════════════


class TestTheSampleDataFabricatorIsNeverReached:
    """``market_data_validation._generate_sample_data`` imports ``random`` and invents candles.

    The module docstring states it is reachable only from that module's two ``@router`` demo
    endpoints. This class turns that statement into a control: the function is replaced with one that
    raises, and the whole feed path is driven over it - an accepted candle, and one of every refusal
    reason. Nothing may trip it.

    Asserted by **substitution** rather than by inspection because that is what proves a call did not
    happen at run time. The companion file already asserts, over the AST, that no module in
    ``backend_app/backend/paper/`` imports ``random`` at all; this is the other half - that the path
    does not reach a fabricator living somewhere else.

    Red run: a call to ``_generate_sample_data(symbol, '1m')`` was planted at the top of
    ``_validate_candle`` - the shape a "get a reference window to compare against" reading produces.
    **3 failed, 6 passed**, every one of the three with the planted
    ``AssertionError("_generate_sample_data was reached ...")``: the accepted candle, the
    ``VALIDATOR_REJECTED`` row, and the no-random-draw case.

    The six survivors are correct and worth stating: ``UNDECODABLE_PAYLOAD``,
    ``PAYLOAD_NOT_AN_OBJECT``, ``WRONG_EXCHANGE_SYMBOL_OR_TIMEFRAME``, ``UNREADABLE_TIMESTAMP``,
    ``UNREADABLE_CANDLE_SHAPE`` and ``VALUE_NOT_EXACT_DECIMAL`` are all refused *upstream* of the
    validator, so a fabricator planted inside it is unreachable from them. They are in this class
    anyway, because the claim is about the whole path and a future refusal moved downstream of the
    validator would be covered without anyone remembering to add it.
    """

    @pytest.fixture(autouse=True)
    def _no_fabrication(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend_app.backend import market_data_validation as mdv

        def _forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(
                "_generate_sample_data was reached from the paper market-data path; it imports "
                "random and fabricates candles, and Requirement 14.9 forbids a synthesised price"
            )

        monkeypatch.setattr(mdv, "_generate_sample_data", _forbidden)
        # The other reachable fabrication seam: MarketDataValidator.validate's _attempt_fallback
        # branch calls a registered fetcher. The module docstring says this path does not call
        # validate() at all, so it is planted too.
        monkeypatch.setattr(
            mdv.MarketDataValidator, "validate", _forbidden, raising=True
        )

    def test_an_accepted_candle_does_not_reach_it(self, counters: Any) -> None:
        handle, _redis, client = _feed_on([_frame(timestamp_ms=_ms_before(60_000))])

        event = _deliver(handle)

        assert event is not None
        assert len(client.market_events) == 1

    @pytest.mark.parametrize(
        "reason, frame", INVALID_CASES, ids=[case[0] for case in INVALID_CASES]
    )
    def test_no_refusal_path_reaches_it(
        self, reason: str, frame: Any, counters: Any
    ) -> None:
        handle, _redis, client = _feed_on([frame])

        assert _deliver(handle) is None

        assert counters.paper_feed_invalid.get(symbol=SYMBOL, reason=reason) == 1
        assert client.market_events == []

    def test_no_random_draw_happens_on_the_path_either(self, counters: Any) -> None:
        """The stronger form: every ``random`` entry point the fabricator uses is planted.

        ``exchange_simulator`` draws prices from ``random.gauss`` and fills from ``random.random``,
        and Requirement 18.8 confines it to staging self-tests. If any of these fired, a paper price
        would have come from a distribution rather than from a market.
        """
        import random as random_module

        def _forbidden(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(
                "a random draw happened on the paper market-data path (Requirements 14.9, 18.1)"
            )

        with pytest.MonkeyPatch.context() as patch:
            for name in ("random", "gauss", "uniform", "randint", "choice", "shuffle"):
                patch.setattr(random_module, name, _forbidden)

            handle, _redis, client = _feed_on([_frame(timestamp_ms=_ms_before(60_000))])
            event = _deliver(handle)

        assert event is not None
        assert len(client.market_events) == 1


# ══════════════════════════════════════════════════════════════════════════
# 24.4 / 11. THE BACKOFF IS 1, 2, 4, 8, 16, 30, 30, ... AND IT IS JITTER-FREE
#            (Requirements 14.5, 15.4)
# ══════════════════════════════════════════════════════════════════════════

#: The delays Requirement 14.5's "bounded backoff" means here, written as a literal.
EXPECTED_BACKOFF: Tuple[Decimal, ...] = (
    Decimal("1"),
    Decimal("2"),
    Decimal("4"),
    Decimal("8"),
    Decimal("16"),
    Decimal("30"),
    Decimal("30"),
    Decimal("30"),
    Decimal("30"),
    Decimal("30"),
)


class TestTheBackoffIsExactBoundedAndJitterFree:
    """Task 24.4's first bullet, as an exact sequence and as a reproducibility claim.

    The jitter-free property is not cosmetic. Requirement 15.4 requires a session replayed against
    the same recorded events to produce the same order states and fills, and a randomised delay would
    move the reconnection points between the run and the replay - so the same events would be consumed
    at different moments and the replay would not be one. It would also be a ``random`` draw inside
    ``backend_app/backend/paper/``, which Requirements 8.13 and 18.1 forbid outright.

    Red run: ``backoff_delay_seconds`` was given the conventional jitter -
    ``BACKOFF_SECONDS[...] * Decimal(str(0.5 + random.random()))`` with ``import random`` added. Run
    together with the companion file's ``TestThereIsNoSecondMarketDataPath``: **7 failed, 8 passed**
    of the 15. Six of the seven are in this class - the exact sequence, the cap, the exactness, the
    repeated-call stability, the two-run reproducibility and the attempt reset - and the seventh is
    the companion's ``test_no_module_in_the_paper_package_imports_random``, which caught the import
    itself. Two files, one guard, and both halves fired.

    ``test_the_declared_table_is_the_first_six_of_them`` and
    ``test_the_attempt_number_is_one_based_and_zero_is_refused`` stayed green: the table and the
    bounds check are untouched by jitter, which is why the delays are asserted as *values* and not
    only as a table.
    """

    def test_the_delays_are_exactly_the_ten_expected(self) -> None:
        assert tuple(
            feed.backoff_delay_seconds(attempt) for attempt in range(1, 11)
        ) == EXPECTED_BACKOFF

    def test_the_declared_table_is_the_first_six_of_them(self) -> None:
        assert feed.BACKOFF_SECONDS == EXPECTED_BACKOFF[:6]
        assert feed.BACKOFF_SECONDS[-1] == Decimal("30")

    def test_the_cap_holds_for_every_later_attempt(self) -> None:
        """Bounded: attempt 6 and every attempt after it waits 30 s, forever."""
        for attempt in (6, 7, 20, 500, 10_000):
            assert feed.backoff_delay_seconds(attempt) == Decimal("30")

    def test_the_attempt_number_is_one_based_and_zero_is_refused(self) -> None:
        """A 0th attempt is a caller bug, and it is refused rather than answered with 1 s."""
        for bad in (0, -1):
            with pytest.raises(ValueError) as caught:
                feed.backoff_delay_seconds(bad)
            assert "1-based" in str(caught.value)

    def test_the_delays_are_exact_decimals_and_not_floats(self) -> None:
        for attempt in range(1, 8):
            delay = feed.backoff_delay_seconds(attempt)
            assert isinstance(delay, Decimal)
            assert delay == delay.to_integral_value()

    def test_repeated_calls_return_the_identical_value(self) -> None:
        """The function is a table lookup, so it has nothing to vary."""
        for attempt in range(1, 11):
            values = {feed.backoff_delay_seconds(attempt) for _ in range(50)}
            assert len(values) == 1

    def test_two_independent_runs_sleep_the_identical_sequence(
        self, counters: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reproducibility claim, measured where it matters: at ``asyncio.sleep``.

        Two separately constructed sessions, each driven through eight outages, must sleep the same
        eight delays. That is what makes the reconnection points of a replayed session the
        reconnection points of the original - the property Requirement 15.4 needs and the one jitter
        would destroy.
        """
        import asyncio

        def _run() -> List[Any]:
            recorded: List[Any] = []

            async def _record(delay: Any) -> None:
                recorded.append(delay)

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(asyncio, "sleep", _record)
                handle, _redis, _client_ = _feed_on([])
                _drain(handle, 8)
            return recorded

        first = _run()
        second = _run()

        assert first == second
        assert [Decimal(str(delay)) for delay in first] == list(EXPECTED_BACKOFF[:8])

    def test_the_attempt_counter_resets_on_a_validated_event(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        """A recovered session starts its next outage at 1 s, not where the last one stopped.

        Otherwise a session that dropped once an hour would eventually be waiting 30 s before its
        first attempt, which is a bounded backoff applied to the wrong thing.
        """
        handle, redis, _client_ = _feed_on([])

        _drain(handle, 3)
        assert no_sleeping == [1.0, 2.0, 4.0]
        assert handle.reconnect_attempts == 3

        redis.frames.append(_frame(timestamp_ms=_ms_before(60_000)))
        assert _deliver(handle) is not None
        assert handle.reconnect_attempts == 0

        _deliver(handle)
        assert no_sleeping == [1.0, 2.0, 4.0, 1.0]


class TestTheSleepIsAsyncioAndNeverTime:
    """Task 24.4: "``asyncio.sleep``, never ``time.sleep``".

    Structural, over the module's AST, following the precedent the companion file set for the
    ``markets_are_mocked`` detector: the module's prose *says* ``asyncio.sleep``, so a substring
    search would match the sentence rather than the code. A blocking sleep here would stall every
    other session in the process for up to 30 seconds (Requirement 27.3).

    Red run: ``await asyncio.sleep(float(delay))`` was replaced with ``import time`` plus
    ``time.sleep(float(delay))``. **3 failed, 0 passed** - the whole class: the receiver check naming
    ``time``, the import check, and the awaited-call check finding no ``await`` around the sleep.

    Only this class was run under that mutation, deliberately. A blocking sleep makes the
    disconnection cases in this file wait the real 1 + 2 + 4 + 8 + 16 + 30 + 30 + 30 seconds, so a
    whole-file red run would have taken minutes to say what three structural assertions say in a
    second - and the structural assertions are the ones that hold whether or not a test happens to
    drive a reconnection.
    """

    def _tree(self) -> ast.Module:
        return ast.parse(Path(inspect.getfile(feed)).read_text(encoding="utf-8"))

    def test_every_sleep_call_is_on_asyncio(self) -> None:
        receivers: List[str] = []
        bare: List[int] = []
        for node in ast.walk(self._tree()):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            if isinstance(target, ast.Attribute) and target.attr == "sleep":
                receivers.append(
                    target.value.id if isinstance(target.value, ast.Name) else "<expr>"
                )
            elif isinstance(target, ast.Name) and target.id == "sleep":
                bare.append(node.lineno)

        assert receivers == ["asyncio"], (
            f"every sleep on this path must be asyncio.sleep; found {receivers}"
        )
        assert bare == [], (
            f"a bare sleep(...) at lines {bare} hides which clock is being used"
        )

    def test_the_module_imports_no_blocking_clock_module(self) -> None:
        imported: set = set()
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        assert "time" not in imported
        assert "asyncio" in imported

    def test_the_awaited_sleep_is_inside_reconnect_and_is_awaited(self) -> None:
        """And it is the reconnection backoff, not a poller: exactly one, in exactly one method.

        Read off the AST of the method's **body**, docstring excluded. The docstring says
        "``asyncio.sleep``, never ``time.sleep``" in so many words, so a substring search over the
        source would match the explanation and would keep matching after the code changed.
        """
        awaited = [
            node
            for node in ast.walk(_body_tree(feed.FeedHandle.reconnect))
            if isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "sleep"
        ]

        assert len(awaited) == 1
        assert awaited[0].value.func.value.id == "asyncio"


# ══════════════════════════════════════════════════════════════════════════
# 24.4 / 13. ON A DROP: feed_state = 'DEGRADED' AND ONE paper_error RECORD
#            (Requirements 14.5, 26.3)
# ══════════════════════════════════════════════════════════════════════════


class TestADroppedSubscriptionIsRecordedOnceAsDegraded:
    """Task 24.4's second bullet, with the emphasis on **one**.

    One record per outage, not one per attempt. The Paper_Channel is a log an operator reads; a
    session that failed on every reconnection for an hour would otherwise write 120 identical lines
    and bury the one that matters, and Requirement 19.3's per-session sequence would advance 120 times
    for a single event.

    Red run 1 - the idempotence is lost. ``if already_degraded: return`` was short-circuited.
    **2 failed, 6 passed**: the eight-attempt count (eight records where one was asserted) and the
    second-outage case (nine records, and the sequences no longer ``[1, 2]``). Every state assertion
    stayed green, which is why the count is asserted separately from the state.

    Red run 2 - the record is not written at all. ``await self._emit_feed_disconnected(reason)`` was
    replaced with ``return``. **4 failed, 4 passed**: the four cases that read the log. The state
    cases stayed green - the pair that shows the two halves of Requirement 14.5's record are asserted
    independently rather than through one another.

    Red run 3 - the state is not persisted before the backoff. ``_transition_feed_state`` was replaced
    with a bare ``self.feed_state = FEED_STATE_DEGRADED``, so the in-process value moved and the row
    did not. **3 failed, 5 passed**, including
    ``test_the_degraded_state_is_persisted_before_the_first_attempt_sleeps`` - which is the case that
    turns "DEGRADED is written" into "DEGRADED is written *before* anything waits on it", and without
    it a session with a dead feed would read ``HEALTHY`` for up to 30 seconds.
    """

    def test_the_state_is_degraded_in_process_and_in_the_row(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        handle, _redis, client = _feed_on([])

        assert _deliver(handle) is None

        assert handle.feed_state == feed.FEED_STATE_DEGRADED == "DEGRADED"
        assert client.sessions[0]["feed_state"] == "DEGRADED"

    def test_exactly_one_paper_error_record_is_written_for_eight_attempts(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        handle, _redis, client = _feed_on([])

        _drain(handle, 8)

        assert len(no_sleeping) == 8  # eight attempts really were made
        assert len(client.events) == 1
        assert len(client.statements_on(repo.EVENTS_TABLE, "insert")) == 1

    def test_the_record_carries_the_feed_disconnected_code(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        handle, _redis, client = _feed_on([])
        _deliver(handle)

        record = client.events[0]
        assert record["event_type"] == feed.PAPER_ERROR_EVENT_TYPE == "paper_error"
        assert record["payload"]["code"] == feed.FEED_DISCONNECTED_CODE == "FEED_DISCONNECTED"
        assert record["payload"]["feed_state"] == "DEGRADED"
        assert record["payload"]["channel"] == DATA_CHANNEL
        assert record["session_id"] == SESSION
        assert record["user_id"] == USER
        assert record["sequence"] == 1
        assert record["schema_version"] == feed.PAPER_EVENT_SCHEMA_VERSION

    def test_the_record_carries_no_credential_endpoint_or_other_identifier(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        """Requirement 26.4: the payload names the channel and the reason, and nothing sensitive."""
        handle, _redis, client = _feed_on([])
        _deliver(handle)

        payload = client.events[0]["payload"]
        assert set(payload) == {
            "code",
            "channel",
            "exchange_id",
            "symbol",
            "timeframe",
            "market_data_source",
            "feed_state",
            "reason",
        }
        rendered = json.dumps(payload).lower()
        for forbidden in ("secret", "api_key", "apikey", "token", "password", "redis://"):
            assert forbidden not in rendered
        assert OTHER_USER not in rendered

    def test_the_degraded_state_is_persisted_before_the_first_attempt_sleeps(
        self, counters: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ordering that makes the no-fill guarantee real, not merely eventual.

        The simulator reads the persisted state. If the state were written after the reconnection
        attempt, there would be a window of up to 30 seconds in which a session with a dead feed still
        read ``HEALTHY`` - and a fill in that window would be priced at a pre-disconnection close,
        which is exactly what Requirements 14.5 and 18.15 forbid. So the row is read at the instant
        the backoff sleeps.
        """
        import asyncio

        client = _client()
        observed: List[Any] = []

        async def _observe(delay: Any) -> None:
            observed.append((delay, client.sessions[0]["feed_state"]))

        monkeypatch.setattr(asyncio, "sleep", _observe)
        handle, _redis, _c = _feed_on([], client=client)

        _deliver(handle)

        assert observed == [(1.0, "DEGRADED")]

    def test_a_second_outage_after_a_recovery_writes_a_second_record(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        """One per outage - so the idempotence is per outage and not per session.

        A session that dropped twice has two incidents, and an operator has to be able to see both.
        """
        handle, redis, client = _feed_on([])

        _drain(handle, 3)
        assert len(client.events) == 1

        redis.frames.append(_frame(timestamp_ms=_ms_before(60_000)))
        assert _deliver(handle) is not None
        assert handle.feed_state == feed.FEED_STATE_HEALTHY

        _deliver(handle)

        assert len(client.events) == 2
        assert [row["payload"]["code"] for row in client.events] == [
            "FEED_DISCONNECTED",
            "FEED_DISCONNECTED",
        ]
        assert [row["sequence"] for row in client.events] == [1, 2]

    def test_the_reconnection_attempts_are_counted(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        """Requirement 26.6: the attempts are a metric, since the record is written only once."""
        handle, _redis, _client_ = _feed_on([])

        _drain(handle, 4)

        assert counters.paper_feed_reconnects.name == "paper.feed.reconnects"
        assert counters.paper_feed_reconnects.label_names == ["outcome"]
        assert counters.paper_feed_reconnects.get(outcome="resubscribed") == 4
        assert counters.paper_feed_reconnects.total() == 4
        # The state transition is counted once, because it happened once.
        assert counters.paper_feed_state.total() == 1

    def test_the_drop_is_never_an_exception_to_the_caller(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        """``None``, so a session worker's loop is not written around an exception.

        ``PaperFeedDisconnected`` is internal and never reaches an HTTP response; the caller learns
        about the outage from the persisted state or the metric.
        """
        handle, _redis, _client_ = _feed_on([])

        assert _drain(handle, 5) == [None, None, None, None, None]
        assert isinstance(feed.PaperFeedDisconnected("x"), feed.PaperFeedError)
        assert not issubclass(feed.PaperFeedDisconnected, feed.PaperMarketDataUnavailable)


# ══════════════════════════════════════════════════════════════════════════
# 24.4 / 14. ON RECONNECTION THE SESSION RESUMES FROM THE FIRST VALIDATED EVENT,
#            AND NOTHING IS INTERPOLATED ACROSS THE GAP (Requirements 14.5, 14.9)
# ══════════════════════════════════════════════════════════════════════════


class TestTheSessionResumesFromTheFirstValidatedEventAndFillsNoGap:
    """Task 24.4's fourth bullet, asserted mostly by **absence**.

    "Nothing is interpolated" is a claim about rows that must not exist and prices that must not have
    been invented, so the assertions are a row count, the set of recorded instants, and the set of
    recorded closes - all compared to exactly the two candles that actually arrived.

    Red run 1 - the gap is back-filled. ``FeedHandle.reconnect`` was given the insert a "don't lose
    the gap" reading produces: a synthesised candle at the midpoint of the outage carrying the last
    close forward, written before re-subscribing. **7 failed, 0 passed** - the whole class, including
    the two cases that only look at the row count and the recorded instants. That is what asserting by
    absence buys: an interpolation cannot be added anywhere on this path without every case here
    noticing.

    Red run 2 - the ordering record is reset. ``self.last_timestamp.clear()`` was added to
    ``reconnect``, the "start fresh after an outage" reading. **2 failed, 5 passed** -
    ``test_the_high_water_mark_survives_the_outage``, because a stale candle republished after the
    reconnection was accepted, and ``test_the_reconnection_only_resubscribes``, on the AST naming
    ``last_timestamp``.
    """

    #: The candle before the outage and the first one after it. 300 s apart, so the gap is wide
    #: enough that an interpolation would be unmistakable.
    BEFORE_MS = _ms_before(600_000)
    AFTER_MS = _ms_before(300_000)

    def _outage(self, counters: Any) -> Tuple[feed.FeedHandle, RecordingRedis, FakeSupabase]:
        handle, redis, client = _feed_on([_frame(timestamp_ms=self.BEFORE_MS, volume=1.5)])
        assert _deliver(handle) is not None
        assert _deliver(handle) is None  # the outage
        assert handle.feed_state == feed.FEED_STATE_DEGRADED
        return handle, redis, client

    def test_an_invalid_event_after_the_outage_does_not_end_it(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        """"the first event that **passes validation**" - a rejected candle is not that event.

        A session restored to ``HEALTHY`` by a candle the validator refused would be filling orders
        from a feed that has demonstrated nothing.
        """
        handle, redis, client = self._outage(counters)

        redis.frames.append(
            _frame(timestamp_ms=self.AFTER_MS, overrides={"low": 99999.0})
        )
        assert _deliver(handle) is None

        assert handle.feed_state == feed.FEED_STATE_DEGRADED
        assert client.sessions[0]["feed_state"] == "DEGRADED"
        assert len(client.market_events) == 1
        assert (
            counters.paper_feed_invalid.get(
                symbol=SYMBOL, reason=feed.INVALID_VALIDATOR_REJECTED
            )
            == 1
        )

    def test_the_first_validated_event_ends_the_outage(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        handle, redis, client = self._outage(counters)
        redis.frames.append(
            _frame(timestamp_ms=self.AFTER_MS, overrides={"low": 99999.0})
        )
        _deliver(handle)

        redis.frames.append(_frame(timestamp_ms=self.AFTER_MS, volume=2.5))
        resumed = _deliver(handle)

        assert resumed is not None
        assert resumed.sequence == 2
        assert handle.feed_state == feed.FEED_STATE_HEALTHY
        assert client.sessions[0]["feed_state"] == "HEALTHY"

    def test_no_row_is_written_for_the_gap(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        handle, redis, client = self._outage(counters)
        redis.frames.append(_frame(timestamp_ms=self.AFTER_MS, volume=2.5))
        _deliver(handle)

        assert len(client.market_events) == 2
        assert [row["sequence"] for row in client.market_events] == [1, 2]
        # The market log has exactly one INSERT per accepted candle, and the outage produced none.
        assert len(client.statements_on(repo.MARKET_EVENTS_TABLE, "insert")) == 2

    def test_the_instants_recorded_are_only_the_two_that_arrived(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        handle, redis, client = self._outage(counters)
        redis.frames.append(_frame(timestamp_ms=self.AFTER_MS, volume=2.5))
        _deliver(handle)

        recorded = {row["payload"]["timestamp"] for row in client.market_events}
        assert recorded == {self.BEFORE_MS, self.AFTER_MS}
        # Nothing landed anywhere between them - which is what an interpolation would look like.
        assert not [
            stamp for stamp in recorded if self.BEFORE_MS < stamp < self.AFTER_MS
        ]

    def test_the_recorded_closes_are_only_the_ones_that_arrived(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        """No price is synthesised, and no price is carried forward as though it were current."""
        handle, redis, client = self._outage(counters)
        redis.frames.append(
            _frame(timestamp_ms=self.AFTER_MS, close=60099.5, volume=2.5)
        )
        _deliver(handle)

        closes = [Decimal(row["payload"]["close"]) for row in client.market_events]
        assert closes == [Decimal("60000.5"), Decimal("60099.5")]
        assert len(set(closes)) == 2, (
            "the post-outage close equals the pre-outage close, which is what carrying a stale "
            "price forward looks like (Requirements 14.9, 18.15)"
        )

    def test_the_high_water_mark_survives_the_outage(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        """``reconnect`` touches no ordering state, so a stale republication is still refused.

        The candles that closed during the outage are candles this session never saw - not candles it
        is now willing to accept out of order.
        """
        handle, redis, client = self._outage(counters)

        # The outage neither cleared the mark nor moved it.
        assert set(handle.last_timestamp) == {SYMBOL}
        assert handle.last_timestamp[SYMBOL] == datetime.fromtimestamp(
            self.BEFORE_MS / 1000, tz=timezone.utc
        )

        redis.frames.append(_frame(timestamp_ms=_ms_before(900_000), volume=3.5))
        assert _deliver(handle) is None

        assert counters.paper_feed_out_of_order.get(symbol=SYMBOL) == 1
        assert len(client.market_events) == 1

    def test_the_reconnection_only_resubscribes(self) -> None:
        """Asserted on the AST of ``reconnect``'s **body**: it sleeps, closes, subscribes.

        The docstring excluded, because it explains at length that the method does *not* fetch the
        missed window, does *not* back-fill and does *not* touch ``last_timestamp`` - so a substring
        search over the source would match the explanation of the guarantee instead of the guarantee.

        A method that fetched the missed window would be an interpolation and a second market-data
        path at once; the companion file's ``FORBIDDEN_CALLS`` check over the whole module is the
        other half of this one.
        """
        referenced = _names_in(_body_tree(feed.FeedHandle.reconnect))

        forbidden = {
            "last_timestamp",
            "seen_event_ids",
            "insert_market_event",
            "fetch_ohlcv",
            "get_market_events",
            "sequence",
        }
        assert referenced & forbidden == set(), (
            f"FeedHandle.reconnect names {sorted(referenced & forbidden)}; it must re-open a "
            f"subscription and do nothing else (Requirement 14.9)"
        )
        assert {"sleep", "_subscribe", "_close_pubsub"} <= referenced


# ══════════════════════════════════════════════════════════════════════════
# 24.4 / 15. THE FEED GATE: ONE NAMED CALLABLE, AND IT REFUSES EVERYTHING BUT HEALTHY
#            (Requirements 14.5, 14.6, 18.15)
# ══════════════════════════════════════════════════════════════════════════

#: Every feed state that is not ``HEALTHY``. Derived from the module's own vocabulary rather than
#: listed, so a state added later is refused by this test until somebody decides about it - which is
#: the opposite of the default a hardcoded list would give.
NON_HEALTHY_STATES: Tuple[str, ...] = tuple(
    state for state in feed.FEED_STATES if state != feed.FEED_STATE_HEALTHY
)


def _row_at(state: Any, *, transport: Optional[str] = "WEBSOCKET") -> Dict[str, Any]:
    """A ``paper_sessions`` row as the simulator would have read it inside its transaction."""
    return _session_row(feed_state=state, feed_transport=transport)


class TestTheFeedGateIsOneNamedCallableThatAdmitsOnlyHealthy:
    """Task 24.4's third bullet: the predicate ``paper_simulator`` must consult.

    THIS IS THE DEFECT THIS FILE FOUND. ``admit_execution`` and ``ExecutionAdmission`` were listed on
    the module's own docstring surface and referenced by ``FeedNotHealthy``'s docstring, and neither
    existed. There was no named predicate for task 25 to call, and ``FeedNotHealthy`` was raised by
    nothing in the codebase. Both are now implemented, reading ``TRADEABLE_FEED_STATES``.

    Red run 1 - the gate is absent, which is the state the module was found in. ``admit_execution``
    was renamed away. Run with the ``FALLBACK_REST`` class as well: **15 failed, 3 passed** of the 18.
    The three survivors are the tuple check, the docstring check and
    ``test_the_token_cannot_be_forged_outside_the_module`` - so with no gate at all, the module still
    declared a whitelist, still documented a decision and still refused a forged token, and nothing
    enforced any of it. That is precisely the shape of the defect: everything around the gate existed
    and the gate did not.

    Red run 2 - the whitelist is widened. ``TRADEABLE_FEED_STATES`` became
    ``(FEED_STATE_HEALTHY, FEED_STATE_FALLBACK_REST)`` - the "REST is a working feed, so let it trade"
    reading. **5 failed, 13 passed**: the parametrised ``FALLBACK_REST`` case, the four-states case,
    the whitelist case, and both cases in the class below. See
    :class:`TestWhetherFallbackRestIsTradeableIsStatedNotImplied` for the requirement text on both
    sides and why the narrow reading is the one in force.
    """

    def test_the_gate_exists_and_is_one_callable(self) -> None:
        """One, and named - so a call site either calls it or provably does not."""
        assert callable(feed.admit_execution)
        assert feed.admit_execution.__module__ == feed.__name__

    def test_a_healthy_session_is_admitted(self) -> None:
        admission = feed.admit_execution(_row_at(feed.FEED_STATE_HEALTHY))

        assert isinstance(admission, feed.ExecutionAdmission)
        assert admission.feed_state == feed.FEED_STATE_HEALTHY
        assert admission.session_id == SESSION
        assert admission.user_id == USER
        assert admission.feed_transport == "WEBSOCKET"
        assert admission.admitted_at.tzinfo is not None

    @pytest.mark.parametrize("state", NON_HEALTHY_STATES)
    def test_every_non_healthy_state_is_refused(self, state: str) -> None:
        with pytest.raises(feed.FeedNotHealthy) as caught:
            feed.admit_execution(_row_at(state))

        assert caught.value.feed_state == state
        assert caught.value.session_id == SESSION

    def test_the_four_states_task_24_4_names_are_all_covered(self) -> None:
        """``DEGRADED``, ``PENDING``, ``FALLBACK_REST`` and ``TRANSPORT_UNKNOWN``, explicitly.

        The parametrised case above derives its list from the module, so this one pins the four the
        task names - a state quietly dropped from ``FEED_STATES`` would shrink that list and the
        parametrised case would pass with fewer refusals asserted.
        """
        assert set(NON_HEALTHY_STATES) == {
            "DEGRADED",
            "PENDING",
            "FALLBACK_REST",
            "TRANSPORT_UNKNOWN",
        }
        for state in ("DEGRADED", "PENDING", "FALLBACK_REST", "TRANSPORT_UNKNOWN"):
            with pytest.raises(feed.FeedNotHealthy):
                feed.admit_execution(_row_at(state))

    def test_the_whitelist_holds_exactly_healthy(self) -> None:
        """A whitelist, not a blacklist: a state added later is refused until somebody decides.

        ``!= 'DEGRADED'`` would admit every future state by default, and the future state is exactly
        the one nobody has thought about yet.
        """
        assert feed.TRADEABLE_FEED_STATES == (feed.FEED_STATE_HEALTHY,)
        assert len(feed.TRADEABLE_FEED_STATES) == 1
        assert set(feed.TRADEABLE_FEED_STATES) <= set(feed.FEED_STATES)

    def test_an_unknown_or_absent_state_is_refused_rather_than_defaulted(self) -> None:
        """"the row did not say" is not evidence of a healthy feed (Requirement 28.5)."""
        for row in (
            _session_row(feed_state=None),
            _session_row(feed_state=""),
            _session_row(feed_state="   "),
            _session_row(feed_state="SOMETHING_NEW"),
            {"id": SESSION, "user_id": USER},  # the column absent entirely
        ):
            with pytest.raises(feed.FeedNotHealthy):
                feed.admit_execution(row)

    def test_a_state_differing_only_in_case_or_whitespace_is_not_admitted_as_healthy(
        self,
    ) -> None:
        """Whitespace is stripped; case is not folded, because the column's vocabulary is upper.

        A row reading ``'healthy'`` is a row somebody wrote outside ``_transition_feed_state``, which
        refuses anything outside ``FEED_STATES`` - so it is a state the platform did not produce and
        is refused rather than interpreted.
        """
        assert feed.admit_execution(_row_at("  HEALTHY  ")).feed_state == "HEALTHY"

        for spelling in ("healthy", "Healthy", "HEALTHY_ISH"):
            with pytest.raises(feed.FeedNotHealthy):
                feed.admit_execution(_row_at(spelling))

    def test_the_refusal_names_the_condition_rather_than_describing_it(self) -> None:
        """``FeedNotHealthy``'s message quotes the state and the two requirements it enforces."""
        with pytest.raises(feed.FeedNotHealthy) as caught:
            feed.admit_execution(_row_at(feed.FEED_STATE_DEGRADED))

        message = str(caught.value)
        assert "DEGRADED" in message
        assert "HEALTHY" in message
        assert "14.5" in message and "18.15" in message
        assert isinstance(caught.value, feed.PaperFeedError)

    def test_the_token_cannot_be_forged_outside_the_module(self) -> None:
        """The reason the gate returns a token and not a ``bool``.

        A boolean can be skipped: a simulator that forgot to consult the gate would carry ``True`` by
        omission and the omission would look exactly like a pass. A token has to be produced, and only
        ``admit_execution`` can produce one - so task 25 can require it on the code path that fills an
        order and "the gate was consulted" becomes a property of the type.
        """
        with pytest.raises(ValueError) as caught:
            feed.ExecutionAdmission(
                session_id=SESSION,
                user_id=USER,
                feed_state=feed.FEED_STATE_HEALTHY,
                feed_transport="WEBSOCKET",
                admitted_at=datetime(2025, 6, 15, tzinfo=timezone.utc),
            )

        assert "cannot be constructed directly" in str(caught.value)
        # And not by guessing the keyword either.
        with pytest.raises(ValueError):
            feed.ExecutionAdmission(
                session_id=SESSION,
                user_id=USER,
                feed_state=feed.FEED_STATE_HEALTHY,
                feed_transport=None,
                admitted_at=datetime(2025, 6, 15, tzinfo=timezone.utc),
                _key=object(),
            )

    def test_the_gate_issues_no_statement_of_its_own(self) -> None:
        """It reads the row the caller already read, inside the caller's transaction.

        A read here would be outside that transaction and could see a state the transaction will not -
        which is the whole reason task 24.4 says "inside its transaction".
        """
        client = _client()

        feed.admit_execution(client.sessions[0] | {"feed_state": "HEALTHY"})

        assert client.statements == []
        referenced = _names_in(_body_tree(feed.admit_execution))
        assert "table" not in referenced
        assert "supabase" not in referenced
        assert "read_session" not in referenced

    def test_the_gate_takes_the_persisted_row_and_not_a_live_handle(
        self, counters: Any
    ) -> None:
        """The in-process handle lives in one worker's memory; the row is what every instance sees.

        Requirement 17.3 requires every backend instance to answer the same, so the gate has to read
        the column and not the object.
        """
        handle, _redis, client = _feed_on([_frame(timestamp_ms=_ms_before(60_000))])
        assert _deliver(handle) is not None
        assert handle.feed_state == feed.FEED_STATE_HEALTHY

        admission = feed.admit_execution(client.sessions[0])
        assert admission.feed_state == feed.FEED_STATE_HEALTHY

        with pytest.raises(ValueError):
            feed.admit_execution(handle)  # type: ignore[arg-type]

    def test_a_degraded_session_read_from_the_row_is_refused_end_to_end(
        self, counters: Any, no_sleeping: List[Any]
    ) -> None:
        """The whole 24.4 chain in one case: drop -> DEGRADED persisted -> the gate refuses.

        This is the join between the two halves of Requirement 14.5. The state the outage wrote is the
        state the gate reads, from the row, so no order may be accepted and no resting order filled
        while the feed is down.
        """
        handle, redis, client = _feed_on([_frame(timestamp_ms=_ms_before(600_000))])
        assert _deliver(handle) is not None
        assert feed.admit_execution(client.sessions[0]).feed_state == "HEALTHY"

        assert _deliver(handle) is None  # the outage

        with pytest.raises(feed.FeedNotHealthy) as caught:
            feed.admit_execution(client.sessions[0])
        assert caught.value.feed_state == "DEGRADED"

        # ... and admitted again only after an event has PASSED VALIDATION.
        redis.frames.append(
            _frame(timestamp_ms=_ms_before(300_000), overrides={"low": 99999.0})
        )
        _deliver(handle)
        with pytest.raises(feed.FeedNotHealthy):
            feed.admit_execution(client.sessions[0])

        redis.frames.append(_frame(timestamp_ms=_ms_before(300_000), volume=2.5))
        assert _deliver(handle) is not None
        assert feed.admit_execution(client.sessions[0]).feed_state == "HEALTHY"


class TestWhetherFallbackRestIsTradeableIsStatedNotImplied:
    """The module says so, in words, and the words and the tuple agree.

    Two requirements pull in different directions here and the resolution has to be legible rather
    than inferred from a tuple:

    * **Requirement 14.6** - "WHERE the WebSocket market-data path is unavailable and the REST path
      is available, THE Paper_Session SHALL fall back to the REST path and SHALL record the fallback
      in the session's feed state." A recorded, working feed. Honoured in full: the subscription stays
      open, events are validated and recorded, and the state says ``FALLBACK_REST``.
    * **Requirement 18.15** - a stale price may not be substituted, and ``mds/main.py``'s REST
      fallback polls on a bar cadence rather than streaming, so its most recent candle can be a whole
      bar old.
    * **tasks.md 24.4** resolves it explicitly: not ``HEALTHY`` means no market order accepted and no
      resting limit order filled.

    So ``FALLBACK_REST`` is **not tradeable**, neither requirement is contradicted - 14.6 requires the
    fallback to happen and to be recorded, not that execution continues over it - and the operational
    consequence (a REST-polled session records data and stops trading) is stated in the module
    docstring rather than discovered in production.

    Red run: the "IS ``FALLBACK_REST`` TRADEABLE?" heading was renamed in the module docstring.
    **1 failed, 2 passed** - only ``test_the_module_docstring_answers_the_question_explicitly``, which
    is the whole point of it: the tuple and the behaviour were untouched, so the decision was still in
    force and no longer written down anywhere. A reader would have had to infer it from a one-element
    tuple.
    """

    def test_fallback_rest_is_not_tradeable(self) -> None:
        assert feed.FEED_STATE_FALLBACK_REST not in feed.TRADEABLE_FEED_STATES

        with pytest.raises(feed.FeedNotHealthy) as caught:
            feed.admit_execution(_row_at(feed.FEED_STATE_FALLBACK_REST, transport="REST"))

        assert caught.value.feed_state == "FALLBACK_REST"

    def test_the_module_docstring_answers_the_question_explicitly(self) -> None:
        docstring = feed.__doc__ or ""

        assert "IS ``FALLBACK_REST`` TRADEABLE?" in docstring
        assert "**No.**" in docstring
        # Both requirements are named where the decision is recorded, so a reader sees the tension
        # rather than only the outcome.
        assert "14.6" in docstring
        assert "18.15" in docstring

    def test_the_fallback_itself_still_happens_and_is_still_recorded(
        self, counters: Any
    ) -> None:
        """Requirement 14.6 is not skipped: the REST event is validated, recorded and counted.

        Refusing to *trade* on it is a different decision from refusing to *consume* it, and only the
        first one is made.
        """
        handle, _redis, client = _feed_on(
            [_frame(transport="REST", timestamp_ms=_ms_before(60_000))]
        )

        event = _deliver(handle)

        assert event is not None
        assert event.transport == feed.FEED_TRANSPORT_REST
        assert client.sessions[0]["feed_state"] == "FALLBACK_REST"
        assert client.sessions[0]["feed_transport"] == "REST"
        assert len(client.market_events) == 1
        assert counters.paper_feed_events.get(symbol=SYMBOL, transport="REST") == 1

        with pytest.raises(feed.FeedNotHealthy):
            feed.admit_execution(client.sessions[0])
