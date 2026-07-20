"""Test script for execution event worker."""

import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from backend.services.event_bus import event_bus, ORDER_EVENTS_CHANNEL
from backend.services.cache import redis_manager


async def test_execution_callback(event):
    """Callback to capture execution events."""
    print(f"⚡ Event Received: {event.get('type')} - {event.get('order_id')}")
    if event.get("type") == "ORDER_CONFIRMATION":
        print(f"   Status: {event.get('status')}")
        print(f"   Reason: {event.get('reason', 'N/A')}")
        if event.get("broker_order_id"):
            print(f"   Broker Order ID: {event.get('broker_order_id')}")
            print(f"   Filled Price: {event.get('filled_price')}")
            print(f"   Filled Amount: {event.get('filled_amount')}")


async def test_execution_worker():
    """Test the execution event worker."""
    print("=" * 60)
    print("Testing Execution Event Worker")
    print("=" * 60)

    # Connect to Redis
    await redis_manager.connect()

    # Subscribe to order events
    await event_bus.subscribe(ORDER_EVENTS_CHANNEL, test_execution_callback)
    # Subscribe to events (listener starts automatically)
    await event_bus.subscribe(
        ORDER_EVENTS_CHANNEL, lambda e: print(f"Order event: {e}")
    )

    await asyncio.sleep(0.5)

    # Test 1: Valid order
    print("\nTest 1: Valid order")
    valid_order = {
        "type": "ORDER_REQUEST",
        "user_id": "test_user_1",
        "symbol": "BTC/USDT",
        "amount": 0.1,
        "order_id": "test_exec_1",
        "timestamp": 1234567890000,
    }

    subscribers = await event_bus.publish(ORDER_EVENTS_CHANNEL, valid_order)
    print(f"   Published to {subscribers} subscribers")
    await asyncio.sleep(2)

    # Test 2: Invalid amount
    print("\nTest 2: Invalid amount (negative)")
    invalid_amount_order = {
        "type": "ORDER_REQUEST",
        "user_id": "test_user_2",
        "symbol": "ETH/USDT",
        "amount": -1.0,
        "order_id": "test_exec_2",
        "timestamp": 1234567890001,
    }

    subscribers = await event_bus.publish(ORDER_EVENTS_CHANNEL, invalid_amount_order)
    print(f"   Published to {subscribers} subscribers")
    await asyncio.sleep(2)

    # Test 3: Missing fields
    print("\nTest 3: Missing required fields")
    missing_fields_order = {
        "type": "ORDER_REQUEST",
        "user_id": "test_user_3",
        "symbol": "SOL/USDT",
        # amount missing
        "order_id": "test_exec_3",
        "timestamp": 1234567890002,
    }

    subscribers = await event_bus.publish(ORDER_EVENTS_CHANNEL, missing_fields_order)
    print(f"   Published to {subscribers} subscribers")
    await asyncio.sleep(2)

    # Test 4: Invalid symbol format
    print("\nTest 4: Invalid symbol format")
    invalid_symbol_order = {
        "type": "ORDER_REQUEST",
        "user_id": "test_user_4",
        "symbol": "BTCUSDT",  # Missing slash
        "amount": 0.5,
        "order_id": "test_exec_4",
        "timestamp": 1234567890003,
    }

    subscribers = await event_bus.publish(ORDER_EVENTS_CHANNEL, invalid_symbol_order)
    print(f"   Published to {subscribers} subscribers")
    await asyncio.sleep(2)

    # Cleanup
    await event_bus.close()
    await redis_manager.close()

    print("\n" + "=" * 60)
    print("✅ Execution worker tests completed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_execution_worker())
