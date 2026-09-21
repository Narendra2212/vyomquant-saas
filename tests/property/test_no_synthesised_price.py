"""
tests/property/test_no_synthesised_price.py - P-55, and nothing else.

Spec: marketplace-subscriptions-paper-trading task 24.7. Requirements 14.9, 16.12, 28.3.

**Property P-55 (invariant, provenance)** - for all generated Paper_Sessions, every
``paper_fills.price``, every ``paper_positions.current_price``, every price used in a
``paper_equity_snapshots`` valuation and every price in a ``market_tick`` payload is derivable by
the session's recorded fee and slippage configuration from a price present in that session's
``paper_market_events``; no recorded price is drawn from a random source, interpolated between two
events, or extrapolated beyond the last one.

WHAT A "GENERATED PAPER_SESSION" IS HERE
---------------------------------------
Four things are drawn, and together they determine every number in the ledger:

* the **market-data stream**, from ``paper_market_streams.market_event_streams`` relabelled to the
  session's one traded symbol - its closes, its volumes and its instants;
* a **settlement candle** appended to it, newer than every generated instant and carrying a
  drawn **non-zero** volume. It is forced because ``fillable_quantity`` returns the whole remaining
  quantity when an event reports no volume, so a stream whose drawn volume family is ``0`` fills
  every resting order in one go and the participation **cap** - the partial fill - would be absent
  from a third of examples. See "FORCED, NOT HOPED FOR" below;
* the session's **fee rate** and **slippage rate**, drawn from enumerated exact decimals, all
  non-zero. Those two are the "recorded fee and slippage configuration" the property names, and
  drawing them is what makes the derivation a *function of the config* rather than of one
  hard-coded pair.

Everything else follows: the reference each order is priced from, every fill price, every fee, every
slippage cost, every position revaluation and every equity point.

WHAT IS DRIVEN, AND WHAT IS NOT DOUBLED
---------------------------------------
The whole path, end to end, with nothing between the wire and the ledger stubbed:

* the real ``paper_market_feed.next_validated_event`` receives each candle over the real
  ``mds:data:*`` wire shape and writes it to ``paper_market_events``. That matters more here than
  anywhere else: the oracle **reads those rows**, so the set of admissible prices is the set the
  production feed recorded and not a list this file kept;
* the real ``paper_simulator.submit_intent`` prices and fills market orders, and the real
  ``paper_simulator.check_resting_orders`` fills resting limit orders. Both route through
  ``apply_fill``, the module's single write path, so ``paper_fills``, ``paper_positions`` and
  ``paper_equity_snapshots`` are written by the code production writes them with;
* one ``FakeSupabase`` - ``tests/test_paper_repository.FakeSupabase``, the one paper double - so
  the events the oracle reads and the fills it checks are in the same store.

THE ORACLE: THE CLOSURE OF THE RECORDED EVENT PRICES UNDER THE FROZEN CONFIG
---------------------------------------------------------------------------
:func:`_admissible_prices` builds, from ``paper_market_events`` alone:

1. every recorded ``close``, at the session's ``price_precision``;
2. each of those under the **adverse** slippage transform, both directions - which is what a market
   order's fill price is allowed to be;
3. each order's own ``limit_price``, at that same precision - which is what a resting limit order's
   fill price is allowed to be, since a resting order fills at exactly its limit with no slippage
   in either direction.

That third term is the one that could launder a synthesised price, so it is closed off separately:
:func:`_assert_the_recorded_limits_are_the_submitted_ones` asserts every ``paper_orders.limit_price``
in the store is character-for-character one of the two limits **this file submitted**. A limit is
the caller's own number, not a market price, and the term is only admissible because its value is
independently known.

The transform is ``paper_market_streams.adverse_slippage_price`` - written out there and shared with
P-56 - and it is **not** a call to ``paper_simulator.market_fill_price``. An expectation built from
the function that produced the value would hold for any function whatsoever, including one that
returned a constant. The same applies to the fee and the slippage cost:
:func:`_assert_the_costs_are_derivable` recomputes ``quantity × price × fee_rate`` and
``|price - reference| × quantity`` at the Minor_Units scale from the frozen rates, rather than
calling ``fee_amount`` or ``slippage_amount``.

WHY MEMBERSHIP IN A FINITE SET IS THE RIGHT SHAPE FOR "NOT SYNTHESISED"
----------------------------------------------------------------------
The property forbids three specific things, and set membership answers all three at once:

* **a random source.** The closure is finite and small - a handful of closes, each in three
  forms, plus two known limits. A ``random.random()`` anywhere on the path lands outside it with
  probability 1 for all practical purposes. (The structural half of the same claim -
  ``backend_app/backend/paper/`` imports no ``random`` at all - is
  ``tests/test_paper_no_random.py``'s, and it is the stronger statement about *code*; this is the
  statement about *values*.)
* **interpolation between two events.** A price at the midpoint of two closes is not the transform
  of either, so it is not in the closure. The generated stream carries several distinct closes in
  every example, which is what makes a midpoint expressible at all.
* **extrapolation beyond the last one.** A price above the highest close is admissible **only** as
  the adverse buy transform of a recorded close - the one lawful way a fill price exceeds a
  published one. Anything further out is not in the set.

WHAT IS COMPARED, AND WITH WHAT TOLERANCE
-----------------------------------------
None. Every comparison is exact ``Decimal`` equality or membership in a set of exact ``Decimal``s,
and the two ``*_minor`` columns are compared as exact integers.

FORCED, NOT HOPED FOR
---------------------
A session that never filled anything satisfies the property vacuously, so every example is made to
produce: a market **buy** filled in full at an adversely slipped price; a market **sell**, which is
the other sign of the transform; a resting limit **buy** filled at exactly its limit with zero
slippage; a **partial** fill capped by a real event volume; a position revalued at a fill price; and
an equity snapshot per applied fill. :data:`P55_FLOORS` fails the run if any of those under-fills,
and a shortfall is fixed by forcing the case in the generator - never by lowering a floor.

GAPS LEFT OPEN
--------------
* **``market_tick``'s emitter exists, and this harness does not reach it.** Task 27.2 landed it:
  ``paper_session_service.step_session`` emits one ``market_tick`` per validated event, carrying
  the event's own five OHLCV values and its ``source_event_id``. What this file drives is
  ``submit_intent`` / ``check_resting_orders`` DIRECTLY, against a generated event stream and no
  session loop - which is deliberate, because the property is about the prices the SIMULATOR
  records - so no ``market_tick`` row is produced here and the conjunct below still runs zero
  times. **No census floor claims it**, for the same reason as before: a floor that could only be
  met by this file writing the row itself would be this file asserting its own output. The named
  cases for the emitter are
  ``tests/test_task_27_session_service.py::TestTheMarketTickCarriesTheValidatedBar``, which holds
  each broadcast value against the ``paper_market_events`` row it came from - this conjunct's claim,
  on one bar. Driving the loop from here would mean generating a feed and a strategy runtime as
  well, and the price provenance it would then assert is the provenance those two cases already
  assert.
* **CLOSED: ``paper_fills.market_event_id`` used to be ``NULL`` on both fill paths.** This entry
  recorded that ``apply_fill``'s docstring claimed the column made "a fill's price provenance a
  stored fact (P-55)" while ``submit_intent`` and ``check_resting_orders`` both passed
  ``_event_text(event, "id")`` - and ``paper_market_feed.MarketEvent`` has no ``id``, because
  ``next_validated_event`` never carries out the row ``repo.insert_market_event`` returns. Both call
  sites now pass ``_event_text(event, "source_event_id")``: the identity
  ``uq_paper_market_event UNIQUE (session_id, source_event_id)`` de-duplicates on, which is the
  reading ``tests/strategies/paper_generators.py`` already used, and which
  ``paper_replay._flat_event`` copies into a reconstructed event so a replay reproduces the column
  instead of diverging in it. The provenance is therefore **stored** as well as derivable, and
  :func:`_assert_the_provenance_is_stored_and_it_is_the_right_event` asserts it per fill with three
  census floors behind it. No floor was lowered to close this: conjunct 1 got stronger, because a
  fill is now held against the close of the event its own row NAMES rather than against any recorded
  close.
* **No disconnection is generated here.** P-55 is about provenance and P-56 owns the feed-state
  gate; a session that spends part of its run ``DEGRADED`` would spend the example budget on
  refusals that produce no price. ``test_no_stale_fill.py`` drives that.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, List, Mapping, Optional, Sequence, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.paper import paper_market_feed as feed
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper import paper_simulator as sim
from backend_app.backend.paper.paper_accounting import LONG, SHORT
from backend_app.backend.paper.paper_order_state import PaperOrderState

# ── The census, written once for the paper property modules that need it. ──────────────────
from tests.property.paper_census import Recorder, publish_hypothesis_statistics

# ── The one generated stream and the one price transform, shared with P-54 and P-56. ──────
from tests.property.paper_market_streams import (
    ALL_CLOSES,
    VOLUME_FAMILIES,
    Candle,
    adverse_slippage_price,
    market_events_for,
    minor_units_of,
    money_at,
    quantize_at,
    recorded_close,
    single_symbol_market_event_streams,
    wire_frame,
)

# ── The feed harness. A second market-data double would be a second account of the wire. ──
from tests.test_paper_market_feed_events import _feed_on
from tests.test_paper_market_feed_events import (  # noqa: F401 - used by name as fixtures
    counters,
)
from tests.test_paper_market_feed_selection import (
    PROCESSED_AT,
    SESSION,
    SYMBOL,
    USER,
    _client,
)
from tests.test_paper_market_feed_selection import _config as _feed_config
from tests.test_paper_market_feed_selection import (  # noqa: F401 - autouse, used by name
    _fresh_probe,
)

# ── The simulator harness. ``_run_coroutine`` drives the process's ONE event loop; see that ─
# ── module's ``_HARNESS_LOOP`` note for why a loop per call is a correctness matter here. ──
from tests.test_paper_order_lifecycle_writes import _config as _session_config
from tests.test_paper_order_lifecycle_writes import _intent, _run_coroutine

#: ``design.md § Property-based testing configuration``: at least 100 examples, no per-example
#: deadline. One example opens a subscription, drives up to fifteen candles through the real
#: validator and applies a double-digit number of real fills, so ``too_slow`` is suppressed rather
#: than the example count being cut.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

#: Generous on purpose. Two resting limit orders at :data:`LIMIT_PRICE` lock about 120,600 between
#: them, and an order whose required funds exceeded the balance would be persisted ``REJECTED``
#: with ``INSUFFICIENT_FUNDS`` and would produce no price at all - so the balance is never the
#: binding constraint on what this property observes.
CAPITAL = Decimal("500000")

#: The resting limit order: one whole unit at a limit the stream's candles always trade through
#: (``_frame``'s default ``low`` is 59,800, so ``low <= limit`` holds for every generated candle and
#: ``limit_fill_triggered`` is true every time). One unit is far above any participation cap the
#: drawn volumes can produce, which is what makes the fill a **partial** one.
LIMIT_PRICE = "60000.00"
LIMIT_QUANTITY = "1"

#: The two market orders. The buy is filled in full at an adversely slipped price; the sell is the
#: other sign of the same transform and is small enough that it only ever partially closes the long
#: the limit fills have already opened, so no position reverses through zero.
MARKET_BUY_QUANTITY = "0.01"
MARKET_SELL_QUANTITY = "0.005"

#: The settlement candle's instant: 30 seconds before the pinned clock, which is strictly newer
#: than every member of ``paper_market_streams.OFFSET_POOL`` (60 s and up) and shares its value with
#: none of them - so it is always accepted, and its identity is always fresh.
SETTLEMENT_OFFSET_MS = 30_000

#: The volume families a settlement candle may carry: every one except ``0``. A bar that reports no
#: volume has stated nothing about liquidity, so ``fillable_quantity`` returns the whole remainder
#: rather than a cap - which is correct, and is why the *capped* case needs a bar that traded.
NON_ZERO_VOLUMES: Tuple[Tuple[str, str], ...] = tuple(
    (wire, canonical)
    for canonical, spellings in VOLUME_FAMILIES
    if Decimal(canonical) > 0
    for wire in spellings
)

#: The session's recorded rates. All non-zero, so every example records a non-zero fee and a
#: non-zero slippage cost on its market fills - the two figures Requirement 16.12 requires stored
#: at the currency's Minor_Units precision. Enumerated exact decimals rather than a
#: ``st.decimals`` draw: a rate whose product with a 60,000 price is not a whole number of minor
#: units would make ``_minor_units`` refuse the fill, which is a different property (18.2) and
#: would spend this budget on it.
FEE_RATES: Tuple[str, ...] = ("0.0010", "0.0005", "0.0020")
SLIPPAGE_RATES: Tuple[str, ...] = ("0.0005", "0.0010", "0.0025")

#: The ``paper_sessions`` row handed to the simulator as its ``session`` argument. Only ``id`` and
#: ``user_id`` are read from it; the ``feed_state`` that decides every fill is re-read from the
#: store inside each attempt, which is why this carries none.
_ARGUMENT_ROW: Mapping[str, Any] = {"id": SESSION, "user_id": USER}


# ══════════════════════════════════════════════════════════════════════════
# THE GENERATOR
# ══════════════════════════════════════════════════════════════════════════


class _GeneratedSession:
    """One generated Paper_Session: its stream, and the two rates it froze at start."""

    __slots__ = ("stream", "fee_rate", "slippage_rate")

    def __init__(
        self, stream: List[Candle], fee_rate: Decimal, slippage_rate: Decimal
    ) -> None:
        self.stream = stream
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_GeneratedSession(fee={self.fee_rate}, slippage={self.slippage_rate}, "
            f"stream={self.stream!r})"
        )


@st.composite
def paper_sessions(draw: Any) -> _GeneratedSession:
    """A Paper_Session: a generated stream, a settlement candle, and two frozen rates.

    See "WHAT A GENERATED PAPER_SESSION IS HERE" in the module docstring for what each draw
    contributes and why the settlement candle is forced.
    """
    stream = draw(single_symbol_market_event_streams())
    settlement = Candle(
        SYMBOL,
        SETTLEMENT_OFFSET_MS,
        draw(st.sampled_from(ALL_CLOSES)),
        draw(st.sampled_from(NON_ZERO_VOLUMES)),
    )
    return _GeneratedSession(
        stream + [settlement],
        Decimal(draw(st.sampled_from(FEE_RATES))),
        Decimal(draw(st.sampled_from(SLIPPAGE_RATES))),
    )


# ══════════════════════════════════════════════════════════════════════════
# THE RUN
# ══════════════════════════════════════════════════════════════════════════


class _Applied:
    """One fill that was applied, and the material the oracle needs to check it.

    ``reference`` is the number the module measured its slippage against - the event's close for a
    market order, the limit for a resting one - and it is recorded here because
    ``paper_fills.slippage_minor`` is a *cost* and a cost cannot be checked without it.
    """

    __slots__ = (
        "order_type",
        "side",
        "close",
        "reference",
        "fill",
        "position",
        "snapshot",
        "order",
    )

    def __init__(
        self,
        order_type: str,
        side: str,
        close: Optional[Decimal],
        reference: Decimal,
        outcome: sim.FillOutcome,
    ) -> None:
        self.order_type = order_type
        self.side = side
        self.close = close
        self.reference = reference
        self.fill = outcome.fill
        self.position = outcome.position
        self.snapshot = outcome.snapshot
        self.order = outcome.order

    @property
    def price(self) -> Decimal:
        return Decimal(str(self.fill["price"]))

    @property
    def quantity(self) -> Decimal:
        return Decimal(str(self.fill["quantity"]))

    def __repr__(self) -> str:  # pragma: no cover - failure output
        return (
            f"_Applied({self.order_type} {self.side} qty={self.quantity} px={self.price} "
            f"ref={self.reference} close={self.close})"
        )


def _open_session(
    session: _GeneratedSession,
) -> Tuple[Any, feed.FeedHandle, Any, str, sim.SessionConfig]:
    """A store, its isolated Paper_Account, an open subscription, and the frozen config.

    The account is created through ``paper_repository.get_or_create_account`` and the subscription
    through the feed harness's ``_feed_on``, so the premise is the premise production runs under.
    The config is frozen once, from the drawn rates, and never rebuilt - which is Requirement
    16.12's "captured at session start and unchanged for the session's lifetime" as a property of
    this driver as well as of the column.
    """
    repo.reset_persistence_probe()
    client = _client()
    account = repo.get_or_create_account(
        client, USER, "USD", SESSION, initial_capital=CAPITAL
    )
    handle, redis, _ = _feed_on([], client=client, config=_feed_config(symbol=SYMBOL))
    client.statements.clear()
    client.ops.clear()
    config = _session_config(
        fee_rate=session.fee_rate, slippage_rate=session.slippage_rate
    )
    return client, handle, redis, str(account["id"]), config


def _deliver(handle: feed.FeedHandle) -> Optional[feed.MarketEvent]:
    """One ``next_validated_event`` call on the process's one event loop."""
    return _run_coroutine(feed.next_validated_event(handle))


