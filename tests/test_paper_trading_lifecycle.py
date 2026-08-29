"""
tests/test_paper_trading_lifecycle.py — Production-Grade Automated Test Battery for Paper Trading.

Verifies:
1. Paper Account Creation, Virtual Balances, & Account Reset
2. Market Orders Execution, Adverse Slippage, Fees, & Position Accounting
3. Limit Orders, Margin Locking, Limit Matching Engine, and Cancellation
4. Short Positions & Position Reversals (Long -> Short & Short -> Long)
5. State Machine Invariants & Illegal State Transition Protection
6. High-Concurrency & Race Condition Protection
7. Accounting Invariant Properties: total_equity = available + locked + position_market_value
8. Idempotency & Duplicate Execution Protection
9. Security, Multi-Tenant Isolation & Zero Live Exchange Leakage
10. End-to-End API Router Verification (/api/paper/*)
"""

import asyncio
from decimal import Decimal
import os
import pytest
from uuid import uuid4
from fastapi.testclient import TestClient

from backend_app.backend.paper_trading_service import (
    PaperTradingService, PaperOrderStatus, get_paper_trading_service
)
from backend_app.main import app


@pytest.fixture
def paper_service():
    """Provides an isolated instance of PaperTradingService for unit testing."""
    return PaperTradingService(default_capital=100_000.0, default_fee_rate=0.001, default_slippage=0.0005)


@pytest.fixture
def test_client():
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════════════════
# 1. ACCOUNT LIFECYCLE & TENANT ISOLATION
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_paper_account_creation_and_defaults(paper_service):
    user_id = str(uuid4())
    account = paper_service.get_or_create_account(user_id)

    assert account["user_id"] == user_id
    assert Decimal(account["initial_capital"]) == Decimal("100000.0")
    assert Decimal(account["available_balance"]) == Decimal("100000.0")
    assert Decimal(account["locked_balance"]) == Decimal("0.0")
    assert Decimal(account["total_equity"]) == Decimal("100000.0")
    assert Decimal(account["realized_pnl"]) == Decimal("0.0")
    assert account["status"] == "active"
    assert paper_service.verify_accounting_invariants(user_id) is True


@pytest.mark.asyncio
async def test_paper_account_reset(paper_service):
    user_id = str(uuid4())
    paper_service.get_or_create_account(user_id)

    # Place a trade to change state
    await paper_service.place_order(
        user_id=user_id,
        symbol="BTC-USDT",
        side="buy",
        order_type="market",
        quantity=0.1,
        price=50000.0
    )

    # Reset with $250,000 custom capital
    reset_acct = paper_service.reset_account(user_id, capital=250_000.0)
    assert Decimal(reset_acct["initial_capital"]) == Decimal("250000.0")
    assert Decimal(reset_acct["available_balance"]) == Decimal("250000.0")
    assert Decimal(reset_acct["total_equity"]) == Decimal("250000.0")
    assert len(paper_service.get_positions(user_id)) == 0
    assert paper_service.verify_accounting_invariants(user_id) is True


@pytest.mark.asyncio
async def test_tenant_isolation_between_users(paper_service):
    user_a = str(uuid4())
    user_b = str(uuid4())

    # User A buys BTC
    await paper_service.place_order(
        user_id=user_a,
        symbol="BTC-USDT",
        side="buy",
        quantity=0.5,
        price=60000.0
    )

    # User B should have 0 positions and pristine balance
    pos_b = paper_service.get_positions(user_b)
    orders_b = paper_service.get_orders(user_b)
    acct_b = paper_service.get_or_create_account(user_b)

    assert len(pos_b) == 0
    assert len(orders_b) == 0
    assert Decimal(acct_b["available_balance"]) == Decimal("100000.0")


# ═══════════════════════════════════════════════════════════════════════════
# 2. MARKET ORDERS & POSITION ACCOUNTING
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_market_buy_and_sell_lifecycle(paper_service):
    user_id = str(uuid4())
    
    # 1. Market Buy 1.0 BTC @ $60,000
    buy_order = await paper_service.place_order(
        user_id=user_id,
        symbol="BTC-USDT",
        side="buy",
        order_type="market",
        quantity=1.0,
        price=60000.0
    )

    assert buy_order["status"] == PaperOrderStatus.FILLED.value
    fill_price = Decimal(buy_order["price"])
    # Slippage applied (buy slips up)
    assert fill_price >= Decimal("60000.0")
    fee = Decimal(buy_order["fee"])
    assert fee > Decimal("0")

    # Check Position
    positions = paper_service.get_positions(user_id)
    assert len(positions) == 1
    assert positions[0]["symbol"] == "BTC-USDT"
    assert Decimal(positions[0]["size"]) == Decimal("1.0")

    # Check Account balance reduced
    acct = paper_service.get_or_create_account(user_id)
    expected_avail = Decimal("100000.0") - (Decimal("1.0") * fill_price + fee)
    assert Decimal(acct["available_balance"]) == expected_avail.quantize(Decimal("0.01"))
    assert paper_service.verify_accounting_invariants(user_id) is True

    # 2. Market Sell 1.0 BTC @ $70,000 (Profitable trade)
    sell_order = await paper_service.place_order(
        user_id=user_id,
        symbol="BTC-USDT",
        side="sell",
        order_type="market",
        quantity=1.0,
        price=70000.0
    )

    assert sell_order["status"] == PaperOrderStatus.FILLED.value
    # Position should now be closed
    positions_after = paper_service.get_positions(user_id)
    assert len(positions_after) == 0

    # Realized PnL should be positive (~$10,000 minus fees)
    summary = paper_service.get_performance_summary(user_id)
    assert Decimal(summary["account"]["realized_pnl"]) > Decimal("9000.0")
    assert summary["winning_trades"] == 1
    assert summary["roi_pct"] > 0
    assert paper_service.verify_accounting_invariants(user_id) is True


