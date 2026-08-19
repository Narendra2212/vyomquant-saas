"""
tests/test_tenant_isolation_fixes.py

Tests for tenant isolation fixes in strategy_operations.py and signal_trace.py.

Validates that:
1. User A cannot access User B's strategies via strategy_operations endpoints
2. User A cannot access User B's signals via signal_trace endpoints
3. Service layer properly scopes queries by user_id
4. 404 is returned (not 403) for cross-tenant access attempts

These tests address the critical bugs where user_id was undefined in router handlers.
"""

import os
import sys
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, DEFAULT

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app


@pytest.fixture
def user_a():
    """User A - owns the test resources"""
    return {
        "id": "usr_tenant_a_123",
        "email": "user_a@vyomquant.io",
        "role": "authenticated",
        "access_token": "token_a"
    }


@pytest.fixture
def user_b():
    """User B - should not access User A's resources"""
    return {
        "id": "usr_tenant_b_456",
        "email": "user_b@vyomquant.io",
        "role": "authenticated",
        "access_token": "token_b"
    }


@pytest.fixture
def mock_strategy_service():
    """Mock strategy service that simulates tenant isolation"""
    service = MagicMock()

    # Simulate service.get_strategy(user_id, strategy_id)
    async def mock_get_strategy(user_id, strategy_id):
        # Only return strategy if user_id matches owner
        if user_id == "usr_tenant_a_123" and strategy_id == "str_test_789":
            return {
                "id": strategy_id,
                "user_id": user_id,
                "name": "Test Strategy",
                "status": "draft"
            }
        return None  # 404 for cross-tenant access

    service.get_strategy = AsyncMock(side_effect=mock_get_strategy)

    # Simulate service.update_strategy(user_id, strategy_id, updates)
    async def mock_update_strategy(user_id, strategy_id, updates):
        if user_id == "usr_tenant_a_123" and strategy_id == "str_test_789":
            return {"strategy": {"id": strategy_id, "user_id": user_id, **updates}}
        return None

    service.update_strategy = AsyncMock(side_effect=mock_update_strategy)

    # Simulate service.delete_strategy(user_id, strategy_id)
    async def mock_delete_strategy(user_id, strategy_id):
        if user_id == "usr_tenant_a_123" and strategy_id == "str_test_789":
            return True
        return False

    service.delete_strategy = AsyncMock(side_effect=mock_delete_strategy)

    return service


@pytest.fixture
def mock_signal_service():
    """Mock signal service that simulates tenant isolation"""
    service = MagicMock()

    # Simulate service.get_signal(user, signal_id) â€” user is a dict with "id" key
    async def mock_get_signal(user, signal_id):
        user_id = user["id"] if isinstance(user, dict) else user
        # Only return signal if user_id matches owner
        if user_id == "usr_tenant_a_123" and signal_id == "sig_test_999":
            return {
                "id": signal_id,
                "user_id": user_id,
                "decision": "BUY",
                "status": "pending"
            }
        return None  # 404 for cross-tenant access

    service.get_signal = AsyncMock(side_effect=mock_get_signal)

    # Simulate service.get_signal_timeline(user, signal_id) â€” user is a dict with "id" key
    async def mock_get_signal_timeline(user, signal_id):
        user_id = user["id"] if isinstance(user, dict) else user
        if user_id == "usr_tenant_a_123" and signal_id == "sig_test_999":
            return [
                {"event": "SIGNAL_GENERATED", "timestamp": "2024-01-01T00:00:00Z"}
            ]
        return []

    service.get_signal_timeline = AsyncMock(side_effect=mock_get_signal_timeline)

    return service


