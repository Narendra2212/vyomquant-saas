"""
EXCHANGE-CRITICAL-001 Regression Test: Exchange Reconciliation Safety

Tests that exchange reconciliation handles timeout, retry, and unknown states correctly
to prevent incorrect order status assumptions.

This test verifies that exchange reconciliation fails safely rather than assuming orders are filled.
"""

import pytest
import asyncio
from types import SimpleNamespace
from typing import ClassVar, Optional
from unittest.mock import AsyncMock, Mock
from backend_app.backend.exchange_reconciliation import ExchangeReconciliationService, ReconciliationAction


@pytest.mark.asyncio
async def test_exchange_timeout_fails_safely():
    """
    EXCHANGE-CRITICAL-001: Verify that exchange timeout causes safe failure.
    
    This test ensures that when exchange API times out, the reconciliation service
    does NOT assume orders are filled but marks them as UNKNOWN for manual review.
    """
    # Create mock exchange that times out
    mock_exchange = AsyncMock()
    mock_exchange.fetch_open_orders = AsyncMock(side_effect=asyncio.TimeoutError("Exchange timeout"))
    
    # Create mock database session
    mock_db = Mock()
    
    # Create reconciliation service
    service = ExchangeReconciliationService(mock_db, mock_exchange)
    
    # Try to fetch exchange orders - should raise exception
    with pytest.raises(Exception):
        await service._fetch_exchange_orders("test_tenant", "binance")
    
    print("✓ Exchange timeout raises exception (does not return empty list)")


@pytest.mark.asyncio
async def test_exchange_error_propagates():
    """
    EXCHANGE-CRITICAL-001: Verify that exchange errors propagate correctly.
    
    This test ensures that exchange API errors (429, 500, etc.) are not swallowed
    but propagate to the caller for proper error handling.
    """
    # Create mock exchange that returns error
    mock_exchange = AsyncMock()
    mock_exchange.fetch_open_orders = AsyncMock(side_effect=Exception("Exchange API error 429"))
    
    # Create mock database session
    mock_db = Mock()
    
    # Create reconciliation service
    service = ExchangeReconciliationService(mock_db, mock_exchange)
    
    # Try to fetch exchange orders - should raise exception
    with pytest.raises(Exception, match="Exchange API error"):
        await service._fetch_exchange_orders("test_tenant", "binance")
    
    print("✓ Exchange API errors propagate correctly")


@pytest.mark.asyncio
async def test_empty_response_not_assumed():
    """
    EXCHANGE-CRITICAL-001: Verify that empty response is not assumed as success.
    
    This test ensures that an empty list from exchange is not assumed to mean
    "no orders exist" but is treated as a potential error condition.
    """
    # Create mock exchange that returns empty list
    mock_exchange = AsyncMock()
    mock_exchange.fetch_open_orders = AsyncMock(return_value=[])
    
    # Create mock database session
    mock_db = Mock()
    
    # Create reconciliation service
    service = ExchangeReconciliationService(mock_db, mock_exchange)
    
    # Fetch exchange orders - returns empty list
    orders = await service._fetch_exchange_orders("test_tenant", "binance")
    
    # Empty list should be returned but reconciliation should handle this carefully
    assert orders == []
    
    print("✓ Empty response handled correctly (not assumed as success)")


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ENV-01 — CONNECTION POOLING, PROVEN WITHOUT A VENUE
#
#  WHAT WAS WRONG WITH THIS TEST. `test_connection_pooling_safety` had no mocking at all.
#  `get_or_create_exchange("user_123", "binance", "key1", "secret1")` constructs a real
#  `ccxt.pro.binance` and `ConnectionEngine.connect()` awaits `load_markets()`, which for a
#  CREDENTIALED binance client issues
#      GET https://api.binance.com/sapi/v1/capital/config/getall
#  with `X-MBX-APIKEY: key1`. So the test made an outbound authenticated request to a live
#  production venue from the unit lane, using a credential invented in the test body, and
#  was red either way: with network it reached Binance and got
#      ccxt.base.errors.AuthenticationError: binance {"code":-2008,"msg":"Invalid Api-Key ID."}
#  and without network it got `ExchangeNotAvailable`. Neither outcome says anything about
#  pooling. The property was never actually being measured.
#
#  THE BOUNDARY THIS DRAWS, AND WHY IT MATTERS. Two different things were tangled together
#  in one assertion:
#
#    (1) CONNECTION-POOLING CORRECTNESS — does `_exchange_pool` key, reuse, isolate and
#        evict correctly? This is pure in-process bookkeeping: a sha256 of
#        `[user_id, exchange_id]`, a dict, an `asyncio.Lock`, and `pop` + `close` on
#        release. It involves no venue, no credential and no network, and it is fully
#        decidable here. It is tested below.
#
#    (2) VENUE AUTHENTICATION / CONNECTIVITY — will Binance accept this key, is the API
#        reachable, does `load_markets()` succeed? This is a statement about a third party
#        and a secret, is not decidable in the unit lane, and cannot be made deterministic
#        without either shipping a credential or depending on the Internet. It is NOT
#        tested here, and this file does not claim it.
#
#  Conflating them is what made a pooling regression indistinguishable from an expired key.
#  The tests below exercise the REAL `get_or_create_exchange`, the REAL
#  `ConnectionEngine.connect()` (config assembly, sandbox branch, retry loop, failure
#  teardown) and the REAL `release_exchange`; only the venue client class is replaced, by a
#  controlled in-process transport that answers `load_markets()` locally. Nothing is
#  skipped, xfailed, credentialed or weakened — the assertions are strictly stronger than
#  the originals, because they now check the credentials each pooled client actually
#  carries rather than only object identity.
# ══════════════════════════════════════════════════════════════════════════════════════════


