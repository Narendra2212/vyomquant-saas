"""
tests/property/test_market_event_dedupe.py - P-54, and nothing else.

Spec: marketplace-subscriptions-paper-trading task 24.5. Requirements 14.7, 15.5.

**Property P-54 (invariant, dedupe and ordering)** - for all generated streams containing
duplicated ``source_event_id`` values and out-of-order timestamps, each ``source_event_id`` is
processed at most once per session, the processed timestamp sequence per symbol is
non-decreasing, and the ``paper_market_events`` row count equals the distinct processed count.

WHAT IS DRIVEN, AND WHAT IS NOT DOUBLED
---------------------------------------
The real ``paper_market_feed.next_validated_event`` is driven, event by event, through the real
``mds:data:*`` wire shape. Nothing about the dedupe, the ordering guard, the identity or the
insert is stubbed:

* the market-data double is ``tests/test_paper_market_feed_selection.RecordingRedis`` and its
  frame queue - the one this repository has for that channel;
* the Persistence_Layer double is ``tests/test_paper_repository.FakeSupabase`` - the one paper
  double, which enforces ``uq_paper_market_event UNIQUE (session_id, source_event_id)``, so a
  dedupe assertion here cannot pass without a dedupe;
* the frame builder is that file's ``_frame``, so this module invents no second opinion about
  what ``mds/main.py::broadcast_ohlcv`` publishes;
* ``_run_coroutine`` runs every coroutine, never ``asyncio.run`` - see that module's note.

WHERE THE GENERATOR AND THE PROJECTION LIVE
-------------------------------------------
Both were written here and moved to ``tests/property/paper_market_streams.py`` at tasks 24.6/24.7,
when ``test_no_stale_fill.py`` (P-56) and ``test_no_synthesised_price.py`` (P-55) needed the same
generated stream. Three copies of a generator are three generators, and the one that gets fixed is
never the one that is running. Nothing about the property changed: the names are re-bound below to
the spellings this module always used, and that module's docstring records what moved.

THE ORACLE IS A PROJECTION OF THE STREAM, NOT THE FEED'S OUTPUT
--------------------------------------------------------------
:func:`_project` computes the expected result from the generated stream alone: distinct by
identity, then filtered to a non-decreasing per-symbol timestamp sequence. It calls nothing from
``paper_market_feed``. In particular:

* the identity is recomputed in the support module, as
  ``sha256("exchange|symbol|timeframe|timestamp|close|volume")`` over material that module builds -
  the same independence ``tests/test_paper_market_feed_events._independent_event_id`` establishes.
  An oracle that called ``feed.source_event_id`` would be asserting that function against itself;
* the **canonical** spelling of a close and a volume is a written-down literal in
  :data:`CLOSE_FAMILIES` and :data:`VOLUME_FAMILIES`, not a call to ``feed.canonical_number``. So
  the convention "``60000.50`` and ``6.00005E+4`` are the same event as ``60000.5``" is stated by
  the test material and can disagree with the module.
  :func:`test_the_spelling_families_are_coherent` - still collected from this file, because a guard
  has to run somewhere pytest collects - guards the table itself.

WHY THE WIRE VALUES ARE UNQUOTED BACK INTO JSON NUMBERS
------------------------------------------------------
The dedupe claim turns on a *respelled republication*: ``mds/main.py`` re-serialises whatever
CCXT handed it on every socket update, so one forming candle can arrive as ``60000.5`` once and
``60000.50`` or ``6.00005E+4`` the next time. A respelling cannot survive a Python ``float``:
``json.dumps(60000.50)`` is ``60000.5``, and the case would be lost before it reached the module.
So the spelling travels as text through ``_frame`` and the support module's
``unquote_json_number`` removes the quotes from the serialised body, putting an exact JSON
**number** on the wire. That is the path
``decode_payload``'s ``parse_float=Decimal`` reads, which is the path production uses.

WHAT "PER SESSION" AND "PER SYMBOL" MEAN HERE, EXACTLY
-----------------------------------------------------
``PaperFeedConfig`` carries **one** symbol and ``FeedHandle`` subscribes to one
``mds:data:{exchange}:{symbol}`` channel, so a session watching two symbols holds two
subscriptions against one ``session_id``. That is the construction used below, and it is the one
``tests/test_paper_market_feed_events.TestOrderingIsPerSymbolAndNotGlobal`` already established:
two handles, one ``FakeSupabase``, and **one** ``last_timestamp`` mapping shared between them, so
the ordering record is the session's rather than each worker's. ``uq_paper_market_event`` is keyed
on ``(session_id, source_event_id)``, so the dedupe conjunct is asserted session-wide over both
subscriptions' rows.

The sequence conjunct is asserted **per symbol**, and the reason is a property of the module
rather than a convenience: ``open_feed`` reads the session's log cursor once, at subscription
time (``next_market_event_sequence(...) - 1``), so each subscription owns a cursor. For the
single-symbol session - the shape ``PaperFeedConfig`` describes and the shape 24.1-24.4 were
written for - per-symbol and session-wide are the same sequence, and the assertion is that it is
``1, 2, 3, ...`` with no hole. See "GAP LEFT OPEN" at the bottom of this docstring.

WHAT COULD MAKE THIS PROPERTY PASS FOR THE WRONG REASON, AND WHAT STOPS IT
--------------------------------------------------------------------------
A stream containing no duplicate and no late candle satisfies all three conjuncts trivially, so
the generator is the load-bearing part. :func:`market_event_streams` therefore **forces** a spine
of eight candles into every example - an accepted candle, its exact republication, its respelled
republication, a revision at the same instant, a late candle, a second symbol below the first
symbol's high-water mark, a newer candle and a second-symbol duplicate - and appends a freely
drawn tail. The census in :func:`_record` counts what the *oracle* classified rather than what
the spine intended, so an interleaving that reclassified a case is reported rather than assumed,
and :data:`CENSUS_FLOORS` fails the run if any bucket under-fills. A shortfall is fixed by
forcing the case in the generator, never by lowering the floor.

GAP LEFT OPEN
-------------
``paper_market_events`` has ``chk_paper_market_event_sequence CHECK (sequence >= 1)`` and
``idx_paper_market_events (session_id, sequence ASC)`` - the replay read - but **no**
``UNIQUE (session_id, sequence)``. Two subscriptions on one session each read the cursor once at
``open_feed`` and both therefore write ``sequence = 1``, so a two-symbol session's replay order is
ambiguous by ``sequence`` alone. That is recorded here rather than asserted around: the
consecutive-from-1 claim is made **per symbol**, which is the strongest form the module's
per-subscription cursor supports, and for the one-symbol session that ``PaperFeedConfig``
describes it is the session-wide claim unchanged. Every generated stream drives two symbols,
because the per-symbol half of Requirement 14.7 is vacuous on one. Closing the gap is a change to
how a session's subscriptions share a cursor - 24.x design territory, not this task's, and not
something a property test may decide by itself.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pytest
from hypothesis import HealthCheck, event, given, settings

from backend_app.backend.paper import paper_market_feed as feed
from backend_app.backend.paper import paper_repository as repo

# ── The census, written once for the paper property modules that need it. ─────────────────
from tests.property.paper_census import publish_hypothesis_statistics

# ── The shared harness. Two market-data doubles would be two accounts of the wire. ────────
from tests.test_paper_market_feed_events import _deliver, _feed_on
from tests.test_paper_market_feed_events import (  # noqa: F401 - used by name as fixtures
    counters,
    no_sleeping,
)
from tests.test_paper_market_feed_selection import (
    PROCESSED_AT,
    SESSION,
    SYMBOL,
    _client,
    _config,
)
from tests.test_paper_market_feed_selection import (  # noqa: F401 - autouse, used by name
    _fresh_probe,
)

#: ``design.md § Property-based testing configuration``: at least 100 examples, no per-example
#: deadline. One example opens two subscriptions and drives up to fourteen events through the
#: real validator (which builds a one-row frame per candle), so ``too_slow`` is suppressed
#: rather than the example count being cut.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

#: THE STREAM, THE CANDLE, THE IDENTITY, THE PROJECTION AND THE WIRE NOW LIVE IN ONE PLACE.
#:
#: All five were written here for P-54 and were moved to ``tests/property/paper_market_streams.py``
#: at task 24.6/24.7, when ``test_no_stale_fill.py`` (P-56) and ``test_no_synthesised_price.py``
#: (P-55) needed the same generated stream. Three copies of a generator are three generators, and
#: the one that gets fixed is never the one that is running. That module's docstring records what
#: moved and what stayed; the reasoning behind each piece moved with the code.
#:
#: The names are re-bound to the spellings this module already used, so every assertion below reads
#: exactly as it did - and so the diff at the move is a deletion, not a rewrite of the property.
from tests.property.paper_market_streams import (  # noqa: E402 - after the settings block
    ACCEPTED,
    CLOSE_FAMILIES,
    DROPPED_DUPLICATE,
    SPINE_LENGTH,
    SYMBOLS,
    VOLUME_FAMILIES,
    market_event_streams,
)
from tests.property.paper_market_streams import Candle as _Candle
from tests.property.paper_market_streams import Verdict as _Verdict
from tests.property.paper_market_streams import project as _project
from tests.property.paper_market_streams import wire_frame as _wire_frame


def _run_stream(stream: Sequence[_Candle]) -> Tuple[Any, List[Optional[Any]]]:
    """Drive ``stream`` through the real feed. Returns the client and one result per candle.

    One session, one subscription per symbol - the construction ``PaperFeedConfig`` forces, since
    it carries a single symbol - and **one** ``last_timestamp`` mapping shared across the
    subscriptions, so the ordering record belongs to the session rather than to a worker. Both
    subscriptions are opened before any delivery, so each starts from the same empty log.

    Each frame is appended to its symbol's queue immediately before that symbol's
    ``next_validated_event`` call, which is what keeps the queue non-empty at the moment the
    subscription reads it: an empty queue ends the iterator, and ``_receive`` reads that as the
    dropped subscription of Requirement 14.5. The caller asserts no disconnection was recorded.
    """
    client = _client()
    handles: Dict[str, Any] = {}
    queues: Dict[str, Any] = {}
    for symbol in SYMBOLS:
        handle, redis, _ = _feed_on([], client=client, config=_config(symbol=symbol))
        handles[symbol] = handle
        queues[symbol] = redis

    shared_high_water: Dict[str, datetime] = {}
    for handle in handles.values():
        handle.last_timestamp = shared_high_water

    results: List[Optional[Any]] = []
    for candle in stream:
        queues[candle.symbol].frames.append(_wire_frame(candle))
        results.append(_deliver(handles[candle.symbol]))
    return client, results


# ══════════════════════════════════════════════════════════════════════════
# THE THREE CONJUNCTS, ASSERTED SEPARATELY
# ══════════════════════════════════════════════════════════════════════════


def _rows_by_symbol(client: Any, symbol: str) -> List[Mapping[str, Any]]:
    """This session's ``paper_market_events`` rows for one symbol, ordered by ``sequence``.

    ``sequence`` and not insertion order, because ``idx_paper_market_events (session_id,
    sequence ASC)`` is the replay read and the ordering conjunct is a claim about what a replay
    sees.
    """
    return sorted(
        (
            row
            for row in client.market_events
            if row["session_id"] == SESSION and row["symbol"] == symbol
        ),
        key=lambda row: row["sequence"],
    )


def _assert_each_identity_at_most_once(
    client: Any, processed: Sequence[Tuple[str, _Candle]], stream: Sequence[_Candle]
) -> None:
    """Conjunct 1: each ``source_event_id`` appears at most once for this session."""
    rows = [row for row in client.market_events if row["session_id"] == SESSION]
    identities = [row["source_event_id"] for row in rows]
    repeated = sorted({name for name in identities if identities.count(name) > 1})
    assert not repeated, (
        "P-54 conjunct 1 (Requirement 14.7): a source_event_id was processed more than once "
        f"for session {SESSION}: {repeated}. Stream: {list(stream)}"
    )
    assert set(identities) == {identity for identity, _ in processed}, (
        "P-54 conjunct 1: the set of recorded identities is not the set the distinct-and-sorted "
        f"projection expects. Recorded {len(set(identities))}, expected {len(processed)}. "
        f"Stream: {list(stream)}"
    )


def _assert_non_decreasing_per_symbol(
    client: Any, stream: Sequence[_Candle]
) -> None:
    """Conjunct 2: ``event_timestamp`` ordered by ``sequence`` is non-decreasing per symbol."""
    for symbol in SYMBOLS:
        rows = _rows_by_symbol(client, symbol)
        stamps: List[datetime] = []
        for row in rows:
            stamp = datetime.fromisoformat(row["event_timestamp"])
            assert stamp.tzinfo is not None, (
                f"P-54 conjunct 2: {symbol} recorded event_timestamp {row['event_timestamp']!r} "
                "without a zone; paper_market_events.event_timestamp is TIMESTAMPTZ and a naive "
                "instant would be reinterpreted as local time"
            )
            stamps.append(stamp)
        for index, (earlier, later) in enumerate(zip(stamps, stamps[1:])):
            assert earlier <= later, (
                f"P-54 conjunct 2 (Requirement 14.7): {symbol} processed {later.isoformat()} at "
                f"sequence {rows[index + 1]['sequence']} after {earlier.isoformat()} at "
                f"sequence {rows[index]['sequence']}, so the per-symbol timestamp sequence "
                f"decreased. Stream: {list(stream)}"
            )


def _assert_row_count_equals_distinct_processed(
    client: Any, processed: Sequence[Tuple[str, _Candle]], stream: Sequence[_Candle]
) -> None:
    """Conjunct 3: the row count is the distinct processed count, and no sequence is skipped."""
    rows = [row for row in client.market_events if row["session_id"] == SESSION]
    assert len(rows) == len(processed), (
        "P-54 conjunct 3 (Requirements 14.7, 15.5): paper_market_events holds "
        f"{len(rows)} row(s) for the session and the distinct-and-sorted projection of the "
        f"stream has {len(processed)} event(s). Stream: {list(stream)}"
    )
    for symbol in SYMBOLS:
        sequences = [row["sequence"] for row in _rows_by_symbol(client, symbol)]
        assert sequences == list(range(1, len(sequences) + 1)), (
            f"P-54 conjunct 3 (Requirement 15.5): {symbol}'s recorded sequences are "
            f"{sequences}, not 1..{len(sequences)}. A hole is an event a replay reads as lost, "
            f"and a repeat is two events a replay cannot order. Stream: {list(stream)}"
        )


def _assert_the_recorded_figures_are_exact(
    client: Any, processed: Sequence[Tuple[str, _Candle]]
) -> None:
    """Every processed event's price, volume and instant, compared exactly. No tolerance."""
    by_identity = {
        row["source_event_id"]: row
        for row in client.market_events
        if row["session_id"] == SESSION
    }
    for identity, candle in processed:
        row = by_identity[identity]
        recorded_close = Decimal(row["payload"]["close"])
        recorded_volume = Decimal(row["payload"]["volume"])
        assert recorded_close == candle.close, (
            f"P-54: {candle!r} was recorded with close {recorded_close}, not {candle.close}"
        )
        assert recorded_volume == candle.volume, (
            f"P-54: {candle!r} was recorded with volume {recorded_volume}, not {candle.volume}"
        )
        assert datetime.fromisoformat(row["event_timestamp"]) == candle.event_timestamp, (
            f"P-54: {candle!r} was recorded at {row['event_timestamp']!r}, not "
            f"{candle.event_timestamp.isoformat()}"
        )


