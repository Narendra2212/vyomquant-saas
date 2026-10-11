"""Pytest plugin: satisfy the real-Redis gate with a controlled mock, then force the
application cache manager onto its fallback branch.

PHASE 11B-A5, verification case 3. Proves the disposition change in
`tests/test_webhook_duplicate_delivery_real_redis.py::real_redis_cache` -- that a manager
fallback FAILS rather than skips -- without a Redis server and without touching the
repository's own test files.

HOW, AND WHY IT HAS TO BE DONE IN `pytest_configure`
----------------------------------------------------
`real_redis_gate.real_redis_required` is built as
`pytest.mark.skipif(GATE_STATE == "skip", ...)`, and `GATE_STATE` is computed once at
module import. Patching `GATE_STATE` afterwards therefore cannot un-skip the marker -- the
condition was already evaluated. So the gate must classify as "run" AT IMPORT, which means
`AERORA_TEST_REDIS_URL` must be set and the PING must succeed before collection.

`pytest_configure` runs before collection, so both are arranged here:

  * the opt-in is set to a loopback URL (so the non-loopback refusal does not fire);
  * `redis.Redis.ping` is replaced with one that returns True, which is the controlled
    mock standing in for a reachable server. No socket is opened and no server is started.

Then `_get_active_cache` on the shared manager singleton is replaced so it returns the
fallback the real branch would return when the manager cannot produce a cache:

  P11B5_FALLBACK=mock  -> MockRedisClient   (what ENV=test produces in the real code)
  P11B5_FALLBACK=none  -> None              (the fail-closed branch)

EXPECTED RESULT: the gate passes, the autouse `_real_redis_is_usable` passes, and
`real_redis_cache` FAILS. Before the A5 change it SKIPPED, which is the defect.

Contacts nothing. Starts nothing. Only this pytest process is affected.
"""
import os


def pytest_configure(config):
    mode = os.environ.get("P11B5_FALLBACK", "mock").strip().lower()

    # 1. Make the shared gate classify as "run" at import, with no server involved.
    os.environ["AERORA_TEST_REDIS_URL"] = "redis://127.0.0.1:6399/15"
    os.environ.pop("AERORA_TEST_REDIS_ALLOW_REMOTE", None)

    import redis as _redis

    _redis.Redis.ping = lambda self: True          # controlled mock for reachability
    print("\n[p11b5] patched redis.Redis.ping -> True (gate will classify 'run')")

    # 2. Force the application manager onto the branch under test.
    from backend_app.core.cache import redis_manager
    from backend_app.core.cache.redis_manager import MockRedisClient

    if mode == "none":
        async def _fallback(*_a, **_k):
            return None
        label = "None"
    else:
        _mock = MockRedisClient()

        async def _fallback(*_a, **_k):
            return _mock
        label = "MockRedisClient"

    redis_manager._get_active_cache = _fallback
    print(f"[p11b5] patched redis_manager._get_active_cache -> {label}")
    print("[p11b5] EXPECT: gate passes, real_redis_cache FAILS (must not skip)")
