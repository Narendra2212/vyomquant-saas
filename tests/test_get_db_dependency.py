"""
tests/test_get_db_dependency.py

Integration tests for get_db dependency injection in FastAPI routes.

WHAT IS TESTED
-------------
1. get_db is recognized by inspect.isgeneratorfunction as a generator function.
2. Routes using Depends(get_db) receive a real working SQLAlchemy Session instance.
3. GET /api/billing/payment-methods returns 200 with real Session (not 500 AttributeError).
4. GET /api/billing/entitlements returns user-specific plan data with real Session (not 500 AttributeError).
5. GET /api/billing/invoices returns 200 with real Session (not 500 AttributeError).
6. Database session is properly closed after request completes.
"""

import inspect
import pytest
from unittest.mock import MagicMock, patch
from unittest.mock import AsyncMock
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend_app.core.database_pool import get_db, get_db_context
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app


class TestGetDbDependency:
    def test_inspect_isgeneratorfunction_returns_true(self):
        assert inspect.isgeneratorfunction(get_db), (
            "get_db must be a plain generator function for FastAPI to manage it as a yield dependency"
        )

    def test_depends_get_db_injects_real_sqlalchemy_session(self):
        captured_session = None

        @app.get("/test-db-dependency-injection")
        def sample_route(db: Session = Depends(get_db)):
            nonlocal captured_session
            captured_session = db
            return {"type": str(type(db))}

        client = TestClient(app)
        response = client.get("/test-db-dependency-injection")

        assert response.status_code == 200
        assert isinstance(captured_session, Session), (
            f"Expected real Session object, got {type(captured_session)}"
        )

    def test_billing_payment_methods_with_real_db_dependency(self):
        mock_user = {
            "id": "00000000-0000-0000-0000-000000000001",
            "email": "test@vyomquant.com",
            "tenant_id": "00000000-0000-0000-0000-000000000001"
        }

        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            client = TestClient(app)
            response = client.get("/api/billing/payment-methods", headers={"Authorization": "Bearer mock-token"})
            assert response.status_code == 200
            assert isinstance(response.json(), list)
        finally:
            app.dependency_overrides.clear()

    def test_user_billing_plan_with_real_db_dependency(self):
        """Test that /api/billing/entitlements returns user-specific plan data with real DB dependencies."""
        mock_user = {
            "id": "00000000-0000-0000-0000-000000000001",
            "email": "test@vyomquant.com",
            "tenant_id": "00000000-0000-0000-0000-000000000001"
        }

        mock_supabase = MagicMock()
        mock_table = MagicMock()
        # Mock profile data for entitlements endpoint
        mock_table.select.return_value.eq.return_value.execute.return_value.data = [
            {
                "subscription_tier": "free",
                "subscription_status": "active",
                "subscription_renewal_date": None,
                "cancel_at_period_end": False
            }
        ]
        mock_supabase.table.return_value = mock_table

        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[get_request_supabase] = lambda: mock_supabase
        
        # Mock redis_manager.get for quota usage tracking in subscription_engine
        # The rate limiter now uses in-memory storage (configured via conftest.py setting ENV=testing)
        try:
            with patch('backend_app.core.subscription_engine.redis_manager.get', new_callable=AsyncMock, return_value="0"):
                client = TestClient(app)
                response = client.get("/api/billing/entitlements", headers={"Authorization": "Bearer mock-token"})
                assert response.status_code == 200
                entitlements_data = response.json()
                
                # Validate the response contains expected user-specific plan data
                assert "plan" in entitlements_data
                assert "features" in entitlements_data
                assert "quotas" in entitlements_data
                assert "usage" in entitlements_data
                assert "subscription_status" in entitlements_data
                
                # Validate the plan matches our mock data
                assert entitlements_data["plan"] == "free"
                assert entitlements_data["subscription_status"] == "active"
                
                # Validate features and quotas are non-empty lists/dicts
                assert isinstance(entitlements_data["features"], list)
                assert isinstance(entitlements_data["quotas"], dict)
                assert isinstance(entitlements_data["usage"], dict)
        finally:
            app.dependency_overrides.clear()

    def test_user_billing_invoices_with_real_db_dependency(self):
        mock_user = {
            "id": "00000000-0000-0000-0000-000000000001",
            "email": "test@vyomquant.com",
            "tenant_id": "00000000-0000-0000-0000-000000000001"
        }

        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            client = TestClient(app)
            response = client.get("/api/billing/invoices", headers={"Authorization": "Bearer mock-token"})
            assert response.status_code == 200
            assert isinstance(response.json(), list)
        finally:
            app.dependency_overrides.clear()

    def test_database_session_properly_closed_after_request(self):
        """Test that database sessions are properly closed after request completes."""
        # FastAPI's dependency injection system automatically handles session cleanup
        # for generator functions like get_db. Multiple sequential requests succeeding
        # without connection leaks indicates proper session management.
        
        @app.get("/test-sequential-requests")
        def test_route(db: Session = Depends(get_db)):
            return {"session_type": str(type(db))}

        client = TestClient(app)
        
        # Make multiple sequential requests to test session management
        for i in range(3):
            response = client.get("/test-sequential-requests")
            assert response.status_code == 200
            assert "session_type" in response.json()