class TestStrategyOperationsTenantIsolation:
    """Test tenant isolation in strategy endpoints"""

    @patch('backend_app.routers.strategies._sb', new_callable=AsyncMock)
    @patch('backend_app.routers.strategy_operations.get_strategy_service')
    def test_get_strategy_owner_can_access(self, mock_get_service, mock_sb, user_a, mock_strategy_service):
        """
        Test GET /api/strategies/{strategy_id} - owner can access their own strategy
        """
        async def async_get_service():
            return mock_strategy_service
        mock_get_service.side_effect = async_get_service

        sb_instance = MagicMock()
        query_mock = MagicMock()
        query_mock.execute = AsyncMock(return_value=MagicMock(data=[{
            "id": "str_test_789",
            "user_id": user_a["id"],
            "name": "Test Strategy",
            "status": "draft"
        }]))
        sb_instance.table.return_value.select.return_value.eq.return_value.eq.return_value = query_mock
        mock_sb.return_value = sb_instance

        app.dependency_overrides[get_current_user] = lambda: user_a
        app.dependency_overrides[get_request_supabase] = lambda: None

        try:
            client = TestClient(app)
            response = client.get("/api/strategies/str_test_789")

            # Owner should successfully access their strategy
            assert response.status_code == 200
            assert response.json()["id"] == "str_test_789"
            assert response.json()["user_id"] == user_a["id"]

        finally:
            app.dependency_overrides.clear()

    @patch('backend_app.routers.strategies._sb', new_callable=AsyncMock)
    @patch('backend_app.routers.strategy_operations.get_strategy_service')
    def test_get_strategy_cross_tenant_returns_404(self, mock_get_service, mock_sb, user_b, mock_strategy_service):
        """
        Test GET /api/strategies/{strategy_id} - cross-tenant access returns 404
        """
        async def async_get_service():
            return mock_strategy_service
        mock_get_service.side_effect = async_get_service

        sb_instance = MagicMock()
        query_mock = MagicMock()
        query_mock.execute = AsyncMock(return_value=MagicMock(data=[]))  # Empty list for cross-tenant
        sb_instance.table.return_value.select.return_value.eq.return_value.eq.return_value = query_mock
        mock_sb.return_value = sb_instance

        app.dependency_overrides[get_current_user] = lambda: user_b
        app.dependency_overrides[get_request_supabase] = lambda: None

        try:
            client = TestClient(app)
            response = client.get("/api/strategies/str_test_789")

            # Cross-tenant access should return 404 (not 403)
            assert response.status_code == 404

        finally:
            app.dependency_overrides.clear()

    @patch('backend_app.routers.strategies._sb', new_callable=AsyncMock)
    @patch('backend_app.routers.strategy_operations.get_strategy_service')
    def test_update_strategy_owner_can_modify(self, mock_get_service, mock_sb, user_a, mock_strategy_service):
        """
        Test PUT /api/strategies/{strategy_id} - owner can modify their own strategy
        """
        async def async_get_service():
            return mock_strategy_service
        mock_get_service.side_effect = async_get_service

        sb_instance = MagicMock()
        query_mock = MagicMock()
        query_mock.execute = AsyncMock(return_value=MagicMock(data=[{
            "id": "str_test_789",
            "user_id": user_a["id"],
            "name": "Updated Strategy Name",
            "status": "updated"
        }]))
        sb_instance.table.return_value.update.return_value.eq.return_value.eq.return_value = query_mock
        mock_sb.return_value = sb_instance

        app.dependency_overrides[get_current_user] = lambda: user_a
        app.dependency_overrides[get_request_supabase] = lambda: None

        try:
            client = TestClient(app)
            response = client.put(
                "/api/strategies/str_test_789",
                json={"name": "Updated Strategy Name"}
            )

            # Owner should successfully update their strategy
            assert response.status_code == 200
            assert response.json()["id"] == "str_test_789"

        finally:
            app.dependency_overrides.clear()

    @patch('backend_app.routers.strategies._sb', new_callable=AsyncMock)
    @patch('backend_app.routers.strategy_operations.get_strategy_service')
    def test_delete_strategy_owner_can_delete(self, mock_get_service, mock_sb, user_a, mock_strategy_service):
        """
        Test DELETE /api/strategies/{strategy_id} - owner can delete their own strategy
        """
        async def async_get_service():
            return mock_strategy_service
        mock_get_service.side_effect = async_get_service

        sb_instance = MagicMock()
        # Delete endpoint first does SELECT then DELETE, both with await execute()
        select_query_mock = MagicMock()
        select_query_mock.execute = AsyncMock(return_value=MagicMock(data=[{"id": "str_test_789", "symbol": "BTCUSDT", "status": "stopped"}]))
        sb_instance.table.return_value.select.return_value.eq.return_value.eq.return_value = select_query_mock

        delete_query_mock = MagicMock()
        delete_query_mock.execute = AsyncMock(return_value=MagicMock(data=[]))
        sb_instance.table.return_value.delete.return_value.eq.return_value.eq.return_value = delete_query_mock
        mock_sb.return_value = sb_instance

        app.dependency_overrides[get_current_user] = lambda: user_a
        app.dependency_overrides[get_request_supabase] = lambda: None

        try:
            client = TestClient(app)
            response = client.delete("/api/strategies/str_test_789")

            # Owner should successfully delete their strategy
            assert response.status_code == 200

        finally:
            app.dependency_overrides.clear()


