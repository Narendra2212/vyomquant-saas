"""EXCH-01 — sandbox status must be PROVEN from endpoint configuration, not announced.

WHAT WAS WRONG
--------------
`CCXTExchangeExecutor.connect()` established "sandbox" by setting a flag and logging it:

    if self.sandbox:
        config["sandbox"] = True
        config["options"] = {"sandbox": True}
        logger.info(f"Connecting to {self.exchange_id} in SANDBOX mode")

Nothing checked where the client would actually send. For bitget the log was false: ccxt's
bitget `set_sandbox_mode` sets only `options['sandboxMode']` and bitget publishes no
`urls['test']`, so the client stayed on production while every log line said sandbox.

And bitget would ALSO have passed the obvious fix — "check that `set_sandbox_mode()` did
not raise" — because it does not raise. That is the premise these tests encode:
**absence of an exception is not evidence of routing.** Verification requires positive
endpoint evidence.

WHAT THESE TESTS ARE, AND ARE NOT
---------------------------------
Unit tests over deterministic fake clients. They prove the VERIFIER's logic and the
connect-path invariant. They are **not** venue sandbox certification: no venue is
contacted, no credential is used, and passing them certifies nothing about any exchange.
Actual sandbox certification needs a testnet credential and a venue round trip, and is
tracked in `staging/EXCHANGE_SANDBOX_CERTIFICATION.md` — where it remains at 0 venues.

NO NETWORK. NO CREDENTIALS. NO ORDERS. Every client here is a local object with a `urls`
dict and a `headers` dict.
"""
from __future__ import annotations

import asyncio

import pytest

from backend_app.backend.exchange_executor import (SANDBOX_MECHANISMS,
                                                   CCXTExchangeExecutor,
                                                   ExchangeExecutorFactory,
                                                   ExchangeRoutingBlocked,
                                                   SandboxMechanism,
                                                   SandboxVerificationFailed,
                                                   assert_sandbox_supported,
                                                   verify_sandbox_endpoint)

APPROVED = ["binance", "bybit", "coinbase", "kraken", "okx", "kucoin"]
FAKE = {"api_key": "not-a-real-key", "api_secret": "not-a-real-secret"}


class FakeClient:
    """A ccxt-shaped client: just the two attributes the verifier reads.

    Deliberately minimal. The verifier must work from `urls` and `headers` alone, and a
    fake that offered more would let a dependency on something else go unnoticed.
    """

    def __init__(self, urls: dict | None = None, headers: dict | None = None):
        self.urls = dict(urls or {})
        self.headers = dict(headers or {})
        self.closed = 0

    async def close(self):
        self.closed += 1


PROD = "https://api.example.com"
SBOX = "https://testnet.example.com"


def url_swapped_client() -> FakeClient:
    """A client as ccxt leaves it after a genuine URL swap."""
    return FakeClient(urls={"api": SBOX, "test": SBOX, "apiBackup": PROD})


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 1 — a venue whose sandbox resolves to a known sandbox endpoint
# ══════════════════════════════════════════════════════════════════════════════════════

def test_url_swap_to_the_declared_sandbox_endpoint_is_verified():
    v = verify_sandbox_endpoint(url_swapped_client(), "binance")

    assert v.verified is True
    assert v.mechanism == SandboxMechanism.URL_SWAP.value
    assert v.endpoint != v.production_endpoint
    assert "apiBackup" in v.detail


def test_header_mechanism_is_verified_even_though_the_url_is_unchanged():
    """OKX shares its production host and selects demo by header.

    A URL-only rule would reject OKX, so this is the case that forces the verifier to be
    venue-aware rather than applying one comparison everywhere.
    """
    client = FakeClient(
        urls={"api": PROD, "test": PROD, "apiBackup": PROD},
        headers={"x-simulated-trading": "1"},
    )
    v = verify_sandbox_endpoint(client, "okx")

    assert v.verified is True
    assert v.mechanism == SandboxMechanism.HEADER.value
    assert v.endpoint == v.production_endpoint  # expected for this venue


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 2 — the sandbox switch is ineffective and production remains configured
# ══════════════════════════════════════════════════════════════════════════════════════

def test_an_ineffective_switch_that_leaves_production_configured_is_refused():
    """The EXCH-01 condition. `options['sandboxMode']` set, endpoint untouched.

    This client is exactly what ccxt produces for bitget, and it is the case that passes
    a "did set_sandbox_mode raise?" check. The verifier must refuse it.
    """
    client = FakeClient(urls={"api": PROD})  # no apiBackup, no test, no header

    with pytest.raises(SandboxVerificationFailed) as caught:
        verify_sandbox_endpoint(client, "binance")

    assert "apiBackup" in str(caught.value)
    assert caught.value.error_code == "SANDBOX_UNVERIFIED"
    assert caught.value.retryable is False


