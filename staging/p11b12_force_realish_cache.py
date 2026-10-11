"""Pytest plugin: satisfy the gate, then hand the manager a NON-mock stub cache.

Purpose. `staging/p11b5_force_gate.py` forces `_get_active_cache()` onto
`MockRedisClient` / `None`, which trips the A5 guard in `real_redis_cache` before the new
Phase 11B-A12 keyspace probe can run. To prove the FIXTURE WIRING actually reaches
`_prove_one_keyspace` -- as opposed to proving the probe function works when called
directly, which `staging/p11b12_probe_harness.py` already does -- the manager has to return
something that is neither `MockRedisClient` nor `None`.

So `_get_active_cache` is replaced with one returning a tiny in-process stub. The A5 guard
then passes (correctly: it is not a mock and not None), `redis_client` derives the
application's cache URL, and the synchronous client tries to reach it. There is no server,
so the probe's fail-closed branch must fire.

EXPECTED RESULT: the five cache-dependent tests ERROR with the probe's own message about
being unable to use the application's effective cache URL -- and NOT skip. That is the
evidence that the probe is on the path every one of those tests takes.

Contacts nothing. Starts nothing. Opens no socket beyond the one redis-py attempts to the
loopback address, which is refused.
"""
from __future__ import annotations

import os


def pytest_configure(config):  # noqa: D103
    os.environ.setdefault("AERORA_TEST_REDIS_URL", "redis://127.0.0.1:6379/15")

    import redis

    redis.Redis.ping = lambda self, *a, **k: True  # type: ignore[assignment]

    class _StubCache:
        """Not MockRedisClient, not None. Enough surface for the probe to call."""

        def __init__(self):
            self._store: dict = {}

        async def get(self, key):
            return self._store.get(key)

        async def set(self, key, value, **kwargs):
            self._store[key] = value
            return True

        async def delete(self, *keys):
            for k in keys:
                self._store.pop(k, None)
            return len(keys)

    stub = _StubCache()

    from backend_app.core.cache import redis_manager

    async def _stub_active_cache(self=None):
        return stub

    type(redis_manager)._get_active_cache = _stub_active_cache  # type: ignore[assignment]
