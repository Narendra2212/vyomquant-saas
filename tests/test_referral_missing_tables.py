"""
tests/test_referral_missing_tables.py

Test suite verifying referral endpoints handle missing database tables gracefully.
This tests the fallback behavior when the referral_system_redesign.sql migration has not been executed.
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from backend_app.main import app
from backend_app.core.dependencies import get_current_user, get_request_supabase


def mock_get_current_user():
    return {"id": "test-user-123", "email": "trader@vyomquant.com", "role": "authenticated"}


def mock_get_request_supabase_missing_tables():
    """Mock Supabase client that simulates missing referral tables"""
    mock_sb = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_limit = MagicMock()
    mock_execute = MagicMock()

    # Simulate PostgREST error for missing table as Exception
    error = Exception("{'code': 'PGRST205', 'details': None, 'hint': \"Perhaps you meant the table 'public.processed_orders'\", 'message': \"Could not find the table 'public.referral_codes' in the schema cache\"}")
    mock_execute.execute.side_effect = error
    mock_limit.limit.return_value = mock_execute

    # Supabase table chaining behavior
    def mock_select_side_effect(columns="*"):
        return mock_limit

    mock_select.select.side_effect = mock_select_side_effect

    def mock_table_side_effect(table_name):
        return mock_select

    mock_sb.table.side_effect = mock_table_side_effect

    return mock_sb


class TestReferralMissingTables:
    """Test referral endpoints when database tables are missing"""

    def test_referral_stats_missing_tables_fallback(self):
        """Test that /api/referral/stats returns safe empty response when tables are missing"""
        app.dependency_overrides[get_current_user] = mock_get_current_user
        app.dependency_overrides[get_request_supabase] = mock_get_request_supabase_missing_tables

        try:
            client = TestClient(app)
            response = client.get("/api/referral/stats")
            assert response.status_code == 200
            data = response.json()

            # Should return safe empty response instead of 500
            assert data["referral_code"] == "NOT-AVAILABLE"
            assert data["total_referrals"] == 0
            assert data["active_referrals"] == 0
            assert data["pending_earnings"] == 0.0
            assert data["approved_earnings"] == 0.0
            assert data["paid_earnings"] == 0.0
            assert data["lifetime_earnings"] == 0.0
            assert data["commission_history"] == []
            assert data["payout_history"] == []
        finally:
            app.dependency_overrides.clear()

    def test_referral_profile_missing_tables_fallback(self):
        """Test that /api/referral/profile returns safe empty response when tables are missing"""
        app.dependency_overrides[get_current_user] = mock_get_current_user
        app.dependency_overrides[get_request_supabase] = mock_get_request_supabase_missing_tables

        try:
            client = TestClient(app)
            response = client.get("/api/referral/profile")
            assert response.status_code == 200
            data = response.json()

            # Should return safe empty response instead of 500
            assert data["referral_code"] == "NOT-AVAILABLE"
            assert data["total_referrals"] == 0
            assert data["active_referrals"] == 0
            assert data["pending_earnings"] == 0.0
            assert data["approved_earnings"] == 0.0
            assert data["paid_earnings"] == 0.0
            assert data["lifetime_earnings"] == 0.0
        finally:
            app.dependency_overrides.clear()
