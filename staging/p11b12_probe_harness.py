"""Exercise `_prove_one_keyspace` (F3) against in-process doubles.

No Redis server, no infrastructure, no network. The point is to demonstrate that the
fixture-time assertion added in Phase 11B-A12 actually DETECTS the condition that produced
the CI failures in run 38044138718, and that it cleans up its sentinels in every case.

Run:  python staging/p11b12_probe_harness.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# The gate evaluates its PING at import; keep it on the legitimate opt-out so no network
# operation happens when this module imports the test file.
os.environ.pop("AERORA_TEST_REDIS_URL", None)
os.environ["REDIS_URL"] = ""

import redis as _redis  # noqa: E402
from _pytest.outcomes import Failed  # noqa: E402

import tests.test_webhook_duplicate_delivery_real_redis as mod  # noqa: E402

PREFIX = mod._PROBE_PREFIX


class SyncDouble:
    """The handful of synchronous commands the probe and the teardown use."""

    def __init__(self, store: dict, raises: Exception | None = None):
        self.store = store
        self.raises = raises

    def get(self, key):
        if self.raises:
            raise self.raises
        return self.store.get(key)

    def set(self, key, value, **kwargs):
        if self.raises:
            raise self.raises
        self.store[key] = value
        return True

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
        return len(keys)


class ManagerDouble:
    """The async proxies, including the fail-soft return values the real ones use."""

    def __init__(self, store: dict, read_store: dict | None = None, set_result=True):
        self.store = store
        self.read_store = read_store if read_store is not None else store
        self.set_result = set_result

    async def set(self, key, value, **kwargs):
        if not self.set_result:
            return False
        self.store[key] = value
        return True

    async def get(self, key):
        return self.read_store.get(key)

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
            self.read_store.pop(k, None)
        return len(keys)


def _leftover(*stores) -> list:
    return [k for s in stores for k in s if k.startswith(PREFIX)]


def run(name, manager, client, *, expect, expect_phrase=None):
    try:
        asyncio.run(mod._prove_one_keyspace(manager, client))
        outcome, detail = "passed", ""
    except Failed as exc:
        outcome, detail = "failed", str(exc)

    ok = outcome == expect
    phrase_ok = True
    if expect_phrase:
        phrase_ok = expect_phrase.lower() in detail.lower()

    stores = {id(s): s for s in (manager.store, manager.read_store, client.store)}
    left = _leftover(*stores.values())

    status = "OK  " if (ok and phrase_ok and not left) else "BAD "
    print(f"{status}{name}")
    print(f"      outcome={outcome} (expected {expect})")
    if expect_phrase:
        print(f"      message contains {expect_phrase!r}: {phrase_ok}")
    print(f"      sentinels left behind: {left if left else 'none'}")
    if detail:
        print(f"      first line: {detail.splitlines()[0][:150]}")
    print()
    return ok and phrase_ok and not left


def main() -> int:
    results = []

    # 1. Agreement: one shared keyspace. This is what the fix is supposed to produce.
    shared = {}
    results.append(run(
        "1. same keyspace -> probe accepts",
        ManagerDouble(shared), SyncDouble(shared), expect="passed",
    ))

    # 2. The CI condition: app on one database, test client on another.
    app_db, test_db = {}, {}
    results.append(run(
        "2. split keyspace (the CI defect) -> probe FAILS, direction 1",
        ManagerDouble(app_db), SyncDouble(test_db),
        expect="failed", expect_phrase="KEYSPACE SPLIT",
    ))

    # 3. Direction 1 agrees, direction 2 does not: the app writes where the test can read
    #    it, but reads from somewhere else. Must be caught by the reverse check.
    w, r = {}, {}
    results.append(run(
        "3. reverse-only split -> probe FAILS, direction 2",
        ManagerDouble(w, read_store=r), SyncDouble(w),
        expect="failed", expect_phrase="reverse direction",
    ))

    # 4. Fail-soft write: the manager's `set` returns False, as it does on any exception.
    #    Must be reported as a refused write, NOT as a keyspace mismatch.
    s4 = {}
    results.append(run(
        "4. manager refuses the write -> reported as a refusal, not a mismatch",
        ManagerDouble(s4, set_result=False), SyncDouble(s4),
        expect="failed", expect_phrase="REFUSED a write",
    ))

    # 5. The derived application endpoint is unusable. Must fail closed, never skip.
    s5 = {}
    results.append(run(
        "5. sync client cannot reach the app endpoint -> fails closed",
        ManagerDouble(s5),
        SyncDouble(s5, raises=_redis.exceptions.ConnectionError("refused")),
        expect="failed", expect_phrase="could not use the application's effective cache URL",
    ))

    print("=" * 78)
    print(f"{sum(results)}/{len(results)} scenarios behaved as specified")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