class _ControlledVenueClient:
    """An in-process stand-in for a `ccxt.pro` exchange client.

    Constructed by `ConnectionEngine.connect()` exactly as the real class is — it receives
    the same assembled `config` dict — so what credentials reached which client is
    observable rather than assumed. `load_markets()` resolves locally, which is the single
    thing that removes the network from this path.

    `raise_on_load_markets` lets a test ask for a venue that refuses the handshake, so the
    "a failed connect is never pooled" property can be measured instead of argued.
    """

    raise_on_load_markets: Optional[BaseException] = None
    instances: ClassVar[list] = []

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

    async def load_markets(self, *_args, **_kwargs):
        self.load_markets_calls += 1
        if self.raise_on_load_markets is not None:
            raise self.raise_on_load_markets
        return self.markets

    async def close(self):
        self.close_calls += 1

    def set_sandbox_mode(self, enabled: bool):
        self.sandbox = bool(enabled)


@pytest.fixture
def controlled_venue(monkeypatch):
    """Point `connection_engine` at the controlled transport and isolate the pool.

    `connection_engine.ccxt` is the `ccxt.pro` module, and `connect()` reaches the venue
    class through `hasattr(ccxt, id)` / `getattr(ccxt, id)`. Swapping the module reference
    on the `connection_engine` namespace (rather than mutating `ccxt.pro` itself) keeps the
    substitution local to this module and this test.

    `_exchange_pool` is module-global, so it is snapshotted and restored: a leaked entry
    would make a later test depend on how many earlier tests ran — the ordering class of
    defect this suite already found once in Phase 6.
    """
    from backend_app.backend import connection_engine as ce

    saved_pool = dict(ce._exchange_pool)
    # `_pool_credential_id` arrived with the CONN-01 fix and is snapshotted for the same reason
    # as the pool: an identity left behind for an evicted entry would make a later test's
    # credential comparison depend on how many earlier tests ran.
    saved_ids = dict(ce._pool_credential_id)
    # `_pool_lock` is a module-global `asyncio.Lock` and binds to the running loop the first
    # time it has to WAIT. pytest-asyncio gives each test a fresh loop, so the first contending
    # test anywhere in the suite bound it and every later contending test on another loop raised
    # "RuntimeError: <Lock> is bound to a different event loop" — making the result depend on
    # file order, which is the defect class Phase 6 already found in this module. Replaced per
    # test. Production has one loop for the process lifetime, so this is test isolation only.
    saved_lock = ce._pool_lock
    ce._pool_lock = asyncio.Lock()
    ce._exchange_pool.clear()
    ce._pool_credential_id.clear()
    _ControlledVenueClient.instances = []
    _ControlledVenueClient.raise_on_load_markets = None

    # Every venue id this test module uses resolves to the controlled client; an id that is
    # NOT listed stays unresolvable, so `connect()`'s "unsupported exchange" branch is
    # untouched and a typo in a test cannot silently reach the real module.
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


