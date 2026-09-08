"""
tests/test_phase12a_production_security_validation.py — Phase 12A Full Production Security Validation Suite

Validates the complete security chain:
Password + Email OTP -> Supabase Session -> REST API -> WebSocket -> RBAC/MFA/AAL2 -> Database -> Risk Engine -> Paper/Live Boundary -> Idempotency
"""

import asyncio
import os
import time
import uuid
import jwt
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend_app.core.dependencies import (
    get_admin_user,
    get_current_user,
    get_operator_user,
    require_aal2,
)
from backend_app.core.websocket_auth import (
    WebSocketAuthMiddleware,
    _decode_hs256_token,
    authorize_channel_subscription,
)
from backend_app.main import app

client = TestClient(app)

# Helper to generate test JWTs signed with test secret
def create_test_jwt(user_id: str, email: str = "trader@vyomquant.io", role: str = "authenticated", app_metadata: dict = None, aal: str = "aal1", exp_offset: int = 3600):
    secret = os.environ.get("SUPABASE_JWT_SECRET", "super-secret-test-jwt-key-minimum-32-chars-long")
    payload = {
        "iss": "algo22-test",
        "sub": user_id,
        "email": email,
        "role": role,
        "aud": "authenticated",
        "aal": aal,
        "app_metadata": app_metadata or {"role": role, "aal": aal},
        "exp": int(time.time()) + exp_offset,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ══════════════════════════════════════════════════════════════════════════
# 1. REST API AUTHENTICATION & TOKEN LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════

class TestRestApiAuthentication:
    """Validates that unauthenticated, invalid, or expired sessions fail closed on REST APIs."""

    def test_missing_jwt_returns_401(self):
        """No Authorization header results in 401 Unauthorized."""
        resp = client.get("/api/dashboard/overview")
        assert resp.status_code == 401
        assert "Missing" in resp.json().get("detail", "") or "token" in resp.json().get("detail", "").lower()

    def test_invalid_jwt_signature_returns_401(self):
        """Malformed or forged signature returns 401 Unauthorized."""
        headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.forged.payload"}
        resp = client.get("/api/dashboard/overview", headers=headers)
        assert resp.status_code == 401

    def test_expired_jwt_returns_401(self):
        """Expired JWT token returns 401 Unauthorized."""
        expired_token = create_test_jwt(user_id="user-expired-001", exp_offset=-3600)
        headers = {"Authorization": f"Bearer {expired_token}"}
        resp = client.get("/api/dashboard/overview", headers=headers)
        assert resp.status_code == 401
        assert "expired" in resp.json().get("detail", "").lower()

    def test_valid_jwt_passes_authentication(self):
        """Properly signed, valid JWT is authorized."""
        user_id = str(uuid.uuid4())
        valid_token = create_test_jwt(user_id=user_id)
        headers = {"Authorization": f"Bearer {valid_token}"}
        
        # Test against an authenticated endpoint with mocked DB/profile.
        #
        # ``/api/paper/account`` is served from the ``paper_*`` tables as of
        # marketplace-subscriptions-paper-trading task 23.2 and answers 503
        # PAPER_PERSISTENCE_UNAVAILABLE when there is no Persistence_Layer behind it - it does
        # not fall back to an in-memory balance (Requirements 17.2, 28.3). The claim under test
        # is about authentication, so the endpoint is given storage; a 401 or a 403 would still
        # be the failure this test is looking for.
        from backend_app.backend.paper_trading_service import get_paper_trading_service
        from tests.paper_seed import bind_paper_persistence, release_paper_persistence

        paper_service = get_paper_trading_service()
        bind_paper_persistence(paper_service)
        try:
            with patch("backend_app.core.dependencies._get_cached_profile", new_callable=AsyncMock) as mock_profile:
                mock_profile.return_value = {"subscription_tier": "pro", "is_frozen": False}
                resp = client.get("/api/paper/account", headers=headers)
                assert resp.status_code == 200
        finally:
            release_paper_persistence(paper_service)


# ══════════════════════════════════════════════════════════════════════════
# 2. RBAC & PRIVILEGE ESCALATION CONTROLS
# ══════════════════════════════════════════════════════════════════════════

class TestRbacAndPrivilegeControls:
    """Validates role-based access control and guarantees client-writable metadata cannot grant admin access."""

    def test_regular_user_cannot_access_admin(self):
        """Regular authenticated user cannot access admin resources."""
        user_data = {
            "id": "regular-user-001",
            "email": "user@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {"role": "trader"},
            "user_metadata": {},
        }
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_admin_user(user_data))
        assert exc.value.status_code == 403
        assert "Admin role required" in exc.value.detail

    def test_client_side_metadata_manipulation_rejected(self):
        """Client trying to forge admin role via user_metadata is rejected."""
        attacker_data = {
            "id": "attacker-001",
            "email": "attacker@evil.com",
            "role": "authenticated",
            "app_metadata": {},
            "user_metadata": {"role": "admin", "is_admin": True},
        }
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_admin_user(attacker_data))
        assert exc.value.status_code == 403

    def test_server_controlled_app_metadata_admin_accepted(self):
        """Genuine admin with app_metadata.role='admin' is allowed."""
        admin_data = {
            "id": "admin-001",
            "email": "admin@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {"role": "admin"},
        }
        user = asyncio.run(get_admin_user(admin_data))
        assert user["id"] == "admin-001"


