"""
tests/test_order_state_engine.py — ORDER STATE ENGINE TESTS

STEP 5: Verify order state machine and partial fill handling
"""

import sys
import os
import asyncio
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.order_state_engine import (
    OrderStateEngine,
    OrderState,
    OrderLifecycle,
    FillRecord,
    get_order_state_engine,
)


def test_order_state_enum():
    """Test order state enumeration."""
    assert OrderState.CREATED.value == "created"
    assert OrderState.SUBMITTED.value == "submitted"
    assert OrderState.OPEN.value == "open"
    assert OrderState.PARTIAL.value == "partial"
    assert OrderState.FILLED.value == "filled"
    assert OrderState.CANCELLED.value == "cancelled"
    assert OrderState.FAILED.value == "failed"
    assert OrderState.TIMED_OUT.value == "timed_out"
    
    print("[PASS] Order states defined")


def test_lifecycle_creation():
    """Test order lifecycle creation."""
    lifecycle = OrderLifecycle(
        order_id="ord_123",
        user_id="user_456",
        symbol="BTC-USD",
        side="buy",
        order_type="limit",
        original_quantity=Decimal("1.0"),
        current_state=OrderState.CREATED,
        timeout_seconds=30.0
    )
    
    assert lifecycle.order_id == "ord_123"
    assert lifecycle.current_state == OrderState.CREATED
    assert lifecycle.remaining_quantity == Decimal("1.0")
    assert len(lifecycle.fill_history) == 0
    
    print("[PASS] Lifecycle creation working")


def test_fill_record():
    """Test fill record creation."""
    fill = FillRecord(
        fill_id="fill_001",
        order_id="ord_123",
        timestamp=__import__('datetime').datetime.utcnow(),
        filled_quantity=Decimal("0.3"),
        fill_price=Decimal("50100.00"),
        total_filled=Decimal("0.3"),
        remaining_quantity=Decimal("0.7"),
        fee=Decimal("0.0015"),
        fee_currency="BTC"
    )
    
    assert fill.fill_id == "fill_001"
    assert fill.filled_quantity == Decimal("0.3")
    assert fill.remaining_quantity == Decimal("0.7")
    
    # Test serialization
    data = fill.to_dict()
    assert data["fill_id"] == "fill_001"
    assert data["filled_quantity"] == "0.3"
    
    print("[PASS] Fill record working")


def test_state_transitions():
    """Test state transitions."""
    lifecycle = OrderLifecycle(
        order_id="ord_123",
        user_id="user_456",
        symbol="BTC-USD",
        side="buy",
        order_type="limit",
        original_quantity=Decimal("1.0"),
        current_state=OrderState.CREATED
    )
    
    # CREATED → SUBMITTED
    lifecycle.transition_to(OrderState.SUBMITTED)
    assert lifecycle.current_state == OrderState.SUBMITTED
    assert len(lifecycle.state_history) == 1
    
    # SUBMITTED → OPEN
    lifecycle.transition_to(OrderState.OPEN)
    assert lifecycle.current_state == OrderState.OPEN
    
    # OPEN → PARTIAL
    lifecycle.transition_to(OrderState.PARTIAL)
    assert lifecycle.current_state == OrderState.PARTIAL
    
    # PARTIAL → FILLED
    lifecycle.transition_to(OrderState.FILLED)
    assert lifecycle.current_state == OrderState.FILLED
    
    print("[PASS] State transitions working")


