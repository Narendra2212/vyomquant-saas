"""tests/test_api11_market_data_pooled_and_rate_limited.py

API-11 (P3) -- six market-data routes (``/candles``, ``/orderbook``, ``/ticker``,
``/funding``, ``/data/{symbol}/{timeframe}``, ``/data/historical``) required only the
cheapest possible authentication (a free Supabase signup) and had no route-specific
rate limit, and their public-fallback path (no stored exchange keys -- the common case
for a free account) opened a brand-new ``ConnectionEngine`` -- and therefore a fresh
``load_markets()`` handshake against the real venue -- on every single request. Together
this made the platform's own outbound IP a free, unthrottled proxy for probing a real
exchange's public market data.

THE FIX
-------
1. ``@limiter.limit("60/minute")`` added to all six routes (the same bound ``/symbols``
   already used).
2. The public-fallback branch of ``_get_data_engine`` now goes through
   ``get_or_create_exchange`` -- the SAME pool the authenticated branch already uses --
   under a shared pseudo-identity, so repeated public requests for one exchange reuse
   one pooled client instead of opening a new one every time.

WHAT THIS FILE PROVES
----------------------
1. the public fallback calls ``get_or_create_exchange`` (not a raw ``ConnectionEngine``)
   with no credentials, under the same identity on every call -- so a second public
   request for the same exchange hits the pool, not a fresh connection;
2. a static sweep of ``market.py``'s source confirms every one of the six routes named
   above carries a ``@limiter.limit(...)`` decorator.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend_app.routers.market import (
    _PUBLIC_MARKET_DATA_POOL_IDENTITY,
    _get_data_engine,
)

REPO = Path(__file__).resolve().parents[1]
MARKET_SOURCE = REPO / "backend_app" / "routers" / "market.py"

_RATE_LIMITED_ROUTE_HANDLERS = {
    "get_candles",
    "get_orderbook",
    "get_ticker",
    "get_funding_rate",
    "get_market_data",
    "get_historical_data",
}


class _KeylessVault:
    """Every caller reaches the public-fallback branch: no stored keys at all."""

    def load_decrypted_keys(self, user_id, exchange_id, access_token=None):
        return None


@pytest.mark.asyncio
async def test_the_public_fallback_goes_through_the_shared_pool_not_a_fresh_connection():
    fake_exchange = object()
    with patch(
        "backend_app.routers.market.get_or_create_exchange",
        new=AsyncMock(return_value=fake_exchange),
    ) as pooled:
        await _get_data_engine({"id": "user-a"}, _KeylessVault(), "binance")
        await _get_data_engine({"id": "user-b"}, _KeylessVault(), "binance")

    assert pooled.call_count == 2
    for call in pooled.await_args_list:
        assert call.kwargs["user_id"] == _PUBLIC_MARKET_DATA_POOL_IDENTITY, (
            "every public-fallback caller must be pooled under the SAME shared "
            "identity -- a different user_id per real caller would defeat pooling "
            "exactly as the pre-fix raw ConnectionEngine did"
        )
        assert call.kwargs["exchange_id"] == "binance"
        assert "api_key" not in call.kwargs or call.kwargs["api_key"] is None, (
            "the public fallback must never carry a credential -- that would make a "
            "shared pool key a cross-user credential leak"
        )


def test_every_named_market_data_route_carries_a_rate_limit_decorator():
    tree = ast.parse(MARKET_SOURCE.read_text(encoding="utf-8"))

    def _is_limiter_limit_call(decorator) -> bool:
        if not isinstance(decorator, ast.Call):
            return False
        func = decorator.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "limit"
            and isinstance(func.value, ast.Name)
            and func.value.id == "limiter"
        )

    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in _RATE_LIMITED_ROUTE_HANDLERS:
            found[node.name] = any(_is_limiter_limit_call(d) for d in node.decorator_list)

    missing = [name for name in _RATE_LIMITED_ROUTE_HANDLERS if name not in found]
    assert not missing, f"handler(s) not found in market.py at all: {missing}"

    unlimited = [name for name, has_limit in found.items() if not has_limit]
    assert not unlimited, (
        f"the following market-data route handler(s) carry no @limiter.limit(...) "
        f"decorator: {unlimited}"
    )
