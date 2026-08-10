"""
Tests for dashboard aggregation service fixes.

Tests for:
1. TelemetryEngine async boundary fix
2. Authenticated Supabase context fix
3. Dashboard endpoint authentication
4. Telemetry sync boundary regression test
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient

from backend_app.main import app
from backend_app.core.dependencies import get_current_user


class TestDashboardAggregationService:
    """Test dashboard aggregation service fixes."""

    def test_telemetry_sync_boundary(self):
        """Test that get_telemetry is synchronous and not awaited incorrectly."""
        from backend_app.core.dependencies import get_telemetry
        from backend_app.core.state import app_state

        # Mock the telemetry engine
        mock_telemetry = MagicMock()
        with patch('backend_app.core.state.app_state') as mock_state:
            mock_state.telemetry = mock_telemetry

            # Call get_telemetry - should return synchronously
            result = get_telemetry()

            # Should return the same object without await
            assert result is mock_telemetry
            assert result == mock_telemetry

    @patch('backend_app.backend.dashboard_aggregation_service.get_dashboard_service')
    @patch('backend_app.core.dependencies.create_request_supabase')
    def test_dashboard_passes_user_object(self, mock_create_supabase, mock_get_service):
        """Test that dashboard receives full user object with access_token."""
        from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService

        # Mock user with access_token
        mock_user = {
            "id": "test_user_123",
            "email": "test@example.com",
            "access_token": "valid_jwt_token",
            "tenant_id": "test_user_123"
        }

        # Mock dashboard service
        mock_service = MagicMock()
        mock_service.get_dashboard_data = AsyncMock(return_value={
            "overview": {"total_value": 1000.0},
            "strategies": {"total": 0, "items": []}
        })
        mock_get_service.return_value = mock_service

        service = DashboardAggregationService()

        # Call with user object
        result = service._get_supabase(mock_user)

        # Should call create_request_supabase with access_token
        mock_create_supabase.assert_called_once_with("valid_jwt_token")


class TestDashboardEndpoint:
    """Test dashboard endpoint fixes."""

    def _user(self):
        """Mock authenticated user."""
        return {
            "id": "test_user_123",
            "email": "test@example.com",
            "tenant_id": "test_user_123",
            "access_token": "valid_jwt_token",
            "role": "authenticated",
            "app_metadata": {}
        }

    @patch('backend_app.backend.dashboard_aggregation_service.get_dashboard_service')
    def test_dashboard_endpoint_authenticated_success(self, mock_get_service):
        """Test that dashboard endpoint succeeds with authenticated user."""
        mock_service = MagicMock()
        mock_service.get_dashboard_data = AsyncMock(return_value={
            "overview": {"total_value": 1000.0, "today_pnl": 50.0},
            "strategies": {"total": 0, "items": []},
            "equity_curve": []
        })
        mock_get_service.return_value = mock_service

        app.dependency_overrides[get_current_user] = lambda: self._user()

        try:
            client = TestClient(app)
            response = client.get("/api/dashboard", headers={"Authorization": "Bearer valid_jwt_token"})

            assert response.status_code == 200
            data = response.json()
            assert "overview" in data
            assert "strategies" in data
        finally:
            app.dependency_overrides.clear()

    def test_dashboard_endpoint_unauthenticated_rejected(self):
        """Test that dashboard endpoint rejects unauthenticated requests."""
        from fastapi import HTTPException

        def mock_none_user():
            raise HTTPException(status_code=401, detail="Missing Authorization header")

        app.dependency_overrides[get_current_user] = mock_none_user

        try:
            client = TestClient(app)
            response = client.get("/api/dashboard")

            assert response.status_code == 401
        finally:
            app.dependency_overrides.clear()

    @patch('backend_app.backend.dashboard_aggregation_service.get_dashboard_service')
    def test_dashboard_endpoint_preserves_all_fields(self, mock_get_service):
        """Test that dashboard response preserves all expected fields."""
        mock_service = MagicMock()
        mock_service.get_dashboard_data = AsyncMock(return_value={
            "overview": {"total_value": 1000.0, "today_pnl": 50.0, "today_return_pct": 5.0, "unrealized_pnl": 50.0, "available_balance": 950.0},
            "subscription": {"tier": "free", "billing_status": "active"},
            "usage": {"strategies": 0, "strategies_limit": 3},
            "strategies": {"total": 0, "active": 0, "paused": 0, "items": []},
            "marketplace": {"available_count": 0},
            "risk": {"risk_level": "low"},
            "notifications": {"unread_count": 0},
            "referrals": {"referral_code": ""},
            "health": {"exchange_api_latency_ms": 0},
            "exchange": {"total_exchanges": 0},
            "recent_activity": {"signals": [], "insights": []},
            "equity_curve": []
        })
        mock_get_service.return_value = mock_service

        app.dependency_overrides[get_current_user] = lambda: self._user()

        try:
            client = TestClient(app)
            response = client.get("/api/dashboard", headers={"Authorization": "Bearer valid_jwt_token"})

            assert response.status_code == 200
            data = response.json()

            # Verify all expected fields are present
            expected_fields = [
                "overview", "subscription", "usage", "strategies",
                "marketplace", "risk", "notifications", "referrals",
                "health", "exchange", "recent_activity", "equity_curve"
            ]
            for field in expected_fields:
                assert field in data, f"Missing field: {field}"
        finally:
            app.dependency_overrides.clear()

    def test_dashboard_telemetry_sync_boundary(self):
        """Test that dashboard telemetry accessor is synchronous and doesn't raise TypeError."""
        from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService

        service = DashboardAggregationService()

        # Should be able to call _get_telemetry() without await
        # This should NOT raise TypeError
        try:
            telemetry = service._get_telemetry()
            # Success - no TypeError
            assert True
        except TypeError as e:
            if "can't be used in 'await' expression" in str(e):
                pytest.fail(f"_get_telemetry() still tries to await a sync function: {e}")
            else:
                raise
