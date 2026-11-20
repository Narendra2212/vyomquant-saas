"""BILL-CANCEL-01: cancelling with no paid subscription is refused and writes nothing.

Found 2026-11-20 in the final launch certification, in production: the free QA account's
``POST /api/billing/cancel`` answered 200 "will be cancelled at the end of the current billing
period" and stored subscription_status='cancelled' / cancel_at_period_end=true on a profile
with no subscription (reverted).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app

client = TestClient(app)


def test_free_plan_cancel_is_409_and_never_reaches_the_lifecycle():
    app.dependency_overrides[get_current_user] = lambda: {"id": "bill-cancel01", "access_token": "t"}
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        with patch(
            "backend_app.core.subscription_dependencies.get_plan_context",
            AsyncMock(return_value=SimpleNamespace(plan="free")),
        ), patch(
            "backend_app.core.billing_lifecycle.BillingLifecycle.cancel_subscription",
            AsyncMock(),
        ) as lifecycle:
            response = client.post("/api/billing/cancel")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409, response.text
    assert "NO_ACTIVE_SUBSCRIPTION" in response.text
    lifecycle.assert_not_called()
