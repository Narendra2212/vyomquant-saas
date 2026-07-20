"""Test script for Redis Pub/Sub event bus functionality."""

import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from backend.services.event_bus import (
    event_bus,
    ORDER_EVENTS_CHANNEL,
    MARKET_EVENTS_CHANNEL,
)
from backend.services.cache import redis_manager


async def test_order_callback(event):
    """Callback for order events."""
    print(f"📦 Order Event Received: {event}")


async def test_market_callback(event):
    """Callback for market events."""
    print(f"📊 Market Event Received: {event}")


async def test_publish_subscribe():
    """Test basic publish/subscribe functionality."""
    print("=" * 60)
    print("Testing Redis Pub/Sub Event Bus")
    print("=" * 60)

    # Connect to Redis
    print("\n1. Connecting to Redis...")
    await redis_manager.connect()
    if redis_manager.pool:
        print("✅ Redis connected successfully")
    else:
        print("❌ Failed to connect to Redis")
        return

    # Subscribe to channels
    print("\n2. Subscribing to channels...")
    await event_bus.subscribe(ORDER_EVENTS_CHANNEL, test_order_callback)
    await event_bus.subscribe(MARKET_EVENTS_CHANNEL, test_market_callback)
    print("✅ Subscribed to order_events and market_events")

    # Start the listener
    print("\n3. Starting event bus listener...")
    # Subscribe to events (listener starts automatically)
    await event_bus.subscribe(
        ORDER_EVENTS_CHANNEL, lambda e: print(f"Order event: {e}")
    )
    await event_bus.subscribe(
        MARKET_EVENTS_CHANNEL, lambda e: print(f"Market event: {e}")
    )
    print("✅ Event bus listener started")

    # Wait a moment for listener to be ready
    await asyncio.sleep(1)

    # Publish test events
    print("\n4. Publishing test events...")

    order_event = {
        "type": "order_filled",
        "order_id": "12345",
        "symbol": "BTC/USDT",
        "price": 50000.0,
        "quantity": 0.1,
        "timestamp": "2026-04-25T01:50:00Z",
    }

    market_event = {
        "type": "price_update",
        "symbol": "BTC/USDT",
        "price": 50001.0,
        "change_24h": 2.5,
        "timestamp": "2026-04-25T01:50:01Z",
    }

    subscribers = await event_bus.publish(ORDER_EVENTS_CHANNEL, order_event)
    print(f"✅ Published order event to {subscribers} subscribers")

    await asyncio.sleep(0.5)

    subscribers = await event_bus.publish(MARKET_EVENTS_CHANNEL, market_event)
    print(f"✅ Published market event to {subscribers} subscribers")

    # Wait for callbacks to process
    print("\n5. Waiting for callbacks to process...")
    await asyncio.sleep(2)

    # Cleanup
    print("\n6. Cleaning up...")
    await event_bus.close()
    await redis_manager.close()
    print("✅ Event bus and Redis connections closed")

    print("\n" + "=" * 60)
    print("Test completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_publish_subscribe())
