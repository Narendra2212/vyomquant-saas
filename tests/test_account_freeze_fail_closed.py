"""
tests/test_account_freeze_fail_closed.py

Unit tests verifying that get_current_user in core/dependencies.py enforces a fail-closed policy
when account-freeze status verification fails, returning HTTP 503 Service Unavailable instead of
silently allowing access as not-frozen, while maintaining normal access for valid active users.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from backend_app.core.dependencies import get_current_user, _get_cached_profile

DUMMY_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLTEyMyIsImVtYWlsIjoidXNlckB2eW9tcXVhbnQuaW8iLCJyb2xlIjoiYXV0aGVudGljYXRlZCJ9.signature"


def test_frozen_user_raises_http_403():
    async def _run():
        mock_payload = {
            "sub": "frozen-user-123",
            "email": "frozen@vyomquant.io",
            "tenant_id": "frozen-user-123",
            "role": "authenticated"
        }
        mock_profile = {"subscription_tier": "pro_999", "is_frozen": True}
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=DUMMY_JWT)

        with patch("backend_app.core.auth_middleware.decode_token_local", return_value=mock_payload):
            with patch("backend_app.core.dependencies.create_request_supabase", MagicMock()):
                with patch("backend_app.core.dependencies._get_cached_profile", new_callable=AsyncMock) as mock_get_profile:
                    mock_get_profile.return_value = mock_profile

                    with pytest.raises(HTTPException) as exc_info:
                        await get_current_user(credentials=creds)

                    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
                    assert "account is frozen" in exc_info.value.detail.lower()

    asyncio.run(_run())


def test_active_user_succeeds():
    async def _run():
        mock_payload = {
            "sub": "active-user-123",
            "email": "active@vyomquant.io",
            "tenant_id": "active-user-123",
            "role": "authenticated"
        }
        mock_profile = {"subscription_tier": "pro_999", "is_frozen": False}
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=DUMMY_JWT)

        with patch("backend_app.core.auth_middleware.decode_token_local", return_value=mock_payload):
            with patch("backend_app.core.dependencies.create_request_supabase", MagicMock()):
                with patch("backend_app.core.dependencies._get_cached_profile", new_callable=AsyncMock) as mock_get_profile:
                    mock_get_profile.return_value = mock_profile

                    user = await get_current_user(credentials=creds)
                    assert user["id"] == "active-user-123"
                    assert user["email"] == "active@vyomquant.io"

    asyncio.run(_run())


def test_infrastructure_failure_raises_http_503_fail_closed():
    async def _run():
        mock_payload = {
            "sub": "user-456",
            "email": "user@vyomquant.io",
            "tenant_id": "user-456",
            "role": "authenticated"
        }
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=DUMMY_JWT)

        with patch("backend_app.core.auth_middleware.decode_token_local", return_value=mock_payload):
            with patch("backend_app.core.dependencies.create_request_supabase", MagicMock()):
                with patch("backend_app.core.dependencies._get_cached_profile", side_effect=RuntimeError("Database and Redis down")):
                    with pytest.raises(HTTPException) as exc_info:
                        await get_current_user(credentials=creds)

                    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
                    assert "unable to verify account security status" in exc_info.value.detail.lower()

    asyncio.run(_run())


def test_redis_outage_with_working_supabase_succeeds():
    async def _run():
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"subscription_tier": "pro_999", "is_frozen": False}
        ]

        with patch("backend_app.core.dependencies.redis_manager.get", side_effect=Exception("Redis connection error")):
            with patch("backend_app.core.dependencies.redis_manager.setex", AsyncMock()):
                profile = await _get_cached_profile("user-789", mock_supabase)
                assert profile["is_frozen"] is False
                assert profile["subscription_tier"] == "pro_999"

    asyncio.run(_run())
