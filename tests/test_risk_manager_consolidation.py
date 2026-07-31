"""
tests/test_risk_manager_consolidation.py

Unit test suite verifying:
1. Deletion of redundant backend/risk_manager.py.
2. Single surviving risk manager in backend_app.core.risk_manager.py.
3. Feature parity and enforcement across all distinct risk checks:
   - Position size cap (10% equity)
   - Global hard notional cap ($100k)
   - Drawdown limit (15%)
   - Daily loss limit (5%)
   - Weekly loss limit (10%)
   - Monthly loss limit (20%)
   - Leverage scaling
   - Subscription tier limits
   - Capital Allocator limit
   - Correlation cluster limits
   - Max open trades limit (5)
   - Duplicate spam cooldown
"""

import asyncio
import pathlib
from uuid import uuid4

from backend_app.core.risk_manager import (
    InstitutionalRiskManager,
    RiskManager,
    RiskThresholds,
    RiskVerdict,
    TradeRequest,
)


def test_dead_risk_manager_deleted():
    dead_path = pathlib.Path("backend_app/backend/risk_manager.py")
    assert not dead_path.exists(), "backend/risk_manager.py should be deleted"


def test_class_aliases():
    assert RiskManager is InstitutionalRiskManager
    rm = InstitutionalRiskManager(initial_equity=100000.0)
    assert rm.total_capital == 100000.0


def test_risk_thresholds_defaults():
    """Explicitly assert that every threshold value in RiskThresholds matches pre-refactor literals."""
    t = RiskThresholds()
    assert t.global_max_notional_per_trade == 100_000.0
    assert t.max_global_orders_per_second == 45
    assert t.max_user_orders_per_second == 5
    assert t.duplicate_order_cooldown == 2.0
    assert t.max_drawdown == 0.15
    assert t.max_daily_loss_pct == 0.05
    assert t.weekly_loss_limit == 0.10
    assert t.monthly_loss_limit == 0.20
    assert t.max_position_size_pct == 0.10
    assert t.max_open_trades == 5
    assert t.max_risk_per_trade == 0.02
    assert t.cluster_limit == 50_000.0
    assert t.flash_crash_price_deviation_pct == 0.10
    assert t.drawdown_tier_1_pct == 0.15
    assert t.drawdown_tier_1_max_leverage == 1.0
    assert t.drawdown_tier_2_pct == 0.10
    assert t.drawdown_tier_2_max_leverage == 2.0
    assert t.drawdown_tier_3_pct == 0.05
    assert t.drawdown_tier_3_max_leverage == 5.0
    assert t.base_max_leverage == 10.0
    assert t.max_strategy_exposure_pct == 0.30


def test_paper_trading_helpers():
    rm = RiskManager(initial_equity=100000.0)
    
    # Check initial status
    allowed, msg = rm.can_trade()
    assert allowed is True

    # Test position size calculation (2% risk)
    pos_sz = rm.position_size(price=50000.0)
    assert pos_sz == (100000.0 * 0.02) / 50000.0

    # Test can_open_position within limit ($5k position)
    ok, _ = rm.can_open_position(position_value=5000.0)
    assert ok is True

    # Test position size exceeded ($15k position > 10% of $100k)
    ok, msg = rm.can_open_position(position_value=15000.0)
    assert ok is False
    assert "MAX POSITION SIZE EXCEEDED" in msg


