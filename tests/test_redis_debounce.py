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


# ══════════════════════════════════════════════════════════════════════════════
#  R6 — THE RUNTIME-FAILURE RECOVERY CONTRACT
#
#  WHY THESE EXIST. Phase 11B-A19 observed four webhook tests ERROR at setup with
#
#      Cache SET failed for '…': Event loop is closed
#
#  and Phase 11B-A20 traced it to a gap in this module's recovery contract rather
#  than to the keyspace or the test fixtures:
#
#      initialize()           sets _connected True (L171) / False (L180)
#      try_reconnect()        `if self._connected: return True`            (L192)
#      get_redis_manager()    `if _redis_manager … and _redis_manager._connected:
#                                  return _redis_manager`                  (L548)
#
#  `_connected` is therefore written in exactly ONE place — initialize() — and a
#  command that fails at RUNTIME never touches it. The core cache proxy
#  (`backend_app/core/cache/redis_manager.py`) catches the exception, logs a
#  warning and returns a neutral value, so the flag stays True, the fast path
#  above keeps handing back the same manager, and the documented reconnection
#  machinery is never reached. One failure disables Redis for the rest of the
#  process.
#
#  In production, with a single long-lived event loop, the closed-loop trigger
#  does not occur — but the stickiness is not test-specific. A server restart, a
#  failover or a network blip surfaces the same way: as an exception inside a
#  proxy, leaving _connected True.
#
#  WHAT THESE TESTS ARE, AND ARE NOT. Three of the four are CHARACTERIZATION:
#  they pin today's behaviour and pass today. They are not assertions that
#  today's behaviour is correct. The fourth states the DESIRED contract and is
#  `xfail(strict=True)`, so it fails now for the documented reason and turns into
#  a reported XPASS the moment a fix lands — which is the signal the owner of
#  this module wants, without adding a permanent red test to the blocking lane.
#
#  SERVER-FREE BY CONSTRUCTION. No Redis, no database, no network, no
#  credentials. `_make_fresh_manager()` (above) builds a NON-SINGLETON manager
#  through `object.__new__`, so the process-wide `RedisManager._instance` is never
#  involved and nothing leaks between tests. The three module globals are saved
#  and restored exactly as `test_get_redis_manager_debounced` does.
#
#  NO PRODUCTION CODE AND NO FIXTURE SEMANTICS ARE CHANGED BY THIS BLOCK.
# ══════════════════════════════════════════════════════════════════════════════

#: The exception observed in CI run 38061973739, reproduced verbatim so the fault
#: injected here is the fault that actually occurs rather than a convenient stand-in.
_OBSERVED_RUNTIME_ERROR = "Event loop is closed"


class _RecordingManagerGlobals:
    """Save/restore `backend/redis_manager`'s three module globals.

    `get_redis_manager()` reads all three. Isolating them is what lets these tests
    exercise the real public entry point without touching `tests/conftest.py` or
    any autouse fixture, and without leaving state behind for the next test.
    """

    def __enter__(self):
        import backend_app.backend.redis_manager as rm

        self._rm = rm
        self._saved = (rm._redis_manager, rm._get_manager_last_failed, rm._get_manager_lock)
        return rm

    def __exit__(self, *exc):
        self._rm._redis_manager, self._rm._get_manager_last_failed, \
            self._rm._get_manager_lock = self._saved
        return False


def _failing_cache(message: str = _OBSERVED_RUNTIME_ERROR):
    """A cache client whose every command raises, the way a dead pool does."""
    client = AsyncMock()
    client.set.side_effect = RuntimeError(message)
    client.get.side_effect = RuntimeError(message)
    client.ping.side_effect = RuntimeError(message)
    return client


def _connected_manager_with(cache):
    """A manager that believes it is connected and holds `cache`."""
    mgr = _make_fresh_manager()
    mgr._cache = cache
    mgr._queue = cache
    mgr._events = cache
    mgr._connected = True
    return mgr


@pytest.mark.anyio
async def test_r6_a_runtime_command_failure_is_real_and_leaves_health_unchanged():
    """The injected fault fires, and nothing in the manager notices.

    This guards the OTHER tests: if the double stopped raising, they would pass
    for the wrong reason. Asserting the raise here is what makes "no rebuild was
    attempted" meaningful rather than vacuous.
    """
    cache = _failing_cache()
    mgr = _connected_manager_with(cache)

    with pytest.raises(RuntimeError, match=_OBSERVED_RUNTIME_ERROR):
        await mgr.cache.set("r6:probe", "1")

    assert cache.set.await_count == 1, "the fault was never actually exercised"
    # Supporting detail, not the point of the test: the command failed and the
    # manager's own health record did not move. `initialize()` is the only writer.
    assert mgr._connected is True


