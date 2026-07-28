"""
tests/test_durable_event_bus.py

Unit tests verifying durable Redis Streams publishing, retry logic, explicit PublishError surfacing for trading-critical streams, and fail-soft preservation for best-effort cache operations.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock
from backend_app.core.cache import RedisClient, PublishError, TRADING_CRITICAL_STREAMS
from backend_app.core.event_bus import publish, publish_command, COMMAND_STREAM, RISK_STREAM, EXECUTION_STREAM


def test_trading_critical_stream_failure_raises_publish_error():
    async def _run():
        client = RedisClient()
        mock_events = AsyncMock()
        mock_events.xadd.side_effect = Exception("Redis_outage_simulation")
        client._redis_manager = AsyncMock()
        client._redis_manager.events = mock_events

        for stream in TRADING_CRITICAL_STREAMS:
            with pytest.raises(PublishError) as exc_info:
                await client.xadd(stream, {"test": "value"}, max_retries=2)
            assert "CRITICAL" in str(exc_info.value)
            assert stream in str(exc_info.value)

    asyncio.run(_run())


def test_best_effort_stream_failure_returns_none():
    async def _run():
        client = RedisClient()
        mock_events = AsyncMock()
        mock_events.xadd.side_effect = Exception("Redis_outage_simulation")
        client._redis_manager = AsyncMock()
        client._redis_manager.events = mock_events

        result = await client.xadd("non_critical_stream", {"test": "value"})
        assert result is None

    asyncio.run(_run())


def test_best_effort_cache_operations_retain_fail_soft():
    async def _run():
        client = RedisClient()
        mock_cache = AsyncMock()
        mock_cache.get.side_effect = Exception("Redis_outage")
        mock_cache.set.side_effect = Exception("Redis_outage")
        mock_cache.delete.side_effect = Exception("Redis_outage")
        client._redis_manager = AsyncMock()
        client._redis_manager.cache = mock_cache

        assert await client.get("test_key") is None
        assert await client.set("test_key", "value") is False
        assert await client.delete("test_key") == 0

    asyncio.run(_run())


def test_event_bus_publish_command_raises_on_failure():
    async def _run():
        from backend_app.core.cache import redis_manager
        mock_events = AsyncMock()
        mock_events.xadd.side_effect = Exception("Redis_down")
        redis_manager._redis_manager = AsyncMock()
        redis_manager._redis_manager.events = mock_events

        with pytest.raises(PublishError):
            await publish_command("start_bot", {"symbol": "BTC/USDT"}, max_retries=1)

    asyncio.run(_run())
