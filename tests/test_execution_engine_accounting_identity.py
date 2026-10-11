"""The accounting identity of ``ExecutionEngine``, and the risk controls that depend on it.

WHAT THIS GUARDS  (BUG-016)
---------------------------
``ExecutionEngine.open_position`` checked whether a position was affordable against
``current_equity`` and then debited only the **fee**:

    open_position(BTCUSDT, price=50000, size=0.1) -> ok=True
    notional committed    -> 5000.0
    equity before         -> 100000.0
    equity after          ->  99995.0      (the fee, and nothing else)

Three consequences, each independently serious:

1. ``cash + position value != equity``. The identity did not hold at all, so every
   figure derived from equity was wrong while a position was open.
2. ``get_drawdown()`` read a number that never moved while a position was open, so a
   position 40% underwater reported **zero drawdown** and the max-drawdown kill switch
   could not fire on open risk — the only risk that matters while a trade is live.
3. The affordability check passed again for the next symbol, because equity had barely
   fallen. N positions could each be worth up to the whole account; the only brake was
   ``max_open_trades = 5`` capping exposure at ~500% of equity by coincidence.

THE DEFINED IDENTITY, stated precisely because the brief asks for it:

    equity = cash + position_market_value

    position_market_value = Σ over open positions of:
        LONG  -> size * mark
        SHORT -> size * (2 * entry_price - mark)

    cash moves only as:  cash_delta = -(new_position_value - old_position_value) - fee

``current_equity`` is never incremented or decremented; it is recomputed from cash and
position value on every fill. That is why the identity cannot drift. The SHORT valuation
convention and the cash-as-residual rule are both taken from
``backend/paper/paper_accounting``, which is the already-correct reference implementation
in this repository, rather than invented here.

A corollary worth stating: **the change in equity across any single fill is exactly
``-fee``.** Opening a position does not make the account richer or poorer beyond the
commission, which is the sanity check that catches an accounting error immediately.
"""

from decimal import Decimal

import pytest

from backend_app.core.execution_engine import ExecutionEngine

CAPITAL = Decimal("100000")


def _engine(**kw):
    """An engine whose ACCOUNTING is what is under test.

    Two deliberate choices, both of which would otherwise make these tests measure
    something other than the accounting:

    ``slippage=0.0``
        Slippage is ``random.uniform``-based (recorded separately as a reproducibility
        defect), so every expected value below would become a range instead of a number.

    ``risk_manager=_PermissiveRisk()``
        The default ``RiskManager`` caps a SINGLE position at 10% of equity
        (``max_position_size_pct``) and open trades at 5. Those are real controls and
        they work — ``test_the_real_risk_manager_still_caps_a_single_position`` below
        proves it. But with them in force, a 50,000 position against 100,000 of capital
        is refused by the position-size cap before any cash arithmetic happens, so a
        test of aggregate exposure would pass while proving nothing about cash. This was
        not hypothetical: the first run of this file failed six tests that way, because
        the positions never opened at all.
    """
    params = dict(
        fee_rate=0.001,
        slippage=0.0,
        risk_manager=_PermissiveRisk(),
        portfolio_state={"total_equity": float(CAPITAL)},
    )
    params.update(kw)
    return ExecutionEngine(**params)


def _engine_with_real_risk(**kw):
    """An engine with the production ``RiskManager``, for the guardrail tests."""
    params = dict(
        fee_rate=0.001, slippage=0.0, portfolio_state={"total_equity": float(CAPITAL)}
    )
    params.update(kw)
    return ExecutionEngine(**params)