@pytest.mark.anyio
async def test_r6_b_get_redis_manager_returns_the_stale_manager_without_attempting_a_rebuild():
    """CHARACTERIZATION of the defect: the fast path hands back the broken manager.

    Observable assertions, not a private flag:
      * `initialize` is awaited ZERO times — no rebuild is even attempted;
      * the returned object IS the stale manager (identity);
      * the cache it exposes is still the failing double, so the next caller
        inherits the same broken client.
    """
    cache = _failing_cache()
    mgr = _connected_manager_with(cache)

    with _RecordingManagerGlobals() as rm:
        rm._redis_manager = mgr
        rm._get_manager_last_failed = 0.0
        rm._get_manager_lock = None

        with pytest.raises(RuntimeError, match=_OBSERVED_RUNTIME_ERROR):
            await mgr.cache.set("r6:probe", "1")

        rebuild = AsyncMock(return_value=True)
        with patch.object(mgr, "initialize", rebuild):
            returned = await rm.get_redis_manager()

        assert rebuild.await_count == 0, (
            "get_redis_manager() attempted a rebuild; the recorded behaviour is that it "
            "does not, because the `_connected` fast path short-circuits first"
        )
        assert returned is mgr, "a different manager was returned; the fast path was not taken"
        assert returned.cache is cache, (
            "the caller was handed a manager whose cache is the failing client"
        )

        with pytest.raises(RuntimeError, match=_OBSERVED_RUNTIME_ERROR):
            await returned.cache.set("r6:probe", "2")


@pytest.mark.anyio
async def test_r6_c_the_same_harness_detects_a_genuine_rebuild_attempt():
    """CONTROL: the harness can tell "rebuilt" from "stale fast path".

    Without this, `await_count == 0` in the test above would be unfalsifiable — it
    could mean the patch never took effect. Here the ONLY difference is that the
    manager does not claim to be connected, and the same harness observes
    `initialize` awaited exactly once and a healthy cache coming back. So a stale
    fast path can never be mistaken for a successful rebuild.
    """
    broken = _failing_cache()
    healthy = AsyncMock()
    mgr = _connected_manager_with(broken)
    mgr._connected = False                      # the one and only difference
    mgr._last_failed_at = time.monotonic() - 100  # cooldown already expired

    with _RecordingManagerGlobals() as rm:
        rm._redis_manager = mgr
        rm._get_manager_last_failed = time.monotonic() - 100
        rm._get_manager_lock = None

        async def rebuild():
            mgr._cache = healthy
            mgr._queue = healthy
            mgr._events = healthy
            mgr._connected = True
            return True

        attempts = AsyncMock(side_effect=rebuild)
        with patch.object(mgr, "initialize", attempts):
            returned = await rm.get_redis_manager()

        assert attempts.await_count == 1, (
            "a rebuild was expected exactly once; got %d" % attempts.await_count
        )
        assert returned is mgr
        assert returned.cache is healthy, "the rebuilt manager still exposes the broken cache"
        await returned.cache.set("r6:probe", "3")   # does not raise


@pytest.mark.anyio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "DESIRED CONTRACT, not yet implemented. A runtime command failure should make the "
        "manager recoverable: `_connected` is written only by initialize(), so a failed "
        "command leaves it True and get_redis_manager()'s fast path returns the broken "
        "manager for the rest of the process. Phase 11B-A20 §3e options A/B. When a fix "
        "lands this becomes an XPASS, which pytest reports as a failure under strict=True "
        "-- the signal to update this test and delete this marker."
    ),
)
async def test_r6_d_desired_a_runtime_failure_should_be_recoverable():
    """The contract the owner is being asked to approve or reject.

    Identical arrangement to r6_b. The only difference is the expectation: after a
    command fails at runtime, the manager obtained from the public entry point
    should be usable — whether by invalidating health so the existing
    `try_reconnect` path can run (option A), or by noticing the pool belongs to a
    dead loop and rebuilding (option B).
    """
    cache = _failing_cache()
    mgr = _connected_manager_with(cache)

    with _RecordingManagerGlobals() as rm:
        rm._redis_manager = mgr
        rm._get_manager_last_failed = 0.0
        rm._get_manager_lock = None

        with pytest.raises(RuntimeError, match=_OBSERVED_RUNTIME_ERROR):
            await mgr.cache.set("r6:probe", "1")

        returned = await rm.get_redis_manager()

        # Fails today: the same failing client comes straight back.
        await returned.cache.set("r6:probe", "2")