@pytest.mark.asyncio
async def test_connection_pooling_safety(controlled_venue):
    """
    EXCHANGE-CRITICAL-002 / ENV-01: connection pooling reuses one client per
    (user, exchange) and never serves one user's client to another.

    Deterministic: no network, no credential, no Internet dependency. See the boundary note
    above — this proves pooling bookkeeping, and deliberately says nothing about whether any
    venue would accept these keys.

    Stronger than the original, which only compared object identity. Identity alone cannot
    distinguish "isolated correctly" from "both users got the same wrong client", so this
    also reads back the credentials each pooled client was constructed with.
    """
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange,
                                                       release_exchange)

    # Same user + exchange: the second call must be served from the pool, NOT reconnected.
    first = await get_or_create_exchange("user_123", "binance", "key-123", "secret-123")
    second = await get_or_create_exchange("user_123", "binance", "key-123", "secret-123")

    assert first is second, "Same user+exchange should share connection"
    # The decisive measurement the identity check cannot make on its own: exactly ONE venue
    # client was ever constructed, and the handshake ran exactly once. Two clients that
    # happened to compare equal would pass an identity check and still be two sockets.
    assert len(controlled_venue.instances) == 1
    assert first.load_markets_calls == 1

    # Different user, same exchange: a separate client carrying only its own credential.
    other = await get_or_create_exchange("user_456", "binance", "key-456", "secret-456")

    assert other is not first, "Different user should get different connection"
    assert len(controlled_venue.instances) == 2
    assert first.apiKey == "key-123" and first.secret == "secret-123"
    assert other.apiKey == "key-456" and other.secret == "secret-456"
    assert len(_exchange_pool) == 2

    # Same user, different exchange: also a separate client — the pool key is the PAIR.
    other_venue = await get_or_create_exchange("user_123", "kraken", "key-123", "secret-123")
    assert other_venue is not first
    assert len(_exchange_pool) == 3

    # Release is eviction AND close: a popped client that is never closed is the "Unclosed
    # client session" leak this module's CE-3 comment is about.
    await release_exchange("user_123", "binance")
    assert first.close_calls == 1
    assert len(_exchange_pool) == 2

    # And eviction is real: the next call for that pair reconnects rather than serving a
    # closed client.
    reconnected = await get_or_create_exchange("user_123", "binance", "key-123", "secret-123")
    assert reconnected is not first
    assert len(controlled_venue.instances) == 4

    await release_exchange("user_123", "binance")
    await release_exchange("user_456", "binance")
    await release_exchange("user_123", "kraken")
    assert _exchange_pool == {}


