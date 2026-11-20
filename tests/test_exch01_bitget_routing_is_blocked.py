"""EXCH-01 follow-up — Bitget cannot be routed, by any path.

WHAT WAS WRONG, AND WHY THE SHAPE OF THIS FILE FOLLOWS FROM IT
--------------------------------------------------------------
`core/exchange_capability_registry.py` declared a `SAFETY_BLOCKED` dict and filtered
bitget out of `order_routing_allowlisted()`. That made the registry's REPORT correct and
changed nothing about what could be executed, because the registry reads
`SUPPORTED_EXCHANGES` from the executor and nothing in the executor ever read the
registry. Three paths still built a bitget executor:

  1. `ExchangeExecutorFactory.create("bitget", ...)` — bitget was in SUPPORTED_EXCHANGES
  2. `get_exchange_executor(..., exchange_id="bitget")` — delegates to the factory
  3. `CCXTExchangeExecutor(exchange_id="bitget", ...)` — direct construction, unchecked

That is the most dangerous kind of inconsistency: it reads as fixed. A dashboard said
"safety_blocked: [bitget]" while the order path would have happily signed a real bitget
order with a real credential.

So these tests do not test the report. **Each one closes a specific path**, and the
direct-construction case is the load-bearing one — a factory-only check is one
`CCXTExchangeExecutor(...)` call away from being irrelevant.

NO NETWORK, NO CREDENTIALS, NO ORDERS. Every executor here is built with fake string
credentials and the refusal happens in the constructor, before any client exists, so
nothing can reach a venue even in principle.
"""
from __future__ import annotations

import pytest

from backend_app.backend.exchange_executor import (SAFETY_BLOCKED_EXCHANGES,
                                                   CCXTExchangeExecutor,
                                                   ExchangeExecutorFactory,
                                                   ExchangeRoutingBlocked,
                                                   assert_exchange_routable,
                                                   get_exchange_executor)

#: The approved routing set. Six venues, verified against the working tree at the time of
#: this fix rather than copied from a report — bitget was the seventh and is now absent.
APPROVED_ROUTING = ["binance", "bybit", "coinbase", "kraken", "okx", "kucoin"]

FAKE = {"api_key": "not-a-real-key", "api_secret": "not-a-real-secret"}


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE ALLOWLIST
# ══════════════════════════════════════════════════════════════════════════════════════

def test_bitget_is_not_in_the_executor_allowlist():
    assert "bitget" not in ExchangeExecutorFactory.SUPPORTED_EXCHANGES


def test_the_six_approved_routing_venues_are_unchanged():
    """Exactly six, exactly these.

    Asserted as an exact set rather than a membership check so that this test fails if a
    venue is ADDED as well as if one is removed. The connectivity workstream measured 103
    venues with public connectivity and 103 declaring `createOrder`; none of that may
    widen order routing, and a test that only checked for bitget's absence would not
    notice.
    """
    assert sorted(ExchangeExecutorFactory.SUPPORTED_EXCHANGES) == sorted(APPROVED_ROUTING)
    assert len(ExchangeExecutorFactory.SUPPORTED_EXCHANGES) == 6


def test_safety_block_names_bitget_with_a_reason():
    assert "bitget" in SAFETY_BLOCKED_EXCHANGES
    reason = SAFETY_BLOCKED_EXCHANGES["bitget"]
    assert "EXCH-01" in reason
    assert "production" in reason.lower()
    assert "sandbox" in reason.lower()


# ══════════════════════════════════════════════════════════════════════════════════════
#  PATH 1 — THE FACTORY
# ══════════════════════════════════════════════════════════════════════════════════════

def test_factory_refuses_bitget():
    with pytest.raises(ExchangeRoutingBlocked) as caught:
        ExchangeExecutorFactory.create(exchange_id="bitget", **FAKE)

    assert caught.value.exchange_id == "bitget"
    assert caught.value.code == "EXCHANGE_ROUTING_BLOCKED"
    assert "EXCH-01" in caught.value.reason


