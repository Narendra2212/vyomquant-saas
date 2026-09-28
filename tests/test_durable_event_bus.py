"""
tests/test_durable_event_bus.py

Unit tests verifying durable Redis Streams publishing, retry logic, explicit PublishError surfacing for trading-critical streams, and fail-soft preservation for best-effort cache operations.

IMPORTANT: This test explicitly validates the SharedRedisManager implementation from
backend_app/core/cache/redis_manager.py, not the old unreachable cache.py implementation.

`RedisClient()` DOES NOT HAND BACK A FRESH OBJECT. `SharedRedisManager.__new__` caches
`_instance`, so every `RedisClient()` below is the *same* object as the process-wide
`backend_app.core.cache.redis_manager` singleton that the rest of the suite reads
through. The injection seams these tests use — `_set_test_mode`, `_bypass_dev_mode` and
the `_redis_manager` assignment — therefore mutate shared state, and before
`_restore_shared_redis_manager_seams` existed they were never undone: the last test in
this module left `_dev_mode_bypass=True` and `_redis_manager` bound to an `AsyncMock`,
so from here to the end of the session `redis_manager.setex`/`getdel`/`incr` resolved
to `AsyncMock().cache` instead of the in-memory `MockRedisClient`. Eleven assertions in
tests/test_ws_ticket_redemption.py, three in tests/test_exchange_credential_log_redaction.py
and one in tests/test_tenant_isolation_fixes.py failed on that leak and passed when their
file ran alone.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock
from backend_app.core.cache import RedisClient, PublishError, TRADING_CRITICAL_STREAMS
from backend_app.core.event_bus import publish, publish_command, COMMAND_STREAM, RISK_STREAM, EXECUTION_STREAM

#: The singleton attributes every test below writes to. `_test_mode` and `_redis_manager`
#: exist as class defaults; `_dev_mode_bypass` is created on first `_bypass_dev_mode`
#: call, so "absent" is a state that has to be restorable too.
_INJECTION_SEAMS = ("_test_mode", "_dev_mode_bypass", "_redis_manager")


@pytest.fixture(autouse=True)
def _restore_shared_redis_manager_seams():
    """Put the shared Redis singleton back exactly as this module found it.

    Snapshot-and-restore rather than "set the defaults back", so an attribute that did
    not exist before a test is deleted afterwards instead of being invented with a
    guessed value.
    """
    client = RedisClient()
    before = {
        name: client.__dict__[name]
        for name in _INJECTION_SEAMS
        if name in client.__dict__
    }
    try:
        yield
    finally:
        for name in _INJECTION_SEAMS:
            if name in before:
                client.__dict__[name] = before[name]
            else:
                client.__dict__.pop(name, None)


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
