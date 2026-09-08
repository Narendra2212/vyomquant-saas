"""
tests/test_risk_gate_enforcement.py — Runtime Risk Gate Enforcement & Behavioral Audit Suite.

Verifies that every supported risk setting directly and immediately controls
order execution decisions at runtime, proving:
- Tightening a setting blocks violating executions
- Relaxing a setting permits valid executions
- Rejected orders produce ZERO financial side-effects (no balance deductions, no position mutations)
- Concurrent order submissions cannot race or bypass position/exposure limits
- Strategy-specific symbol restrictions and notional caps are strictly enforced
"""

import asyncio
from decimal import Decimal
import os
import time
import pytest
from uuid import uuid4

from fastapi.testclient import TestClient
from backend_app.main import app
from backend_app.backend.paper import paper_repository as paper_repo
from backend_app.backend.paper_trading_service import get_paper_trading_service
from backend_app.routers.risk import (
    _user_risk_settings, _user_strategy_limits, _user_kill_switch_state,
    get_user_risk_settings_store, is_user_kill_switched
)
from tests.test_paper_repository import FakeSupabase


@pytest.fixture(autouse=True)
def _paper_persistence():
    """Give the paper service the storage it now requires.

    The risk gates below are evaluated inside ``PaperTradingService.place_order``, and as of
    marketplace-subscriptions-paper-trading task 23.2 that service keeps no balance, no position
    and no order in process memory: it reads and writes the ``paper_*`` tables, and an absent
    ``paper_accounts`` is answered with 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` rather than with a
    remembered figure (Requirements 17.2, 28.3). So each test gets a fresh in-memory
    Persistence_Layer - the double from ``tests/test_paper_repository.py``, which enforces the
    five unique indexes ``009_paper_trading.sql`` declares - and the gates are asserted against
    persisted balances and persisted positions. Not one threshold, message or outcome below
    changed.
    """
    paper_repo.reset_persistence_probe()
    service = get_paper_trading_service()
    service.bind_persistence(FakeSupabase())
    try:
        yield service
    finally:
        service.bind_persistence(None)
        paper_repo.reset_persistence_probe()


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
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def auth_client():
    return TestClient(app)