def _submit_limit(
    client: Any, account_id: str, config: sim.SessionConfig
) -> sim.SubmitOutcome:
    """Accept one resting limit buy. No event is needed: a limit order's reference is its limit."""
    return _run_coroutine(
        sim.submit_intent(
            client,
            _ARGUMENT_ROW,
            _intent(
                side="buy",
                order_type="limit",
                quantity=LIMIT_QUANTITY,
                limit_price=LIMIT_PRICE,
            ),
            config=config,
            account_id=account_id,
            filled_at=PROCESSED_AT,
        )
    )


def _submit_market(
    client: Any,
    account_id: str,
    config: sim.SessionConfig,
    event: feed.MarketEvent,
    *,
    side: str,
    quantity: str,
) -> sim.SubmitOutcome:
    """Accept and fill one market order priced from ``event``, by the module's own arithmetic."""
    return _run_coroutine(
        sim.submit_intent(
            client,
            _ARGUMENT_ROW,
            _intent(side=side, order_type="market", quantity=quantity),
            config=config,
            account_id=account_id,
            latest_event=event,
        )
    )


def _check_resting(
    client: Any, account_id: str, config: sim.SessionConfig, event: feed.MarketEvent
) -> List[sim.FillOutcome]:
    """Fill every resting limit order this validated event triggers, through ``apply_fill``."""
    return _run_coroutine(
        sim.check_resting_orders(
            client, _ARGUMENT_ROW, event, config=config, account_id=account_id
        )
    )


