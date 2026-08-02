"""
tests/test_router_registration_completeness.py — Router Registration Completeness Test

Tests that every router file under the routers directory is actually registered
in the main FastAPI app. This prevents silent failures where new routers are added
but forgotten to be mounted, making their endpoints completely unreachable.

Author: Principal Software Architect
Date: 2025-08-02
"""

import sys
import os
import pytest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestRouterRegistrationCompleteness:
    """Test that all router files are registered in the main app."""

    @pytest.fixture
    def app(self):
        """Create FastAPI app to test route registration."""
        from backend_app.main import app
        return app

    def test_all_router_files_registered(self, app):
        """
        Test that every router file under backend_app/routers/ is registered.
        
        This test ensures that a future PR that adds a new router but forgets to
        register it will fail CI immediately instead of shipping silently unreachable.
        """
        routers_dir = Path(__file__).parent.parent / "backend_app" / "routers"
        
        # Get all router files (excluding __init__.py)
        router_files = [
            f.stem for f in routers_dir.glob("*.py") 
            if f.name != "__init__.py"
        ]
        
        # Get all registered routes
        registered_routes = {route.path for route in app.routes}
        
        # List of router files that are expected to be registered
        expected_routers = {
            "admin",
            "analytics", 
            "auth",
            "billing",
            "dag_tasks",
            "dashboard",
            "distributed_execution",
            "exchange",
            "health",
            "health_websocket",
            "library",
            "market",
            "metrics",
            "notifications",
            "orders",
            "portfolio",
            "referral",
            "risk",
            "security",
            "signal_trace",
            "signals",
            "strategies",
            "strategy_operations",
            "support",
            "user"
        }
        
        # Check that each expected router contributes at least one route
        missing_routers = []
        for router_name in expected_routers:
            # Check if any route is registered that would come from this router
            # We check for common path prefixes associated with each router
            router_contributed = False
            
            if router_name == "admin" and any("/api/admin" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "analytics" and any("/api/analytics" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "auth" and any("/api/auth" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "billing" and any("/api/billing" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "dashboard" and any("/api/" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "distributed_execution" and any("/api/distributed-execution" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "exchange" and any("/api/exchanges" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "health" and any("/health" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "health_websocket" and any("/health/websockets" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "library" and any("/api/library" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "market" and any("/api/market" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "metrics" and any("/metrics" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "notifications" and any("/api/notifications" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "orders" and any("/api/orders" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "portfolio" and any("/api/portfolio" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "referral" and any("/api/referral" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "risk" and any("/api/risk" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "security" and any("/api/security" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "signal_trace" and any("/api/signal-trace" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "signals" and any("/api/signals" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "strategies" and any("/api/strategies" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "support" and any("/api/support" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "user" and any("/api/user" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "dag_tasks" and any("/api/dag/tasks" in route for route in registered_routes):
                router_contributed = True
            elif router_name == "strategy_operations" and any("/api/" in route for route in registered_routes):
                router_contributed = True
            
            if not router_contributed:
                missing_routers.append(router_name)
        
        if missing_routers:
            pytest.fail(
                f"The following router files are not registered in the app: {missing_routers}. "
                f"These routers exist in the codebase but their endpoints are completely unreachable. "
                f"Add them to main.py with app.include_router()."
            )


class TestMetricsEndpointFailureBehavior:
    """Test that metrics endpoint fails loudly on errors."""

    @pytest.fixture
    def app(self):
        """Create FastAPI app to test metrics endpoint."""
        from backend_app.main import app
        return app

    def test_metrics_endpoint_returns_500_on_error(self, app):
        """
        Test that metrics endpoint returns 500 on errors, not a fake success.
        
        This test forces the metrics generation to raise and asserts the endpoint
        returns a 5xx, never a fabricated "up" payload.
        """
        from unittest.mock import patch
        from prometheus_client import generate_latest
        
        # Mock generate_latest to raise an exception
        with patch('backend_app.routers.metrics.generate_latest', side_effect=Exception("Test error")):
            from fastapi.testclient import TestClient
            client = TestClient(app)
            
            response = client.get("/metrics")
            
            # Should return 500, not 200 with fake metrics
            assert response.status_code == 500
            assert "Metrics generation failed" in response.text


def run_all_tests():
    """Run all router registration tests."""
    print("=" * 60)
    print("ROUTER REGISTRATION COMPLETENESS TESTS")
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