def assert_identity(engine, where=""):
    """equity == cash + position_market_value, exactly. Zero tolerance."""
    expected = engine.cash + engine.position_market_value()
    assert engine.current_equity == expected, (
        f"accounting identity broken {where}: equity {engine.current_equity} != "
        f"cash {engine.cash} + position_market_value {engine.position_market_value()} "
        f"(= {expected})"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE DEFECT ITSELF
# ═══════════════════════════════════════════════════════════════════════════

def test_opening_a_position_debits_the_notional_from_cash():
    """THE DEFECT. Cash must fall by notional + fee, not by the fee alone."""
    eng = _engine()
    ok, msg = eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")

    assert ok, msg
    notional = Decimal("50000") * Decimal("0.1")   # 5000
    fee = notional * Decimal("0.001")              # 5

    assert eng.cash == CAPITAL - notional - fee, (
        f"cash is {eng.cash}; expected {CAPITAL - notional - fee} "
        f"(capital {CAPITAL} - notional {notional} - fee {fee})"
    )


def test_opening_a_position_changes_equity_by_exactly_the_fee():
    """The corollary: a fill is not a profit or a loss beyond its commission."""
    eng = _engine()
    before = eng.current_equity

    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")

    fee = Decimal("50000") * Decimal("0.1") * Decimal("0.001")
    assert eng.current_equity == before - fee
    assert_identity(eng, "after open")


def test_the_identity_holds_after_an_open():
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    assert_identity(eng, "after open")


# ═══════════════════════════════════════════════════════════════════════════
# 2. LONG LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════

def test_long_open_then_full_close_at_a_profit():
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    pnl, _ = eng.close_position("BTCUSDT", Decimal("55000"))

    gross = (Decimal("55000") - Decimal("50000")) * Decimal("0.1")       # 500
    fees = (Decimal("50000") + Decimal("55000")) * Decimal("0.1") * Decimal("0.001")  # 10.5
    assert pnl == (gross - fees)
    assert eng.positions == {}
    assert eng.current_equity == CAPITAL + gross - fees
    assert eng.cash == eng.current_equity, "no positions left, so cash must equal equity"
    assert_identity(eng, "after full close")


def test_long_open_then_full_close_at_a_loss():
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    pnl, _ = eng.close_position("BTCUSDT", Decimal("45000"))

    gross = (Decimal("45000") - Decimal("50000")) * Decimal("0.1")       # -500
    fees = (Decimal("50000") + Decimal("45000")) * Decimal("0.1") * Decimal("0.001")
    assert pnl == (gross - fees)
    assert eng.current_equity == CAPITAL + gross - fees
    assert_identity(eng, "after losing close")


def test_a_zero_pnl_long_loses_exactly_the_fees():
    """Closing where you opened is not break-even; it costs two commissions."""
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    pnl, _ = eng.close_position("BTCUSDT", Decimal("50000"))

    fees = Decimal("50000") * Decimal("0.1") * Decimal("0.001") * 2
    assert pnl == -fees
    assert eng.current_equity == CAPITAL - fees
    assert_identity(eng, "after flat close")


def test_long_partial_close_keeps_the_identity_and_the_remaining_position():
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "long")
    eng.close_position("BTCUSDT", Decimal("55000"), Decimal("0.4"))

    assert eng.positions["BTCUSDT"].size == Decimal("0.6")
    assert eng.positions["BTCUSDT"].entry_price == Decimal("50000"), (
        "a partial close must not disturb the average entry price"
    )
    assert_identity(eng, "after partial close")


def test_a_partial_close_then_a_full_close_equals_one_full_close():
    """Path independence: the account cannot care how a position was unwound."""
    one = _engine()
    one.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "long")
    one.close_position("BTCUSDT", Decimal("55000"))

    two = _engine()
    two.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "long")
    two.close_position("BTCUSDT", Decimal("55000"), Decimal("0.4"))
    two.close_position("BTCUSDT", Decimal("55000"))

    assert one.current_equity == two.current_equity
    assert one.cash == two.cash


# ═══════════════════════════════════════════════════════════════════════════
# 3. SHORT LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════

def test_short_open_debits_cash_and_moves_equity_by_the_fee_only():
    eng = _engine()
    before = eng.current_equity
    ok, msg = eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "short")

    assert ok, msg
    fee = Decimal("50000") * Decimal("0.1") * Decimal("0.001")
    assert eng.current_equity == before - fee
    assert_identity(eng, "after short open")


def test_short_profits_when_the_price_falls():
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "short")
    pnl, _ = eng.close_position("BTCUSDT", Decimal("45000"))

    gross = (Decimal("50000") - Decimal("45000")) * Decimal("0.1")       # +500
    fees = (Decimal("50000") + Decimal("45000")) * Decimal("0.1") * Decimal("0.001")
    assert pnl == (gross - fees)
    assert eng.current_equity == CAPITAL + gross - fees
    assert_identity(eng, "after short close")


