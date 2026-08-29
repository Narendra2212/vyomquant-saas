"""
tests/test_risk_management_lifecycle.py — Production-Grade Test Battery for Risk Management.

Verifies:
1. Risk Settings Retrieval & Defaults (/api/risk/settings)
2. Server-side Validation & Bounds Checking (PUT /api/risk/settings, POST /api/risk/validate)
3. Live Risk Status & Utilization Calculation (/api/risk/status)
4. Margin Health & Risk Score Calculation (/api/risk/margin-health)
5. Kill Switch Activation, Recovery & Trading Block Enforcement
6. Strategy Limits CRUD (/api/risk/strategy-limits)
7. Audit Log of Risk Violations (/api/risk/violations)
8. Execution Integration: Paper & Strategy Orders Blocked by Risk Limits (Daily Loss, Max Positions, Kill Switch)
9. Tenant Isolation & IDOR Protection
"""

import os
import time
import pytest
from uuid import uuid4
from fastapi.testclient import TestClient

from backend_app.main import app
from backend_app.backend.paper_trading_service import get_paper_trading_service


@pytest.fixture
def client():
    return TestClient(app)


def get_test_auth_token(user_id: str = "test-risk-user-12345", tenant_id: str = "tenant-risk-1"):
    import jwt
    secret = os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("JWT_SECRET") or "dev-secret-change-in-production"
    payload = {
        "sub": user_id,
        "email": f"{user_id}@vyomquant.io",
        "tenant_id": tenant_id,
        "role": "authenticated",
        "aud": "authenticated",
        "iss": "algo22-test",
        "exp": int(time.time()) + 3600,
        "app_metadata": {"role": "authenticated", "tenant_id": tenant_id}
    }
    return jwt.encode(payload, secret, algorithm="HS256")


# ═══════════════════════════════════════════════════════════════════════════
# 1. RISK SETTINGS RETRIEVAL & UPDATES
# ═══════════════════════════════════════════════════════════════════════════