def _drive(
    session: _GeneratedSession, recorder: Recorder
) -> Tuple[Any, sim.SessionConfig, List[_Applied]]:
    """Run one generated Paper_Session to completion. Returns the store, its config and the fills.

    The action plan is keyed on the ordinal of each **accepted** event, so it does not depend on
    which of the generated candles the validator, the dedupe cache and the ordering guard let
    through - the stream is drawn, and how many events it yields is drawn with it:

    * before any event: the resting limit buy is accepted. It needs no price of its own;
    * at accepted event 0: resting orders are checked (the limit fills), then a market **buy** is
      accepted and filled in full;
    * at accepted event 1: resting orders are checked, then a market **sell** partially closes the
      long - the other sign of the slippage transform;
    * at every later accepted event: resting orders are checked;
    * immediately before the settlement candle: a **second** limit order is accepted, so the
      settlement candle's real volume caps a fill on an order with a whole unit outstanding.
    """
    client, handle, redis, account_id, config = _open_session(session)
    applied: List[_Applied] = []
    limit_reference = quantize_at(
        LIMIT_PRICE, config.price_precision, config.rounding_mode
    )

    def _collect_resting(
        outcomes: Sequence[sim.FillOutcome], event: feed.MarketEvent
    ) -> None:
        for outcome in outcomes:
            if not outcome.applied:
                continue
            applied.append(_Applied("limit", "buy", None, limit_reference, outcome))
            recorder.mark("limit_fills")
            if outcome.order["order_state"] == PaperOrderState.PARTIALLY_FILLED.value:
                recorder.mark("partial_fills")
            if event.volume == 0:
                # The bar reported no volume, so ``fillable_quantity`` returned the whole
                # remainder rather than a participation cap - which is the branch the settlement
                # candle exists to stop being the only one.
                recorder.mark("limit_fill_on_a_bar_with_no_volume")

    first = _submit_limit(client, account_id, config)
    assert first.accepted, (
        f"the resting limit order was not accepted: {first.rejection_reason!r}"
    )

    settlement_index = len(session.stream) - 1
    accepted_events = 0

    for index, candle in enumerate(session.stream):
        if index == settlement_index:
            second = _submit_limit(client, account_id, config)
            assert second.accepted, "the second resting limit order was not accepted"

        redis.frames.append(wire_frame(candle))
        event = _deliver(handle)
        if event is None:
            continue

        accepted_events += 1
        recorder.mark("accepted_events")

        _collect_resting(_check_resting(client, account_id, config, event), event)

        if accepted_events == 1:
            outcome = _submit_market(
                client,
                account_id,
                config,
                event,
                side="buy",
                quantity=MARKET_BUY_QUANTITY,
            )
            assert outcome.fill is not None and outcome.fill.applied, (
                f"the forced market buy did not fill: {outcome.rejection_reason!r}"
            )
            reference = quantize_at(
                event.close, config.price_precision, config.rounding_mode
            )
            applied.append(
                _Applied("market", "buy", event.close, reference, outcome.fill)
            )
            recorder.mark("market_fills")
            recorder.mark("market_buy_fills")
            recorder.mark("full_fills")
        elif accepted_events == 2:
            outcome = _submit_market(
                client,
                account_id,
                config,
                event,
                side="sell",
                quantity=MARKET_SELL_QUANTITY,
            )
            assert outcome.fill is not None and outcome.fill.applied, (
                f"the forced market sell did not fill: {outcome.rejection_reason!r}"
            )
            reference = quantize_at(
                event.close, config.price_precision, config.rounding_mode
            )
            applied.append(
                _Applied("market", "sell", event.close, reference, outcome.fill)
            )
            recorder.mark("market_fills")
            recorder.mark("market_sell_fills")

    recorder.mark("fills", len(applied))
    assert accepted_events >= 2, (
        "the forced spine and the settlement candle must yield at least two accepted events, or "
        f"the action plan cannot run: {session!r}"
    )
    return client, config, applied


