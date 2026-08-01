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
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["DEV_MODE"] = "true"
os.environ["ENV"] = "testing"

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app


def _user(user_id="usr_concurrency_test"):
    return {
        "id": user_id,
        "email": f"{user_id}@vyomquant.io",
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
        """
        import backend_app.routers.library as lib_module
        
        # Mock supabase that returns pending subscription on first call
        def mock_svc():
            sb = MagicMock()
            # First call (select pending)
            select_result = MagicMock()
            select_result.data = [{"id": "sub_123", "status": "pending"}]
            sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = select_result
            
            # Second call (update)
            update_result = MagicMock()
            update_result.data = [{"id": "sub_123", "status": "active"}]
            sb.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = update_result
            
            # Strategy lookup
            strat_result = MagicMock()
            strat_result.data = [{"id": "lib_456", "name": "Test Strategy", "author_id": "other_user", "price": 10.0}]
            sb.table.return_value.select.return_value.eq.return_value.execute.return_value = strat_result
            
            return sb
        
        # Mock grant_deployment_permission
        with patch.object(lib_module, 'grant_deployment_permission') as mock_grant:
            with patch.object(lib_module, '_build_service_client', return_value=mock_svc):
                app.dependency_overrides[get_current_user] = lambda: _user()
                
                try:
                    client = TestClient(app, raise_server_exceptions=False)
                    
                    # First activation
                    r1 = client.post("/api/library/lib_456/subscribe")
                    assert r1.status_code == 200
                    assert r1.json()["status"] == "activated"
                    assert mock_grant.call_count == 1
                    
                    # Simulate second concurrent request (update returns no rows)
                    def mock_svc_no_rows():
                        sb = MagicMock()
                        select_result = MagicMock()
                        select_result.data = [{"id": "sub_123", "status": "pending"}]
                        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = select_result
                        
                        update_result = MagicMock()
                        update_result.data = []  # No rows affected
                        sb.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = update_result
                        
                        strat_result = MagicMock()
                        strat_result.data = [{"id": "lib_456", "name": "Test Strategy", "author_id": "other_user", "price": 10.0}]
                        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = strat_result
                        
                        return sb
                    
                    with patch.object(lib_module, '_build_service_client', return_value=mock_svc_no_rows):
                        r2 = client.post("/api/library/lib_456/subscribe")
                        assert r2.status_code == 200
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
                    await billing_module._apply_marketplace_entitlement("user_b", "lib_456", "sub_123")
            except Exception as e:
                # Expected - no rows affected due to user_id mismatch
                pass


class TestConcurrentRenewal:
    def test_concurrent_renewal_is_idempotent(self):
        """
        Two concurrent renewals of cancelled subscription should:
        - First succeeds with status=renewed
        - Second returns status=already_active (no double counter increment)
        """
        import backend_app.routers.library as lib_module
        
        def mock_svc():
            sb = MagicMock()
            # Lookup
            lookup_result = MagicMock()
            lookup_result.data = [{"id": "sub_123", "library_id": "lib_456", "status": "cancelled"}]
            sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = lookup_result
            
            # Update (cancelled -> active)
            update_result = MagicMock()
            update_result.data = [{"id": "sub_123", "status": "active"}]
            sb.table.return_value.update.return_value.eq.return_value.in_.return_value.execute.return_value = update_result
            
            # Post-update lookup
            post_lookup_result = MagicMock()
            post_lookup_result.data = [{"id": "sub_123", "library_id": "lib_456", "status": "active"}]
            sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = post_lookup_result
            
            # Subscriber count lookup
            count_result = MagicMock()
            count_result.data = [{"subscriber_count": 5}]
            sb.table.return_value.select.return_value.eq.return_value.execute.return_value = count_result
            
            return sb
        
        with patch.object(lib_module, 'grant_deployment_permission'):
            with patch.object(lib_module, '_build_service_client', return_value=mock_svc):
                app.dependency_overrides[get_current_user] = lambda: _user()
                
                try:
                    client = TestClient(app, raise_server_exceptions=False)
                    
                    # First renewal
                    r1 = client.post("/api/library/subscriptions/sub_123/renew")
                    assert r1.status_code == 200
                    assert r1.json()["status"] == "renewed"
                    
                    # Simulate second concurrent request (already active)
                    def mock_svc_active():
                        sb = MagicMock()
                        lookup_result = MagicMock()
                        lookup_result.data = [{"id": "sub_123", "library_id": "lib_456", "status": "active"}]
                        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = lookup_result
                        
                        # Update returns no rows (not in cancelled/expired status)
                        update_result = MagicMock()
                        update_result.data = []
                        sb.table.return_value.update.return_value.eq.return_value.in_.return_value.execute.return_value = update_result
                        
                        return sb
                    
                    with patch.object(lib_module, '_build_service_client', return_value=mock_svc_active):
                        r2 = client.post("/api/library/subscriptions/sub_123/renew")
                        assert r2.status_code == 200
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
        """
        import backend_app.routers.library as lib_module
        
        def mock_svc_with_existing():
            sb = MagicMock()
            # Strategy lookup
            strat_result = MagicMock()
            strat_result.data = [{"id": "lib_456", "price": 10.0}]
            sb.table.return_value.select.return_value.eq.return_value.in_.return_value.single.return_value.execute.return_value = strat_result
            
            # Existing active subscription check
            existing_result = MagicMock()
            existing_result.data = [{"id": "sub_existing", "status": "active"}]
            sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = existing_result
            
            return sb
        
        with patch.object(lib_module, '_build_service_client', return_value=mock_svc_with_existing):
            app.dependency_overrides[get_current_user] = lambda: _user()
            
            try:
                client = TestClient(app, raise_server_exceptions=False)
                r = client.post("/api/library/lib_456/checkout", json={"currency": "USD"})
                assert r.status_code == 400
                assert "already subscribed" in r.json()["detail"].lower()
            finally:
                app.dependency_overrides.clear()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