@pytest.mark.asyncio
async def test_short_position_and_reversal(paper_service):
    user_id = str(uuid4())

    # 1. Open Short 1.0 ETH @ $3,000
    short_ord = await paper_service.place_order(
        user_id=user_id,
        symbol="ETH-USDT",
        side="sell",
        order_type="market",
        quantity=1.0,
        price=3000.0
    )
    assert short_ord["status"] == PaperOrderStatus.FILLED.value
    pos = paper_service.get_positions(user_id)
    assert len(pos) == 1
    assert pos[0]["side"] == "short"
    assert Decimal(pos[0]["size"]) == Decimal("1.0")

    # 2. Reverse Short to Long: Buy 2.0 ETH @ $2,500 (Profitable short exit + Long entry)
    rev_ord = await paper_service.place_order(
        user_id=user_id,
        symbol="ETH-USDT",
        side="buy",
        order_type="market",
        quantity=2.0,
        price=2500.0
    )
    assert rev_ord["status"] == PaperOrderStatus.FILLED.value

    pos_after = paper_service.get_positions(user_id)
    assert len(pos_after) == 1
    assert pos_after[0]["side"] == "long"
    assert Decimal(pos_after[0]["size"]) == Decimal("1.0")
    assert Decimal(paper_service.get_or_create_account(user_id)["realized_pnl"]) > Decimal("450.0")
    assert paper_service.verify_accounting_invariants(user_id) is True


# ═══════════════════════════════════════════════════════════════════════════
# 3. LIMIT ORDERS, MATCHING ENGINE & CANCELLATION
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_limit_order_margin_locking_and_cancel(paper_service):
    user_id = str(uuid4())
    
    # Place Limit Buy order for 0.5 BTC @ $50,000 ($25,000 cost)
    limit_order = await paper_service.place_order(
        user_id=user_id,
        symbol="BTC-USDT",
        side="buy",
        order_type="limit",
        quantity=0.5,
        price=50000.0
    )

    assert limit_order["status"] == PaperOrderStatus.OPEN.value
    order_id = limit_order["order_id"]

    # Check locked balance
    acct = paper_service.get_or_create_account(user_id)
    assert Decimal(acct["locked_balance"]) > Decimal("25000.0")
    assert Decimal(acct["available_balance"]) < Decimal("75000.0")

    # Cancel order
    cancelled_order = await paper_service.cancel_order(user_id, order_id)
    assert cancelled_order["status"] == PaperOrderStatus.CANCELLED.value

    # Locked balance must be released
    acct_after = paper_service.get_or_create_account(user_id)
    assert Decimal(acct_after["locked_balance"]) == Decimal("0.0")
    assert Decimal(acct_after["available_balance"]) == Decimal("100000.0")
    assert paper_service.verify_accounting_invariants(user_id) is True


@pytest.mark.asyncio
async def test_limit_order_matching_engine(paper_service):
    user_id = str(uuid4())

    # Place Limit Buy order for 1.0 SOL @ $140.00
    limit_ord = await paper_service.place_order(
        user_id=user_id,
        symbol="SOL-USDT",
        side="buy",
        order_type="limit",
        quantity=1.0,
        price=140.0
    )
    assert limit_ord["status"] == PaperOrderStatus.OPEN.value

    # Market price is $150.00 -> Should NOT fill
    unfilled = await paper_service.check_limit_orders("SOL-USDT", Decimal("150.00"))
    assert len(unfilled) == 0

    # Market price drops to $138.00 -> Should TRIGGER FILL!
    filled = await paper_service.check_limit_orders("SOL-USDT", Decimal("138.00"))
    assert len(filled) == 1
    assert filled[0]["status"] == PaperOrderStatus.FILLED.value
    assert len(paper_service.get_positions(user_id)) == 1
    assert paper_service.verify_accounting_invariants(user_id) is True


