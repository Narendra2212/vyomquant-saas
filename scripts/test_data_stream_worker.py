"""Test script for data stream worker."""

import asyncio
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from backend.services.event_bus import event_bus, MARKET_EVENTS_CHANNEL
from backend.services.cache import redis_manager


async def test_market_callback(event):
    """Callback to capture market data events."""
    print(f"📊 Market Event Received: {event.get('type')} - {event.get('symbol')}")
    if event.get("type") == "price_update":
        print(f"   Price: {event.get('price')}")
        print(f"   Volume: {event.get('volume')}")
        print(f"   Bid: {event.get('bid')}")
        print(f"   Ask: {event.get('ask')}")
    elif event.get("type") == "candle_update":
        print(
            f"   OHLC: {event.get('open')} / {event.get('high')} / {event.get('low')} / {event.get('close')}"
        )
        print(f"   Volume: {event.get('volume')}")


async def test_data_stream_worker():
    """Test the data stream worker."""
    print("=" * 60)
    print("Testing Data Stream Worker")
    print("=" * 60)

    # Connect to Redis
    await redis_manager.connect()

    # Subscribe to market events
    await event_bus.subscribe(MARKET_EVENTS_CHANNEL, test_market_callback)
    # Subscribe to events (listener starts automatically)
    await event_bus.subscribe(
        MARKET_EVENTS_CHANNEL, lambda e: print(f"Market event: {e}")
    )

    await asyncio.sleep(0.5)

    # Import data stream worker after Redis is connected
    from backend.services.workers.data_stream_worker import data_stream_worker

    # Start data stream worker
    await data_stream_worker.start()

    # Test 1: Subscribe to single symbol
    print("\nTest 1: Subscribe to BTC/USDT")
    success = await data_stream_worker.subscribe_symbol("BTC/USDT")
    print(f"   Subscription result: {success}")

    # Wait for some ticks
    print("   Waiting for ticks...")
    await asyncio.sleep(5)

    # Test 2: Subscribe to multiple symbols
    print("\nTest 2: Subscribe to multiple symbols")
    results = await data_stream_worker.subscribe_multiple(["ETH/USDT", "SOL/USDT"])
    for symbol, success in results.items():
        print(f"   {symbol}: {success}")

    # Wait for more ticks
    print("   Waiting for ticks...")
    await asyncio.sleep(5)

    # Test 3: Get subscribed symbols
    print("\nTest 3: Get subscribed symbols")
    symbols = await data_stream_worker.get_subscribed_symbols()
    print(f"   Subscribed symbols: {symbols}")

    # Test 4: Unsubscribe from a symbol
    print("\nTest 4: Unsubscribe from SOL/USDT")
    await data_stream_worker.unsubscribe_symbol("SOL/USDT")
    symbols = await data_stream_worker.get_subscribed_symbols()
    print(f"   Subscribed symbols after unsubscribe: {symbols}")

    # Wait to verify no more ticks from SOL
    print("   Waiting to verify...")
    await asyncio.sleep(3)

    # Cleanup
    await data_stream_worker.stop()
    await event_bus.close()
    await redis_manager.close()

    print("\n" + "=" * 60)
    print("✅ Data stream worker tests completed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_data_stream_worker())