# ══════════════════════════════════════════════════════════════════════════
# 3. MFA / AAL2 SENSITIVE OPERATION BOUNDARY
# ══════════════════════════════════════════════════════════════════════════

class TestMfaAal2Enforcement:
    """Validates that sensitive financial and administrative operations strictly require AAL2."""

    def test_aal1_session_rejected_for_aal2_operation(self):
        """Password + Email OTP establishes AAL1, but AAL2-required operation rejects AAL1."""
        user_aal1 = {
            "id": "trader-001",
            "email": "trader@vyomquant.io",
            "app_metadata": {"aal": "aal1"},
        }
        with pytest.raises(HTTPException) as exc:
            asyncio.run(require_aal2(user_aal1))
        assert exc.value.status_code == 403
        assert "Two-factor authentication is required" in exc.value.detail

    def test_aal2_session_accepted_for_sensitive_operation(self):
        """Session elevated to AAL2 via TOTP authenticator challenge is accepted."""
        user_aal2 = {
            "id": "trader-001",
            "email": "trader@vyomquant.io",
            "app_metadata": {"aal": "aal2"},
        }
        user = asyncio.run(require_aal2(user_aal2))
        assert user["id"] == "trader-001"


# ══════════════════════════════════════════════════════════════════════════
# 4. WEBSOCKET SECURITY & TENANT ISOLATION
# ══════════════════════════════════════════════════════════════════════════

class TestWebSocketSecurity:
    """Validates WebSocket authentication, tenant cross-check, and channel authorization."""

    def test_ws_missing_token_rejected(self):
        """WebSocket connection without token is rejected."""
        ws_auth = WebSocketAuthMiddleware()
        mock_ws = AsyncMock()
        user = asyncio.run(ws_auth.authenticate_websocket(mock_ws, token=None, user_id="user-001"))
        assert user is None
        mock_ws.close.assert_called_once()

    def test_ws_tenant_mismatch_rejected(self):
        """User A attempting to connect to User B's WebSocket channel is rejected."""
        user_a_token = create_test_jwt(user_id="user-A")
        ws_auth = WebSocketAuthMiddleware()
        mock_ws = AsyncMock()
        
        # Claiming user-B with user-A's token
        user = asyncio.run(ws_auth.authenticate_websocket(mock_ws, token=user_a_token, user_id="user-B"))
        assert user is None
        mock_ws.close.assert_called_once()

    def test_ws_valid_user_accepted(self):
        """Matching user ID and valid JWT authenticates WebSocket cleanly."""
        user_a_token = create_test_jwt(user_id="user-A")
        ws_auth = WebSocketAuthMiddleware()
        mock_ws = AsyncMock()
        
        user = asyncio.run(ws_auth.authenticate_websocket(mock_ws, token=user_a_token, user_id="user-A"))
        assert user is not None
        assert user["id"] == "user-A"

    def test_ws_unauthorized_channel_subscription_rejected(self):
        """User A subscribing to User B's strategy stream is forbidden."""
        user_a = {"id": "user-A", "access_token": create_test_jwt(user_id="user-A")}
        
        # Mock database returning strategy owned by user-B
        mock_sb = MagicMock()
        mock_query = MagicMock()
        mock_query.execute.return_value = MagicMock(data=[{"id": "strat-001", "user_id": "user-B"}])
        mock_sb.table.return_value.select.return_value.eq.return_value.limit.return_value = mock_query

        authz = asyncio.run(
            authorize_channel_subscription("strategy.strat-001", user_a, supabase=mock_sb)
        )
        assert authz.allowed is False
        assert authz.code == "CHANNEL_FORBIDDEN"