# ══════════════════════════════════════════════════════════════════════════
# THE ORACLE - THE CLOSURE OF THE RECORDED EVENT PRICES
# ══════════════════════════════════════════════════════════════════════════


def _recorded_closes(client: Any) -> List[Decimal]:
    """Every ``close`` in this session's ``paper_market_events``, exact, in replay order."""
    closes = [recorded_close(row) for row in market_events_for(client, SESSION)]
    assert closes and all(close is not None for close in closes), (
        "the session recorded no market event, so the oracle has nothing to derive from"
    )
    return [close for close in closes if close is not None]


def _admissible_prices(
    client: Any, config: sim.SessionConfig
) -> Tuple[frozenset, frozenset, frozenset, frozenset]:
    """The closure. Returns ``(all, market_buy, market_sell, limits)``.

    Computed from ``paper_market_events`` rows and from the ``paper_orders.limit_price`` column,
    under the transform of :func:`~tests.property.paper_market_streams.adverse_slippage_price`.
    Nothing in ``paper_fills``, ``paper_positions`` or ``paper_equity_snapshots`` is consulted, so
    the set cannot have been widened by the value it is about to admit.
    """
    closes = _recorded_closes(client)
    at_precision = {
        quantize_at(close, config.price_precision, config.rounding_mode)
        for close in closes
    }
    buys = {adverse_slippage_price(close, "buy", config) for close in closes}
    sells = {adverse_slippage_price(close, "sell", config) for close in closes}
    limits = {
        quantize_at(row["limit_price"], config.price_precision, config.rounding_mode)
        for row in client.orders
        if row.get("limit_price") is not None
    }
    return (
        frozenset(at_precision | buys | sells | limits),
        frozenset(buys),
        frozenset(sells),
        frozenset(limits),
    )


# ══════════════════════════════════════════════════════════════════════════
# THE FOUR CONJUNCTS, ASSERTED SEPARATELY
# ══════════════════════════════════════════════════════════════════════════


def _assert_the_recorded_limits_are_the_submitted_ones(
    client: Any, session: _GeneratedSession
) -> None:
    """The one term of the closure that is not a market price is a number this file chose.

    Without this, "plus each order's own limit price" would be a hole big enough to launder a
    synthesised price through: a module that wrote an invented limit onto the order row and then
    filled at it would satisfy membership. Every stored limit is therefore held against
    :data:`LIMIT_PRICE`, the only limit submitted.
    """
    stored = {
        str(row["limit_price"])
        for row in client.orders
        if row.get("limit_price") is not None
    }
    expected = Decimal(LIMIT_PRICE)
    for value in sorted(stored):
        assert Decimal(value) == expected, (
            f"P-55: paper_orders records limit_price {value!r}, and the only limit this session "
            f"submitted is {LIMIT_PRICE}. A limit is the caller's own number, so a stored value "
            f"that is not the submitted one is invented. Session: {session!r}"
        )