def test_factory_refusal_is_distinguishable_from_an_unsupported_venue():
    """"Blocked for a money-safety defect" and "never supported" are different facts.

    Removing bitget from the allowlist alone would have produced the generic
    `ValueError: Unsupported exchange: bitget`, indistinguishable from a typo. An
    operator seeing that would reasonably try to add it back. The block is checked first
    so the error carries its reason.
    """
    with pytest.raises(ExchangeRoutingBlocked):
        ExchangeExecutorFactory.create(exchange_id="bitget", **FAKE)

    with pytest.raises(ValueError) as unsupported:
        ExchangeExecutorFactory.create(exchange_id="mexc", **FAKE)
    assert not isinstance(unsupported.value, ExchangeRoutingBlocked)
    assert "Unsupported exchange" in str(unsupported.value)


def test_blocked_refusal_is_still_a_valueerror_so_existing_callers_fail_closed():
    """Existing handlers catch `ValueError`; the new type must not slip past them.

    `ExchangeRoutingBlocked` subclasses `ValueError` deliberately, so a caller written
    against the old `ValueError: Unsupported exchange` contract continues to fail closed
    without modification.
    """
    with pytest.raises(ValueError):
        ExchangeExecutorFactory.create(exchange_id="bitget", **FAKE)


# ══════════════════════════════════════════════════════════════════════════════════════
#  PATH 2 — THE CACHED ACCESSOR
# ══════════════════════════════════════════════════════════════════════════════════════

def test_get_exchange_executor_refuses_bitget():
    with pytest.raises(ExchangeRoutingBlocked):
        get_exchange_executor(exchange_id="bitget", user_id="tenant-1", **FAKE)


def test_a_blocked_venue_is_never_cached():
    """A refused venue must leave no executor behind for a later call to find."""
    from backend_app.backend import exchange_executor as mod

    before = dict(mod._executors)
    for _ in range(3):
        with pytest.raises(ExchangeRoutingBlocked):
            get_exchange_executor(exchange_id="bitget", user_id="tenant-1", **FAKE)

    assert mod._executors == before, "a blocked venue left an entry in the executor cache"


# ══════════════════════════════════════════════════════════════════════════════════════
#  PATH 3 — DIRECT CONSTRUCTION. THE ONE THAT MATTERS MOST.
# ══════════════════════════════════════════════════════════════════════════════════════

def test_direct_construction_of_a_bitget_executor_is_refused():
    """The bypass. `CCXTExchangeExecutor` is public and other code paths instantiate it."""
    with pytest.raises(ExchangeRoutingBlocked):
        CCXTExchangeExecutor(exchange_id="bitget", **FAKE)


def test_the_refusal_happens_before_credentials_are_retained():
    """No instance exists, so there is nothing holding the decrypted secret.

    The guard is the first statement in `BaseExchangeExecutor.__init__`, ahead of the
    lines that assign `_api_key` / `_api_secret`. Verified by the absence of an object
    rather than by reading the source: `pytest.raises` proves `__init__` did not return.
    """
    executor = None
    with pytest.raises(ExchangeRoutingBlocked):
        executor = CCXTExchangeExecutor(
            exchange_id="bitget", api_key="SECRET-KEY", api_secret="SECRET-SECRET"
        )
    assert executor is None


@pytest.mark.parametrize("spelling", ["bitget", "BITGET", "BitGet", " bitget ", "Bitget"])
def test_the_block_is_not_defeated_by_casing_or_whitespace(spelling):
    """Normalisation, so the block cannot be walked around with a different spelling.

    `ExchangeExecutorFactory.create` lowercases its input, but the constructor is reached
    directly too and did not. `assert_exchange_routable` normalises on both paths.
    """
    with pytest.raises(ExchangeRoutingBlocked):
        CCXTExchangeExecutor(exchange_id=spelling, **FAKE)


def test_approved_venues_still_construct():
    """The block must refuse exactly one venue and nothing else.

    A guard that over-refuses would be a trading outage, so this is the negative control.
    Construction only — `connect()` is never called, so no network access occurs.
    """
    for venue in APPROVED_ROUTING:
        executor = CCXTExchangeExecutor(exchange_id=venue, **FAKE)
        assert executor.exchange_id == venue

    assert_exchange_routable("binance")  # must not raise


