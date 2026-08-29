"""
tests/test_dashboard_phase1_5_adversarial.py — Phase 1.5 Production Adversarial Data-Contract Test Suite.

Adversarial Stress Testing:
1. Realized P&L UTC Midnight Boundary (Yesterday vs Today vs Negative Trades)
2. Unrealized P&L Long vs Short vs Futures Mark-to-Market
3. Zero vs Unknown Financial State Distinction
4. Bidirectional Live vs Paper Isolation Attack
5. Multi-Exchange Identity Preservation (Binance + Bybit + Kraken)
6. Tenant Cross-Contamination Attack (User A vs User B)
7. Cache Key Environment Partitioning
8. Disconnected / Timeout Graceful Degradation
"""

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService
from backend_app.backend.paper_trading_service import get_paper_trading_service


@pytest.fixture
def user_a():
    return {
        "id": "usr_alpha_tenant_111",
        "email": "alpha@vyomquant.com",
        "access_token": "jwt_alpha_token"
    }


@pytest.fixture
def user_b():
    return {
        "id": "usr_beta_tenant_222",
        "email": "beta@vyomquant.com",
        "access_token": "jwt_beta_token"
    }


@pytest.fixture
def dashboard_service():
    return DashboardAggregationService()


# ═══════════════════════════════════════════════════════════════════════════
# 1. REALIZED P&L FORENSIC TEST (UTC MIDNIGHT BOUNDARIES)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_realized_pnl_forensic_utc_boundary(user_a, dashboard_service):
    """
    Stress test UTC midnight boundary for today's realized P&L.
    Must include:
    - Trade executed yesterday at 23:59:59 UTC (+ $1000) -> EXCLUDED from today_realized_pnl
    - Trade executed today at 00:00:01 UTC (+ $450) -> INCLUDED
    - Trade executed today at 04:30:00 UTC (- $200) -> INCLUDED
    - Open position with unrealized profit (+ $300) -> NOT in today_realized_pnl, only in unrealized_pnl
    """
    paper_svc = get_paper_trading_service()
    paper_svc.reset_account(user_a["id"], capital=100000.0)
    
    now_utc = datetime.now(timezone.utc)
    today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    
    yesterday_2359 = (today_start_utc - timedelta(seconds=1)).isoformat()
    today_0001 = (today_start_utc + timedelta(seconds=1)).isoformat()
    today_0430 = (today_start_utc + timedelta(hours=4, minutes=30)).isoformat()
    
    paper_svc._trades[user_a["id"]] = [
        {
            "execution_id": "exec_yest_win",
            "order_id": "ord_1",
            "symbol": "BTC/USDT",
            "side": "sell",
            "quantity": "1.0",
            "price": "65000.0",
            "fee": "5.0",
            "realized_pnl": "1000.00",
            "executed_at": yesterday_2359
        },
        {
            "execution_id": "exec_today_win",
            "order_id": "ord_2",
            "symbol": "ETH/USDT",
            "side": "sell",
            "quantity": "2.0",
            "price": "3600.0",
            "fee": "2.0",
            "realized_pnl": "450.00",
            "executed_at": today_0001
        },
        {
            "execution_id": "exec_today_loss",
            "order_id": "ord_3",
            "symbol": "SOL/USDT",
            "side": "sell",
            "quantity": "10.0",
            "price": "140.0",
            "fee": "1.0",
            "realized_pnl": "-200.00",
            "executed_at": today_0430
        }
    ]
    
    # Open position with +$300 unrealized profit
    paper_svc._positions[user_a["id"]] = {
        "BTC/USDT": {
            "symbol": "BTC/USDT",
            "side": "long",
            "size": "0.1",
            "entry_price": "60000.0",
            "current_price": "63000.0",
            "unrealized_pnl": "300.00",
            "updated_at": now_utc.isoformat()
        }
    }
    
    acct = paper_svc.get_or_create_account(user_a["id"])
    acct["available_balance"] = "101250.00"  # 100000 + 1000 + 450 - 200
    acct["realized_pnl"] = "1250.00"
    
    overview = await dashboard_service.get_portfolio_overview(user_a, environment="paper")
    
    # Today's realized = +450 - 200 = +250.00 (Excludes yesterday's 1000.00)
    assert overview["today_realized_pnl"] == 250.00
    # Unrealized = +300.00
    assert overview["unrealized_pnl"] == 300.00
    # Today's Total PnL = today_realized + unrealized = 250 + 300 = 550.00
    assert overview["today_pnl"] == 550.00
    # Cumulative PnL = lifetime realized + unrealized = 1250 + 300 = 1550.00
    assert overview["cumulative_pnl"] == 1550.00


