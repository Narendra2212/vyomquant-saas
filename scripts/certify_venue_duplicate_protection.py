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
    urls = assert_pointed_at_sandbox(executor.exchange)
    print(f"   connected. {len(urls)} resolved URL(s), all sandbox-shaped:")
    for u in urls[:5]:
        print(f"      {u}")

    try:
        print("\n2. MARKET PRICE, to choose a resting price far from it")
        ticker = await executor.exchange.fetch_ticker(
            executor.normalizer.format_symbol_for_ccxt(symbol))
        last = float(ticker.get("last") or ticker.get("close") or 0)
        if last <= 0:
            print("   could not read a price; aborting rather than guessing one")
            return 1
        price = round(last * PRICE_FACTOR_BELOW_MARKET, 2)
        print(f"   last={last}  resting buy price={price} "
              f"({PRICE_FACTOR_BELOW_MARKET:.0%} of market, so it will not fill)")

        market = executor.exchange.market(
            executor.normalizer.format_symbol_for_ccxt(symbol))
        size = str(market.get("limits", {}).get("amount", {}).get("min") or "0.001")
        print(f"   size={size} (the venue's own declared minimum)")

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
            first = await executor.place_order(
                symbol=symbol, side=OrderSide.BUY, order_type=OrderType.LIMIT,
                size=size, price=str(price), execution_id=client_order_id,
            )
            print(f"   success={first.success} venue_order_id={first.order_id} "
                  f"error={first.error_message}")
            if not first.success:
                print("   CLAIM 1 FAILED: the venue refused the first order, so nothing")
                print("   below can be concluded. This is a setup problem, not a verdict.")
                return 1
            placed.append(first.order_id)

            print("\n4. CLAIM 2 -- find it by the id WE chose")
            found = await executor.find_order_by_client_order_id(client_order_id, symbol)
            print(f"   success={found.success} status={found.status} "
                  f"venue_order_id={getattr(found, 'venue_order_id', None)}")
            claim2 = found.success and found.status not in ("not_found", "error")
            print(f"   CLAIM 2 {'PASSED' if claim2 else 'FAILED'}: the platform "
                  f"{'can' if claim2 else 'CANNOT'} resolve an order by its own id here")

            print("\n5. CLAIM 3 -- submit the SAME clientOrderId again")
            print("   If the venue protects, this is refused or returns the first order.")
            second = await executor.place_order(
                symbol=symbol, side=OrderSide.BUY, order_type=OrderType.LIMIT,
                size=size, price=str(price), execution_id=client_order_id,
            )
            print(f"   success={second.success} venue_order_id={second.order_id} "
                  f"error={second.error_message}")

            if not second.success:
                verdict3 = "PASSED (venue REFUSED the duplicate)"
            elif second.order_id and second.order_id == first.order_id:
                verdict3 = "PASSED (venue returned the SAME order, idempotent)"
            else:
                verdict3 = (f"FAILED -- the venue created a SECOND order "
                            f"({second.order_id}). Venue-side duplicate protection does "
                            f"NOT hold here; the platform's client-side refusal "
                            f"(DUP-02) is the only thing preventing a double order.")
                if second.order_id:
                    placed.append(second.order_id)
            print(f"   CLAIM 3 {verdict3}")

            print("\n" + "=" * 70)
            print(f"VENUE {venue} CERTIFICATION")
            print("   claim 1 submit tags      : PASSED")
            print(f"   claim 2 lookup resolves  : {'PASSED' if claim2 else 'FAILED'}")
            print(f"   claim 3 duplicate refused: {verdict3}")
            return 0 if (claim2 and verdict3.startswith("PASSED")) else 1
        finally:
            print("\n6. CLEANUP -- cancelling every order this run placed")
            for oid in placed:
                if not oid:
                    continue
                try:
                    await executor.cancel_order(oid, symbol)
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
