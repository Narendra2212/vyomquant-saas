"""EXCH-01, SECOND FACTORY — `ConnectionEngine` must PROVE a sandbox, not announce one.

WHAT WAS WRONG
--------------
EXCH-01 was closed on `CCXTExchangeExecutor.connect`, and the identical defect was left
open in the other venue-client factory -- the one paper trading and live market data
actually use. `ConnectionEngine.connect` read:

    if self.testnet:
        try:
            self.exchange.set_sandbox_mode(True)
            logger.info(f"[{...}] Testnet/Sandbox ENABLED.")
        except Exception:
            logger.warning(f"[{...}] Testnet requested but not supported by this exchange.")

Two ways that lied. On bitget `set_sandbox_mode` neither raises nor moves the endpoint, so
"Testnet/Sandbox ENABLED" was logged for a client addressing PRODUCTION. On a venue with no
sandbox it raised, the bare `except` swallowed it, and execution continued with
`self.testnet` still True and the client still on production -- a warning, not a refusal.

**Absence of an exception is not evidence of routing, and neither is a log line.**

AND THE POOL COULD NOT TELL THE TWO APART
-----------------------------------------
`_pool_key` is `sha256([user_id, exchange_id])`, and nothing recorded which environment a
pooled client addressed. A caller asking for testnet could be handed the live client that
happened to be pooled first. `exchange_executor`'s own cache got this right (its key folds
in `"sandbox"`/`"live"`); this pool did not.

WHAT THESE TESTS ARE, AND ARE NOT
---------------------------------
Unit tests over deterministic fake venue classes. They prove the FACTORY's logic. They are
not venue sandbox certification: no venue is contacted, no credential is used, no order is
placed. NO NETWORK. Every client here is a local object.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from backend_app.backend import connection_engine as ce
from backend_app.backend.exchange_executor import (SandboxMechanism,
                                                   SandboxVerificationFailed)

PROD = {"public": "https://api.example.com", "ws": "wss://stream.example.com"}
SBOX = {"public": "https://testnet.example.com", "ws": "wss://stream-testnet.example.com"}


class FakeProVenue:
    """A ccxt.pro-shaped exchange CLASS: production at construction, swap on arming.

    WHY IT MODELS THE SWAP RATHER THAN THE RESULT. `ConnectionEngine.connect` learns the
    venue's production endpoint by constructing a PRISTINE client of this same class, then
    arms the real one and compares. A fake that returned sandbox urls however it was built
    would answer "what is production?" with "the sandbox" and refuse everything for a
    reason that exists only in the fake.

    The `ws` key is not decoration. MEASURED on ccxt 4.5.90: a `ccxt.pro` client's
    `urls['api']` carries a `ws` entry that the `ccxt.async_support` client's does not, for
    binance, bybit and okx alike. That is exactly why `connect()` must inject the
    production endpoint instead of letting the verifier default to a REST pristine client
    -- see `test_production_must_come_from_a_pro_client_not_the_rest_default`.
    """

    PRODUCTION = PROD
    TEST = SBOX

    def __init__(self, config=None):
        self.config = dict(config or {})
        self.urls = {"api": dict(self.PRODUCTION), "test": dict(self.TEST)}
        self.headers = {}
        self.symbols: list[str] = []
        self.closed = 0
        self.arm_calls = 0

    def set_sandbox_mode(self, enabled):
        self.arm_calls += 1
        if enabled:
            # The measured ccxt 4.5.90 post-swap shape: apiBackup equals the TEST urls,
            # not production.
            self.urls["apiBackup"] = dict(self.urls["test"])
            self.urls["api"] = dict(self.urls["test"])

    async def load_markets(self):
        self.symbols = ["BTC/USDT"]
        return {"BTC/USDT": {"symbol": "BTC/USDT"}}

    async def close(self):
        self.closed += 1


class NeverLeavesProduction(FakeProVenue):
    """Declares a `test` url that IS production, and swaps onto it.

    The case the production comparison exists for: `apiBackup` is present and
    `api == test`, so the first two conditions both pass and only a production
    comparison can catch it. A venue that repointed its testnet host at its live one
    would look exactly like this.
    """

    TEST = PROD


class SilentNoop(FakeProVenue):
    """The bitget shape: arming raises nothing and moves nothing."""

    def set_sandbox_mode(self, enabled):
        self.arm_calls += 1  # and does nothing else, exactly as ccxt.bitget does


class RaisesOnArm(FakeProVenue):
    """A venue with no sandbox on ccxt 4.5.90.

    The raised type is `TypeError`, not ccxt's `NotSupported`: measured on 4.5.90,
    `set_sandbox_mode(True)` on coinbase, kraken and kucoin raises
    `TypeError("'NoneType' object is not iterable")`. The old code's bare `except`
    swallowed whichever it was.
    """

    def set_sandbox_mode(self, enabled):
        self.arm_calls += 1
        raise TypeError("'NoneType' object is not iterable")


def _install(monkeypatch, **venues):
    """Point `connection_engine.ccxt` at a module exposing only the given fake classes."""
    monkeypatch.setattr(ce, "ccxt", type("M", (), dict(venues)))


@pytest.fixture(autouse=True)
def _drain_pool():
    """The pool is module-global. Leaving an entry behind would leak into the next test."""
    ce._exchange_pool.clear()
    ce._pool_credential_id.clear()
    ce._pool_sandbox.clear()
    yield
    ce._exchange_pool.clear()
    ce._pool_credential_id.clear()
    ce._pool_sandbox.clear()


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE POSITIVE PATH
# ══════════════════════════════════════════════════════════════════════════════════════

def test_a_genuine_swap_is_verified_and_the_connection_proceeds(monkeypatch, caplog):
    _install(monkeypatch, binance=FakeProVenue)

    engine = ce.ConnectionEngine("binance", testnet=True)
    # This module logs to the named "ConnectionEngine" logger at INFO; caplog defaults to
    # WARNING on the root, so without this the success line is simply not captured and the
    # assertion below would be measuring the fixture rather than the code.
    with caplog.at_level(logging.INFO, logger="ConnectionEngine"):
        exchange = asyncio.run(engine.connect())

    assert exchange.arm_calls == 1
    assert exchange.urls["api"] == SBOX, "the client is not on the sandbox endpoint"
    assert exchange.closed == 0, "a verified client must not be torn down"
    # The success line must name the MECHANISM and run only after the proof. The old code
    # logged "Testnet/Sandbox ENABLED" before anything had established it.
    assert "VERIFIED SANDBOX" in caplog.text
    assert SandboxMechanism.URL_SWAP.value in caplog.text
    assert "Testnet/Sandbox ENABLED" not in caplog.text


def test_a_live_connection_is_untouched_by_any_of_this(monkeypatch):
    """testnet=False must not arm, must not verify, must not refuse."""
    _install(monkeypatch, binance=FakeProVenue)

    engine = ce.ConnectionEngine("binance", testnet=False)
    exchange = asyncio.run(engine.connect())

    assert exchange.arm_calls == 0
    assert exchange.urls["api"] == PROD
    assert "apiBackup" not in exchange.urls


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE REFUSALS — each one used to be a log line
# ══════════════════════════════════════════════════════════════════════════════════════

def test_an_arming_failure_is_raised_where_it_used_to_be_swallowed(monkeypatch):
    """The `except Exception: logger.warning(...)` case. It must refuse, not warn."""
    _install(monkeypatch, binance=RaisesOnArm)

    engine = ce.ConnectionEngine("binance", testnet=True)
    with pytest.raises(SandboxVerificationFailed) as caught:
        asyncio.run(engine.connect())

    assert "arming sandbox mode on this venue failed" in str(caught.value)
    assert caught.value.error_code == "SANDBOX_UNVERIFIED"
    assert caught.value.retryable is False
    assert engine.exchange is None, "the unverified client was retained"


def test_an_arming_noop_that_stays_on_production_is_refused(monkeypatch):
    """The bitget case, and the one that made the old log line false."""
    _install(monkeypatch, binance=SilentNoop)

    engine = ce.ConnectionEngine("binance", testnet=True)
    with pytest.raises(SandboxVerificationFailed) as caught:
        asyncio.run(engine.connect())

    assert "apiBackup" in str(caught.value)
    assert engine.exchange is None


def test_production_must_come_from_a_pro_client_not_the_rest_default(monkeypatch):
    """THE TEST THAT PROVES THE INJECTION IS LOAD-BEARING.

    `verify_sandbox_endpoint` defaults to reading production from a pristine client it
    resolves out of `ccxt.async_support`. `ConnectionEngine` builds `ccxt.pro` clients,
    and MEASURED on 4.5.90 a pro client's `urls['api']` carries an extra `ws` key, so a
    pro endpoint can never equal a REST one. Left to that default, the "did it leave
    production" comparison would come out true for a reason that has nothing to do with
    routing -- it would pass VACUOUSLY, which is worse than failing because it looks like
    proof.

    This venue declares a `test` url that IS its production url and swaps onto it, so it
    MUST be refused. Delete the `production_endpoint=` argument in `connect()` and this
    test goes green while the control is dead: the default would compare the fake's
    endpoint against the real binance host, find them different, and verify.
    """
    _install(monkeypatch, binance=NeverLeavesProduction)

    engine = ce.ConnectionEngine("binance", testnet=True)
    with pytest.raises(SandboxVerificationFailed) as caught:
        asyncio.run(engine.connect())

    assert "identical to the venue's production endpoint" in str(caught.value)
    assert engine.exchange is None


def test_a_sandbox_request_for_an_unmeasured_venue_is_refused_before_a_client_exists(
    monkeypatch,
):
    """Fail closed for the 100+ venues this factory serves but nobody measured.

    The fake would have verified happily -- it performs a textbook swap. It is refused on
    the venue's name alone, before construction, because a sandbox nobody has measured is
    not one this platform will certify by a generic rule.
    """
    _install(monkeypatch, mexc=FakeProVenue)

    engine = ce.ConnectionEngine("mexc", testnet=True)
    with pytest.raises(SandboxVerificationFailed) as caught:
        asyncio.run(engine.connect())

    assert "no sandbox mechanism has been measured" in str(caught.value)
    assert engine.exchange is None, "no client should have been constructed at all"


@pytest.mark.parametrize("venue", ["coinbase", "kraken", "kucoin"])
def test_a_venue_with_no_sandbox_is_refused_before_a_client_exists(monkeypatch, venue):
    """Measured UNSUPPORTED venues are refused ahead of construction."""
    _install(monkeypatch, **{venue: FakeProVenue})

    engine = ce.ConnectionEngine(venue, testnet=True)
    with pytest.raises(SandboxVerificationFailed) as caught:
        asyncio.run(engine.connect())

    assert "publishes no sandbox endpoint" in str(caught.value)
    assert engine.exchange is None


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE POOL — a sandbox client and a live client are not interchangeable
# ══════════════════════════════════════════════════════════════════════════════════════

def test_a_live_request_is_not_served_the_pooled_sandbox_client(monkeypatch):
    _install(monkeypatch, binance=FakeProVenue)

    async def scenario():
        sandboxed = await ce.get_or_create_exchange(
            user_id="u1", exchange_id="binance", testnet=True
        )
        live = await ce.get_or_create_exchange(
            user_id="u1", exchange_id="binance", testnet=False
        )
        return sandboxed, live

    sandboxed, live = asyncio.run(scenario())

    assert sandboxed is not live, "the live caller was handed the sandbox client"
    assert sandboxed.urls["api"] == SBOX
    assert live.urls["api"] == PROD
    assert sandboxed.closed == 1, "the superseded sandbox client was not closed"


def test_a_sandbox_request_is_not_served_the_pooled_live_client(monkeypatch):
    """The dangerous direction, and the one a credential-less caller could hit.

    "No credential supplied -> reuse whatever is pooled" is the right rule for
    authentication and the WRONG one for the environment, so sandbox equality is required
    unconditionally. Both calls below pass no credential, which is precisely the shape
    that used to return the pooled live client to a caller asking for testnet.
    """
    _install(monkeypatch, binance=FakeProVenue)

    async def scenario():
        live = await ce.get_or_create_exchange(
            user_id="u1", exchange_id="binance", testnet=False
        )
        sandboxed = await ce.get_or_create_exchange(
            user_id="u1", exchange_id="binance", testnet=True
        )
        return live, sandboxed

    live, sandboxed = asyncio.run(scenario())

    assert live is not sandboxed
    assert sandboxed.urls["api"] == SBOX, "a sandbox caller got a production endpoint"
    assert live.closed == 1


def test_two_identical_sandbox_requests_still_share_one_client(monkeypatch):
    """CE-4 is not sacrificed: the pool still pools."""
    _install(monkeypatch, binance=FakeProVenue)

    async def scenario():
        a = await ce.get_or_create_exchange(
            user_id="u1", exchange_id="binance", testnet=True
        )
        b = await ce.get_or_create_exchange(
            user_id="u1", exchange_id="binance", testnet=True
        )
        return a, b

    a, b = asyncio.run(scenario())
    assert a is b
    assert ce.exchange_pool_size() == 1


def test_releasing_a_pooled_client_drops_its_environment_flag(monkeypatch):
    """A flag left behind for an evicted entry would make the next comparison meaningless."""
    _install(monkeypatch, binance=FakeProVenue)

    async def scenario():
        await ce.get_or_create_exchange(
            user_id="u1", exchange_id="binance", testnet=True
        )
        assert ce._pool_sandbox, "the environment was not recorded"
        await ce.release_exchange("u1", "binance")
        return dict(ce._pool_sandbox)

    assert asyncio.run(scenario()) == {}


def test_draining_the_pool_clears_every_environment_flag(monkeypatch):
    _install(monkeypatch, binance=FakeProVenue, bybit=FakeProVenue)

    async def scenario():
        await ce.get_or_create_exchange("u1", "binance", testnet=True)
        await ce.get_or_create_exchange("u1", "bybit", testnet=False)
        closed = await ce.close_all_exchanges()
        return closed, dict(ce._pool_sandbox)

    closed, flags = asyncio.run(scenario())
    assert closed == 2
    assert flags == {}
