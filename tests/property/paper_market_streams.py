"""
tests/property/paper_market_streams.py - one generated ``mds:data:*`` stream, shared.

Not a test module: the name deliberately does not match ``test_*.py``, so pytest does not collect
it and ``tests/property/test_property_coverage.py`` - which discovers by ``ast``-walking every
``test_*.py`` under ``tests/property/`` - does not read it looking for a ``test_p{n}_`` claim. Same
convention as ``tests/property/paper_census.py``.

WHY THIS FILE EXISTS
--------------------
Three property modules now drive the same generated market-data stream:

* ``test_market_event_dedupe.py`` (P-54, task 24.5) - dedupe and per-symbol monotonicity;
* ``test_no_stale_fill.py`` (P-56, task 24.6) - no fill while the feed is not ``HEALTHY``, and
  every post-reconnection fill priced from a post-reconnection validated event;
* ``test_no_synthesised_price.py`` (P-55, task 24.7) - every recorded price traces to a
  ``paper_market_events`` row under the frozen config's fee/slippage transform.

:func:`market_event_streams` and its supporting material were written for P-54. Three copies of a
generator are three generators, and the one that gets fixed is never the one that is running, so
the generator, the candle it yields, the identity convention its oracle needs, the wire builder
that puts an exact spelling on the channel and the distinct-and-sorted projection all live here and
every module imports them. P-54's docstring records the reasoning behind each; this file keeps the
reasoning next to the code rather than restating it.

WHAT IS STILL P-54's, AND WHY IT STAYED THERE
---------------------------------------------
``test_the_spelling_families_are_coherent`` and
``test_the_wire_carries_the_exact_spelling_as_a_json_number`` are guards on :data:`CLOSE_FAMILIES`,
:data:`VOLUME_FAMILIES` and :func:`_wire_frame` - the tables and the builder this file owns - and
they remain collected from ``test_market_event_dedupe.py``. A guard has to run somewhere pytest
collects, and duplicating it in three modules would be three copies of the same check.

THE ONE-SYMBOL VIEW, AND WHY BOTH NEW MODULES TAKE IT
-----------------------------------------------------
:func:`market_event_streams` drives **two** symbols, because the per-symbol half of Requirement
14.7 is vacuous on one. P-55 and P-56 are both about a Paper_Session's *execution*, and a
``SessionConfig``'s ``validated_symbols`` holds the one symbol whose exchange market metadata was
read (``paper_simulator.freeze_session_config``), so an order on the second symbol would be
rejected ``SYMBOL_NOT_VALIDATED`` before any of the arithmetic those two properties are about.
:func:`single_symbol_market_event_streams` therefore relabels every candle to one symbol.

Relabelling **strengthens** the two new properties rather than weakening them: the second symbol's
candles, which P-54 uses to show that the ordering record is per symbol, become candles of the
traded symbol at instants below its high-water mark - so every example now contains dropped
duplicates *and* dropped out-of-order candles for the symbol being traded. Those are exactly the
arrivals that must **not** end an outage (only an event that passes validation *and* is recorded
does), which is the case P-56 would otherwise have had to invent.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import combinations
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from hypothesis import strategies as st

from tests.test_paper_market_feed_selection import (
    EXCHANGE,
    PROCESSED_AT,
    SYMBOL,
    TIMEFRAME,
    _frame,
    _ms_before,
)

__all__ = [
    "ACCEPTED",
    "ALL_CLOSES",
    "ALL_VOLUMES",
    "CLOSE_FAMILIES",
    "DROPPED_DUPLICATE",
    "DROPPED_OUT_OF_ORDER",
    "ETH",
    "OFFSET_POOL",
    "OFFSET_QUADRUPLES",
    "SPINE_LENGTH",
    "SYMBOLS",
    "VOLUME_FAMILIES",
    "Candle",
    "Verdict",
    "adverse_slippage_price",
    "identity_of",
    "market_event_streams",
    "market_events_for",
    "minor_units_of",
    "money_at",
    "project",
    "quantize_at",
    "recorded_close",
    "relabel",
    "single_symbol_market_event_streams",
    "unquote_json_number",
    "wire_frame",
]

#: The second symbol. The same one ``TestOrderingIsPerSymbolAndNotGlobal`` uses, so the files
#: describe one multi-symbol session rather than several.
ETH = "ETH/USDT"

SYMBOLS: Tuple[str, ...] = (SYMBOL, ETH)

#: Candle open times, as whole minutes **before** :data:`PROCESSED_AT`. A larger offset is an
#: OLDER instant, which is what makes a late candle expressible as a larger draw.
#:
#: Eight of them, all inside 8 minutes, for two reasons. ``paper_market_events.latency_ms`` is
#: ``NUMERIC(10,3)`` and the latency is the processing instant minus the candle's own, so a
#: candle hours behind the pinned clock would be a column overflow rather than a dedupe case.
#: And eight slots against fourteen candles is what makes a repeated instant - the equal-timestamp
#: case Requirement 14.7 arbitrates by identity - common rather than rare.
OFFSET_POOL: Tuple[int, ...] = tuple(minutes * 60_000 for minutes in range(1, 9))

#: Every four of those slots, ascending - so the spine's "newest, base, late, cross" assignment
#: is one draw from an enumerated set rather than a filtered one. 70 of them.
OFFSET_QUADRUPLES: Tuple[Tuple[int, int, int, int], ...] = tuple(
    combination for combination in combinations(OFFSET_POOL, 4)
)

#: ``(canonical_text, wire_spellings)``. The canonical text is the identity's spelling of the
#: number, WRITTEN DOWN here - trailing fractional zeros removed, never exponential - so an oracle
#: states the convention instead of borrowing ``feed.canonical_number``'s answer for it. Every wire
#: spelling in a family denotes the same number as its canonical text, which is what
#: ``test_market_event_dedupe.test_the_spelling_families_are_coherent`` checks.
#:
#: The values sit inside ``_frame``'s default ``low`` 59800 / ``high`` 60100 band so the
#: platform's ``CandleIntegrityValidator`` admits every generated candle: P-54 is about dedupe and
#: ordering and a candle dropped as invalid would be a different claim, and P-55/P-56 need a
#: stream whose accepted events are the ones the generator intended.
CLOSE_FAMILIES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("60000.5", ("60000.5", "60000.50", "6.00005E+4")),
    ("60000.6", ("60000.6", "60000.60")),
    ("60001", ("60001", "60001.0", "6.0001E+4")),
    ("59900", ("59900", "59900.00", "5.99E+4")),
)

#: The same, for volume. Zero is included because ``CandleIntegrityValidator`` admits a
#: non-negative volume, and a bar in which nothing traded is the republication case at its
#: purest - two arrivals of one candle whose volume never moved.
VOLUME_FAMILIES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("1.5", ("1.5", "1.50", "1.5E+0")),
    ("2.5", ("2.5", "2.50")),
    ("0", ("0", "0.0", "0E+1")),
)


def _flatten(families: Sequence[Tuple[str, Tuple[str, ...]]]) -> Tuple[Tuple[str, str], ...]:
    """Every ``(wire_spelling, canonical_text)`` pair in ``families``."""
    return tuple(
        (wire, canonical) for canonical, spellings in families for wire in spellings
    )


ALL_CLOSES: Tuple[Tuple[str, str], ...] = _flatten(CLOSE_FAMILIES)
ALL_VOLUMES: Tuple[Tuple[str, str], ...] = _flatten(VOLUME_FAMILIES)


# ══════════════════════════════════════════════════════════════════════════
# ONE GENERATED CANDLE
# ══════════════════════════════════════════════════════════════════════════


class Candle:
    """One candle as it will appear on ``mds:data:*``, and as an oracle reads it.

    ``close_wire`` is the text the feed publishes and ``close_canonical`` is this file's
    written-down canonical spelling of the same number. Both are carried because the dedupe
    claim is precisely that the first varies while the second does not.
    """

    __slots__ = (
        "symbol",
        "offset_ms",
        "close_wire",
        "close_canonical",
        "volume_wire",
        "volume_canonical",
    )

    def __init__(
        self,
        symbol: str,
        offset_ms: int,
        close: Tuple[str, str],
        volume: Tuple[str, str],
    ) -> None:
        self.symbol = symbol
        self.offset_ms = int(offset_ms)
        self.close_wire, self.close_canonical = close
        self.volume_wire, self.volume_canonical = volume

    @property
    def timestamp_ms(self) -> int:
        """The candle's open time as the integer epoch milliseconds the wire carries."""
        return _ms_before(self.offset_ms)

    @property
    def event_timestamp(self) -> datetime:
        """The same instant as a tz-aware UTC ``datetime`` - no float division of the epoch."""
        return PROCESSED_AT - timedelta(milliseconds=self.offset_ms)

    @property
    def close(self) -> Decimal:
        return Decimal(self.close_wire)

    @property
    def volume(self) -> Decimal:
        return Decimal(self.volume_wire)

    @property
    def spelling(self) -> Tuple[str, str]:
        return (self.close_wire, self.volume_wire)

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"Candle({self.symbol!r}, -{self.offset_ms}ms, close={self.close_wire!r}, "
            f"volume={self.volume_wire!r})"
        )