def test_partial_fill_accumulation():
    """Test partial fill accumulation."""
    lifecycle = OrderLifecycle(
        order_id="ord_123",
        user_id="user_456",
        symbol="BTC-USD",
        side="buy",
        order_type="limit",
        original_quantity=Decimal("1.0"),
        current_state=OrderState.OPEN
    )
    
    # Fill 1: 0.3 @ $50,100
    fill1 = FillRecord(
        fill_id="fill_1",
        order_id="ord_123",
        timestamp=__import__('datetime').datetime.utcnow(),
        filled_quantity=Decimal("0.3"),
        fill_price=Decimal("50100.00"),
        total_filled=Decimal("0.3"),
        remaining_quantity=Decimal("0.7"),
    )
    lifecycle.add_fill(fill1)
    
    assert lifecycle.total_filled == Decimal("0.3")
    assert lifecycle.remaining_quantity == Decimal("0.7")
    assert lifecycle.average_fill_price == Decimal("50100.00")
    
    # Fill 2: 0.4 @ $49,900
    fill2 = FillRecord(
        fill_id="fill_2",
        order_id="ord_123",
        timestamp=__import__('datetime').datetime.utcnow(),
        filled_quantity=Decimal("0.4"),
        fill_price=Decimal("49900.00"),
        total_filled=Decimal("0.7"),
        remaining_quantity=Decimal("0.3"),
    )
    lifecycle.add_fill(fill2)
    
    assert lifecycle.total_filled == Decimal("0.7")
    assert lifecycle.remaining_quantity == Decimal("0.3")
    # Weighted average: (0.3*50100 + 0.4*49900) / 0.7 = 50,014.2857...
    expected_avg = (Decimal("0.3") * Decimal("50100") + Decimal("0.4") * Decimal("49900")) / Decimal("0.7")
    assert abs(lifecycle.average_fill_price - expected_avg) < Decimal("0.01")
    
    # Fill 3: 0.3 @ $50,050 (complete)
    fill3 = FillRecord(
        fill_id="fill_3",
        order_id="ord_123",
        timestamp=__import__('datetime').datetime.utcnow(),
        filled_quantity=Decimal("0.3"),
        fill_price=Decimal("50050.00"),
        total_filled=Decimal("1.0"),
        remaining_quantity=Decimal("0.0"),
    )
    lifecycle.add_fill(fill3)
    
    assert lifecycle.total_filled == Decimal("1.0")
    assert lifecycle.remaining_quantity == Decimal("0.0")
    
    print("[PASS] Partial fill accumulation working")


def test_terminal_states():
    """Test terminal state detection."""
    # FILLED is terminal
    lifecycle = OrderLifecycle(
        order_id="ord_1",
        user_id="user_1",
        symbol="BTC-USD",
        side="buy",
        order_type="limit",
        original_quantity=Decimal("1.0"),
        current_state=OrderState.FILLED
    )
    assert lifecycle.is_terminal() is True
    assert lifecycle.is_active() is False
    
    # CANCELLED is terminal
    lifecycle.current_state = OrderState.CANCELLED
    assert lifecycle.is_terminal() is True
    
    # OPEN is not terminal
    lifecycle.current_state = OrderState.OPEN
    assert lifecycle.is_terminal() is False
    assert lifecycle.is_active() is True
    
    # PARTIAL is not terminal
    lifecycle.current_state = OrderState.PARTIAL
    assert lifecycle.is_terminal() is False
    assert lifecycle.is_active() is True
    
    print("[PASS] Terminal states working")


def test_timeout_detection():
    """Test timeout detection."""
    lifecycle = OrderLifecycle(
        order_id="ord_1",
        user_id="user_1",
        symbol="BTC-USD",
        side="buy",
        order_type="limit",
        original_quantity=Decimal("1.0"),
        current_state=OrderState.SUBMITTED,
        timeout_seconds=1.0  # 1 second timeout for testing
    )
    
    # Set timeout in the past
    lifecycle.timeout_at = __import__('datetime').datetime.utcnow() - __import__('datetime').timedelta(seconds=2)
    
    assert lifecycle.has_timed_out() is True
    
    # Set timeout in the future
    lifecycle.timeout_at = __import__('datetime').datetime.utcnow() + __import__('datetime').timedelta(seconds=10)
    
    assert lifecycle.has_timed_out() is False
    
    print("[PASS] Timeout detection working")


def test_engine_initialization():
    """Test order state engine initialization."""
    engine = OrderStateEngine(
        default_timeout_seconds=30.0,
        auto_update_positions=True,
        enable_timeouts=False
    )
    
    assert engine.default_timeout_seconds == 30.0
    assert engine.auto_update_positions is True
    assert engine.enable_timeouts is False
    
    print("[PASS] Engine initialization working")


def test_global_singleton():
    """Test global singleton instance."""
    engine1 = get_order_state_engine()
    engine2 = get_order_state_engine()
    
    assert engine1 is engine2
    print("[PASS] Global singleton working")


def run_all_tests():
    """Run all order state engine tests."""
    print("=" * 60)
    print("ORDER STATE ENGINE TESTS")
    print("=" * 60)
    
    tests = [
        test_order_state_enum,
        test_lifecycle_creation,
        test_fill_record,
        test_state_transitions,
        test_partial_fill_accumulation,
        test_terminal_states,
        test_timeout_detection,
        test_engine_initialization,
        test_global_singleton,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            print(f"\n--- {test.__name__} ---")
            test()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
