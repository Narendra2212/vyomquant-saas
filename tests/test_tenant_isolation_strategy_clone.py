"""
tests/test_tenant_isolation_strategy_clone.py — Tenant Isolation Test for Strategy Clone

Tests that users cannot clone strategies belonging to other tenants.
This validates the fix for the critical tenant-isolation vulnerability in
/api/strategies/{strategy_id}/clone endpoint.

Author: Principal Software Architect
Date: 2025-08-02
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["DEV_MODE"] = "true"
os.environ["ENV"] = "testing"
os.environ["REDIS_URL"] = ""

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi import HTTPException
from fastapi.testclient import TestClient


class TestStrategyCloneTenantIsolation:
    """Test tenant isolation for strategy clone endpoint."""

    @pytest.fixture
    def app(self):
        """Create FastAPI app with strategy routes."""
        from backend_app.main import app
        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return TestClient(app)

    @pytest.fixture
    def user_a_token(self):
        """Generate a valid test token for user A."""
        import jwt
        import time
        
        secret = os.getenv("SUPABASE_JWT_SECRET", "dev-secret-change-in-production")
        payload = {
            "sub": "user_a_123",
            "email": "user_a@example.com",
            "tenant_id": "tenant_a",
            "role": "authenticated",
            "aud": "authenticated",
            "iss": "algo22-test",
            "exp": int(time.time()) + 3600
        }
        return jwt.encode(payload, secret, algorithm="HS256")

    @pytest.fixture
    def user_b_token(self):
        """Generate a valid test token for user B."""
        import jwt
        import time
        
        secret = os.getenv("SUPABASE_JWT_SECRET", "dev-secret-change-in-production")
        payload = {
            "sub": "user_b_456",
            "email": "user_b@example.com",
            "tenant_id": "tenant_b",
            "role": "authenticated",
            "aud": "authenticated",
            "iss": "algo22-test",
            "exp": int(time.time()) + 3600
        }
        return jwt.encode(payload, secret, algorithm="HS256")

    @pytest.fixture
    def mock_supabase(self):
        """Mock Supabase client for testing."""
        with patch('backend_app.routers.strategies._sb') as mock_sb, \
             patch('backend_app.routers.strategies.create_request_supabase') as mock:
            mock_client = MagicMock()
            mock_client.table = MagicMock()
            mock.return_value = mock_client
            mock_sb.return_value = mock_client
            yield mock_client

    def test_clone_own_strategy_succeeds(self, client, user_a_token, mock_supabase):
        """Test that user can clone their own strategy."""
        # Mock successful strategy fetch and clone
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": "strategy_123",
                "user_id": "user_a_123",
                "name": "Test Strategy",
                "blueprint": {"nodes": [], "edges": []}
            }
        ]
        
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {
                "id": "strategy_456",
                "user_id": "user_a_123",
                "name": "Test Strategy (Copy)",
                "description": "Cloned from strategy_123"
            }
        ]
        
        response = client.post(
            "/api/strategies/strategy_123/clone",
            headers={"Authorization": f"Bearer {user_a_token}"}
        )
        
        # Should succeed for own strategy
        assert response.status_code == 200
        assert response.json()["status"] == "cloned"

    def test_clone_other_user_strategy_returns_404(self, client, user_b_token, mock_supabase):
        """Test that user cannot clone another user's strategy - returns 404."""
        # Mock empty result (strategy not found for this user)
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        
        response = client.post(
            "/api/strategies/strategy_123/clone",
            headers={"Authorization": f"Bearer {user_b_token}"}
        )
        
        # Should return 404, not the strategy data
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_clone_without_token_returns_401(self, client):
        """Test that clone without authentication fails."""
        response = client.post("/api/strategies/strategy_123/clone")
        
        # Should return 401 for missing authentication
        assert response.status_code == 401

    def test_clone_with_invalid_token_returns_401(self, client):
        """Test that clone with invalid token fails."""
        response = client.post(
            "/api/strategies/strategy_123/clone",
            headers={"Authorization": "Bearer invalid_token"}
        )
        
        # Should return 401 for invalid token
        assert response.status_code == 401


def run_all_tests():
    """Run all tenant isolation tests."""
    print("=" * 60)
    print("STRATEGY CLONE TENANT ISOLATION TESTS")
    print("=" * 60)
    
    import pytest
    result = pytest.main([__file__, "-v", "--tb=short"])
    
    print("\n" + "=" * 60)
    if result == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"TESTS FAILED (exit code: {result})")
    print("=" * 60)
    
    return result


if __name__ == "__main__":
    sys.exit(run_all_tests())
