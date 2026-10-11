"""A22 verification: pool-cleanup gaps in RedisManager.initialize/close, proven server-free.

PHASE 11B / A22 (verification pass) — read-only with respect to the repository. No Redis,
no network, no database. Nothing under `backend_app/` or `tests/` is modified. Writes
nothing but its own redirected stdout.

NAMING: `a22v_` because another session owns the A22 namespace
(`PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md`, written 21:39:06) and the R6 tests in
`tests/test_redis_debounce.py`. Nothing of theirs is written or overwritten.

WHY THIS EXISTS
---------------
The A22 brief requires inspecting "partial initialization failure and whether replaced
pools are closed safely". Reading `initialize()` suggests two leaks and `close()` a third
gap. Reading is not proof, and these bear directly on the design options -- an option that
rebuilds pools more often makes a pool leak worse, so the leak has to be established
before a rebuild strategy is recommended.

Each check replaces `aioredis.from_url` with a recorder that never touches a socket, so
the real `initialize()` and `close()` run against instrumented doubles.

GAPS UNDER TEST (all in backend_app/backend/redis_manager.py, which is TRACKED AND CLEAN)
  G1  partial-init leak: a ping failure on the 3rd client leaves all 3 created clients
      unclosed, and `initialize()` is retried on a cooldown, so each retry leaks 3 pools.
  G2  replace-without-close: a SUCCESSFUL re-initialize overwrites self._cache/_queue/
      _events without closing the previous three.
  G3  close() does not reset health: after `close()`, `_connected` stays True and the
      clients stay bound, so `get_redis_manager()`'s fast path still returns the manager
      and the compat proxy still hands out methods on closed clients.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import backend_app.backend.redis_manager as rm                      # noqa: E402


class FakeClient:
    """Records lifecycle calls. Never opens a socket."""

    def __init__(self, label: str, ping_fails: bool = False):
        self.label = label
        self.ping_fails = ping_fails
        self.ping_calls = 0
        self.close_calls = 0
        self.aclose_calls = 0

    async def ping(self):
        self.ping_calls += 1
        if self.ping_fails:
            raise ConnectionError(f"{self.label}: simulated unreachable server")
        return True

    async def close(self):
        self.close_calls += 1

    async def aclose(self):
        self.aclose_calls += 1

    def __repr__(self):
        return (f"<FakeClient {self.label} ping={self.ping_calls} "
                f"close={self.close_calls} aclose={self.aclose_calls}>")


class FakeAioredis:
    """Stands in for the `aioredis` module inside redis_manager."""

    def __init__(self, fail_on: set[int] | None = None):
        self.created: list[FakeClient] = []
        self.fail_on = fail_on or set()

    async def from_url(self, url, **kwargs):
        idx = len(self.created)
        client = FakeClient(f"client{idx}", ping_fails=idx in self.fail_on)
        self.created.append(client)
        return client


def _fresh_manager() -> rm.RedisManager:
    """A RedisManager with no singleton or cross-test contamination.

    `RedisManager.__new__` is a singleton, so constructing one normally can return an
    instance another check already mutated. This bypasses `__new__` deliberately and
    initialises the fields `__init__` sets, so each check starts from a known state and a
    failure cannot be blamed on leaked singleton state.
    """
    mgr = object.__new__(rm.RedisManager)
    mgr._cache = None
    mgr._queue = None
    mgr._events = None
    mgr._connected = False
    mgr._last_failed_at = 0.0
    mgr._reconnect_lock = None
    mgr._initialized = True
    return mgr


async def g1_partial_init_leak() -> bool:
    print("=" * 78)
    print("G1  PARTIAL INITIALIZATION -- are the created clients closed on failure?")
    print("=" * 78)
    fake = FakeAioredis(fail_on={2})          # third client's ping raises
    original = rm.aioredis
    rm.aioredis = fake
    try:
        mgr = _fresh_manager()
        ok = await mgr.initialize()
    finally:
        rm.aioredis = original

    print(f"   initialize() returned        : {ok}   (False expected)")
    print(f"   clients created              : {len(fake.created)}")
    for c in fake.created:
        print(f"     {c}")
    print(f"   _connected after failure     : {mgr._connected}   (False expected)")
    print(f"   self._cache bound            : {mgr._cache is not None}")
    closed = sum(c.close_calls + c.aclose_calls for c in fake.created)
    print(f"   TOTAL close/aclose calls     : {closed}")
    leaked = len(fake.created) - closed
    print(f"   LEAKED connection pools      : {leaked}")
    print()
    if leaked > 0:
        print(f"   GAP CONFIRMED. {len(fake.created)} clients were created, the ping on")
        print(f"   client2 raised, and the except block set _connected=False and returned")
        print(f"   without closing ANY of them. Because try_reconnect() retries once per")
        print(f"   {rm._RECONNECT_COOLDOWN}s cooldown, a persistent outage leaks "
              f"{len(fake.created)} pools per attempt.")
    else:
        print("   no leak observed")
    print()
    return leaked > 0


async def g2_replace_without_close() -> bool:
    print("=" * 78)
    print("G2  SUCCESSFUL RE-INITIALIZE -- are the PREVIOUS clients closed?")
    print("=" * 78)
    fake = FakeAioredis()
    original = rm.aioredis
    rm.aioredis = fake
    try:
        mgr = _fresh_manager()
        first_ok = await mgr.initialize()
        first_three = list(fake.created)
        first_cache = mgr._cache
        second_ok = await mgr.initialize()
        second_cache = mgr._cache
    finally:
        rm.aioredis = original

    print(f"   first initialize()           : {first_ok}")
    print(f"   second initialize()          : {second_ok}")
    print(f"   clients created in total     : {len(fake.created)}  (6 expected)")
    print(f"   self._cache replaced         : {first_cache is not second_cache}")
    closed_first = sum(c.close_calls + c.aclose_calls for c in first_three)
    print(f"   close/aclose on the FIRST 3  : {closed_first}")
    for c in first_three:
        print(f"     {c}")
    print()
    if closed_first == 0:
        print("   GAP CONFIRMED. The second initialize() overwrote self._cache/_queue/")
        print("   _events and the first three clients were never closed. Any design that")
        print("   rebuilds on failure will leak 3 pools per rebuild unless initialize()")
        print("   closes the outgoing clients first.")
    else:
        print("   previous clients were closed")
    print()
    return closed_first == 0


async def g3_close_does_not_reset_health() -> bool:
    print("=" * 78)
    print("G3  close() -- does it reset health and unbind the clients?")
    print("=" * 78)
    fake = FakeAioredis()
    original = rm.aioredis
    rm.aioredis = fake
    try:
        mgr = _fresh_manager()
        await mgr.initialize()
        connected_before = mgr._connected
        await mgr.close()
    finally:
        rm.aioredis = original

    print(f"   _connected before close()    : {connected_before}")
    print(f"   _connected AFTER close()     : {mgr._connected}   (False would be safe)")
    print(f"   self._cache still bound      : {mgr._cache is not None}")
    print(f"   close() used .close()        : "
          f"{sum(c.close_calls for c in fake.created)} call(s)")
    print(f"   close() used .aclose()       : "
          f"{sum(c.aclose_calls for c in fake.created)} call(s)")
    gap = mgr._connected and mgr._cache is not None
    print()
    if gap:
        print("   GAP CONFIRMED. After close(), the manager still reports _connected=True")
        print("   and still holds the (now closed) clients. So:")
        print("     * get_redis_manager()'s fast path at :548 returns it as healthy;")
        print("     * try_reconnect() at :192 returns True without reconnecting;")
        print("     * RedisManagerCompatProxy hands out methods on closed clients.")
        print("   This is the SAME never-reset-flag mechanism as R6, reached through an")
        print("   orderly shutdown rather than a runtime failure.")
    print()
    return gap


async def r6_contract_from_source() -> None:
    print("=" * 78)
    print("R6 CONTRACT, RE-CONFIRMED AGAINST CURRENT SOURCE")
    print("=" * 78)
    src = (REPO / "backend_app" / "backend" / "redis_manager.py").read_text(
        encoding="utf-8")
    lines = src.splitlines()
    writes = [(i + 1, l.strip()) for i, l in enumerate(lines)
              if "_connected" in l and "=" in l and "==" not in l
              and not l.strip().startswith("#")
              and ("self._connected" in l or "_connected:" in l)]
    print("   every write to _connected:")
    for n, text in writes:
        print(f"     {n:>5}: {text}")
    print()
    gates = [(i + 1, l.strip()) for i, l in enumerate(lines)
             if "_connected" in l and ("if " in l or "return " in l)]
    print("   every read of _connected used as a gate:")
    for n, text in gates:
        print(f"     {n:>5}: {text}")
    print()
    print("   => _connected is written ONLY inside initialize(). No command method, and")
    print("      not close(), ever clears it. Both recovery entry points gate on it:")
    print("      get_redis_manager() fast path and try_reconnect()'s first line. A")
    print("      runtime command failure therefore disables BOTH, not just one.")
    print()


async def main() -> int:
    print("READ-ONLY. No Redis, no network. backend_app/ and tests/ are NOT modified.\n")
    await r6_contract_from_source()
    g1 = await g1_partial_init_leak()
    g2 = await g2_replace_without_close()
    g3 = await g3_close_does_not_reset_health()

    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"   G1 partial-init pool leak        : {'CONFIRMED' if g1 else 'not observed'}")
    print(f"   G2 replace-without-close leak    : {'CONFIRMED' if g2 else 'not observed'}")
    print(f"   G3 close() leaves health True    : {'CONFIRMED' if g3 else 'not observed'}")
    print()
    print("   These are NOT new defects introduced by any design option -- they are")
    print("   pre-existing properties of initialize()/close() that every option in the")
    print("   A22 design comparison has to account for, because each option increases")
    print("   how often initialize() runs.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
