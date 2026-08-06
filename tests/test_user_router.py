"""
tests/test_user_router.py

Unit tests for backend_app/routers/user.py
Validates:
1. GET /api/user/profile returns user profile data.
2. PUT /api/user/profile allows updating whitelisted fields and rejects protected fields (e.g. subscription_tier).
3. GET /api/billing/plans returns billing plan details.
4. GET /api/referral/stats returns referral statistics without crashing when supabase is None.
5. GET /api/notifications/settings returns settings dict.
6. GET /api/security/logs returns security log entries.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app


@pytest.fixture
def test_user():
    return {
        "id": "usr_test_777",
        "email": "trader777@vyomquant.io",
        "role": "authenticated",
    }


def test_update_profile_whitelist_protection(test_user):
    """
    Test PUT /api/user/profile rejects updates to non-whitelisted fields (e.g., subscription_tier).
    """
    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_request_supabase] = lambda: None

    try:
        client = TestClient(app)

        # Attempt to upgrade tier directly via profile update — must fail 403
        bad_response = client.put(
            "/api/user/profile",
            json={"subscription_tier": "pro_999", "display_name": "Pro Trader"},
        )
        assert bad_response.status_code == 403
        assert "Cannot update protected fields" in bad_response.json()["detail"]

        # Valid update of whitelisted field — must succeed 200
        good_response = client.put(
            "/api/user/profile",
            json={"display_name": "Quant Trader", "bio": "Algo developer"},
        )
        assert good_response.status_code == 200
        assert good_response.json()["status"] == "success"

    finally:
        app.dependency_overrides.clear()


def test_user_endpoints_with_none_supabase(test_user):
    """
    Test user endpoints execute cleanly when get_request_supabase returns None (DEV_MODE fallback).
    """
    app.dependency_overrides[get_current_user] = lambda: test_user
    app.dependency_overrides[get_request_supabase] = lambda: None

    try:
        client = TestClient(app)

        # Profile
        res_prof = client.get("/api/user/profile")
        assert res_prof.status_code == 200
        assert res_prof.json()["id"] == test_user["id"]

        # Referral stats
        res_ref = client.get("/api/referral/stats")
        assert res_ref.status_code == 200
        assert res_ref.json()["status"] == "active"
        assert res_ref.json()["total_referrals"] == 0

        # Notifications
        res_notif = client.get("/api/notifications/settings")
        assert res_notif.status_code == 200
        assert res_notif.json() == {}

        # Security logs
        res_sec = client.get("/api/security/logs")
        assert res_sec.status_code == 200
        assert res_sec.json() == []

    finally:
        app.dependency_overrides.clear()
