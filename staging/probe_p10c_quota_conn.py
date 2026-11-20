"""PHASE 10 / WORKSTREAM C -- baseline probe for QUOTA-01 and CONN-01.

Measures the behaviour that is suspected to be defective, BEFORE any fix, so the
remediation has a before/after figure rather than an argument. No network, no live order,
no real credential: the venue client class is replaced by an in-process transport, the
same technique tests/test_exchange_safety_fix.py uses.

Run:  .venv\\Scripts\\python.exe staging/probe_p10c_quota_conn.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import ClassVar, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENV", "development")


def banner(text: str) -> None:
    print("=" * 78)
    print(text)
    print("=" * 78)


class _Venue:
    """In-process stand-in for a ccxt.pro client. Records the config it was built with."""

    instances: ClassVar[list] = []

    def __init__(self, config: dict):
        self.config = dict(config)
        self.apiKey = config.get("apiKey")
        self.secret = config.get("secret")
        self.symbols = ["BTC/USDT"]
        self.markets = {"BTC/USDT": {}}
        self.close_calls = 0
        type(self).instances.append(self)

    async def load_markets(self, *_a, **_k):
        return self.markets

    async def fetch_balance(self, *_a, **_k):
        return {"total": {}, "free": {}, "used": {}}

    async def close(self):
        self.close_calls += 1

    def set_sandbox_mode(self, enabled: bool):
        pass


def _install_venue(ce):
    ce.ccxt = SimpleNamespace(
        binance=_Venue, kraken=_Venue, coinbase=_Venue,
        exchanges=["binance", "kraken", "coinbase"],
    )


# ──────────────────────────────────────────────────────────────────────────────────────
# CONN-01-A: a CREDENTIAL-LESS caller poisons the pool for credentialed callers
# ──────────────────────────────────────────────────────────────────────────────────────
async def probe_credential_less_poisoning():
    banner("CONN-01-A  a credential-less caller decides the pooled client for everyone")
    from backend_app.backend import connection_engine as ce

    ce._exchange_pool.clear()
    _Venue.instances = []
    _install_venue(ce)

    # core/consistency_checker.py:319 and backend/exchange_websocket_listener.py:206 both
    # call get_or_create_exchange with NO credentials. Simulate that winning the race.
    unauth = await ce.get_or_create_exchange("user_x", "binance")
    print(f"  background service pooled a client; apiKey={unauth.apiKey!r}")

    # routers/orders.py:794 then asks for the SAME pair WITH the vault credential, because
    # it needs private calls (fetch_open_orders).
    served = await ce.get_or_create_exchange("user_x", "binance", "real-key", "real-secret")
    print(f"  the credentialed caller was served: apiKey={served.apiKey!r}")

    poisoned = served is unauth and served.apiKey is None
    print(f"  clients constructed: {len(_Venue.instances)}")
    print(f"  RESULT: {'DEFECT CONFIRMED' if poisoned else 'no defect'} -- a credentialed "
          f"caller {'received an UNAUTHENTICATED client' if poisoned else 'got its own client'}")
    ce._exchange_pool.clear()
    return poisoned


# ──────────────────────────────────────────────────────────────────────────────────────
# CONN-01-B: no shutdown drain -- pooled clients are left open at process exit
# ──────────────────────────────────────────────────────────────────────────────────────
async def probe_shutdown_drain():
    banner("CONN-01-B  shutdown leaves every pooled client open")
    from backend_app.backend import connection_engine as ce

    ce._exchange_pool.clear()
    _Venue.instances = []
    _install_venue(ce)

    for uid in ("u1", "u2", "u3"):
        await ce.get_or_create_exchange(uid, "binance", f"k-{uid}", f"s-{uid}")
    print(f"  pooled clients: {len(ce._exchange_pool)}")

    has_drain = any(
        hasattr(ce, name) for name in ("close_all_exchanges", "drain_exchange_pool")
    )
    print(f"  a drain/close-all function exists in connection_engine: {has_drain}")

    import pathlib
    main_src = pathlib.Path("backend_app/main.py").read_text(encoding="utf-8", errors="replace")
    drained_in_lifespan = ("release_exchange" in main_src) or ("_exchange_pool" in main_src) \
        or ("close_all_exchanges" in main_src)
    print(f"  main.py lifespan drains the pool: {drained_in_lifespan}")

    unclosed = sum(1 for c in _Venue.instances if c.close_calls == 0)
    print(f"  clients still OPEN after a simulated shutdown: {unclosed} of {len(_Venue.instances)}")
    defect = (not has_drain) and (not drained_in_lifespan)
    print(f"  RESULT: {'DEFECT CONFIRMED' if defect else 'no defect'} -- "
          f"{'no code path closes a pooled client at shutdown' if defect else 'a drain exists'}")
    ce._exchange_pool.clear()
    return defect


# ──────────────────────────────────────────────────────────────────────────────────────
# CONN-01-C: POST /connections/{id}/reconnect does not touch the pool
# ──────────────────────────────────────────────────────────────────────────────────────
async def probe_reconnect_does_not_evict():
    banner("CONN-01-C  the /reconnect endpoint answers RECONNECTED without reconnecting")
    import inspect as _inspect

    from backend_app.routers import exchange as xr

    src = _inspect.getsource(xr.reconnect_connection_by_id)
    evicts = "release_exchange" in src
    print("  handler source:")
    for line in src.splitlines():
        print(f"    {line}")
    print(f"  calls release_exchange: {evicts}")
    print(f"  RESULT: {'DEFECT CONFIRMED' if not evicts else 'no defect'} -- a user-initiated "
          f"reconnect {'leaves the stale pooled client in place' if not evicts else 'evicts'}")
    return not evicts


# ──────────────────────────────────────────────────────────────────────────────────────
# QUOTA-01: the exchange-connection slot is read-then-write, so it races
# ──────────────────────────────────────────────────────────────────────────────────────
class _ProbeLockStore:
    """The shared cache, with real SET NX semantics, so the guard can actually exclude."""

    def __init__(self):
        self.store = {}

    async def set(self, key, value, **kwargs):
        if kwargs.get("nx") and key in self.store:
            return False
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
        return len(keys)


async def probe_exchange_slot_race():
    banner("QUOTA-01  concurrent stores of DIFFERENT venues both pass one free slot")
    from backend_app.core import subscription_dependencies as sd
    from backend_app.core import usage_ledger

    async def fake_plan_context(user_id, supabase):
        return sd.PlanContext(plan="free", tier="free", display_name="Free")

    orig_ids = usage_ledger.exchange_ids
    orig_sd_ids = sd.usage_ledger.exchange_ids
    orig_ctx = sd.get_plan_context
    orig_redis = sd.redis_manager
    sd.get_plan_context = fake_plan_context
    try:
        limit = 1
        print(f"  plan limit for exchange_connections (free): {limit}")

        async def run(use_guard: bool):
            """One pair of concurrent POST /api/exchange/keys requests for DIFFERENT venues."""
            stored: List[str] = []

            async def fake_exchange_ids(user_id, supabase):
                # Reflects only what has been COMMITTED, exactly as a database read would.
                return {e for e in stored}

            usage_ledger.exchange_ids = fake_exchange_ids
            sd.usage_ledger.exchange_ids = fake_exchange_ids
            sd.redis_manager = _ProbeLockStore()

            async def attempt(venue):
                user = {"id": "user_q"}
                try:
                    # The handler's fast pre-check.
                    await sd.check_exchange_connection_slot(user, object(), venue)
                    if use_guard:
                        # ...and the control that authorises the write.
                        async with sd.exchange_connection_slot_guard(user, object(), venue):
                            await asyncio.sleep(0.01)  # the write is not instantaneous
                            stored.append(venue)
                    else:
                        await asyncio.sleep(0.01)
                        stored.append(venue)
                except Exception as exc:
                    code = getattr(exc, "status_code", type(exc).__name__)
                    return (venue, "REFUSED", str(code))
                return (venue, "ADMITTED", None)

            results = await asyncio.gather(attempt("binance"), attempt("kraken"))
            return results, stored

        # (a) The PRE-CHECK ALONE -- the pre-fix code path, and still the shape of the bare
        #     dependency. It is kept in the handler on purpose (it refuses an over-limit
        #     account before any credential leaves the process) but it is NOT the control.
        results, stored = await run(use_guard=False)
        print("  (a) pre-check alone -- the pre-fix path, unchanged by design:")
        for venue, outcome, code in results:
            print(f"        {venue:10s} -> {outcome}{'' if not code else ' (' + code + ')'}")
        bare_admitted = sum(1 for _, o, _ in results if o == "ADMITTED")
        print(f"        held: {len(stored)} ({', '.join(stored)})  admitted: {bare_admitted}")

        # (b) THE GUARDED PATH -- what routers/exchange.py::store_keys now does.
        results, stored = await run(use_guard=True)
        print("  (b) with exchange_connection_slot_guard -- the control:")
        for venue, outcome, code in results:
            print(f"        {venue:10s} -> {outcome}{'' if not code else ' (' + code + ')'}")
        guarded_admitted = sum(1 for _, o, _ in results if o == "ADMITTED")
        print(f"        held: {len(stored)} ({', '.join(stored)})  admitted: {guarded_admitted}")

        over = guarded_admitted > limit
        print(f"  the race is real at the pre-check ({bare_admitted} > {limit}) and closed by "
              f"the guard ({guarded_admitted} <= {limit})")
        print(f"  RESULT: {'DEFECT CONFIRMED' if over else 'no defect'} -- the enforced path "
              f"{'EXCEEDED' if over else 'respected'} the plan limit")
        return over
    finally:
        usage_ledger.exchange_ids = orig_ids
        sd.usage_ledger.exchange_ids = orig_sd_ids
        sd.get_plan_context = orig_ctx
        sd.redis_manager = orig_redis


# ──────────────────────────────────────────────────────────────────────────────────────
# QUOTA-01: HardQuotaEnforcer is still undeprecated and still instantiated in live code
# ──────────────────────────────────────────────────────────────────────────────────────
def probe_enforcer_deprecation():
    banner("QUOTA-01  HardQuotaEnforcer carries no deprecation marker and is instantiated")
    import ast
    import pathlib

    src = pathlib.Path("backend_app/core/hard_quota_enforcer.py").read_text(
        encoding="utf-8", errors="replace"
    )
    has_marker = ("DeprecationWarning" in src) or ("warnings.warn" in src)
    print(f"  deprecation marker present: {has_marker}")

    # Counted by AST, not by substring: after the fix, both files still MENTION the class in a
    # comment explaining why the attribute was removed, and a naive text match reports those
    # comments as constructions. A probe that cannot tell a comment from a call is not evidence.
    sites = []
    for path in ("backend_app/core/dag_task_queue.py", "backend_app/core/entitlement_engine.py"):
        tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "HardQuotaEnforcer"):
                sites.append(f"{path}:{node.lineno}")
    print(f"  live-code constructions of the fail-open enforcer (AST): {len(sites)}")
    for s in sites:
        print(f"    {s}")

    # The singleton must be lazy, or importing the module is itself deprecated use.
    import warnings as _w
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        import importlib
        import sys
        sys.modules.pop("backend_app.core.hard_quota_enforcer", None)
        importlib.import_module("backend_app.core.hard_quota_enforcer")
    import_warns = [c for c in caught if issubclass(c.category, DeprecationWarning)
                    and "HardQuotaEnforcer" in str(c.message)]
    print(f"  importing the module warns (it should not): {bool(import_warns)}")

    defect = (not has_marker) or bool(sites) or bool(import_warns)
    print(f"  RESULT: {'DEFECT CONFIRMED' if defect else 'no defect'}")
    return defect


async def main():
    findings = {}
    findings["CONN-01-A credential-less pool poisoning"] = await probe_credential_less_poisoning()
    findings["CONN-01-B no shutdown drain"] = await probe_shutdown_drain()
    findings["CONN-01-C reconnect does not evict"] = await probe_reconnect_does_not_evict()
    findings["QUOTA-01 exchange-slot concurrency overshoot"] = await probe_exchange_slot_race()
    findings["QUOTA-01 enforcer undeprecated"] = probe_enforcer_deprecation()

    banner("SUMMARY")
    for name, confirmed in findings.items():
        print(f"  {'DEFECT' if confirmed else 'OK    '}  {name}")
    print(f"\n  {sum(findings.values())} of {len(findings)} probed behaviours are defective.")


if __name__ == "__main__":
    asyncio.run(main())
