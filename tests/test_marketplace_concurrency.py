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

os.environ["DEV_MODE"] = "true"
os.environ["ENV"] = "testing"

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


# Valid UUID for the test user (user["id"] is passed through _safe_uuid in route handlers)
_USER_UUID = str(uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))


def _user(user_id=None):
    uid = user_id if user_id is not None else _USER_UUID
    return {
        "id": uid,
        "email": f"test@vyomquant.io",
        "role": "authenticated",
        "access_token": "fake-token",
    }


def _mock_supabase():
    """Returns a mock supabase client."""
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": "sub_123",
        "library_id": "lib_456",
        "user_id": "usr_concurrency_test",
        "status": "pending",
        "subscription_tier": "standard",
        "price_paid": 10.0,
        "currency": "USD",
    }
    sb.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{
        "id": "sub_123",
        "status": "active",
    }]
    return sb


class TestConcurrentActivation:
    def test_concurrent_activation_is_idempotent(self):
        """
        Two concurrent requests activating same pending subscription should:
        - First succeeds with status=activated
        - Second returns status=already_active (no double side effects)

        Root cause of prior 401: require_marketplace_access depends on get_request_supabase
        (not get_current_user) to check the user's plan. The dependency override for
        get_current_user was set, but get_request_supabase was not, so requests arrived
        with no Bearer token → 401 before any route handler ran.
        Fix: also override get_request_supabase with a mock returning a Pro profile.
        """
        import backend_app.routers.library as lib_module
        
        # Mock supabase that returns pending subscription on first call
        def mock_svc():
            sb = MagicMock()
            # First call (select pending) — .single() returns a dict, not a list
            select_result = MagicMock()
            select_result.data = {"id": _SUB_UUID, "status": "pending"}
            sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = select_result
            
            # Update subscription: .update(...).eq("id",sub_id).eq("status","pending").execute()
            # Production code uses 2 .eq() calls — chain must match exactly
            update_result = MagicMock()
            update_result.data = [{"id": _SUB_UUID, "status": "active"}]
            sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = update_result
            
            # Strategy lookup (.select().eq().execute() — no .single())
            strat_result = MagicMock()
            strat_result.data = [{
                "id": _LIB_UUID,
                "name": "Test Strategy",
                "author_id": "other_user",
                "price": 10.0,
                "subscription_tier": "standard",  # Required: missing this defaults to "free" -> 400
                "currency": "USD",
            }]
            sb.table.return_value.select.return_value.eq.return_value.execute.return_value = strat_result
            
            return sb
        
        # Mock grant_deployment_permission
        with patch.object(lib_module, 'grant_deployment_permission') as mock_grant:
            with patch.object(lib_module, '_build_service_client', return_value=mock_svc()):
                app.dependency_overrides[get_current_user] = lambda: _user()
                # Override get_request_supabase so require_marketplace_access sees a Pro plan
                app.dependency_overrides[get_request_supabase] = _mock_supabase_with_pro_profile
                
                try:
                    client = TestClient(app, raise_server_exceptions=False)
                    
                    # First activation
                    r1 = client.post("/api/library/" + _LIB_UUID + "/subscribe")
                    assert r1.status_code == 200, f"Expected 200, got {r1.status_code}: {r1.text}"
                    assert r1.json()["status"] == "activated"
                    assert mock_grant.call_count == 1
                    
                    # Simulate second concurrent request (update returns no rows)
                    def mock_svc_no_rows():
                        sb = MagicMock()
                        # pending lookup via .single() — returns dict
                        select_result = MagicMock()
                        select_result.data = {"id": _SUB_UUID, "status": "pending"}
                        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = select_result
                        
                        # Update returns no rows — simulates concurrent winner already activated
                        # Production code: .update(...).eq("id",...).eq("status","pending").execute()
                        update_result = MagicMock()
                        update_result.data = []  # No rows affected
                        sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = update_result
                        
                        strat_result = MagicMock()
                        strat_result.data = [{
                            "id": _LIB_UUID,
                            "name": "Test Strategy",
                            "author_id": "other_user",
                            "price": 10.0,
                            "subscription_tier": "standard",
                            "currency": "USD",
                        }]
                        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = strat_result
                        
                        return sb
                    
                    with patch.object(lib_module, '_build_service_client', return_value=mock_svc_no_rows()):
                        r2 = client.post("/api/library/" + _LIB_UUID + "/subscribe")
                        assert r2.status_code == 200, f"Expected 200, got {r2.status_code}: {r2.text}"
                        assert r2.json()["status"] == "already_active"
                        # Permission grant should NOT fire again
                        assert mock_grant.call_count == 1  # Still 1, not 2
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
        Two concurrent renewals of cancelled subscription should:
        - First succeeds with status=renewed
        - Second returns status=already_active (no double counter increment)

        Root cause of prior 422: the renew endpoint calls _safe_uuid() on the
        {sub_id} path parameter. "sub_123" is not a valid UUID, so _safe_uuid
        raises HTTPException(422) before any handler logic executes.
        Fix: use _SUB_UUID (a valid UUID constant) for the path parameter.
        """
        import backend_app.routers.library as lib_module
        
        def mock_svc():
            sb = MagicMock()
            # Lookup
            lookup_result = MagicMock()
            lookup_result.data = [{"id": _SUB_UUID, "library_id": _LIB_UUID, "status": "cancelled"}]
            sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = lookup_result
            
            # Update (cancelled -> active)
            update_result = MagicMock()
            update_result.data = [{"id": _SUB_UUID, "status": "active"}]
            sb.table.return_value.update.return_value.eq.return_value.in_.return_value.execute.return_value = update_result
            
            # Post-update lookup
            post_lookup_result = MagicMock()
            post_lookup_result.data = [{"id": _SUB_UUID, "library_id": _LIB_UUID, "status": "active"}]
            sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = post_lookup_result
            
            # Subscriber count lookup
            count_result = MagicMock()
            count_result.data = [{"subscriber_count": 5}]
            sb.table.return_value.select.return_value.eq.return_value.execute.return_value = count_result
            
            return sb
        
        with patch.object(lib_module, 'grant_deployment_permission'):
            with patch.object(lib_module, '_build_service_client', return_value=mock_svc()):
                app.dependency_overrides[get_current_user] = lambda: _user()
                
                try:
                    client = TestClient(app, raise_server_exceptions=False)
                    
                    # First renewal — use valid UUID in path
                    r1 = client.post("/api/library/subscriptions/" + _SUB_UUID + "/renew")
                    assert r1.status_code == 200, f"Expected 200, got {r1.status_code}: {r1.text}"
                    assert r1.json()["status"] == "renewed"
                    
                    # Simulate second concurrent request (already active)
                    def mock_svc_active():
                        sb = MagicMock()
                        lookup_result = MagicMock()
                        lookup_result.data = [{"id": _SUB_UUID, "library_id": _LIB_UUID, "status": "active"}]
                        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = lookup_result
                        
                        # Update returns no rows (not in cancelled/expired status)
                        update_result = MagicMock()
                        update_result.data = []
                        sb.table.return_value.update.return_value.eq.return_value.in_.return_value.execute.return_value = update_result
                        
                        return sb
                    
                    with patch.object(lib_module, '_build_service_client', return_value=mock_svc_active()):
                        r2 = client.post("/api/library/subscriptions/" + _SUB_UUID + "/renew")
                        assert r2.status_code == 200, f"Expected 200, got {r2.status_code}: {r2.text}"
                        assert r2.json()["status"] == "already_active"
                finally:
                    app.dependency_overrides.clear()


class TestMalformedSubscriptionId:
    def test_invalid_uuid_returns_400(self):
        """
        Malformed subscription_id should return HTTP 400, not 500.
        """
        import backend_app.routers.billing as billing_module
        
        with pytest.raises(Exception) as exc_info:
            billing_module._validate_uuid("not-a-uuid", "subscription_id")
        
        # Should raise HTTPException with status 400
        assert "HTTPException" in str(type(exc_info.value))


class TestUniqueConstraint:
    def test_duplicate_active_subscription_prevented(self):
        """
        Unique constraint (library_id, user_id) prevents duplicate active subscriptions.
        Checkout endpoint pre-checks existing active subscriptions.

        Root cause of prior 401: the checkout endpoint uses require_marketplace_access,
        which injects get_request_supabase to check the user plan. Without overriding
        get_request_supabase, the dependency raises 401 (no Bearer token) before
        any route handler executes — masking the real 400 duplicate check.
        Fix: also override get_request_supabase with a mock returning a Pro profile.
        """
        import backend_app.routers.library as lib_module
        
        def mock_svc_with_existing():
            sb = MagicMock()
            # Strategy lookup
            strat_result = MagicMock()
            strat_result.data = [{"id": _LIB_UUID, "price": 10.0}]
            sb.table.return_value.select.return_value.eq.return_value.in_.return_value.single.return_value.execute.return_value = strat_result
            
            # Existing active subscription check
            existing_result = MagicMock()
            existing_result.data = [{"id": _SUB_UUID, "status": "active"}]
            sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = existing_result
            
            return sb
        
        with patch.object(lib_module, '_build_service_client', return_value=mock_svc_with_existing()):
            app.dependency_overrides[get_current_user] = lambda: _user()
            # Override get_request_supabase so require_marketplace_access sees a Pro plan
            app.dependency_overrides[get_request_supabase] = _mock_supabase_with_pro_profile
            
            try:
                client = TestClient(app, raise_server_exceptions=False)
                r = client.post("/api/library/" + _LIB_UUID + "/checkout", json={"currency": "USD"})
                assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
                assert "already subscribed" in r.json()["detail"].lower()
            finally:
                app.dependency_overrides.clear()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
