"""
tests/test_admin_auth.py

Unit and integration tests for get_admin_user authorization dependency and admin endpoints.

WHAT IS TESTED
-------------
1. get_admin_user allows access for real Supabase JWT shape: top-level role="authenticated", app_metadata={"role": "admin"}.
2. get_admin_user rejects user_metadata={"role": "admin"} with 403 (Phase 7B F-02 fix).
3. get_admin_user rejects non-admin users with top-level role="authenticated" and app_metadata={} with 403.
4. get_admin_user rejects users with missing app_metadata key gracefully (no KeyError/AttributeError) with 403.
5. Admin endpoints (GET /api/admin/users, GET /api/library/admin/pending) return authorized response for admins and 403 for non-admins.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_admin_user, get_current_user, get_request_supabase
from backend_app.main import app

client = TestClient(app)


class TestGetAdminUserDependency:
    def test_admin_user_with_app_metadata(self):
        # Real-world Supabase Admin JWT shape
        user = {
            "id": "admin-uuid-1234",
            "email": "admin@vyomquant.io",
            "role": "authenticated",  # Postgres RLS role
            "app_metadata": {"role": "admin"},
            "user_metadata": {}
        }
        result = asyncio.run(get_admin_user(user))
        assert result == user
        assert result["id"] == "admin-uuid-1234"

    def test_admin_user_with_user_metadata_rejected(self):
        # Phase 7B F-02: user_metadata is user-editable; role in user_metadata MUST NOT grant admin access
        user = {
            "id": "attacker-uuid-5678",
            "email": "attacker@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {},
            "user_metadata": {"role": "admin"}
        }
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_admin_user(user))
        assert exc_info.value.status_code == 403
        assert "Admin role required" in exc_info.value.detail

    def test_non_admin_user_rejected(self):
        user = {
            "id": "user-uuid-9999",
            "email": "user@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {"role": "user"},
            "user_metadata": {}
        }
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_admin_user(user))
        assert exc_info.value.status_code == 403
        assert "Admin role required" in exc_info.value.detail

    def test_missing_app_metadata_rejected_no_error(self):
        user = {
            "id": "user-uuid-8888",
            "email": "user2@vyomquant.io",
            "role": "authenticated"
            # app_metadata key absent
        }
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_admin_user(user))
        assert exc_info.value.status_code == 403


class TestAdminEndpointsIntegration:
    def test_get_admin_users_endpoint_positive_and_negative(self):
        admin_user = {
            "id": "admin-uuid-100",
            "email": "admin@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {"role": "admin"}
        }
        normal_user = {
            "id": "user-uuid-200",
            "email": "normal@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {"role": "user"}
        }

        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_query.limit.return_value = mock_query
        mock_query.ilike.return_value = mock_query
        mock_query.order.return_value.execute = AsyncMock(return_value=MagicMock(data=[
            {"id": "admin-uuid-100", "email": "admin@vyomquant.io", "subscription_tier": "admin", "is_frozen": False}]))
        mock_supabase.table.return_value.select.return_value = mock_query

        # Override get_current_user and get_request_supabase
        app.dependency_overrides[get_current_user] = lambda: admin_user
        app.dependency_overrides[get_request_supabase] = lambda: mock_supabase
        try:
            res_admin = client.get("/api/admin/users", headers={"Authorization": "Bearer mock_token"})
            assert res_admin.status_code == 200
        finally:
            app.dependency_overrides.clear()

        # Override get_current_user with normal_user
        app.dependency_overrides[get_current_user] = lambda: normal_user
        app.dependency_overrides[get_request_supabase] = lambda: mock_supabase
        try:
            res_user = client.get("/api/admin/users", headers={"Authorization": "Bearer mock_token"})
            assert res_user.status_code == 403
        finally:
            app.dependency_overrides.clear()

    def test_library_admin_pending_endpoint(self):
        admin_user = {
            "id": "admin-uuid-100",
            "email": "admin@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {"role": "admin"}
        }
        normal_user = {
            "id": "user-uuid-200",
            "email": "normal@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {}
        }

        mock_svc = MagicMock()
        mock_svc.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = []

        app.dependency_overrides[get_current_user] = lambda: admin_user
        try:
            with patch("backend_app.routers.library._build_service_client", return_value=mock_svc):
                res_admin = client.get("/api/library/admin/pending", headers={"Authorization": "Bearer mock_token"})
                assert res_admin.status_code == 200
        finally:
            app.dependency_overrides.clear()

        app.dependency_overrides[get_current_user] = lambda: normal_user
        try:
            with patch("backend_app.routers.library._build_service_client", return_value=mock_svc):
                res_user = client.get("/api/library/admin/pending", headers={"Authorization": "Bearer mock_token"})
                assert res_user.status_code == 403
        finally:
            app.dependency_overrides.clear()
