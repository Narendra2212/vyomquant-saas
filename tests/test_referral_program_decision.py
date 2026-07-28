"""
tests/test_referral_program_decision.py

Test suite verifying referral stats endpoint behavior under the SHIP decision:
- Confirms domain does not use legacy algo22.io string
- Confirms 'status': 'active' response flag
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from backend_app.main import app
from backend_app.core.dependencies import get_current_user, get_request_supabase


def mock_get_current_user():
    return {"id": "test-user-123", "email": "trader@vyomquant.com", "role": "authenticated"}


def mock_get_request_supabase():
    mock_sb = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_eq = MagicMock()
    mock_execute = MagicMock()
    execute_mock = MagicMock()
    execute_mock.data = []
    mock_execute.execute.return_value = execute_mock
    mock_eq.eq.return_value = mock_execute
    
    # Supabase table chaining behavior
    def mock_select_side_effect(columns="*"):
        return mock_eq
        
    mock_select.select.side_effect = mock_select_side_effect
    
    def mock_table_side_effect(table_name):
        return mock_select
        
    mock_sb.table.side_effect = mock_table_side_effect

    return mock_sb


class TestReferralProgramDecision:

    def test_referral_stats_returns_coming_soon_and_canonical_domain(self):
        app.dependency_overrides[get_current_user] = mock_get_current_user
        app.dependency_overrides[get_request_supabase] = mock_get_request_supabase

        try:
            client = TestClient(app)
            response = client.get("/api/referral/stats")
            assert response.status_code == 200
            data = response.json()

            assert data["status"] == "active"
            assert data["available_discounts"] == 0
            assert "algo22.io" not in data["referral_link"]
            assert "vyomquant.com" in data["referral_link"] or "http" in data["referral_link"]
            assert data["referral_link"].endswith("/ref/TEST-USE")
        finally:
            app.dependency_overrides.clear()
