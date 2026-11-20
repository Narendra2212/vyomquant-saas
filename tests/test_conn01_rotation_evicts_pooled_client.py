"""CONN-01, closed: a credential ROTATION through POST /api/exchange/keys now evicts the
pooled in-process exchange client, so the new credential takes effect without a restart.

THE DEFECT (characterised, not fixed, before Phase 10)
------------------------------------------------------
`backend/connection_engine.py::get_or_create_exchange` keys the connection pool on
``sha256([user_id, exchange_id])`` and deliberately omits the credential. That is correct
for the POOL as a primitive -- the key still separates users and venues, and
`tests/test_exchange_safety_fix.py::test_pool_key_is_the_user_exchange_pair_not_the_credential`
proves each pooled client carries only its own secret -- but it means a second
`get_or_create_exchange` for an already-pooled (user, exchange) returns the EXISTING client
and discards the new credentials. `delete_connection` already evicts via `release_exchange`;
`store_keys` (the create-OR-ROTATE handler) did not, so a rotation was silently ineffective
in-process until the connection was deleted or the process restarted.

THE FIX
-------
`routers/exchange.py::store_keys` now calls `release_exchange(user_id, exchange_id)` AFTER
the vault write succeeds. The pool key still omits the credential -- the pool primitive is
unchanged -- but the rotation path now evicts the stale client, so the next
`get_or_create_exchange` reconstructs with the freshly stored credential.

WHAT THESE TESTS DRIVE, AND WHAT THEY DO NOT
--------------------------------------------
The REAL `store_keys` handler runs, against controlled in-process doubles for the vault, the
Supabase client, the Redis cache and the connection-slot quota, and the REAL
`_exchange_pool` / `get_or_create_exchange` / `release_exchange`. The venue client class is
the same controlled transport the ENV-01 tests use, so no network, no real credential and no
live order are involved. The claim is strictly about in-process pool eviction on rotation;
it says nothing about whether any venue would accept any key, exactly the ENV-01 boundary.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import ClassVar, List, Optional

import pytest


# ── A controlled venue client, identical in shape to the ENV-01 suite's. ──────────────────
class _ControlledVenueClient:
    instances: ClassVar[list] = []
    raise_on_load_markets: ClassVar[Optional[BaseException]] = None

    def __init__(self, config: dict):
        self.config = dict(config)
        self.apiKey = config.get("apiKey")
        self.secret = config.get("secret")
        self.password = config.get("password")
        self.symbols = ["BTC/USDT", "ETH/USDT"]
        self.markets = {"BTC/USDT": {}, "ETH/USDT": {}}
        self.load_markets_calls = 0
        self.close_calls = 0
        self.sandbox = False
        type(self).instances.append(self)

    async def load_markets(self, *_a, **_k):
        self.load_markets_calls += 1
        if self.raise_on_load_markets is not None:
            raise self.raise_on_load_markets
        return self.markets

    async def fetch_balance(self, *_a, **_k):
        return {"total": {}, "free": {}, "used": {}}

    async def close(self):
        self.close_calls += 1

    def set_sandbox_mode(self, enabled: bool):
        self.sandbox = bool(enabled)


@pytest.fixture
def controlled_venue(monkeypatch):
    """Point connection_engine at the controlled transport and isolate the global pool."""
    from backend_app.backend import connection_engine as ce

    saved_pool = dict(ce._exchange_pool)
    # `_pool_credential_id` and the per-test lock replacement both arrived with the Phase 10
    # CONN-01 work; see `tests/test_conn01_pool_lifecycle_and_shutdown.py` for why a
    # module-global `asyncio.Lock` cannot be shared across pytest-asyncio's per-test loops.
    saved_ids = dict(ce._pool_credential_id)
    saved_lock = ce._pool_lock
    ce._pool_lock = asyncio.Lock()
    ce._exchange_pool.clear()
    ce._pool_credential_id.clear()
    _ControlledVenueClient.instances = []
    _ControlledVenueClient.raise_on_load_markets = None
    monkeypatch.setattr(
        ce,
        "ccxt",
        SimpleNamespace(
            binance=_ControlledVenueClient,
            kraken=_ControlledVenueClient,
            exchanges=["binance", "kraken"],
        ),
    )
    try:
        yield _ControlledVenueClient
    finally:
        ce._exchange_pool.clear()
        ce._pool_credential_id.clear()
        ce._exchange_pool.update(saved_pool)
        ce._pool_credential_id.update(saved_ids)
        ce._pool_lock = saved_lock
        _ControlledVenueClient.instances = []
        _ControlledVenueClient.raise_on_load_markets = None


# ── Doubles for the store_keys handler's other collaborators. ─────────────────────────────
class _FakeVault:
    """Records what store_exchange_keys was asked to persist; never touches a real store."""

    def __init__(self) -> None:
        self.stored: List[dict] = []

    def store_exchange_keys(self, **kwargs):
        self.stored.append(dict(kwargs))


class _FakeSupabase:
    """Enough of the supabase surface for check_exchange_connection_slot's count query.

    The slot check calls usage_ledger.count(...) which runs SELECTs; returning an empty
    result set means "zero connections held", so the slot is available and the handler
    proceeds to the part under test (the eviction).
    """

    def table(self, *_a, **_k):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def execute(self, *_a, **_k):
        return SimpleNamespace(data=[], error=None)


class _FakeRedis:
    def __init__(self) -> None:
        self.deleted: List[str] = []

    async def cache_delete(self, key):
        self.deleted.append(key)


def _body(exchange_id: str, api_key: str, secret_key: str):
    from backend_app.core.models import ExchangeKeysRequest

    return ExchangeKeysRequest(
        exchange_id=exchange_id, api_key=api_key, secret_key=secret_key
    )


@pytest.fixture(autouse=True)
def _slot_check_is_open(monkeypatch):
    """Make the connection-slot quota a no-op for this file.

    CONN-01 is about the connection POOL on a rotation, not about the plan's connection
    LIMIT -- that gate (`check_exchange_connection_slot`, which reads the plan and the
    account's existing connections) is QUOTA-01's ground and has its own tests. Stubbing it
    here keeps these tests about the one behaviour they exist to pin: that a successful store
    evicts the stale pooled client. The gate runs first in the handler, so without this a
    plan-context lookup against a fake supabase would decide the outcome before the eviction
    is ever reached.
    """
    from backend_app.routers import exchange as exchange_router

    async def _open(*_a, **_k):
        return None

    monkeypatch.setattr(exchange_router, "check_exchange_connection_slot", _open)


async def _call_store_keys(exchange_id, api_key, secret_key, user_id="user_rot"):
    """Invoke the REAL store_keys handler with controlled dependencies."""
    from backend_app.routers import exchange as exchange_router

    return await exchange_router.store_keys(
        body=_body(exchange_id, api_key, secret_key),
        user={"id": user_id},
        vault=_FakeVault(),
        redis_manager=_FakeRedis(),
        supabase=_FakeSupabase(),
    )


@pytest.mark.asyncio
async def test_a_rotation_evicts_the_pooled_client_so_the_new_credential_takes_effect(
    controlled_venue, monkeypatch
):
    """The CONN-01 fix: after store_keys with new keys, the next pool fetch reconstructs.

    This is the direct inverse of the characterisation test: there, a second
    get_or_create_exchange returned the stale client; here, store_keys has evicted it in
    between, so the next fetch builds a fresh client carrying the ROTATED credential.
    """
    from backend_app.backend.connection_engine import (
        _exchange_pool,
        get_or_create_exchange,
        release_exchange,
    )

    # 1. A client is pooled for (user_rot, binance) with the OLD credential, as a running
    #    bot would have done.
    pooled = await get_or_create_exchange("user_rot", "binance", "old-key", "old-secret")
    assert pooled.apiKey == "old-key"
    assert len(_exchange_pool) == 1

    # 2. The trader rotates their keys through the real store_keys handler.
    result = await _call_store_keys("binance", "NEW-key", "NEW-secret", user_id="user_rot")
    assert result["status"] == "ok"

    # 3. The stale client was evicted (popped AND closed) by store_keys.
    assert pooled.close_calls == 1, "store_keys did not close the superseded pooled client"
    assert _exchange_pool == {}, "store_keys did not evict the pooled client on rotation"

    # 4. The next pool fetch reconstructs with the ROTATED credential -- the rotation is now
    #    effective in-process, which is exactly what CONN-01 was that it was not.
    rotated = await get_or_create_exchange("user_rot", "binance", "NEW-key", "NEW-secret")
    assert rotated is not pooled
    assert rotated.apiKey == "NEW-key"
    assert rotated.secret == "NEW-secret"

    await release_exchange("user_rot", "binance")


@pytest.mark.asyncio
async def test_store_keys_calls_release_exchange_exactly_once_for_the_rotated_pair(
    controlled_venue, monkeypatch
):
    """The eviction is targeted and happens once, for the pair being rotated.

    Asserted on a spy over release_exchange rather than only on the pool's end state, so a
    future refactor that evicts the wrong pair (or evicts twice, or not at all) is caught by
    name rather than only by a downstream symptom.
    """
    from backend_app.routers import exchange as exchange_router

    calls: List[tuple] = []
    real_release = exchange_router.release_exchange

    async def _spy(user_id, exchange_id):
        calls.append((user_id, exchange_id))
        return await real_release(user_id, exchange_id)

    monkeypatch.setattr(exchange_router, "release_exchange", _spy)

    await _call_store_keys("binance", "k", "s", user_id="user_abc")

    assert calls == [("user_abc", "binance")], (
        f"store_keys must evict exactly the rotated (user, exchange) pair once; got {calls}"
    )


@pytest.mark.asyncio
async def test_a_first_time_connection_is_unaffected_by_the_eviction(controlled_venue):
    """The eviction is a no-op when nothing was pooled -- store_keys stays correct on create.

    A first-time connection has no pooled client, so release_exchange pops an absent key and
    returns without error. The store still succeeds; the eviction must not turn a create
    into a failure.
    """
    from backend_app.backend.connection_engine import _exchange_pool

    assert _exchange_pool == {}
    result = await _call_store_keys("kraken", "first-key", "first-secret", user_id="user_new")
    assert result["status"] == "ok"
    # Nothing to evict; the pool is still empty and no error was raised.
    assert _exchange_pool == {}


@pytest.mark.asyncio
async def test_a_store_that_fails_to_persist_does_not_evict(controlled_venue, monkeypatch):
    """Eviction happens AFTER the vault write, so a failed persist never drops a good client.

    If the vault write raises, the handler must propagate the failure WITHOUT having evicted
    the existing (still-valid) pooled client -- evicting on a failed rotation would degrade a
    working connection on behalf of a store that did not happen.
    """
    from backend_app.backend.connection_engine import (
        _exchange_pool,
        get_or_create_exchange,
        release_exchange,
    )
    from backend_app.routers import exchange as exchange_router
    from fastapi import HTTPException

    pooled = await get_or_create_exchange("user_fail", "binance", "old-key", "old-secret")
    assert len(_exchange_pool) == 1

    class _RaisingVault:
        def store_exchange_keys(self, **kwargs):
            raise RuntimeError("supabase write failed")

    released: List[tuple] = []
    real_release = exchange_router.release_exchange

    async def _spy(user_id, exchange_id):
        released.append((user_id, exchange_id))
        return await real_release(user_id, exchange_id)

    monkeypatch.setattr(exchange_router, "release_exchange", _spy)

    with pytest.raises(HTTPException):
        await exchange_router.store_keys(
            body=_body("binance", "NEW-key", "NEW-secret"),
            user={"id": "user_fail"},
            vault=_RaisingVault(),
            redis_manager=_FakeRedis(),
            supabase=_FakeSupabase(),
        )

    # The store failed, so NOTHING was evicted and the old client is still pooled and open.
    assert released == [], "a failed store must not evict the existing pooled client"
    assert len(_exchange_pool) == 1
    assert pooled.close_calls == 0

    await release_exchange("user_fail", "binance")
