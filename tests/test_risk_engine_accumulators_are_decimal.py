"""RiskEngine's loss accumulators must actually accumulate, so the kill switch can fire.

WHAT THIS GUARDS
----------------
``RiskEngine.__init__`` converts every monetary field to ``Decimal``. But
``update_equity`` declared its parameter as ``float`` and did::

    self.current_equity += pnl
    ...
    self.daily_loss += abs(pnl)

``Decimal + float`` raises ``TypeError`` in Python, so the only calls that ever worked
were the ones that happened to pass an ``int``. And ``reset_daily_loss`` assigned
``0.0`` — a float — into the Decimal accumulator, so even a working caller broke on the
first loss recorded after a day boundary.

Why this is a safety defect and not a typing nit: ``daily_loss`` is the value
``check_daily_loss`` compares against ``daily_loss_limit``, and ``can_trade`` gates
trading on that comparison. An accumulator that raises instead of accumulating is a
kill switch that never fires — the exception propagates out of whatever was recording
the loss, the loss is never added, and the limit is never reached however far the
account falls.

Reproduced by execution during the pre-launch audit:

    RiskEngine(initial_capital=10000).update_equity(-500.0)
    -> TypeError: unsupported operand type(s) for +=: 'decimal.Decimal' and 'float'
"""

from decimal import Decimal

import pytest

from backend_app.core.risk_engine import RiskEngine


def _engine(**kw):
    return RiskEngine(initial_capital=10000, **kw)


# ─────────────────────────────────────────────────────────────────────────────
# The accumulators accept what callers actually pass
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "pnl",
    [-500.0, -500, Decimal("-500"), "-500", -0.5, Decimal("-0.00000001")],
)
def test_update_equity_accepts_every_numeric_form_a_caller_uses(pnl):
    """THE DEFECT. A float loss used to raise TypeError before it could be recorded."""
    eng = _engine()
    eng.update_equity(pnl)  # must not raise
    assert eng.daily_loss > 0


def test_a_float_loss_is_recorded_in_the_daily_accumulator():
    eng = _engine()
    eng.update_equity(-500.0)

    assert eng.daily_loss == Decimal("500")
    assert eng.current_equity == Decimal("9500")
    assert isinstance(eng.daily_loss, Decimal)
    assert isinstance(eng.current_equity, Decimal)


def test_a_profit_does_not_add_to_the_loss_accumulator():
    eng = _engine()
    eng.update_equity(1000.0)

    assert eng.daily_loss == Decimal("0")
    assert eng.current_equity == Decimal("11000")
    assert eng.peak_equity == Decimal("11000")


def test_losses_accumulate_across_several_trades():
    eng = _engine()
    for loss in (-100.0, -250.5, -49.5):
        eng.update_equity(loss)

    assert eng.daily_loss == Decimal("400.0")
    assert eng.current_equity == Decimal("9600.0")


# ─────────────────────────────────────────────────────────────────────────────
# The kill switch it exists to drive
# ─────────────────────────────────────────────────────────────────────────────

def test_the_daily_loss_limit_actually_blocks_trading():
    """The whole point: a 5% daily loss on 10,000 is 500, so 600 must stop trading."""
    eng = _engine(daily_loss_limit=0.05)

    allowed_before, _ = eng.check_daily_loss()
    assert allowed_before is True

    eng.update_equity(-600.0)

    allowed_after, reason = eng.check_daily_loss()
    assert allowed_after is False, (
        "a 600 loss against a 500 daily limit did not stop trading; the daily-loss "
        "kill switch is inoperative"
    )
    assert "Daily loss limit exceeded" in reason
    assert eng.can_trade() is False


def test_a_loss_just_under_the_limit_still_permits_trading():
    """The bound must be the stated one, not an approximation of it."""
    eng = _engine(daily_loss_limit=0.05)
    eng.update_equity(-499.99)

    allowed, _ = eng.check_daily_loss()
    assert allowed is True
    assert eng.can_trade() is True