def _assert_every_fill_price_is_derivable(
    client: Any,
    config: sim.SessionConfig,
    applied: Sequence[_Applied],
    session: _GeneratedSession,
) -> None:
    """Conjunct 1: every ``paper_fills.price`` in the store is in the closure, and attributed.

    Three readings of the same claim, and all three are needed:

    * **over the store**, so a fill written by a path this driver did not call is caught. The
      admissible set is narrowed by the order's own type and side, which is stronger than the union:
      a market buy priced at the *sell* transform of some close would be in the union and is
      refused here;
    * **against the event each fill row NAMES**, through the ``paper_fills.market_event_id``
      provenance the row stores - see :func:`_assert_the_provenance_is_stored_and_it_is_the_right_event`;
    * **over the applied outcomes**, exactly: a market fill's price is the adverse transform of the
      close of the event it was priced from, and a resting fill's price is **exactly** its limit.
    """
    admissible, buys, sells, limits = _admissible_prices(client, config)
    orders = {str(row["id"]): row for row in client.orders}

    for row in client.fills:
        price = Decimal(str(row["price"]))
        order = orders[str(row["order_id"])]
        order_type = str(order["order_type"])
        side = str(order["side"])
        allowed = limits if order_type == "limit" else (buys if side == "buy" else sells)
        assert price in admissible, (
            f"P-55 (Requirements 14.9, 28.3): paper_fills carries price {price}, which is not "
            f"derivable from any close in this session's paper_market_events under the frozen "
            f"config. Admissible: {sorted(admissible)}. Session: {session!r}"
        )
        assert price in allowed, (
            f"P-55: a {order_type} {side} fill recorded price {price}, and the prices that order "
            f"shape may fill at are {sorted(allowed)}. Session: {session!r}"
        )

    for record in applied:
        if record.order_type == "limit":
            expected = record.reference
        else:
            expected = adverse_slippage_price(record.close, record.side, config)
        assert record.price == expected, (
            f"P-55: {record!r} recorded price {record.price}; the transform of its reference under "
            f"fee_rate={config.fee_rate} slippage_rate={config.slippage_rate} is {expected}. "
            f"Exact decimal, no tolerance. Session: {session!r}"
        )


def _assert_the_provenance_is_stored_and_it_is_the_right_event(
    client: Any,
    config: sim.SessionConfig,
    session: _GeneratedSession,
    recorder: Recorder,
) -> None:
    """Conjunct 1, raised: each fill NAMES the event it was priced from, and that event proves it.

    Membership in the closure says a fill price is derivable from *some* recorded close. This says
    which one, off the row's own ``paper_fills.market_event_id`` - so provenance is a **stored**
    fact and not only a derivable one, and a fill that took the right-looking price from the wrong
    event is caught. That column used to be ``NULL`` on both fill paths, and this file recorded the
    discrepancy as an open gap rather than asserting it; ``submit_intent`` and
    ``check_resting_orders`` now pass the event's own ``source_event_id``, so the floor goes up
    instead of the claim coming down.

    The identity is ``source_event_id`` - what ``uq_paper_market_event`` de-duplicates on - and not
    the row's primary key: see ``paper_simulator.apply_fill``'s ``market_event_id`` argument for the
    reasons, of which replay reproducibility is the one that decides it.

    Both fill shapes are held to their own event:

    * a **market** fill's price must be the adverse transform of *that* event's close, exactly;
    * a **resting limit** fill's named event must be one that actually triggered the order -
      ``low <= limit`` for a buy, ``paper_simulator.limit_fill_triggered``'s own rule - so a limit
      fill cannot name a bar the market never traded through.

    Every fill in this harness is priced from an event, so a ``NULL`` here is a failure rather than
    a skip. The one production path that legitimately records ``NULL`` - a market order priced from
    an explicit ``reference`` with no event - is not driven here, and is not driven anywhere in this
    file.
    """
    by_identity = {
        str(row["source_event_id"]): row for row in market_events_for(client, SESSION)
    }
    orders = {str(row["id"]): row for row in client.orders}

    for row in client.fills:
        stored = row.get("market_event_id")
        assert stored is not None and str(stored).strip(), (
            f"P-55 (Requirements 14.9, 28.3): the paper_fills row for order "
            f"{row['order_id']} at price {row['price']} records no market_event_id, so the event "
            f"it was priced from is not a stored fact - only a derivable one. Session: {session!r}"
        )
        event_row = by_identity.get(str(stored))
        assert event_row is not None, (
            f"P-55: a fill names market_event_id {str(stored)[:24]!r}, which is not the "
            f"source_event_id of any row in this session's paper_market_events - so it claims "
            f"provenance from an event this session never validated. Recorded identities: "
            f"{sorted(identity[:16] for identity in by_identity)}. Session: {session!r}"
        )
        recorder.mark("fills_with_stored_provenance")

        price = Decimal(str(row["price"]))
        order = orders[str(row["order_id"])]
        side = str(order["side"])
        close = recorded_close(event_row)
        assert close is not None, (
            f"P-55: the paper_market_events row a fill names carries no close, so the price it "
            f"claims to come from is not recorded. Session: {session!r}"
        )

        if str(order["order_type"]) == "limit":
            assert side == "buy", (
                f"this file submits only buy limits, so the trigger rule below (low <= limit) is "
                f"the whole rule; a {side} limit reached here means the driver changed and the "
                f"sell rule (high >= limit) has to be checked too. Session: {session!r}"
            )
            limit = Decimal(str(order["limit_price"]))
            trigger = Decimal(str(event_row["payload"]["low"]))
            assert trigger <= limit, (
                f"P-55 (Requirement 14.9): a resting buy at limit {limit} names the event whose "
                f"low is {trigger}, and that bar never traded through the limit - so the fill is "
                f"attributed to an event that could not have triggered it. Session: {session!r}"
            )
            recorder.mark("limit_fills_naming_a_triggering_event")
        else:
            expected = adverse_slippage_price(close, side, config)
            assert price == expected, (
                f"P-55: a market {side} fill recorded price {price} and names the event whose "
                f"close is {close}; that close under fee_rate={config.fee_rate} "
                f"slippage_rate={config.slippage_rate} is {expected}. The stored provenance and "
                f"the stored price disagree. Session: {session!r}"
            )
            recorder.mark("market_fills_priced_from_the_event_they_name")