def _assert_the_returned_events_match_the_projection(
    results: Sequence[Optional[Any]],
    processed: Sequence[Tuple[str, _Candle]],
    verdicts: Sequence[_Verdict],
    stream: Sequence[_Candle],
) -> None:
    """The per-candle accept/drop decision, position by position, against the oracle.

    Stronger than counting rows: it names *which* candle the module and the projection disagree
    about. A drop is a ``None`` return and never an exception, which is asserted implicitly by
    ``_run_stream`` having completed.
    """
    observed = ["accepted" if result is not None else "dropped" for result in results]
    expected = [
        "accepted" if verdict.outcome == ACCEPTED else "dropped" for verdict in verdicts
    ]
    assert observed == expected, (
        "P-54: the feed's per-candle decision differs from the distinct-and-sorted projection. "
        + ", ".join(
            f"candle {index} {stream[index]!r}: feed {was}, projection {want}"
            for index, (was, want) in enumerate(zip(observed, expected))
            if was != want
        )
    )

    accepted = [result for result in results if result is not None]
    assert [result.source_event_id for result in accepted] == [
        identity for identity, _ in processed
    ], (
        "P-54: the identities the feed returned, in order, are not the projection's. The "
        "identity is recomputed in this file, so a disagreement is a disagreement about the "
        f"convention itself. Stream: {list(stream)}"
    )
    for result, (_identity_text, candle) in zip(accepted, processed):
        assert result.close == candle.close and result.volume == candle.volume, (
            f"P-54: the feed returned close {result.close} volume {result.volume} for "
            f"{candle!r}"
        )
        assert result.event_timestamp == candle.event_timestamp, (
            f"P-54: the feed returned {result.event_timestamp.isoformat()} for {candle!r}"
        )


