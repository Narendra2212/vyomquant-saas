"""FE-03: /api/system/trading-capabilities reports live availability honestly."""
import pytest
from fastapi.testclient import TestClient

from backend_app.main import app

client = TestClient(app)


@pytest.mark.parametrize(
    "mode,enable,expected",
    [
        ("safe", "true", False),
        ("paper", "true", False),
        ("live", "false", False),
        ("live", None, False),
        ("live", "true", True),
        ("live_trading", "true", True),
    ],
)
def test_live_is_available_only_when_mode_and_enable_flag_agree(monkeypatch, mode, enable, expected):
    for k in ("VYOMQUANT_MODE", "AERORA_MODE", "VYOMQUANT_ENABLE_LIVE_TRADING", "AERORA_ENABLE_LIVE_TRADING"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VYOMQUANT_MODE", mode)
    if enable is not None:
        monkeypatch.setenv("VYOMQUANT_ENABLE_LIVE_TRADING", enable)
    res = client.get("/api/system/trading-capabilities")
    assert res.status_code == 200
    assert res.json()["live_trading_available"] is expected