def _assert_the_costs_are_derivable(
    client: Any,
    config: sim.SessionConfig,
    applied: Sequence[_Applied],
    session: _GeneratedSession,
    recorder: Recorder,
) -> None:
    """The "by the session's recorded fee and slippage configuration" half, as exact integers.

    ``fee_minor`` is recomputed here as ``quantity × price × fee_rate`` at the Minor_Units scale and
    ``slippage_minor`` as ``|price - reference| × quantity`` at the same scale - the two figures
    Requirement 16.12 requires recorded. Neither ``fee_amount`` nor ``slippage_amount`` is called.

    A resting limit fill's reference **is** its limit, so its slippage is exactly zero - which is
    asserted rather than skipped: a non-zero slippage on a resting order would mean it filled at
    something other than its limit.
    """
    for record in applied:
        fee = money_at(record.quantity * record.price * config.fee_rate, config)
        slippage = money_at(
            abs(record.price - record.reference) * record.quantity, config
        )
        assert int(record.fill["fee_minor"]) == minor_units_of(fee, config), (
            f"P-55 (Requirement 16.12): {record!r} recorded fee_minor "
            f"{record.fill['fee_minor']}, and quantity × price × {config.fee_rate} at the "
            f"currency's Minor_Units scale is {minor_units_of(fee, config)}. Session: {session!r}"
        )
        assert int(record.fill["slippage_minor"]) == minor_units_of(slippage, config), (
            f"P-55 (Requirement 16.12): {record!r} recorded slippage_minor "
            f"{record.fill['slippage_minor']}, and |price - reference| × quantity at that scale is "
            f"{minor_units_of(slippage, config)}. Session: {session!r}"
        )
        if record.order_type == "limit":
            assert int(record.fill["slippage_minor"]) == 0, (
                f"P-55 (Requirement 14.9): {record!r} is a resting limit fill and recorded "
                f"non-zero slippage, so it did not fill at exactly its limit. Session: {session!r}"
            )
            recorder.mark("zero_slippage_on_a_limit_fill")
        else:
            assert int(record.fill["slippage_minor"]) > 0, (
                f"P-55: {record!r} is a market fill under slippage_rate={config.slippage_rate} and "
                f"recorded no slippage cost. Session: {session!r}"
            )
            recorder.mark("non_zero_slippage_on_a_market_fill")
        if int(record.fill["fee_minor"]) > 0:
            recorder.mark("non_zero_fee")


def _assert_every_position_price_is_derivable(
    client: Any,
    config: sim.SessionConfig,
    session: _GeneratedSession,
    recorder: Recorder,
) -> None:
    """Conjunct 2: every ``paper_positions.current_price`` in the store is in the closure.

    A position with **no** ``current_price`` is left alone rather than counted as a zero: that is
    Requirement 18.15 read literally, and ``position_of`` keeps the column ``None`` for exactly
    this reason.
    """
    admissible, _buys, _sells, _limits = _admissible_prices(client, config)
    for row in client.positions:
        raw = row.get("current_price")
        if raw is None:
            continue
        price = Decimal(str(raw))
        assert price in admissible, (
            f"P-55 (Requirements 14.9, 18.15): paper_positions for {row['symbol']} is valued at "
            f"current_price {price}, which is not derivable from any close in this session's "
            f"paper_market_events. Admissible: {sorted(admissible)}. Session: {session!r}"
        )
        recorder.mark("position_revaluations")


def _position_value(row: Mapping[str, Any], price: Decimal) -> Decimal:
    """One position's value at ``price``, under the retained SHORT convention.

    ``LONG -> size × price``; ``SHORT -> size × (2 × entry_price - price)``. Written out here rather
    than taken from ``paper_accounting.position_value``, because the claim below is that the
    persisted ``position_market_value`` is that arithmetic over a price from the closure.
    """
    size = Decimal(str(row["size"]))
    if str(row["side"]) == LONG:
        return size * price
    assert str(row["side"]) == SHORT
    return size * (Decimal(2) * Decimal(str(row["entry_price"])) - price)


def _assert_every_equity_valuation_uses_a_derivable_price(
    client: Any,
    config: sim.SessionConfig,
    applied: Sequence[_Applied],
    session: _GeneratedSession,
    recorder: Recorder,
) -> None:
    """Conjunct 3: every price used in a ``paper_equity_snapshots`` valuation is in the closure.

    A snapshot stores a *value*, not a price, so the price it used is checked by reproducing the
    valuation: ``position_market_value`` must equal this file's own
    ``money(size × price)`` over the position row that fill wrote, and that row's
    ``current_price`` must be in the closure. Both halves matter - a snapshot whose value did not
    come from the position it was taken with would pass the membership check alone.

    Every snapshot in the store is covered, because ``apply_fill`` writes exactly one per applied
    fill (Requirement 18.11) and nothing else in this run writes any: the count is asserted, so a
    snapshot from some other valuation would be reported rather than skipped.
    """
    admissible, _buys, _sells, _limits = _admissible_prices(client, config)

    assert len(client.equity_snapshots) == len(applied), (
        f"P-55: the store holds {len(client.equity_snapshots)} equity snapshot(s) and "
        f"{len(applied)} fill(s) were applied. Requirement 18.11 writes one per applied fill, so a "
        f"difference means a valuation this conjunct has not checked. Session: {session!r}"
    )

    for record in applied:
        snapshot = record.snapshot
        position = record.position
        assert snapshot is not None and position is not None
        price = position.get("current_price")
        assert price is not None, (
            f"P-55: {record!r} wrote a position with no current_price, so the equity snapshot "
            f"taken with it was valued at a price that is not recorded. Session: {session!r}"
        )
        price = Decimal(str(price))
        assert price in admissible, (
            f"P-55 (Requirements 14.9, 28.3): the equity snapshot for {record!r} was valued at "
            f"{price}, which is not derivable from any recorded close. Session: {session!r}"
        )
        expected = money_at(_position_value(position, price), config)
        recorded = Decimal(str(snapshot["position_market_value"]))
        assert recorded == expected, (
            f"P-55 (Requirement 18.3): the snapshot for {record!r} records "
            f"position_market_value {recorded}, and size × the position's own current_price at the "
            f"currency's Minor_Units scale is {expected}. Session: {session!r}"
        )
        recorder.mark("equity_snapshots")


def _assert_every_market_tick_price_is_recorded(
    client: Any, session: _GeneratedSession, recorder: Recorder
) -> None:
    """Conjunct 4: every price in a ``market_tick`` payload is one the session recorded.

    ``market_tick``'s payload is ``design.md``'s ``symbol, timestamp, open, high, low, close,
    volume, source_event_id, latency_ms, feed_state`` - the validated event, forwarded - so every
    one of its five numeric market values must be exactly the value ``paper_market_events`` holds
    for the event its ``source_event_id`` names. That is checked here rather than only ``close``,
    because a broadcast that forwarded four fields faithfully and synthesised the fifth would be
    the same defect.

    **No census floor claims this bucket.** The emitter exists as of task 27.2
    (``paper_session_service.step_session``), but this harness drives ``submit_intent`` and
    ``check_resting_orders`` directly rather than the session loop, so no ``market_tick`` row is
    produced here and this loop still runs zero times. A floor that could only be met by this file
    writing the row itself would be this file asserting its own output. See "GAPS LEFT OPEN" for
    where the emitter's own named cases are.
    """
    by_identity = {
        str(row["source_event_id"]): row for row in market_events_for(client, SESSION)
    }
    for record in client.events:
        if str(record.get("event_type")) != "market_tick":
            continue
        recorder.mark("market_tick_events")
        payload = record.get("payload") or {}
        identity = str(payload.get("source_event_id") or "")
        event_row = by_identity.get(identity)
        assert event_row is not None, (
            f"P-55 (Requirements 14.9, 28.3): a market_tick names source_event_id "
            f"{identity[:16]!r}, which is not in this session's paper_market_events - so it "
            f"carries prices from no validated event. Session: {session!r}"
        )
        for field in ("open", "high", "low", "close", "volume"):
            if field not in payload:
                continue
            broadcast = Decimal(str(payload[field]))
            stored = Decimal(str(event_row["payload"][field]))
            assert broadcast == stored, (
                f"P-55: the market_tick for {identity[:16]} broadcasts {field} {broadcast} and "
                f"paper_market_events records {stored}. Session: {session!r}"
            )


