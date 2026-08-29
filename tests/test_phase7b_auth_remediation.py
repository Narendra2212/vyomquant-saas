"""
tests/test_phase7b_auth_remediation.py — Comprehensive Phase 7B Authentication Remediation Test Suite

Covers:
1. P0 (F-02): Privilege Escalation Defense — user_metadata.role rejected, app_metadata.role required.
2. P0 (F-01): Email Verification Contract — no admin auto-confirm bypass, structured response.
3. P1 (F-06): MFA / AAL2 Enforcement — require_aal2 rejects AAL1, accepts AAL2.
4. P1 (F-05): WebSocket Ticket Issuance — /api/auth/ws-ticket provides short-lived token.
5. P1 (F-03/F-04): Secret Leakage Prevention — Token redaction and dev-only logging verification.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend_app.core.dependencies import (
    get_admin_user,
    get_current_user,
    get_operator_user,
    require_aal2,
    get_request_supabase,
    get_supabase,
)
from backend_app.main import app

client = TestClient(app)


# ══════════════════════════════════════════════════════════════════════════
# 1. P0 (F-02): PRIVILEGE ESCALATION REGRESSION SUITE
# ══════════════════════════════════════════════════════════════════════════

class TestF02PrivilegeEscalationRemediation:
    """Verifies that user_metadata cannot grant administrative or operator privileges."""

    def test_user_metadata_role_admin_rejected(self):
        """Attacker sets user_metadata.role = 'admin' via Supabase SDK."""
        attacker = {
            "id": "attacker-001",
            "email": "attacker@evil.com",
            "role": "authenticated",
            "app_metadata": {},
            "user_metadata": {"role": "admin"},
        }
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_admin_user(attacker))
        assert exc.value.status_code == 403
        assert "Admin role required" in exc.value.detail

    def test_user_metadata_role_operator_rejected(self):
        """Attacker sets user_metadata.role = 'operator' via Supabase SDK."""
        attacker = {
            "id": "attacker-002",
            "email": "attacker2@evil.com",
            "role": "authenticated",
            "app_metadata": {},
            "user_metadata": {"role": "operator"},
        }
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_operator_user(attacker))
        assert exc.value.status_code == 403
        assert "OPERATOR PERMISSION REQUIRED" in exc.value.detail

    def test_top_level_postgres_role_not_trusted_as_admin(self):
        """Top-level role='authenticated' must not be treated as admin."""
        normal_user = {
            "id": "user-003",
            "email": "trader@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {"role": "trader"},
            "user_metadata": {},
        }
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_admin_user(normal_user))
        assert exc.value.status_code == 403

    def test_legitimate_admin_via_app_metadata_admitted(self):
        """Legitimate admin with role set in server-controlled app_metadata."""
        admin = {
            "id": "admin-001",
            "email": "admin@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {"role": "admin"},
            "user_metadata": {},
        }
        res = asyncio.run(get_admin_user(admin))
        assert res["id"] == "admin-001"

    def test_legitimate_support_via_app_metadata_admitted(self):
        """Legitimate support with role set in server-controlled app_metadata."""
        support = {
            "id": "support-001",
            "email": "support@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {"role": "support"},
            "user_metadata": {},
        }
        res = asyncio.run(get_admin_user(support))
        assert res["id"] == "support-001"

    def test_legitimate_operator_via_app_metadata_admitted(self):
        """Legitimate operator with role set in server-controlled app_metadata."""
        operator = {
            "id": "operator-001",
            "email": "operator@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {"role": "operator"},
            "user_metadata": {},
        }
        res_admin = asyncio.run(get_admin_user(operator))
        assert res_admin["id"] == "operator-001"
        res_op = asyncio.run(get_operator_user(operator))
        assert res_op["id"] == "operator-001"

    def test_admin_cannot_access_operator_actions(self):
        """Standard admin without operator role cannot execute operator actions."""
        admin = {
            "id": "admin-002",
            "email": "admin2@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {"role": "admin"},
            "user_metadata": {},
        }
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_operator_user(admin))
        assert exc.value.status_code == 403


# ══════════════════════════════════════════════════════════════════════════
# 2. P0 (F-01): EMAIL VERIFICATION CONTRACT
# ══════════════════════════════════════════════════════════════════════════

class TestF01EmailVerificationContract:
    """Verifies that registration enforces email verification state."""

    def test_registration_unverified_returns_structured_flag(self):
        """When Supabase returns user without active session, verification_required is True."""
        mock_user = MagicMock(id="new-user-123", email="newuser@vyomquant.io")
        mock_res = MagicMock(user=mock_user, session=None)

        mock_supabase = MagicMock()
        mock_supabase.auth.sign_up.return_value = mock_res

        with patch("backend_app.routers.auth.get_supabase", return_value=mock_supabase):
            app.dependency_overrides[get_supabase] = lambda: mock_supabase
            try:
                response = client.post(
                    "/api/auth/register",
                    json={
                        "email": "newuser@vyomquant.io",
                        "password": "SecurePassword123!",
                        "username": "newtrader",
                    },
                )
                assert response.status_code == 201
                data = response.json()
                assert data.get("verification_required") is True
                # Ensure fake token string is eliminated
                assert data.get("access_token") is None or data.get("access_token") != "email_verification_pending"
                assert "check your email" in data.get("message", "").lower()
            finally:
                app.dependency_overrides.clear()

    def test_registration_verified_returns_access_token(self):
        """When Supabase returns user with active session (auto-confirm configured on project)."""
        mock_user = MagicMock(id="verified-user-123", email="verified@vyomquant.io")
        mock_session = MagicMock(access_token="valid_supabase_jwt_session")
        mock_res = MagicMock(user=mock_user, session=mock_session)

        mock_supabase = MagicMock()
        mock_supabase.auth.sign_up.return_value = mock_res

        app.dependency_overrides[get_supabase] = lambda: mock_supabase
        try:
            response = client.post(
                "/api/auth/register",
                json={
                    "email": "verified@vyomquant.io",
                    "password": "SecurePassword123!",
                    "username": "verifiedtrader",
                },
            )
            assert response.status_code == 201
            data = response.json()
            assert data.get("verification_required") is False
            assert data.get("access_token") == "valid_supabase_jwt_session"
        finally:
            app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════
# 3. P1 (F-06): MFA / AAL2 ENFORCEMENT
# ══════════════════════════════════════════════════════════════════════════

class TestF06MFAEnforcement:
    """Verifies that require_aal2 strictly checks the AAL claim in JWT."""

    def test_aal1_user_rejected_from_mfa_route(self):
        """User with password-only authentication (aal1) is rejected with 403."""
        user_aal1 = {
            "id": "user-aal1",
            "email": "user@vyomquant.io",
            "aal": "aal1",
            "app_metadata": {"aal": "aal1"},
        }
        with pytest.raises(HTTPException) as exc:
            asyncio.run(require_aal2(user_aal1))
        assert exc.value.status_code == 403
        assert "Two-factor authentication is required" in exc.value.detail

    def test_aal2_user_admitted_to_mfa_route(self):
        """User with completed MFA (aal2) is admitted."""
        user_aal2 = {
            "id": "user-aal2",
            "email": "user@vyomquant.io",
            "aal": "aal2",
            "app_metadata": {"aal": "aal2"},
        }
        res = asyncio.run(require_aal2(user_aal2))
        assert res["id"] == "user-aal2"

    def test_missing_aal_claim_defaults_to_aal1_and_rejected(self):
        """User with missing aal claim defaults safely to aal1 and is rejected."""
        user_no_aal = {
            "id": "user-no-aal",
            "email": "user@vyomquant.io",
            "app_metadata": {},
        }
        with pytest.raises(HTTPException) as exc:
            asyncio.run(require_aal2(user_no_aal))
        assert exc.value.status_code == 403


# ══════════════════════════════════════════════════════════════════════════
# 4. P1 (F-05): WEBSOCKET TICKET ENDPOINT
# ══════════════════════════════════════════════════════════════════════════

class TestF05WebSocketTicketEndpoint:
    """Verifies that the /api/auth/ws-ticket endpoint issues tickets for authenticated users."""

    def test_unauthenticated_ws_ticket_rejected(self):
        """Unauthenticated request to /api/auth/ws-ticket returns 401."""
        response = client.post("/api/auth/ws-ticket")
        assert response.status_code in (401, 403)

    def test_authenticated_ws_ticket_issued(self):
        """Authenticated user receives a secure ticket with TTL."""
        auth_user = {
            "id": "trader-uuid-123",
            "email": "trader@vyomquant.io",
            "role": "authenticated",
            "app_metadata": {},
        }
        app.dependency_overrides[get_current_user] = lambda: auth_user
        try:
            response = client.post(
                "/api/auth/ws-ticket",
                headers={"Authorization": "Bearer valid_jwt_token"}
            )
            assert response.status_code == 200
            data = response.json()
            assert "ticket" in data
            assert len(data["ticket"]) >= 32
            assert data.get("ttl_seconds") == 30
        finally:
            app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════
# 5. P1 (F-03/F-04): SECRET SAFETY AND LOGGING AUDIT
# ══════════════════════════════════════════════════════════════════════════

class TestF03F04SecretSafetyAudit:
    """Static checks to verify no credentials appear in logs or test output."""

    def test_apiclient_request_log_is_gated(self):
        """Verify that console.log('📡 REQUEST:') in apiClient.js is gated behind DEV."""
        with open("algo22-terminal/src/apiClient.js", "r", encoding="utf-8") as f:
            content = f.read()
        
        # Check that the unconditional log is gone
        assert "console.log(\"📡 REQUEST:\", config.url);" not in content or \
               "if (import.meta.env.DEV)" in content

    def test_e2e_test_does_not_print_raw_jwt(self):
        """Verify that app.spec.js does not print unredacted access token."""
        with open("tests/e2e/app.spec.js", "r", encoding="utf-8") as f:
            content = f.read()
        assert "console.log('Access token obtained:', accessToken);" not in content
        assert "console.log('Final access token:', accessToken);" not in content
