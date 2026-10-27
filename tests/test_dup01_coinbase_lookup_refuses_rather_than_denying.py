"""DUP-01 — on a venue that never received our id, the lookup must refuse, not deny.

THE DEFECT, AND WHY IT WAS WORSE THAN THE GAP IT SAT INSIDE
-----------------------------------------------------------
`exchange_executor.place_order` forwards the deterministic `execution_id` as
`clientOrderId`, and `find_order_by_client_order_id` adjudicates an ambiguous submission
by asking the venue about it. `tests/test_dup01_client_order_id_mapping.py` measures, per
venue, whether ccxt actually forwards that id. Five of the six routable venues do.
**Coinbase does not**: ccxt mints its own `client_order_id` as `id + '-' + uuid()` AND
omits `clientOrderId` from the request params, so the platform's id never arrives.

DUP-01 recorded that as a missing venue-side backstop. It was worse than missing. The
lookup's three strategies all match on our id, so on coinbase every one of them missed --
while the venue had plainly ANSWERED. The method therefore returned

    success=True, status="not_found"

and `OrderWatchdog` is explicit that this is "a definite negative from the venue: it
ANSWERED and has no such order ... immediate and does not go through the age-gated give-up
path". It resolved the row to plain `FAILED`.

That chain ends in a second real order:

  1. the coinbase order may be resting or filled under the venue's own id;
  2. `ExecutionRecordRepository.has_unresolved_submission` -- RT-17's symbol freeze --
     matches only `UNKNOWN` or a `FAILED` row carrying `RECOVERY_CONFIRMATION_LOST`. The
     `not_found` branch sets neither, so the freeze LIFTS;
  3. the next signal for that symbol places another order.

WHAT THIS FILE PROVES
---------------------
1. the constant names coinbase, with a reason, and AGREES WITH THE INSTALLED ccxt -- so a
   future ccxt that honours our id makes these tests fail and the entry can be removed
   with evidence rather than hope;
2. the refusal happens BEFORE any venue method is called, proven with a double that raises
   on every call rather than by reading the source;
3. the result is `success=False`, and specifically NEVER `status="not_found"` -- which is
   the single value that would re-enter the watchdog's definite-negative branch;
4. a forwarding venue is unaffected, so the guard does not over-refuse into a blind spot
   of its own;
5. every routable venue is classified either forwarding or stripping, so a seventh venue
   cannot arrive unexamined.

NO NETWORK, NO CREDENTIALS, NO ORDERS. Executors are built with fake string credentials
and `connect()` is never called.
"""
from __future__ import annotations

import asyncio
import inspect
import re

import ccxt
import pytest

from backend_app.backend.exchange_executor import (
    VENUES_THAT_STRIP_OUR_CLIENT_ORDER_ID, CCXTExchangeExecutor,
    ExchangeExecutorFactory)

FAKE = {"api_key": "not-a-real-key", "api_secret": "not-a-real-secret"}

#: The measured classification, read from the mapping suite so the two cannot drift.
from tests.test_dup01_client_order_id_mapping import (  # noqa: E402
    DOES_NOT_FORWARD, FORWARDS_OUR_ID)


