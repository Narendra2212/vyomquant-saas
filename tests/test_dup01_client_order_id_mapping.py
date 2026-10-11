"""DUP-01 — does our `execution_id` actually reach each venue as its client order id?

WHY THIS FILE EXISTS
--------------------
`exchange_executor.place_order` forwards the platform's deterministic `execution_id` as

    extra_params["clientOrderId"] = self._consumed_execution_id

and that is the **only** protection against a retry becoming a second real order. It works
only if ccxt translates the unified `clientOrderId` into the field the venue actually
reads. ccxt does **not** do this uniformly, and the brief forbids assuming it does.

ESTABLISHED by reading ccxt 4.3.92's own per-venue source (offline, no venue contact):

    binance   newClientOrderId   TRANSLATED
    bybit     orderLinkId        TRANSLATED
    okx       clOrdId            TRANSLATED
    kucoin    clientOid          TRANSLATED
    coinbase  client_order_id    *** OVERWRITTEN — ours is discarded ***
    kraken    userref            *** NOT HANDLED — ours is ignored ***

Evidence: `staging/probe_p11bc_client_order_id.py` →
`staging/p11bc_client_order_id.txt` / `.json`, which records the matched source lines so
the verdict is auditable rather than resting on a regex.

SCOPE AND LIMITS, STATED PLAINLY
--------------------------------
These are **offline source-contract tests**. They establish what ccxt *intends* to send.
They do **not** establish that any venue accepts the id, echoes it back, or refuses a
second order carrying it — those are venue behaviours and only a real sandbox order can
settle them. **Scenario 8 is not satisfied by this file**, and nothing here certifies
duplicate-order protection on any venue.

These tests are characterisation: they pin the current mapping so a ccxt upgrade or a
venue-implementation change becomes visible instead of silently removing the platform's
only duplicate protection.
"""
from __future__ import annotations

import inspect
import re

import ccxt
import pytest

from backend_app.backend.exchange_executor import ExchangeExecutorFactory

#: venue -> native client-order-id parameter.
NATIVE_FIELD = {
    "binance": "newClientOrderId",
    "bybit": "orderLinkId",
    "okx": "clOrdId",
    "kucoin": "clientOid",
    "coinbase": "client_order_id",
    "kraken": "userref",
}

#: Venues whose ccxt implementation forwards OUR id. Measured, not assumed.
FORWARDS_OUR_ID = {"binance", "bybit", "okx", "kucoin"}

#: Venues where our id does NOT reach the venue, with the measured reason.
DOES_NOT_FORWARD = {
    "coinbase": "ccxt builds its own `client_order_id` as `id + '-' + self.uuid()`, "
                "discarding any clientOrderId supplied in params",
    "kraken": "no create*/edit* method mentions clientOrderId or userref at all, so the "
              "parameter is passed through verbatim and the venue does not read it",
}


def _create_source(venue: str) -> str:
    """Concatenated source of every order-constructing method for *venue*."""
    cls = getattr(ccxt, venue)
    out = []
    for name in dir(cls):
        if not name.startswith(("create", "edit")):
            continue
        fn = getattr(cls, name, None)
        if not callable(fn):
            continue
        try:
            out.append(inspect.getsource(fn))
        except (OSError, TypeError):
            continue
    return "\n".join(out)


def _uncommented(src: str) -> str:
    return "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#")
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE PLATFORM SIDE — what we send
# ══════════════════════════════════════════════════════════════════════════════════════

def test_place_order_forwards_the_execution_id_as_clientOrderId():
    """The one line the whole protection rests on, pinned."""
    src = inspect.getsource(
        __import__("backend_app.backend.exchange_executor", fromlist=["x"])
        .CCXTExchangeExecutor.place_order
    )
    assert 'extra_params["clientOrderId"] = self._consumed_execution_id' in src, (
        "place_order no longer forwards execution_id as clientOrderId — the platform's "
        "only duplicate-order protection has been removed"
    )


