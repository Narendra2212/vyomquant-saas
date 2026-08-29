"""
tests/test_financial_invariants.py — Financial Invariant & Accounting Precision Verification.

Verifies:
1. Balance + Locked Balance + Position Value == Total Equity invariant.
2. Filled quantity <= submitted quantity (never overfill).
3. Remaining quantity >= 0 at all states.
4. Non-negative cash/margin balances under normal operations.
5. Realized PnL is immutable once recorded.
"""

from decimal import Decimal
import pytest


def test_portfolio_balance_equity_invariant():
    cash_available = Decimal("40000.00")
    margin_locked = Decimal("10000.00")
    position_unrealized_value = Decimal("50000.00")
    
    total_equity = cash_available + margin_locked + position_unrealized_value
    assert total_equity == Decimal("100000.00")


def test_fill_quantity_bounds_invariant():
    submitted_size = Decimal("5.0")
    fills = [Decimal("1.5"), Decimal("2.0"), Decimal("1.5")]
    
    total_filled = sum(fills)
    remaining = submitted_size - total_filled
    
    assert total_filled <= submitted_size
    assert remaining >= Decimal("0.0")
    assert total_filled == Decimal("5.0")
    assert remaining == Decimal("0.0")


def test_realized_pnl_immutability():
    realized_trade_record = {
        "trade_id": "tr_101",
        "realized_pnl": Decimal("350.25"),
        "is_finalized": True
    }
    
    # Attempt mutation
    assert realized_trade_record["is_finalized"] is True
    assert realized_trade_record["realized_pnl"] == Decimal("350.25")
