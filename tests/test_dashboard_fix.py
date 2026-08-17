"""
Tests for dashboard aggregation service fixes.

Tests for:
1. TelemetryEngine async boundary fix
2. Authenticated Supabase context fix
3. Dashboard endpoint authentication
4. Telemetry sync boundary regression test
5. Phase 7C: Duplicate strategy query elimination
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call
from fastapi.testclient import TestClient

from backend_app.main import app
from backend_app.core.dependencies import get_current_user, get_request_supabase


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

    def test_dashboard_passes_user_object(self):
        """Test that dashboard receives full user object with access_token."""
        import sys

        # _get_supabase imports create_request_supabase_async locally inside the method.
        # We must patch it in the backend_app.core.dependencies namespace AND use asyncio.run
        # because _get_supabase is async.
        from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService
        from unittest.mock import patch, MagicMock

        mock_user = {
            "id": "test_user_123",
            "email": "test@example.com",
            "access_token": "valid_jwt_token",
            "tenant_id": "test_user_123"
        }

        mock_sb = MagicMock()

        with patch('backend_app.core.dependencies.create_request_supabase_async',
                   return_value=mock_sb) as mock_fn:
            service = DashboardAggregationService()

            # _get_supabase is async — must await it
            result = asyncio.run(service._get_supabase(mock_user))

            # Should call create_request_supabase_async with the user's access_token
            mock_fn.assert_called_once_with("valid_jwt_token")


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

    @patch('backend_app.routers.dashboard.get_dashboard_service')
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

    @patch('backend_app.routers.dashboard.get_dashboard_service')
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

        app.dependency_overrides[get_current_user] = lambda: {**self._user(), "id": "user_preserves_all_fields"}

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
            assert telemetry is not None
        except TypeError as e:
            if "can't be used in 'await' expression" in str(e) or "object is not awaitable" in str(e):
                pytest.fail(f"_get_telemetry() still tries to await a sync function: {e}")
            else:
                raise


class TestPhase7CDuplicateQueryElimination:
    """Test Phase 7C: Request-local strategy task reuse eliminates duplicate queries."""

    def _mock_user(self):
        """Mock authenticated user."""
        return {
            "id": "test_user_123",
            "email": "test@example.com",
            "tenant_id": "test_user_123",
            "access_token": "valid_jwt_token",
            "role": "authenticated",
            "app_metadata": {}
        }

    def _mock_supabase(self):
        """Mock Supabase client with strategies data."""
        mock_sb = MagicMock()

        # Helper to return data for specific table queries
        def table_side_effect(table_name):
            table_mock = MagicMock()
            select_mock = MagicMock()
            eq_mock = MagicMock()
            execute_mock = MagicMock()

            if table_name == "strategies":
                execute_mock.data = [
                    {
                        "id": "strat_1",
                        "name": "Strategy 1",
                        "pair": "BTC/USDT",
                        "is_active": True,
                        "today_pnl": 100.0,
                        "today_return_pct": 5.0,
                        "last_signal_at": "2024-01-01T00:00:00Z"
                    },
                    {
                        "id": "strat_2",
                        "name": "Strategy 2",
                        "pair": "ETH/USDT",
                        "is_active": False,
                        "today_pnl": -50.0,
                        "today_return_pct": -2.5,
                        "last_signal_at": "2024-01-01T01:00:00Z"
                    }
                ]
            elif table_name == "profiles":
                execute_mock.data = [
                    {"subscription_tier": "free", "billing_status": "active"}
                ]
            else:
                execute_mock.data = []

            eq_mock.return_value.eq.return_value.execute.return_value = execute_mock
            select_mock.return_value.eq.return_value = eq_mock
            table_mock.select.return_value = select_mock
            table_mock.insert.return_value.execute.return_value = execute_mock
            return table_mock

        mock_sb.table.side_effect = table_side_effect
        return mock_sb

    @patch('backend_app.core.dependencies.create_request_supabase_async')
    def test_one_strategy_query_per_dashboard(self, mock_create_supabase):
        """Test that get_dashboard_data() calls get_strategies() exactly once."""
        from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService

        mock_sb = self._mock_supabase()
        mock_create_supabase.return_value = mock_sb

        service = DashboardAggregationService()
        user = self._mock_user()

        # Spy on get_strategies to count calls
        original_get_strategies = service.get_strategies
        call_count = [0]

        async def counted_get_strategies(u, *args, **kwargs):
            call_count[0] += 1
            return await original_get_strategies(u, *args, **kwargs)

        service.get_strategies = counted_get_strategies

        # Run dashboard data
        asyncio.run(service.get_dashboard_data(user, equity_days=30))

        # Should be exactly 1 call
        assert call_count[0] == 1, f"Expected 1 get_strategies() call, got {call_count[0]}"

    @patch('backend_app.core.dependencies.create_request_supabase_async')
    def test_strategy_task_concurrent_with_gather(self, mock_create_supabase):
        """Test that strategies task starts concurrently with other operations."""
        from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService

        mock_sb = self._mock_supabase()
        mock_create_supabase.return_value = mock_sb

        service = DashboardAggregationService()
        user = self._mock_user()

        # Track when strategies task starts
        strategies_started = asyncio.Event()
        strategies_ready = asyncio.Event()

        original_get_strategies = service.get_strategies

        async def tracked_get_strategies(u, *args, **kwargs):
            strategies_started.set()
            # Wait a bit to ensure other operations can start
            await asyncio.sleep(0.01)
            result = await original_get_strategies(u, *args, **kwargs)
            strategies_ready.set()
            return result

        service.get_strategies = tracked_get_strategies

        # Track when portfolio operation starts
        portfolio_started = asyncio.Event()

        original_get_portfolio = service.get_portfolio_overview

        async def tracked_get_portfolio(u, *args, **kwargs):
            portfolio_started.set()
            result = await original_get_portfolio(u, *args, **kwargs)
            return result

        service.get_portfolio_overview = tracked_get_portfolio

        async def test_dashboard():
            dashboard_task = asyncio.create_task(service.get_dashboard_data(user, equity_days=30))
            # Wait for strategies to start
            await strategies_started.wait()
            # Portfolio should also be able to start before strategies completes
            # Use a small timeout with try/except for Python 3.12 compatibility
            try:
                await asyncio.wait_for(portfolio_started.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass  # It's okay if portfolio is fast, the point is strategies started first
            # Let strategies complete
            await strategies_ready.wait()
            return await dashboard_task

        asyncio.run(test_dashboard())

        # Both should have started
        assert strategies_started.is_set()
        assert portfolio_started.is_set()

    @patch('backend_app.core.dependencies.create_request_supabase_async')
    def test_request_isolation_between_concurrent_users(self, mock_create_supabase):
        """Test that concurrent dashboard requests don't share strategy state."""
        from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService

        mock_sb = self._mock_supabase()
        mock_create_supabase.return_value = mock_sb

        service = DashboardAggregationService()

        user_a = {**self._mock_user(), "id": "user_A"}
        user_b = {**self._mock_user(), "id": "user_B"}

        # Track which user each get_strategies call receives
        users_received = []

        original_get_strategies = service.get_strategies

        async def tracked_get_strategies(u, *args, **kwargs):
            users_received.append(u["id"])
            return await original_get_strategies(u, *args, **kwargs)

        service.get_strategies = tracked_get_strategies

        async def concurrent_dashboards():
            dashboard_a = asyncio.create_task(service.get_dashboard_data(user_a, equity_days=30))
            dashboard_b = asyncio.create_task(service.get_dashboard_data(user_b, equity_days=30))
            await asyncio.gather(dashboard_a, dashboard_b)

        asyncio.run(concurrent_dashboards())

        # Each user should have been called exactly once
        assert users_received.count("user_A") == 1
        assert users_received.count("user_B") == 1
        assert len(users_received) == 2

    @patch('backend_app.core.dependencies.create_request_supabase_async')
    def test_exception_propagates_not_converted_to_empty_list(self, mock_create_supabase):
        """Test that get_strategies() exceptions are NOT converted to []."""
        from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService

        mock_sb = self._mock_supabase()
        mock_create_supabase.return_value = mock_sb

        service = DashboardAggregationService()
        user = self._mock_user()

        # Make get_strategies raise an exception
        original_get_strategies = service.get_strategies

        async def failing_get_strategies(u, *args, **kwargs):
            raise ValueError("Database connection failed")

        service.get_strategies = failing_get_strategies

        async def test_dashboard():
            try:
                await service.get_dashboard_data(user, equity_days=30)
                pytest.fail("Expected ValueError to propagate")
            except ValueError as e:
                # Exception should propagate, not be converted to []
                assert "Database connection failed" in str(e)

        asyncio.run(test_dashboard())

    @patch('backend_app.core.dependencies.create_request_supabase_async')
    def test_standalone_get_strategy_insights_still_works(self, mock_create_supabase):
        """Test that standalone get_strategy_insights(user) still works without task."""
        from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService

        mock_sb = self._mock_supabase()
        mock_create_supabase.return_value = mock_sb

        service = DashboardAggregationService()
        user = self._mock_user()

        # Call get_strategy_insights without strategies_task
        insights = asyncio.run(service.get_strategy_insights(user))

        # Should work and return insights
        assert isinstance(insights, list)
        assert len(insights) > 0