# ══════════════════════════════════════════════════════════════════════════
# THE CENSUS - WHAT MAKES THE RUN NON-VACUOUS
# ══════════════════════════════════════════════════════════════════════════

CENSUS_KEYS: Tuple[str, ...] = (
    "examples",
    "candles",
    "accepted",
    "dropped_duplicate",
    "dropped_duplicate_respelled",
    "dropped_duplicate_at_equal_timestamp",
    "dropped_out_of_order",
    "accepted_revision_at_equal_timestamp",
    "both_symbols_present",
    "both_symbols_recorded",
    "accepted_below_another_symbols_high_water",
    "tail_beyond_the_spine",
)

#: The floor for each bucket, and every floor that reads 100 is the example count: the spine in
#: :func:`market_event_streams` forces that case into EVERY example, so anything below 100 means
#: the forcing stopped working - not that the case is rare. ``tail_beyond_the_spine`` is the one
#: bucket the generator leaves to chance, so its floor is a distribution claim rather than a
#: forcing claim. A shortfall is fixed by forcing the case in the generator, never by lowering
#: the number here.
CENSUS_FLOORS: Mapping[str, int] = {
    "examples": 100,
    "candles": 800,
    "accepted": 100,
    "dropped_duplicate": 100,
    "dropped_duplicate_respelled": 100,
    "dropped_duplicate_at_equal_timestamp": 100,
    "dropped_out_of_order": 100,
    "accepted_revision_at_equal_timestamp": 100,
    "both_symbols_present": 100,
    "both_symbols_recorded": 100,
    "accepted_below_another_symbols_high_water": 100,
    "tail_beyond_the_spine": 25,
}

