"""Property tests for the Paper_Accounting_Engine.

Feature: marketplace-subscriptions-paper-trading
Design reference: ``design.md § Property-to-test mapping`` ->
``P-25 … P-30 | tests/property/test_paper_accounting.py | fill_sequences(),
equity_series() | paper_replay.ReferenceLedger for balances; an independent running-peak
scan for drawdown``.

Module under test: ``backend_app/backend/paper/paper_accounting.py`` (task 9.1).

Properties living here
----------------------
``test_p25_equity_identity_holds_after_every_event``        (task 9.3, Requirement 18.3)

Tasks 9.4 … 9.8 append ``test_p26_`` … ``test_p30_`` below. Everything above the first
property function is shared on purpose: ``PROPERTY_SETTINGS``, the event replay
(:func:`replay_events`) and the independent valuation oracle (:func:`oracle_equity`) are
written once here so the later properties assert against the same event sequence rather
than five slightly different ones.

WHY THE ORACLE RESTATES THE VALUATION INSTEAD OF CALLING THE MODULE
------------------------------------------------------------------
``paper_accounting.total_equity`` is the *definition* the module under test uses, so
comparing ``account.total_equity`` against it would only check that one function agrees
with itself. The oracle here recomputes ``available + locked + sum(position value)`` from
the position fields directly, with the SHORT convention written out
(``size * (2 * entry_price - price)`` — ``design.md § Paper accounting``), so a wrong
valuation in the module cannot make the comparison pass by construction.

``invariants_hold`` is asserted *as well*, because Requirement 18.14 has the simulator gate
its transaction on that function: a state the oracle accepts but ``invariants_hold`` rejects
would roll back a legitimate fill, and that is worth failing on too.

WHY EVERY EVENT IS ASSERTED, INCLUDING THE REFUSED ONES
-------------------------------------------------------
Requirement 18.3 says the identity holds after every event, and a refusal is an event: a
fill that would drive ``available_balance`` below zero, or a lock larger than the available
cash, is rejected and — Requirement 18.14 — must leave the ledger exactly as it was. The
replay yields the post-state of refusals and duplicate deliveries as well as of applied
fills, so a refusal path that mutated the account in passing would fail here rather than
somewhere downstream.

WHY THERE IS NO ``float`` IN THIS MODULE
----------------------------------------
Requirement 18.1 forbids binary floating-point money arithmetic, and every generator in
``tests/strategies/paper_generators`` yields exact ``Decimal`` values for that reason. The
oracle is written in ``Decimal`` under an explicit ``localcontext(prec=34)`` — the same
working precision the module under test sets — so the comparison is exact equality with
zero tolerance, which is what Requirement 18.3 asks for.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Any, Dict, Iterator, List, Mapping, Sequence, Set, Tuple

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.paper.paper_accounting import (
    LONG,
    Account,
    AccountingConfig,
    InvariantViolation,
    Position,
    SnapshotOrderError,
    apply_fill,
    invariants_hold,
    lock,
    max_drawdown,
    recalculate,
    required_funds,
    unlock,
    violated_invariant,
)
from tests.strategies.paper_generators import (
    MINOR_UNIT_EXPONENT,
    equity_series,
    fill_sequences,
    market_event_streams,
    order_intents,
)

#: The configuration ``design.md § Property-based testing configuration`` prescribes for
#: every property test in this plan: at least 100 examples, no per-example deadline (the
#: first example pays import cost), and ``derandomize`` left at its default False so the
#: ``.hypothesis`` database of failing examples keeps accumulating across runs. Four
#: composite generators are drawn per example, which is slow rather than wrong, so the
#: ``too_slow`` health check is suppressed instead of the example count being cut.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

#: The working precision ``paper_accounting`` sets on every entry point. Mirrored here so a
#: product of two drawn figures is exact in the oracle too, rather than rounded at the
#: ambient context's 28 digits.
ORACLE_PRECISION = 34

_ZERO = Decimal("0")
_TWO = Decimal("2")


# ══════════════════════════════════════════════════════════════════════════
# SHARED HELPERS (P-25 … P-30)
# ══════════════════════════════════════════════════════════════════════════


def fee_of(fill: Mapping[str, Any], config: AccountingConfig) -> Decimal:
    """The ``Decimal`` fee of a drawn fill, from its integer ``fee_minor``.

    ``paper_fills.fee_minor`` is an integer count of Minor_Units, which is how the
    generators yield it and how the column stores it; ``apply_fill`` takes a ``Decimal`` at
    the currency's Minor_Units scale. ``scaleb`` shifts the exponent without touching the
    coefficient, so the conversion is exact and no ``float`` exists at any point.
    """
    return Decimal(int(fill["fee_minor"])).scaleb(-config.minor_unit_exponent)


def prices_of(positions: Mapping[str, Position]) -> Dict[str, Decimal]:
    """``{symbol: current_price}`` for every position that has been priced.

    The prices the current state was valued at: every position that exists is the result of
    a fill or a revaluation that recorded one. Written out here rather than delegating to
    ``paper_accounting.last_validated_prices`` so the assertion's price map is assembled
    independently of the module being asserted about.
    """
    return {
        position.symbol: position.current_price
        for position in positions.values()
        if position.current_price is not None
    }


def oracle_position_value(
    position: Position, price: Decimal, config: AccountingConfig
) -> Decimal:
    """One position's market value at ``price``, computed from the position's own fields.

    ``LONG`` -> ``size * price``; ``SHORT`` -> ``size * (2 * entry_price - price)`` — the
    convention ``design.md`` retains from ``paper_trading_service._recalculate_account``,
    written out here rather than imported. Quantized to the account currency's Minor_Units
    scale under the session's recorded rounding mode, per position, because that is the
    scale the engine sums at (Requirement 18.2).
    """
    with localcontext() as ctx:
        ctx.prec = ORACLE_PRECISION
        if position.side == LONG:
            raw = position.size * price
        else:
            raw = position.size * (_TWO * position.entry_price - price)
        return raw.quantize(config.money_quantum, rounding=config.rounding_mode)


def oracle_equity(
    account: Account,
    positions: Mapping[str, Position],
    prices: Mapping[str, Decimal],
    config: AccountingConfig,
) -> Decimal:
    """``available_balance + locked_balance + position_market_value``, recomputed.

    Requirement 18.3's right-hand side, summed over the **open** positions only: a position
    of exactly zero size is worth zero and is retained rather than deleted
    (Requirement 18.5), so it contributes nothing without needing a tolerance.
    """
    with localcontext() as ctx:
        ctx.prec = ORACLE_PRECISION
        market_value = _ZERO
        for position in positions.values():
            if position.size <= _ZERO:
                continue
            market_value += oracle_position_value(
                position, prices[position.symbol], config
            )
        return account.available_balance + account.locked_balance + market_value


def replay_events(
    case: Mapping[str, Any],
    intents: Sequence[Mapping[str, Any]],
    stream: Mapping[str, Any],
    config: AccountingConfig,
) -> Iterator[Tuple[str, Account, Dict[str, Position], Dict[str, Decimal]]]:
    """Drive one session through every kind of accounting event, yielding each post-state.

    Yields ``(label, account, positions, prices)`` after each event, where ``prices`` is the
    price map the state was last valued at. The event order is the order a live session
    produces them in, with the fills first so they see the full opening balance rather than
    a balance already reserved against resting orders:

    ==================  ==============================================================
    ``SESSION_START``   the opening account: all cash, no positions
    ``FILL``            one ``apply_fill`` from ``fill_sequences()``
    ``DUPLICATE_FILL``  a re-delivered ``fill_event_id``, absorbed as a no-op (Req 16.9)
    ``FILL_REFUSED``    a fill that would breach Requirement 18.4; state unchanged
    ``LOCK``            ``required_funds`` reserved against an ``order_intents()`` intent
    ``LOCK_REFUSED``    a reservation larger than the available cash; state unchanged
    ``UNLOCK``          that reservation released again
    ``REVALUATION``     ``recalculate`` at a ``market_event_streams()`` close price
    ==================  ==============================================================

    The refusal branches are yielded rather than swallowed: Requirement 18.14 makes "the
    stored state is unchanged" part of what a refusal means, and a state that stops
    satisfying the identity because a rejected fill mutated something halfway through is
    exactly the failure this replay exists to catch.
    """
    account = Account(
        available_balance=case["initial_balance"],
        locked_balance=_ZERO,
        realized_pnl=_ZERO,
        total_equity=case["initial_balance"],
        currency=case["currency"],
    )
    positions: Dict[str, Position] = {}
    yield "SESSION_START", account, positions, {}

    order = case["order"]

    # -- fills --------------------------------------------------------------------
    applied: Set[str] = set()
    for fill in case["fills"]:
        event_id = fill["fill_event_id"]
        if event_id in applied:
            # ``uq_paper_fill_event`` absorbs a re-delivery before the arithmetic runs
            # (Requirement 16.9), so the post-state of a duplicate is the state itself.
            yield f"DUPLICATE_FILL {event_id}", account, positions, prices_of(positions)
            continue
        try:
            result = apply_fill(
                account,
                positions,
                order,
                fill["quantity"],
                fill["price"],
                fee_of(fill, config),
                config,
                filled_at=fill["filled_at"],
            )
        except InvariantViolation:
            yield f"FILL_REFUSED {event_id}", account, positions, prices_of(positions)
            continue
        applied.add(event_id)
        account = result.account
        positions = dict(result.positions)
        yield f"FILL {event_id}", account, positions, prices_of(positions)

    # -- reservations -------------------------------------------------------------
    reserved: List[Decimal] = []
    for intent in intents:
        key = intent["idempotency_key"]
        reference_price = (
            intent["limit_price"]
            if intent["limit_price"] is not None
            else order["reference_price"]
        )
        amount = required_funds(intent, reference_price, config)
        try:
            account = lock(account, amount, config)
        except InvariantViolation:
            yield f"LOCK_REFUSED {key}", account, positions, prices_of(positions)
            continue
        reserved.append(amount)
        yield f"LOCK {key}", account, positions, prices_of(positions)

    for index, amount in enumerate(reserved):
        account = unlock(account, amount, config)
        yield f"UNLOCK {index}", account, positions, prices_of(positions)

    # -- revaluations -------------------------------------------------------------
    for index, event in enumerate(stream["events"]):
        prices = prices_of(positions)
        prices[event["symbol"]] = event["close"]
        valuation = recalculate(
            account, positions, prices, config, price_at=event["timestamp"]
        )
        account = valuation.account
        positions = dict(valuation.positions)
        yield f"REVALUATION {index}", account, positions, prices


def account_of_snapshot(
    snapshot: Mapping[str, Any], currency: str
) -> Tuple[Account, Dict[str, Position], Dict[str, Decimal]]:
    """A persisted ``paper_equity_snapshots`` row as an account, a book and a price map.

    Requirement 18.3's identity is a property of a *stored* row as much as of an in-process
    state — ``violated_invariant`` exists to catch a persisted ``total_equity`` that no
    longer equals the sum. The row records a ``position_market_value`` but not the positions
    behind it, so it is reconstituted as a single unit-size ``LONG`` priced at that value:
    ``size * price`` with ``size == 1`` is the value itself, exactly, at the Minor_Units
    scale the snapshot already carries.
    """
    symbol = "SNAPSHOT/BOOK"
    value = snapshot["position_market_value"]
    account = Account(
        available_balance=snapshot["available_balance"],
        locked_balance=snapshot["locked_balance"],
        realized_pnl=_ZERO,
        total_equity=snapshot["total_equity"],
        currency=currency,
    )
    positions = {
        symbol: Position(
            symbol=symbol,
            side=LONG,
            size=Decimal("1"),
            entry_price=value,
            current_price=value,
            price_at=snapshot["taken_at"],
        )
    }
    return account, positions, {symbol: value}


# ══════════════════════════════════════════════════════════════════════════
# P-25
# ══════════════════════════════════════════════════════════════════════════


# Feature: marketplace-subscriptions-paper-trading, Property 25 (invariant, equity
# identity): For all generated event sequences applied to a Paper_Account:
# total_equity == available_balance + locked_balance + position_market_value after every
# event.
@PROPERTY_SETTINGS
@given(
    case=fill_sequences(max_fills=3),
    intents=order_intents(max_size=3),
    stream=market_event_streams(max_events=4),
    snapshots=equity_series(max_size=6),
)
def test_p25_equity_identity_holds_after_every_event(
    case: Mapping[str, Any],
    intents: Sequence[Mapping[str, Any]],
    stream: Mapping[str, Any],
    snapshots: Sequence[Mapping[str, Any]],
) -> None:
    """``total_equity`` is the sum of cash and position value after every event, exactly.

    **Validates: Requirements 18.3**
    """
    config = AccountingConfig.from_session_config(case["config"])

    for label, account, positions, prices in replay_events(
        case, intents, stream, config
    ):
        expected = oracle_equity(account, positions, prices, config)

        # The property itself, as exact Decimal equality. No tolerance: the 0.05 the
        # existing paper_trading_service.verify_accounting_invariants allows is precisely
        # what let a cash-less short close go unnoticed.
        book = [
            (p.symbol, p.side, str(p.size), str(p.entry_price))
            for p in positions.values()
        ]
        priced = sorted((symbol, str(p)) for symbol, p in prices.items())
        assert account.total_equity == expected, (
            f"after {label}: total_equity {account.total_equity} != available_balance "
            f"{account.available_balance} + locked_balance {account.locked_balance} + "
            f"position_market_value "
            f"{expected - account.available_balance - account.locked_balance} "
            f"(= {expected}), a discrepancy of {account.total_equity - expected}; "
            f"positions={book} prices={priced}"
        )

        # The engine's own gate must agree, because paper_simulator rolls its transaction
        # back on this function (Requirement 18.14): a state the identity accepts but
        # invariants_hold rejects would discard a legitimate fill.
        breach = violated_invariant(account, positions, prices, config)
        assert breach is None, (
            f"after {label}: violated_invariant reported {breach!r} on a state whose "
            f"equity identity holds (total_equity {account.total_equity})"
        )
        assert invariants_hold(account, positions, prices, config), (
            f"after {label}: invariants_hold is False while violated_invariant named "
            "nothing - the two disagree"
        )

    # The same identity on a persisted equity snapshot: a stored row whose total_equity has
    # drifted from the sum is what Requirement 18.14 exists to detect and roll back.
    for snapshot in snapshots:
        account, positions, prices = account_of_snapshot(snapshot, case["currency"])
        expected = oracle_equity(account, positions, prices, config)
        assert account.total_equity == expected, (
            f"persisted snapshot {snapshot['series_index']} "
            f"({snapshot['cause']}): total_equity {account.total_equity} != "
            f"{expected}, a discrepancy of {account.total_equity - expected}"
        )
        breach = violated_invariant(account, positions, prices, config)
        assert breach is None, (
            f"persisted snapshot {snapshot['series_index']} ({snapshot['cause']}) was "
            f"rejected as {breach!r} although its equity identity holds"
        )


# ══════════════════════════════════════════════════════════════════════════
# P-26
# ══════════════════════════════════════════════════════════════════════════


#: The two side values Requirement 18.5 names, written as literals rather than imported from
#: ``paper_accounting.SIDES``. The requirement fixes the vocabulary ("exactly one of ``LONG``
#: or ``SHORT``"), so asserting against the module's own constant would let a renamed
#: constant pass a test whose whole point is that the persisted vocabulary is those two
#: strings — ``chk`` on ``paper_positions.side`` allows nothing else.
SPEC_SIDES = ("LONG", "SHORT")

#: The invariant names ``violated_invariant`` uses for the three clauses this property owns,
#: plus the side clause. ``'total_equity'`` is deliberately absent: it belongs to P-25, and a
#: failure there should not also be reported here as a non-negativity breach.
NON_NEGATIVITY_INVARIANTS = frozenset(
    {"available_balance", "locked_balance", "position_size", "position_side"}
)


# Feature: marketplace-subscriptions-paper-trading, Property 26 (invariant,
# non-negativity): For all generated event sequences: available_balance >= 0,
# locked_balance >= 0, and every position size >= 0 after every event.
@PROPERTY_SETTINGS
@given(
    case=fill_sequences(max_fills=3),
    intents=order_intents(max_size=3),
    stream=market_event_streams(max_events=4),
    snapshots=equity_series(max_size=6),
)
def test_p26_balances_and_position_sizes_are_non_negative(
    case: Mapping[str, Any],
    intents: Sequence[Mapping[str, Any]],
    stream: Mapping[str, Any],
    snapshots: Sequence[Mapping[str, Any]],
) -> None:
    """Cash balances and position sizes never go negative, and direction lives in ``side``.

    Requirement 18.4 bounds both cash balances at zero; Requirement 18.5 bounds every
    position size at zero *and* fixes how a short is represented — an explicit ``side`` of
    ``LONG`` or ``SHORT``, never a negative size. The two clauses are asserted together
    because they are the same failure seen from two angles: an engine that encoded a short as
    ``size = -3`` would satisfy "the size is negative only when it should be" and still break
    every consumer of ``paper_positions.size``, whose ``CHECK (size >= 0)`` would reject the
    row outright.

    The replay drives fills, duplicate deliveries, refusals, reservations, releases and
    revaluations, and each post-state is asserted — including the refused ones, because
    Requirement 18.4 is enforced *by* refusing (``apply_fill`` raises rather than writing a
    negative balance), and a refusal that had already decremented the balance before
    discovering it would go negative is precisely the bug this catches.

    **Validates: Requirements 18.4, 18.5**
    """
    config = AccountingConfig.from_session_config(case["config"])

    # Requirement 18.5's "a fully closed position's size is set to exactly zero" implies the
    # row survives: a closed position that vanished from the book would trivially satisfy
    # every size check while losing the history the trade list is drawn from.
    seen_symbols: Set[str] = set()

    for label, account, positions, prices in replay_events(
        case, intents, stream, config
    ):
        assert account.available_balance >= _ZERO, (
            f"after {label}: available_balance is {account.available_balance}, below zero "
            f"(Requirement 18.4); locked_balance {account.locked_balance}"
        )
        assert account.locked_balance >= _ZERO, (
            f"after {label}: locked_balance is {account.locked_balance}, below zero "
            f"(Requirement 18.4); available_balance {account.available_balance}"
        )

        for symbol, position in positions.items():
            assert position.size >= _ZERO, (
                f"after {label}: position {symbol} has size {position.size}, below zero "
                f"(Requirement 18.5); side={position.side} "
                f"entry_price={position.entry_price}"
            )
            assert position.side in SPEC_SIDES, (
                f"after {label}: position {symbol} has side {position.side!r}, not one of "
                f"{SPEC_SIDES} - Requirement 18.5 makes direction an explicit side value, "
                f"never a negative size (size={position.size})"
            )
            # A closed position is exactly zero, not a residue under some tolerance: the
            # engine must not carry 1E-11 of a coin forward as if the trade were still open.
            if position.closed_at is not None:
                assert position.size == _ZERO, (
                    f"after {label}: position {symbol} is closed at {position.closed_at} "
                    f"but still holds size {position.size} (Requirement 18.5 sets a fully "
                    "closed position to exactly zero)"
                )

        assert seen_symbols <= set(positions), (
            f"after {label}: position(s) {sorted(seen_symbols - set(positions))} left the "
            "book - Requirement 18.5 sets a fully closed position's size to exactly zero "
            "rather than deleting it"
        )
        seen_symbols |= set(positions)

        # The engine's own gate must name none of these three clauses, since none of them is
        # breached: paper_simulator rolls its transaction back on this function
        # (Requirement 18.14), so a false positive here discards a legitimate fill.
        breach = violated_invariant(account, positions, prices, config)
        assert breach not in NON_NEGATIVITY_INVARIANTS, (
            f"after {label}: violated_invariant reported {breach!r} on a state whose "
            f"balances ({account.available_balance}, {account.locked_balance}) and sizes "
            f"({[str(p.size) for p in positions.values()]}) are all at or above zero"
        )

    # A negative size is not representable, rather than merely unobserved above: the guard is
    # in the constructor, so no code path anywhere - including one this replay does not
    # exercise - can build the state Requirement 18.5 forbids.
    try:
        Position(
            symbol=case["order"]["symbol"],
            side=LONG,
            size=Decimal("-1").scaleb(-config.quantity_precision),
            entry_price=case["order"]["reference_price"],
        )
    except InvariantViolation as exc:
        assert exc.invariant == "position_size", (
            f"a negative position size was rejected as {exc.invariant!r} rather than as "
            "'position_size' - Requirement 18.14 has the error name the violated invariant"
        )
    else:
        raise AssertionError(
            "a Position of negative size was accepted; Requirement 18.5 represents "
            "direction as an explicit side value and keeps size at or above zero"
        )

    # The same bounds on a persisted equity snapshot: Requirement 18.4 holds at every point
    # either balance is readable through the Persistence_Layer, not only in process.
    for snapshot in snapshots:
        account, positions, prices = account_of_snapshot(snapshot, case["currency"])
        assert account.available_balance >= _ZERO, (
            f"persisted snapshot {snapshot['series_index']} ({snapshot['cause']}) stores "
            f"available_balance {account.available_balance}, below zero (Requirement 18.4)"
        )
        assert account.locked_balance >= _ZERO, (
            f"persisted snapshot {snapshot['series_index']} ({snapshot['cause']}) stores "
            f"locked_balance {account.locked_balance}, below zero (Requirement 18.4)"
        )
        breach = violated_invariant(account, positions, prices, config)
        assert breach not in NON_NEGATIVITY_INVARIANTS, (
            f"persisted snapshot {snapshot['series_index']} ({snapshot['cause']}) was "
            f"rejected as {breach!r} although its balances are at or above zero"
        )

# ══════════════════════════════════════════════════════════════════════════
# P-27
# ══════════════════════════════════════════════════════════════════════════


def equity_at_fill_price(
    account: Account,
    positions: Mapping[str, Position],
    symbol: str,
    fill_price: Decimal,
    config: AccountingConfig,
) -> Decimal:
    """Equity with ``symbol`` valued at ``fill_price`` and every other position at its last.

    Requirement 18.6 does not say "equity is unchanged"; it says equity is unchanged
    *"evaluated with each affected position valued at that fill's price at the moment the
    fill is applied"*. That qualifier is the whole content of the property. A fill at a price
    away from the position's last recorded price moves the mark and the cash at once, so
    comparing a before-state valued at the **old** mark against an after-state valued at the
    **new** one would measure the revaluation, not the conservation, and would fail on a
    perfectly correct engine.

    So the same price map is used on both sides of the fill: the filled symbol at this fill's
    price, every other open position at the price it was last valued at
    (:func:`prices_of`). Requirement 18.6's residual is then the cash-versus-position-value
    residual and nothing else.
    """
    prices = prices_of(positions)
    prices[symbol] = fill_price
    return oracle_equity(account, positions, prices, config)


def replay_zero_cost_fills(
    case: Mapping[str, Any],
    config: AccountingConfig,
    *,
    force_zero_fee: bool,
) -> Iterator[Tuple[str, Decimal, Decimal, Decimal, Any]]:
    """Apply each fill with zero cost, yielding the equity measured either side of it.

    Yields ``(label, fill_price, equity_before, equity_after, result)``, where both equity
    figures come from :func:`equity_at_fill_price` at *that fill's* price and ``result`` is
    the :class:`FillResult`, or ``None`` for a fill the engine refused.

    Two ways of reaching zero cost are driven, because Requirement 18.6's precondition is
    "no fees and no slippage" and the two halves are independent:

    * ``force_zero_fee=False`` with a ``zero_cost=True`` case - the generator itself yields
      ``fee_rate = slippage_rate = 0`` and every fill exactly at the reference price, which
      is the requirement's precondition as written.
    * ``force_zero_fee=True`` on an ordinary case - fills land at slipped prices drawn from a
      non-zero ``slippage_rate``, and the fee is overridden to ``Decimal(0)``. Conservation
      must still hold: a fill price away from the reference is not a *cost* to the ledger,
      it is a different mark, and Requirement 18.6 evaluates both sides at it. This half is
      what would catch an engine that conserved equity only when the fill price happened to
      equal the price the position was already carried at.

    A re-delivered ``fill_event_id`` is skipped rather than applied (``uq_paper_fill_event``,
    Requirement 16.9); a refused fill is yielded with ``result=None`` so the caller can
    assert Requirement 18.14's "the state is unchanged" as a conservation statement too.
    """
    account = Account(
        available_balance=case["initial_balance"],
        locked_balance=_ZERO,
        realized_pnl=_ZERO,
        total_equity=case["initial_balance"],
        currency=case["currency"],
    )
    positions: Dict[str, Position] = {}
    order = case["order"]
    symbol = order["symbol"]

    applied: Set[str] = set()
    for fill in case["fills"]:
        event_id = fill["fill_event_id"]
        if event_id in applied:
            continue
        fee = _ZERO if force_zero_fee else fee_of(fill, config)
        fill_price = config.price(fill["price"])

        before = equity_at_fill_price(account, positions, symbol, fill_price, config)
        try:
            result = apply_fill(
                account,
                positions,
                order,
                fill["quantity"],
                fill["price"],
                fee,
                config,
                filled_at=fill["filled_at"],
            )
        except InvariantViolation:
            after = equity_at_fill_price(account, positions, symbol, fill_price, config)
            yield f"FILL_REFUSED {event_id}", fill_price, before, after, None
            continue

        applied.add(event_id)
        account = result.account
        positions = dict(result.positions)
        after = equity_at_fill_price(account, positions, symbol, fill_price, config)
        yield f"FILL {event_id}", fill_price, before, after, result


# Feature: marketplace-subscriptions-paper-trading, Property 27 (invariant, cash
# conservation): For all fills with zero fee and zero slippage: total_equity is unchanged
# across the fill, evaluated with each affected position valued at that fill's price.
@PROPERTY_SETTINGS
@given(
    zero_cost_case=fill_sequences(max_fills=3, zero_cost=True),
    forced_case=fill_sequences(max_fills=3),
)
def test_p27_zero_cost_fill_conserves_equity(
    zero_cost_case: Mapping[str, Any],
    forced_case: Mapping[str, Any],
) -> None:
    """A fill that costs nothing moves cash and position value by equal and opposite amounts.

    Requirement 18.6: with no fees and no slippage, ``total_equity`` changes by **exactly**
    zero across the fill, evaluated with each affected position valued at that fill's price
    at the moment the fill is applied. The measurement is taken independently on both sides
    of ``apply_fill`` and compared as exact ``Decimal`` equality - no tolerance, because a
    tolerance here is precisely what would let a short close that credits no cash pass.

    ``FillResult.total_equity_delta`` is asserted as well, but *after* the measured
    difference and never instead of it. That field is a literal ``-fill_fee`` in
    ``apply_fill``, so on a zero fee it reports zero whatever the cash arithmetic actually
    did; an engine that credited the wrong cash would still report ``total_equity_delta ==
    0`` and satisfy a test that only read the field. The before/after measurement is what
    makes the assertion about the ledger rather than about a constant.

    Refused fills are asserted too: Requirement 18.4's refusal must leave the state as it
    was (Requirement 18.14), which is conservation in the strictest form - a rejected fill
    that had already moved cash before discovering the balance would go negative would show
    up as a non-zero difference here.

    **Validates: Requirements 18.6**
    """
    for case, force_zero_fee in ((zero_cost_case, False), (forced_case, True)):
        config = AccountingConfig.from_session_config(case["config"])
        mode = "generated zero-cost" if not force_zero_fee else "fee forced to zero"

        for label, fill_price, before, after, result in replay_zero_cost_fills(
            case, config, force_zero_fee=force_zero_fee
        ):
            # The property, as exact Decimal equality on figures measured either side of
            # the fill with both sides valued at that fill's price.
            assert after == before, (
                f"[{mode}] after {label} at price {fill_price}: total_equity moved from "
                f"{before} to {after}, a change of {after - before}, on a fill that costs "
                "nothing - cash and position value are not conserved across it "
                "(Requirement 18.6)"
            )

            if result is None:
                # A refusal is not a ledger movement (Requirement 18.14); the equality above
                # already carries that, and there is no FillResult to cross-check.
                continue

            # The fee really was zero, so the precondition held rather than the assertion
            # above passing on a fill that quietly cost something.
            assert result.fee == _ZERO, (
                f"[{mode}] {label}: the fill was applied with fee {result.fee}, not zero - "
                "Requirement 18.6's precondition did not hold for this fill"
            )

            # The engine's own reported delta must agree with the measurement. Checked
            # second: it is a constant -fee in apply_fill, so it can only confirm, never
            # establish, that equity was conserved.
            assert result.total_equity_delta == _ZERO, (
                f"[{mode}] {label}: FillResult.total_equity_delta is "
                f"{result.total_equity_delta} on a zero-fee fill whose measured equity "
                f"change is {after - before}"
            )

            # And the equity the engine reports is the equity that was measured, so the
            # conservation is a property of the figure the session publishes rather than of
            # the oracle alone.
            assert result.account.total_equity == after, (
                f"[{mode}] {label}: the engine reports total_equity "
                f"{result.account.total_equity} where the same valuation at fill price "
                f"{fill_price} gives {after}, a discrepancy of "
                f"{result.account.total_equity - after}"
            )


# ══════════════════════════════════════════════════════════════════════════
# P-28
# ══════════════════════════════════════════════════════════════════════════


def charged_fee_of(
    fill: Mapping[str, Any], config: AccountingConfig, *, force_nonzero: bool
) -> Decimal:
    """The fee this fill is charged, raised to one Minor_Unit when ``force_nonzero``.

    ``fill_sequences()`` computes ``fee_minor`` as ``to_minor(quantity * price * fee_rate)``
    from a ``fee_rate`` drawn from ``{0, 0.0005, 0.0010, 0.0025}``, so a large share of drawn
    fills carry a fee of exactly zero — either because the rate is zero or because the
    notional is small enough that the product rounds down to no Minor_Units at all. Those
    fills are P-27's subject, not this one: a property about "the decrease attributable to
    costs" that only ever saw zero costs would pass on an engine that never charged anything.

    So the forced half of the property lifts a zero fee to ``config.money_quantum`` — one
    Minor_Unit, the smallest fee that is representable in ``paper_fills.fee_minor`` and
    therefore the smallest one that can be *recorded*. The lifted value is the recorded fee
    for the purposes of the assertion: it is what is handed to :func:`apply_fill` and what
    ``FillResult.fee`` must report back, so the sum being compared against is still the sum
    of the fees the ledger actually charged.
    """
    fee = fee_of(fill, config)
    if force_nonzero and fee <= _ZERO:
        return config.money_quantum
    return fee


def replay_charged_fills(
    case: Mapping[str, Any],
    config: AccountingConfig,
    *,
    force_nonzero: bool,
) -> Iterator[Tuple[str, Decimal, Decimal, Decimal, Decimal, Any]]:
    """Apply each fill with a charged fee, yielding the equity measured either side of it.

    Yields ``(label, fill_price, fee, equity_before, equity_after, result)``, where both
    equity figures come from :func:`equity_at_fill_price` at *that fill's* price — the
    valuation Requirement 18.7 inherits from Requirement 18.6, and the reason the difference
    isolates the cost term instead of also picking up the mark moving from the price the
    position was last carried at to the price this fill happened at.

    ``result`` is the :class:`FillResult`, or ``None`` for a fill the engine refused. A
    refusal is yielded rather than dropped because Requirement 18.14 makes "the state is
    unchanged" part of what a refusal is: a rejected fill that had already deducted its fee
    before discovering the balance would go negative would decrease equity by a fee that was
    never recorded, which is exactly the residual this property forbids.

    A re-delivered ``fill_event_id`` is skipped, not applied and not charged
    (``uq_paper_fill_event``, Requirement 16.9) — a duplicate that charged its fee twice
    would show up as a total decrease larger than the sum of the recorded fees.
    """
    account = Account(
        available_balance=case["initial_balance"],
        locked_balance=_ZERO,
        realized_pnl=_ZERO,
        total_equity=case["initial_balance"],
        currency=case["currency"],
    )
    positions: Dict[str, Position] = {}
    order = case["order"]
    symbol = order["symbol"]

    applied: Set[str] = set()
    for fill in case["fills"]:
        event_id = fill["fill_event_id"]
        if event_id in applied:
            continue
        fee = charged_fee_of(fill, config, force_nonzero=force_nonzero)
        fill_price = config.price(fill["price"])

        before = equity_at_fill_price(account, positions, symbol, fill_price, config)
        try:
            result = apply_fill(
                account,
                positions,
                order,
                fill["quantity"],
                fill["price"],
                fee,
                config,
                filled_at=fill["filled_at"],
            )
        except InvariantViolation:
            after = equity_at_fill_price(account, positions, symbol, fill_price, config)
            yield f"FILL_REFUSED {event_id}", fill_price, fee, before, after, None
            continue

        applied.add(event_id)
        account = result.account
        positions = dict(result.positions)
        after = equity_at_fill_price(account, positions, symbol, fill_price, config)
        yield f"FILL {event_id}", fill_price, fee, before, after, result


# Feature: marketplace-subscriptions-paper-trading, Property 28 (invariant, fee
# accounting): For all generated fill sequences: the decrease in total_equity attributable
# to costs equals the exact sum of the recorded fees, with no residual.
@PROPERTY_SETTINGS
@given(
    charged_case=fill_sequences(max_fills=3),
    forced_case=fill_sequences(max_fills=4),
)
def test_p28_equity_decrease_equals_recorded_fees(
    charged_case: Mapping[str, Any],
    forced_case: Mapping[str, Any],
) -> None:
    """Fees are the only cost: equity falls by their exact sum and by nothing else.

    Requirement 18.7: ``total_equity`` decreases by exactly the sum of the recorded fees.
    Where P-27 pins the fee at zero and asks whether the ledger conserves, this drives
    **non-zero** fees and asks whether the ledger charges precisely what it recorded. Both
    halves of the same identity, and the interesting half is this one, because it is the one
    a hidden cost term can fail: a fee applied twice, a spread quietly deducted alongside it,
    a rounding crumb left over on a partial close.

    The measurement is taken per fill and then in aggregate:

    * **Per fill** — ``equity_after - equity_before == -fee`` exactly, with both sides valued
      at that fill's price (:func:`equity_at_fill_price`, Requirement 18.6's qualifier which
      Requirement 18.7 inherits). Anything other than the fee moving equity fails here, on
      the individual fill, which is where a failure is diagnosable.
    * **In aggregate** — the total decrease across the sequence equals the exact sum of the
      recorded fees, and the residual is asserted to be ``Decimal('0')`` in its own right.
      A per-fill discrepancy of ``+q`` on one fill and ``-q`` on the next would satisfy a
      cumulative check alone; a sequence whose fills each look right but which loses a
      Minor_Unit somewhere in between would satisfy the per-fill check alone. Both are
      asserted because neither implies the other.
    * **End to end**, where every applied fill landed at the same price — the equity of the
      final state minus the equity of the opening state, both at that one price, equals minus
      the total fees. ``fill_sequences()`` derives one slipped price per case, so this
      normally holds; it is guarded rather than assumed so a generator that later varies the
      price within a case makes this clause skip rather than fail spuriously.

    ``FillResult.total_equity_delta`` and ``FillResult.fee`` are cross-checked *after* the
    measured difference, never in place of it. ``total_equity_delta`` is a literal
    ``-fill_fee`` in ``apply_fill``, so it reports the right answer no matter what the cash
    arithmetic did; only the before/after measurement can tell whether the ledger moved.

    Refused fills are asserted at zero movement, and duplicate deliveries are never charged:
    both are ways for a cost to appear in the equity without appearing in the recorded fees.

    **Validates: Requirements 18.7**
    """
    for case, force_nonzero in ((charged_case, False), (forced_case, True)):
        config = AccountingConfig.from_session_config(case["config"])
        mode = "recorded fees" if not force_nonzero else "fee forced non-zero"

        total_fees = _ZERO
        total_decrease = _ZERO
        # (label, fill_price, before, after) for the fills that were actually applied, so
        # the aggregate clauses below read the same figures the per-fill clause asserted.
        measured: List[Tuple[str, Decimal, Decimal, Decimal]] = []

        for label, fill_price, fee, before, after, result in replay_charged_fills(
            case, config, force_nonzero=force_nonzero
        ):
            if result is None:
                # Requirement 18.14: a refusal charges nothing, so it moves equity by
                # nothing. Asserted here rather than skipped, because a fee deducted on the
                # way to a rejection is a decrease with no recorded fee behind it.
                assert after == before, (
                    f"[{mode}] after {label} at price {fill_price}: the fill was refused "
                    f"but total_equity moved from {before} to {after}, a change of "
                    f"{after - before} - a refused fill charges nothing and must leave the "
                    "ledger exactly as it was (Requirement 18.14)"
                )
                continue

            # The property, per fill: the only movement in equity is the fee, measured on
            # figures computed either side of apply_fill at that fill's price.
            assert after - before == -fee, (
                f"[{mode}] after {label} at price {fill_price}: total_equity moved from "
                f"{before} to {after}, a change of {after - before}, on a fill whose "
                f"recorded fee is {fee} - the decrease attributable to costs is "
                f"{before - after}, leaving a residual of {(before - after) - fee} beyond "
                "the fee (Requirement 18.7)"
            )

            # The fee the engine recorded is the fee the sum is taken over. If apply_fill
            # re-quantized it to something else, the identity above would be comparing
            # against a figure that never reaches paper_fills.fee_minor.
            assert result.fee == fee, (
                f"[{mode}] {label}: the fill was applied with fee {fee} but FillResult "
                f"records {result.fee}; the recorded fee and the charged fee must be the "
                "same number (Requirement 18.7)"
            )

            if force_nonzero:
                # The forced half really does exercise a charged fill, so the equalities
                # above are not all reading 0 == -0.
                assert result.fee > _ZERO, (
                    f"[{mode}] {label}: fee is {result.fee}; this half of the property "
                    "exists to drive a non-zero cost and has not done so"
                )

            # Secondary: the engine's own reported delta, and the equity it publishes.
            assert result.total_equity_delta == -fee, (
                f"[{mode}] {label}: FillResult.total_equity_delta is "
                f"{result.total_equity_delta} on a fill with fee {fee} whose measured "
                f"equity change is {after - before}"
            )
            assert result.account.total_equity == after, (
                f"[{mode}] {label}: the engine reports total_equity "
                f"{result.account.total_equity} where the same valuation at fill price "
                f"{fill_price} gives {after}, a discrepancy of "
                f"{result.account.total_equity - after}"
            )

            total_fees += fee
            total_decrease += before - after
            measured.append((label, fill_price, before, after))

        if not measured:
            # Every drawn fill was refused; there is no charged sequence to aggregate over
            # and the refusal clause above has already carried the requirement.
            continue

        # The aggregate: the whole sequence's decrease is the whole sequence's fees, and the
        # residual is exactly zero rather than merely small.
        residual = total_decrease - total_fees
        assert residual == _ZERO, (
            f"[{mode}] across {len(measured)} applied fill(s) "
            f"({[label for label, _, _, _ in measured]}): total_equity decreased by "
            f"{total_decrease} against recorded fees summing to {total_fees}, a residual of "
            f"{residual} - Requirement 18.7 admits no cost term other than the fees"
        )
        assert total_decrease == total_fees, (
            f"[{mode}]: total decrease {total_decrease} != recorded fee sum {total_fees}"
        )

        if force_nonzero:
            assert total_fees > _ZERO, (
                f"[{mode}]: the applied fills carried no fee at all, so the aggregate "
                "equality above compared zero against zero"
            )

        # End to end, when every applied fill was priced identically: the opening equity
        # minus the closing equity, both at that one price, is the total of the fees. This is
        # Requirement 18.7 read over the session rather than over each fill, and it holds
        # only at a common valuation price - hence the guard.
        prices_used = {fill_price for _, fill_price, _, _ in measured}
        if len(prices_used) == 1:
            opening = measured[0][2]
            closing = measured[-1][3]
            assert opening - closing == total_fees, (
                f"[{mode}] valued throughout at {prices_used.pop()}: total_equity went "
                f"from {opening} to {closing}, a decrease of {opening - closing}, against "
                f"recorded fees of {total_fees} - a residual of "
                f"{(opening - closing) - total_fees} accumulated across the sequence"
            )


# ══════════════════════════════════════════════════════════════════════════
# P-29
# ══════════════════════════════════════════════════════════════════════════


#: The instant ``equity_series()`` starts its ``taken_at`` run from. Needed only when the
#: drawn series is empty and an appended snapshot still has to carry a timestamp: appending
#: to an empty series is the sharpest edge of this property (the result must stay zero), so
#: it is exercised rather than skipped.
SERIES_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)

#: The one snapshot cause an appended revaluation carries. ``equity_series()`` draws
#: ``SESSION_START`` first and one of the four movement causes afterwards; an appended peak
#: is a revaluation, which is one of the five values ``paper_equity_snapshots.cause``
#: admits (``design.md § paper_equity_snapshots``).
APPENDED_CAUSE = "REVALUATION"


def equities_of(snapshots: Sequence[Mapping[str, Any]]) -> List[Decimal]:
    """The ``total_equity`` column of a drawn series, in the order it was handed over.

    Requirement 18.9 computes drawdown from the snapshots "taken in non-decreasing timestamp
    order", and ``equity_series()`` already yields them in that order, so the sequence is
    read as it stands. It is deliberately **not** sorted here: a drawdown computed from a
    resorted series is the drawdown of a different series.
    """
    return [snapshot["total_equity"] for snapshot in snapshots]


def oracle_drawdown(equities: Sequence[Decimal]) -> Tuple[Decimal, Decimal]:
    """``(amount, peak_at_trough)`` for an equity sequence, by prefix maxima over all pairs.

    The independent oracle ``design.md § Property-to-test mapping`` names for P-29 ("an
    independent running-peak scan"), written as the definition rather than as the module's
    algorithm: maximum drawdown is ``max(E_i - E_j)`` over every ``i < j``, which for a
    given trough ``j`` is ``max(E_0 … E_{j-1}) - E_j``. Computing the prefix peak with
    ``max()`` on each step rather than carrying it incrementally makes this a different
    computation from the one in ``paper_accounting.max_drawdown``, so the two agreeing is
    evidence rather than a tautology. The series are at most a few dozen points, so the
    quadratic shape costs nothing.

    Returns ``(Decimal('0'), Decimal('0'))`` for a sequence shorter than two points:
    Requirement 18.9 reports zero while the session holds fewer than two snapshots, because
    a single point describes no decline. Ties go to the **earliest** trough (the comparison
    is strictly greater), which is what fixes ``peak_at_trough`` — and therefore the reported
    fraction — when two troughs decline by the same amount from different peaks.
    """
    if len(equities) < 2:
        return _ZERO, _ZERO

    with localcontext() as ctx:
        ctx.prec = ORACLE_PRECISION
        amount = _ZERO
        peak_at_trough = _ZERO
        for index in range(1, len(equities)):
            prefix_peak = max(equities[:index])
            decline = prefix_peak - equities[index]
            if decline > amount:
                amount = decline
                peak_at_trough = prefix_peak
        return amount, peak_at_trough


def snapshot_of_equity(
    equity: Decimal, *, series_index: int, taken_at: datetime
) -> Dict[str, Any]:
    """A ``paper_equity_snapshots`` row carrying ``equity``, shaped as the generator does.

    ``total_equity == available_balance + locked_balance + position_market_value`` holds
    exactly (Requirement 18.3), with the whole equity in cash, so the appended row is one the
    engine's own invariants accept rather than a bare number the drawdown path happens to
    tolerate.
    """
    return {
        "series_index": series_index,
        "total_equity": equity,
        "available_balance": equity,
        "locked_balance": _ZERO,
        "position_market_value": _ZERO,
        "stale": False,
        "cause": APPENDED_CAUSE,
        "taken_at": taken_at,
    }


def ticks_to_equity(ticks: int) -> Decimal:
    """An integer count of Minor_Units as an exact ``Decimal`` at the currency's scale.

    ``scaleb`` shifts the exponent without touching the coefficient, so no binary ``float``
    exists at any point (Requirement 18.1) and the appended value sits on the same
    Minor_Units grid as every value ``equity_series()`` draws.
    """
    return Decimal(int(ticks)).scaleb(-MINOR_UNIT_EXPONENT)


# Feature: marketplace-subscriptions-paper-trading, Property 29 (metamorphic, drawdown
# bounds): For all generated equity series: reported maximum drawdown is at or above zero,
# is at most the series maximum minus the series minimum, and is unchanged by appending an
# equity value at or above the running peak.
@PROPERTY_SETTINGS
@given(
    snapshots=equity_series(max_size=12),
    appended_ticks=st.lists(
        st.integers(min_value=0, max_value=10**11), min_size=1, max_size=3
    ),
    gap_seconds=st.integers(min_value=0, max_value=3600),
)
def test_p29_drawdown_bounds_and_peak_append_invariance(
    snapshots: Sequence[Mapping[str, Any]],
    appended_ticks: Sequence[int],
    gap_seconds: int,
) -> None:
    """Drawdown is bounded by the series span and blind to equity making a new high.

    Requirement 18.9 asks for three things at once, and this property asserts all three
    against the same drawn series:

    * **The bounds.** ``amount >= 0`` — a drawdown is a decline, and a series that only ever
      rises has none, reported as zero rather than as a negative number. ``amount <=
      max(series) - min(series)`` — the largest decline from a peak to a *subsequent* trough
      cannot exceed the whole span of the series, so an engine that accumulated declines
      across separate troughs, or that measured from a peak that came *after* the trough,
      breaks this bound. ``fraction`` in ``[0, 1]``, which is what
      ``chk_paper_metrics_drawdown_fraction`` on ``paper_metrics`` will refuse to store
      otherwise.
    * **The value**, against :func:`oracle_drawdown` — a pairwise maximum over every
      ``(peak, later trough)`` pair, so the bounds above cannot be satisfied by an engine that
      simply reports zero for everything.
    * **The metamorphic clause.** Appending an equity value at or above the running peak
      changes the reported drawdown by nothing. This is the part a bounds check alone cannot
      reach: it is precisely the behaviour that distinguishes "largest decline from a running
      peak" from the plausible-but-wrong "distance from the final peak", "the last decline
      observed" or "peak minus final equity". A new high creates no new decline, so a session
      that keeps making money must keep reporting the drawdown it already suffered — the
      figure ``Portfolio.jsx`` shows as the session's worst point, not as its most recent
      one. The append is iterated, each new value at or above the peak the previous append
      established, so a monotone run of new highs is covered as well as a single one.

    The zero-below-two-snapshots rule is asserted where the drawn series is short, including
    the empty one: Requirement 18.9 reports zero while fewer than two snapshots exist, and
    the append clause then holds that appending a first or second point which is a new high
    still leaves it at zero.

    A series handed over out of ``taken_at`` order is asserted to be **refused**, not sorted.
    That clause belongs to this property rather than to a separate one: the oracle above
    scans in the order given, and an engine that quietly sorted its input would be computing
    the drawdown of a series nobody read, which is exactly the reading Requirement 18.9's
    "taken in non-decreasing timestamp order" excludes.

    **Validates: Requirements 18.9**
    """
    equities = equities_of(snapshots)
    reported = max_drawdown(snapshots)

    where = (
        f"series of {len(equities)} snapshot(s) "
        f"{[str(equity) for equity in equities]}"
    )

    # -- the bounds ---------------------------------------------------------------
    assert reported.amount >= _ZERO, (
        f"{where}: max drawdown amount is {reported.amount}, below zero - a drawdown is a "
        "decline from a peak and is reported at or above zero (Requirement 18.9)"
    )
    if equities:
        span = max(equities) - min(equities)
        assert reported.amount <= span, (
            f"{where}: max drawdown amount is {reported.amount}, above the series span "
            f"{span} (max {max(equities)} - min {min(equities)}) - the largest decline from "
            "a peak to a later trough cannot exceed the whole range of the series "
            "(Requirement 18.9)"
        )
    assert _ZERO <= reported.fraction <= Decimal("1"), (
        f"{where}: max drawdown fraction is {reported.fraction}, outside [0, 1] - "
        "chk_paper_metrics_drawdown_fraction would refuse to store it (Requirement 18.9)"
    )

    # -- fewer than two snapshots is zero, not a measurement ----------------------
    if len(equities) < 2:
        assert reported.amount == _ZERO and reported.fraction == _ZERO, (
            f"{where}: max drawdown is reported as ({reported.amount}, "
            f"{reported.fraction}) from fewer than two snapshots - Requirement 18.9 reports "
            "zero while the session holds fewer than two, because one point describes no "
            "decline"
        )

    # -- the value, against the independent pairwise scan --------------------------
    oracle_amount, oracle_peak = oracle_drawdown(equities)
    assert reported.amount == oracle_amount, (
        f"{where}: max drawdown amount is {reported.amount} where the largest decline from "
        f"any peak to a later trough is {oracle_amount}, a discrepancy of "
        f"{reported.amount - oracle_amount} (Requirement 18.9)"
    )

    if oracle_amount == _ZERO or oracle_peak <= _ZERO:
        # A fraction of a non-positive peak is not a measurement, and a series with no
        # decline has no fraction to report.
        assert reported.fraction == _ZERO, (
            f"{where}: max drawdown fraction is {reported.fraction} where the decline is "
            f"{oracle_amount} from a peak of {oracle_peak} - neither supports a fraction"
        )
    else:
        with localcontext() as ctx:
            ctx.prec = ORACLE_PRECISION
            exact = oracle_amount / oracle_peak
        # The reported fraction is rounded to the scale paper_metrics stores, so it is
        # compared at that scale: half a quantum of its own exponent is the whole error a
        # correct rounding can introduce, and anything larger is a different number.
        quantum = Decimal(1).scaleb(reported.fraction.as_tuple().exponent)
        assert abs(reported.fraction - exact) <= quantum / _TWO, (
            f"{where}: max drawdown fraction is {reported.fraction} where the decline "
            f"{oracle_amount} over the peak at the trough {oracle_peak} is {exact} - a "
            f"difference of {reported.fraction - exact}, beyond the half quantum "
            f"{quantum / _TWO} rounding to that scale can account for (Requirement 18.9)"
        )

    # -- the metamorphic clause: a new high changes nothing ------------------------
    series: List[Dict[str, Any]] = [dict(snapshot) for snapshot in snapshots]
    running_peak = max(equities) if equities else _ZERO
    taken_at = snapshots[-1]["taken_at"] if snapshots else SERIES_EPOCH

    for offset, ticks in enumerate(appended_ticks):
        appended = running_peak + ticks_to_equity(ticks)
        taken_at = taken_at + timedelta(seconds=gap_seconds + offset)
        series.append(
            snapshot_of_equity(
                appended, series_index=len(series), taken_at=taken_at
            )
        )

        after = max_drawdown(series)
        assert after.amount == reported.amount, (
            f"{where}: appending equity {appended} at or above the running peak "
            f"{running_peak} moved the reported max drawdown amount from "
            f"{reported.amount} to {after.amount}, a change of "
            f"{after.amount - reported.amount} - a new high creates no new decline, so the "
            f"worst decline already suffered is unchanged (Requirement 18.9). Series after "
            f"{offset + 1} append(s): "
            f"{[str(snapshot['total_equity']) for snapshot in series]}"
        )
        assert after.fraction == reported.fraction, (
            f"{where}: appending equity {appended} at or above the running peak "
            f"{running_peak} moved the reported max drawdown fraction from "
            f"{reported.fraction} to {after.fraction} while the amount stayed at "
            f"{after.amount} - the peak the decline is measured against is the peak at the "
            "trough, which an append after that trough cannot change (Requirement 18.9)"
        )

        # The appended value is the running peak for the next append, so the run is a
        # sequence of new highs rather than one high followed by a decline.
        running_peak = appended

    # -- an out-of-order series is refused, not sorted ----------------------------
    if snapshots:
        mis_ordered = [dict(snapshot) for snapshot in snapshots]
        mis_ordered.append(
            snapshot_of_equity(
                running_peak,
                series_index=len(mis_ordered),
                taken_at=snapshots[-1]["taken_at"] - timedelta(seconds=1),
            )
        )
        try:
            sorted_result = max_drawdown(mis_ordered)
        except SnapshotOrderError:
            pass
        else:
            raise AssertionError(
                f"{where}: a snapshot timestamped one second before the previous one was "
                f"accepted and a drawdown of {sorted_result.amount} reported from it - "
                "Requirement 18.9 computes drawdown from snapshots in non-decreasing "
                "timestamp order, and the drawdown of a resorted series is not the "
                "drawdown of the series that was read"
            )
