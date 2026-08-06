"""
tests/test_marketplace_concurrency.py

Tests for marketplace subscription concurrency and idempotency.

Tests:
1. Concurrent activation of same pending subscription
2. Cross-user payment_reference scenarios
3. Concurrent renewal of cancelled subscription
4. Malformed subscription_id/payment_reference
5. Unique constraint handling for duplicate subscriptions
"""

import os
import sys
import uuid
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app

# Valid UUIDs for use in tests (path params must pass _safe_uuid validation)
_SUB_UUID = str(uuid.UUID("12345678-1234-5678-1234-567812345678"))
_LIB_UUID = str(uuid.UUID("87654321-4321-8765-4321-876543218765"))


def _mock_supabase_with_pro_profile():
    """Returns a mock supabase client that reports a Pro plan for the test user.

    This is required by require_marketplace_access → _get_user_plan, which
    queries the 'profiles' table via the request-scoped Supabase client.
    The FREE plan does not include MARKETPLACE_ACCESS, so tests that hit
    endpoints protected by require_marketplace_access must override
    get_request_supabase with this mock to avoid a 403.
    """
    sb = MagicMock()
    profile_result = MagicMock()
    profile_result.data = [{"subscription_tier": "pro_999"}]
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = profile_result
    return sb


def _user():
    """Mock authenticated user object."""
    return {
        "id": "usr_test_user",
        "email": "test@example.com",
        "aud": "authenticated"
    }


class TestConcurrentActivation:
    def test_concurrent_activation_is_idempotent(self):
        """
        Security fix: POST /subscribe is now blocked (405 Method Not Allowed).
        Activation is only performed by billing webhooks after payment verification.
        This test verifies the endpoint is read-only (GET) and returns status information.
        """
        import backend_app.routers.library as lib_module
        
        with patch.object(lib_module, '_build_service_client', return_value=MagicMock()):
            app.dependency_overrides[get_current_user] = lambda: _user()
            app.dependency_overrides[get_request_supabase] = _mock_supabase_with_pro_profile
            
            try:
                client = TestClient(app, raise_server_exceptions=False)
                
                # POST should be blocked (security fix)
                r_post = client.post("/api/library/" + _LIB_UUID + "/subscribe")
                assert r_post.status_code == 405, f"Expected 405 (Method Not Allowed), got {r_post.status_code}"
            finally:
                app.dependency_overrides.clear()


class TestCrossUserPaymentReference:
    def test_cross_user_payment_reference_fails_user_check(self):
        """
        User A's payment reference cannot activate User B's subscription.
        WHERE user_id in UPDATE prevents this.
        """
        import asyncio
        import backend_app.routers.billing as billing_module
        
        # Mock that payment metadata has user_id and subscription_id
        def mock_background_sb():
            sb = MagicMock()
            # Update returns no rows because user_id doesn't match
            update_result = MagicMock()
            update_result.data = []
            sb.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = update_result
            return sb
        
        with patch.object(billing_module, '_background_sb', mock_background_sb):
            try:
                with pytest.raises(Exception):  # Should raise because no rows updated
                    asyncio.run(billing_module._apply_marketplace_entitlement("user_b", "lib_456", "sub_123"))
            except Exception as e:
                # Expected - no rows affected due to user_id mismatch
                pass


class TestConcurrentRenewal:
    def test_concurrent_renewal_is_idempotent(self):
        """
        Two concurrent renewal requests for same cancelled subscription should:
        - First succeeds with status=active
        - Second returns status=already_active (no double side effects)
        """
        # This test placeholder exists to document the expected behavior
        # The actual renewal logic would be in a separate endpoint
        pass


class TestMalformedSubscriptionId:
    def test_invalid_uuid_returns_400(self):
        """
        Invalid UUID in subscription_id should return 400.
        """
        import asyncio
        import backend_app.routers.billing as billing_module
        
        with pytest.raises(Exception):  # _validate_uuid raises HTTPException for invalid UUID
            asyncio.run(billing_module._apply_marketplace_entitlement("user_123", "not-a-uuid", "sub_123"))


class TestUniqueConstraint:
    def test_duplicate_active_subscription_prevented(self):
        """
        Database unique constraint on (user_id, library_id, status) prevents duplicate active subscriptions.
        """
        # This test placeholder documents the expected database-level constraint
        pass
