"""
tests/test_redis_debounce.py — Redis Thundering-Herd Debounced Reconnection Tests

Tests the debouncing added to RedisManager (backend_app/backend/redis_manager.py),
the authoritative reconnection point for the entire platform.
"""

import os
import sys
import time
from unittest.mock import AsyncMock, patch

import anyio
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _make_fresh_manager():
    """Return a fresh, non-singleton RedisManager with a clean slate."""
    from backend_app.backend.redis_manager import RedisManager

    mgr = object.__new__(RedisManager)
    mgr._cache = None
    mgr._queue = None
    mgr._events = None
    mgr._connected = False
    mgr._last_failed_at = 0.0
    mgr._reconnect_lock = None
    mgr._initialized = True
    return mgr


@pytest.mark.anyio
async def test_thundering_herd_one_attempt():
    """50 concurrent callers during an outage must trigger exactly 1 initialization call."""
    mgr = _make_fresh_manager()

    init_count = 0

    async def failing_initialize():
        nonlocal init_count
        init_count += 1
        mgr._connected = False
        mgr._last_failed_at = time.monotonic()
        return False  # simulate Redis down

    results: list = []

    async def caller():
        results.append(await mgr.try_reconnect())

    # anyio.create_task_group() is the backend-agnostic way to run the 50 callers
    # concurrently. asyncio.gather() would bind this test to the asyncio backend,
    # but @pytest.mark.anyio parametrises over every installed backend.
    with patch.object(mgr, "initialize", side_effect=failing_initialize):
        async with anyio.create_task_group() as tg:
            for _ in range(50):
                tg.start_soon(caller)

    assert len(results) == 50, f"Expected all 50 callers to complete, got {len(results)}"
    assert all(r is False for r in results)
    assert init_count <= 2, f"Expected at most 2 attempts (1 winner + 1 cooldown bypass), got {init_count}"


@pytest.mark.anyio
async def test_reconnect_cooldown_respected():
    """After a failure, subsequent calls within the cooldown window are skipped."""
    mgr = _make_fresh_manager()

    init_count = 0

    async def failing_initialize():
        nonlocal init_count
        init_count += 1
        mgr._connected = False
        mgr._last_failed_at = time.monotonic()
        return False

    with patch.object(mgr, "initialize", side_effect=failing_initialize):
        await mgr.try_reconnect()   # attempt 1
        assert init_count == 1

        # Immediately try again — within cooldown
        await mgr.try_reconnect()
        assert init_count == 1  # blocked


@pytest.mark.anyio
async def test_reconnect_allowed_after_cooldown():
    """After cooldown expires, a fresh attempt is allowed."""
    mgr = _make_fresh_manager()
    mgr._last_failed_at = time.monotonic() - 100  # expired cooldown

    async def failing_initialize():
        mgr._connected = False
        mgr._last_failed_at = time.monotonic()
        return False

    with patch.object(mgr, "initialize", side_effect=failing_initialize):
        result = await mgr.try_reconnect()
        assert result is False  # failed but attempted


@pytest.mark.anyio
async def test_recovery_after_outage():
    """After Redis recovers, try_reconnect returns True and self._connected becomes True."""
    mgr = _make_fresh_manager()
    mgr._last_failed_at = time.monotonic() - 100  # expired

    mock_client = AsyncMock()

    async def succeeding_initialize():
        mgr._cache = mock_client
        mgr._queue = mock_client
        mgr._events = mock_client
        mgr._connected = True
        return True

    with patch.object(mgr, "initialize", side_effect=succeeding_initialize):
        result = await mgr.try_reconnect()
        assert result is True
        assert mgr._connected is True
        assert mgr.cache is mock_client


@pytest.mark.anyio
async def test_healthy_fast_path_skips_lock():
    """When already connected, try_reconnect returns immediately without touching the lock."""
    mgr = _make_fresh_manager()
    mock_client = AsyncMock()
    mgr._cache = mock_client
    mgr._connected = True

    init_count = 0

    async def should_not_be_called():
        nonlocal init_count
        init_count += 1

    with patch.object(mgr, "initialize", side_effect=should_not_be_called):
        result = await mgr.try_reconnect()
        assert result is True
        assert init_count == 0  # lock & initialize never touched


@pytest.mark.anyio
async def test_get_redis_manager_debounced():
    """get_redis_manager() should call initialize at most once across N concurrent callers."""
    import backend_app.backend.redis_manager as rm_module

    # Reset global state for this test
    original_manager = rm_module._redis_manager
    original_failed = rm_module._get_manager_last_failed
    original_lock = rm_module._get_manager_lock
    rm_module._redis_manager = None
    rm_module._get_manager_last_failed = 0.0
    rm_module._get_manager_lock = None

    init_count = 0

    async def failing_initialize(self):
        nonlocal init_count
        init_count += 1
        self._connected = False
        self._last_failed_at = time.monotonic()
        return False

    completed = 0

    async def caller():
        nonlocal completed
        await rm_module.get_redis_manager()
        completed += 1

    try:
        # Backend-agnostic concurrency: see note in test_thundering_herd_one_attempt.
        with patch.object(rm_module.RedisManager, "initialize", side_effect=failing_initialize, autospec=True):
            async with anyio.create_task_group() as tg:
                for _ in range(30):
                    tg.start_soon(caller)

        assert completed == 30, f"Expected all 30 callers to complete, got {completed}"
        assert init_count <= 2, f"Expected at most 2 init attempts, got {init_count}"
    finally:
        rm_module._redis_manager = original_manager
        rm_module._get_manager_last_failed = original_failed
        rm_module._get_manager_lock = original_lock
