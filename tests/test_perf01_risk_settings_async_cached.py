"""PERF-01 -- the per-trade risk-settings read no longer blocks the event loop or
hits Supabase on every single trade.

THE DEFECT
    core/risk_manager.py::_load_user_risk_settings was a plain `def`, called with no
    `await` from `validate_trade_request` (an `async def`), that did `from supabase
    import create_client; sb = create_client(...)` -- a brand-new, synchronous
    `httpx.Client` -- and then a blocking `.execute()`, UNCONDITIONALLY, on every single
    trade. Both the client construction and the network round trip ran on the event
    loop, blocking every other coroutine in the process for their duration, not just
    the one request making the trade.

THE FIX
    1. `_load_user_risk_settings` is now `async def`, awaiting
       `core.dependencies.get_service_supabase_async()` -- a connection-pooled,
       memoized async PostgREST client (mirrors `create_request_supabase_async`'s
       pool) -- instead of constructing a client and blocking per call.
    2. Redis-cached under its own key (`risk_manager_trade_settings:{user_id}`,
       deliberately distinct from routers/risk.py's `risk_settings:{uid}`, which caches
       a wider shape) for `RISK_SETTINGS_CACHE_TTL` seconds (default 300,
       env-overridable), with explicit invalidation
       (`invalidate_user_risk_settings_cache`) wired into `PUT /api/risk/settings`.

WHAT THIS FILE PROVES
    1. `_load_user_risk_settings` is a coroutine function (so the one real caller's
       `await` actually awaits something).
    2. Its source no longer imports `supabase.create_client` -- the historical
       blocking-client construction is gone, not just hidden behind an await.
    3. A cache hit returns the cached value WITHOUT ever touching the DB client.
    4. A cache miss queries the async client once and then POPULATES the cache, so the
       next call is a hit.
    5. `invalidate_user_risk_settings_cache` deletes exactly the key the read path
       reads.
    6. `routers/risk.py::update_risk_settings`'s source calls
       `invalidate_user_risk_settings_cache` -- a settings change is visible to the
       very next trade, not up to five minutes later.
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend_app.core import risk_manager

REPO = Path(__file__).resolve().parents[1]
RISK_MANAGER_SOURCE = REPO / "backend_app" / "core" / "risk_manager.py"
RISK_ROUTER_SOURCE = REPO / "backend_app" / "routers" / "risk.py"


def test_load_user_risk_settings_is_a_coroutine_function():
    assert inspect.iscoroutinefunction(risk_manager._load_user_risk_settings)


def test_source_no_longer_imports_the_blocking_sync_client():
    tree = ast.parse(RISK_MANAGER_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "supabase":
            names = {alias.name for alias in node.names}
            assert "create_client" not in names, (
                "risk_manager.py still imports supabase.create_client -- the blocking, "
                "brand-new-client-per-call construction this finding is about"
            )


@pytest.mark.asyncio
async def test_cache_hit_never_touches_the_database_client():
    cached_payload = {"max_daily_loss": 777.0, "max_positions": 4, "max_leverage": 2}

    fake_redis = AsyncMock()
    fake_redis.get.return_value = json.dumps(cached_payload)

    with patch(
        "backend_app.core.cache.redis_manager.redis_manager", fake_redis
    ), patch(
        "backend_app.core.dependencies.get_service_supabase_async",
        new=AsyncMock(side_effect=AssertionError("must not be called on a cache hit")),
    ) as db_client_factory:
        result = await risk_manager._load_user_risk_settings("user-abc")

    assert result == cached_payload
    db_client_factory.assert_not_called()


@pytest.mark.asyncio
async def test_cache_miss_queries_the_async_client_once_then_populates_the_cache():
    db_row = {
        "user_id": "user-abc",
        "max_daily_loss": 1234.0,
        "max_positions": 7,
        "max_leverage": 4,
    }

    fake_redis = AsyncMock()
    fake_redis.get.return_value = None  # miss

    fake_resp = MagicMock()
    fake_resp.data = [db_row]

    # .table()/.select()/.eq() are synchronous query-builder methods on the real
    # PostgREST client; only .execute() is a coroutine.
    fake_table = MagicMock()
    fake_table.select.return_value = fake_table
    fake_table.eq.return_value = fake_table
    fake_table.execute = AsyncMock(return_value=fake_resp)

    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    with patch(
        "backend_app.core.cache.redis_manager.redis_manager", fake_redis
    ), patch(
        "backend_app.core.dependencies.get_service_supabase_async",
        new=AsyncMock(return_value=fake_client),
    ) as db_client_factory:
        result = await risk_manager._load_user_risk_settings("user-abc")

    assert result == {"max_daily_loss": 1234.0, "max_positions": 7, "max_leverage": 4}
    db_client_factory.assert_awaited_once()
    fake_client.table.assert_called_once_with("risk_settings")
    fake_table.eq.assert_called_once_with("user_id", "user-abc")

    # The cache was populated with exactly what was returned.
    fake_redis.setex.assert_awaited_once()
    cache_key, ttl, cached_json = fake_redis.setex.await_args.args
    assert cache_key == "risk_manager_trade_settings:user-abc"
    assert ttl == risk_manager.RISK_SETTINGS_CACHE_TTL
    assert json.loads(cached_json) == result


@pytest.mark.asyncio
async def test_invalidate_deletes_exactly_the_key_the_read_path_reads():
    fake_redis = AsyncMock()
    with patch("backend_app.core.cache.redis_manager.redis_manager", fake_redis):
        await risk_manager.invalidate_user_risk_settings_cache("user-xyz")
    fake_redis.delete.assert_awaited_once_with("risk_manager_trade_settings:user-xyz")


def test_update_risk_settings_endpoint_invalidates_the_trade_settings_cache():
    """Static check: the settings-update route's source actually calls the
    invalidation helper, so a changed limit reaches the very next trade."""
    source = RISK_ROUTER_SOURCE.read_text(encoding="utf-8")
    assert "invalidate_user_risk_settings_cache" in source