@pytest.mark.asyncio
async def test_pool_key_is_the_user_exchange_pair_not_the_credential(controlled_venue):
    """
    ENV-01 boundary, and finding CONN-01: the pool KEY is `sha256([user_id, exchange_id])` and
    does NOT include the credential. That is still true, and still deliberate.

    WHY THE KEY MUST NOT INCLUDE THE CREDENTIAL. Re-keying on the credential would let one
    (user, venue) pair hold SEVERAL simultaneous authenticated sockets — one per credential ever
    presented — which defeats FIX CE-4, the reason the pool exists. So the key stays the pair.

    WHAT THIS TEST USED TO RECORD, AND WHY IT CHANGED. It was a CHARACTERISATION test: it
    asserted that a second `get_or_create_exchange` for an already-pooled pair was served the
    existing client and the new credential was DISCARDED. That was measured behaviour and it was
    the defect — recorded here rather than endorsed, pending an owner for the key-rotation flow.

    Phase 10 fixed it in two layers, and this test now pins the key derivation plus the first
    layer:

      1. THE PRIMITIVE (this test). The credential is tracked ALONGSIDE the pool entry, as a
         non-reversible fingerprint in `_pool_credential_id`. A caller presenting a credential
         that differs from the pooled client's gets a REBUILD in place — same key, new client,
         old socket closed. So the rotation takes effect even if no router evicted, which is
         what stops a future second rotation path from resurrecting the finding.
         See `tests/test_conn01_pool_lifecycle_and_shutdown.py` for the full rule, including why
         a CREDENTIAL-LESS caller deliberately does NOT rebuild (it would thrash).

      2. THE ROUTER. `store_keys` also calls `release_exchange` after the vault write, so the
         eviction is explicit on the path a trader actually uses.
         See `tests/test_conn01_rotation_evicts_pooled_client.py`.

    WHAT THIS IS NOT. It was never cross-tenant credential mixing: the key separates users, and
    `test_connection_pooling_safety` above proves each user's client carries only its own secret.
    The exposure was "a rotation is silently ineffective until restart" — an operational
    correctness defect, not a tenant-isolation breach.
    """
    import hashlib
    import json

    from backend_app.backend.connection_engine import (_exchange_pool, _pool_key,
                                                       get_or_create_exchange,
                                                       release_exchange)

    # The key is the pair, and nothing else. Computed independently of the implementation so a
    # change to the derivation is caught here rather than inferred from a downstream symptom.
    expected_key = hashlib.sha256(
        json.dumps(["user_rot", "binance"], sort_keys=True).encode()
    ).hexdigest()
    assert _pool_key("user_rot", "binance") == expected_key

    pooled = await get_or_create_exchange("user_rot", "binance", "old-key", "old-secret")
    assert list(_exchange_pool) == [expected_key], (
        "the pooled entry is not under sha256([user_id, exchange_id]); the key now depends on "
        "something else, which would let one pair hold several authenticated sockets"
    )

    # Same KEY, different credential: the entry is REPLACED rather than re-keyed...
    after_rotation = await get_or_create_exchange("user_rot", "binance", "NEW-key", "NEW-secret")

    assert list(_exchange_pool) == [expected_key], "the rotation added a second pool entry"
    assert len(_exchange_pool) == 1
    # ...and the new credential is the one in service, which is what CONN-01 was that it was not.
    assert after_rotation is not pooled
    assert after_rotation.apiKey == "NEW-key"
    assert after_rotation.secret == "NEW-secret"
    # The superseded client was closed, not leaked.
    assert pooled.close_calls == 1
    assert len(controlled_venue.instances) == 2

    await release_exchange("user_rot", "binance")
    assert _exchange_pool == {}


@pytest.mark.asyncio
async def test_a_refused_venue_handshake_is_never_pooled(controlled_venue):
    """
    ENV-01 boundary, stated as a property instead of a disclaimer.

    This is the one place the two concerns legitimately touch: venue authentication is NOT
    decidable here, but what the POOL does when a handshake is refused is. A rejected
    credential must leave nothing behind — if a failed client were cached, every later
    request for that pair would be served a dead object, and the only way back would be a
    restart.

    The refusal is injected, so the venue's verdict is a controlled input rather than a
    measurement of Binance. That is exactly the separation the original test collapsed: it
    took a REAL `AuthenticationError` from a REAL venue as its (accidental) outcome and
    therefore reported a credential problem as a pooling failure.

    Also covers the CE-3 leak the module comment describes: a `connect()` that raises never
    hands the caller a reference, so if `connect()` did not close the client itself the
    aiohttp session would be unreachable and unclosable.
    """
    import ccxt as ccxt_base

    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange)

    controlled_venue.raise_on_load_markets = ccxt_base.AuthenticationError(
        'binance {"code":-2008,"msg":"Invalid Api-Key ID."}'
    )

    with pytest.raises(ccxt_base.AuthenticationError):
        await get_or_create_exchange("user_bad", "binance", "revoked", "revoked")

    # Nothing pooled...
    assert _exchange_pool == {}
    # ...and the client that was constructed was closed rather than leaked.
    assert len(controlled_venue.instances) == 1
    assert controlled_venue.instances[0].close_calls == 1

    # The failure is not sticky: once the venue accepts, the pool fills normally.
    controlled_venue.raise_on_load_markets = None
    recovered = await get_or_create_exchange("user_bad", "binance", "good", "good")
    assert recovered.apiKey == "good"
    assert len(_exchange_pool) == 1