def test_a_swap_that_lands_back_on_production_is_refused():
    """`apiBackup` present but the active endpoint equals production."""
    client = FakeClient(urls={"api": PROD, "test": PROD, "apiBackup": PROD})

    with pytest.raises(SandboxVerificationFailed) as caught:
        verify_sandbox_endpoint(client, "binance")
    assert "identical to the production endpoint" in str(caught.value)


def test_a_missing_header_is_refused_for_a_header_venue():
    client = FakeClient(urls={"api": PROD, "test": PROD, "apiBackup": PROD}, headers={})

    with pytest.raises(SandboxVerificationFailed) as caught:
        verify_sandbox_endpoint(client, "okx")
    assert "x-simulated-trading" in str(caught.value)


def test_a_header_set_to_a_non_demo_value_is_refused():
    """Presence is not enough. `x-simulated-trading: 0` is a LIVE connection.

    A presence-only check would accept a client wearing a sandbox label while trading
    live — the same class of error as trusting the log line.
    """
    client = FakeClient(
        urls={"api": PROD, "test": PROD, "apiBackup": PROD},
        headers={"x-simulated-trading": "0"},
    )
    with pytest.raises(SandboxVerificationFailed) as caught:
        verify_sandbox_endpoint(client, "okx")
    assert "does not select the demo environment" in str(caught.value)


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 3 — unknown or ambiguous endpoint configuration
# ══════════════════════════════════════════════════════════════════════════════════════

def test_an_unmeasured_venue_is_refused_rather_than_guessed():
    """A venue absent from `SANDBOX_MECHANISMS` has no declaration to verify against.

    Fail-closed for anything new: adding a venue must be a deliberate act with a
    measurement behind it, never an accident of a generic rule.
    """
    with pytest.raises(SandboxVerificationFailed) as caught:
        verify_sandbox_endpoint(url_swapped_client(), "mexc")
    assert "no sandbox mechanism has been measured" in str(caught.value)


def test_an_endpoint_that_is_neither_production_nor_the_declared_sandbox_is_refused():
    """"Something changed" is not proof. It must be the DECLARED sandbox endpoint."""
    client = FakeClient(
        urls={"api": "https://somewhere-else.example.com", "test": SBOX, "apiBackup": PROD}
    )
    with pytest.raises(SandboxVerificationFailed) as caught:
        verify_sandbox_endpoint(client, "binance")
    assert "does not match the venue's declared sandbox endpoint" in str(caught.value)


def test_a_venue_declaring_no_test_url_is_refused_for_a_url_swap_mechanism():
    client = FakeClient(urls={"api": SBOX, "apiBackup": PROD})
    with pytest.raises(SandboxVerificationFailed) as caught:
        verify_sandbox_endpoint(client, "binance")
    assert "declares no urls['test']" in str(caught.value)


def test_nested_dict_endpoints_compare_order_independently():
    """Several venues publish `urls['api']` as a dict of a dozen per-section hosts.

    Pinned because an order-sensitive comparison would make the verdict depend on dict
    ordering, which is not a property of the endpoint.
    """
    a = {"public": SBOX, "private": SBOX + "/p"}
    reordered = {"private": SBOX + "/p", "public": SBOX}
    client = FakeClient(urls={"api": reordered, "test": a, "apiBackup": {"public": PROD}})

    v = verify_sandbox_endpoint(client, "binance")
    assert v.verified is True


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 4 — a sandbox initialization exception
# ══════════════════════════════════════════════════════════════════════════════════════

def test_a_constructor_raise_during_sandbox_init_is_classified_not_leaked(monkeypatch):
    """ccxt raises `NotSupported` from the CONSTRUCTOR, outside connect()'s try.

    Before this change that escaped as a bare ccxt type, so callers written against this
    platform's `ExchangeError` did not catch it and the failure was reported as a generic
    connection fault. It is now a terminal `SandboxVerificationFailed`.
    """
    from backend_app.backend import exchange_executor as mod

    class Boom:
        def __init__(self, _config):
            raise RuntimeError("venue client blew up during sandbox init")

    monkeypatch.setattr(mod, "ccxt", type("M", (), {"binance": Boom}))
    monkeypatch.setattr(mod, "CCXT_AVAILABLE", True)

    ex = CCXTExchangeExecutor(exchange_id="binance", sandbox=True, **FAKE)
    with pytest.raises(SandboxVerificationFailed) as caught:
        asyncio.run(ex.connect())

    assert "could not be constructed in sandbox mode" in str(caught.value)
    assert ex.sandbox_verified is False


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 5 — an unsupported sandbox implementation
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("venue", ["coinbase", "kraken", "kucoin"])
def test_a_venue_with_no_sandbox_is_refused_before_a_client_is_built(venue):
    """Measured: these three raise `NotSupported` on the installed ccxt.

    Refused ahead of construction, so no client and no aiohttp session is allocated for
    a connection that cannot exist.
    """
    assert SANDBOX_MECHANISMS[venue][0] is SandboxMechanism.UNSUPPORTED

    with pytest.raises(SandboxVerificationFailed) as caught:
        assert_sandbox_supported(venue)
    assert "publishes no sandbox endpoint" in str(caught.value)

    with pytest.raises(SandboxVerificationFailed):
        verify_sandbox_endpoint(url_swapped_client(), venue)


