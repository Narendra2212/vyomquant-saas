"""
tests/test_exception_swallow_regression.py

Regression protection for the exception-swallowing pattern:
  "except Exception: return empty-200" on read endpoints.

BEFORE the fix, all assertions on status_code == 503 would FAIL
because each handler returned HTTP 200 with empty data when the
underlying query raised.

After the fix, every handler raises HTTPException(503).
"""

import os
import sys
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _user():
    return {
        "id": "usr_swallow_test",
        "email": "test@vyomquant.io",
        "role": "authenticated",
        "access_token": "fake-token",
    }


def _crashing_supabase(exc_msg="DB exploded"):
    """Returns a mock supabase client whose table().* chain always raises."""
    sb = MagicMock()
    sb.table.side_effect = Exception(exc_msg)
    return sb


# ---------------------------------------------------------------------------
# support.py — get_tickets
# ---------------------------------------------------------------------------

class TestGetTicketsExceptionSwallow:
    def test_db_error_returns_503_not_empty_200(self):
        """
        Before fix: returned {"tickets": [], "count": 0} with HTTP 200.
        After fix:  returns HTTP 503 with error key.
        """
        app.dependency_overrides[get_current_user] = lambda: _user()
        app.dependency_overrides[get_request_supabase] = lambda: _crashing_supabase()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get("/api/support/tickets")
            assert r.status_code == 503, (
                f"Expected 503 (db crash), got {r.status_code}: {r.text}"
            )
            # Error code is in top-level "error" field, structured payload (if any) in "details"
            assert r.json()["error"] == "TICKETS_FETCH_FAILED"
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# signals.py — list_signal_traces
# ---------------------------------------------------------------------------

class TestListSignalTracesExceptionSwallow:
    def test_db_error_returns_503_not_empty_200(self):
        """
        Before fix: returned {"items": [], "total": 0} with HTTP 200.
        After fix:  returns HTTP 503.
        """
        app.dependency_overrides[get_current_user] = lambda: _user()

        # _sb() inside signals.py calls create_request_supabase(user.get("access_token"))
        # We need to patch create_request_supabase so it returns a crashing client.
        import backend_app.routers.signals as sig_module

        crashing = _crashing_supabase("QuestDB unreachable")
        original = sig_module.create_request_supabase_async
        sig_module.create_request_supabase_async = lambda token: crashing

        try:
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get("/api/signals/")
            assert r.status_code == 503, (
                f"Expected 503 (db crash), got {r.status_code}: {r.text}"
            )
            # Error code is in top-level "error" field
            assert r.json()["error"] == "SIGNAL_FETCH_FAILED"
        finally:
            sig_module.create_request_supabase_async = original
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# portfolio.py — equity_curve
# ---------------------------------------------------------------------------

class TestEquityCurveExceptionSwallow:
    def test_telemetry_error_returns_503_not_empty_list(self):
        """
        Before fix: returned [] with HTTP 200.
        After fix:  returns HTTP 503.
        """
        from backend_app.core.dependencies import get_telemetry

        async def crashing_telemetry():
            class CrashingTelemetry:
                async def execute_query(self, *args, **kwargs):
                    raise RuntimeError("QuestDB connection refused")
            return CrashingTelemetry()

        app.dependency_overrides[get_current_user] = lambda: _user()
        app.dependency_overrides[get_telemetry] = crashing_telemetry

        try:
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get("/api/portfolio/equity-curve?days=7")
            assert r.status_code == 503, (
                f"Expected 503 (telemetry crash), got {r.status_code}: {r.text}"
            )
            assert r.json()["error"] == "EQUITY_CURVE_FETCH_FAILED"
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# portfolio.py — recent_transactions
# ---------------------------------------------------------------------------

class TestRecentTransactionsExceptionSwallow:
    def test_telemetry_error_returns_503_not_empty_transactions(self):
        """
        Before fix: returned {"transactions": [], "count": 0} with HTTP 200.
        After fix:  returns HTTP 503.
        """
        from backend_app.core.dependencies import get_telemetry

        async def crashing_telemetry():
            class CrashingTelemetry:
                async def execute_query(self, *args, **kwargs):
                    raise RuntimeError("QuestDB timeout")
            return CrashingTelemetry()

        app.dependency_overrides[get_current_user] = lambda: _user()
        app.dependency_overrides[get_telemetry] = crashing_telemetry

        try:
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get("/api/portfolio/recent-transactions")
            assert r.status_code == 503, (
                f"Expected 503 (telemetry crash), got {r.status_code}: {r.text}"
            )
            assert r.json()["error"] == "TRANSACTIONS_FETCH_FAILED"
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# risk.py — get_strategy_limits
# ---------------------------------------------------------------------------

class TestGetStrategyLimitsExceptionSwallow:
    def test_db_error_returns_503_not_empty_limits(self):
        """
        Before fix: returned {"limits": [], "count": 0} with HTTP 200.
        After fix:  returns HTTP 503.
        """
        import backend_app.routers.risk as risk_module

        crashing = _crashing_supabase("Supabase timeout")
        original = risk_module.create_request_supabase_async
        risk_module.create_request_supabase_async = lambda token: crashing

        app.dependency_overrides[get_current_user] = lambda: _user()

        try:
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get("/api/risk/strategy-limits")
            assert r.status_code == 503, (
                f"Expected 503 (db crash), got {r.status_code}: {r.text}"
            )
            assert r.json()["error"] == "STRATEGY_LIMITS_FETCH_FAILED"
        finally:
            risk_module.create_request_supabase_async = original
            app.dependency_overrides.clear()