def test_an_order_without_a_consumed_execution_id_sends_no_client_order_id():
    """Characterisation of the gap: no id -> no venue-side idempotency.

    `place_order` only sets the key `if self._consumed_execution_id`. That is reachable:
    `verify_and_consume_token` sets it, and any path that failed to would submit with no
    client order id at all — on binance ccxt would then invent a random one, which is
    not idempotency.
    """
    src = inspect.getsource(
        __import__("backend_app.backend.exchange_executor", fromlist=["x"])
        .CCXTExchangeExecutor.place_order
    )
    assert "if self._consumed_execution_id:" in src


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE CCXT SIDE — what actually reaches each venue
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("venue", sorted(FORWARDS_OUR_ID))
def test_venue_translates_our_client_order_id_to_its_native_field(venue):
    """ccxt reads our unified key and writes the venue's native parameter."""
    native = NATIVE_FIELD[venue]
    src = _uncommented(_create_source(venue))

    assert re.search(r"safe_string\w*\([^)]*['\"]clientOrderId['\"]", src), (
        f"{venue}: ccxt no longer reads 'clientOrderId' out of params — our execution_id "
        f"would be dropped and duplicate protection lost"
    )
    assert re.search(rf"['\"]{re.escape(native)}['\"]", src), (
        f"{venue}: ccxt no longer references the native field {native!r}"
    )
    assert re.search(r"request\[[^\]]+\]\s*=", src), (
        f"{venue}: no request assignment found in the order construction path"
    )


@pytest.mark.parametrize("venue", sorted(DOES_NOT_FORWARD))
def test_venue_does_not_forward_our_client_order_id(venue):
    """DUP-01. Characterisation of the two venues where our id never arrives.

    This test asserts the **measured reality**, not the desired behaviour, and is
    deliberately shaped that way: if a future ccxt starts honouring our id on these
    venues, this test fails and the gap can be closed with evidence rather than hope.

    The consequence while it holds: a retry on coinbase or kraken is a **brand-new order**
    as far as the venue is concerned. Nothing at the venue will refuse it.
    """
    src = _uncommented(_create_source(venue))
    reads_ours = bool(re.search(r"safe_string\w*\([^)]*['\"]clientOrderId['\"]", src))

    if venue == "coinbase":
        # ccxt constructs its own id and discards ours.
        assert re.search(r"['\"]client_order_id['\"]\s*:\s*id\s*\+", src), (
            "coinbase no longer self-generates client_order_id — re-measure DUP-01"
        )
        assert not reads_ours, (
            "coinbase now reads clientOrderId from params — DUP-01 may be closed for "
            "this venue; re-measure and move it to FORWARDS_OUR_ID"
        )
    else:  # kraken
        assert not reads_ours, (
            "kraken now reads clientOrderId from params — re-measure DUP-01"
        )
        assert "userref" not in src, (
            "kraken now references userref — re-measure DUP-01"
        )


def test_binance_invents_a_client_order_id_when_none_is_supplied():
    """A random id is not idempotency, and it is important not to mistake it for one.

    ccxt's binance path does:

        if clientOrderId is None:
            request[clientOrderIdRequest] = brokerId + self.uuid22()

    So an order submitted without our `execution_id` still carries *a* client order id.
    A reader checking "did we send a client order id?" at the wire level would see one
    and wrongly conclude the order was protected. It is fresh on every attempt, so a
    retry is a new order.
    """
    src = _uncommented(_create_source("binance"))
    assert re.search(r"uuid22\(\)", src), (
        "binance no longer auto-generates a client order id — re-measure"
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  COVERAGE OF THE ALLOWLIST
# ══════════════════════════════════════════════════════════════════════════════════════

def test_every_routable_venue_has_a_measured_mapping_verdict():
    """No allowlisted venue may be left unclassified.

    A venue whose duplicate-ID behaviour nobody measured is the condition the brief
    requires to fail closed, so the first requirement is that the set is complete.
    """
    routable = set(ExchangeExecutorFactory.SUPPORTED_EXCHANGES)
    classified = FORWARDS_OUR_ID | set(DOES_NOT_FORWARD)

    unclassified = routable - classified
    assert not unclassified, (
        f"routable venues with no measured client-order-id verdict: "
        f"{sorted(unclassified)}. Duplicate-order behaviour is unknown for these, which "
        f"must fail closed — measure with staging/probe_p11bc_client_order_id.py"
    )


def test_the_unprotected_venues_are_recorded_with_a_reason():
    """Each gap carries its measured cause, so it cannot be dismissed as unknown."""
    for venue, reason in DOES_NOT_FORWARD.items():
        assert venue in ExchangeExecutorFactory.SUPPORTED_EXCHANGES
        assert len(reason) > 40, f"{venue} has no substantive recorded reason"


def test_no_venue_is_trading_authorized_so_the_gap_is_not_live():
    """Context, asserted rather than assumed: live trading is OFF.

    DUP-01 is a real gap in a path that cannot currently be reached, which is why it is
    reported for authorisation rather than hot-fixed by narrowing routing.
    """
    from backend_app.core.exchange_capability_registry import \
        ExchangeCapabilityRegistry

    reg = ExchangeCapabilityRegistry()
    reg.load_inventory()
    assert reg.tradable() == []