#: The buckets also emitted as a Hypothesis ``event``, so the observed distribution is printed by
#: ``--hypothesis-show-statistics`` rather than only asserted.
EVENT_LABELS: Mapping[str, str] = {
    "dropped_duplicate": "a duplicate source_event_id was dropped",
    "dropped_duplicate_respelled": "a respelled republication was dropped",
    "dropped_duplicate_at_equal_timestamp": "a duplicate arrived at the high-water instant",
    "dropped_out_of_order": "an out-of-order candle was dropped",
    "accepted_revision_at_equal_timestamp": "a revision at the high-water instant was processed",
    "both_symbols_recorded": "both symbols recorded an event",
    "accepted_below_another_symbols_high_water": (
        "a symbol was processed below another symbol's high-water mark"
    ),
    "tail_beyond_the_spine": "the stream carried candles beyond the forced spine",
}


def _new_census() -> Dict[str, int]:
    return {key: 0 for key in CENSUS_KEYS}


def _record(
    census: Dict[str, int],
    stream: Sequence[_Candle],
    verdicts: Sequence[_Verdict],
    processed: Sequence[Tuple[str, _Candle]],
) -> None:
    """Count what this example actually exercised, as the ORACLE classified it.

    Classified by the projection rather than by the spine's intent, so an example in which a
    forced case was reclassified is reported by the floors instead of being assumed away.
    """
    census["examples"] += 1
    census["candles"] += len(stream)

    seen: Dict[str, bool] = {key: False for key in EVENT_LABELS}

    def mark(key: str) -> None:
        census[key] += 1
        if key in seen:
            seen[key] = True

    if len(stream) > SPINE_LENGTH:
        mark("tail_beyond_the_spine")
    if len({candle.symbol for candle in stream}) > 1:
        mark("both_symbols_present")
    if len({candle.symbol for _, candle in processed}) > 1:
        mark("both_symbols_recorded")

    for verdict in verdicts:
        if verdict.outcome == ACCEPTED:
            mark("accepted")
            if verdict.at_equal_high_water:
                mark("accepted_revision_at_equal_timestamp")
            if verdict.below_other_symbol:
                mark("accepted_below_another_symbols_high_water")
        elif verdict.outcome == DROPPED_DUPLICATE:
            mark("dropped_duplicate")
            if verdict.respelled:
                mark("dropped_duplicate_respelled")
            if verdict.at_equal_high_water:
                mark("dropped_duplicate_at_equal_timestamp")
        else:
            mark("dropped_out_of_order")

    for key, label in EVENT_LABELS.items():
        if seen[key]:
            event(label)