#: How many one-tick candidates :func:`_assert_the_admissible_set_is_sparse` will examine before it
#: gives up looking for an interpolated price the closure excludes. The generated closes span at
#: least 0.10 - the two closest members of ``CLOSE_FAMILIES`` - so at a ``price_precision`` of 2
#: there are always interior ticks to try, and a handful is enough to make the point.
INTERPOLATION_PROBES = 64


def _assert_the_admissible_set_is_sparse(
    client: Any,
    config: sim.SessionConfig,
    session: _GeneratedSession,
) -> None:
    """Why membership refuses an interpolated price: the closure is sparse in its own range.

    Conjunct 1 forbids "interpolated between two events" and "extrapolated beyond the last one" by
    requiring membership in a finite derived set. That only *means* anything while the set stays
    sparse: a closure that had grown to cover the price range continuously would admit every
    interpolation and the conjunct would be vacuous. So the premise is asserted rather than assumed:

    * the session recorded at least two distinct closes, or "between two events" names nothing;
    * the closure holds at most three forms of each recorded close plus the one submitted limit;
    * and there **is** a price strictly between the two extreme recorded closes that the closure
      excludes - exhibited, by walking one price tick at a time, rather than argued.

    The witness is exhibited rather than fixed at the midpoint because a particular interpolated
    value can coincide with a lawful transform of some other close; the claim is about the set being
    sparse, not about one arithmetic accident.
    """
    admissible, _buys, _sells, _limits = _admissible_prices(client, config)
    closes = sorted(
        {
            quantize_at(close, config.price_precision, config.rounding_mode)
            for close in _recorded_closes(client)
        }
    )

    assert len(closes) >= 2, (
        f"P-55 would have nothing to say about interpolation: this session recorded only "
        f"{len(closes)} distinct close(s). Session: {session!r}"
    )
    assert len(admissible) <= 3 * len(closes) + 1, (
        f"P-55: the closure has {len(admissible)} member(s) for {len(closes)} distinct recorded "
        f"close(s); it should hold at most each close, its two adverse transforms and the one "
        f"submitted limit. A set this wide would make the membership conjunct weak. "
        f"Session: {session!r}"
    )

    tick = Decimal(1).scaleb(-config.price_precision)
    excluded: List[Decimal] = []
    candidate = closes[0] + tick
    probes = 0
    while candidate < closes[-1] and probes < INTERPOLATION_PROBES:
        if candidate not in admissible:
            excluded.append(candidate)
        candidate += tick
        probes += 1

    assert probes, (
        f"P-55: the two extreme recorded closes {closes[0]} and {closes[-1]} are one tick apart, "
        f"so no interpolated price is expressible and this conjunct has nothing to check. "
        f"Session: {session!r}"
    )
    assert excluded, (
        f"P-55 (Requirement 14.9): every one of the {probes} price(s) between the extreme recorded "
        f"closes {closes[0]} and {closes[-1]} is in the closure, so an interpolated price would "
        f"pass the membership conjunct. Session: {session!r}"
    )


# ══════════════════════════════════════════════════════════════════════════
# THE CENSUS - WHAT MAKES THE RUN NON-VACUOUS
# ══════════════════════════════════════════════════════════════════════════

#: Every floor that reads 100 is the example count: the forced action plan and the settlement
#: candle put that case into EVERY example, so anything below 100 means the forcing stopped working
#: rather than that the case is rare. ``limit_fill_on_a_bar_with_no_volume`` is the one bucket left
#: to chance - it needs the stream's drawn volume family to be ``0`` - so its floor is a
#: distribution claim. ``market_tick_events`` has no floor at all, deliberately; see
#: :func:`_assert_every_market_tick_price_is_recorded`. A shortfall is fixed by FORCING the case in
#: the generator, never by lowering the number here.
P55_FLOORS: Mapping[str, int] = {
    "examples": 100,
    "accepted_events": 400,
    "fills": 400,
    "fills_with_stored_provenance": 400,
    "market_fills": 200,
    "market_fills_priced_from_the_event_they_name": 200,
    "limit_fills_naming_a_triggering_event": 100,
    "market_buy_fills": 100,
    "market_sell_fills": 100,
    "limit_fills": 100,
    "partial_fills": 100,
    "full_fills": 100,
    "position_revaluations": 100,
    "equity_snapshots": 400,
    "non_zero_fee": 400,
    "non_zero_slippage_on_a_market_fill": 200,
    "zero_slippage_on_a_limit_fill": 100,
    "limit_fill_on_a_bar_with_no_volume": 20,
    "market_tick_events": 0,
}

#: The buckets also emitted as a Hypothesis ``event``, so the observed distribution is printed by
#: ``--hypothesis-show-statistics`` rather than only asserted.
P55_LABELS: Mapping[str, str] = {
    "fills_with_stored_provenance": (
        "a fill recorded the source_event_id of the event it was priced from"
    ),
    "market_fills_priced_from_the_event_they_name": (
        "a market fill's price is the adverse transform of the close of the event it names"
    ),
    "limit_fills_naming_a_triggering_event": (
        "a resting fill names an event whose low reached its limit"
    ),
    "market_buy_fills": "a market buy filled at an adversely slipped price",
    "market_sell_fills": "a market sell filled at an adversely slipped price",
    "limit_fills": "a resting limit order filled at exactly its limit",
    "partial_fills": "a fill was capped by the event's own volume",
    "full_fills": "an order filled in full",
    "position_revaluations": "a position was revalued at a fill price",
    "non_zero_slippage_on_a_market_fill": "a market fill recorded a non-zero slippage cost",
    "zero_slippage_on_a_limit_fill": "a resting fill recorded exactly zero slippage",
    "limit_fill_on_a_bar_with_no_volume": (
        "a bar reporting no volume filled the whole remaining quantity"
    ),
    "market_tick_events": (
        "a market_tick was emitted (this harness drives the simulator, not the session loop that "
        "emits them; see the docstring)"
    ),
}


