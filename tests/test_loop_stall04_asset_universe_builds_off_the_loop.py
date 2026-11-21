"""LOOP-STALL-04 -- the asset-universe refresh does its CPU work off the event loop.

Production 2026-11-21: after each start (and every 3 h) ~40 LOOP-STALL events of 2-3 s in
ccxt parse_market / set_markets / deep_extend (Bybit, Binance market maps) and in
asset_universe.to_dict's deepcopy, all on the API's event loop.
"""
from __future__ import annotations

import threading

import pytest

from backend_app.backend import asset_universe as au

_MARKET = {
    "symbol": "BTC/USDT", "base": "BTC", "quote": "USDT", "type": "spot", "spot": True,
    "active": True, "precision": {"price": 0.01, "amount": 0.0001},
    "limits": {"cost": {"min": 5}, "amount": {"min": 0.0001}},
}


@pytest.mark.asyncio
async def test_markets_load_and_merge_off_the_event_loop_thread(monkeypatch):
    loop_thread = threading.get_ident()
    seen = {}

    async def _load(exchange_id):
        seen["load"] = threading.get_ident()
        return {"BTC/USDT": dict(_MARKET)}

    real_merge = au.merge_markets

    def _merge(per_exchange):
        seen["merge"] = threading.get_ident()
        return real_merge(per_exchange)

    written = {}

    async def _write(universe, payload=None):
        seen["write"] = threading.get_ident()
        written["payload"] = payload
        return True

    monkeypatch.setattr(au, "supported_exchange_ids", lambda: ["binance"])
    monkeypatch.setattr(au, "load_exchange_markets", _load)
    monkeypatch.setattr(au, "merge_markets", _merge)
    monkeypatch.setattr(au, "write_cached_universe", _write)

    universe = await au.refresh_universe()

    assert universe.assets and universe.exchanges == ["binance"]
    assert seen["load"] != loop_thread
    assert seen["merge"] != loop_thread
    assert seen["write"] == loop_thread            # the cache write stays on the loop
    assert written["payload"] == universe.to_payload()  # payload was built in the thread


@pytest.mark.asyncio
async def test_every_venue_failing_still_raises_unavailable(monkeypatch):
    async def _load(exchange_id):
        raise ConnectionError("down")

    monkeypatch.setattr(au, "supported_exchange_ids", lambda: ["binance", "bybit"])
    monkeypatch.setattr(au, "load_exchange_markets", _load)

    with pytest.raises(au.AssetUniverseUnavailable):
        await au.refresh_universe()
    assert au._last_refresh_error and "binance" in au._last_refresh_error


@pytest.mark.asyncio
async def test_deployment_state_runs_a_sync_query_off_the_loop():
    from backend_app.backend.deployment_state import _run

    loop_thread = threading.get_ident()
    ran_on = []

    class _Query:
        def execute(self):
            ran_on.append(threading.get_ident())
            return "rows"

    assert await _run(_Query()) == "rows"
    assert ran_on and ran_on[0] != loop_thread


@pytest.mark.asyncio
async def test_deployment_state_still_awaits_an_async_query():
    from backend_app.backend.deployment_state import _run

    class _AsyncQuery:
        async def execute(self):
            return "async rows"

    assert await _run(_AsyncQuery()) == "async rows"