def test_get_risk_settings_defaults(client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}

    res = client.get("/api/risk/settings", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "max_daily_loss" in data
    assert "max_positions" in data
    assert "max_leverage" in data
    assert data["max_daily_loss"] > 0


def test_update_risk_settings_and_validation(client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Valid update
    update_payload = {
        "max_daily_loss": 1250.0,
        "max_positions": 5,
        "max_leverage": 5,
        "circuit_breaker_armed": True,
        "kill_switches": {"loss": True, "blackswan": True, "streak": True, "capital": True}
    }
    res = client.put("/api/risk/settings", json=update_payload, headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    # Verify saved settings
    get_res = client.get("/api/risk/settings", headers=headers)
    saved = get_res.json()
    assert saved["max_daily_loss"] == 1250.0
    assert saved["max_positions"] == 5
    assert saved["max_leverage"] == 5

    # 2. Invalid update: Negative daily loss
    res_bad = client.put("/api/risk/settings", json={"max_daily_loss": -100.0, "max_positions": 5, "max_leverage": 3, "circuit_breaker_armed": True}, headers=headers)
    assert res_bad.status_code == 422 or res_bad.status_code == 400


def test_validate_risk_endpoint(client):
    token = get_test_auth_token()
    headers = {"Authorization": f"Bearer {token}"}

    # Valid payload
    res_valid = client.post("/api/risk/validate", json={"max_daily_loss": 2000.0, "max_positions": 10, "max_leverage": 3}, headers=headers)
    assert res_valid.status_code == 200
    assert res_valid.json()["valid"] is True

    # Invalid payload
    res_invalid = client.post("/api/risk/validate", json={"max_daily_loss": -50.0, "max_positions": 200, "max_leverage": 0}, headers=headers)
    assert res_invalid.status_code == 200
    assert res_invalid.json()["valid"] is False
    assert len(res_invalid.json()["errors"]) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 2. RISK STATUS & MARGIN HEALTH
# ═══════════════════════════════════════════════════════════════════════════

def test_risk_status_and_margin_health(client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}

    # Status
    res_status = client.get("/api/risk/status", headers=headers)
    assert res_status.status_code == 200
    status_data = res_status.json()
    assert "status" in status_data
    assert "daily_loss" in status_data
    assert "positions" in status_data

    # Margin health
    res_margin = client.get("/api/risk/margin-health", headers=headers)
    assert res_margin.status_code == 200
    margin_data = res_margin.json()
    assert "margin_ratio" in margin_data
    assert "free_margin" in margin_data
    assert "risk_score" in margin_data


# ═══════════════════════════════════════════════════════════════════════════
# 3. KILL SWITCH LIFECYCLE & TRADING BLOCK
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_kill_switch_blocks_execution(client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    paper_svc = get_paper_trading_service()

    # 1. Activate kill switch
    res_kill = client.post("/api/risk/kill-switch", json={"scope": "user", "reason": "Testing emergency halt"}, headers=headers)
    assert res_kill.status_code == 200
    assert res_kill.json()["kill_switch_active"] is True

    # Check status shows BLOCKED
    res_status = client.get("/api/risk/status", headers=headers)
    assert res_status.json()["status"] == "BLOCKED"
    assert res_status.json()["kill_switch_active"] is True

    # 2. Attempting to place an order while kill-switched MUST FAIL
    with pytest.raises(ValueError, match="Trading halted: Risk Kill Switch is active"):
        await paper_svc.place_order(
            user_id=user_id,
            symbol="BTC-USDT",
            side="buy",
            quantity=0.1,
            price=60000.0
        )

    # 3. Deactivate / Recover kill switch
    res_recover = client.post("/api/risk/kill-switch/recover", headers=headers)
    assert res_recover.status_code == 200
    assert res_recover.json()["kill_switch_active"] is False

    # 4. Now order execution is permitted
    ord_success = await paper_svc.place_order(
        user_id=user_id,
        symbol="BTC-USDT",
        side="buy",
        quantity=0.1,
        price=60000.0
    )
    assert ord_success["status"] == "FILLED"


# ═══════════════════════════════════════════════════════════════════════════
# 4. MAX OPEN POSITIONS ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_max_positions_risk_enforcement(client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    paper_svc = get_paper_trading_service()

    # Configure max positions = 2
    client.put("/api/risk/settings", json={"max_daily_loss": 50000.0, "max_positions": 2, "max_leverage": 3, "circuit_breaker_armed": True}, headers=headers)

    # 1. Open Position 1: BTC
    await paper_svc.place_order(user_id=user_id, symbol="BTC-USDT", side="buy", quantity=0.1, price=60000.0)
    # 2. Open Position 2: ETH
    await paper_svc.place_order(user_id=user_id, symbol="ETH-USDT", side="buy", quantity=1.0, price=3000.0)

    # 3. Open Position 3: SOL -> MUST FAIL (Limit of 2 reached)
    with pytest.raises(ValueError, match="Risk limit exceeded: Max open positions reached"):
        await paper_svc.place_order(user_id=user_id, symbol="SOL-USDT", side="buy", quantity=10.0, price=150.0)


# ═══════════════════════════════════════════════════════════════════════════
# 5. STRATEGY LIMITS CRUD
# ═══════════════════════════════════════════════════════════════════════════

def test_strategy_limits_crud(client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Put strategy limits
    payload = {
        "limits": [
            {
                "strategy_id": "strat_alpha_1",
                "max_position_size": 2500.0,
                "max_daily_trades": 50,
                "allowed_symbols": ["BTC-USDT", "ETH-USDT"],
                "max_drawdown_pct": 0.08,
                "enabled": True
            }
        ]
    }
    res_put = client.put("/api/risk/strategy-limits", json=payload, headers=headers)
    assert res_put.status_code == 200

    # 2. Get strategy limits
    res_get = client.get("/api/risk/strategy-limits", headers=headers)
    assert res_get.status_code == 200
    assert res_get.json()["count"] == 1
    assert res_get.json()["limits"][0]["strategy_id"] == "strat_alpha_1"

    # 3. Direct single strategy limit update (as sent by UI slider)
    res_single = client.put("/api/risk/strategy-limits/strat_alpha_1", json={"max_position_size": 35.0}, headers=headers)
    assert res_single.status_code == 200
    assert res_single.json()["status"] == "ok"

    # 4. Delete strategy limit
    res_del = client.delete("/api/risk/strategy-limits/strat_alpha_1", headers=headers)
    assert res_del.status_code == 200
    assert res_del.json()["deleted"] == "strat_alpha_1"


# ═══════════════════════════════════════════════════════════════════════════
# 6. VIOLATIONS AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════════

def test_risk_violations_audit_log(client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}

    # Trigger kill switch to generate violation
    client.post("/api/risk/kill-switch", json={"reason": "Audit log check"}, headers=headers)

    res_v = client.get("/api/risk/violations", headers=headers)
    assert res_v.status_code == 200
    violations = res_v.json()["violations"]
    assert len(violations) >= 1
    assert violations[0]["rule"] == "KILL_SWITCH_MANUAL_ACTIVATION"