# ══════════════════════════════════════════════════════════════════════════
# P-54
# ══════════════════════════════════════════════════════════════════════════


#: The helper moved to ``tests/property/paper_census.py`` at tasks 25.11-25.13, when the fourth
#: paper property module needed the same nested-``check`` statistics collector. The alias is kept
#: so the call site below still reads the way this module describes it.
_publish_hypothesis_statistics = publish_hypothesis_statistics


def test_p54_market_events_are_deduplicated_and_monotonic(
    request: Any,
    monkeypatch: pytest.MonkeyPatch,
    counters: Any,
    no_sleeping: List[Any],
) -> None:
    """A session processes each event identity once, in non-decreasing order per symbol.

    For all generated streams containing duplicated ``source_event_id`` values and out-of-order
    timestamps: each ``source_event_id`` is processed at most once per session, the processed
    timestamp sequence per symbol is non-decreasing, and the ``paper_market_events`` row count
    equals the distinct processed count. The oracle is this file's own distinct-and-sorted
    projection of the stream; prices and volumes are compared as exact ``Decimal`` and instants
    as tz-aware ``datetime``, with no tolerance.

    **Validates: Requirements 14.7, 15.5**
    """
    # The one clock the feed reads, pinned. Requirement 14.10's latency is then the exact
    # difference between two known instants rather than a figure that depends on when the suite
    # ran - and paper_market_events.latency_ms is NUMERIC(10,3), which a candle hours behind a
    # real clock would overflow.
    monkeypatch.setattr(feed, "_utc_now", lambda: PROCESSED_AT)

    census = _new_census()

    @PROPERTY_SETTINGS
    @given(stream=market_event_streams())
    def check(stream: List[_Candle]) -> None:
        repo.reset_persistence_probe()

        processed, verdicts = _project(stream)
        client, results = _run_stream(stream)
        _record(census, stream, verdicts, processed)

        # No frame ran the queue dry, so nothing below is about a dropped subscription: an
        # outage would have written one paper_error record and slept a backoff delay.
        assert client.events == [], (
            "the subscription dropped during the run, so the drops below are Requirement 14.5's "
            f"and not Requirement 14.7's: {client.events}"
        )
        assert no_sleeping == [], (
            f"a reconnection backoff was slept, so a subscription dropped: {no_sleeping}"
        )

        _assert_each_identity_at_most_once(client, processed, stream)
        _assert_non_decreasing_per_symbol(client, stream)
        _assert_row_count_equals_distinct_processed(client, processed, stream)
        _assert_the_recorded_figures_are_exact(client, processed)
        _assert_the_returned_events_match_the_projection(
            results, processed, verdicts, stream
        )

    try:
        with _publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    shortfalls = {
        key: (census[key], floor)
        for key, floor in CENSUS_FLOORS.items()
        if census[key] < floor
    }
    assert not shortfalls, (
        "P-54 would be vacuous: "
        + ", ".join(
            f"{key} occurred {seen} time(s), floor {floor}"
            for key, (seen, floor) in sorted(shortfalls.items())
        )
        + f" -- full census {census}. Fix a shortfall by FORCING the case in the generator, "
        "never by lowering the floor."
    )