def _seed_realized_pnl(paper_svc, user_id: str, realized_pnl: str) -> None:
    """Persist ``realized_pnl`` on the user's default paper account.

    The daily-loss gate reads ``paper_accounts.realized_pnl``, so a test that wants a realized
    loss has to write one. Only that column moves: ``realized_pnl`` is not part of the equity
    identity (``total_equity = available + locked + position_market_value``), so the seeded row
    stays consistent and ``verify_accounting_invariants`` still holds.
    """
    supabase = paper_svc._supabase
    account = paper_svc.get_or_create_account(user_id)
    locked = paper_repo.lock_account_for_update(
        supabase, str(user_id), account_id=account["account_id"]
    )
    paper_repo.bump_version(
        supabase,
        user_id=str(user_id),
        account_id=account["account_id"],
        expected_version=locked["version"],
        payload={"realized_pnl": realized_pnl},
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. EMERGENCY KILL SWITCH RUNTIME ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_kill_switch_runtime_gate_enforcement(auth_client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    paper_svc = get_paper_trading_service()

    # 1. Activate kill switch via API
    res = auth_client.post("/api/risk/kill-switch", json={"scope": "user", "reason": "Audit Kill Switch"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["kill_switch_active"] is True

    # 2. Capture initial account state
    acct_before = paper_svc.get_or_create_account(user_id)
    init_avail = Decimal(str(acct_before["available_balance"]))

    # 3. Order MUST be blocked by runtime risk gate with zero balance mutation
    with pytest.raises(ValueError, match="Trading halted: Risk Kill Switch is active"):
        await paper_svc.place_order(
            user_id=user_id,
            symbol="BTC-USDT",
            side="buy",
            quantity=0.5,
            price=60000.0
        )

    # 4. Zero Financial Side-Effects
    acct_after = paper_svc.get_or_create_account(user_id)
    assert Decimal(str(acct_after["available_balance"])) == init_avail
    assert len(paper_svc.get_positions(user_id)) == 0

    # 5. Recover kill switch via API
    res_recover = auth_client.post("/api/risk/kill-switch/recover", headers=headers)
    assert res_recover.status_code == 200
    assert res_recover.json()["kill_switch_active"] is False

    # 6. Order now proceeds successfully
    order = await paper_svc.place_order(
        user_id=user_id,
        symbol="BTC-USDT",
        side="buy",
        quantity=0.5,
        price=60000.0
    )
    assert order["status"] == "FILLED"
    assert len(paper_svc.get_positions(user_id)) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 2. MAX DAILY LOSS RUNTIME ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_max_daily_loss_runtime_gate_enforcement(auth_client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    paper_svc = get_paper_trading_service()

    # Set restrictive max_daily_loss = $200
    auth_client.put("/api/risk/settings", json={"max_daily_loss": 200.0, "max_positions": 10}, headers=headers)

    # Simulate realized loss of -$250, in the row the gate reads.
    #
    # The old form of this line assigned to the dict ``get_or_create_account`` returned, which
    # was the store itself. It is a projection of a ``paper_accounts`` row now, so the loss is
    # seeded where the gate looks for it - through the repository, under the version guard.
    _seed_realized_pnl(paper_svc, user_id, "-250.00")

    # Attempt order -> MUST BE REJECTED
    with pytest.raises(ValueError, match="Risk limit exceeded: Max Daily Loss limit reached"):
        await paper_svc.place_order(
            user_id=user_id,
            symbol="ETH-USDT",
            side="buy",
            quantity=1.0,
            price=3000.0
        )

    # Relax limit to $500
    auth_client.put("/api/risk/settings", json={"max_daily_loss": 500.0}, headers=headers)

    # Retry order -> MUST NOW BE ALLOWED
    ord_success = await paper_svc.place_order(
        user_id=user_id,
        symbol="ETH-USDT",
        side="buy",
        quantity=1.0,
        price=3000.0
    )
    assert ord_success["status"] == "FILLED"


# ═══════════════════════════════════════════════════════════════════════════
# 3. MAX OPEN POSITIONS RUNTIME ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_max_positions_runtime_gate_enforcement(auth_client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    paper_svc = get_paper_trading_service()

    # Set max_positions = 1
    auth_client.put("/api/risk/settings", json={"max_positions": 1, "max_daily_loss": 10000.0}, headers=headers)

    # Position 1: BTC -> ALLOWED
    await paper_svc.place_order(user_id=user_id, symbol="BTC-USDT", side="buy", quantity=0.1, price=60000.0)
    assert len(paper_svc.get_positions(user_id)) == 1

    # Position 2: ETH -> MUST BE REJECTED
    with pytest.raises(ValueError, match="Risk limit exceeded: Max open positions reached"):
        await paper_svc.place_order(user_id=user_id, symbol="ETH-USDT", side="buy", quantity=1.0, price=3000.0)

    # Increase max_positions = 2
    auth_client.put("/api/risk/settings", json={"max_positions": 2}, headers=headers)

    # Retry Position 2: ETH -> MUST NOW BE ALLOWED
    ord2 = await paper_svc.place_order(user_id=user_id, symbol="ETH-USDT", side="buy", quantity=1.0, price=3000.0)
    assert ord2["status"] == "FILLED"
    assert len(paper_svc.get_positions(user_id)) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 4. STRATEGY POSITION SIZE & NOTIONAL LIMIT
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_strategy_max_notional_gate_enforcement(auth_client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    paper_svc = get_paper_trading_service()

    # Configure strategy limit: max_position_size = $5,000
    strat_id = "strat_scalp_99"
    auth_client.put(f"/api/risk/strategy-limits/{strat_id}", json={"max_position_size": 5000.0}, headers=headers)

    # Order 1: 0.1 BTC @ $60,000 = $6,000 -> EXCEEDS $5,000 LIMIT -> REJECTED
    with pytest.raises(ValueError, match="Risk limit exceeded: Order size .* exceeds maximum strategy limit"):
        await paper_svc.place_order(
            user_id=user_id,
            strategy_id=strat_id,
            symbol="BTC-USDT",
            side="buy",
            quantity=0.1,
            price=60000.0
        )

    # Order 2: 0.05 BTC @ $60,000 = $3,000 -> UNDER $5,000 LIMIT -> ALLOWED
    ord_pass = await paper_svc.place_order(
        user_id=user_id,
        strategy_id=strat_id,
        symbol="BTC-USDT",
        side="buy",
        quantity=0.05,
        price=60000.0
    )
    assert ord_pass["status"] == "FILLED"


# ═══════════════════════════════════════════════════════════════════════════
# 5. STRATEGY SYMBOL RESTRICTIONS
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_strategy_symbol_restrictions_gate_enforcement(auth_client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    paper_svc = get_paper_trading_service()

    # Strategy only allowed to trade BTC-USDT
    strat_id = "strat_btc_only"
    auth_client.put(f"/api/risk/strategy-limits/{strat_id}", json={
        "allowed_symbols": ["BTC-USDT"],
        "max_position_size": 100000.0
    }, headers=headers)

    # Attempt to trade SOL-USDT -> MUST BE REJECTED
    with pytest.raises(ValueError, match="Risk limit exceeded: Symbol SOL-USDT is not allowed"):
        await paper_svc.place_order(
            user_id=user_id,
            strategy_id=strat_id,
            symbol="SOL-USDT",
            side="buy",
            quantity=10.0,
            price=150.0
        )

    # Trade BTC-USDT -> ALLOWED
    ord_btc = await paper_svc.place_order(
        user_id=user_id,
        strategy_id=strat_id,
        symbol="BTC-USDT",
        side="buy",
        quantity=0.1,
        price=60000.0
    )
    assert ord_btc["status"] == "FILLED"


# ═══════════════════════════════════════════════════════════════════════════
# 6. CONCURRENT ORDER SERIALIZATION & RACE CONDITION GUARD
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_concurrent_orders_risk_gate_serialization(auth_client):
    user_id = str(uuid4())
    token = get_test_auth_token(user_id=user_id)
    headers = {"Authorization": f"Bearer {token}"}
    paper_svc = get_paper_trading_service()

    # Set max_positions = 1
    auth_client.put("/api/risk/settings", json={"max_positions": 1}, headers=headers)

    # Concurrently launch 2 orders for distinct symbols
    results = await asyncio.gather(
        paper_svc.place_order(user_id=user_id, symbol="BTC-USDT", side="buy", quantity=0.1, price=60000.0),
        paper_svc.place_order(user_id=user_id, symbol="ETH-USDT", side="buy", quantity=1.0, price=3000.0),
        return_exceptions=True
    )

    successes = [r for r in results if isinstance(r, dict) and r.get("status") == "FILLED"]
    failures = [r for r in results if isinstance(r, Exception)]

    # Exactly 1 must succeed and exactly 1 must be rejected by the risk lock
    assert len(successes) == 1
    assert len(failures) == 1
    assert len(paper_svc.get_positions(user_id)) == 1