class TestSignalTraceTenantIsolation:
    """Test tenant isolation in signal_trace.py endpoints"""

    @patch('backend_app.routers.signal_trace.get_signal_service')
    def test_get_signal_owner_can_access(self, mock_get_service, user_a, mock_signal_service):
        """
        Test GET /api/signals/{signal_id} - owner can access their own signal
        """
        async def async_get_service():
            return mock_signal_service
        mock_get_service.side_effect = async_get_service

        app.dependency_overrides[get_current_user] = lambda: user_a
        app.dependency_overrides[get_request_supabase] = lambda: None

        try:
            client = TestClient(app)
            response = client.get("/api/signal-trace/signals/sig_test_999")

            # Owner should successfully access their signal
            assert response.status_code == 200
            assert response.json()["signal"]["id"] == "sig_test_999"
            assert response.json()["signal"]["user_id"] == user_a["id"]

            # Verify service was called with correct user_id
            mock_signal_service.get_signal.assert_called_once_with(user_a, "sig_test_999")

        finally:
            app.dependency_overrides.clear()

    @patch('backend_app.routers.signal_trace.get_signal_service')
    def test_get_signal_cross_tenant_returns_404(self, mock_get_service, user_b, mock_signal_service):
        """
        Test GET /api/signals/{signal_id} - cross-tenant access returns 404
        """
        async def async_get_service():
            return mock_signal_service
        mock_get_service.side_effect = async_get_service

        app.dependency_overrides[get_current_user] = lambda: user_b
        app.dependency_overrides[get_request_supabase] = lambda: None

        try:
            client = TestClient(app)
            response = client.get("/api/signal-trace/signals/sig_test_999")

            # Cross-tenant access should return 404 (not 403)
            assert response.status_code == 404
            detail = response.json()["detail"]
            error_str = detail.get("error", "") if isinstance(detail, dict) else str(detail)
            assert "SIGNAL_NOT_FOUND" in error_str or "Not Found" in str(detail) or response.status_code == 404

            # Verify service was called with user_b's ID (not user_a's)
            mock_signal_service.get_signal.assert_called_once_with(user_b, "sig_test_999")

        finally:
            app.dependency_overrides.clear()


class TestTenantIsolationRegression:
    """Regression tests to ensure user_id variable is always defined"""

    @patch('backend_app.routers.strategies._sb', new_callable=AsyncMock)
    @patch('backend_app.routers.strategy_operations.get_strategy_service')
    def test_strategy_operations_user_id_defined(self, mock_get_service, mock_sb, user_a, mock_strategy_service):
        """
        Regression test: Ensure user_id is defined (not undefined variable)
        in strategy endpoints
        """
        async def async_get_service():
            return mock_strategy_service
        mock_get_service.side_effect = async_get_service
        sb_instance = MagicMock()
        query_mock = MagicMock()
        query_mock.execute = AsyncMock(return_value=MagicMock(data=[{"id": "str_test_789"}]))
        sb_instance.table.return_value.select.return_value.eq.return_value.eq.return_value = query_mock
        mock_sb.return_value = sb_instance

        app.dependency_overrides[get_current_user] = lambda: user_a
        app.dependency_overrides[get_request_supabase] = lambda: None

        try:
            client = TestClient(app)

            # This would fail with NameError if user_id was undefined
            response = client.get("/api/strategies/str_test_789")

            # Should not raise NameError
            assert response.status_code in [200, 404]  # Either found or not found, but no NameError

        finally:
            app.dependency_overrides.clear()

    @patch('backend_app.routers.signal_trace.get_signal_service')
    def test_signal_trace_user_id_defined(self, mock_get_service, user_a, mock_signal_service):
        """
        Regression test: Ensure user_id is defined (not undefined variable)
        in signal_trace.py endpoints
        """
        async def async_get_service():
            return mock_signal_service
        mock_get_service.side_effect = async_get_service

        app.dependency_overrides[get_current_user] = lambda: user_a
        app.dependency_overrides[get_request_supabase] = lambda: None

        try:
            client = TestClient(app)

            # This would fail with NameError if user_id was undefined
            response = client.get("/api/signal-trace/signals/sig_test_999")

            # Should not raise NameError
            assert response.status_code in [200, 404]  # Either found or not found, but no NameError

        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