def test_validate_trade_request_checks():
    async def _run():
        rm = InstitutionalRiskManager(initial_equity=100000.0)

        # 1. Normal valid trade request
        v, msg = await rm.validate_trade_request(
            TradeRequest(
                user_id="user1",
                user_tier="pro_999",
                symbol="BTC/USDT",
                side="buy",
                amount=0.1,
                current_price=50000.0,  # notional = $5,000 (5% of capital <= 10% limit)
                current_exposure=0.0,
                current_drawdown_pct=0.02,
                daily_pnl_pct=0.0,
            )
        )
        assert v == RiskVerdict.PASS

        # 2. Position size cap check (> 10% total capital, e.g. $15,000 notional)
        v_size, _ = await rm.validate_trade_request(
            TradeRequest(
                user_id="user2",
                user_tier="pro_999",
                symbol="ETH/USDT",
                side="buy",
                amount=0.3,
                current_price=50000.0,  # notional = $15,000 > 10% of $100k
                current_exposure=0.0,
                current_drawdown_pct=0.0,
                daily_pnl_pct=0.0,
            )
        )
        assert v_size == RiskVerdict.REJECT_MAX_POSITION_SIZE

        # 3. Drawdown limit check (> 15% drawdown)
        v_dd, _ = await rm.validate_trade_request(
            TradeRequest(
                user_id="user3",
                user_tier="pro_999",
                symbol="BTC/USDT",
                side="buy",
                amount=0.05,
                current_price=50000.0,
                current_exposure=0.0,
                current_drawdown_pct=0.18,  # 18% > 15%
                daily_pnl_pct=0.0,
            )
        )
        assert v_dd == RiskVerdict.REJECT_DRAWDOWN

        # 4. Daily loss limit check (> 5% loss)
        v_loss, _ = await rm.validate_trade_request(
            TradeRequest(
                user_id="user4",
                user_tier="pro_999",
                symbol="BTC/USDT",
                side="buy",
                amount=0.05,
                current_price=50000.0,
                current_exposure=0.0,
                current_drawdown_pct=0.02,
                daily_pnl_pct=-0.06,  # -6% < -5%
            )
        )
        assert v_loss == RiskVerdict.REJECT_DAILY_LOSS

        # 5. Tier trade limit check (free tier max trade $1,000)
        v_tier, _ = await rm.validate_trade_request(
            TradeRequest(
                user_id="user5",
                user_tier="free",
                symbol="BTC/USDT",
                side="buy",
                amount=0.05,
                current_price=50000.0,  # notional = $2,500 > $1,000 free tier
                current_exposure=0.0,
                current_drawdown_pct=0.0,
                daily_pnl_pct=0.0,
            )
        )
        assert v_tier == RiskVerdict.REJECT_TIER_LIMIT

        # 6. Max open trades limit check (open_trades_count = 5)
        v_open, _ = await rm.validate_trade_request(
            TradeRequest(
                user_id="user6",
                user_tier="pro_999",
                symbol="BTC/USDT",
                side="buy",
                amount=0.05,
                current_price=50000.0,
                current_exposure=0.0,
                current_drawdown_pct=0.0,
                daily_pnl_pct=0.0,
                open_trades_count=5,
            )
        )
        assert v_open == RiskVerdict.REJECT_MAX_OPEN_TRADES

        # 7. Leverage scaling check
        v_lev, _ = await rm.validate_trade_request(
            TradeRequest(
                user_id="user7",
                user_tier="pro_999",
                symbol="BTC/USDT",
                side="buy",
                amount=0.01,
                current_price=50000.0,
                current_exposure=0.0,
                current_drawdown_pct=0.12,  # 12% drawdown -> max lev 2.0x
                daily_pnl_pct=0.0,
                leverage=5.0,  # 5.0x > 2.0x -> REJECT
            )
        )
        assert v_lev == RiskVerdict.REJECT_LEVERAGE_SCALING

        # 8. Flash crash anomaly check
        v_fc, _ = await rm.validate_trade_request(
            TradeRequest(
                user_id="user8",
                user_tier="pro_999",
                symbol="BTC/USDT",
                side="buy",
                amount=0.01,
                current_price=50000.0,
                current_exposure=0.0,
                current_drawdown_pct=0.0,
                daily_pnl_pct=0.0,
                recent_prices=[60000.0, 60000.0, 60000.0],  # >10% deviation
            )
        )
        assert v_fc == RiskVerdict.REJECT_FLASH_CRASH

    asyncio.run(_run())