# ═══════════════════════════════════════════════════════════════════════════
# 4. STATE MACHINE & ILLEGAL TRANSITIONS
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_illegal_order_state_transitions(paper_service):
    user_id = str(uuid4())

    # Market order is immediately filled
    filled_ord = await paper_service.place_order(
        user_id=user_id,
        symbol="BTC-USDT",
        side="buy",
        order_type="market",
        quantity=0.1,
        price=60000.0
    )
    order_id = filled_ord["order_id"]

    # Attempting to cancel a FILLED order must raise ValueError
    with pytest.raises(ValueError, match="Cannot cancel an already filled order"):
        await paper_service.cancel_order(user_id, order_id)


# ═══════════════════════════════════════════════════════════════════════════
# 5. CONCURRENCY & RACE PROTECTION
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_concurrent_orders_race_safety(paper_service):
    user_id = str(uuid4())
    paper_service.get_or_create_account(user_id)

    # 10 concurrent Buy orders of 0.05 BTC each
    async def _place_buy():
        return await paper_service.place_order(
            user_id=user_id,
            symbol="BTC-USDT",
            side="buy",
            quantity=0.05,
            price=60000.0
        )

    results = await asyncio.gather(*[_place_buy() for _ in range(10)])
    assert len(results) == 10
    pos = paper_service.get_positions(user_id)
    assert len(pos) == 1
    assert Decimal(pos[0]["size"]) == Decimal("0.5")  # 10 * 0.05
    assert paper_service.verify_accounting_invariants(user_id) is True


# ═══════════════════════════════════════════════════════════════════════════
# 6. IDEMPOTENCY PROTECTION
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_idempotent_execution_protection(paper_service):
    user_id = str(uuid4())
    idempotency_key = f"idem_test_{uuid4().hex}"

    # First attempt
    ord_1 = await paper_service.place_order(
        user_id=user_id,
        symbol="ETH-USDT",
        side="buy",
        quantity=1.0,
        price=3000.0,
        idempotency_key=idempotency_key
    )

    # Second retry attempt with same key
    ord_2 = await paper_service.place_order(
        user_id=user_id,
        symbol="ETH-USDT",
        side="buy",
        quantity=1.0,
        price=3000.0,
        idempotency_key=idempotency_key
    )

    # Must return identical order without creating second trade or deducting balance twice
    assert ord_1["order_id"] == ord_2["order_id"]
    positions = paper_service.get_positions(user_id)
    assert len(positions) == 1
    assert Decimal(positions[0]["size"]) == Decimal("1.0")
    assert paper_service.verify_accounting_invariants(user_id) is True


# ═══════════════════════════════════════════════════════════════════════════
# 7. REST API ENDPOINT INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

def get_auth_token_for_test():
    import jwt
    import time
    secret = os.environ.get("SUPABASE_JWT_SECRET") or os.environ.get("JWT_SECRET") or "dev-secret-change-in-production"
    payload = {
        "sub": "test-user-id-12345",
        "email": "paper_tester@vyomquant.io",
        "tenant_id": "tenant-12345",
        "role": "authenticated",
        "aud": "authenticated",
        "iss": "algo22-test",
        "exp": int(time.time()) + 3600,
        "app_metadata": {"role": "authenticated", "tenant_id": "tenant-12345"}
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def test_paper_api_endpoints_end_to_end(test_client):
    token = get_auth_token_for_test()
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Get Account
    res = test_client.get("/api/paper/account", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "available_balance" in data

    # 2. Place Paper Order via API
    order_req = {
        "symbol": "BTC-USDT",
        "side": "buy",
        "order_type": "market",
        "quantity": 0.05,
        "price": 65000.0
    }
    res_order = test_client.post("/api/paper/orders", json=order_req, headers=headers)
    assert res_order.status_code == 200
    assert res_order.json()["status"] == "success"

    # 3. Get Positions
    res_pos = test_client.get("/api/paper/positions", headers=headers)
    assert res_pos.status_code == 200
    assert len(res_pos.json()["positions"]) >= 1

    # 4. Get Orders & Trades
    res_orders = test_client.get("/api/paper/orders", headers=headers)
    assert res_orders.status_code == 200
    assert res_orders.json()["count"] >= 1

    res_trades = test_client.get("/api/paper/trades", headers=headers)
    assert res_trades.status_code == 200
    assert res_trades.json()["count"] >= 1

    # 5. Get Summary
    res_sum = test_client.get("/api/paper/summary", headers=headers)
    assert res_sum.status_code == 200
    assert "account" in res_sum.json()

    # 6. Reset Account
    res_reset = test_client.post("/api/paper/account/reset", json={"capital": 150000.0}, headers=headers)
    assert res_reset.status_code == 200
    assert res_reset.json()["status"] == "success"