def test_an_unsupported_venue_refuses_even_when_handed_a_plausible_client():
    """The declaration wins over the client's appearance.

    A kraken client that somehow presented a swapped URL must still be refused: kraken
    publishes no sandbox, so such a client would be addressing an endpoint nobody has
    established the identity of.
    """
    with pytest.raises(SandboxVerificationFailed):
        verify_sandbox_endpoint(url_swapped_client(), "kraken")


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 6 — an unverified connection cannot be marked sandbox-verified
# ══════════════════════════════════════════════════════════════════════════════════════

def test_a_fresh_executor_is_not_sandbox_verified():
    """`self.sandbox` is a REQUEST; `sandbox_verified` is EVIDENCE. Not the same."""
    ex = CCXTExchangeExecutor(exchange_id="binance", sandbox=True, **FAKE)

    assert ex.sandbox is True
    assert ex.sandbox_verified is False
    assert ex.sandbox_evidence is None


def test_a_refused_sandbox_connection_leaves_the_executor_unverified_and_closed(monkeypatch):
    """The whole invariant, end to end on the connect path.

    An ineffective sandbox switch must: refuse, leave `sandbox_verified` False, drop the
    client reference, and CLOSE the socket — an abandoned client leaks its session and
    keeps a decrypted credential reachable.
    """
    from backend_app.backend import exchange_executor as mod

    created: list[FakeClient] = []

    class ProductionOnly(FakeClient):
        def __init__(self, _config):
            super().__init__(urls={"api": PROD})  # the bitget shape
            created.append(self)

    monkeypatch.setattr(mod, "ccxt", type("M", (), {"binance": ProductionOnly}))
    monkeypatch.setattr(mod, "CCXT_AVAILABLE", True)

    ex = CCXTExchangeExecutor(exchange_id="binance", sandbox=True, **FAKE)
    with pytest.raises(SandboxVerificationFailed):
        asyncio.run(ex.connect())

    assert ex.sandbox_verified is False
    assert ex.sandbox_evidence is None
    assert ex._exchange is None, "the unverified client was retained"
    assert created and created[0].closed == 1, "the unverified client was not closed"


def test_a_verified_sandbox_connection_reports_its_evidence(monkeypatch):
    """The positive path: verification succeeds and the evidence is recorded.

    `load_markets` and `fetch_balance` are answered locally, so no network call occurs.
    """
    from backend_app.backend import exchange_executor as mod

    class Verified(FakeClient):
        def __init__(self, _config):
            super().__init__(urls={"api": SBOX, "test": SBOX, "apiBackup": PROD})

        async def load_markets(self, *_a, **_k):
            return {"BTC/USDT": {"symbol": "BTC/USDT"}}

        async def fetch_balance(self):
            return {"USDT": {"free": 0, "used": 0, "total": 0}}

    monkeypatch.setattr(mod, "ccxt", type("M", (), {"binance": Verified}))
    monkeypatch.setattr(mod, "CCXT_AVAILABLE", True)

    ex = CCXTExchangeExecutor(exchange_id="binance", sandbox=True, **FAKE)
    asyncio.run(ex.connect())

    assert ex.sandbox_verified is True
    assert ex.sandbox_evidence.mechanism == SandboxMechanism.URL_SWAP.value
    assert ex._connected is True


def test_live_mode_is_never_reported_as_sandbox_verified(monkeypatch):
    """A live connection must not acquire sandbox evidence by omission."""
    from backend_app.backend import exchange_executor as mod

    class Live(FakeClient):
        def __init__(self, _config):
            super().__init__(urls={"api": PROD})

        async def load_markets(self, *_a, **_k):
            return {}

        async def fetch_balance(self):
            return {}

    monkeypatch.setattr(mod, "ccxt", type("M", (), {"binance": Live}))
    monkeypatch.setattr(mod, "CCXT_AVAILABLE", True)

    ex = CCXTExchangeExecutor(exchange_id="binance", sandbox=False, **FAKE)
    asyncio.run(ex.connect())

    assert ex.sandbox_verified is False
    assert ex.sandbox_evidence is not None
    assert "live mode" in ex.sandbox_evidence.detail


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 7 — the live-trading authorization gate is preserved
# ══════════════════════════════════════════════════════════════════════════════════════