def test_short_loses_when_the_price_rises():
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "short")
    pnl, _ = eng.close_position("BTCUSDT", Decimal("55000"))

    gross = (Decimal("50000") - Decimal("55000")) * Decimal("0.1")       # -500
    assert pnl < 0
    fees = (Decimal("50000") + Decimal("55000")) * Decimal("0.1") * Decimal("0.001")
    assert eng.current_equity == CAPITAL + gross - fees
    assert_identity(eng, "after losing short")


def test_a_shorts_unrealized_loss_is_visible_in_equity_before_it_is_closed():
    """The short valuation convention exists so that this works."""
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "short")
    equity_at_entry = eng.current_equity

    eng.mark_to_market("BTCUSDT", Decimal("55000"))   # moved against the short

    assert eng.current_equity < equity_at_entry
    assert eng.current_equity == equity_at_entry - Decimal("500")
    assert_identity(eng, "after adverse mark on a short")


def test_short_partial_close():
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "short")
    eng.close_position("BTCUSDT", Decimal("48000"), Decimal("0.5"))

    assert eng.positions["BTCUSDT"].size == Decimal("0.5")
    assert_identity(eng, "after short partial close")


# ═══════════════════════════════════════════════════════════════════════════
# 4. MARK TO MARKET AND DRAWDOWN  — the risk control that could not fire
# ═══════════════════════════════════════════════════════════════════════════

def test_an_open_position_that_falls_40_percent_is_visible_in_equity():
    """THE DEFECT. This used to report no change at all."""
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "long")
    equity_at_entry = eng.current_equity

    eng.mark_to_market("BTCUSDT", Decimal("30000"))   # -40%

    assert eng.current_equity == equity_at_entry - Decimal("20000")
    assert_identity(eng, "after adverse mark")


def test_drawdown_sees_an_open_position_loss():
    """``get_drawdown`` is what the max-drawdown kill switch reads. While it measured
    realised-only equity it could not fire until the loss had already been taken."""
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "long")

    # Not zero: the entry fee is itself a real, if tiny, drawdown from the peak set at
    # construction. 50 on 100,000 is 0.0005. Asserting exactly zero here was wrong, and
    # the first run of this file said so.
    dd_at_entry = eng.get_drawdown()
    assert dd_at_entry < Decimal("0.001"), (
        f"drawdown immediately after opening is {dd_at_entry}; it should be only the fee"
    )

    eng.mark_to_market("BTCUSDT", Decimal("30000"))

    dd = eng.get_drawdown()
    assert dd > Decimal("0.15"), (
        f"a position down 20,000 on a ~99,950 peak reported drawdown {dd}; the "
        "max-drawdown kill switch cannot fire on open risk"
    )


def test_a_favourable_mark_raises_the_peak_so_drawdown_measures_from_it():
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "long")
    eng.mark_to_market("BTCUSDT", Decimal("60000"))
    peak = eng.peak_equity

    eng.mark_to_market("BTCUSDT", Decimal("55000"))

    assert eng.peak_equity == peak, "the peak must not fall back"
    assert eng.get_drawdown() > Decimal("0")


def test_marking_moves_no_cash():
    """A revaluation restates what a position is worth; it does not move money."""
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "long")
    cash = eng.cash

    eng.mark_to_market("BTCUSDT", Decimal("61234.56"))

    assert eng.cash == cash
    assert_identity(eng, "after a pure revaluation")


# ═══════════════════════════════════════════════════════════════════════════
# 5. AGGREGATE EXPOSURE AND CAPITAL  — the over-commitment
# ═══════════════════════════════════════════════════════════════════════════

def test_positions_cannot_aggregate_beyond_the_cash_balance():
    """THE DEFECT. Each open used to see almost the full equity as available, so N
    positions could each be worth up to the whole account."""
    eng = _engine()

    # Each leg is 40% of capital. The third must be refused on cash.
    assert eng.open_position("A", Decimal("40000"), Decimal("1"), "long")[0] is True
    assert eng.open_position("B", Decimal("40000"), Decimal("1"), "long")[0] is True

    ok, reason = eng.open_position("C", Decimal("40000"), Decimal("1"), "long")

    assert ok is False, (
        f"three 40,000 positions were admitted against {CAPITAL} of capital; "
        f"aggregate exposure is unbounded"
    )
    assert "Insufficient capital" in reason
    assert eng.cash >= 0, "cash went negative"


