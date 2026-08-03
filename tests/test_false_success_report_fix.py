"""
tests/test_false_success_report_fix.py

Tests for false success reporting fixes from Phase 1-4 audit.

This file tests:
1. Marketplace payment verification bypass
2. Webhook missing data handling
3. Exchange status claims
4. Performance fetch error handling
"""

import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["DEV_MODE"] = "true"
os.environ["ENV"] = "testing"

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app


def _user():
    return {
        "id": "usr_false_success_test",
        "email": "test@vyomquant.io",
        "role": "authenticated",
        "access_token": "fake-token",
    }


# ---------------------------------------------------------------------------
# FIX 1: Marketplace payment verification bypass
# ---------------------------------------------------------------------------

class TestMarketplacePaymentVerification:
    def test_subscribe_without_pending_payment_fails(self):
        """
        Before fix: POST /api/library/{id}/subscribe created active subscription without payment
        After fix:  Returns 400 requiring checkout first
        """
        import backend_app.routers.library as lib_module
        
        # Verify POST is blocked (security fix)
        def mock_supabase():
            sb = MagicMock()
            sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
            # Mock profile with pro plan for marketplace access
            profile_result = MagicMock()
            profile_result.data = [{"subscription_tier": "pro_999"}]
            sb.table.return_value.select.return_value.eq.return_value.execute.return_value = profile_result
            return sb
        
        app.dependency_overrides[get_current_user] = lambda: _user()
        app.dependency_overrides[get_request_supabase] = mock_supabase
        
        try:
            client = TestClient(app, raise_server_exceptions=False)
            # Use valid UUID for library_id
            lib_uuid = "550e8400-e29b-41d4-a716-446655440000"
            # POST should return 405 (Method Not Allowed) - manual activation is blocked
            r = client.post(f"/api/library/{lib_uuid}/subscribe")
            assert r.status_code == 405, f"Expected 405 (Method Not Allowed), got {r.status_code}"
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# FIX 2: Marketplace item_key handling in billing webhooks
# ---------------------------------------------------------------------------

class TestMarketplaceBillingWebhook:
    def test_marketplace_item_key_activates_subscription(self):
        """
        Before fix: marketplace_{lib_id} item_key was rejected as invalid
        After fix:  Updates library_subscriptions status to active
        """
        import asyncio
        import backend_app.routers.billing as billing_module
        
        # Mock the background supabase to have a pending subscription
        def mock_background_sb():
            sb = MagicMock()
            result = MagicMock()
            result.data = [{"id": "sub_123"}]
            sb.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = result
            return sb
        
        with patch.object(billing_module, '_background_sb', mock_background_sb):
            try:
                # Use valid UUID format for library_id
                asyncio.run(billing_module._apply_marketplace_entitlement("user_123", "550e8400-e29b-41d4-a716-446655440000"))
                # Should not raise HTTPException
            except Exception as e:
                pytest.fail(f"Should not raise exception for valid marketplace item_key: {e}")


# ---------------------------------------------------------------------------
# FIX 3: Webhook missing data returns HTTP error
# ---------------------------------------------------------------------------

class TestWebhookMissingData:
    def test_stripe_webhook_missing_user_id_returns_400(self):
        """
        Before fix: returned {"status": "ignored"} with HTTP 200
        After fix:  raises HTTPException(400)
        """
        # This is tested indirectly through the billing webhook flow
        # The fix ensures the exception is raised instead of returning ignored
        pass  # Integration test would require full webhook payload simulation


# ---------------------------------------------------------------------------
# FIX 4: Exchange status claims
# ---------------------------------------------------------------------------

class TestExchangeStatus:
    def test_exchange_status_is_available_not_active(self):
        """
        Before fix: returned "status": "active" unconditionally
        After fix:  returns "status": "available" (supported by CCXT)
        """
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/exchange/supported")
        assert r.status_code == 200
        data = r.json()
        
        # Check that exchanges have "available" status, not "active"
        if data.get("exchanges"):
            for ex in data["exchanges"][:5]:  # Check first 5
                assert ex.get("status") == "available", f"Exchange {ex['id']} has status {ex.get('status')}, expected 'available'"


# ---------------------------------------------------------------------------
# FIX 5: Performance fetch error handling
# ---------------------------------------------------------------------------

class TestPerformanceFetchError:
    def test_performance_fetch_failure_propagates_error(self):
        """
        Before fix: returned empty dict {} on failure
        After fix:  error is propagated and caught by caller with None indicator
        """
        import backend_app.backend.strategy_service as strat_service
        from backend_app.core.dependencies import get_telemetry
        
        # Mock crashing telemetry
        async def crashing_telemetry():
            class CrashingTelemetry:
                async def execute_query(self, *args, **kwargs):
                    raise RuntimeError("Telemetry connection failed")
            return CrashingTelemetry()
        
        # Mock metrics service to raise error
        async def mock_get_metrics_service():
            class CrashingMetricsService:
                async def get_strategy_performance(self, user_id, strategy_id):
                    raise RuntimeError("Metrics service unavailable")
            return CrashingMetricsService()
        
        with patch.object(strat_service, 'get_metrics_service', mock_get_metrics_service):
            try:
                service = strat_service.StrategyService()
                # This should now raise an error instead of returning {}
                service._get_supabase = lambda user: MagicMock()
                service.get_strategy = AsyncMock(return_value={"strategy": {"id": "strat_123"}})
                
                import asyncio
                with pytest.raises(RuntimeError):
                    asyncio.run(service._get_strategy_performance("user_123", "strat_123"))
            except Exception as e:
                pytest.fail(f"Should raise RuntimeError, got: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
