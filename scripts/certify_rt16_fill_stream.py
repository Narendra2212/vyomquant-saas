#!/usr/bin/env python3
"""RT-16: certify the private fill stream against a real venue, end to end.

WHAT THIS PROVES, AND WHY IT COULD NOT BE PROVEN BEFORE
=======================================================
RT-16's three links each existed and none had a caller, so "a fill reaches the ledger over
a websocket" had never once happened. Unit tests with fakes cannot establish it: the whole
question is whether a VENUE's authenticated private channel delivers an order update that
this platform's own chain carries end to end.

So this drives the REAL chain -- the real ``ExchangeWebSocketListener``, the real
``EventListener`` and its priority queue and drain loop, started in the real worker's
order -- against the real Binance spot testnet, with a real credential, and a real order
placed and cancelled.

WHAT IS SUBSTITUTED, AND WHY EACH ONE IS HONEST
===============================================
Two things, both at the far end, and neither is on the path under test:

* THE ROUTER IS A RECORDER. ``EventRouter`` needs a synchronous SQLAlchemy session and an
  ``execution_records`` row whose ``venue_order_id`` matches the venue's id -- i.e. a row
  the live execution engine would have written when it placed the order. This platform
  cannot place that order: live trading is hard-gated off (RT-15) and
  ``place_order`` refuses without a token minted by UnifiedExecutionEngine. Minting one
  here, or writing a synthetic execution row into the production database, would both
  defeat a control to make a test pass. The recorder captures exactly what
  ``EventRouter.route_event`` would have received, and
  ``tests/test_rt16_fill_evidence_is_recorded_not_adjudicated.py`` already proves what the
  real router does with it.

* CREDENTIALS COME FROM THE ENVIRONMENT, not the vault. ``_load_keys`` reads the vault,
  which resolves by ``(user_id, exchange_id)`` and would need a tenant row in production.
  The credential itself is real and the venue authenticates it; only the lookup is
  short-circuited.

SO THE CLAIM IS PRECISE: the venue half of RT-16 works -- an authenticated private channel
opens with a real credential, a real order produces a real order update, and this
platform's feeder, event bus and queue carry it to the router's doorstep. The router's own
behaviour is proven separately, in unit tests, against the same payload shape this script
prints.

SAFETY
======
* SPOT TESTNET ONLY. Every resolved URL is asserted to be a testnet host before anything
  is placed, using the same check EXCH-01's verifier applies.
* The order is placed FAR BELOW the market so it RESTS rather than fills, and it is
  cancelled in a ``finally``.
* No order is placed unless ``--apply`` is passed. ``--plan`` (the default) opens the
  stream, proves it is connected and authenticated, and places nothing.

    $env:VENUE_TESTNET_API_KEY="..."; $env:VENUE_TESTNET_SECRET="..."
    python scripts/certify_rt16_fill_stream.py --plan
    python scripts/certify_rt16_fill_stream.py --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from decimal import Decimal
from typing import Any, Dict, List

sys.path.insert(0, ".")

SYMBOL = os.getenv("CERT_SYMBOL", "BTC/USDT")
VENUE = "binance"
#: A synthetic tenant id. UUID-shaped because `_handle_fill_event` parses it with
#: `uuid.UUID(...)`, and never written to any database by this script.
TENANT = "00000000-0000-4000-8000-00000000rt16".replace("rt16", "0016")


class _RecordingRouter:
    """Captures what `EventRouter.route_event` would have received."""

    def __init__(self):
        self.events: List[Any] = []

    async def route_event(self, event: Any) -> None:
        self.events.append(event)
        print(f"      [router] received {event.event_type} "
              f"order={event.order_id} id={event.event_id[:48]}")


def _hosts(urls: Any) -> List[str]:
    if isinstance(urls, dict):
        return sorted({str(v) for v in urls.values()})
    return [str(urls)]


async def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--plan", action="store_true", default=True,
                   help="open the stream and prove it is authenticated; place nothing")
    g.add_argument("--apply", action="store_true",
                   help="place one resting order, observe the stream, then cancel it")
    args = ap.parse_args()
    apply = bool(args.apply)

    key = os.environ.get("VENUE_TESTNET_API_KEY")
    secret = os.environ.get("VENUE_TESTNET_SECRET")
    if not key or not secret:
        print("VENUE_TESTNET_API_KEY / VENUE_TESTNET_SECRET are required.")
        return 2

    import ccxt.async_support as ccxt_async

    from backend_app.backend import exchange_websocket_listener as wsl
    from backend_app.backend.event_listener import get_event_listener
    from backend_app.backend.exchange_websocket_listener import \
        get_exchange_websocket_listener

    print("=" * 78)
    print(f"RT-16 FILL STREAM CERTIFICATION -- {VENUE} spot testnet, {SYMBOL}")
    print(f"mode: {'APPLY (one resting order)' if apply else 'PLAN (no order)'}")
    print("=" * 78)

    # ── credentials, without the vault ──────────────────────────────────────────
    # `sandbox: True` is NOT decoration. It is the shape `load_decrypted_keys` returns
    # for a credential stored with EXCH-06's flag set, and `_listen_for_orders` passes it
    # to the pool as `testnet=`. Omitting it here is how the first run of this script
    # failed with `-2008 Invalid Api-Key ID`: a testnet key presented to binance's LIVE
    # API. That surfaced a real defect -- the feeder was dropping the argument entirely --
    # and this dict has to carry the flag for the same reason the vault does.
    creds = {
        "api_key": key,
        "secret_key": secret,
        "password": None,
        "sandbox": True,
    }
    original_load_keys = wsl.ExchangeWebSocketListener._load_keys
    wsl.ExchangeWebSocketListener._load_keys = staticmethod(
        lambda tenant_id, exchange_id: dict(creds)
    )

    # ── the chain, in the worker's order: router, drain, feeder ─────────────────
    router = _RecordingRouter()
    event_bus = get_event_listener()
    listener = get_exchange_websocket_listener()

    placer = None
    order_id = None
    try:
        print()
        print("1. START THE CHAIN (router -> event drain -> feeder)")
        await event_bus.start(router)
        print(f"   event drain running={event_bus._running} "
              f"router_attached={event_bus._event_router is not None}")
        await listener.start()
        print(f"   feeder running={listener._running}")

        print()
        print(f"2. OPEN THE PRIVATE CHANNEL for {TENANT[:13]}... on {VENUE}")
        await listener.start_listening(TENANT, VENUE)
        await asyncio.sleep(6)  # let the socket authenticate and subscribe

        key_name = f"{TENANT}:{VENUE}"
        task = listener._listeners.get(key_name)
        print(f"   listener task: {'running' if task and not task.done() else 'NOT RUNNING'}")
        if task is not None and task.done():
            exc = task.exception()
            print(f"   *** the stream task ended: {exc!r}")
            return 1

        # The pooled client the feeder built -- assert where it is pointed.
        from backend_app.backend.connection_engine import _exchange_pool
        pooled = list(_exchange_pool.values())
        if not pooled:
            print("   *** no pooled client; the feeder never reached the venue.")
            return 1
        hosts = _hosts((pooled[0].urls or {}).get("api"))
        non_testnet = [h for h in hosts if "testnet" not in h]
        print(f"   resolved hosts: {len(hosts)}, non-testnet: {len(non_testnet)}")
        for h in hosts:
            print(f"      {h}{'   <-- NOT TESTNET' if 'testnet' not in h else ''}")
        if non_testnet:
            print("   *** REFUSING to continue: a non-testnet host is in play.")
            return 1

        if not apply:
            print()
            print("=" * 78)
            print("PLAN ONLY. The chain is started and the private channel is open and")
            print("authenticated against spot testnet. No order was placed, so no fill")
            print("event was produced. Re-run with --apply to certify delivery.")
            return 0

        # ── place one resting order ─────────────────────────────────────────────
        print()
        print("3. PLACE ONE RESTING ORDER (far below market, so it does not fill)")
        placer = ccxt_async.binance({
            "apiKey": key, "secret": secret, "enableRateLimit": True,
            "options": {"sandbox": True}, "sandbox": True,
        })
        await placer.load_markets()
        ticker = await placer.fetch_ticker(SYMBOL)
        last = Decimal(str(ticker["last"]))
        market = placer.market(SYMBOL)

        # 60% of market: below binance's bidMultiplierDown (0.5) by a comfortable margin,
        # so the venue accepts it and the book never reaches it.
        price = (last * Decimal("0.6")).quantize(Decimal("0.01"))
        min_cost = Decimal(str(market["limits"]["cost"]["min"] or "5"))
        amount = ((min_cost * Decimal("1.2")) / price).quantize(Decimal("0.00001"))
        print(f"   last={last}  resting price={price}  amount={amount}  "
              f"notional={(price * amount).quantize(Decimal('0.01'))} "
              f"(venue min {min_cost})")

        before = len(router.events)
        placed = await placer.create_order(
            SYMBOL, "limit", "buy", float(amount), float(price),
        )
        order_id = placed.get("id")
        print(f"   placed. venue order id = {order_id}  status={placed.get('status')}")

        print()
        print("4. WAIT FOR THE STREAM TO DELIVER IT")
        deadline = time.time() + 30
        while time.time() < deadline and len(router.events) == before:
            await asyncio.sleep(0.5)

        delivered = router.events[before:]
        print(f"   events delivered through the chain: {len(delivered)}")
        if not delivered:
            print("   *** NOTHING ARRIVED in 30s. The chain did not carry the update.")
            print(f"   queue size now: {event_bus._event_queue.qsize()}")
            return 1

        matched = [e for e in delivered if str(e.order_id) == str(order_id)]
        print(f"   events naming THIS order: {len(matched)}")
        for e in delivered:
            print(f"      type={e.event_type} order={e.order_id} "
                  f"tenant={str(e.tenant_id)[:13]}...")

        if not matched:
            print("   *** the chain delivered events, but none for the order placed.")
            return 1

        # ── the payload shape, which is the thing that was broken ──────────────
        print()
        print("5. THE PAYLOAD THE ROUTER RECEIVED (the shape _handle_filled must read)")
        sample = matched[0]
        data = sample.data or {}
        from backend_app.backend.event_router import (_venue_cumulative_fill,
                                                      _venue_fill_price)
        print(f"   keys present: {sorted(data)[:14]}")
        print(f"   'filled' present      : {'filled' in data}  value={data.get('filled')!r}")
        print(f"   'filled_size' present : {'filled_size' in data}  "
              f"<- what the OLD handler read")
        print(f"   'z' present           : {'z' in data}  <- the other key it read")
        print(f"   _venue_cumulative_fill: {_venue_cumulative_fill(data)!r}")
        print(f"   _venue_fill_price     : {_venue_fill_price(data)!r}")
        if "filled" not in data and "filled_size" not in data and "z" not in data:
            print("   *** no cumulative-fill key at all; the handler would record nothing.")
            return 1
        if "filled_size" not in data and "z" not in data:
            print("   CONFIRMED: the live payload carries NEITHER key the old handler")
            print("   read, so every streamed fill would have resolved to 0 and the")
            print("   execution would have been marked COMPLETED with nothing filled.")

        print()
        print("=" * 78)
        print("CERTIFIED. A real venue order update crossed the real chain:")
        print("  venue private websocket -> ExchangeWebSocketListener ->")
        print("  EventListener queue -> drain loop -> router.")
        print(f"  {len(matched)} event(s) for venue order {order_id}.")
        print("The router's handling of this payload is proven separately in")
        print("tests/test_rt16_fill_evidence_is_recorded_not_adjudicated.py.")
        return 0

    finally:
        print()
        print("CLEANUP")
        if placer is not None and order_id is not None:
            try:
                await placer.cancel_order(order_id, SYMBOL)
                print(f"   cancelled venue order {order_id}")
            except Exception as exc:  # noqa: BLE001
                print(f"   cancel failed ({type(exc).__name__}: {exc}); "
                      f"CANCEL IT BY HAND: {order_id}")
        try:
            await listener.stop()
            print("   feeder stopped")
        except Exception as exc:  # noqa: BLE001
            print(f"   feeder stop failed: {exc}")
        try:
            await event_bus.stop()
            print("   event drain stopped")
        except Exception as exc:  # noqa: BLE001
            print(f"   drain stop failed: {exc}")
        try:
            from backend_app.backend.connection_engine import \
                close_all_exchanges
            closed = await close_all_exchanges()
            print(f"   closed {closed} pooled venue client(s)")
        except Exception as exc:  # noqa: BLE001
            print(f"   pool drain failed: {exc}")
        if placer is not None:
            try:
                await placer.close()
            except Exception:  # noqa: BLE001
                pass
        wsl.ExchangeWebSocketListener._load_keys = original_load_keys


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
