"""Phase 10 / Workstream E — the SAFE ALTERNATIVE suite. Offline, no credential, no order.

WHAT THIS FILE IS FOR
---------------------
Step 3 of the workstream brief requires that, for any scenario a venue's sandbox cannot
support, a safe alternative test is created and the missing evidence recorded explicitly.
Four of the seven order-path venues have no usable sandbox at all, so for those the
question "does the order lifecycle work against a sandbox?" is not merely unanswered —
it is unanswerable, and no credential would change that.

What IS decidable without a venue, a credential or a packet is the question one layer
below: **when "sandbox" is armed, where would an order actually go?** That is a property
of the pinned ccxt's URL and header tables, and it is fully measurable here.

It also happens to be the question that decides whether a trader's money is at risk,
because an order sent to production while the platform believes it is in a sandbox is
worse than no sandbox support at all.

SCOPE: THIS FILE MEASURES THE LIBRARY, NOT THIS PLATFORM
--------------------------------------------------------
Every assertion below constructs a **ccxt** client and inspects it. Nothing here imports
`CCXTExchangeExecutor` or exercises `connect()`. So these tests characterise the
dependency and the claims this repository makes *about* venues — they do **not** test the
platform's own connect path.

That boundary matters for reading the failures, and it was not drawn sharply enough when
this file was written. The platform-level half of EXCH-01 — `connect()` announcing
"SANDBOX mode" without establishing it — was closed separately and is covered by
`tests/test_exch01_sandbox_endpoint_verification.py` (`verify_sandbox_endpoint`,
`assert_sandbox_supported`, fail-closed refusal, client teardown). Closing it did not and
could not turn the bitget assertion below green, because that assertion is about ccxt.
See `staging/PHASE10_EXCH01_CLOSEOUT.md`.

THREE OF THESE TESTS ARE RED BY DESIGN
--------------------------------------
`test_bitget_sandbox_flag_is_inert_and_orders_reach_production`,
`test_certification_registry_sandbox_claims_match_measured_reality` and
`test_customer_facing_faq_sandbox_claims_match_measured_reality` are reproductions of
findings EXCH-01, EXCH-04 and EXCH-05. A reproduction that passes is not a reproduction.

What would turn each green is NOT the same thing, and conflating them is how a closed
defect gets mistaken for an open one:

* **bitget (EXCH-01)** — an UPSTREAM change only: ccxt's `bitget.set_sandbox_mode` calling
  `super()`, or bitget publishing a `urls['test']`. Neither is in this repository's gift.
  Expect this to stay red on ccxt 4.3.92 indefinitely. The platform no longer depends on
  it: bitget is withheld from order routing and `connect()` refuses any venue it cannot
  prove left production.
* **certification registry (EXCH-04)** — correcting the hand-written `sandbox` booleans in
  `backend_app/core/exchange_certification.py`, which `validate_deployment_preflight`
  gates live deployment on. Owned elsewhere.
* **customer FAQ (EXCH-05)** — correcting the published copy in
  `backend_app/routers/support.py`. Owned by product.

That is also why this whole directory is gated out of the default lane — see
`conftest.py`. Workstream E does not own the shared lane and must not red it.

NOTHING HERE TOUCHES A NETWORK. Every ccxt client is constructed with empty credentials
and interrogated only for its URL table, header table and options, then closed. ccxt
allocates its aiohttp session in the constructor, which is why the async clients are
driven through an event loop solely so they can be awaited closed.
"""
from __future__ import annotations

import asyncio

import ccxt
import ccxt.async_support as ccxt_async
import pytest

from backend_app.backend.exchange_executor import ExchangeExecutorFactory
from tests.exchange_sandbox.harness import (GATE_ENV, SANDBOX_ROUTABLE, api_url,
                                            executor_sandbox_config)

ORDER_PATH_VENUES = tuple(ExchangeExecutorFactory.SUPPORTED_EXCHANGES)


# ══════════════════════════════════════════════════════════════════════════════════════
#  MEASUREMENT — construct the client EXACTLY as the executor does
# ══════════════════════════════════════════════════════════════════════════════════════