def test_aggregate_exposure_is_reported_and_bounded():
    eng = _engine()
    eng.open_position("A", Decimal("40000"), Decimal("1"), "long")
    eng.open_position("B", Decimal("30000"), Decimal("1"), "long")

    assert eng.total_exposure() == Decimal("70000")
    assert eng.total_exposure() <= CAPITAL
    assert_identity(eng, "with two open positions")


def test_the_refusal_names_the_committed_capital():
    """An operator reading the refusal should be able to see why."""
    eng = _engine()
    eng.open_position("A", Decimal("90000"), Decimal("1"), "long")

    ok, reason = eng.open_position("B", Decimal("90000"), Decimal("1"), "long")

    assert ok is False
    assert "cash is" in reason or "cash" in reason
    assert "already committed" in reason


def test_cash_is_restored_when_positions_are_closed():
    eng = _engine()
    eng.open_position("A", Decimal("40000"), Decimal("1"), "long")
    eng.open_position("B", Decimal("40000"), Decimal("1"), "long")
    eng.close_position("A", Decimal("40000"))

    ok, _ = eng.open_position("C", Decimal("35000"), Decimal("1"), "long")
    assert ok is True, "closing a position did not return its capital to cash"
    assert_identity(eng, "after close and reopen")


def test_the_identity_holds_across_many_mixed_operations():
    """A longer sequence, asserting the identity after every single step."""
    eng = _engine()
    steps = [
        ("open", "A", Decimal("10000"), Decimal("1"), "long"),
        ("open", "B", Decimal("20000"), Decimal("0.5"), "short"),
        ("mark", "A", Decimal("11000")),
        ("open", "C", Decimal("5000"), Decimal("2"), "long"),
        ("close_partial", "A", Decimal("11000"), Decimal("0.5")),
        ("mark", "B", Decimal("19000")),
        ("close_full", "C", Decimal("4500")),
        ("close_full", "A", Decimal("10500")),
        ("close_full", "B", Decimal("18000")),
    ]
    for step in steps:
        if step[0] == "open":
            _, sym, price, size, side = step
            eng.open_position(sym, price, size, side)
        elif step[0] == "mark":
            eng.mark_to_market(step[1], step[2])
        elif step[0] == "close_partial":
            eng.close_position(step[1], step[2], step[3])
        else:
            eng.close_position(step[1], step[2])
        assert_identity(eng, f"after {step}")

    assert eng.positions == {}
    assert eng.cash == eng.current_equity


# ═══════════════════════════════════════════════════════════════════════════
# 6. BACKWARD COMPATIBILITY  — realised results must not move
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "side,entry,exit_price",
    [
        ("long", "50000", "55000"),
        ("long", "50000", "45000"),
        ("long", "50000", "50000"),
        ("short", "50000", "45000"),
        ("short", "50000", "55000"),
    ],
)
def test_a_round_trip_ends_on_the_same_equity_as_before_the_fix(side, entry, exit_price):
    """THE COMPATIBILITY GUARANTEE, and the reason this fix does not invalidate
    realised backtest results.

    The old code reached the final equity by ``equity += net_pnl + entry_fee`` after an
    open that had debited only the fee. The new code debits the notional and credits the
    proceeds. Algebraically both arrive at ``initial + net_pnl``:

        old:  (C - fee_e) + net_pnl + fee_e              = C + net_pnl
        new:  C - E*S - fee_e + X*S - fee_x              = C + gross - fees = C + net_pnl

    So a CLOSED trade's contribution is unchanged. What changed is equity while the
    position is OPEN — which is the defect, not a result.
    """
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal(entry), Decimal("0.1"), side)
    pnl, _ = eng.close_position("BTCUSDT", Decimal(exit_price))

    assert eng.current_equity == CAPITAL + pnl, (
        "a completed round trip no longer ends on initial_capital + net_pnl; realised "
        "backtest results would shift"
    )