# ══════════════════════════════════════════════════════════════════════════════════════
#  REGISTRY AGREEMENT
# ══════════════════════════════════════════════════════════════════════════════════════

def test_registry_and_executor_agree_on_the_block():
    """One source of truth. The registry imports the executor's list.

    Before the fix these were two independent declarations that disagreed, and the
    disagreement was invisible because each was internally consistent.
    """
    from backend_app.core.exchange_capability_registry import SAFETY_BLOCKED

    assert SAFETY_BLOCKED == SAFETY_BLOCKED_EXCHANGES
    assert "bitget" in SAFETY_BLOCKED


def test_registry_reports_bitget_blocked_and_not_routable():
    from backend_app.core.exchange_capability_registry import \
        ExchangeCapabilityRegistry

    reg = ExchangeCapabilityRegistry()
    reg.load_inventory()

    assert "bitget" in reg.safety_blocked()
    assert "bitget" not in reg.order_routing_allowlisted()
    assert sorted(reg.order_routing_allowlisted()) == sorted(APPROVED_ROUTING)

    rec = reg.get("bitget")
    assert rec is not None, "bitget must still be DISCOVERED — it exists in ccxt"
    assert any("SAFETY BLOCK" in lim for lim in rec.known_limitations)
    assert not rec.is_tradable()


def test_bitget_remains_discoverable_for_market_data():
    """Blocked from ROUTING is not removed from existence.

    bitget is a real venue and its public market data is unaffected by this block. The
    registry must keep reporting it, or the capability API would start lying in the other
    direction — and "we cannot route orders here" is a different statement from "this
    exchange does not exist".
    """
    from backend_app.core.exchange_capability_registry import (ConnectivityState,
                                                               ExchangeCapabilityRegistry)

    reg = ExchangeCapabilityRegistry()
    reg.load_inventory()
    rec = reg.get("bitget")

    assert rec.has_state(ConnectivityState.DISCOVERED)
    assert rec.has_state(ConnectivityState.CLIENT_INITIALIZED)
    assert rec.capabilities.public_ticker is True


# ══════════════════════════════════════════════════════════════════════════════════════
#  FAIL-CLOSED TRADING AUTHORIZATION IS UNAFFECTED
# ══════════════════════════════════════════════════════════════════════════════════════

def test_no_venue_is_trading_authorized():
    """Live trading stays OFF. Removing bitget authorizes nothing else."""
    from backend_app.core.exchange_capability_registry import \
        ExchangeCapabilityRegistry

    reg = ExchangeCapabilityRegistry()
    reg.load_inventory()
    assert reg.tradable() == []
    assert reg.summary()["tradable_count"] == 0


def test_connectivity_cannot_widen_the_routing_allowlist():
    """103 venues declare `createOrder`; six may be routed to. The gap must persist."""
    from backend_app.core.exchange_capability_registry import \
        ExchangeCapabilityRegistry

    reg = ExchangeCapabilityRegistry()
    reg.load_inventory()

    declares = [r for r in reg.eligible_candidates()
                if r.capabilities.order_create is True]
    assert len(declares) > 50

    routable = set(reg.order_routing_allowlisted())
    assert routable == set(APPROVED_ROUTING)
    assert "bitget" not in routable
    assert not any(r.is_tradable() for r in declares)


def test_asset_universe_no_longer_offers_bitget_markets():
    """`asset_universe` reads SUPPORTED_EXCHANGES, so the removal reaches it too.

    Worth pinning explicitly: this is a second consumer of the allowlist and the reason
    removal from the list matters in addition to the constructor refusal. A venue the
    executor can never trade has no business in a strategy author's asset selector.
    """
    from backend_app.backend.asset_universe import supported_exchange_ids

    venues = supported_exchange_ids()
    assert "bitget" not in venues
    assert sorted(venues) == sorted(APPROVED_ROUTING)
