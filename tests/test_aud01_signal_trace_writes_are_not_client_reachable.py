"""AUD-01 -- the execution audit trail no longer accepts order/fill/fee/risk data from
an ordinary authenticated caller.

THE DEFECT
    ``routers/signal_trace.py`` declared four mutating routes - ``POST /signals``,
    ``PUT /signals/{id}/risk``, ``PUT /signals/{id}/order``,
    ``PUT /signals/{id}/execution`` - gated by nothing more than ``get_current_user``
    (any authenticated trader). Each wrote WHATEVER the request body said directly into
    ``public.signals`` via the identical ``SignalService.create_signal`` /
    ``update_risk_decision`` / ``update_order`` / ``update_execution`` methods the
    platform's own runtime uses to record a REAL signal. A trader could therefore:

    * write a signal for a strategy or deployment they do not own just by naming its id
      in the request body (nothing in these methods validates the ids against anything);
    * claim a risk engine "approved" a position, with any ``capital``/``exposure``/
      ``expected_loss`` figures they chose;
    * report an order fill, fee, slippage and PnL that never happened.

    ``tests/sandbox_lifecycle/test_cross_tenant_ownership_matrix.py``'s own docstring had
    already found the database write was correctly scoped AGAINST OTHER TENANTS
    (``.eq("id", ...).eq("user_id", ...)``) - the gap this file is about is different: the
    DATA ITSELF was never validated against anything real, for the caller's own identity.

THE FIX
    All four routes were deleted. Neither the frontend
    (grepped across ``algo22-terminal/src/``) nor anything else in this backend ever
    called any of the four paths over HTTP - the real writer,
    ``backend/master_executor.py``, calls the SAME ``SignalService`` methods as a direct,
    in-process Python call. Deleting the HTTP surface removes the only way an ordinary
    caller could ever reach these methods, while the runtime's own calls are completely
    unaffected. The read endpoints (list, export, detail, timeline) are UNCHANGED.

WHAT THIS FILE PROVES
    * the four routes answer 404/405 (gone), not 200 and not even 401/403 - there is
      nothing left at that path for ANY identity, authenticated or not;
    * the read endpoints are still there and still require authentication;
    * the module no longer defines the four request models or the four handlers at all;
    * the REAL write path (SignalService's methods, as master_executor.py calls them) is
      completely unaffected and still produces a real, readable signal.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user
from backend_app.main import app

TEST_USER = {
    "id": "aud01-test-user",
    "email": "trader@example.invalid",
    "access_token": "",
    "app_metadata": {},
}


@pytest.fixture
def authenticated_client():
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def anonymous_client():
    return TestClient(app, raise_server_exceptions=False)


DELETED_WRITE_ROUTES = (
    ("POST", "/api/signal-trace/signals", {"strategy_id": "forged"}),
    ("PUT", "/api/signal-trace/signals/sig-1/risk", {"risk_passed": True, "risk_reason": "x"}),
    ("PUT", "/api/signal-trace/signals/sig-1/order", {"order_id": "x", "order_status": "FILLED",
        "quantity": 1, "filled": 1, "remaining": 0, "average_price": 1, "fees": 0,
        "slippage": 0, "latency_ms": 1}),
    ("PUT", "/api/signal-trace/signals/sig-1/execution", {"trade_id": "x", "pnl": 0}),
)


class TestTheFourWriteRoutesAreGone:
    @pytest.mark.parametrize("method,path,body", DELETED_WRITE_ROUTES, ids=[r[1] for r in DELETED_WRITE_ROUTES])
    def test_an_authenticated_trader_cannot_reach_it(self, authenticated_client, method, path, body):
        response = authenticated_client.request(method, path, json=body)
        assert response.status_code in (404, 405), (
            f"{method} {path} is still reachable (status {response.status_code}) - an "
            "authenticated trader could still write fabricated audit-trail data"
        )

    @pytest.mark.parametrize("method,path,body", DELETED_WRITE_ROUTES, ids=[r[1] for r in DELETED_WRITE_ROUTES])
    def test_an_anonymous_caller_cannot_reach_it_either(self, anonymous_client, method, path, body):
        """NEGATIVE CONTROL. Not just unauthenticated-refused (401/403) - genuinely gone."""
        response = anonymous_client.request(method, path, json=body)
        assert response.status_code in (404, 405)


class TestTheReadEndpointsStillWork:
    def test_list_signals_still_requires_authentication(self, anonymous_client):
        response = anonymous_client.get("/api/signal-trace/signals")
        assert response.status_code in (401, 403)

    def test_list_signals_still_answers_for_an_authenticated_caller(self, authenticated_client):
        response = authenticated_client.get("/api/signal-trace/signals")
        assert response.status_code != 404, (
            "GET /api/signal-trace/signals should still exist - AUD-01 only removed "
            "mutating routes"
        )


def test_the_deleted_handlers_and_models_no_longer_exist_in_the_module():
    import backend_app.routers.signal_trace as signal_trace_router

    for name in (
        "create_signal",
        "update_risk_decision",
        "update_order",
        "update_execution",
        "SignalCreateRequest",
        "RiskDecisionRequest",
        "OrderUpdateRequest",
        "ExecutionUpdateRequest",
    ):
        assert not hasattr(signal_trace_router, name), (
            f"backend_app.routers.signal_trace still defines {name!r} - AUD-01's fix was "
            "supposed to delete it"
        )

    # The read handlers must still be there.
    for name in ("list_signals", "export_signals", "get_signal", "get_signal_timeline"):
        assert hasattr(signal_trace_router, name), f"the read handler {name!r} went missing too"


def test_the_real_internal_write_path_is_unaffected():
    """The runtime's own call (what backend/master_executor.py does) still works end to
    end, through the exact SignalService methods the deleted routes used to expose.
    """
    from backend_app.backend.signal_service import SignalService, SignalStatus

    async def _drive():
        service = SignalService()
        user = {"id": "runtime-internal-user"}

        sig = await service.create_signal(
            user=user,
            strategy_id="strat-1",
            strategy_version="1.0",
            deployment_id="dep-1",
            exchange_id="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            worker_id="worker-1",
            decision="BUY",
            indicators={"rsi": 50.0},
            market_info={"price": 65000.0},
        )
        assert sig["status"] == SignalStatus.PENDING
        signal_id = sig["id"]

        updated = await service.update_risk_decision(
            user=user,
            signal_id=signal_id,
            risk_passed=True,
            risk_reason="within limits",
        )
        assert updated["risk_passed"] is True
        return signal_id

    signal_id = asyncio.run(_drive())
    assert signal_id
