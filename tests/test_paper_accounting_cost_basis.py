"""Unit tests for the Paper_Accounting_Engine cost-basis and short-position edge cases.

Feature: marketplace-subscriptions-paper-trading (task 9.9).
Module under test: ``backend_app/backend/paper/paper_accounting.py`` (task 9.1).

WHY THESE EXAMPLES EXIST ALONGSIDE THE PROPERTY TESTS
-----------------------------------------------------
P-25 ... P-31 (``tests/property/test_paper_accounting.py``) already drive the whole engine
from Hypothesis across a generated input space and assert the equity identity and the
invariants after every event. What they do *not* do is pin the exact number a specific,
named scenario produces: a property that holds for every input still holds if the
weighted-average formula and the SHORT valuation are both wrong in a way that cancels.

These example tests fix the specific figures ``Portfolio.jsx`` depends on:

* the weighted-average entry price ``((old_size*old_entry) + (qty*fill)) / new_size``
  (Requirement 18.8's one cost-basis convention) survives a *partial* close unchanged - the
  quantity that stays open keeps the average the quantity that left was bought at;
* a *reversal through zero* closes the old side to exactly ``Decimal('0')``, records the
  realized PnL of the closed quantity at recorded fill prices, and re-opens the opposite
  side at the reversing fill's price as its new weighted-average entry;
* a SHORT position revalued *above* its entry shows a loss and *below* its entry shows a
  gain, under the retained ``size * (2*entry - price)`` convention (Requirement 18.8's
  "unrealized ... only from open quantity at the latest validated price").

Every figure below is hand-computed with exact Decimals and asserted with zero tolerance,
which is the whole point: a tolerance is what let the existing service's short-close path
credit no cash and go unnoticed. Fees and slippage are held at zero so the arithmetic under
test is the cost-basis arithmetic and nothing else; the fee path is Requirement 18.7's, and
P-28 covers it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend_app.backend.paper.paper_accounting import (
    LONG,
    SHORT,
    Account,
    AccountingConfig,
    Position,
    apply_fill,
    invariants_hold,
    position_unrealized_pnl,
    position_value,
    recalculate,
    unrealized_pnl,
)

# A spot USD session with zero fees and zero slippage: 2 decimal places for money and
# price, 8 for quantity, weighted-average cost basis, half-even rounding - the frozen
# defaults ``design.md`` records for the session config this module reads.
CONFIG = AccountingConfig(
    fee_rate=Decimal("0"),
    slippage_rate=Decimal("0"),
    rounding_mode="ROUND_HALF_EVEN",
    cost_basis="WEIGHTED_AVERAGE",
    price_precision=2,
    quantity_precision=8,
    minor_unit_exponent=2,
    market_type="spot",
)

# A fixed timestamp; the engine records the timestamps it is handed and never reads a clock,
# so any instant works and the same one is reused for every fill in a scenario.
T0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _order(symbol: str, side: str) -> dict:
    """The minimal order shape ``apply_fill`` reads: a symbol and a ``buy``/``sell`` side."""
    return {"symbol": symbol, "side": side}


def _empty_account(cash: str) -> Account:
    """An account holding ``cash`` available, nothing locked, no realized PnL, no positions."""
    return Account(
        available_balance=Decimal(cash),
        locked_balance=Decimal("0"),
        realized_pnl=Decimal("0"),
        total_equity=Decimal(cash),
        currency="USD",
    )


# ══════════════════════════════════════════════════════════════════════════
# 1. WEIGHTED-AVERAGE BASIS ACROSS A PARTIAL CLOSE (Requirement 18.8)
# ══════════════════════════════════════════════════════════════════════════


def test_weighted_average_entry_survives_a_partial_close():
    """A long built at two prices carries the blended entry, and a partial close leaves it.

    Buy 2 BTC @ 100, then buy 2 BTC @ 200. The weighted-average entry is
    ``((2*100) + (2*200)) / 4 = 600/4 = 150``. Selling 1 BTC @ 300 closes a quarter of the
    book: the entry price of the 3 BTC that remain is still exactly 150 - the quantity that
    stayed open was bought at the same average as the quantity that left - and the realized
    PnL of the closed unit is ``(300 - 150) * 1 = 150``.
    """
    account = _empty_account("100000")
    positions: dict = {}

    # Open 2 @ 100.
    first = apply_fill(
        account, positions, _order("BTC/USDT", "buy"),
        quantity=Decimal("2"), price=Decimal("100"), fee=Decimal("0"),
        config=CONFIG, filled_at=T0,
    )
    assert first.position.side == LONG
    assert first.position.size == Decimal("2")
    assert first.position.entry_price == Decimal("100.00")

    # Add 2 @ 200 -> weighted-average entry 150.
    second = apply_fill(
        first.account, first.positions, _order("BTC/USDT", "buy"),
        quantity=Decimal("2"), price=Decimal("200"), fee=Decimal("0"),
        config=CONFIG, filled_at=T0,
    )
    assert second.position.size == Decimal("4")
    assert second.position.entry_price == Decimal("150.00")

    # Partially close 1 @ 300. Realized PnL = (300 - 150) * 1 = 150; entry price unchanged.
    third = apply_fill(
        second.account, second.positions, _order("BTC/USDT", "sell"),
        quantity=Decimal("1"), price=Decimal("300"), fee=Decimal("0"),
        config=CONFIG, filled_at=T0,
    )
    assert third.position.side == LONG
    assert third.position.size == Decimal("3")
    assert third.position.entry_price == Decimal("150.00")
    assert third.realized_pnl == Decimal("150.00")
    assert third.account.realized_pnl == Decimal("150.00")

    # A partial close reaches no zero, so it records no closed trade (Requirement 18.10).
    assert third.closed_trade is None

    # The position remains open, and the equity identity still holds on the returned state.
    assert third.position.is_open
    assert invariants_hold(
        third.account, third.positions, {"BTC/USDT": Decimal("300")}, CONFIG
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. REVERSAL THROUGH ZERO (Requirement 18.8, Requirement 18.5)
# ══════════════════════════════════════════════════════════════════════════


def test_reversal_through_zero_closes_the_long_and_opens_a_short():
    """Selling more than the open long closes it to exactly zero and opens a short surplus.

    Open long 3 BTC @ 100. Then sell 5 BTC @ 120. The 3 open units close first: realized
    PnL ``(120 - 100) * 3 = 60``, and the closing quantity reaching zero writes a closed
    trade. The surplus 2 BTC open as a SHORT at the reversing fill's price, 120, which is its
    new weighted-average entry (a fresh position has its fill price as its entry).
    """
    account = _empty_account("100000")

    opened = apply_fill(
        account, {}, _order("BTC/USDT", "buy"),
        quantity=Decimal("3"), price=Decimal("100"), fee=Decimal("0"),
        config=CONFIG, filled_at=T0,
    )
    assert opened.position.side == LONG
    assert opened.position.size == Decimal("3")
    assert opened.position.entry_price == Decimal("100.00")

    reversed_fill = apply_fill(
        opened.account, opened.positions, _order("BTC/USDT", "sell"),
        quantity=Decimal("5"), price=Decimal("120"), fee=Decimal("0"),
        config=CONFIG, filled_at=T0,
    )

    # The resulting position is a SHORT of the 2-unit surplus, entered at the reversal price.
    assert reversed_fill.position.side == SHORT
    assert reversed_fill.position.size == Decimal("2")
    assert reversed_fill.position.entry_price == Decimal("120.00")

    # The long's 3 units realized (120 - 100) * 3 = 60, at recorded fill prices, fee-free.
    assert reversed_fill.realized_pnl == Decimal("60.00")
    assert reversed_fill.account.realized_pnl == Decimal("60.00")

    # The position reached zero on the way through, so a closed trade is recorded, for the
    # closed LONG quantity and its realized PnL (Requirement 18.10).
    trade = reversed_fill.closed_trade
    assert trade is not None
    assert trade.side == LONG
    assert trade.quantity == Decimal("3")
    assert trade.entry_price == Decimal("100.00")
    assert trade.exit_price == Decimal("120.00")
    assert trade.realized_pnl == Decimal("60.00")

    # The equity identity holds on the reversed state, valued at the reversal price.
    assert invariants_hold(
        reversed_fill.account, reversed_fill.positions, {"BTC/USDT": Decimal("120")}, CONFIG
    )


def test_exact_full_close_leaves_a_zero_size_position_not_a_deleted_row():
    """Closing the whole long drives size to exactly zero and records the closed trade.

    A full close is the boundary of the reversal case: sell exactly the open quantity. The
    position must be returned with ``size == Decimal('0')`` and ``closed_at`` set, never
    removed from the map and never compared against a ``0.00000001`` tolerance
    (Requirement 18.5).
    """
    account = _empty_account("100000")

    opened = apply_fill(
        account, {}, _order("ETH/USDT", "buy"),
        quantity=Decimal("4"), price=Decimal("50"), fee=Decimal("0"),
        config=CONFIG, filled_at=T0,
    )

    closed = apply_fill(
        opened.account, opened.positions, _order("ETH/USDT", "sell"),
        quantity=Decimal("4"), price=Decimal("75"), fee=Decimal("0"),
        config=CONFIG, filled_at=T0,
    )

    assert closed.position.size == Decimal("0")
    assert closed.position.is_closed
    assert closed.position.closed_at == T0
    assert "ETH/USDT" in closed.positions  # never deleted
    assert closed.realized_pnl == Decimal("100.00")  # (75 - 50) * 4
    assert closed.closed_trade is not None
    assert closed.closed_trade.realized_pnl == Decimal("100.00")


# ══════════════════════════════════════════════════════════════════════════
# 3. A SHORT REVALUED ABOVE AND BELOW ENTRY (Requirement 18.8)
# ══════════════════════════════════════════════════════════════════════════


def _short(symbol: str, size: str, entry: str) -> Position:
    """A SHORT position of ``size`` opened at ``entry`` - direction in the side, never a sign."""
    return Position(
        symbol=symbol,
        side=SHORT,
        size=Decimal(size),
        entry_price=Decimal(entry),
        opened_at=T0,
    )


def test_short_valued_below_entry_shows_a_gain():
    """A short is worth more than the cash it consumed when the price drops below entry.

    Short 2 BTC @ 100. At the moment it opens, its value is ``2 * 100 = 200`` - the same as
    a long, which is why one cash rule covers both sides. Revalued at 80 (below entry) the
    value is ``2 * (2*100 - 80) = 2 * 120 = 240`` and the unrealized PnL is
    ``(100 - 80) * 2 = 40``: the short has made money.
    """
    position = _short("BTC/USDT", "2", "100")

    # At entry, value equals size * entry and unrealized PnL is zero.
    assert position_value(position, Decimal("100")) == Decimal("200")
    assert position_unrealized_pnl(position, Decimal("100")) == Decimal("0")

    # Below entry: value rises above the consumed cash, PnL positive.
    assert position_value(position, Decimal("80")) == Decimal("240")
    assert position_unrealized_pnl(position, Decimal("80")) == Decimal("40")

    # The aggregate helpers agree, quantized to the money scale.
    assert unrealized_pnl([position], {"BTC/USDT": Decimal("80")}, CONFIG) == Decimal("40.00")


def test_short_valued_above_entry_shows_a_loss():
    """A short is worth less than the cash it consumed when the price rises above entry.

    Short 2 BTC @ 100, revalued at 130 (above entry). Value is
    ``2 * (2*100 - 130) = 2 * 70 = 140`` and unrealized PnL is ``(100 - 130) * 2 = -60``:
    the short has lost money, and the value stays non-negative rather than going negative,
    which is what keeps the equity identity holding for a short.
    """
    position = _short("BTC/USDT", "2", "100")

    assert position_value(position, Decimal("130")) == Decimal("140")
    assert position_unrealized_pnl(position, Decimal("130")) == Decimal("-60")
    assert unrealized_pnl([position], {"BTC/USDT": Decimal("130")}, CONFIG) == Decimal("-60.00")


def test_short_opened_through_apply_fill_revalues_both_ways():
    """A short opened by a sell fill revalues to a gain below entry and a loss above it.

    This exercises the whole engine path rather than the valuation helpers alone: a sell
    against no position opens a SHORT at the fill price, and ``recalculate`` restates its
    value and unrealized PnL at a later validated price. A revaluation moves no cash, so it
    is ``recalculate`` - not ``apply_fill`` - that recomputes ``total_equity`` at the new
    price, and the equity identity must hold on that revalued state with zero tolerance.
    """
    account = _empty_account("100000")

    opened = apply_fill(
        account, {}, _order("BTC/USDT", "sell"),
        quantity=Decimal("2"), price=Decimal("100"), fee=Decimal("0"),
        config=CONFIG, filled_at=T0,
    )
    assert opened.position.side == SHORT
    assert opened.position.entry_price == Decimal("100.00")

    # At the fill price the just-applied state already satisfies the identity.
    assert invariants_hold(
        opened.account, opened.positions, {"BTC/USDT": Decimal("100")}, CONFIG
    )

    # Revalue below entry -> gain of (100 - 80) * 2 = 40.
    below = recalculate(
        opened.account, opened.positions, {"BTC/USDT": Decimal("80")},
        CONFIG, price_at=T0,
    )
    assert below.unrealized_pnl == Decimal("40.00")
    assert below.position_market_value == Decimal("240.00")
    assert invariants_hold(
        below.account, below.positions, {"BTC/USDT": Decimal("80")}, CONFIG
    )

    # Revalue above entry -> loss of (100 - 130) * 2 = -60.
    above = recalculate(
        opened.account, opened.positions, {"BTC/USDT": Decimal("130")},
        CONFIG, price_at=T0,
    )
    assert above.unrealized_pnl == Decimal("-60.00")
    assert above.position_market_value == Decimal("140.00")
    assert invariants_hold(
        above.account, above.positions, {"BTC/USDT": Decimal("130")}, CONFIG
    )


if __name__ == "__main__":  # pragma: no cover - convenience for a direct run
    raise SystemExit(pytest.main([__file__, "-q"]))