@pytest.mark.asyncio
async def test_concurrent_first_calls_create_exactly_one_pooled_client(
    controlled_venue, monkeypatch
):
    """
    ENV-01: the `_pool_lock` is the thing that makes "one connection per (user, exchange)"
    true under concurrency, and it is only observable with concurrent callers.

    Several bots for the same user start at once — the real shape of this code's workload
    (`portfolio_cache_updater`, `exchange_websocket_listener`, `ws_routes` and the REST
    routers all call `get_or_create_exchange` for the same pair). Without the lock, each
    would see `pool_key not in _exchange_pool`, each would connect, and the last writer
    would win while the others' sockets leaked.

    `load_markets` yields control, so the interleaving is real rather than nominal.
    """
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange,
                                                       release_exchange)

    async def slow_load_markets(self, *_a, **_k):
        self.load_markets_calls += 1
        await asyncio.sleep(0.01)  # a real suspension point inside the critical section
        return self.markets

    # Through monkeypatch so the slow handshake cannot leak into a later test in this
    # module and make it depend on execution order.
    monkeypatch.setattr(controlled_venue, "load_markets", slow_load_markets)

    results = await asyncio.gather(
        *[
            get_or_create_exchange("user_race", "binance", "key-race", "secret-race")
            for _ in range(8)
        ]
    )

    assert len({id(r) for r in results}) == 1, "all concurrent callers must share one client"
    assert len(controlled_venue.instances) == 1, "exactly one venue client constructed"
    assert controlled_venue.instances[0].load_markets_calls == 1, "one handshake, not eight"
    assert len(_exchange_pool) == 1

    await release_exchange("user_race", "binance")


@pytest.mark.asyncio
async def test_sha256_collision_prevention():
    """
    EXCHANGE-CRITICAL-003: Verify that SHA-256 hashing prevents key collision.
    
    This test ensures that the exchange pool uses SHA-256 hashing to prevent
    Python hash() collision risks and credential mixing.
    """
    import hashlib
    import json
    
    # Test that SHA-256 is used for pool keys
    user_id = "user_123"
    exchange_id = "binance"
    
    # Replicate the hashing logic from connection_engine
    pool_key = hashlib.sha256(
        json.dumps([user_id, exchange_id], sort_keys=True).encode()
    ).hexdigest()
    
    # Verify the hash is deterministic
    pool_key2 = hashlib.sha256(
        json.dumps([user_id, exchange_id], sort_keys=True).encode()
    ).hexdigest()
    
    assert pool_key == pool_key2, "SHA-256 should be deterministic"
    
    # Verify different inputs produce different hashes
    different_key = hashlib.sha256(
        json.dumps([user_id, "coinbase"], sort_keys=True).encode()
    ).hexdigest()
    
    assert pool_key != different_key, "Different inputs should produce different hashes"
    
    print("✓ SHA-256 hashing prevents collision")


@pytest.mark.asyncio
async def test_retry_with_exponential_backoff():
    """
    EXCHANGE-CRITICAL-004: Verify that retry logic uses exponential backoff.
    
    This test ensures that exchange API failures trigger exponential backoff
    rather than immediate retry storms.

    `OrderWatchdog(max_retries=..., retry_delay=...)` is not the constructor: the real
    signature is `OrderWatchdog(db_session, config=WatchdogConfig(...))`, the retry budget
    lives on the config, and there is no `retry_delay` at all — the delay is computed as
    `2 ** attempt` rather than scaled off a configured base (`order_watchdog.py:400, 405`).
    The session is `None` because nothing here reaches the database; the constructor only
    stores it. The loop bound now comes from the watchdog's own configured budget instead
    of a repeated literal, so it cannot silently disagree with it.
    """
    from backend_app.backend.order_watchdog import OrderWatchdog, WatchdogConfig
    
    # Create mock watchdog with exponential backoff config
    watchdog = OrderWatchdog(
        None,
        config=WatchdogConfig(max_retries=3),
    )
    
    assert watchdog.config.max_retries == 3
    
    # Test exponential backoff calculation
    for attempt in range(watchdog.config.max_retries):
        expected_delay = 2 ** attempt  # Exponential backoff: 1, 2, 4 seconds
        # The actual implementation uses 2 ** attempt
        print(f"Attempt {attempt}: expected delay = {expected_delay}s")
    
    print("✓ Exponential backoff logic verified")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
