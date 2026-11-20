"""CONN-01, the rest of the lifecycle: credential currency, failure cleanup, shutdown drain.

WHAT THIS FILE ADDS TO THE CONN-01 PROOF
----------------------------------------
``tests/test_conn01_rotation_evicts_pooled_client.py`` proves the ROUTER evicts on a key
rotation. That closed the finding as written. Tracing the rest of the connection lifecycle
for Phase 10 found three more behaviours on the same pool, all measured before any fix in
``staging/p10c_probe_before.txt``:

1. A CREDENTIAL-LESS CALLER DECIDED THE CLIENT FOR EVERYONE. ``core/consistency_checker.py``
   and ``backend/exchange_websocket_listener.py`` call ``get_or_create_exchange`` with no
   credentials at all. Whichever caller populated the pool key first won, so if one of those
   won the race an UNAUTHENTICATED client was pooled and then handed to ``routers/orders.py``
   and ``routers/market.py``, whose private calls cannot work on it. Restart was the only cure.
   This is the defect the rotation fix did NOT cover: no rotation happened, and no router was
   involved.

2. NOTHING CLOSED A POOLED CLIENT AT SHUTDOWN. ``main.py``'s lifespan stopped every service
   that opens connections and closed none of the connections. Every pooled ``ccxt`` client —
   open socket, plaintext credential — survived until the process died.

3. ``POST /connections/{id}/reconnect`` ANSWERED ``RECONNECTED`` WITHOUT RECONNECTING. It only
   ran a connectivity test against a throwaway client; the pooled client the trader pressed the
   button *because of* was still there afterwards.

THE BOUNDARY, UNCHANGED FROM ENV-01
-----------------------------------
The real ``get_or_create_exchange``, ``release_exchange``, ``close_all_exchanges`` and
``ConnectionEngine.connect()`` all run. Only the venue CLIENT CLASS is replaced, by an
in-process transport that receives the same assembled config dict — so which credential
reached which client is OBSERVABLE rather than asserted. No network, no real credential, no
live order. These tests say nothing about whether a venue would accept any key.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import ClassVar, List, Optional

import pytest


class _ControlledVenueClient:
    """In-process stand-in for a ``ccxt.pro`` client. Records the config it was built with."""

    raise_on_load_markets: ClassVar[Optional[BaseException]] = None
    raise_on_close: ClassVar[Optional[BaseException]] = None
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
        if self.raise_on_close is not None:
            raise self.raise_on_close

    def set_sandbox_mode(self, enabled: bool):
        pass


@pytest.fixture
def controlled_venue(monkeypatch):
    """Point ``connection_engine`` at the controlled transport and isolate the global pool.

    Both module globals are snapshotted and restored. ``_pool_credential_id`` is new in the
    CONN-01 fix and must be cleaned up alongside ``_exchange_pool``: a credential identity left
    behind for an evicted entry would make a later test's comparison depend on execution order,
    which is the defect class Phase 6 already found in this suite once.
    """
    from backend_app.backend import connection_engine as ce

    saved_pool = dict(ce._exchange_pool)
    saved_ids = dict(ce._pool_credential_id)
    # `_pool_lock` is a module-global `asyncio.Lock`, and `Lock.acquire` binds itself to the
    # running loop the FIRST time it has to wait. pytest-asyncio gives each test a fresh loop,
    # so the first test anywhere in the suite that CONTENDS on this lock binds it, and every
    # later contending test on a different loop raises
    # "RuntimeError: <Lock> is bound to a different event loop". That made the outcome depend
    # on which file ran first — the order-dependent class Phase 6 already found here once. A
    # fresh, unbound lock per test removes the dependency. Production has one loop for the
    # process lifetime, so this is a test-isolation concern only.
    saved_lock = ce._pool_lock
    ce._pool_lock = asyncio.Lock()
    ce._exchange_pool.clear()
    ce._pool_credential_id.clear()
    _ControlledVenueClient.instances = []
    _ControlledVenueClient.raise_on_load_markets = None
    _ControlledVenueClient.raise_on_close = None

    monkeypatch.setattr(
        ce,
        "ccxt",
        SimpleNamespace(
            binance=_ControlledVenueClient,
            kraken=_ControlledVenueClient,
            coinbase=_ControlledVenueClient,
            exchanges=["binance", "kraken", "coinbase"],
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
        _ControlledVenueClient.raise_on_close = None


# ══════════════════════════════════════════════════════════════════════════════════════════
# 1. Credential currency at the pool primitive
# ══════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_credentialed_caller_is_never_served_an_unauthenticated_pooled_client(
    controlled_venue,
):
    """The measured defect, inverted.

    ``consistency_checker`` pools a credential-less client; ``routers/orders.py`` then asks for
    the same pair WITH the vault credential because it needs private calls. It must get a client
    carrying that credential, not the unauthenticated one that happened to arrive first.
    """
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange,
                                                       release_exchange)

    # A background service with no credentials wins the race.
    unauth = await get_or_create_exchange("user_x", "binance")
    assert unauth.apiKey is None
    assert len(_exchange_pool) == 1

    # The credentialed caller must NOT be handed that client.
    served = await get_or_create_exchange("user_x", "binance", "real-key", "real-secret")

    assert served is not unauth, (
        "a credentialed caller was served the unauthenticated client a background service pooled"
    )
    assert served.apiKey == "real-key"
    assert served.secret == "real-secret"
    # The superseded client was closed, not leaked, and the pool still holds exactly one entry
    # for the pair -- the rebuild replaces, it does not accumulate.
    assert unauth.close_calls == 1
    assert len(_exchange_pool) == 1

    await release_exchange("user_x", "binance")


@pytest.mark.asyncio
async def test_a_credential_less_caller_reuses_an_authenticated_client_and_does_not_downgrade_it(
    controlled_venue,
):
    """The other direction, which is what keeps the rule from thrashing.

    A caller that supplies no credential is asking for "a client for this venue". It must reuse
    whatever is pooled rather than tearing down an authenticated client it did not build. If it
    rebuilt, two alternating callers -- which is exactly the real workload, a REST router and a
    background listener on the same pair -- would close and re-handshake the venue on every
    single call.
    """
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange,
                                                       release_exchange)

    authed = await get_or_create_exchange("user_y", "binance", "key-y", "secret-y")
    assert authed.apiKey == "key-y"

    # The background service arrives second, with nothing.
    reused = await get_or_create_exchange("user_y", "binance")

    assert reused is authed, "a credential-less caller must not rebuild an authenticated client"
    assert reused.apiKey == "key-y"
    assert len(controlled_venue.instances) == 1, "exactly one handshake, not two"
    assert authed.close_calls == 0
    assert len(_exchange_pool) == 1

    await release_exchange("user_y", "binance")


@pytest.mark.asyncio
async def test_alternating_credentialed_and_credential_less_callers_do_not_thrash(
    controlled_venue,
):
    """The anti-thrash property, measured rather than argued.

    Ten alternating calls -- the REST router with a credential, the listener without -- must
    settle on ONE client after the initial build. A rule that rebuilt in both directions would
    construct ten.
    """
    from backend_app.backend.connection_engine import (get_or_create_exchange,
                                                       release_exchange)

    for i in range(10):
        if i % 2 == 0:
            await get_or_create_exchange("user_z", "binance", "key-z", "secret-z")
        else:
            await get_or_create_exchange("user_z", "binance")

    assert len(controlled_venue.instances) == 1, (
        f"alternating callers rebuilt the connection {len(controlled_venue.instances)} times; "
        f"the credential-less branch must not invalidate an authenticated client"
    )

    await release_exchange("user_z", "binance")


@pytest.mark.asyncio
async def test_a_rotation_takes_effect_at_the_pool_even_without_the_router(controlled_venue):
    """Defence in depth for CONN-01: the primitive is now self-correcting.

    The router's ``release_exchange`` in ``store_keys`` is still there and still tested. This
    asserts the pool no longer DEPENDS on it: a second path that stores credentials and forgets
    to evict cannot resurrect the finding, because a changed credential rebuilds here.
    """
    from backend_app.backend.connection_engine import (get_or_create_exchange,
                                                       release_exchange)

    old = await get_or_create_exchange("user_rot2", "binance", "old-key", "old-secret")
    new = await get_or_create_exchange("user_rot2", "binance", "NEW-key", "NEW-secret")

    assert new is not old
    assert new.apiKey == "NEW-key" and new.secret == "NEW-secret"
    assert old.close_calls == 1, "the superseded client must be closed, not dropped"

    await release_exchange("user_rot2", "binance")


@pytest.mark.asyncio
async def test_the_same_credential_is_still_reused_so_one_pair_keeps_one_socket(
    controlled_venue,
):
    """FIX CE-4 is not weakened by any of the above.

    Three bots for one user on one venue, all with the same stored credential, share ONE client
    and ONE handshake. That is the property the pool exists for, and a credential comparison
    that was too eager would have broken it.
    """
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange,
                                                       release_exchange)

    a = await get_or_create_exchange("user_pool", "binance", "k", "s")
    b = await get_or_create_exchange("user_pool", "binance", "k", "s")
    c = await get_or_create_exchange("user_pool", "binance", "k", "s")

    assert a is b is c
    assert len(controlled_venue.instances) == 1
    assert a.load_markets_calls == 1, "one handshake for three callers"
    assert len(_exchange_pool) == 1

    await release_exchange("user_pool", "binance")


@pytest.mark.asyncio
async def test_a_password_change_alone_rebuilds_the_connection(controlled_venue):
    """The fingerprint covers the whole credential triple, not just the api key.

    Venues like OKX and KuCoin require a passphrase alongside the key. A rotation that changes
    only the passphrase is still a rotation, and a client built with the old one is still stale.
    """
    from backend_app.backend.connection_engine import (get_or_create_exchange,
                                                       release_exchange)

    first = await get_or_create_exchange("user_pw", "coinbase", "k", "s", password="old-pass")
    assert first.password == "old-pass"

    second = await get_or_create_exchange("user_pw", "coinbase", "k", "s", password="new-pass")

    assert second is not first, "a passphrase-only rotation must rebuild"
    assert second.password == "new-pass"
    assert first.close_calls == 1

    await release_exchange("user_pw", "coinbase")


# ══════════════════════════════════════════════════════════════════════════════════════════
# 2. Tenant and account isolation, under the new rule
# ══════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_two_tenants_never_share_a_client_even_with_identical_credentials(
    controlled_venue,
):
    """Isolation is a property of the POOL KEY and survives the credential comparison.

    The adversarial case for a credential-aware pool: two users whose credential fingerprints
    are IDENTICAL. If the credential had been made part of the key, or if the comparison were
    consulted before the key, these two would collide and one tenant would be handed the other's
    authenticated socket. They must get separate clients.
    """
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange,
                                                       release_exchange)

    a = await get_or_create_exchange("tenant_a", "binance", "same-key", "same-secret")
    b = await get_or_create_exchange("tenant_b", "binance", "same-key", "same-secret")

    assert a is not b, "two tenants were served one client"
    assert len(_exchange_pool) == 2
    assert len(controlled_venue.instances) == 2

    await release_exchange("tenant_a", "binance")
    await release_exchange("tenant_b", "binance")


@pytest.mark.asyncio
async def test_one_tenants_rotation_does_not_evict_another_tenants_client(controlled_venue):
    """A rebuild is scoped to the pair that rotated.

    The rebuild path pops and closes. If it were keyed loosely -- on the venue, say, rather than
    the (user, venue) pair -- one customer rotating their Binance keys would drop every other
    customer's Binance connection, which on a live account is an outage caused by a neighbour.
    """
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange,
                                                       release_exchange)

    victim = await get_or_create_exchange("tenant_v", "binance", "v-key", "v-secret")
    rotator_old = await get_or_create_exchange("tenant_r", "binance", "r-key", "r-secret")

    # tenant_r rotates.
    rotator_new = await get_or_create_exchange("tenant_r", "binance", "r-key-2", "r-secret-2")

    assert rotator_new is not rotator_old
    assert rotator_old.close_calls == 1
    # The neighbour is untouched: same object, still open, still its own credential.
    assert victim.close_calls == 0, "another tenant's client was closed by this rotation"
    assert (await get_or_create_exchange("tenant_v", "binance", "v-key", "v-secret")) is victim
    assert victim.apiKey == "v-key"
    assert len(_exchange_pool) == 2

    await release_exchange("tenant_v", "binance")
    await release_exchange("tenant_r", "binance")


@pytest.mark.asyncio
async def test_the_pool_key_is_still_the_user_exchange_pair(controlled_venue):
    """The key derivation is unchanged by the CONN-01 fix, and that is deliberate.

    Re-keying the pool on the credential would let one (user, venue) pair hold several
    simultaneous authenticated sockets, defeating FIX CE-4. The fix tracks the credential
    ALONGSIDE the entry and rebuilds in place instead. This pins the key so a future change to
    it is a deliberate act.
    """
    import hashlib
    import json

    from backend_app.backend.connection_engine import (_exchange_pool, _pool_key,
                                                       get_or_create_exchange,
                                                       release_exchange)

    expected = hashlib.sha256(
        json.dumps(["user_k", "binance"], sort_keys=True).encode()
    ).hexdigest()
    assert _pool_key("user_k", "binance") == expected

    await get_or_create_exchange("user_k", "binance", "k", "s")
    assert expected in _exchange_pool, "the pooled entry is not under the (user, venue) key"

    await release_exchange("user_k", "binance")


@pytest.mark.asyncio
async def test_the_credential_fingerprint_is_not_the_credential(controlled_venue):
    """The pool's bookkeeping must not be a second copy of the secret.

    ``_pool_credential_id`` is iterated, counted and (by key) logged during a drain. The
    credential itself must not be recoverable from it, and must not appear in it.
    """
    from backend_app.backend.connection_engine import (_credential_identity,
                                                       _pool_credential_id,
                                                       get_or_create_exchange,
                                                       release_exchange)

    secret = "SUPER-SECRET-VALUE"
    await get_or_create_exchange("user_f", "binance", "API-KEY-VALUE", secret)

    stored = list(_pool_credential_id.values())
    assert stored and stored[0] is not None
    for value in stored:
        assert secret not in value
        assert "API-KEY-VALUE" not in value
        # A sha256 hexdigest, not a credential.
        assert len(value) == 64 and all(c in "0123456789abcdef" for c in value)

    # No credential means no fingerprint at all, which is a distinct state from "some digest".
    assert _credential_identity(None, None, None) is None
    assert _credential_identity("", "", None) is None
    # And the same credential always fingerprints the same, or reuse would never happen.
    assert _credential_identity("k", "s", None) == _credential_identity("k", "s", None)
    assert _credential_identity("k", "s", None) != _credential_identity("k", "s2", None)

    await release_exchange("user_f", "binance")


# ══════════════════════════════════════════════════════════════════════════════════════════
# 3. Failure cleanup
# ══════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_failed_rebuild_leaves_no_pool_entry_and_no_stale_client(controlled_venue):
    """The dangerous interleaving in the rebuild path, measured.

    The rebuild pops the old client BEFORE constructing the new one. If the new handshake then
    fails, the pool must be left EMPTY for that pair -- not holding the closed old client, and
    not holding a dead new one. Either would be served to the next caller, and the only cure
    would be a restart.
    """
    import ccxt as ccxt_base

    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       _pool_credential_id,
                                                       get_or_create_exchange)

    old = await get_or_create_exchange("user_fail2", "binance", "old-key", "old-secret")
    assert len(_exchange_pool) == 1

    # The venue now refuses. The caller presents a NEW credential, so a rebuild is attempted.
    controlled_venue.raise_on_load_markets = ccxt_base.AuthenticationError(
        'binance {"code":-2008,"msg":"Invalid Api-Key ID."}'
    )
    with pytest.raises(ccxt_base.AuthenticationError):
        await get_or_create_exchange("user_fail2", "binance", "NEW-key", "NEW-secret")

    assert _exchange_pool == {}, "a failed rebuild left an entry behind"
    assert _pool_credential_id == {}, "a failed rebuild left a credential identity behind"
    # The old client was closed by the rebuild, and the new one by connect()'s own teardown.
    assert old.close_calls == 1
    assert controlled_venue.instances[-1].close_calls == 1, "the failed client was leaked"

    # Not sticky: once the venue accepts, the pair recovers without a restart.
    controlled_venue.raise_on_load_markets = None
    recovered = await get_or_create_exchange("user_fail2", "binance", "NEW-key", "NEW-secret")
    assert recovered.apiKey == "NEW-key"
    assert len(_exchange_pool) == 1


@pytest.mark.asyncio
async def test_a_close_that_raises_during_a_rebuild_still_produces_a_working_client(
    controlled_venue,
):
    """A venue that cannot be closed cleanly must not block the rotation.

    ``ccxt`` clients do raise on close in the wild (a half-open socket, a cancelled task). The
    rebuild must treat the close as best-effort: the caller asked for a connection with their
    new credential, and failing that request because the OLD socket objected would leave the
    stale credential in service -- the exact outcome CONN-01 describes.
    """
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange,
                                                       release_exchange)

    old = await get_or_create_exchange("user_badclose", "binance", "old-key", "old-secret")
    _ControlledVenueClient.raise_on_close = RuntimeError("socket already gone")
    try:
        new = await get_or_create_exchange(
            "user_badclose", "binance", "NEW-key", "NEW-secret"
        )
        assert new is not old
        assert new.apiKey == "NEW-key"
        assert len(_exchange_pool) == 1, "the pool must hold exactly the new client"
    finally:
        _ControlledVenueClient.raise_on_close = None
        await release_exchange("user_badclose", "binance")


@pytest.mark.asyncio
async def test_concurrent_callers_with_the_same_new_credential_rebuild_exactly_once(
    controlled_venue, monkeypatch
):
    """The rebuild happens inside ``_pool_lock``, so a rotation is not a thundering herd.

    Eight bots for one pair all notice the new credential at once. Without the lock covering the
    comparison as well as the construction, each would see a mismatch, each would rebuild, and
    seven sockets would leak while the last writer won.
    """
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange,
                                                       release_exchange)

    old = await get_or_create_exchange("user_herd", "binance", "old-key", "old-secret")
    before = len(controlled_venue.instances)

    async def slow_load_markets(self, *_a, **_k):
        self.load_markets_calls += 1
        await asyncio.sleep(0.01)  # a real suspension inside the critical section
        return self.markets

    monkeypatch.setattr(controlled_venue, "load_markets", slow_load_markets)

    results = await asyncio.gather(
        *[
            get_or_create_exchange("user_herd", "binance", "NEW-key", "NEW-secret")
            for _ in range(8)
        ]
    )

    assert len({id(r) for r in results}) == 1, "concurrent callers got different clients"
    assert results[0].apiKey == "NEW-key"
    assert len(controlled_venue.instances) - before == 1, (
        "the rotation constructed "
        f"{len(controlled_venue.instances) - before} clients; it must construct exactly one"
    )
    assert old.close_calls == 1, "the superseded client must be closed exactly once"
    assert len(_exchange_pool) == 1

    await release_exchange("user_herd", "binance")


# ══════════════════════════════════════════════════════════════════════════════════════════
# 4. Shutdown drain and boundedness
# ══════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_close_all_exchanges_closes_and_empties_the_pool(controlled_venue):
    """The drain that did not exist. Every pooled client is closed and the pool is emptied."""
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       _pool_credential_id,
                                                       close_all_exchanges,
                                                       exchange_pool_size,
                                                       get_or_create_exchange)

    for uid in ("u1", "u2", "u3"):
        await get_or_create_exchange(uid, "binance", f"k-{uid}", f"s-{uid}")
    await get_or_create_exchange("u1", "kraken", "k-u1", "s-u1")

    assert exchange_pool_size() == 4
    clients = list(controlled_venue.instances)
    assert all(c.close_calls == 0 for c in clients)

    closed = await close_all_exchanges()

    assert closed == 4
    assert exchange_pool_size() == 0
    assert _exchange_pool == {}
    assert _pool_credential_id == {}, "the drain left credential identities behind"
    assert all(c.close_calls == 1 for c in clients), "a pooled client survived the drain"


@pytest.mark.asyncio
async def test_the_drain_closes_every_client_even_when_one_refuses(controlled_venue):
    """One venue refusing to close must not strand the others.

    A drain that stopped at the first exception would leave the remaining sockets open, which is
    the behaviour the drain exists to prevent -- just later in the list.
    """
    from backend_app.backend.connection_engine import (close_all_exchanges,
                                                       exchange_pool_size,
                                                       get_or_create_exchange)

    for uid in ("a", "b", "c"):
        await get_or_create_exchange(uid, "binance", f"k-{uid}", f"s-{uid}")
    clients = list(controlled_venue.instances)

    # The middle client raises on close.
    async def _bad_close(self=clients[1]):
        self.close_calls += 1
        raise RuntimeError("venue refused to close")

    clients[1].close = _bad_close

    closed = await close_all_exchanges()

    assert exchange_pool_size() == 0, "the pool must be emptied even when a close raises"
    assert closed == 2, f"two clean closes expected, got {closed}"
    # Every client was ATTEMPTED, which is the property that matters.
    assert all(c.close_calls == 1 for c in clients)


@pytest.mark.asyncio
async def test_the_drain_is_a_no_op_on_an_empty_pool(controlled_venue):
    """Shutdown must not fail when no connection was ever opened -- the paper-only case.

    With live trading off, a process can run its whole life without constructing a venue
    adapter. The drain runs on that path too, so it has to be harmless there.
    """
    from backend_app.backend.connection_engine import (close_all_exchanges,
                                                       exchange_pool_size)

    assert exchange_pool_size() == 0
    assert await close_all_exchanges() == 0
    assert exchange_pool_size() == 0


def test_the_lifespan_shutdown_drains_the_pool():
    """The drain is WIRED, not merely available.

    A close-all nobody calls is the shape of this repository's other recurring finding -- a
    control that exists and has zero call sites (``validate_safety_config_integrity``,
    ``validate_production_config``, ``HardQuotaEnforcer``). So this asserts the lifespan
    actually calls it, by reading ``main.py``'s shutdown half rather than by importing and
    running the whole application.
    """
    import pathlib

    src = pathlib.Path("backend_app/main.py").read_text(encoding="utf-8", errors="replace")
    assert "close_all_exchanges" in src, (
        "backend_app/main.py does not call close_all_exchanges; the pool is not drained at "
        "shutdown and every pooled venue client leaks at process exit (CONN-01)"
    )

    # And on the SHUTDOWN side of the lifespan, not at startup.
    marker = "yield"
    assert marker in src
    shutdown_half = src.split(marker, 1)[1]
    assert "close_all_exchanges" in shutdown_half, (
        "close_all_exchanges is called before the lifespan's `yield`, i.e. at startup rather "
        "than at shutdown"
    )

    # After the bots are stopped: closing sockets out from under an in-flight order is worse
    # than leaking them.
    assert shutdown_half.index("fleet.shutdown_all") < shutdown_half.index(
        "close_all_exchanges"
    ), "the pool is drained before the bot fleet is stopped"


@pytest.mark.asyncio
async def test_the_pool_is_bounded_by_distinct_pairs_not_by_callers(controlled_venue):
    """Connection counts stay bounded: the pool cannot grow per request.

    Fifty calls across three (user, venue) pairs -- including rotations and failed handshakes --
    must leave exactly three entries. Rotations REPLACE, a refused handshake adds NOTHING, and
    repeat callers share. This is the boundedness property; there is no age- or idleness-based
    eviction, which is recorded as a residual risk rather than given an invented ceiling.
    """
    import ccxt as ccxt_base

    from backend_app.backend.connection_engine import (exchange_pool_size,
                                                       get_or_create_exchange)

    pairs = [("u1", "binance"), ("u2", "binance"), ("u1", "kraken")]

    for i in range(50):
        user, venue = pairs[i % len(pairs)]
        # Every tenth call rotates the credential for that pair.
        gen = i // 10
        await get_or_create_exchange(user, venue, f"key-{user}-{gen}", f"sec-{user}-{gen}")

    assert exchange_pool_size() == len(pairs), (
        f"50 calls over {len(pairs)} pairs produced {exchange_pool_size()} pool entries"
    )

    # A refused handshake for a NEW pair adds nothing.
    controlled_venue.raise_on_load_markets = ccxt_base.AuthenticationError("refused")
    with pytest.raises(ccxt_base.AuthenticationError):
        await get_or_create_exchange("u3", "binance", "k", "s")
    assert exchange_pool_size() == len(pairs), "a refused handshake grew the pool"


# ══════════════════════════════════════════════════════════════════════════════════════════
# 5. The /reconnect endpoint
# ══════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_reconnect_endpoint_evicts_the_pooled_client(controlled_venue, monkeypatch):
    """``POST /connections/{id}/reconnect`` must actually reconnect.

    It answered ``{"status": "RECONNECTED"}`` while leaving the pooled client untouched, so the
    one situation the endpoint exists for -- a stale or broken pooled connection -- was the one
    situation it could not repair. The real handler runs here, with the connectivity test it
    delegates to stubbed out: what is being measured is the eviction, not the test's verdict.
    """
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange)
    from backend_app.routers import exchange as xr

    stale = await get_or_create_exchange("user_rc", "binance", "k", "s")
    assert len(_exchange_pool) == 1

    async def _fake_test(body, user, vault):
        return {"status": "CONNECTED", "exchange_id": body.exchange_id}

    monkeypatch.setattr(xr, "test_stored_connection", _fake_test)

    result = await xr.reconnect_connection_by_id(
        exchange_id="binance", user={"id": "user_rc"}, vault=object()
    )

    assert result["status"] == "RECONNECTED"
    assert _exchange_pool == {}, "reconnect did not evict the pooled client"
    assert stale.close_calls == 1, "reconnect did not close the stale socket"

    # And the next caller gets a genuinely new client.
    fresh = await get_or_create_exchange("user_rc", "binance", "k", "s")
    assert fresh is not stale


@pytest.mark.asyncio
async def test_reconnect_still_reports_when_the_venue_is_down_after_evicting(
    controlled_venue, monkeypatch
):
    """Eviction happens BEFORE the connectivity test, so a failing venue still clears the socket.

    If eviction came after, a caller whose venue is refusing -- the likeliest reason to press
    reconnect -- would get an error AND keep the dead pooled client.
    """
    from backend_app.backend.connection_engine import (_exchange_pool,
                                                       get_or_create_exchange)
    from backend_app.routers import exchange as xr

    stale = await get_or_create_exchange("user_rc2", "binance", "k", "s")

    async def _failing_test(body, user, vault):
        raise RuntimeError("venue unreachable")

    monkeypatch.setattr(xr, "test_stored_connection", _failing_test)

    with pytest.raises(RuntimeError):
        await xr.reconnect_connection_by_id(
            exchange_id="binance", user={"id": "user_rc2"}, vault=object()
        )

    assert _exchange_pool == {}, "a failing reconnect left the stale pooled client in place"
    assert stale.close_calls == 1


# ══════════════════════════════════════════════════════════════════════════════════════════
# 6. Negative controls -- these tests must be able to fail
# ══════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_credential_comparison_is_what_makes_these_tests_pass(controlled_venue):
    """Deliberately break the enforcement path and confirm this suite notices.

    Reverting ``_credential_identity`` to a constant is exactly the regression that reintroduces
    CONN-01: every credential then looks identical, so a rotation is served the stale client.
    A suite that still passed under that substitution would be measuring nothing.
    """
    from backend_app.backend import connection_engine as ce

    original = ce._credential_identity
    ce._credential_identity = lambda *_a, **_k: "always-the-same"
    try:
        old = await ce.get_or_create_exchange("user_neg", "binance", "old-key", "old-secret")
        after = await ce.get_or_create_exchange("user_neg", "binance", "NEW-key", "NEW-secret")
        # With enforcement broken, the stale client comes back -- the original defect.
        assert after is old
        assert after.apiKey == "old-key", (
            "the sabotage did not reproduce the defect, so the probe itself is wrong"
        )
    finally:
        ce._credential_identity = original
        await ce.release_exchange("user_neg", "binance")

    # And with it restored, the same sequence is correct again.
    old2 = await ce.get_or_create_exchange("user_neg2", "binance", "old-key", "old-secret")
    after2 = await ce.get_or_create_exchange("user_neg2", "binance", "NEW-key", "NEW-secret")
    assert after2 is not old2 and after2.apiKey == "NEW-key"
    await ce.release_exchange("user_neg2", "binance")


# ══════════════════════════════════════════════════════════════════════════════════════════
# 7. Diagnostics must not carry credentials
# ══════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_no_credential_appears_in_pool_log_output(controlled_venue, caplog):
    """Every log line the pool emits is checked against the credentials in play.

    The pool logs on create, on rebuild, on release and on drain, and the rebuild path is new.
    A convenient ``f"...{config}"`` added to any of them would print ``apiKey`` and ``secret``
    verbatim into the operator's log sink.
    """
    import logging

    from backend_app.backend.connection_engine import (close_all_exchanges,
                                                       get_or_create_exchange,
                                                       release_exchange)

    secrets = ["LEAK-API-KEY", "LEAK-SECRET", "LEAK-PASSPHRASE",
               "LEAK-API-KEY-2", "LEAK-SECRET-2"]

    with caplog.at_level(logging.DEBUG):
        # create, rebuild, release and drain -- every path that logs.
        await get_or_create_exchange(
            "user_log", "coinbase", "LEAK-API-KEY", "LEAK-SECRET", password="LEAK-PASSPHRASE"
        )
        await get_or_create_exchange("user_log", "coinbase", "LEAK-API-KEY-2", "LEAK-SECRET-2")
        await release_exchange("user_log", "coinbase")
        await get_or_create_exchange("user_log2", "binance", "LEAK-API-KEY", "LEAK-SECRET")
        await close_all_exchanges()

    assert caplog.records, "no log output was captured, so this test proves nothing"
    blob = "\n".join(r.getMessage() for r in caplog.records)
    for secret in secrets:
        assert secret not in blob, (
            f"{secret!r} appeared in log output emitted by the connection pool:\n{blob}"
        )

    # The credential FINGERPRINT must not be logged either: it is a verifier for a guessed key.
    from backend_app.backend.connection_engine import _credential_identity
    fingerprint = _credential_identity("LEAK-API-KEY", "LEAK-SECRET", "LEAK-PASSPHRASE")
    assert fingerprint not in blob, "the credential fingerprint was logged"


@pytest.mark.asyncio
async def test_a_failed_rebuild_does_not_put_the_credential_in_the_exception(controlled_venue):
    """The exception a caller sees from a refused rebuild must not carry the credential.

    The rebuild path constructs a client from the caller's key and lets ``connect()`` raise. A
    ``ccxt`` message can echo a request, so this pins that the credential itself is not in the
    string that propagates.
    """
    import ccxt as ccxt_base

    from backend_app.backend.connection_engine import get_or_create_exchange

    await get_or_create_exchange("user_exc", "binance", "old-key", "old-secret")
    controlled_venue.raise_on_load_markets = ccxt_base.AuthenticationError(
        'binance {"code":-2008,"msg":"Invalid Api-Key ID."}'
    )

    with pytest.raises(ccxt_base.AuthenticationError) as excinfo:
        await get_or_create_exchange(
            "user_exc", "binance", "LEAK-ROTATED-KEY", "LEAK-ROTATED-SECRET"
        )

    text = str(excinfo.value)
    assert "LEAK-ROTATED-KEY" not in text
    assert "LEAK-ROTATED-SECRET" not in text