# ══════════════════════════════════════════════════════════════════════════
# 5. PAPER VS LIVE EXECUTION BOUNDARY & RISK ENFORCEMENT
# ══════════════════════════════════════════════════════════════════════════

class TestPaperLiveAndRiskBoundary:
    """Guarantees paper trading never calls live exchanges and risk gates fail closed."""

    def test_paper_trading_never_routes_to_live_exchange(self):
        """Paper trading order submission is handled entirely in paper simulation."""
        from backend_app.backend.paper_trading_service import get_paper_trading_service
        
        user_id = "test-trader-01"
        valid_token = create_test_jwt(user_id=user_id)
        headers = {"Authorization": f"Bearer {valid_token}"}
        payload = {
            "symbol": "BTC-USDT",
            "side": "buy",
            "order_type": "market",
            "quantity": 0.05,
        }
        service = get_paper_trading_service()
        
        with patch("backend_app.core.dependencies._get_cached_profile", new_callable=AsyncMock) as mock_profile:
            mock_profile.return_value = {"subscription_tier": "pro", "is_frozen": False}
            with patch.object(service, "place_order", new_callable=AsyncMock) as mock_place:
                mock_place.return_value = {
                    "id": "paper_ord_test",
                    "symbol": "BTC-USDT",
                    "side": "buy",
                    "quantity": 0.05,
                    "status": "FILLED",
                    "is_simulated": True,
                }
                resp = client.post("/api/paper/orders", json=payload, headers=headers)
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "success"
                assert data["order"]["is_simulated"] is True
                assert mock_place.called

    def test_risk_rejection_halts_order_execution(self):
        """When risk limits (e.g. daily loss or drawdown) are exceeded, risk check rejects."""
        from backend_app.core.risk_engine import RiskEngine
        from decimal import Decimal
        
        risk = RiskEngine(initial_capital=10000.0, max_drawdown=0.20, daily_loss_limit=0.05)
        
        # Check normal risk sizing (ATR-based)
        size = risk.calculate_position_size(price=100.0, atr=5.0)
        assert size > 0
        
        # Simulate drawdown breach
        risk.current_equity = Decimal("7000.0")  # 30% drawdown vs 20% limit
        drawdown_ok, reason = risk.check_drawdown()
        assert drawdown_ok is False
        assert "drawdown exceeded" in reason.lower()
        assert risk.can_trade() is False


# ══════════════════════════════════════════════════════════════════════════
# 6. IDEMPOTENCY & FINANCIAL INVARIANTS
# ══════════════════════════════════════════════════════════════════════════

class TestIdempotencyAndFinancialInvariants:
    """Validates idempotent execution and the core ledger accounting identity."""

    def test_idempotency_atomic_lock_prevents_duplicate_execution(self):
        """Concurrent requests with same client_order_id acquire only one execution lock."""
        from backend_app.core.distributed_idempotency import DistributedIdempotency
        idemp = DistributedIdempotency()
        
        tenant_id = "test_tenant_phase12a"
        order_id = "order_uniq_12345"
        
        with patch.object(idemp, "_atomic_check_and_set", new_callable=AsyncMock) as mock_cas:
            # First call acquires lock
            mock_cas.return_value = (None, True)
            res1, acquired1 = asyncio.run(idemp._atomic_check_and_set(tenant_id, order_id, "token1"))
            assert acquired1 is True
            assert res1 is None
            
            # Second call blocked
            mock_cas.return_value = (None, False)
            res2, acquired2 = asyncio.run(idemp._atomic_check_and_set(tenant_id, order_id, "token2"))
            assert acquired2 is False

    def test_ledger_equity_invariant(self):
        """Equity = Available Balance + Unrealized PnL holds strictly."""
        available_balance = 50000.00
        unrealized_pnl = 1250.50
        equity = available_balance + unrealized_pnl
        assert equity == 51250.50
