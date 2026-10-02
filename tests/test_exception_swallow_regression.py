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
# signals.py — list_signal_traces: RESOLVED BY DELETION (task 13.19)
# ---------------------------------------------------------------------------

class TestListSignalTracesExceptionSwallow:
    """``GET /api/signals/`` no longer exists, so its swallow cannot return.

    WHY THIS CLASS CHANGED SHAPE RATHER THAN BEING DELETED
    ------------------------------------------------------
    The original test drove ``GET /api/signals/`` with a client whose ``table()`` raised
    an *injected* ``Exception`` and asserted ``503 SIGNAL_FETCH_FAILED``. It passed. What
    it could not see is that the handler answered that same 503 for *every* caller in
    production, with no injection at all: its projection named twelve columns
    ``public.execution_records`` does not have, so the real read raised ``42703`` and the
    blanket ``except`` reported it exactly as it reported this injected crash. A
    permissive double cannot tell "the database is down" from "this projection can never
    succeed" — which is why this suite stayed green for the whole life of that defect.

    Task 13.19 removed the endpoint as superseded by ``routers/signal_trace.py``. The
    exception-swallow property it guarded is therefore resolved by deletion, not by a
    code change, and the assertion that keeps it resolved is that the route is gone. The
    equivalent live surface, ``GET /api/signal-trace/signals``, is covered by
    ``tests/test_task_13_1_signal_trace_list.py``; the projection defect itself is
    covered by ``tests/test_mounted_endpoint_projections.py``, which uses a
    schema-faithful double that CAN reproduce a ``42703``.
    """

    def test_the_endpoint_is_gone_rather_than_swallowing(self):
        app.dependency_overrides[get_current_user] = lambda: _user()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get("/api/signals/")
            assert r.status_code == 404, (
                "GET /api/signals/ answered %s. It was deleted in task 13.19 as "
                "superseded by /api/signal-trace/signals; if it is back, it needs a "
                "projection that names only columns execution_records has, and this "
                "class needs its original 503 assertion back with it."
                % r.status_code
            )
        finally:
            app.dependency_overrides.clear()

    def test_the_surviving_replay_endpoint_still_refuses_rather_than_swallowing(self):
        """The swallow pattern is still guarded on the endpoint that survived.

        ``replay_signal_trace`` carries the same blanket ``except Exception``. It must
        answer a failure, not an empty 200 — so a crashing client produces a 5xx with a
        machine-readable code, which is the property this file exists for.
        """
        app.dependency_overrides[get_current_user] = lambda: _user()

        import backend_app.routers.signals as sig_module

        crashing = _crashing_supabase("QuestDB unreachable")
        original = sig_module.create_request_supabase_async

        async def _crashing_async(token):
            return crashing

        sig_module.create_request_supabase_async = _crashing_async

        try:
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/api/signals/sig-1/replay")
            assert r.status_code >= 500, (
                f"Expected a 5xx (db crash), got {r.status_code}: {r.text}"
            )
            assert "SIGNAL_RETRIEVAL_FAILED" in r.text
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