async def _measure_async(venue: str) -> dict:
    """Routing outcome for *venue* under the executor's own sandbox config.

    Compares the armed client against a pristine one from the same class, so "did it
    move off production" is answered by difference rather than by matching a URL literal
    that ccxt is free to change between versions.
    """
    cls = getattr(ccxt_async, venue)
    live = cls({"apiKey": "", "secret": "", "enableRateLimit": True})
    try:
        live_url = api_url(live)
        live_headers = set(live.headers or {})
        try:
            sandboxed = cls(executor_sandbox_config())
        except Exception as exc:  # noqa: BLE001
            # A constructor raise IS the outcome for venues with no sandbox, and it is
            # reported rather than propagated so the caller can assert on it.
            return {
                "venue": venue,
                "raised": f"{type(exc).__name__}: {exc}",
                "url_changed": False,
                "headers_added": [],
            }
        try:
            return {
                "venue": venue,
                "raised": None,
                "live_url": live_url,
                "sandbox_url": api_url(sandboxed),
                "url_changed": api_url(sandboxed) != live_url,
                "headers_added": sorted(set(sandboxed.headers or {}) - live_headers),
                "sandboxMode": (sandboxed.options or {}).get("sandboxMode", "<absent>"),
            }
        finally:
            await sandboxed.close()
    finally:
        await live.close()


def measure(venue: str) -> dict:
    return asyncio.run(_measure_async(venue))


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE GATE ITSELF — a skip must never be mistaken for a pass
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_sandbox_gate_is_explicit():
    """The opt-in exists and is named, so a skipped run is self-explaining.

    Mirrors the pattern `pytest.ini` describes for the `realaudit` marker: the gate is
    asserted by a test so that "this directory did not run" can never be silent.
    """
    assert GATE_ENV == "AERORA_EXCHANGE_SANDBOX_CERT"
    assert SANDBOX_ROUTABLE == frozenset({"binance", "bybit", "okx"})


# ══════════════════════════════════════════════════════════════════════════════════════
#  SA-1 — WHICH CONFIG KEY ACTUALLY ARMS SANDBOX MODE
# ══════════════════════════════════════════════════════════════════════════════════════

