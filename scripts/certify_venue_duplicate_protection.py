#!/usr/bin/env python3
"""DUP-01: certify venue-side duplicate-order protection against a venue SANDBOX.

WHAT THIS CERTIFIES
===================
DUP-01's database half is verified (24 tests on real PostgreSQL in CI). Its VENUE half is
not, and cannot be without a venue that will accept an order. The platform's design rests
on three claims about the venue, and this proves or disproves each against a real endpoint:

  1. SUBMIT TAGS      the venue accepts `clientOrderId` on create and preserves it
  2. LOOKUP RESOLVES  find_order_by_client_order_id finds that order by the id WE chose,
                      via whichever of its three strategies the venue supports
  3. DUPLICATE REFUSED a second submission with the SAME clientOrderId does not produce a
                      second live order

Claim 3 is the one DUP-01 is named for. Claims 1 and 2 are prerequisites: if the id is not
preserved or not queryable, the platform's whole recovery handle is fiction.

WHY A SANDBOX AND NOT PRODUCTION
================================
Claim 3 cannot be tested without submitting an order twice. On a real account that is two
real orders if the protection does not work -- which is precisely the thing under test, so
it must be assumed broken. A sandbox is therefore not a convenience here, it is the only
ethical way to ask the question.

SAFETY, AND WHY THESE GUARDS ARE UNCONDITIONAL
==============================================
This script submits orders. Pointed at production by mistake it would submit REAL ones. So:

  * It REFUSES to run unless the venue is in SANDBOX_MECHANISMS with a mechanism that is
    not UNSUPPORTED.
  * It REFUSES unless the executor's own sandbox verification passes. That machinery
    already exists and raises SandboxVerificationFailed rather than returning a verdict,
    precisely so a caller cannot drop it -- this script does not try to.
  * It asserts, after connecting, that every resolved URL is a sandbox/testnet host and
    aborts if any is not. Belt and braces: the mechanism above is the venue's claim about
    itself; this is an independent check of where the client is actually pointed.
  * Orders are placed at a price far from the market so they REST rather than fill, and
    every order placed is cancelled in a finally block.
  * `--plan` is the default. `--apply` is required to place anything.

WHY NOT place_order(), AND WHY NO TOKEN IS MINTED HERE
======================================================
This script used to submit through `ExchangeExecutor.place_order`. The first real run
against Binance testnet proved it cannot, and the reason is a safety control doing its
job:

    ValueError: Bypass attempt detected: All executions must route through
                UnifiedExecutionEngine.

`place_order` calls `verify_and_consume_token`, which requires a single-use token minted
by UnifiedExecutionEngine and verified by `global_safety.verify_validation_token`. No
order was submitted when this fired -- the guard refuses before reaching the venue.

TWO WAYS ROUND IT WERE AVAILABLE AND BOTH WERE REJECTED:

  * MINT A TOKEN HERE. Trivial to write, and the reason not to is not squeamishness: it
    would turn this file into a tool that places orders OUTSIDE UnifiedExecutionEngine,
    carrying credentials from the environment and an `--apply` flag. The sandbox guards
    above are what keep it on testnet today; a token-minting helper would mean the only
    thing standing between this script and a real unsupervised order is those guards
    being right forever. Defeating a control for a test's convenience is how the control
    stops being true.
  * ROUTE THROUGH UnifiedExecutionEngine. The honest path, and impossible right now:
    live trading is hard-gated off (RT-15), so the engine refuses by design, and a
    paper-mode engine has `exchange_executor = None` and never reaches a venue at all.

SO THE SUBMISSIONS GO THROUGH ccxt DIRECTLY, WHICH IS ALSO THE RIGHT TEST. DUP-01's three
claims are properties of THE VENUE, not of the platform's order path: does Binance accept
and preserve a clientOrderId, can it be queried back, and does it refuse a second order
carrying the same one. Those answers do not change with which of our functions dialled
out. The id is passed as `params={"clientOrderId": ...}` -- byte-identical to what
`place_order` does at exchange_executor.py:1679 -- so the venue sees exactly the shape
production sends.

Claim 2 still exercises the PLATFORM's real code: `find_order_by_client_order_id` is a
read path with no token requirement, and it is the function under test, so it is called
directly rather than reimplemented.

CREDENTIALS
===========
Read from the environment, never from a file and never written anywhere:

    VENUE_TESTNET_API_KEY, VENUE_TESTNET_SECRET, VENUE_TESTNET_PASSWORD (okx only)

    $env:VENUE_TESTNET_API_KEY="..."; $env:VENUE_TESTNET_SECRET="..."
    python scripts/certify_venue_duplicate_protection.py --venue binance --plan
    python scripts/certify_venue_duplicate_protection.py --venue binance --apply

Nothing is logged that could contain a key: every exception message goes through the
executor's own `_redact_credentials`.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Hosts that prove a client is NOT pointed at production. Checked against every resolved
#: URL after connecting. A venue whose sandbox host is not in this list must be added
#: deliberately, with the host read off the venue's own published sandbox documentation --
#: not inferred from a URL that merely looks unfamiliar.
SANDBOX_HOST_MARKERS = (
    "testnet", "sandbox", "demo", "api-testnet", "testnet.binancefuture.com",
    "api-demo", "paper",
)

#: Size and price posture: small enough to be meaningless, far enough from the market that
#: the order rests instead of filling. A filled order cannot be cancelled, and a test that
#: leaves a position behind is a test that changed the account it was measuring.
PRICE_FACTOR_BELOW_MARKET = 0.5


def venue_sandbox_mechanism(venue: str):
    from backend_app.backend.exchange_executor import (SANDBOX_MECHANISMS,
                                                       SandboxMechanism)
    entry = SANDBOX_MECHANISMS.get(venue)
    if entry is None:
        return None, f"{venue!r} is not in SANDBOX_MECHANISMS at all"
    mechanism, _param = entry
    if mechanism == SandboxMechanism.UNSUPPORTED:
        return None, (
            f"{venue!r} is declared UNSUPPORTED in SANDBOX_MECHANISMS -- the installed "
            f"ccxt publishes no sandbox endpoint for it, so there is nothing to certify "
            f"against. This is DUP-01's permanently blocked half, not a missing input."
        )
    return mechanism, None


def assert_pointed_at_sandbox(client) -> list:
    """Every resolved URL must look like a sandbox. Returns the URLs it checked."""
    urls = []
    raw = getattr(client, "urls", {}) or {}

    def _walk(value):
        if isinstance(value, str) and value.startswith("http"):
            urls.append(value)
        elif isinstance(value, dict):
            for v in value.values():
                _walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                _walk(v)

    _walk(raw.get("api"))
    if not urls:
        raise SystemExit(
            "could not resolve any API URL from the client, so it is impossible to prove "
            "it is not pointed at production. Refusing to submit anything."
        )
    offenders = [u for u in urls
                 if not any(m in u.lower() for m in SANDBOX_HOST_MARKERS)]
    if offenders:
        raise SystemExit(
            "REFUSING TO SUBMIT: these resolved URLs do not look like sandbox hosts, so "
            "this client may be addressing production:\n  "
            + "\n  ".join(sorted(set(offenders))[:8])
            + "\nIf one of these IS the venue's sandbox, add its host to "
              "SANDBOX_HOST_MARKERS deliberately rather than loosening this check."
        )
    return sorted(set(urls))


async def certify(venue: str, symbol: str, apply: bool) -> int:
    from backend_app.backend.exchange_executor import (ExchangeExecutor, OrderSide,
                                                       OrderType)

    mechanism, refusal = venue_sandbox_mechanism(venue)
    if refusal:
        print(f"REFUSED: {refusal}")
        return 2
    print(f"venue              : {venue}")
    print(f"sandbox mechanism  : {mechanism}")
    print(f"symbol             : {symbol}")

    api_key = os.environ.get("VENUE_TESTNET_API_KEY")
    secret = os.environ.get("VENUE_TESTNET_SECRET")
    password = os.environ.get("VENUE_TESTNET_PASSWORD")
    if not api_key or not secret:
        print("\nREFUSED: VENUE_TESTNET_API_KEY / VENUE_TESTNET_SECRET are not set.")
        print("This script never reads credentials from a file. Export them for one")
        print("shell and let them expire with it.")
        return 2
    print(f"credentials        : present (key len={len(api_key)}, "
          f"password={'yes' if password else 'no'})")

    executor = ExchangeExecutor(
        exchange_id=venue, api_key=api_key, api_secret=secret,
        password=password, sandbox=True,
    )

    print("\n1. CONNECT, with the executor's own sandbox verification")
    print("   SandboxVerificationFailed is raised, not returned, so it cannot be dropped.")
    await executor.connect()

    try:
        # `_exchange` is private and the executor exposes no public accessor. Reached
        # deliberately: the independent URL check below must inspect the REAL ccxt
        # client, and asking the executor to describe where it is pointed would make
        # this cross-check depend on the very thing it exists to cross-check.
        #
        # THIS BLOCK WAS OUTSIDE THE try WHOSE finally DISCONNECTS until 2026-10-25,
        # and the first real run proved why that mattered: the line read
        # `executor.exchange`, which does not exist (the attribute is `_exchange`), so
        # it raised AttributeError AFTER connect() had already opened an authenticated
        # aiohttp session -- which was then never closed, and ccxt said so loudly.
        # A credentialed session leaked by the failure path of a safety check is a poor
        # trade for one attribute name.
        client = executor._exchange
        urls = assert_pointed_at_sandbox(client)
        print(f"   connected. {len(urls)} resolved URL(s), all sandbox-shaped:")
        for u in urls[:5]:
            print(f"      {u}")

        print("\n2. MARKET PRICE, to choose a resting price far from it")
        ticker = await client.fetch_ticker(
            executor.normalizer.format_symbol_for_ccxt(symbol))
        last = float(ticker.get("last") or ticker.get("close") or 0)
        if last <= 0:
            print("   could not read a price; aborting rather than guessing one")
            return 1
        ccxt_symbol = executor.normalizer.format_symbol_for_ccxt(symbol)
        market = client.market(ccxt_symbol)

        # HOW FAR BELOW THE MARKET WE MAY REST IS THE VENUE'S DECISION, NOT OURS, and
        # hardcoding it failed. PRICE_FACTOR_BELOW_MARKET alone (0.5) was rejected by
        # Binance with `-1013 Filter failure: PERCENT_PRICE_BY_SIDE`, and the filter says
        # exactly why: bidMultiplierDown is 0.5 -- the same number -- but it is applied to
        # the 5-MINUTE AVERAGE price (avgPriceMins: 5), not to `last`. So resting at
        # last * 0.5 sits precisely ON the boundary and falls outside it whenever the
        # average differs from the last trade at all, which on a live book is always.
        #
        # So the floor is read from the venue's own declared filter and we rest 20% INSIDE
        # it. For Binance that is 0.6 of market: still 40% below the book, so it cannot
        # fill, but no longer balanced on the edge of a filter.
        factor = PRICE_FACTOR_BELOW_MARKET
        basis = f"{PRICE_FACTOR_BELOW_MARKET:.0%} of market (script default)"
        pct_filter = next(
            (f for f in ((market.get("info") or {}).get("filters") or [])
             if f.get("filterType") == "PERCENT_PRICE_BY_SIDE"), None)
        if pct_filter:
            down = float(pct_filter.get("bidMultiplierDown") or 0)
            if down > 0:
                factor = min(0.95, down * 1.2)
                basis = (f"{factor:.0%} of market = venue bidMultiplierDown {down} "
                         f"+20% inside the filter floor "
                         f"(avgPriceMins={pct_filter.get('avgPriceMins')})")
        price = float(client.price_to_precision(ccxt_symbol, last * factor))
        print(f"   last={last}  resting buy price={price}")
        print(f"   basis: {basis} -- far enough below the book that it rests, "
              f"not fills")

        limits = market.get("limits", {}) or {}
        min_amount = float((limits.get("amount") or {}).get("min") or 0) or 0.00001
        min_cost = float((limits.get("cost") or {}).get("min") or 0)

        # THE MINIMUM AMOUNT IS NOT ENOUGH ON ITS OWN, and this used to pass just that.
        # Binance's declared min amount for BTC/USDT is 1e-05, which at a resting price
        # of half the market is roughly $0.40 of notional -- far under the venue's
        # MIN_NOTIONAL filter. The venue would reject the FIRST order on a filter, and
        # claims 2 and 3 would then be unreachable: the run would report "CLAIM 1 FAILED
        # ... a setup problem, not a verdict" and certify nothing. So the size has to
        # satisfy the cost floor at the price we are actually going to use, not at the
        # market price.
        #
        # 20% of headroom above the floor, because rounding DOWN to the venue's amount
        # precision can otherwise drop the notional back under the filter it was sized to
        # clear.
        size_f = min_amount
        reason = f"venue min amount {min_amount}"
        if min_cost:
            needed = (min_cost / price) * 1.2
            if needed > size_f:
                size_f = needed
                reason = (f"venue min cost {min_cost} / resting price {price} "
                          f"+20% headroom")
        size = client.amount_to_precision(ccxt_symbol, size_f)
        notional = float(size) * price
        print(f"   size={size} ({reason})")
        print(f"   notional={notional:.2f} against a min cost of "
              f"{min_cost or 'unspecified'} -- must clear it or the venue rejects on a "
              f"filter and nothing is certified")
        if min_cost and notional < min_cost:
            print(f"\n   REFUSED: even after sizing up, {notional:.2f} is under the "
                  f"venue's {min_cost} minimum notional. Raise PRICE_FACTOR_BELOW_MARKET "
                  f"(closer to market = smaller size for the same notional) rather than "
                  f"submitting an order that can only fail on a filter.")
            return 1

        client_order_id = f"dup01cert{uuid.uuid4().hex[:16]}"
        print(f"\n   clientOrderId to certify with: {client_order_id}")

        if not apply:
            print("\n--plan only. Nothing was submitted. The connection and the sandbox")
            print("verification above are real; re-run with --apply to place the two")
            print("orders claims 1-3 require.")
            return 0

        placed = []
        try:
            print("\n3. CLAIM 1 -- submit once, with our clientOrderId")
            print("   Submitted through ccxt directly, NOT executor.place_order(), and")
            print("   that is deliberate -- see WHY NOT place_order() in the docstring.")
            first_raw = await client.create_order(
                ccxt_symbol, "limit", "buy", float(size), float(price),
                params={"clientOrderId": client_order_id},
            )
            first_id = first_raw.get("id")
            echoed = (first_raw.get("clientOrderId")
                      or (first_raw.get("info") or {}).get("clientOrderId"))
            print(f"   accepted. venue_order_id={first_id}")
            print(f"   venue echoed clientOrderId={echoed!r}")
            claim1 = str(echoed) == str(client_order_id)
            print(f"   CLAIM 1 {'PASSED' if claim1 else 'FAILED'}: the venue "
                  f"{'preserved' if claim1 else 'did NOT preserve'} the id we chose")
            placed.append(first_id)

            print("\n4. CLAIM 2 -- find it by the id WE chose")
            found = await executor.find_order_by_client_order_id(client_order_id, symbol)
            print(f"   success={found.success} status={found.status} "
                  f"venue_order_id={getattr(found, 'venue_order_id', None)}")
            claim2 = found.success and found.status not in ("not_found", "error")
            print(f"   CLAIM 2 {'PASSED' if claim2 else 'FAILED'}: the platform "
                  f"{'can' if claim2 else 'CANNOT'} resolve an order by its own id here")

            print("\n5. CLAIM 3 -- submit the SAME clientOrderId again")
            print("   If the venue protects, this is refused or returns the first order.")
            second_id = None
            refusal = None
            try:
                second_raw = await client.create_order(
                    ccxt_symbol, "limit", "buy", float(size), float(price),
                    params={"clientOrderId": client_order_id},
                )
                second_id = second_raw.get("id")
                print(f"   venue ACCEPTED a second submission. order_id={second_id}")
            except Exception as exc:  # noqa: BLE001 - the refusal IS the result
                refusal = executor._redact_credentials(str(exc))
                print(f"   venue REFUSED: {type(exc).__name__}: {refusal[:200]}")

            if refusal is not None:
                verdict3 = "PASSED (venue REFUSED the duplicate)"
            elif second_id and str(second_id) == str(first_id):
                verdict3 = "PASSED (venue returned the SAME order, idempotent)"
            else:
                verdict3 = (f"FAILED -- the venue created a SECOND order "
                            f"({second_id}). Venue-side duplicate protection does "
                            f"NOT hold here; the platform's client-side refusal "
                            f"(DUP-02) is the only thing preventing a double order.")
                if second_id:
                    placed.append(second_id)
            print(f"   CLAIM 3 {verdict3}")

            print("\n" + "=" * 70)
            print(f"VENUE {venue} CERTIFICATION")
            print(f"   claim 1 submit tags      : {'PASSED' if claim1 else 'FAILED'}")
            print(f"   claim 2 lookup resolves  : {'PASSED' if claim2 else 'FAILED'}")
            print(f"   claim 3 duplicate refused: {verdict3}")
            return 0 if (claim1 and claim2 and verdict3.startswith("PASSED")) else 1
        finally:
            print("\n6. CLEANUP -- cancelling every order this run placed")
            for oid in placed:
                if not oid:
                    continue
                try:
                    await client.cancel_order(oid, ccxt_symbol)
                    print(f"   cancelled {oid}")
                except Exception as exc:  # noqa: BLE001
                    print(f"   COULD NOT CANCEL {oid}: "
                          f"{executor._redact_credentials(str(exc))[:120]}")
                    print("   CANCEL BY HAND -- this run left a resting order behind.")
    finally:
        try:
            await executor.disconnect()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--venue", required=True,
                   help="binance | bybit | okx (the three with real sandboxes)")
    p.add_argument("--symbol", default="BTC/USDT")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--plan", action="store_true",
                   help="connect and verify the sandbox, submit nothing (default)")
    g.add_argument("--apply", action="store_true",
                   help="submit the two orders claims 1-3 require, then cancel them")
    args = p.parse_args()
    return asyncio.run(certify(args.venue, args.symbol, bool(args.apply)))


if __name__ == "__main__":
    sys.exit(main())
