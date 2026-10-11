"""PHASE 11B-A4 — what does the backend cache manager return when Redis is absent?

Measures the `_get_active_cache()` branch that `real_redis_cache` turns into a SKIP, so the
A4 recommendation rests on an observation rather than a reading of the code.

`_get_active_cache()` (backend_app/core/cache/redis_manager.py:676-745):

    (A) DEV_MODE and not _dev_mode_bypass          -> self._mock_client
    (B) _ensure_manager() yields .cache            -> REAL cache
    (C) no .cache and _mock_cache_is_acceptable()  -> self._mock_client
    (D) no .cache and not acceptable               -> None

`real_redis_cache` calls `_bypass_dev_mode(True)` to disable (A), then skips on
`cache is None or isinstance(cache, MockRedisClient)` -- i.e. on BOTH (C) and (D).

This probe drives (C)/(D) by pointing `REDIS_URL` at a loopback port where nothing listens.
READ-ONLY with respect to the repository: it imports application modules and mutates only
its own process environment. No file is written. No production endpoint is contacted -- the
target is 127.0.0.1:6399, deliberately dead.
"""
import asyncio
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEAD = "redis://127.0.0.1:6399/15"

# Mirror the suite's preamble (tests/conftest.py) before importing the manager, because
# DEV_MODE is a MODULE-LEVEL constant evaluated at import time.
os.environ["DEV_MODE"] = "true"
os.environ["ENV"] = "test"
os.environ["REDIS_URL"] = DEAD

print("=" * 78)
print("PHASE 11B-A4 - cache manager resolution with Redis absent (read-only)")
print("=" * 78)

from backend_app.core.cache import redis_manager  # noqa: E402
from backend_app.core.cache.redis_manager import (DEV_MODE, MockRedisClient,  # noqa: E402
                                                  _mock_cache_is_acceptable)

print(f"DEV_MODE (import-time constant)   = {DEV_MODE}")
print(f"_mock_cache_is_acceptable()       = {_mock_cache_is_acceptable()}")
print(f"REDIS_URL target                  = {DEAD}  (nothing listens)")


async def main():
    print("-" * 78)
    print("1. WITHOUT the dev-mode bypass (branch A) -- what the default suite sees:")
    cache = await redis_manager._get_active_cache()
    print(f"   _get_active_cache() -> {type(cache).__name__}")
    print(f"   is MockRedisClient  -> {isinstance(cache, MockRedisClient)}")

    print("-" * 78)
    print("2. WITH _bypass_dev_mode(True) -- exactly what real_redis_cache does:")
    redis_manager._bypass_dev_mode(True)
    cache = await redis_manager._get_active_cache()
    name = type(cache).__name__ if cache is not None else "None"
    print(f"   _get_active_cache() -> {name}")
    is_mock = isinstance(cache, MockRedisClient)
    print(f"   is MockRedisClient  -> {is_mock}")
    print(f"   is None             -> {cache is None}")

    would_skip = (cache is None) or is_mock
    print("-" * 78)
    print("WHAT real_redis_cache WOULD DO WITH THIS RESULT")
    print(f"   predicate `cache is None or isinstance(cache, MockRedisClient)` -> {would_skip}")
    if would_skip:
        print("   => pytest.skip(...)  <-- a SKIP, not a failure.")
        print("      In CI, AERORA_TEST_REDIS_URL is set and the service is health-checked,")
        print("      so reaching this branch means the gate PINGed successfully and the")
        print("      BACKEND MANAGER still could not use the server. That is a wiring")
        print("      fault, not an absence -- and a skip is how it would be reported.")
    else:
        print("   => the fixture would proceed (real cache).")

    return 0 if would_skip else 1


rc = asyncio.run(main())
print("=" * 78)
print("NOTE: this probe cannot measure branch (B) -- a genuinely reachable Redis -- because")
print("no Redis server is available here. That case remains UNVERIFIED pending a CI run.")
sys.exit(rc)
