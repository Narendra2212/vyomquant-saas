"""
tests/test_tenant_isolation_strategy_clone.py â€” Tenant Isolation Test for Strategy Clone

Tests that users cannot clone strategies belonging to other tenants.
This validates the fix for the critical tenant-isolation vulnerability in
/api/strategies/{strategy_id}/clone endpoint.

Also tests that clone preserves full DAG structure (nodes, edges, buy_logic, etc.).

Author: Principal Software Architect
Date: 2025-08-02
"""

import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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
        with patch('backend_app.routers.strategies._sb', new_callable=AsyncMock) as mock_sb:
            mock_client = MagicMock()
            # Make all .execute() calls return AsyncMock to support `await query.execute()`
            mock_execute = AsyncMock()
            mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = mock_execute
            mock_client.table.return_value.insert.return_value.execute = AsyncMock()
            mock_client.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute = AsyncMock()
            mock_sb.return_value = mock_client
            yield mock_client

    def test_clone_own_strategy_succeeds(self, client, user_a_token, mock_supabase):
        """Test that user can clone their own strategy."""
        # Mock successful strategy fetch and clone
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[
                {
                    "id": "strategy_123",
                    "user_id": "user_a_123",
                    "name": "Test Strategy",
                    "blueprint": {"nodes": [], "edges": []}
                }
            ])
        )

        mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[
                {
                    "id": "strategy_456",
                    "user_id": "user_a_123",
                    "name": "Test Strategy (Copy)",
                    "description": "Cloned from strategy_123"
                }
            ])
        )

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
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[])
        )

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

    def test_clone_preserves_dag_content(self, client, user_a_token, mock_supabase):
        """Test that clone preserves full DAG structure (nodes, edges, buy_logic).

        The stored DAG is a real canonical graph. It used to be a hand-written blob whose
        node types ("condition") and edge keys ("from"/"to") matched no vocabulary the
        platform has ever had; the clone endpoint accepted it only because the router's
        own compiler swallowed every failure (SB-02). Since strategy-builder task 2.4 the
        clone path compiles through the single compiler and reports an unexecutable graph
        as 422, so preserving DAG content has to be asserted with a graph that actually
        is one.
        """
        from backend_app.backend.strategy_dag import registry as registry_module
        from backend_app.backend.strategy_dag.schema import (
            EdgeSpec,
            NodeSpec,
            StrategyGraph,
        )

        reg = registry_module.get_registry()

        def node(block_id, **params):
            return NodeSpec.create(block_id, reg[block_id].category, params=params)

        data = node(
            "ohlcv_feed",
            symbol="BTC/USDT",
            timeframe="1h",
            market_type="spot",
            mode="streaming",
        )
        rsi = node("rsi", window=14, source="close")
        threshold = node("constant", value=30.0)
        gt = node("gt")
        buy = node(
            "action_buy_market", quantity_type="percent_of_equity", quantity=0.25
        )
        graph = StrategyGraph(
            nodes=[data, rsi, threshold, gt, buy],
            edges=[
                EdgeSpec.create(data.id, "close", rsi.id, "series"),
                EdgeSpec.create(rsi.id, "value", gt.id, "left"),
                EdgeSpec.create(threshold.id, "value", gt.id, "right"),
                EdgeSpec.create(gt.id, "out", buy.id, "signal"),
            ],
        ).to_dict()

        original_dag = {
            "_nodes": graph["nodes"],
            "_edges": graph["edges"],
            "_dag_schema_version": graph["schema_version"],
            "_dag_hash": None,
        }

        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[
                {
                    "id": "strategy_123",
                    "user_id": "user_a_123",
                    "name": "Test Strategy",
                    "symbol": "BTCUSDT",
                    "timeframe": "1h",
                    "exchange_id": "binance",
                    "buy_logic": original_dag,
                    "sell_logic": {"_nodes": [], "_edges": []},
                    "risk": {"risk_per_trade": 0.02},
                    "indicators": ["RSI", "MACD"],
                    "ml_model_path": None
                }
            ])
        )

        # Mock the insert to return the cloned strategy with preserved DAG
        mock_supabase.table.return_value.insert.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[{
                "id": "strategy_456",
                "user_id": "user_a_123",
                "name": "Test Strategy (Copy)",
                "description": "Cloned from strategy_123",
                "buy_logic": original_dag,  # Should be preserved
                "sell_logic": {"_nodes": [], "_edges": []},
                "risk": {"risk_per_trade": 0.02},
                "indicators": ["RSI", "MACD"],
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "exchange_id": "binance"
            }])
        )

        response = client.post(
            "/api/strategies/strategy_123/clone",
            headers={"Authorization": f"Bearer {user_a_token}"}
        )

        # Should succeed
        assert response.status_code == 200
        cloned_strategy = response.json()["strategy"]

        # Verify DAG content is preserved in the response
        assert cloned_strategy["buy_logic"] == original_dag
        assert cloned_strategy["symbol"] == "BTCUSDT"
        assert cloned_strategy["timeframe"] == "1h"
        assert cloned_strategy["exchange_id"] == "binance"


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