class _RaisingVenue:
    """Every venue call is a failure. The oracle for "the guard ran first".

    A guard that merely returned the right value AFTER querying the venue would still have
    spent a rate-limit budget and a round trip on a question that cannot be answered, and
    would still be one refactor away from reading the answer. Asserting on a double that
    raises makes "did not touch the venue" the thing under test.
    """

    def __init__(self) -> None:
        self.calls: list = []

    def _record(self, name):
        async def _call(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(
                f"{name} was called on a venue that never received our clientOrderId"
            )
        return _call

    def __getattr__(self, name):
        if name.startswith(("fetch_", "create_", "cancel_")):
            return self._record(name)
        raise AttributeError(name)


class _AnsweringVenue:
    """A venue that ANSWERS every scan, carrying its OWN minted id and never ours.

    This is coinbase's actual shape and it is the double that makes the central assertion
    load-bearing. `_RaisingVenue` cannot reach the dangerous branch at all: when every
    fetch raises, `reachable` stays False and the method reports an inability to ask even
    with the guard removed -- so a test built on it would pass under mutation and prove
    nothing about `not_found`.

    Here the scans succeed, nothing matches, `reachable` becomes True, and the ORIGINAL
    code returns `success=True, status="not_found"` -- the definite negative the watchdog
    converts into a plain FAILED that stops freezing the symbol. With the guard in place
    the venue is never reached at all.
    """

    def __init__(self) -> None:
        self.calls: list = []
        # `id + '-' + uuid()`, the shape ccxt's coinbase mints for itself.
        self._foreign = {
            "id": "venue-order-9001",
            "clientOrderId": "ccxt-generated-1234-5678",
            "status": "open",
            "filled": 0,
            "remaining": 1,
            "average": None,
            "info": {"client_order_id": "ccxt-generated-1234-5678"},
        }

    async def fetch_order(self, *args, **kwargs):
        self.calls.append("fetch_order")
        # The venue does not recognise an id it never received.
        raise ccxt.OrderNotFound("no such order")

    async def fetch_open_orders(self, *args, **kwargs):
        self.calls.append("fetch_open_orders")
        return [dict(self._foreign)]

    async def fetch_closed_orders(self, *args, **kwargs):
        self.calls.append("fetch_closed_orders")
        return []


def _executor(venue: str, double=None):
    ex = CCXTExchangeExecutor(exchange_id=venue, **FAKE)
    venue_double = double if double is not None else _RaisingVenue()
    ex._exchange = venue_double
    return ex, venue_double


def _lookup(ex, client_order_id="exec_dup01coinbase01", symbol="BTC/USD"):
    return asyncio.run(ex.find_order_by_client_order_id(client_order_id, symbol))


# ══════════════════════════════════════════════════════════════════════════════════════
#  1. THE CONSTANT, AND ITS AGREEMENT WITH THE INSTALLED ccxt
# ══════════════════════════════════════════════════════════════════════════════════════

def test_coinbase_is_named_with_a_substantive_reason():
    assert "coinbase" in VENUES_THAT_STRIP_OUR_CLIENT_ORDER_ID
    reason = VENUES_THAT_STRIP_OUR_CLIENT_ORDER_ID["coinbase"]
    assert "DUP-01" in reason
    assert "client_order_id" in reason
    assert len(reason) > 80, "a bare label is not a recorded measurement"


def test_the_constant_agrees_with_the_measured_ccxt_source():
    """The entry is a MEASUREMENT, re-taken here. If ccxt changes, this fails.

    Both halves of the coinbase behaviour are asserted, because either one alone would be
    enough to break the mechanism and they could be fixed independently: ccxt could stop
    minting its own id while still omitting ours, or vice versa.
    """
    src = inspect.getsource(ccxt.coinbase.create_order)
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#")
    )

    assert re.search(r"['\"]client_order_id['\"]\s*:\s*\w*id\w*\s*\+", code), (
        "coinbase no longer mints its own client_order_id; re-measure DUP-01 and, if it "
        "now honours ours, remove it from VENUES_THAT_STRIP_OUR_CLIENT_ORDER_ID"
    )
    assert re.search(r"omit\([^)]*['\"]clientOrderId['\"]", code, re.DOTALL), (
        "coinbase no longer omits clientOrderId from params; re-measure DUP-01"
    )
    assert not re.search(
        r"safe_string(_\d)?\(\s*params\s*,\s*['\"](clientOrderId|client_order_id)['\"]",
        code,
    ), (
        "coinbase now reads an incoming client order id, so the platform's id may reach "
        "it; re-measure DUP-01 and remove the entry with the evidence"
    )


def test_the_constant_and_the_mapping_suite_name_the_same_venues():
    """One measured fact, not two declarations that can disagree.

    `DOES_NOT_FORWARD` in the mapping suite and this constant in the executor describe the
    same property. EXCH-01's follow-up defect was exactly two registries disagreeing while
    each stayed internally consistent, so this is pinned rather than trusted.
    """
    assert set(VENUES_THAT_STRIP_OUR_CLIENT_ORDER_ID) == set(DOES_NOT_FORWARD)


# ══════════════════════════════════════════════════════════════════════════════════════
#  2 & 3. THE REFUSAL — BEFORE ANY CALL, AND NEVER AS A DENIAL
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_lookup_refuses_without_touching_the_venue():
    ex, venue = _executor("coinbase")

    result = _lookup(ex)

    assert venue.calls == [], (
        f"the venue was queried before the guard refused: {venue.calls}"
    )
    assert result.success is False


def test_the_refusal_is_never_a_not_found():
    """`not_found` is the one value that re-enters the watchdog's definite-negative branch.

    THE DOUBLE HERE ANSWERS rather than raising, and that is the point. With a venue that
    raises on every call the method reports an inability to ask whether or not the guard
    exists, so such a test would pass with the fix removed and prove nothing. `_AnsweringVenue`
    reproduces coinbase's real shape -- the scans succeed and return an order carrying
    ccxt's own minted id -- which is precisely the condition that made the original code
    answer `not_found` for an order that may be live.
    """
    ex, venue = _executor("coinbase", _AnsweringVenue())

    result = _lookup(ex)

    assert result.status != "not_found", (
        "the lookup answered a DEFINITE negative for a venue that never received our id; "
        "OrderWatchdog converts this into a plain FAILED, which stops freezing the symbol "
        "and lets the next signal place a second real order"
    )
    assert result.success is not True
    assert venue.calls == [], (
        f"the venue was queried before the guard refused: {venue.calls}"
    )


