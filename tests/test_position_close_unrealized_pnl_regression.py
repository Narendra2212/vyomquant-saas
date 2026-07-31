"""
Regression test for unrealized PnL not being reset to zero on position close.

Defect Family: Financial Correctness - Unrealized PnL not reset on position close

Root Cause:
When a position was closed, unrealized_pnl was recalculated with the closing price
instead of being set to 0. This caused closed positions to show unrealized gains/losses
when they should have 0 unrealized PnL (no open position = no unrealized PnL).

Expected Behavior:
- When a position is closed, unrealized_pnl must be 0
- When a position is closed, unrealized_pnl_pct must be 0
- Only realized_pnl should contain the closing PnL

Files Fixed:
- backend_app/backend/portfolio_management.py (Position.close() method)
- backend_app/core/replay_reconstruction_engine.py (_apply_position_closed() method)
- backend_app/backend/distributed_execution/replay_engine.py (_apply_position_closed_event() method)
"""

import pytest
from decimal import Decimal
from datetime import datetime
from backend_app.backend.portfolio_management import Position


def test_position_close_resets_unrealized_pnl_long():
    """
    Test that closing a long position resets unrealized_pnl to 0.
    
    Scenario:
    1. Open long position at 50000 with quantity 1
    2. Update price to 60000 (unrealized_pnl should be 10000)
    3. Close position at 60000
    4. Verify unrealized_pnl is 0 after close
    """
    position = Position(
        id="test_pos_1",
        strategy_id="test_strategy",
        symbol="BTCUSD",
        side="long",
        entry_price=Decimal("50000"),
        entry_time=datetime.now(),
        quantity=Decimal("1"),
    )
    
    # Update price to create unrealized PnL
    position.update_price(Decimal("60000"))
    
    # Verify unrealized PnL before close
    assert position.unrealized_pnl == Decimal("10000"), \
        f"Expected unrealized_pnl=10000, got {position.unrealized_pnl}"
    
    # Close position
    position.close(Decimal("60000"))
    
    # CRITICAL ASSERTIONS: unrealized_pnl must be 0 after close
    assert position.unrealized_pnl == Decimal("0"), \
        f"CRITICAL: unrealized_pnl must be 0 after close, got {position.unrealized_pnl}"
    
    assert position.unrealized_pnl_pct == Decimal("0"), \
        f"CRITICAL: unrealized_pnl_pct must be 0 after close, got {position.unrealized_pnl_pct}"
    
    # Verify realized PnL is calculated correctly
    assert position.realized_pnl == Decimal("10000"), \
        f"Expected realized_pnl=10000, got {position.realized_pnl}"
    
    # Verify status is closed
    assert position.status == "closed", \
        f"Expected status='closed', got {position.status}"


def test_position_close_resets_unrealized_pnl_short():
    """
    Test that closing a short position resets unrealized_pnl to 0.
    
    Scenario:
    1. Open short position at 50000 with quantity 1
    2. Update price to 40000 (unrealized_pnl should be 10000)
    3. Close position at 40000
    4. Verify unrealized_pnl is 0 after close
    """
    position = Position(
        id="test_pos_2",
        strategy_id="test_strategy",
        symbol="BTCUSD",
        side="short",
        entry_price=Decimal("50000"),
        entry_time=datetime.now(),
        quantity=Decimal("1"),
    )
    
    # Update price to create unrealized PnL
    position.update_price(Decimal("40000"))
    
    # Verify unrealized PnL before close
    assert position.unrealized_pnl == Decimal("10000"), \
        f"Expected unrealized_pnl=10000, got {position.unrealized_pnl}"
    
    # Close position
    position.close(Decimal("40000"))
    
    # CRITICAL ASSERTIONS: unrealized_pnl must be 0 after close
    assert position.unrealized_pnl == Decimal("0"), \
        f"CRITICAL: unrealized_pnl must be 0 after close, got {position.unrealized_pnl}"
    
    assert position.unrealized_pnl_pct == Decimal("0"), \
        f"CRITICAL: unrealized_pnl_pct must be 0 after close, got {position.unrealized_pnl_pct}"
    
    # Verify realized PnL is calculated correctly
    assert position.realized_pnl == Decimal("10000"), \
        f"Expected realized_pnl=10000, got {position.realized_pnl}"
    
    # Verify status is closed
    assert position.status == "closed", \
        f"Expected status='closed', got {position.status}"


