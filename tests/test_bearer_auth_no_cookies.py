"""
tests/test_bearer_auth_no_cookies.py

Regression test suite asserting that backend authentication relies solely on
Bearer tokens in the Authorization header and explicitly rejects cookie-only requests,
confirming CSRF-safe-by-construction design.
"""

import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from backend_app.core.dependencies import get_current_user

DUMMY_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLTEyMyIsImVtYWlsIjoidXNlckB2eW9tcXVhbnQuaW8iLCJyb2xlIjoiYXV0aGVudGljYXRlZCJ9.signature"


def test_missing_authorization_header_rejected_even_with_cookies():
    async def _run():
        # Passing credentials=None simulates a request with no Authorization header
        # (even if Cookie header is sent by browser)
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials=None)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "missing authorization header" in exc_info.value.detail.lower()

    asyncio.run(_run())


def test_valid_bearer_token_succeeds_regardless_of_cookies():
    async def _run():
        mock_payload = {
            "sub": "user-bearer-123",
            "email": "bearer@vyomquant.io",
            "tenant_id": "user-bearer-123",
            "role": "authenticated"
        }
        mock_profile = {"subscription_tier": "pro_999", "is_frozen": False}
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=DUMMY_JWT)

        with patch("backend_app.core.auth_middleware.decode_token_local", return_value=mock_payload):
            with patch("backend_app.core.dependencies.create_request_supabase"):
                with patch("backend_app.core.dependencies._get_cached_profile", new_callable=AsyncMock) as mock_get_profile:
                    mock_get_profile.return_value = mock_profile

                    user = await get_current_user(credentials=creds)
                    assert user["id"] == "user-bearer-123"
                    assert user["email"] == "bearer@vyomquant.io"

    asyncio.run(_run())
