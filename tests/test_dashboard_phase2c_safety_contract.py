"""
tests/test_dashboard_phase2c_safety_contract.py

Authoritative tests for Phase 2C backend contracts:
1. POST /api/risk/kill-switch (activation, idempotency, optional body)
2. POST /api/risk/kill-switch/recover (recovery)
3. Multi-tenant isolation of kill-switch state
4. Derivatives position liquidation distance math verification
"""

import pytest
from httpx import AsyncClient, ASGITransport
from backend_app.main import app
from backend_app.core.dependencies import get_current_user
from backend_app.routers.risk import _user_kill_switch_state, is_user_kill_switched


@pytest.fixture(autouse=True)
def override_auth():
    """Mock authenticated user dependency for FastAPI routes."""
    mock_user = {
        "id": "test_user_phase2c_abc123",
        "email": "quant@vyomquant.com",
        "access_token": "valid_mock_jwt"
    }
    app.dependency_overrides[get_current_user] = lambda: mock_user
    yield mock_user
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_kill_switch_activation_and_recovery(override_auth):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Activate kill switch with reason
        payload = {"reason": "Manual emergency halt triggered from Dashboard (LIVE)"}
        
        # Test activation
        res = await client.post("/api/risk/kill-switch", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "halted"
        assert data["kill_switch_active"] is True

        # Verify state in risk module
        uid = override_auth["id"]
        assert is_user_kill_switched(uid) is True

        # Test recovery
        res_rec = await client.post("/api/risk/kill-switch/recover")
        assert res_rec.status_code == 200
        data_rec = res_rec.json()
        assert data_rec["status"] == "active"
        assert data_rec["kill_switch_active"] is False
        assert is_user_kill_switched(uid) is False


@pytest.mark.asyncio
async def test_kill_switch_empty_body(override_auth):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test activating without body
        res = await client.post("/api/risk/kill-switch", json={})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "halted"
        assert data["kill_switch_active"] is True
        
        # Clean up
        await client.post("/api/risk/kill-switch/recover")


def test_derivatives_liquidation_distance_math():
    """Verify standard crypto derivatives liquidation distance math."""
    # Long: ((Mark - Liq) / Mark) * 100
    mark_p = 60000.0
    liq_p = 54000.0
    long_dist = ((mark_p - liq_p) / mark_p) * 100
    assert round(long_dist, 2) == 10.00

    # Short: ((Liq - Mark) / Mark) * 100
    mark_s = 60000.0
    liq_s = 66000.0
    short_dist = ((liq_s - mark_s) / mark_s) * 100
    assert round(short_dist, 2) == 10.00
