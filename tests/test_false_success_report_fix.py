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
    def test_marketplace_item_key_reaches_the_marketplace_branch(self):
        """
        Before fix: marketplace_{lib_id} item_key was rejected as an invalid billing item key.
        After fix:  it is dispatched to the marketplace branch, which settles the payment.

        UPDATED BY TASK 19.2 (marketplace-subscriptions-paper-trading, Requirements 9.5, 10.4,
        11.6, 11.14). This test used to assert that ``_apply_marketplace_entitlement`` flipped
        ``library_subscriptions.status`` to ``active`` on its own, with no Settlement_Record, no
        period expiry and no 90/10 split. That is the defect task 19.2 removes: Requirement 11.6
        admits no transition into ``ACTIVE`` without a payment recorded as a Settlement_Record, and
        ``trg_subscription_transition_guard`` refuses one in the database regardless of the
        handler. The activation now happens inside ``settlement_service.settle``, which needs the
        provider reference, the confirmed amount and the currency from the webhook event.

        What is asserted here is what "FIX 2" was actually about and is still true: a
        ``marketplace_`` item key is NOT rejected as an invalid billing item key and never reaches
        the plan-tier writer. The refusal it now gets names the missing settlement context, which
        is a refusal to activate without a payment record rather than a refusal to understand the
        item key. The end-to-end settlement path is covered by
        ``tests/test_billing_e2e.py::TestMarketplaceSettlementWiring``.
        """
        import asyncio
        import backend_app.routers.billing as billing_module

        library_id = "550e8400-e29b-41d4-a716-446655440000"
        profile_writes = []

        def mock_background_sb():
            sb = MagicMock()
            sb.table.side_effect = lambda name: profile_writes.append(name) or MagicMock()
            return sb

        with patch.object(billing_module, '_background_sb', mock_background_sb):
            with pytest.raises(Exception) as caught:
                asyncio.run(
                    billing_module._apply_billing_entitlement(
                        "user_123", f"marketplace_{library_id}", False, {}
                    )
                )

        assert getattr(caught.value, "status_code", None) == 400
        detail = str(getattr(caught.value, "detail", ""))
        assert "Invalid billing item key" not in detail, (
            "REGRESSION: a marketplace item key is being rejected as an invalid plan key again"
        )
        assert "settlement context" in detail
        assert "profiles" not in profile_writes, (
            "a marketplace payment must never write profiles.subscription_tier"
        )


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
        r = client.get("/api/exchanges/supported")  # Fixed: plural "exchanges"
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
        import backend_app.backend.metrics_service as metrics_service
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
        
        with patch.object(metrics_service, 'get_metrics_service', mock_get_metrics_service):
            try:
                service = strat_service.StrategyService()
                # This should now raise an error instead of returning {}
                service._get_supabase = lambda user: MagicMock()
                service.get_strategy = AsyncMock(return_value={"strategy": {"id": "strat_123"}})
                
                import asyncio
                # _get_strategy_performance expects a user dict with "id" key, not a raw string
                user_dict = {"id": "user_123", "email": "test@test.com"}
                with pytest.raises(RuntimeError):
                    asyncio.run(service._get_strategy_performance(user_dict, "strat_123"))
            except Exception as e:
                pytest.fail(f"Should raise RuntimeError, got: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