def test_get_statistics_total_pnl_still_sums_the_trade_log():
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    eng.close_position("BTCUSDT", Decimal("55000"))

    stats = eng.get_stats()
    assert stats["total_trades"] == 1
    assert stats["total_pnl"] == pytest.approx(float(eng.trade_log[0].pnl))
    assert stats["cash"] == pytest.approx(float(eng.cash))


# ═══════════════════════════════════════════════════════════════════════════
# 7. RESET AND DECIMAL DISCIPLINE
# ═══════════════════════════════════════════════════════════════════════════

def test_reset_restores_cash_and_clears_marks():
    """Leaving cash behind would fund the next run from the previous one's balance."""
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "long")
    eng.mark_to_market("BTCUSDT", Decimal("30000"))

    eng.reset()

    assert eng.cash == CAPITAL
    assert eng.current_equity == CAPITAL
    assert eng.peak_equity == CAPITAL
    assert eng._marks == {}
    assert eng.positions == {}
    assert_identity(eng, "after reset")


def test_every_accounting_figure_is_a_decimal():
    """No float may enter the money path. A float here reintroduces the precision
    problems the Decimal conversion in the constructor exists to prevent."""
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")
    eng.mark_to_market("BTCUSDT", Decimal("51000"))

    for name, value in [
        ("cash", eng.cash),
        ("current_equity", eng.current_equity),
        ("peak_equity", eng.peak_equity),
        ("position_market_value", eng.position_market_value()),
        ("total_exposure", eng.total_exposure()),
        ("get_drawdown", eng.get_drawdown()),
    ]:
        assert isinstance(value, Decimal), f"{name} is {type(value).__name__}, not Decimal"


def test_a_high_precision_asset_keeps_its_precision():
    """A very small quantity at a very large price, and the reverse."""
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("103456.78901234"), Decimal("0.00000001"), "long")
    assert_identity(eng, "high-value asset")

    eng2 = _engine()
    eng2.open_position("SHIBUSDT", Decimal("0.00000912"), Decimal("1000000000"), "long")
    assert_identity(eng2, "high-supply asset")


# ═══════════════════════════════════════════════════════════════════════════
# 8. PARTIAL FILLS
# ═══════════════════════════════════════════════════════════════════════════

def test_a_partial_fill_opens_a_position_and_keeps_the_identity():
    eng = _engine()
    eng.handle_partial_fill(
        symbol="BTCUSDT",
        filled_size=Decimal("0.05"),
        fill_price=Decimal("50000"),
        total_order_size=Decimal("0.1"),
        side="buy",
        fee=Decimal("2.5"),
    )
    assert eng.positions["BTCUSDT"].size == Decimal("0.05")
    assert_identity(eng, "after a partial fill open")


def test_a_second_partial_fill_adds_and_keeps_the_identity():
    eng = _engine()
    for _ in range(2):
        eng.handle_partial_fill(
            symbol="BTCUSDT",
            filled_size=Decimal("0.05"),
            fill_price=Decimal("50000"),
            total_order_size=Decimal("0.1"),
            side="buy",
            fee=Decimal("2.5"),
        )
    assert eng.positions["BTCUSDT"].size == Decimal("0.10")
    assert_identity(eng, "after two partial fills")


def test_a_partial_fill_that_reduces_keeps_the_identity():
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "long")
    eng.handle_partial_fill(
        symbol="BTCUSDT",
        filled_size=Decimal("0.4"),
        fill_price=Decimal("52000"),
        total_order_size=Decimal("0.4"),
        side="sell",
        fee=Decimal("20.8"),
    )
    assert eng.positions["BTCUSDT"].size == Decimal("0.6")
    assert_identity(eng, "after a reducing fill")


def test_a_position_flip_charges_its_fee_and_keeps_the_identity():
    """The flip leg used to open a new position with no cash movement at all."""
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.5"), "long")

    eng.handle_partial_fill(
        symbol="BTCUSDT",
        filled_size=Decimal("0.8"),        # closes 0.5 long, opens 0.3 short
        fill_price=Decimal("52000"),
        total_order_size=Decimal("0.8"),
        side="sell",
        fee=Decimal("41.6"),
    )

    assert eng.positions["BTCUSDT"].side == "short"
    assert eng.positions["BTCUSDT"].size == Decimal("0.3")
    assert_identity(eng, "after a flip")