# ─────────────────────────────────────────────────────────────────────────────
# The day boundary
# ─────────────────────────────────────────────────────────────────────────────

def test_reset_keeps_the_accumulator_a_decimal():
    """``reset_daily_loss`` assigned ``0.0``, replacing the Decimal with a float."""
    eng = _engine()
    eng.update_equity(-100.0)
    eng.reset_daily_loss()

    assert eng.daily_loss == Decimal("0")
    assert isinstance(eng.daily_loss, Decimal), (
        "reset_daily_loss replaced the Decimal accumulator with a float; the next loss "
        "raises TypeError and is never recorded"
    )


def test_losses_still_accumulate_after_a_day_rollover():
    """The sequence that broke in production: accumulate, reset, accumulate again."""
    eng = _engine()
    eng.update_equity(-100.0)
    eng.reset_daily_loss()

    eng.update_equity(Decimal("-250"))  # the form the Decimal callers use
    eng.update_equity(-250.0)           # and the form the float callers use

    assert eng.daily_loss == Decimal("500")


def test_the_kill_switch_still_fires_on_the_second_day():
    eng = _engine(daily_loss_limit=0.05)
    eng.update_equity(-600.0)
    eng.reset_daily_loss()

    assert eng.can_trade() is True
    eng.update_equity(-600.0)
    assert eng.can_trade() is False


# ─────────────────────────────────────────────────────────────────────────────
# Position sizing is bounded
# ─────────────────────────────────────────────────────────────────────────────

def test_atr_sizing_is_capped_by_the_account_rather_than_by_volatility():
    """THE DEFECT, with the audit's own worked example.

    equity 10,000, risk_per_trade 1% -> a 100 risk budget. BTC at 50,000 with ATR 2 and
    multiplier 2 -> stop distance 4 -> 25 BTC -> 1,250,000 notional, 125x the account.
    The lower the volatility, the larger the breach.
    """
    eng = RiskEngine(initial_capital=10000, risk_per_trade=0.01, atr_multiplier=2.0)

    size = eng.calculate_position_size(price=50000.0, atr=2.0)
    notional = size * 50000.0

    assert notional <= 10000.0 * 1.0001, (
        f"ATR sizing returned {size} units = {notional} notional against 10,000 of "
        "equity; risk-based sizing is unbounded as volatility falls"
    )


def test_a_tighter_cap_is_honoured():
    eng = RiskEngine(
        initial_capital=10000,
        risk_per_trade=0.01,
        atr_multiplier=2.0,
        max_position_notional_pct=0.10,
    )

    size = eng.calculate_position_size(price=50000.0, atr=2.0)
    assert size * 50000.0 <= 1000.0 * 1.0001


def test_a_normal_size_is_not_reduced_by_the_cap():
    """The cap must only remove the impossible tail. With a wide stop the risk-based
    size is already small, and the function must return it untouched."""
    eng = RiskEngine(initial_capital=10000, risk_per_trade=0.01, atr_multiplier=2.0)

    # stop distance 2000 -> 100/2000 = 0.05 units -> 2,500 notional, well inside equity
    size = eng.calculate_position_size(price=50000.0, atr=1000.0)

    assert size == pytest.approx(0.05)


def test_sizing_still_returns_zero_for_a_zero_stop_distance():
    """Pre-existing behaviour, retained: no stop distance means no sizeable position."""
    eng = _engine()
    assert eng.calculate_position_size(price=50000.0, atr=0.0) == 0.0


def test_an_uncapped_engine_is_still_possible_for_callers_that_want_one():
    """``None`` disables the cap, so the change is not imposed on a caller that has its
    own downstream bound and needs the raw risk-based figure."""
    eng = RiskEngine(
        initial_capital=10000,
        risk_per_trade=0.01,
        atr_multiplier=2.0,
        max_position_notional_pct=None,
    )

    size = eng.calculate_position_size(price=50000.0, atr=2.0)
    assert size == pytest.approx(25.0)