def test_top_level_sandbox_config_key_is_dead_code():
    """`config["sandbox"] = True` alone arms nothing. EXCH-03.

    ccxt 4.3.92's `Exchange.__init__` reads the flag out of `self.OPTIONS`:

        is_sandbox = self.safe_bool_2(self.options, 'sandbox', 'testnet', False)
        if is_sandbox:
            self.set_sandbox_mode(is_sandbox)

    so the executor's first line (`config["sandbox"] = True`) is inert and its second
    (`config["options"] = {"sandbox": True}`) is the one doing the work. Pinned because
    the pair reads as belt-and-braces and is not: anyone deleting the "redundant" second
    line would silently route every sandbox order to production, and a previous analysis
    of this code reached the opposite (wrong) conclusion by reading the first line alone.
    """
    binance_top_only = ccxt.binance({"sandbox": True})
    binance_plain = ccxt.binance({})
    assert api_url(binance_top_only) == api_url(binance_plain), (
        "the top-level 'sandbox' config key changed the base URL; if ccxt has started "
        "honouring it, EXCH-03's premise has changed and the executor comment needs "
        "updating"
    )

    binance_options = ccxt.binance({"options": {"sandbox": True}})
    assert api_url(binance_options) != api_url(binance_plain), (
        "options['sandbox'] no longer arms sandbox mode — the ORDER path's only working "
        "sandbox mechanism has regressed and every sandbox order now goes to production"
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  SA-2 — THE THREE VENUES THAT CAN BE CERTIFIED
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("venue", sorted(SANDBOX_ROUTABLE))
def test_routable_venue_leaves_production_when_sandbox_is_armed(venue):
    """binance/bybit swap the base URL; okx adds the simulated-trading header.

    This is the precondition for every live scenario in
    `test_sandbox_order_lifecycle.py`. If it fails, that suite must not run, because an
    "order placed in sandbox" would be an order placed with real money.
    """
    m = measure(venue)
    assert m["raised"] is None, f"{venue} constructor raised: {m['raised']}"
    assert m["url_changed"] or m["headers_added"], (
        f"{venue} accepted sandbox mode but neither its base URL nor its headers "
        f"changed, so orders would reach PRODUCTION. sandboxMode={m.get('sandboxMode')!r}"
    )
    if venue == "okx":
        # OKX demo trading shares the production host and is selected by a header, so a
        # URL comparison alone would wrongly classify it as unsafe. Pinned explicitly
        # because it is the one venue where "URL unchanged" is the correct outcome.
        assert m["headers_added"] == ["x-simulated-trading"], m["headers_added"]
        assert not m["url_changed"]
    else:
        assert m["url_changed"], f"{venue} should route by URL swap"


# ══════════════════════════════════════════════════════════════════════════════════════
#  SA-3 — THE THREE VENUES WHERE connect(sandbox=True) RAISES
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("venue", ["coinbase", "kraken", "kucoin"])
def test_venue_without_a_sandbox_raises_from_the_constructor(venue):
    """No sandbox exists, and the refusal arrives as an UNCLASSIFIED raise. EXCH-02.

    `connect()` builds the client at

        self._exchange = exchange_class(config)

    which sits BEFORE the `try:` that wraps `fetch_balance()`. So this `NotSupported`
    propagates raw out of `connect()` — it is never passed through
    `_handle_ccxt_error`, so it arrives at the caller as a ccxt type rather than as this
    platform's `ExchangeError`, and callers written to catch `ExchangeError` will not
    catch it.

    Recorded as the measured reason these venues are absent from `SANDBOX_ROUTABLE`:
    their sandbox scenarios are not merely untested, they are untestable on this stack.
    """
    m = measure(venue)
    assert m["raised"] is not None, (
        f"{venue} now constructs in sandbox mode — if ccxt has added a sandbox URL for "
        f"it, this venue may become certifiable and SANDBOX_ROUTABLE should be revisited"
    )
    assert "NotSupported" in m["raised"], m["raised"]
    assert "does not have a sandbox URL" in m["raised"], m["raised"]


# ══════════════════════════════════════════════════════════════════════════════════════
#  SA-4 — EXCH-01. RED BY DESIGN.
# ══════════════════════════════════════════════════════════════════════════════════════

def test_bitget_sandbox_flag_is_inert_and_orders_reach_production():
    """EXCH-01, UPSTREAM HALF. Arming sandbox mode on ccxt's bitget changes nothing.

    WHAT THIS MEASURES: **ccxt**, not this platform. `measure("bitget")` constructs a
    `ccxt.async_support.bitget` client and compares its URL and header tables against an
    unarmed one. No platform code is involved.

    `ccxt.bitget.set_sandbox_mode` overrides the base implementation and does **not**
    call `super()`:

        def set_sandbox_mode(self, enabled):
            self.options['sandboxMode'] = enabled

    No URL swap. No header. Bitget publishes no `urls['test']`. So the only observable
    effect of arming sandbox mode on bitget is an options flag.

    This is distinct from EXCH-02 in the way that matters: coinbase, kraken and kucoin
    REFUSE, loudly, and a refusal cannot lose anybody's money. Bitget succeeds silently.

    WHY THIS STAYS RED, AND WHY THAT IS NOT AN OPEN PLATFORM DEFECT
    ---------------------------------------------------------------
    An earlier version of this docstring said "Fixing it turns this test green; no change
    to this file is needed." **That was wrong**, and the correction is the point of this
    note. The assertion is about the library, so only an upstream change can satisfy it:
    ccxt's `bitget.set_sandbox_mode` calling `super()`, or bitget publishing a
    `urls['test']`. Neither is in this repository's gift, and neither has happened on the
    pinned ccxt 4.3.92.

    The PLATFORM half of EXCH-01 — `connect()` announcing "SANDBOX mode" without
    establishing it, and `place_order` logging `sandbox=True` regardless — **is closed**:

      * bitget is withheld from order routing (`SAFETY_BLOCKED_EXCHANGES`) and refused at
        executor construction, so it never reaches `connect()`;
      * `connect()` now proves sandbox routing from endpoint evidence via
        `verify_sandbox_endpoint` and refuses any venue it cannot prove left production,
        closing and dereferencing the client first;
      * the success log runs only after that proof and names the mechanism.

    Covered by `tests/test_exch01_sandbox_endpoint_verification.py`. See
    `staging/PHASE10_EXCH01_CLOSEOUT.md`.

    The assertion below is UNCHANGED and states the behaviour a venue's sandbox switch
    ought to have: route away from production, or refuse. Bitget does neither, so this
    remains an accurate characterisation of the dependency and is retained as such.
    """
    m = measure("bitget")
    assert m["raised"] is not None or m["url_changed"] or m["headers_added"], (
        "EXCH-01 REPRODUCED: bitget accepted sandbox mode and changed nothing — "
        f"url_changed={m['url_changed']}, headers_added={m['headers_added']}, "
        f"options['sandboxMode']={m.get('sandboxMode')!r}. "
        "CCXTExchangeExecutor.connect logs 'SANDBOX mode' and place_order logs "
        "sandbox=True, but create_order will reach the PRODUCTION bitget API with the "
        "trader's real credentials and real balance. Arming sandbox mode must either "
        "route away from production or refuse; bitget does neither."
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  SA-5 — EXCH-04 / EXCH-05. RED BY DESIGN.
# ══════════════════════════════════════════════════════════════════════════════════════

def test_certification_registry_sandbox_claims_match_measured_reality():
    """EXCH-04. `exchange_certification.py` claims sandboxes that do not exist.

    REPRODUCTION, EXPECTED TO FAIL UNTIL EXCH-04 IS FIXED.

    The registry's `sandbox` booleans are hand-written constants, not measurements, and
    `validate_deployment_preflight` gates live deployment on the same records. Two of
    them are contradicted by the venue code actually installed: coinbase and kucoin are
    declared `sandbox=True` and both raise `NotSupported` when sandbox mode is armed.

    Kraken is the control: it declares `sandbox=False` with the limitation "No public
    CCXT sandbox endpoint", which is correct, which shows the field CAN be right and that
    the two wrong entries are errors rather than a convention.
    """
    from backend_app.core.exchange_certification import get_exchange_certification_registry

    registry = get_exchange_certification_registry()
    disagreements = []
    for venue in ORDER_PATH_VENUES:
        caps = registry.get_capabilities(venue)
        if caps is None:
            continue  # bitget is absent from the registry entirely — EXCH-06
        m = measure(venue)
        actually_has_sandbox = m["raised"] is None and bool(
            m["url_changed"] or m["headers_added"]
        )
        if bool(caps.sandbox) != actually_has_sandbox:
            disagreements.append(
                f"{venue}: registry says sandbox={caps.sandbox}, measured "
                f"{actually_has_sandbox} (raised={m['raised']}, "
                f"url_changed={m['url_changed']}, headers={m['headers_added']})"
            )

    assert not disagreements, (
        "EXCH-04 REPRODUCED: the certification registry's sandbox claims disagree with "
        "the installed ccxt, and `validate_deployment_preflight` gates live deployment "
        "on these same records:\n  " + "\n  ".join(disagreements)
    )


def test_customer_facing_faq_sandbox_claims_match_measured_reality():
    """EXCH-05. The published FAQ advertises two sandboxes that do not exist.

    REPRODUCTION, EXPECTED TO FAIL UNTIL EXCH-05 IS FIXED.

    `routers/support.py` answers "Which exchanges support Sandbox / Testnet mode?" with
    Binance, Bybit, OKX, **Coinbase** and **KuCoin**. The last two raise `NotSupported`.
    This is served to customers over the support API, so it is a misrepresentation in
    product copy, not only an internal inconsistency — the same class of issue
    `FINAL_PRE_LAUNCH_TEST_REPORT.md:230` already raised about the "110 exchanges" claim.
    """
    from backend_app.routers.support import FAQS_DATA

    answer = next(
        (f["answer"] for f in FAQS_DATA
         if "Sandbox / Testnet" in f.get("question", "")),
        None,
    )
    assert answer is not None, "the sandbox FAQ entry has moved or been renamed"

    # Match a venue name followed by a parenthetical label — "Binance (Testnet)",
    # "Coinbase (Sandbox)" — which is how this copy advertises support.
    #
    # A plain substring search would be WRONG here, and wrongly against the code: the
    # same answer ends "Kraken runs on production endpoints exclusively", which is
    # accurate, and a substring match would report Kraken as an overclaim for saying so.
    # The copy gets Kraken right; it gets Coinbase and KuCoin wrong.
    import re

    advertised = {
        v for v in ORDER_PATH_VENUES
        if re.search(rf"\b{re.escape(v)}\s*\(", answer, re.IGNORECASE)
    }
    assert advertised, f"no venue is advertised with a parenthetical label in: {answer!r}"
    overclaimed = sorted(advertised - SANDBOX_ROUTABLE)

    assert not overclaimed, (
        f"EXCH-05 REPRODUCED: the customer-facing FAQ advertises sandbox support for "
        f"{overclaimed}, and arming sandbox mode for each raises ccxt NotSupported. "
        f"Measured-routable set is {sorted(SANDBOX_ROUTABLE)}. "
        f"(Kraken is correctly excluded by this copy and is not counted.)"
    )
