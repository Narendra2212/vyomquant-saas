"""RISK-PUT-01: a risk-settings save that omits a field leaves that field as it was.

Found 2026-11-20 in the final launch certification, in production: the QA account set
Max Daily Loss to 250, then saved ``{"max_leverage": 2}`` alone -- and read back a Max Daily
Loss of 500. The handler keeps the stored value only when a field is ``None``, but the
request model defaulted every omitted field to the platform default (500 / 10 / 3 / armed),
so any partial save LOOSENED a limit the trader had tightened, and could disarm nothing
less than the circuit breaker's explicit choice.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user
from backend_app.main import app
from backend_app.routers import risk as risk_router

client = TestClient(app)


def test_omitted_fields_are_none_on_the_model():
    body = risk_router.RiskSettingsUpdateRequest(max_leverage=2)
    assert body.max_daily_loss is None
    assert body.max_positions is None
    assert body.circuit_breaker_armed is None


@patch("backend_app.routers.risk.get_service_supabase_async")
def test_a_partial_save_does_not_loosen_the_daily_loss_limit(mock_sb, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://stub.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "stub-anon-key")
    sb = Mock()
    sb.table.return_value.upsert.return_value.execute.return_value = Mock(data=[{"ok": True}])
    mock_sb.side_effect = AsyncMock(return_value=sb)
    uid = "risk_put01_user"
    monkeypatch.setattr(
        risk_router,
        "get_user_risk_settings_store",
        lambda _uid: {
            "max_daily_loss": 250.0,
            "max_positions": 4,
            "max_leverage": 3,
            "circuit_breaker_armed": False,
            "kill_switches": {},
        },
    )
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "access_token": "t"}
    try:
        response = client.put("/api/risk/settings", json={"max_leverage": 2})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    saved = sb.table.return_value.upsert.call_args[0][0]
    assert saved["max_daily_loss"] == 250.0
    assert saved["max_positions"] == 4
    assert saved["circuit_breaker_armed"] is False
    assert saved["max_leverage"] == 2


def test_each_save_notifies_under_its_own_idempotency_key():
    """NOTIF-RISK-01: the key was `risk_settings:{uid}`; the durable (user, key) dedupe then
    let a user hear about their FIRST risk change only. Found in production 2026-11-20:
    four saves, one notification, showing the first save's values."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "backend_app/routers/risk.py").read_text()
    assert '"idempotency_key": f"risk_settings:{uid}"' not in src
    assert "risk_settings:{uid}:{updated_record['updated_at']}" in src