def test_position_close_with_loss():
    """
    Test that closing a losing position resets unrealized_pnl to 0.
    
    Scenario:
    1. Open long position at 50000 with quantity 1
    2. Close position at 40000 (loss of 10000)
    3. Verify unrealized_pnl is 0 after close
    """
    position = Position(
        id="test_pos_3",
        strategy_id="test_strategy",
        symbol="BTCUSD",
        side="long",
        entry_price=Decimal("50000"),
        entry_time=datetime.now(),
        quantity=Decimal("1"),
    )
    
    # Close position at a loss
    position.close(Decimal("40000"))
    
    # CRITICAL ASSERTIONS: unrealized_pnl must be 0 after close
    assert position.unrealized_pnl == Decimal("0"), \
        f"CRITICAL: unrealized_pnl must be 0 after close, got {position.unrealized_pnl}"
    
    assert position.unrealized_pnl_pct == Decimal("0"), \
        f"CRITICAL: unrealized_pnl_pct must be 0 after close, got {position.unrealized_pnl_pct}"
    
    # Verify realized PnL reflects the loss
    assert position.realized_pnl == Decimal("-10000"), \
        f"Expected realized_pnl=-10000, got {position.realized_pnl}"


def test_position_close_multiple_times_idempotent():
    """
    Test that closing a position multiple times is idempotent.
    
    Scenario:
    1. Open and close a position
    2. Try to close it again
    3. Verify unrealized_pnl remains 0
    """
    position = Position(
        id="test_pos_4",
        strategy_id="test_strategy",
        symbol="BTCUSD",
        side="long",
        entry_price=Decimal("50000"),
        entry_time=datetime.now(),
        quantity=Decimal("1"),
    )
    
    # Close position
    position.close(Decimal("60000"))
    
    # Verify first close
    assert position.unrealized_pnl == Decimal("0")
    assert position.realized_pnl == Decimal("10000")
    
    # Try to close again (idempotent)
    position.close(Decimal("70000"))
    
    # Verify unrealized_pnl is still 0
    assert position.unrealized_pnl == Decimal("0"), \
        f"CRITICAL: unrealized_pnl must remain 0 after second close, got {position.unrealized_pnl}"
    
    assert position.unrealized_pnl_pct == Decimal("0"), \
        f"CRITICAL: unrealized_pnl_pct must remain 0 after second close, got {position.unrealized_pnl_pct}"


def test_position_close_with_zero_cost_basis():
    """
    Test that closing a position with zero cost basis handles unrealized_pnl correctly.
    
    Scenario:
    1. Open position with 0 entry price (edge case)
    2. Close position
    3. Verify unrealized_pnl is 0
    """
    position = Position(
        id="test_pos_5",
        strategy_id="test_strategy",
        symbol="BTCUSD",
        side="long",
        entry_price=Decimal("0"),
        entry_time=datetime.now(),
        quantity=Decimal("1"),
    )
    
    # Close position
    position.close(Decimal("50000"))
    
    # CRITICAL ASSERTIONS: unrealized_pnl must be 0 after close
    assert position.unrealized_pnl == Decimal("0"), \
        f"CRITICAL: unrealized_pnl must be 0 after close, got {position.unrealized_pnl}"
    
    assert position.unrealized_pnl_pct == Decimal("0"), \
        f"CRITICAL: unrealized_pnl_pct must be 0 after close, got {position.unrealized_pnl_pct}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
