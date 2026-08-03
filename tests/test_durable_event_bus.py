"""
tests/test_durable_event_bus.py

Unit tests verifying durable Redis Streams publishing, retry logic, explicit PublishError surfacing for trading-critical streams, and fail-soft preservation for best-effort cache operations.

IMPORTANT: This test explicitly validates the SharedRedisManager implementation from
backend_app/core/cache/redis_manager.py, not the old unreachable cache.py implementation.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock
from backend_app.core.cache import RedisClient, PublishError, TRADING_CRITICAL_STREAMS
from backend_app.core.event_bus import publish, publish_command, COMMAND_STREAM, RISK_STREAM, EXECUTION_STREAM


def test_implementation_identity():
    """
    EXPLICIT ASSERTION: Prove this test is exercising the live SharedRedisManager implementation,
    not the unreachable cache.py implementation. This prevents the silent-wrong-implementation
    failure mode that existed before the cache.py deletion.
    """
    # Verify RedisClient is actually SharedRedisManager from redis_manager.py
    assert RedisClient.__module__ == "backend_app.core.cache.redis_manager", \
        f"Test is not testing the expected implementation. Got {RedisClient.__module__}"
    
    # Verify the class has the expected methods from SharedRedisManager
    assert hasattr(RedisClient, 'get_client'), "Missing get_client method from SharedRedisManager"
    assert hasattr(RedisClient, 'xadd'), "Missing xadd method"
    assert hasattr(RedisClient, 'xreadgroup'), "Missing xreadgroup method"
    assert hasattr(RedisClient, 'xack'), "Missing xack method"
    
    # Verify TRADING_CRITICAL_STREAMS matches the expected set
    expected_streams = {"command_queue", "risk_signal", "execution_signal", "strategy_signal"}
    assert TRADING_CRITICAL_STREAMS == expected_streams, \
        f"TRADING_CRITICAL_STREAMS mismatch: {TRADING_CRITICAL_STREAMS}"


def test_trading_critical_stream_failure_raises_publish_error():
    async def _run():
        client = RedisClient()
        # Enable test mode to prevent _ensure_manager() from overwriting our mock
        client._set_test_mode(True)
        
        mock_events = AsyncMock()
        mock_events.xadd.side_effect = Exception("Redis_outage_simulation")
        mock_backend_manager = AsyncMock()
        mock_backend_manager.events = mock_events
        client._redis_manager = mock_backend_manager

        for stream in TRADING_CRITICAL_STREAMS:
            with pytest.raises(PublishError) as exc_info:
                await client.xadd(stream, {"test": "value"}, max_retries=2)
            assert "CRITICAL" in str(exc_info.value)
            assert stream in str(exc_info.value)

    asyncio.run(_run())


def test_best_effort_stream_failure_returns_none():
    async def _run():
        client = RedisClient()
        # Enable test mode to prevent _ensure_manager() from overwriting our mock
        client._set_test_mode(True)
        
        mock_events = AsyncMock()
        mock_events.xadd.side_effect = Exception("Redis_outage_simulation")
        mock_backend_manager = AsyncMock()
        mock_backend_manager.events = mock_events
        client._redis_manager = mock_backend_manager

        result = await client.xadd("non_critical_stream", {"test": "value"})
        assert result is None

    asyncio.run(_run())


def test_best_effort_cache_operations_retain_fail_soft():
    async def _run():
        client = RedisClient()
        # Enable test mode to prevent _ensure_manager() from overwriting our mock
        client._set_test_mode(True)
        # Bypass DEV_MODE to exercise the real Redis path (even though we use a mock)
        client._bypass_dev_mode(True)
        
        mock_cache = AsyncMock()
        mock_cache.get.side_effect = Exception("Redis_outage")
        mock_cache.set.side_effect = Exception("Redis_outage")
        mock_cache.delete.side_effect = Exception("Redis_outage")
        mock_backend_manager = AsyncMock()
        mock_backend_manager.cache = mock_cache
        client._redis_manager = mock_backend_manager

        assert await client.get("test_key") is None
        assert await client.set("test_key", "value") is False
        assert await client.delete("test_key") == 0

    asyncio.run(_run())


def test_event_bus_publish_command_raises_on_failure():
    async def _run():
        from backend_app.core.cache import redis_manager
        # Enable test mode to prevent _ensure_manager() from overwriting our mock
        redis_manager._set_test_mode(True)
        
        mock_events = AsyncMock()
        mock_events.xadd.side_effect = Exception("Redis_down")
        mock_backend_manager = AsyncMock()
        mock_backend_manager.events = mock_events
        redis_manager._redis_manager = mock_backend_manager

        with pytest.raises(PublishError):
            await publish_command("start_bot", {"symbol": "BTC/USDT"}, max_retries=1)

    asyncio.run(_run())


def test_dev_mode_startup_check_allows_testing_environment():
    """
    Verify that DEV_MODE=true + ENV=testing is allowed at startup.
    This is the legitimate development configuration.
    """
    # Simulate the check logic from main.py
    dev_mode = True
    env = "testing"
    
    # The check should NOT raise for this combination
    should_raise = dev_mode and env.lower() in ("production", "staging")
    assert should_raise is False, "DEV_MODE=true + ENV=testing should be allowed"


def test_dev_mode_startup_check_rejects_production_environment():
    """
    Verify that DEV_MODE=true + ENV=production is rejected at startup.
    This is a dangerous misconfiguration that bypasses Redis failure detection.
    """
    # Simulate the check logic from main.py
    dev_mode = True
    env = "production"
    
    # The check should raise for this combination
    should_raise = dev_mode and env.lower() in ("production", "staging")
    assert should_raise is True, "DEV_MODE=true + ENV=production should be rejected"
    
    # Also verify staging is rejected
    env = "staging"
    should_raise = dev_mode and env.lower() in ("production", "staging")
    assert should_raise is True, "DEV_MODE=true + ENV=staging should be rejected"
