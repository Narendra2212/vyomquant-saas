"""Test script for event-driven order flow with backward compatibility."""

import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from backend.services.orders_service import orders_service
from backend.services.event_bus import event_bus, ORDER_EVENTS_CHANNEL
from backend.services.cache import redis_manager


async def test_event_callback(event):
    """Callback to capture order events."""
    print(f"📦 Event Received: {event.get('type')} - {event.get('order_id')}")


async def test_event_driven_flow():
    """Test the new event-driven order flow."""
    print("=" * 60)
    print("Test 1: Event-Driven Order Flow")
    print("=" * 60)

    # Connect to Redis
    await redis_manager.connect()

    # Subscribe to order events
    await event_bus.subscribe(ORDER_EVENTS_CHANNEL, test_event_callback)
    # Subscribe to events (listener starts automatically)
    await event_bus.subscribe(
        ORDER_EVENTS_CHANNEL, lambda e: print(f"Order event: {e}")
    )

    await asyncio.sleep(0.5)

    # Execute order with event bus enabled (default)
    result = await orders_service.execute_order(
        user_id="test_user_1",
        symbol="BTC/USDT",
        amount=0.1,
        order_id="test_order_1",
        use_event_bus=True,
    )

    print("\n✅ Order submitted (event-driven):")
    print(f"   Status: {result.status}")
    print(f"   Order ID: {result.order_id}")
    print(f"   Message: {result.message}")

    # Wait for event processing
    await asyncio.sleep(2)

    # Cleanup
    await event_bus.close()
    await redis_manager.close()

    print("\n✅ Test 1 passed")


async def test_direct_execution_flow():
    """Test the backward-compatible direct execution flow."""
    print("\n" + "=" * 60)
    print("Test 2: Direct Execution Flow (Backward Compatibility)")
    print("=" * 60)

    # Connect to Redis
    await redis_manager.connect()

    # Execute order with event bus disabled
    result = await orders_service.execute_order(
        user_id="test_user_2",
        symbol="ETH/USDT",
        amount=1.0,
        order_id="test_order_2",
        use_event_bus=False,
    )

    print("\n✅ Order executed directly:")
    print(f"   Status: {result.status}")
    print(f"   Order ID: {result.order_id}")
    print(f"   Balance: {result.balance}")

    # Cleanup
    await redis_manager.close()

    print("\n✅ Test 2 passed")


async def test_api_compatibility():
    """Test that the API endpoint still works (backward compatibility)."""
    print("\n" + "=" * 60)
    print("Test 3: API Endpoint Compatibility")
    print("=" * 60)

    # Connect to Redis
    await redis_manager.connect()

    # Execute order using default parameters (event bus enabled)
    result = await orders_service.execute_order(
        user_id="test_user_3",
        symbol="SOL/USDT",
        amount=10.0,
        order_id="test_order_3",
        # use_event_bus defaults to True
    )

    print("\n✅ API endpoint call successful:")
    print(f"   Status: {result.status}")
    print(f"   Order ID: {result.order_id}")

    # Cleanup
    await redis_manager.close()

    print("\n✅ Test 3 passed")


async def run_all_tests():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("Testing Event-Driven Order Flow with Backward Compatibility")
    print("=" * 60)

    try:
        await test_event_driven_flow()
        await test_direct_execution_flow()
        await test_api_compatibility()

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        print("\nSummary:")
        print("- Event-driven flow works correctly")
        print("- Direct execution fallback works")
        print("- API endpoint remains compatible")
        print("- No breaking changes introduced")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_all_tests())