def test_place_order_still_requires_a_live_authorization(monkeypatch):
    """Sandbox verification must not have displaced the TRADE-04 gate.

    `place_order` is driven with no validation token and no authorization. It must still
    refuse — a verified sandbox is not permission to trade.
    """
    from decimal import Decimal

    from backend_app.backend import exchange_executor as mod

    class Verified(FakeClient):
        def __init__(self, _config):
            super().__init__(urls={"api": SBOX, "test": SBOX, "apiBackup": PROD})

        async def load_markets(self, *_a, **_k):
            return {}

        async def fetch_balance(self):
            return {}

        async def create_order(self, *_a, **_k):  # pragma: no cover - must not run
            raise AssertionError("an order reached the venue despite the gate")

    monkeypatch.setattr(mod, "ccxt", type("M", (), {"binance": Verified}))
    monkeypatch.setattr(mod, "CCXT_AVAILABLE", True)

    ex = CCXTExchangeExecutor(exchange_id="binance", sandbox=True, **FAKE)
    asyncio.run(ex.connect())
    assert ex.sandbox_verified is True

    with pytest.raises(ValueError, match="Bypass attempt detected"):
        asyncio.run(ex.place_order(
            symbol="BTC/USDT", side=mod.OrderSide.BUY,
            order_type=mod.OrderType.MARKET, size=Decimal("0.001"),
        ))


def test_capability_registry_still_authorizes_no_venue_for_trading():
    """Live trading stays OFF. This change grants nothing."""
    from backend_app.core.exchange_capability_registry import \
        ExchangeCapabilityRegistry

    reg = ExchangeCapabilityRegistry()
    reg.load_inventory()
    assert reg.tradable() == []
    assert reg.summary()["tradable_count"] == 0


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 8 — the six approved venues, with no allowlist expansion
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_approved_allowlist_is_unchanged():
    assert sorted(ExchangeExecutorFactory.SUPPORTED_EXCHANGES) == sorted(APPROVED)
    assert len(ExchangeExecutorFactory.SUPPORTED_EXCHANGES) == 6


def test_the_mechanism_table_covers_the_approved_venues_and_adds_none():
    """Scoped to the allowlist. A mechanism entry must not become a routing permission.

    Asserted as an exact set so adding a venue to one table without the other is caught,
    in either direction.
    """
    assert set(SANDBOX_MECHANISMS) == set(APPROVED)
    assert "bitget" not in SANDBOX_MECHANISMS


def test_every_approved_venue_has_a_declared_mechanism():
    for venue in APPROVED:
        assert venue in SANDBOX_MECHANISMS
        mechanism, header = SANDBOX_MECHANISMS[venue]
        assert isinstance(mechanism, SandboxMechanism)
        if mechanism is SandboxMechanism.HEADER:
            assert header, f"{venue} declares HEADER with no header name"


def test_live_connections_for_approved_venues_are_unaffected():
    """Non-sandbox construction must not be touched by any of this.

    Construction only; `connect()` is not called, so no network access occurs.
    """
    for venue in APPROVED:
        ex = CCXTExchangeExecutor(exchange_id=venue, sandbox=False, **FAKE)
        assert ex.sandbox is False
        assert ex.sandbox_verified is False


# ══════════════════════════════════════════════════════════════════════════════════════
#  CASE 9 — the Bitget block is intact
# ══════════════════════════════════════════════════════════════════════════════════════

def test_bitget_is_still_blocked_at_construction():
    """The routing block runs first, so bitget never reaches sandbox verification."""
    with pytest.raises(ExchangeRoutingBlocked):
        CCXTExchangeExecutor(exchange_id="bitget", sandbox=True, **FAKE)
    with pytest.raises(ExchangeRoutingBlocked):
        CCXTExchangeExecutor(exchange_id="bitget", sandbox=False, **FAKE)


def test_bitget_is_absent_from_the_allowlist_and_the_mechanism_table():
    assert "bitget" not in ExchangeExecutorFactory.SUPPORTED_EXCHANGES
    assert "bitget" not in SANDBOX_MECHANISMS


def test_bitgets_endpoint_shape_would_be_refused_by_the_verifier():
    """Belt and braces: even if the routing block were lifted, the shape is refused.

    Uses the client shape measured for bitget in
    `staging/exch01_sandbox_endpoint_evidence.json` — no apiBackup, no test URL, no
    header — registered under a URL_SWAP venue so the verifier evaluates it rather than
    short-circuiting on the unmeasured-venue branch.
    """
    bitget_shaped = FakeClient(urls={"api": PROD})

    with pytest.raises(SandboxVerificationFailed):
        verify_sandbox_endpoint(bitget_shaped, "binance")
    # And as an unmeasured venue it is refused for a second, independent reason.
    with pytest.raises(SandboxVerificationFailed):
        verify_sandbox_endpoint(bitget_shaped, "bitget")
