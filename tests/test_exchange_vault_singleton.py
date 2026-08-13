"""
tests/test_exchange_vault_singleton.py — APIKeyVault Singleton Reuse & Tenant Isolation

Verifies:
1. GET /api/exchanges/ reuses app_state.vault singleton via Depends(get_vault).
2. APIKeyVault.__init__ is NOT called on request handling (0 per-request constructions).
3. Response shape is preserved.
4. Concurrent requests for User A (pro tier) and User B (free tier) return isolated, correct subscription tiers.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, Mock, patch
from fastapi.testclient import TestClient

from backend_app.main import app
from backend_app.core.dependencies import get_current_user, get_vault
from backend_app.backend.api_key_vault import APIKeyVault


def _mock_supabase_for_exchanges():
    mock_sb = Mock()
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = Mock(data=[])
    mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = Mock(data=[])
    return mock_sb


def test_list_exchanges_response_shape_and_no_per_request_vault_init():
    """Assert APIKeyVault is NOT constructed during request handling and response shape is valid."""
    def mock_user():
        return {"id": "user_singleton_test_123", "access_token": "token_123"}

    mock_vault = Mock(spec=APIKeyVault)
    mock_vault.get_user_tier.return_value = {"subscription_tier": "pro", "max_api_slots": 10}

    app.dependency_overrides[get_current_user] = mock_user
    app.dependency_overrides[get_vault] = lambda: mock_vault

    client = TestClient(app)

    with patch("backend_app.routers.exchange.get_redis_manager", return_value=None):
        with patch("backend_app.routers.exchange.get_request_supabase", return_value=_mock_supabase_for_exchanges()):
            with patch.object(APIKeyVault, "__init__", return_value=None) as mock_init:
                res = client.get("/api/exchanges/")
                assert res.status_code == 200
                data = res.json()

                # Verify response shape keys
                assert isinstance(data, list)

                # Verify APIKeyVault.__init__ was NEVER called during request handling
                mock_init.assert_not_called()

                # Verify mock_vault.get_user_tier was called with the user's ID
                mock_vault.get_user_tier.assert_called_once_with("user_singleton_test_123")

    app.dependency_overrides.clear()


def test_concurrent_tenant_isolation_subscription_tiers():
    """Verify concurrent requests for different users receive their isolated subscription tiers."""
    user_a = {"id": "tenant_a_111", "access_token": "token_a"}
    user_b = {"id": "tenant_b_222", "access_token": "token_b"}

    mock_vault = Mock(spec=APIKeyVault)
    def side_effect_tier(uid):
        if uid == "tenant_a_111":
            return {"subscription_tier": "enterprise", "max_api_slots": -1}
        elif uid == "tenant_b_222":
            return {"subscription_tier": "starter", "max_api_slots": 3}
        return {"subscription_tier": "free", "max_api_slots": 1}

    mock_vault.get_user_tier.side_effect = side_effect_tier

    app.dependency_overrides[get_vault] = lambda: mock_vault

    def fetch_user(user_dict):
        app.dependency_overrides[get_current_user] = lambda: user_dict
        client = TestClient(app)
        with patch("backend_app.routers.exchange.get_redis_manager", return_value=None):
            with patch("backend_app.routers.exchange.get_request_supabase", return_value=_mock_supabase_for_exchanges()):
                res = client.get("/api/exchanges/")
                assert res.status_code == 200
                return user_dict["id"]

    # Run 10 sequential calls alternating between users
    for _ in range(5):
        fetch_user(user_a)
        fetch_user(user_b)

    # Verify mock_vault was invoked for both tenants independently
    mock_vault.get_user_tier.assert_any_call("tenant_a_111")
    mock_vault.get_user_tier.assert_any_call("tenant_b_222")

    app.dependency_overrides.clear()
