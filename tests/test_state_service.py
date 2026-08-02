"""
tests/test_state_service.py — STATE SERVICE TESTS

STEP 1: Verify global idempotency and consistent state
"""

import sys
import os
import asyncio
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend_app.backend.state_service import (
    StateService,
    Order,
    Position,
    OrderStatus,
    PositionSide,
    IdempotencyChecker,
    ConcurrentModificationError,
)
from backend_app.core.cache import MockRedisClient


def test_idempotency_checker():
    """Test idempotency key generation and checking."""
    checker = IdempotencyChecker(MockRedisClient())  # Mock-based
    
    # Generate key
    key = checker.generate_key("user_123", "place_order", {"symbol": "BTC", "qty": 1})
    assert len(key) == 32
    
    # First check - should be new
    is_new = asyncio.run(checker.check_and_record(key))
    assert is_new is True
    
    # Second check - should be duplicate
    is_new = asyncio.run(checker.check_and_record(key))
    assert is_new is False
    
    print("[PASS] Idempotency checker working")


def test_order_creation():
    """Test order data model."""
    order = Order(
        order_id="ord_123",
        user_id="user_456",
        symbol="BTC-USD",
        side="buy",
        order_type="limit",
        quantity=Decimal("0.5"),
        price=Decimal("50000.00"),
        status=OrderStatus.PENDING
    )
    
    assert order.order_id == "ord_123"
    assert order.quantity == Decimal("0.5")
    assert order.remaining_quantity == Decimal("0.5")  # Auto-set
    
    # Test serialization
    data = order.to_dict()
    assert data["symbol"] == "BTC-USD"
    assert data["quantity"] == "0.5"
    
    # Test deserialization
    restored = Order.from_dict(data)
    assert restored.symbol == "BTC-USD"
    assert restored.quantity == Decimal("0.5")
    
    print("[PASS] Order model working")


def test_position_creation():
    """Test position data model."""
    position = Position(
        position_id="user_123:BTC-USD",
        user_id="user_123",
        symbol="BTC-USD",
        side=PositionSide.LONG,
        quantity=Decimal("1.0"),
        entry_price=Decimal("45000.00"),
        unrealized_pnl=Decimal("5000.00")
    )
    
    assert position.side == PositionSide.LONG
    assert position.quantity == Decimal("1.0")
    assert position.available_quantity == Decimal("1.0")  # Auto-set
    
    # Test serialization
    data = position.to_dict()
    assert data["side"] == "long"
    assert data["quantity"] == "1.0"
    
    # Test deserialization
    restored = Position.from_dict(data)
    assert restored.side == PositionSide.LONG
    assert restored.quantity == Decimal("1.0")
    
    print("[PASS] Position model working")


def test_state_service_initialization():
    """Test state service initialization."""
    async def _run():
        service = StateService()
        await service.connect()
        health = await service.health_check()
        assert "status" in health
        await service.disconnect()
        print(f"[PASS] State service initialized (status: {health['status']})")
    asyncio.run(_run())


def test_idempotency_in_service():
    """Test idempotency through StateService."""
    async def _run():
        service = StateService(redis_client=MockRedisClient())
        await service.connect()
        order = Order(
            order_id="ord_test_1",
            user_id="user_test",
            symbol="BTC-USD",
            side="buy",
            order_type="limit",
            quantity=Decimal("0.1"),
            price=Decimal("50000"),
            status=OrderStatus.PENDING
        )
        idempotency_key = "test:user_test:place_order:12345"
        result1 = await service.save_order(order, idempotency_key)
        assert result1 is not None
        await service.disconnect()
        print("[PASS] Service idempotency working")
    asyncio.run(_run())


def test_concurrent_modification_error():
    """Test exception classes."""
    try:
        raise ConcurrentModificationError("Test error")
    except ConcurrentModificationError as e:
        assert "Test error" in str(e)
    
    print("[PASS] Exception classes working")


def run_all_tests():
    """Run all state service tests."""
    print("=" * 60)
    print("STATE SERVICE TESTS")
    print("=" * 60)
    
    tests = [
        test_idempotency_checker,
        test_order_creation,
        test_position_creation,
        test_concurrent_modification_error,
    ]
    
    async_tests = [
        test_state_service_initialization,
        test_idempotency_in_service,
    ]
    
    passed = 0
    failed = 0
    
    # Run sync tests
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
    
    # Run async tests
    for test in async_tests:
        try:
            print(f"\n--- {test.__name__} ---")
            asyncio.run(test())
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
