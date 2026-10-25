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
    # WS-03 / ccxt 4.5.90: WAS "userref". kraken's create path no longer mentions
    # `userref` at all; it now reads our id via
    # `safe_string_2(params, 'clientOrderId', 'cl_ord_id')`. Re-measured, not guessed.
    "kraken": "cl_ord_id",
}

#: Venues whose ccxt implementation forwards OUR id. Measured, not assumed.
#:
#: KRAKEN JOINED THIS SET ON ccxt 4.5.90, and it joined because the test below did its
#: job. test_venue_does_not_forward_our_client_order_id was deliberately written as a
#: CHARACTERISATION of measured reality rather than of desired behaviour, with its own
#: docstring saying "if a future ccxt starts honouring our id on these venues, this test
#: fails and the gap can be closed with evidence rather than hope". The WS-03 upgrade
#: (4.3.92 -> 4.5.90, forced because every websocket was broken) made it fail exactly as
#: designed, with the message "kraken now reads clientOrderId from params - re-measure
#: DUP-01". Measured: it does.
#:
#: WHAT THIS DOES AND DOES NOT CHANGE FOR DUP-01. The SUBMIT half is now real on kraken --
#: the platform's deterministic execution_id reaches the venue as `cl_ord_id`, so there is
#: something for the venue to recognise a duplicate by. The CERTIFICATION half is
#: unchanged: kraken is still UNSUPPORTED in SANDBOX_MECHANISMS (re-measured under 4.5.90
#: and still true), so there is no sandbox to prove the venue actually REFUSES the second
#: submission. "Our id arrives" and "the venue rejects a repeat of it" are different
#: claims and only the first improved.
FORWARDS_OUR_ID = {"binance", "bybit", "okx", "kucoin", "kraken"}

#: Venues where our id does NOT reach the venue, with the measured reason.
DOES_NOT_FORWARD = {
    "coinbase": "ccxt builds its own `client_order_id` as `id + '-' + self.uuid()`, "
                "discarding any clientOrderId supplied in params",
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

    # ONLY COINBASE REMAINS HERE. kraken was in this set until ccxt 4.5.90 and
    # moved to FORWARDS_OUR_ID when this very assertion fired -- see the note on
    # that constant for what did and did not change for DUP-01.
    assert venue == "coinbase", (
        f"{venue!r} is in DOES_NOT_FORWARD but this branch only characterises "
        f"coinbase. Add its measured reason explicitly rather than letting it "
        f"fall through unasserted."
    )
    # ccxt constructs its own id and discards ours.
    assert re.search(r"['\"]client_order_id['\"]\s*:\s*id\s*\+", src), (
        "coinbase no longer self-generates client_order_id; re-measure DUP-01"
    )
    assert not reads_ours, (
        "coinbase now reads clientOrderId from params, so DUP-01 may be closed "
        "for this venue; re-measure and move it to FORWARDS_OUR_ID"
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
    # THE BEHAVIOUR IS UNCHANGED; ONLY THE HELPER'S NAME MOVED. This asserted
    # `uuid22()` until ccxt 4.5.90, where the same auto-generation is done by
    # `request['newClientOrderId'] = self.generate_client_order_id(market)`.
    # Measured on 4.5.90: `uuid22()` is absent from the create path and
    # `generate_client_order_id` is present, so binance DOES still invent an id
    # when none is supplied and this test's point still holds. The old assertion
    # pinned an implementation detail one rename away from a false alarm; either
    # spelling satisfies it now, and a venue that genuinely STOPPED inventing one
    # would fail both.
    assert re.search(r"uuid22\(\)|generate_client_order_id", src), (
        "binance no longer auto-generates a client order id by any known "
        "spelling; re-measure, because the warning in this docstring (a wire-level "
        "reader seeing an id and wrongly inferring protection) would no longer apply"
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