def test_a_fill_that_fully_closes_leaves_cash_equal_to_equity():
    eng = _engine()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.5"), "long")
    eng.handle_partial_fill(
        symbol="BTCUSDT",
        filled_size=Decimal("0.5"),
        fill_price=Decimal("51000"),
        total_order_size=Decimal("0.5"),
        side="sell",
        fee=Decimal("25.5"),
    )
    assert eng.positions == {}
    assert eng.cash == eng.current_equity
    assert_identity(eng, "after a closing fill")


# ═══════════════════════════════════════════════════════════════════════════
# 9. THE PRODUCTION RISK MANAGER IS STILL IN FORCE
#    Proving that _PermissiveRisk above bypasses a control that genuinely exists,
#    rather than papering over its absence.
# ═══════════════════════════════════════════════════════════════════════════

def test_the_real_risk_manager_still_caps_a_single_position():
    """``max_position_size_pct`` is 10% of equity. A 50% position must be refused."""
    eng = _engine_with_real_risk()

    ok, reason = eng.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "long")

    assert ok is False
    assert "RISK GUARDRAIL BLOCKED" in reason
    assert eng.positions == {}
    assert eng.cash == CAPITAL, "a refused trade must not move cash"
    assert_identity(eng, "after a risk refusal")


def test_a_position_inside_the_risk_cap_is_admitted_by_the_real_manager():
    eng = _engine_with_real_risk()

    ok, _ = eng.open_position("BTCUSDT", Decimal("50000"), Decimal("0.1"), "long")

    assert ok is True, "a 5,000 position against 100,000 equity must be allowed"
    assert_identity(eng, "after an admitted trade")


def test_a_refused_trade_is_recorded_as_blocked():
    eng = _engine_with_real_risk()
    eng.open_position("BTCUSDT", Decimal("50000"), Decimal("1"), "long")

    blocked = eng.get_blocked_trades()
    assert len(blocked) == 1
    assert blocked[0]["symbol"] == "BTCUSDT"


def test_the_real_manager_sees_marked_equity_not_realised_equity():
    """The guardrails are measured against ``current_equity``, which is now marked to
    market. So a drawdown on an open position tightens the next position's cap — which
    is the behaviour a risk control should have and previously could not."""
    eng = _engine_with_real_risk()
    eng.open_position("A", Decimal("10000"), Decimal("0.5"), "long")   # 5,000, inside 10%
    eng.mark_to_market("A", Decimal("2000"))                            # -4,000

    marked_equity = eng.current_equity
    assert marked_equity < CAPITAL, "the adverse mark did not reduce equity"

    # The next open syncs the manager from the engine BEFORE placing, so the figure the
    # guardrails are measured against is the marked one. Compared against `marked_equity`
    # rather than against the engine's equity afterwards, which has since moved by the
    # new position's own fee — the first run of this file compared the wrong pair.
    eng.open_position("B", Decimal("10000"), Decimal("0.5"), "long")

    assert eng.risk_manager.current_equity == pytest.approx(float(marked_equity)), (
        "the risk manager was measured against unmarked equity, so an open position's "
        "loss does not tighten the next position's cap"
    )
    assert eng.risk_manager.current_equity < float(CAPITAL)


class _PermissiveRisk:
    """A risk manager that approves everything, so the CASH bound is what is under test.

    The default ``RiskManager`` caps a single position at 10% of equity and open trades
    at 5. Those are real controls and are tested elsewhere; here they would mask the
    aggregate-exposure question by refusing on a different ground, which would make the
    test pass without proving anything about cash.
    """

    def __init__(self):
        self.current_equity = float(CAPITAL)
        self.peak_equity = float(CAPITAL)
        self.max_open_trades = 99
        self._open_trades_count = 0
        self._daily_pnl = 0.0

    def can_open_position(self, position_value, open_trades_count):
        return True, "permitted"

    def update_open_trades_count(self, n):
        self._open_trades_count = n

    def record_trade_open(self):
        pass

    def record_trade_close(self):
        pass

    def update_equity(self, pnl):
        self.current_equity += float(pnl)

    def get_risk_status(self):
        return {"permissive": True}