# ═══════════════════════════════════════════════════════════════════════════
# 2. UNREALIZED P&L MULTI-ASSET & MULTI-EXCHANGE FORENSIC TEST
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_unrealized_pnl_multi_venue_and_direction(user_a, dashboard_service):
    """
    Verify live unrealized P&L sums correctly across:
    - Binance Long BTC: +$500.00
    - Bybit Short ETH: +$150.00
    - Kraken Long SOL: -$80.00
    Total Live uPnL = 500 + 150 - 80 = $570.00
    """
    binance_positions = json.dumps({
        "BTC/USDT": {"symbol": "BTC/USDT", "side": "long", "contracts": "0.5", "entry_price": "60000.0", "mark_price": "61000.0", "unrealized_pnl": "500.0"}
    })
    bybit_positions = json.dumps({
        "ETH/USDT:USDT": {"symbol": "ETH/USDT:USDT", "side": "short", "contracts": "1.0", "entry_price": "3650.0", "mark_price": "3500.0", "unrealized_pnl": "150.0"}
    })
    kraken_positions = json.dumps({
        "SOL/USD": {"symbol": "SOL/USD", "side": "long", "contracts": "2.0", "entry_price": "160.0", "mark_price": "120.0", "unrealized_pnl": "-80.0"}
    })

    with patch("backend_app.core.cache.redis_manager.redis_manager.keys", new_callable=AsyncMock) as mock_keys, \
         patch("backend_app.core.cache.redis_manager.redis_manager.get", new_callable=AsyncMock) as mock_get, \
         patch("backend_app.backend.dashboard_aggregation_service.DashboardAggregationService._get_telemetry") as mock_tel:
        
        mock_keys.return_value = [
            f"portfolio:{user_a['id']}:binance:positions",
            f"portfolio:{user_a['id']}:bybit:positions",
            f"portfolio:{user_a['id']}:kraken:positions"
        ]
        
        def mock_pos_lookup(k):
            if "binance" in k:
                return binance_positions
            if "bybit" in k:
                return bybit_positions
            if "kraken" in k:
                return kraken_positions
            return None
        
        mock_get.side_effect = mock_pos_lookup
        mock_tel.return_value.execute_query = AsyncMock(return_value={"dataset": [[50000.0, 1000.0, 2.0, 5000.0]], "columns": [{"name": "total_equity"}, {"name": "total_pnl"}, {"name": "pnl_pct"}, {"name": "total_exposure"}]})
        
        positions = await dashboard_service.get_open_positions(user_a, environment="live")
        
        assert len(positions) == 3
        # Ensure exchange_id is preserved on every single position object
        exchanges = {p["exchange_id"] for p in positions}
        assert exchanges == {"binance", "bybit", "kraken"}
        
        # Verify sum of unrealized PnL in portfolio overview
        overview = await dashboard_service.get_portfolio_overview(user_a, environment="live")
        assert overview["unrealized_pnl"] == 570.0  # 500 + 150 - 80


