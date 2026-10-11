"""Verify the capability API renders the real artifact truthfully. Offline.

Calls the router's handlers directly rather than through an HTTP client, because the
subject under test is the contract — the shape and the honesty of the numbers — not
FastAPI's routing.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend_app.core.exchange_capability_registry import get_capability_registry
from backend_app.routers.exchange_capabilities import (connectivity_summary, ladder,
                                                       launch_list, list_capabilities)


async def main() -> None:
    reg = get_capability_registry()
    reg.load_inventory()
    merged = reg.load_verification_results()
    print(f"merged evidence for {merged} venues\n")

    print("=" * 78)
    print("GET /api/exchanges/connectivity/summary")
    print("=" * 78)
    s = await connectivity_summary()
    print(json.dumps(s, indent=2)[:2600])

    print()
    print("=" * 78)
    print("GET /api/exchanges/connectivity/launch-list  (counts)")
    print("=" * 78)
    ll = await launch_list()
    print(f"market_data_verified : {ll['market_data_verified']['count']}")
    print(f"connectivity_only    : {ll['connectivity_only']['count']}")
    print(f"not_verified         : {ll['not_verified']['count']}")
    print()
    print("first 10 market-data-verified venues:")
    for e in ll["market_data_verified"]["exchanges"][:10]:
        print(f"  {e['exchange_id']:<20} {e['state']:<28} ws={e['websocket']}")

    print()
    print("=" * 78)
    print("TRUTHFULNESS CHECKS")
    print("=" * 78)
    # Explicit arguments: calling a FastAPI handler directly bypasses dependency
    # resolution, so the `Query(...)` defaults arrive as Query objects rather than as
    # their values. That is a property of this probe, not of the endpoint.
    caps = await list_capabilities(
        state_at_least=None, market_data_only=False, tradable_only=False
    )
    tradable = [e for e in caps["exchanges"] if e["trading_authorized"]]
    print(f"venues reported in /capabilities        : {caps['count']}")
    print(f"venues claiming trading_authorized      : {len(tradable)}")
    assert not tradable, "a venue claims trading authorization"

    routable = [e for e in caps["exchanges"]
                if e["order_execution"]["platform_can_route"]]
    blocked = [e for e in caps["exchanges"] if e["order_execution"]["safety_blocked"]]
    print(f"venues the platform can route an order to: {len(routable)} "
          f"{[e['exchange_id'] for e in routable]}")
    print(f"venues safety-blocked                    : {len(blocked)} "
          f"{[e['exchange_id'] for e in blocked]}")

    sandbox_claims = [e for e in caps["exchanges"] if e["sandbox"]["verified"]]
    print(f"venues claiming SANDBOX_VERIFIED         : {len(sandbox_claims)}")
    assert not sandbox_claims, "a venue claims a verified sandbox"

    md = [e for e in caps["exchanges"] if e["market_data"]["verified"]]
    print(f"venues with MARKET_DATA_VERIFIED         : {len(md)}")

    unverified_nulls = [
        e["exchange_id"] for e in caps["exchanges"]
        if e["sandbox"]["routing_proven"] is None
    ]
    print(f"venues with sandbox routing UNVERIFIED (null, not false): "
          f"{len(unverified_nulls)}")

    print()
    print("=" * 78)
    print("GET /api/exchanges/connectivity/states")
    print("=" * 78)
    for row in (await ladder())["ladder"]:
        print(f"  {row['value']} {row['name']:<30} {row['meaning'][:72]}")

    print()
    print("ALL TRUTHFULNESS ASSERTIONS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