def test_an_answering_venue_that_does_forward_our_id_is_still_denied_normally():
    """The negative control for the test above: `not_found` must remain reachable.

    If the guard were widened to every venue, `not_found` would become unreachable
    platform-wide and ambiguous rows would never be safely failable again -- a quieter
    regression than the one being fixed, and one the assertion above could not see.
    """
    ex, venue = _executor("binance", _AnsweringVenue())

    result = _lookup(ex)

    assert venue.calls, "binance forwards our id, so the venue must be queried"
    assert result.success is True
    assert result.status == "not_found"


def test_the_refusal_says_why_and_names_the_false_negative_it_avoids():
    """An operator reading this has to be able to tell it from an outage.

    "could not be queried" alone would read as a transient fault and invite a retry that
    would answer the same way forever.
    """
    ex, _ = _executor("coinbase")

    message = _lookup(ex).error_message or ""

    assert "clientOrderId" in message
    assert "false negative" in message.lower()
    assert "reconcile" in message.lower() or "reconciled" in message.lower()


def test_no_fill_or_venue_id_is_reported_on_the_refusal():
    """A refusal must not look like a zero-fill answer about a real order."""
    ex, _ = _executor("coinbase")

    result = _lookup(ex)

    assert result.venue_order_id is None
    assert result.avg_price is None


@pytest.mark.parametrize("spelling", ["coinbase", "COINBASE", "Coinbase", " coinbase "])
def test_the_guard_is_not_defeated_by_casing_or_whitespace(spelling):
    ex, venue = _executor(spelling)

    result = _lookup(ex)

    assert result.success is False
    assert venue.calls == []


# ══════════════════════════════════════════════════════════════════════════════════════
#  4. THE NEGATIVE CONTROL — a forwarding venue still asks
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("venue", sorted(FORWARDS_OUR_ID))
def test_a_forwarding_venue_still_reaches_the_venue_lookups(venue):
    """The guard must refuse exactly one venue. Over-refusing would be a silent blind spot.

    `_RaisingVenue` raises from every fetch, and `find_order_by_client_order_id` catches
    those and falls through -- so the evidence that the lookups were REACHED is the
    recorded call list, not the return value.
    """
    ex, venue_double = _executor(venue)

    result = _lookup(ex)

    assert venue_double.calls, (
        f"{venue} forwards our id but the lookup never queried the venue"
    )
    assert "fetch_order" in venue_double.calls[0]
    # Every call raised, so nothing could be established -- which must be reported as an
    # inability to ask rather than as a denial.
    assert result.success is False
    assert result.status != "not_found"


# ══════════════════════════════════════════════════════════════════════════════════════
#  5. COMPLETENESS — no routable venue is unexamined
# ══════════════════════════════════════════════════════════════════════════════════════

def test_every_routable_venue_is_classified_one_way_or_the_other():
    """A seventh venue must not arrive without someone measuring this property for it.

    The failure mode this prevents is the quiet one: a new venue is added to
    SUPPORTED_EXCHANGES, nobody measures whether ccxt forwards our id, and if it does not
    then the `not_found` false negative is live again on that venue with no record of it.
    """
    routable = {v.strip().lower() for v in ExchangeExecutorFactory.SUPPORTED_EXCHANGES}
    classified = {v.lower() for v in FORWARDS_OUR_ID} | {
        v.lower() for v in VENUES_THAT_STRIP_OUR_CLIENT_ORDER_ID
    }

    assert routable <= classified, (
        f"unclassified routable venue(s): {sorted(routable - classified)}. Measure "
        f"whether ccxt forwards clientOrderId there before routing an order to it."
    )


def test_a_stripping_venue_is_still_routable_because_this_is_not_a_block():
    """Deliberate scope. This fix changes an ANSWER, not a permission.

    Blocking coinbase from routing was considered and is a different decision with a
    product cost; it would also have to pass through the pinned allowlists of EXCH-01,
    EXCH-02 and CLAIM-02. What is wrong here is the lookup claiming knowledge it does not
    have, and that is fixed without removing anyone's venue.
    """
    assert "coinbase" in ExchangeExecutorFactory.SUPPORTED_EXCHANGES