def identity_of(candle: Candle) -> str:
    """Requirement 14.7's event identity, computed **here**, from this file's own material.

    ``sha256("exchange|symbol|timeframe|timestamp|close|volume")`` with the close and the volume
    in their canonical spelling. Deliberately imports, calls and wraps nothing from
    ``paper_market_feed``: an oracle built from ``feed.source_event_id`` would hold for any
    function whatsoever, including one that returned a constant.
    """
    material = (
        f"{EXCHANGE}"
        f"|{candle.symbol}"
        f"|{TIMEFRAME}"
        f"|{candle.timestamp_ms}"
        f"|{candle.close_canonical}"
        f"|{candle.volume_canonical}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════════════
# THE GENERATOR - IT FORCES THE HARD CASES RATHER THAN HOPING FOR THEM
# ══════════════════════════════════════════════════════════════════════════


def _ordered_distinct_pairs(items: Sequence[Any]) -> List[Tuple[Any, Any]]:
    """Every ordered pair of distinct members of ``items``."""
    return [(first, second) for first in items for second in items if first != second]


#: Two spellings of one number, guaranteed different texts - the respelled republication.
RESPELLINGS: Mapping[str, List[Tuple[str, str]]] = {
    canonical: _ordered_distinct_pairs(spellings)
    for canonical, spellings in CLOSE_FAMILIES
    if len(spellings) > 1
}

#: Two spellings of one volume, guaranteed different texts.
VOLUME_RESPELLINGS: Mapping[str, List[Tuple[str, str]]] = {
    canonical: _ordered_distinct_pairs(spellings)
    for canonical, spellings in VOLUME_FAMILIES
    if len(spellings) > 1
}

#: How many candles :func:`market_event_streams` forces into every example.
SPINE_LENGTH = 8


@st.composite
def _free_candles(draw: Any) -> Candle:
    """One unconstrained candle: any symbol, any slot, any spelling of any value."""
    return Candle(
        draw(st.sampled_from(SYMBOLS)),
        draw(st.sampled_from(OFFSET_POOL)),
        draw(st.sampled_from(ALL_CLOSES)),
        draw(st.sampled_from(ALL_VOLUMES)),
    )


@st.composite
def market_event_streams(draw: Any) -> List[Candle]:
    """A stream of ``mds:data:*`` candles that **contains** every case P-54 rests on.

    The eight-candle spine below is forced into every example, because a stream with no
    duplicate and no late candle satisfies all three of P-54's conjuncts trivially and would make
    the property vacuous. Everything about the spine is drawn - which symbol leads, which four bar
    instants, which two distinct closes, which two spellings of the repeated one - so forcing the
    *shape* of the case does not fix its *values*.

    The freely drawn tail is appended rather than interleaved. Interleaving could put a newer
    candle ahead of the spine and reclassify the whole of it, which would quietly empty a census
    bucket; appending keeps the spine's classification stable while still letting the tail
    produce further duplicates, further late candles and further repeated instants of its own.
    """
    primary, secondary = draw(st.sampled_from(_ordered_distinct_pairs(SYMBOLS)))

    # Four distinct bar instants, ascending by offset: index 0 is the NEWEST, index 3 the OLDEST.
    # Drawn from the enumerated 4-subsets rather than as a ``unique=True`` list, so no example is
    # rejected as invalid - a filtered draw would spend the budget on retries and, at a narrower
    # pool, could exhaust it.
    newer, base, late, cross = draw(st.sampled_from(OFFSET_QUADRUPLES))

    close_a_canonical, close_b_canonical = draw(
        st.sampled_from(
            _ordered_distinct_pairs([canonical for canonical, _ in CLOSE_FAMILIES])
        )
    )
    close_a_wire, close_a_respelled = draw(
        st.sampled_from(RESPELLINGS[close_a_canonical])
    )
    close_b_wire = draw(
        st.sampled_from(dict(CLOSE_FAMILIES)[close_b_canonical])
    )

    volume_canonical = draw(
        st.sampled_from(list(VOLUME_RESPELLINGS))
    )
    volume_wire, volume_respelled = draw(
        st.sampled_from(VOLUME_RESPELLINGS[volume_canonical])
    )

    value_a = ((close_a_wire, close_a_canonical), (volume_wire, volume_canonical))
    value_b = ((close_b_wire, close_b_canonical), (volume_wire, volume_canonical))
    respelled = (
        (close_a_respelled, close_a_canonical),
        (volume_respelled, volume_canonical),
    )

    spine: List[Candle] = [
        # 1. The candle the session processes first.
        Candle(primary, base, *value_a),
        # 2. Its exact republication - byte-identical, so the identity is unmistakably the same.
        Candle(primary, base, *value_a),
        # 3. Its RESPELLED republication - same numbers, different text on the wire. Caught only
        #    by the canonicalisation inside the identity.
        Candle(primary, base, *respelled),
        # 4. A REVISION at the same instant: the close moved, so the identity differs and the
        #    event must be processed even though its timestamp equals the high-water mark.
        Candle(primary, base, *value_b),
        # 5. A late candle for the same symbol: strictly older than the high-water mark.
        Candle(primary, late, *value_a),
        # 6. The second symbol, at an instant BELOW the first symbol's high-water mark. Admitted,
        #    because the ordering record is per symbol and not per session.
        Candle(secondary, cross, *value_a),
        # 7. A newer candle for the first symbol.
        Candle(primary, newer, *value_b),
        # 8. A republication of 6, so the second symbol's dedupe is exercised too.
        Candle(secondary, cross, *value_a),
    ]

    tail = draw(st.lists(_free_candles(), min_size=0, max_size=6))
    return spine + tail


def relabel(stream: Sequence[Candle], symbol: str) -> List[Candle]:
    """``stream`` with every candle's symbol replaced by ``symbol``.

    A copy rather than a mutation, because ``Candle`` is the value an oracle compares against and
    a generated example must read the same on a shrink as it did on the first run.
    """
    return [
        Candle(
            symbol,
            candle.offset_ms,
            (candle.close_wire, candle.close_canonical),
            (candle.volume_wire, candle.volume_canonical),
        )
        for candle in stream
    ]


@st.composite
def single_symbol_market_event_streams(draw: Any, symbol: str = SYMBOL) -> List[Candle]:
    """:func:`market_event_streams`, relabelled to one symbol. See the module docstring."""
    return relabel(draw(market_event_streams()), symbol)


# ══════════════════════════════════════════════════════════════════════════
# THE PROJECTION - THE DISTINCT-AND-SORTED READING OF THE STREAM
# ══════════════════════════════════════════════════════════════════════════

ACCEPTED = "accepted"
DROPPED_DUPLICATE = "duplicate"
DROPPED_OUT_OF_ORDER = "out_of_order"


class Verdict:
    """What the projection says about one candle, and why - so a failure names the case."""

    __slots__ = (
        "candle",
        "identity",
        "outcome",
        "at_equal_high_water",
        "below_other_symbol",
        "respelled",
    )

    def __init__(
        self,
        candle: Candle,
        identity: str,
        outcome: str,
        *,
        at_equal_high_water: bool,
        below_other_symbol: bool,
        respelled: bool,
    ) -> None:
        self.candle = candle
        self.identity = identity
        self.outcome = outcome
        self.at_equal_high_water = at_equal_high_water
        self.below_other_symbol = below_other_symbol
        self.respelled = respelled

    @property
    def accepted(self) -> bool:
        return self.outcome == ACCEPTED


def project(
    stream: Sequence[Candle],
) -> Tuple[List[Tuple[str, Candle]], List[Verdict]]:
    """The expected result: distinct by identity, then non-decreasing per symbol.

    Returns ``(processed, verdicts)`` - the events a correct session processes, in the order it
    processes them, and one verdict per input candle.

    The two filters are applied in **this** order, and the order is part of the claim: a candle
    that is both a republication and late is a republication, and a candle refused for being late
    is *not* remembered as processed - so a later arrival of that same identity, at an instant
    that is no longer below the mark, is still processed. Anything else would let one late frame
    erase an event from the replay log permanently.

    Nothing here calls ``paper_market_feed``. The identity comes from :func:`identity_of`, which
    recomputes the digest from this file's own material.
    """
    first_arrival: Dict[str, Candle] = {}
    high_water: Dict[str, datetime] = {}
    processed: List[Tuple[str, Candle]] = []
    verdicts: List[Verdict] = []

    for candle in stream:
        identity = identity_of(candle)
        stamp = candle.event_timestamp
        mark = high_water.get(candle.symbol)
        others = [
            value for symbol, value in high_water.items() if symbol != candle.symbol
        ]
        below_other = bool(others) and stamp < max(others)

        if identity in first_arrival:
            verdicts.append(
                Verdict(
                    candle,
                    identity,
                    DROPPED_DUPLICATE,
                    at_equal_high_water=mark is not None and stamp == mark,
                    below_other_symbol=below_other,
                    respelled=candle.spelling != first_arrival[identity].spelling,
                )
            )
            continue

        if mark is not None and stamp < mark:
            verdicts.append(
                Verdict(
                    candle,
                    identity,
                    DROPPED_OUT_OF_ORDER,
                    at_equal_high_water=False,
                    below_other_symbol=below_other,
                    respelled=False,
                )
            )
            continue

        verdicts.append(
            Verdict(
                candle,
                identity,
                ACCEPTED,
                at_equal_high_water=mark is not None and stamp == mark,
                below_other_symbol=below_other,
                respelled=False,
            )
        )
        first_arrival[identity] = candle
        high_water[candle.symbol] = stamp
        processed.append((identity, candle))

    return processed, verdicts


# ══════════════════════════════════════════════════════════════════════════
# THE WIRE - ONE CANDLE AS mds/main.py::broadcast_ohlcv PUBLISHES IT
# ══════════════════════════════════════════════════════════════════════════


def unquote_json_number(body: str, key: str, text: str) -> str:
    """Turn ``"key": "text"`` into ``"key": text`` in a serialised payload.

    ``_frame`` serialises with ``json.dumps``, which cannot emit an exact decimal: a ``Decimal``
    raises and a ``float`` is rendered at ``repr`` precision, so ``60000.50`` and ``6.00005E+4``
    would both reach the wire as ``60000.5`` and the respelled-republication case - the one the
    canonicalisation inside ``source_event_id`` exists for - would be destroyed before the module
    saw it. Passing the spelling as text and removing its quotes here puts the exact digits on the
    wire as a JSON **number**, which is what ``decode_payload``'s ``parse_float=Decimal`` reads.

    ``json.dumps``' default separator is ``": "``, and ``close`` and ``volume`` are the only two
    payload fields this module passes as text, so the needle is unambiguous. It is asserted
    present rather than replaced blindly: a silent no-op would leave a string on the wire and the
    test would still pass, having exercised the wrong decode branch.
    """
    needle = f'"{key}": "{text}"'
    assert needle in body, (
        f"expected {needle!r} in the serialised payload so the spelling could be put on the "
        f"wire as an exact JSON number; the frame builder's shape has changed. Body: {body!r}"
    )
    return body.replace(needle, f'"{key}": {text}')


def wire_frame(candle: Candle) -> Dict[str, Any]:
    """``candle`` as one ``mds:data:*`` frame, built by the shared ``_frame``."""
    frame = _frame(
        timestamp_ms=candle.timestamp_ms,
        close=candle.close_wire,
        volume=candle.volume_wire,
        overrides={"symbol": candle.symbol},
    )
    body = unquote_json_number(frame["data"], "close", candle.close_wire)
    body = unquote_json_number(body, "volume", candle.volume_wire)
    frame["data"] = body
    return frame


# ══════════════════════════════════════════════════════════════════════════
# THE FROZEN CONFIG'S PRICE TRANSFORM, RECOMPUTED RATHER THAN CALLED
# ══════════════════════════════════════════════════════════════════════════
#
# P-55 and P-56 both have to say what price a validated event's close is *allowed* to become
# before it reaches the ledger, and both must say it without calling the function that produced
# the value: an expectation built from ``paper_simulator.market_fill_price`` would hold for any
# function whatsoever, including one that returned a constant. So the arithmetic is written out
# here, once, and both modules import it.
#
# What is read off the session's frozen ``SessionConfig`` is only what P-55's own statement calls
# "the session's recorded fee and slippage configuration" - the two rates, the price precision, the
# minor-unit exponent and the rounding mode. The multiplication, the sign of the drift and the
# rounding are performed here.


def quantize_at(value: Any, places: int, rounding: str) -> Decimal:
    """``value`` at ``places`` decimal places under ``rounding``. Exact decimal, no float."""
    return Decimal(value).quantize(Decimal(1).scaleb(-int(places)), rounding=rounding)


def adverse_slippage_price(close: Any, side: str, config: Any) -> Decimal:
    """A market order's fill price: ``reference × (1 ± slippage_rate)``, adverse only.

    A buy slips **up** and a sell slips **down** - never the other way, because a favourable slip
    would be a price better than the market published and therefore an invented one
    (Requirements 14.9, 28.3).

    Quantized at **both** stages, and the two are not interchangeable: ``submit_intent`` records
    the reference at the session's ``price_precision`` before pricing from it, so the product is
    taken from the quantized reference and then quantized again.
    """
    reference = quantize_at(close, config.price_precision, config.rounding_mode)
    drift = config.slippage_rate if str(side).strip().lower() == "buy" else -config.slippage_rate
    return quantize_at(
        reference * (Decimal(1) + drift), config.price_precision, config.rounding_mode
    )


def money_at(value: Any, config: Any) -> Decimal:
    """``value`` at the account currency's Minor_Units scale, under the recorded rounding mode.

    The scale ``paper_fills.fee_minor`` and ``paper_fills.slippage_minor`` are stored at as exact
    integers, and the scale ``paper_equity_snapshots.position_market_value`` is quantized to.
    """
    return quantize_at(value, config.minor_unit_exponent, config.rounding_mode)


def minor_units_of(value: Any, config: Any) -> int:
    """``value`` as the exact integer Minor_Units a ``*_minor`` column stores.

    ``scaleb`` and an exactness check rather than a ``round()``: a value that is not a whole number
    of minor units at the recorded exponent means the expectation and the column disagree, which is
    a fact worth failing on rather than rounding away.
    """
    scaled = Decimal(value).scaleb(int(config.minor_unit_exponent))
    assert scaled == scaled.to_integral_value(), (
        f"{value} is not a whole number of minor units at exponent {config.minor_unit_exponent}"
    )
    return int(scaled)


def market_events_for(client: Any, session_id: str) -> List[Mapping[str, Any]]:
    """One session's ``paper_market_events`` rows, ordered by ``sequence``.

    ``sequence`` and not insertion order, because ``idx_paper_market_events (session_id,
    sequence ASC)`` is the replay read, and every claim these modules make about the recorded log
    is a claim about what a replay sees.
    """
    return sorted(
        (row for row in client.market_events if row["session_id"] == session_id),
        key=lambda row: row["sequence"],
    )


def recorded_close(row: Mapping[str, Any]) -> Optional[Decimal]:
    """The exact ``close`` a ``paper_market_events`` row stored, or ``None`` when it has none.

    ``paper_market_feed._stored_payload`` writes the five OHLCV values as decimal **strings**, so
    this reads back the exact number the feed validated - never a ``float``.
    """
    raw = row.get("payload", {}).get("close")
    return None if raw is None else Decimal(str(raw))