# ══════════════════════════════════════════════════════════════════════════
# THE ORACLE'S OWN TABLE, GUARDED
# ══════════════════════════════════════════════════════════════════════════


def test_the_spelling_families_are_coherent() -> None:
    """Every wire spelling denotes its family's number, and the canonical text is canonical.

    The oracle's independence rests on :data:`CLOSE_FAMILIES` and :data:`VOLUME_FAMILIES` being
    a correct statement about decimal numbers, made here rather than borrowed from
    ``feed.canonical_number``. If a spelling in a family were a *different* number, the oracle
    would expect a republication where the feed correctly saw a revision, and P-54 would fail for
    a reason that had nothing to do with the module. So the table is checked against ``Decimal``
    itself: same value, and a canonical text that carries no exponent and no trailing zero.
    """
    for families in (CLOSE_FAMILIES, VOLUME_FAMILIES):
        for canonical, spellings in families:
            expected = Decimal(canonical)
            assert canonical == format(expected.normalize(), "f"), (
                f"{canonical!r} is not written in the canonical form the identity uses "
                "(no exponent, no trailing fractional zero)"
            )
            for wire in spellings:
                assert Decimal(wire) == expected, (
                    f"{wire!r} is not the same number as {canonical!r}, so it is a revision "
                    "and not a republication"
                )
            assert len(set(spellings)) == len(spellings), (
                f"{canonical!r} lists a spelling twice, so the respelling draw could return "
                "two identical texts and the respelled-republication case would not be forced"
            )

    # And the two halves of Requirement 14.7 are genuinely different claims about the table:
    # distinct canonical texts must be distinct numbers, or a "revision" would be a
    # republication and the equal-timestamp arbitration would have nothing to arbitrate.
    for families in (CLOSE_FAMILIES, VOLUME_FAMILIES):
        canonicals = [canonical for canonical, _ in families]
        assert len({Decimal(text) for text in canonicals}) == len(canonicals)


def test_the_wire_carries_the_exact_spelling_as_a_json_number() -> None:
    """``_wire_frame`` puts the generated digits on the wire, unrounded and unquoted.

    The respelled-republication case only exists if the respelling survives serialisation. This
    is the guard on that: the frame's body is parsed with the module's own decode, and the close
    that comes out is the exact ``Decimal`` the generator chose - not a string, and not a float's
    shortest representation of it.
    """
    candle = _Candle(SYMBOL, 60_000, ("6.00005E+4", "60000.5"), ("0E+1", "0"))
    body = json.loads(_wire_frame(candle)["data"])

    assert body["close"] == 6.00005e4
    assert '"close": 6.00005E+4' in _wire_frame(candle)["data"]
    assert '"volume": 0E+1' in _wire_frame(candle)["data"]

    decoded = feed.decode_payload(_wire_frame(candle)["data"])
    assert decoded is not None
    assert decoded["close"] == Decimal("6.00005E+4")
    assert isinstance(decoded["close"], Decimal)
    assert decoded["volume"] == Decimal("0E+1")