# ═══════════════════════════════════════════════════════════════════════════
# 3. BIDIRECTIONAL ENVIRONMENT ISOLATION ATTACK
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_live_never_reads_paper_and_paper_never_reads_live(user_a, dashboard_service):
    """
    Prove that Live requests never query PaperTradingService and Paper requests never query Live Redis/QuestDB.
    """
    # Setup paper state with distinct marker value $99999.0
    paper_svc = get_paper_trading_service()
    paper_svc.reset_account(user_a["id"], capital=99999.0)
    
    with patch("backend_app.backend.dashboard_aggregation_service.DashboardAggregationService._get_telemetry") as mock_tel, \
         patch("backend_app.core.cache.redis_manager.redis_manager.keys", new_callable=AsyncMock) as mock_keys:
        
        mock_keys.return_value = []
        mock_tel.return_value.execute_query = AsyncMock(return_value={"dataset": [[12345.0, 50.0, 0.4, 0.0]], "columns": [{"name": "total_equity"}, {"name": "total_pnl"}, {"name": "pnl_pct"}, {"name": "total_exposure"}]})
        
        # 1. LIVE REQUEST
        live_data = await dashboard_service.get_dashboard_data(user_a, environment="live")
        assert live_data["environment"] == "live"
        assert live_data["overview"]["total_equity"] == 12345.0  # Live QuestDB value, NOT paper $99999
        assert live_data["overview"]["currency"] == "USDT"
        
        # 2. PAPER REQUEST
        paper_data = await dashboard_service.get_dashboard_data(user_a, environment="paper")
        assert paper_data["environment"] == "paper"
        assert paper_data["overview"]["total_equity"] == 99999.0  # Paper value, NOT Live $12345
        assert paper_data["overview"]["currency"] == "USD"


# ═══════════════════════════════════════════════════════════════════════════
# 4. TENANT CROSS-CONTAMINATION ATTACK (USER A VS USER B)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_strict_tenant_isolation(user_a, user_b, dashboard_service):
    """
    Verify User A can never receive User B balances, positions, or executions.
    """
    paper_svc = get_paper_trading_service()
    paper_svc.reset_account(user_a["id"], capital=11111.0)
    paper_svc.reset_account(user_b["id"], capital=22222.0)
    
    paper_svc._positions[user_a["id"]] = {
        "BTC/USDT": {"symbol": "BTC/USDT", "side": "long", "size": "1.0", "entry_price": "60000.0", "current_price": "60000.0", "unrealized_pnl": "0.0"}
    }
    paper_svc._positions[user_b["id"]] = {
        "ETH/USDT": {"symbol": "ETH/USDT", "side": "long", "size": "5.0", "entry_price": "3000.0", "current_price": "3000.0", "unrealized_pnl": "0.0"}
    }
    
    data_a = await dashboard_service.get_dashboard_data(user_a, environment="paper")
    data_b = await dashboard_service.get_dashboard_data(user_b, environment="paper")
    
    # User A: $11,111 cash + $60,000 BTC position = $71,111.0
    assert data_a["overview"]["total_equity"] == 71111.0
    # User B: $22,222 cash + $15,000 ETH position = $37,222.0
    assert data_b["overview"]["total_equity"] == 37222.0
    
    assert len(data_a["positions"]) == 1
    assert data_a["positions"][0]["symbol"] == "BTC/USDT"
    
    assert len(data_b["positions"]) == 1
    assert data_b["positions"][0]["symbol"] == "ETH/USDT"


# ═══════════════════════════════════════════════════════════════════════════
# 5. ZERO VS UNKNOWN DATA & LATENCY AUDIT
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_unmeasured_latency_is_strictly_null(user_a, dashboard_service):
    """
    Verify unmeasured latency is None (null in JSON), never static 38ms or 0ms.
    """
    with patch("backend_app.core.cache.redis_manager.redis_manager.keys", new_callable=AsyncMock) as mock_keys:
        mock_keys.return_value = []
        health = await dashboard_service.get_health_status(user_a, environment="live")
        
        assert health["exchange_api_latency_ms"] is None
        assert health["exchange_api_latency_status"] == "unavailable"
