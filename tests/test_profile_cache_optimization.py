"""
tests/test_profile_cache_optimization.py

Tests for the profile-cache optimizations introduced in backend_app/core/dependencies.py:
  1. Hit-rate counters increment correctly on hit / miss / Redis error
  2. create_request_supabase is NOT called when Redis has a cached value (lazy client)
  3. PROFILE_CACHE_TTL default is 60 s (not the old 300 s)
  4. TTL=0 bypass always queries Supabase (strictest security mode)
  5. P1-7 fail-closed policy: infrastructure failure still returns HTTP 503
  6. Frozen account still blocked correctly within TTL window
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

DUMMY_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJ1c2VyLTEyMyIsImVtYWlsIjoidXNlckB2eW9tcXVhbnQuaW8iLCJyb2xlIjoiYXV0aGVudGljYXRlZCJ9"
    ".signature"
)


def _reset_counters():
    """Reset the module-level hit-rate counters between tests."""
    import backend_app.core.dependencies as deps
    deps._profile_cache_hits = 0
    deps._profile_cache_misses = 0
    deps._profile_cache_errors = 0


# ---------------------------------------------------------------------------
# 1. Default TTL is 60 s
# ---------------------------------------------------------------------------

def test_default_ttl_is_60():
    """PROFILE_CACHE_TTL must default to 60 s, not the old 300 s."""
    # If the env var is not set the default should be 60
    from backend_app.core.dependencies import PROFILE_CACHE_TTL
    default = int(os.getenv("PROFILE_CACHE_TTL", "60"))
    assert PROFILE_CACHE_TTL == default, f"Expected {default}, got {PROFILE_CACHE_TTL}"


# ---------------------------------------------------------------------------
# 2. Hit counter increments on Redis HIT
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_hit_counter_increments_on_cache_hit():
    _reset_counters()
    import backend_app.core.dependencies as deps

    cached_profile = {"subscription_tier": "pro_999", "is_frozen": False}

    with patch.object(deps.redis_manager, "get", new_callable=AsyncMock,
                      return_value=json.dumps(cached_profile)):
        result = await deps._get_cached_profile("user-abc", DUMMY_JWT)

    assert result["is_frozen"] is False
    assert deps._profile_cache_hits == 1
    assert deps._profile_cache_misses == 0
    assert deps._profile_cache_errors == 0


# ---------------------------------------------------------------------------
# 3. Miss counter increments on Redis MISS
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_miss_counter_increments_on_cache_miss():
    _reset_counters()
    import backend_app.core.dependencies as deps

    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"subscription_tier": "pro_999", "is_frozen": False}
    ]

    with patch.object(deps.redis_manager, "get", new_callable=AsyncMock, return_value=None), \
         patch.object(deps.redis_manager, "setex", new_callable=AsyncMock), \
         patch("backend_app.core.dependencies.create_request_supabase", return_value=mock_supabase):
        result = await deps._get_cached_profile("user-def", DUMMY_JWT)

    assert result["is_frozen"] is False
    assert deps._profile_cache_hits == 0
    assert deps._profile_cache_misses == 1
    assert deps._profile_cache_errors == 0


# ---------------------------------------------------------------------------
# 4. Error counter increments on Redis failure
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_error_counter_increments_on_redis_failure():
    _reset_counters()
    import backend_app.core.dependencies as deps

    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"subscription_tier": "free", "is_frozen": False}
    ]

    with patch.object(deps.redis_manager, "get", side_effect=Exception("Redis down")), \
         patch.object(deps.redis_manager, "setex", new_callable=AsyncMock), \
         patch("backend_app.core.dependencies.create_request_supabase", return_value=mock_supabase):
        result = await deps._get_cached_profile("user-ghi", DUMMY_JWT)

    assert result["is_frozen"] is False
    assert deps._profile_cache_errors == 1


# ---------------------------------------------------------------------------
# 5. Lazy Supabase client — NOT created on cache hit
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_supabase_client_not_created_on_cache_hit():
    """
    When Redis returns a hit, create_request_supabase must NOT be called.
    This is the primary hot-path optimization.
    """
    _reset_counters()
    import backend_app.core.dependencies as deps

    cached_profile = {"subscription_tier": "elite_1999", "is_frozen": False}

    with patch.object(deps.redis_manager, "get", new_callable=AsyncMock,
                      return_value=json.dumps(cached_profile)), \
         patch("backend_app.core.dependencies.create_request_supabase") as mock_create:
        await deps._get_cached_profile("user-xyz", DUMMY_JWT)

    mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# 6. TTL=0 always queries Supabase (bypass mode)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_ttl_zero_bypasses_cache():
    """With PROFILE_CACHE_TTL=0, Redis should never be consulted."""
    _reset_counters()
    import backend_app.core.dependencies as deps

    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"subscription_tier": "pro_999", "is_frozen": False}
    ]

    original_ttl = deps.PROFILE_CACHE_TTL
    deps.PROFILE_CACHE_TTL = 0
    try:
        with patch.object(deps.redis_manager, "get", new_callable=AsyncMock) as mock_get, \
             patch("backend_app.core.dependencies.create_request_supabase", return_value=mock_supabase):
            result = await deps._get_cached_profile("user-ttl0", DUMMY_JWT)

        mock_get.assert_not_called()
        assert result["is_frozen"] is False
        assert deps._profile_cache_misses == 1
    finally:
        deps.PROFILE_CACHE_TTL = original_ttl


# ---------------------------------------------------------------------------
# 7. get_profile_cache_stats reflects counters correctly
# ---------------------------------------------------------------------------

def test_get_profile_cache_stats():
    import backend_app.core.dependencies as deps
    _reset_counters()
    deps._profile_cache_hits = 3
    deps._profile_cache_misses = 1
    deps._profile_cache_errors = 0

    stats = deps.get_profile_cache_stats()
    assert stats["hits"] == 3
    assert stats["misses"] == 1
    assert stats["total"] == 4
    assert stats["hit_rate_pct"] == 75.0
    assert stats["ttl_seconds"] == deps.PROFILE_CACHE_TTL
    _reset_counters()


# ---------------------------------------------------------------------------
# 8. P1-7: fail-closed policy still returns 503 on infrastructure failure
# ---------------------------------------------------------------------------

def test_infrastructure_failure_still_returns_503():
    async def _run():
        from backend_app.core.dependencies import get_current_user
        mock_payload = {
            "sub": "user-fail",
            "email": "fail@vyomquant.io",
            "tenant_id": "user-fail",
            "role": "authenticated",
        }
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=DUMMY_JWT)

        with patch("backend_app.core.auth_middleware.decode_token_local", return_value=mock_payload), \
             patch("backend_app.core.dependencies._get_cached_profile",
                   side_effect=RuntimeError("Both Redis and Supabase are down")):
            with pytest.raises(HTTPException) as exc:
                await get_current_user(credentials=creds)

        assert exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 9. Frozen account still blocked correctly
# ---------------------------------------------------------------------------

def test_frozen_account_still_blocked():
    async def _run():
        from backend_app.core.dependencies import get_current_user
        mock_payload = {
            "sub": "frozen-user",
            "email": "frozen@vyomquant.io",
            "tenant_id": "frozen-user",
            "role": "authenticated",
        }
        mock_profile = {"subscription_tier": "pro_999", "is_frozen": True}
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=DUMMY_JWT)

        with patch("backend_app.core.auth_middleware.decode_token_local", return_value=mock_payload), \
             patch("backend_app.core.dependencies._get_cached_profile",
                   new_callable=AsyncMock, return_value=mock_profile):
            with pytest.raises(HTTPException) as exc:
                await get_current_user(credentials=creds)

        assert exc.value.status_code == status.HTTP_403_FORBIDDEN
        assert "frozen" in exc.value.detail.lower()

    asyncio.run(_run())
