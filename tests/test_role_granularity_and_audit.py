"""
tests/test_role_granularity_and_audit.py

Unit tests verifying role granularity and required audit justifications:
1. get_admin_user allows admin, support, and operator roles for read/moderate endpoints.
2. get_operator_user restricts high-blast-radius endpoints to operator role only.
3. set_user_status and global_kill_switch require operator permission AND non-empty reason.
4. Audit log entries are written to Redis/logger on status changes and kill switch events.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_admin_user, get_current_user, get_operator_user, get_request_supabase
from backend_app.main import app

client = TestClient(app)


class TestRoleGranularityDependencies:
    def test_get_admin_user_roles(self):
        admin_user = {"id": "1", "app_metadata": {"role": "admin"}}
        support_user = {"id": "2", "app_metadata": {"role": "support"}}
        operator_user = {"id": "3", "app_metadata": {"role": "operator"}}
        normal_user = {"id": "4", "app_metadata": {"role": "user"}}

        assert asyncio.run(get_admin_user(admin_user)) == admin_user
        assert asyncio.run(get_admin_user(support_user)) == support_user
        assert asyncio.run(get_admin_user(operator_user)) == operator_user

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_admin_user(normal_user))
        assert exc_info.value.status_code == 403

    def test_get_operator_user_roles(self):
        operator_user = {"id": "1", "app_metadata": {"role": "operator"}}
        admin_user = {"id": "2", "app_metadata": {"role": "admin"}}
        support_user = {"id": "3", "app_metadata": {"role": "support"}}
        normal_user = {"id": "4", "app_metadata": {"role": "user"}}

        assert asyncio.run(get_operator_user(operator_user)) == operator_user

        # Standard admin without operator role MUST be rejected from destructive actions
        for u in (admin_user, support_user, normal_user):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(get_operator_user(u))
            assert exc_info.value.status_code == 403
            assert "OPERATOR PERMISSION REQUIRED" in exc_info.value.detail


class TestHighBlastRadiusEndpoints:
    def test_kill_switch_rejected_for_admin_role_not_operator(self):
        admin_user = {"id": "100", "email": "admin@vyomquant.io", "app_metadata": {"role": "admin"}}
        app.dependency_overrides[get_current_user] = lambda: admin_user

        try:
            res = client.post(
                "/api/admin/kill-all",
                json={"confirm_code": "HALT", "reason": "Emergency shutdown test"},
                headers={"Authorization": "Bearer token"}
            )
            assert res.status_code == 403
            assert "OPERATOR PERMISSION REQUIRED" in res.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    def test_kill_switch_rejected_for_empty_reason(self):
        operator_user = {"id": "101", "email": "op@vyomquant.io", "app_metadata": {"role": "operator"}}
        app.dependency_overrides[get_current_user] = lambda: operator_user

        try:
            # Missing or whitespace-only reason rejected
            res = client.post(
                "/api/admin/kill-all",
                json={"confirm_code": "HALT", "reason": "   "},
                headers={"Authorization": "Bearer token"}
            )
            assert res.status_code in (400, 422)
        finally:
            app.dependency_overrides.clear()

    def test_kill_switch_succeeds_for_operator_with_valid_reason(self):
        operator_user = {"id": "101", "email": "op@vyomquant.io", "app_metadata": {"role": "operator"}}
        app.dependency_overrides[get_current_user] = lambda: operator_user

        mock_fleet = MagicMock()
        mock_fleet.shutdown_all = AsyncMock()

        with patch("backend_app.routers.admin.get_fleet", return_value=mock_fleet):
            with patch("backend_app.routers.admin._log_admin_audit_event", AsyncMock()) as mock_audit:
                try:
                    res = client.post(
                        "/api/admin/kill-all",
                        json={"confirm_code": "HALT", "reason": "Market anomaly detected"},
                        headers={"Authorization": "Bearer token"}
                    )
                    assert res.status_code == 200
                    assert res.json()["status"] == "HALTED"
                    assert res.json()["reason"] == "Market anomaly detected"

                    # Verify audit log recorded operator identity and reason
                    mock_audit.assert_called_once()
                    assert mock_audit.call_args.kwargs["action"] == "GLOBAL_KILL_SWITCH"
                    assert mock_audit.call_args.kwargs["reason"] == "Market anomaly detected"
                    assert mock_audit.call_args.kwargs["operator"] == operator_user
                finally:
                    app.dependency_overrides.clear()

    def test_set_user_status_rejected_for_non_operator(self):
        admin_user = {"id": "100", "email": "admin@vyomquant.io", "app_metadata": {"role": "admin"}}
        app.dependency_overrides[get_current_user] = lambda: admin_user

        try:
            res = client.post(
                "/api/admin/users/target-123/status",
                json={"status": "frozen", "reason": "Suspicious trading activity"},
                headers={"Authorization": "Bearer token"}
            )
            assert res.status_code == 403
        finally:
            app.dependency_overrides.clear()

    def test_set_user_status_succeeds_for_operator_with_reason(self):
        operator_user = {"id": "101", "email": "op@vyomquant.io", "app_metadata": {"role": "operator"}}
        mock_supabase = MagicMock()

        app.dependency_overrides[get_current_user] = lambda: operator_user
        app.dependency_overrides[get_request_supabase] = lambda: mock_supabase

        with patch("backend_app.routers.admin.invalidate_profile_cache", AsyncMock()):
            with patch("backend_app.routers.admin._log_admin_audit_event", AsyncMock()) as mock_audit:
                try:
                    res = client.post(
                        "/api/admin/users/target-123/status",
                        json={"status": "frozen", "reason": "Risk threshold breach"},
                        headers={"Authorization": "Bearer token"}
                    )
                    assert res.status_code == 200
                    assert res.json()["is_frozen"] is True
                    assert res.json()["reason"] == "Risk threshold breach"

                    # Verify audit log call
                    mock_audit.assert_called_once()
                    assert mock_audit.call_args.kwargs["action"] == "USER_FROZEN"
                    assert mock_audit.call_args.kwargs["target"] == "target-123"
                    assert mock_audit.call_args.kwargs["reason"] == "Risk threshold breach"
                finally:
                    app.dependency_overrides.clear()