# ══════════════════════════════════════════════════════════════════════════
# P-55
# ══════════════════════════════════════════════════════════════════════════


def test_p55_no_price_is_synthesised(
    request: Any, monkeypatch: pytest.MonkeyPatch, counters: Any
) -> None:
    """Every price the ledger holds traces to a recorded market event under the frozen config.

    For all generated Paper_Sessions: every ``paper_fills.price``, every
    ``paper_positions.current_price``, every price used in a ``paper_equity_snapshots`` valuation
    and every price in a ``market_tick`` payload is a member of the closure of this session's
    ``paper_market_events`` closes under the frozen configuration's adverse-slippage transform,
    together with the order's own limit - and the recorded ``fee_minor`` and ``slippage_minor`` are
    that configuration's arithmetic over the fill's own quantity and price. Nothing is
    interpolated between two events and nothing is extrapolated beyond the last.

    Each fill additionally **names** the event it was priced from, in its own
    ``paper_fills.market_event_id``, and is held against that event rather than against the closure
    at large: a market fill's price is the adverse transform of that event's close and a resting
    fill's event is one whose low reached its limit
    (:func:`_assert_the_provenance_is_stored_and_it_is_the_right_event`).

    The oracle is :func:`_admissible_prices`, built from the ``paper_market_events`` rows the real
    feed wrote and from the limits this file submitted, under
    ``paper_market_streams.adverse_slippage_price`` - not under ``paper_simulator``'s own
    ``market_fill_price``, ``fee_amount`` or ``slippage_amount``. Exact ``Decimal`` and exact
    integer Minor_Units throughout, no tolerance.

    **Validates: Requirements 14.9, 16.12, 28.3**
    """
    # The one clock the feed reads, pinned - so paper_market_events.latency_ms is the exact
    # difference between two known instants and NUMERIC(10,3) cannot overflow.
    monkeypatch.setattr(feed, "_utc_now", lambda: PROCESSED_AT)

    recorder = Recorder("P-55", P55_FLOORS, P55_LABELS)

    @PROPERTY_SETTINGS
    @given(session=paper_sessions())
    def check(session: _GeneratedSession) -> None:
        recorder.start()
        recorder.mark("examples")

        client, config, applied = _drive(session, recorder)

        _assert_the_recorded_limits_are_the_submitted_ones(client, session)
        _assert_every_fill_price_is_derivable(client, config, applied, session)
        _assert_the_provenance_is_stored_and_it_is_the_right_event(
            client, config, session, recorder
        )
        _assert_the_costs_are_derivable(client, config, applied, session, recorder)
        _assert_every_position_price_is_derivable(client, config, session, recorder)
        _assert_every_equity_valuation_uses_a_derivable_price(
            client, config, applied, session, recorder
        )
        _assert_every_market_tick_price_is_recorded(client, session, recorder)
        _assert_the_admissible_set_is_sparse(client, config, session)

        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# THE ORACLE'S OWN MATERIAL, GUARDED
# ══════════════════════════════════════════════════════════════════════════


def test_the_drawn_rates_are_all_non_zero_and_exactly_representable() -> None:
    """:data:`FEE_RATES` and :data:`SLIPPAGE_RATES` are what the census's floors rest on.

    A zero rate would make ``non_zero_fee`` or ``non_zero_slippage_on_a_market_fill`` under-fill,
    and the shortfall would read as a simulator defect rather than as a generator change. A rate
    outside ``[0, 1]`` would be refused by ``SessionConfig`` before a single fill.
    """
    for rates in (FEE_RATES, SLIPPAGE_RATES):
        for text in rates:
            rate = Decimal(text)
            assert 0 < rate < 1, f"{text} is not a usable rate"
            assert str(rate) == text, f"{text} does not round-trip as an exact decimal"

    for fee in FEE_RATES:
        for slippage in SLIPPAGE_RATES:
            config = _session_config(
                fee_rate=Decimal(fee), slippage_rate=Decimal(slippage)
            )
            assert config.fee_rate == Decimal(fee)
            assert config.slippage_rate == Decimal(slippage)


def test_the_transform_agrees_with_the_module_on_a_value_neither_of_them_chose() -> None:
    """The oracle's transform and the simulator's must agree, and this is where that is stated.

    The transform is recomputed everywhere else precisely so it is **not** the module's answer. One
    place has to compare the two, or a disagreement would surface as a hundred confusing
    counterexamples instead of as one sentence. The comparison is over every drawn rate pair and
    both sides, against a close the generator can produce.
    """
    close = Decimal("60000.5")
    for fee in FEE_RATES:
        for slippage in SLIPPAGE_RATES:
            config = _session_config(
                fee_rate=Decimal(fee), slippage_rate=Decimal(slippage)
            )
            for side in ("buy", "sell"):
                assert adverse_slippage_price(close, side, config) == (
                    sim.market_fill_price(close, side, config)
                )
            quantity = Decimal("0.01")
            price = adverse_slippage_price(close, "buy", config)
            assert money_at(quantity * price * config.fee_rate, config) == (
                sim.fee_amount(quantity, price, config)
            )
            reference = quantize_at(
                close, config.price_precision, config.rounding_mode
            )
            assert money_at(abs(price - reference) * quantity, config) == (
                sim.slippage_amount(quantity, price, reference, config)
            )


def test_a_settlement_candle_is_newer_than_every_generated_instant() -> None:
    """:data:`SETTLEMENT_OFFSET_MS` must be accepted, or the forced partial fill never happens.

    A settlement candle at or below any generated instant would be dropped as out of order, the
    second limit order would never be offered a bar that traded, and ``partial_fills`` would
    under-fill for a reason that had nothing to do with the simulator.
    """
    from tests.property.paper_market_streams import OFFSET_POOL

    assert SETTLEMENT_OFFSET_MS < min(OFFSET_POOL), (
        "a smaller offset is a NEWER instant; the settlement candle must be newer than every "
        "candle the generator can draw"
    )
    assert SETTLEMENT_OFFSET_MS not in OFFSET_POOL, (
        "sharing an instant with a drawn candle would let the settlement candle's identity collide"
    )
    assert NON_ZERO_VOLUMES, "the settlement candle needs a volume family that traded"
    for wire, canonical in NON_ZERO_VOLUMES:
        assert Decimal(wire) == Decimal(canonical) > 0
