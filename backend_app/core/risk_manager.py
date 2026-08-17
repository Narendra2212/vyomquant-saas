"""
backend_app/core/risk_manager.py

Consolidated Institutional Risk Manager.
Combines institutional circuit-breaker checks, multi-tenant rate limits,
capital allocations, correlation cluster controls, and paper-trading risk status helpers.

SECURITY: Service role key usage is strictly controlled with audit logging and validation.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import date
import logging
import time
import os
from collections import defaultdict, deque
from enum import Enum
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("RiskManager")


def _validate_service_role_security() -> None:
    """
    SECURITY: Validate that service role key is properly secured.
    
    Raises RuntimeError if service role key security requirements are not met.
    """
    env = os.environ.get("ENV", "development").lower()
    if env == "production":
        service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not service_role_key:
            raise RuntimeError(
                "CRITICAL: SUPABASE_SERVICE_ROLE_KEY not configured in production. "
                "Risk manager requires service role access for backend operations."
            )
        
        # Validate service role key format
        if not service_role_key.startswith("eyJ"):
            logger.warning("Service role key may not be properly formatted")
        
        # Log service role key usage for audit
        logger.info("Service role key validated for risk manager operations")


def _log_service_role_access(user_id: str, operation: str) -> None:
    """
    SECURITY: Audit log all service role key usage.
    
    Args:
        user_id: User ID being accessed
        operation: Operation being performed
    """
    logger.info(
        f"AUDIT: Service role key access - user_id={user_id}, operation={operation}, "
        f"timestamp={time.time()}"
    )


def _validate_user_id_isolation(user_id: str) -> None:
    """
    SECURITY: Validate user_id format to prevent injection attacks.
    
    Args:
        user_id: User ID to validate
    
    Raises ValueError if user_id is invalid.
    """
    if not user_id or not isinstance(user_id, str):
        raise ValueError("Invalid user_id: must be non-empty string")
    
    # Check for potential injection patterns
    dangerous_patterns = ["'", ";", "--", "/*", "*/", "xp_", "sp_"]
    for pattern in dangerous_patterns:
        if pattern in user_id:
            raise ValueError(f"Invalid user_id: contains dangerous pattern '{pattern}'")
    
    # UUID format validation (Supabase uses UUIDs)
    import re
    uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    if not re.match(uuid_pattern, user_id.lower()):
        logger.warning(f"User ID {user_id} does not match UUID format")


def _load_user_risk_settings(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Load user-specific risk settings from Supabase.
    
    SECURITY NOTE: Uses SERVICE_ROLE_KEY to bypass RLS for backend-only access.
    This is intentional - the risk engine needs to read user settings during trade validation
    without requiring the user's session token. Access is still restricted by user_id filtering.
    
    SECURITY ENHANCEMENTS:
    - Service role key validation
    - Audit logging for all access
    - User ID injection validation
    - Strict user_id filtering
    """
    try:
        # SECURITY: Validate service role key before use
        _validate_service_role_security()
        
        # SECURITY: Validate user_id format
        _validate_user_id_isolation(user_id)
        
        # SECURITY: Audit log service role access
        _log_service_role_access(user_id, "load_user_risk_settings")
        
        from supabase import create_client
        
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        
        if not supabase_url or not supabase_key:
            logger.warning("Supabase credentials not configured, using default risk thresholds")
            return None
            
        sb = create_client(supabase_url, supabase_key)
        
        # SECURITY: Strict user_id filtering to prevent cross-tenant access
        resp = sb.table("risk_settings").select("*").eq("user_id", user_id).execute()
        
        if resp.data:
            settings = resp.data[0]
            # SECURITY: Verify returned data belongs to requested user
            if settings.get("user_id") != user_id:
                logger.critical(
                    f"SECURITY VIOLATION: User ID mismatch in risk settings response. "
                    f"Requested: {user_id}, Received: {settings.get('user_id')}"
                )
                raise RuntimeError("Security violation: User ID mismatch in risk settings")
            
            return {
                "max_daily_loss": settings.get("max_daily_loss", 500.0),
                "max_positions": settings.get("max_positions", 10),
                "max_leverage": settings.get("max_leverage", 3),
            }
        return None
    except Exception as e:
        logger.error(f"Failed to load user risk settings for {user_id}: {e}")
        return None


@dataclass
class TradeRequest:
    """
    Structured input payload for trade risk validation, replacing 18 individual parameters.
    """
    user_id: str
    user_tier: str
    symbol: str
    side: str
    amount: float
    current_price: float
    current_exposure: float
    current_drawdown_pct: float
    daily_pnl_pct: float
    is_reduce_only: bool = False
    strategy_id: Optional[str] = None
    strategy_exposure: float = 0.0
    weekly_pnl_pct: float = 0.0
    monthly_pnl_pct: float = 0.0
    leverage: float = 1.0
    cluster_exposures: Optional[dict] = None
    recent_prices: Optional[list] = None
    open_trades_count: int = 0


@dataclass
class RiskThresholds:
    """
    Structured configuration object containing all risk limits and policy thresholds
    for InstitutionalRiskManager.
    """
    global_max_notional_per_trade: float = 100_000.0
    max_global_orders_per_second: int = 45
    max_user_orders_per_second: int = 5
    duplicate_order_cooldown: float = 2.0
    max_drawdown: float = 0.15
    max_daily_loss_pct: float = 0.05
    weekly_loss_limit: float = 0.10
    monthly_loss_limit: float = 0.20
    max_position_size_pct: float = 0.10
    max_open_trades: int = 5
    max_risk_per_trade: float = 0.02
    cluster_limit: float = 50_000.0

    # Extracted from inline magic numbers in validate_trade_request:
    flash_crash_price_deviation_pct: float = 0.10
    drawdown_tier_1_pct: float = 0.15
    drawdown_tier_1_max_leverage: float = 1.0
    drawdown_tier_2_pct: float = 0.10
    drawdown_tier_2_max_leverage: float = 2.0
    drawdown_tier_3_pct: float = 0.05
    drawdown_tier_3_max_leverage: float = 5.0
    base_max_leverage: float = 10.0
    max_strategy_exposure_pct: float = 0.30


class RiskVerdict(Enum):
    PASS = "PASS"
    APPROVED = "PASS"  # alias for master_executor compatibility
    REJECT_GLOBAL_RATE_LIMIT = "REJECT_GLOBAL_RATE_LIMIT"
    REJECT_USER_RATE_LIMIT = "REJECT_USER_RATE_LIMIT"
    REJECT_MAX_NOTIONAL = "REJECT_MAX_NOTIONAL"
    REJECT_TIER_LIMIT = "REJECT_TIER_LIMIT"
    REJECT_DRAWDOWN = "REJECT_DRAWDOWN"
    REJECT_DAILY_LOSS = "REJECT_DAILY_LOSS"
    REJECT_WEEKLY_LOSS = "REJECT_WEEKLY_LOSS"
    REJECT_MONTHLY_LOSS = "REJECT_MONTHLY_LOSS"
    REJECT_OVER_EXPOSURE = "REJECT_OVER_EXPOSURE"
    REJECT_DUPLICATE_SPAM = "REJECT_DUPLICATE_SPAM"
    REJECT_MAX_POSITION_SIZE = "REJECT_MAX_POSITION_SIZE"
    REJECT_MAX_STRATEGY_EXPOSURE = "REJECT_MAX_STRATEGY_EXPOSURE"
    REJECT_CORRELATION_CLUSTER = "REJECT_CORRELATION_CLUSTER"
    REJECT_CAPITAL_ALLOCATOR_LIMIT = "REJECT_CAPITAL_ALLOCATOR_LIMIT"
    REJECT_LEVERAGE_SCALING = "REJECT_LEVERAGE_SCALING"
    REJECT_FLASH_CRASH = "REJECT_FLASH_CRASH"
    REJECT_MAX_OPEN_TRADES = "REJECT_MAX_OPEN_TRADES"


class InstitutionalRiskManager:
    """
    Consolidated, thread-safe, async-safe risk engine for live and paper trading.
    """

    def __init__(
        self,
        initial_equity: float = 100000.0,
        thresholds: Optional[RiskThresholds] = None,
    ):
        self.thresholds = thresholds or RiskThresholds()

        # State (protected by asyncio.Lock)
        self._global_timestamps = deque()
        self._user_timestamps = defaultdict(deque)
        self._last_trade_sig = defaultdict(float)
        self._lock = asyncio.Lock()

        # Subscription tier limits
        self.tier_limits = {
            "free": {"max_trade": 1_000, "max_exposure": 5_000},
            "starter": {"max_trade": 10_000, "max_exposure": 25_000},
            "starter_499": {"max_trade": 10_000, "max_exposure": 25_000},
            "basic": {"max_trade": 10_000, "max_exposure": 25_000},
            "pro": {"max_trade": 50_000, "max_exposure": 200_000},
            "pro_999": {"max_trade": 50_000, "max_exposure": 200_000},
            "professional": {"max_trade": 50_000, "max_exposure": 200_000},
            "enterprise": {"max_trade": 500_000, "max_exposure": 2_000_000},
            "elite": {"max_trade": 500_000, "max_exposure": 2_000_000},
            "elite_1999": {"max_trade": 500_000, "max_exposure": 2_000_000},
        }

        # Centralized Capital Allocator
        self.total_capital = float(initial_equity)
        self.strategy_allocations: Dict[str, float] = {}

        # Correlation Clusters
        self.correlation_clusters = {
            "BTC": "crypto_major",
            "ETH": "crypto_major",
            "SOL": "crypto_alt",
        }

        # Equity tracking
        self.initial_equity = float(initial_equity)
        self.current_equity = float(initial_equity)
        self.peak_equity = float(initial_equity)

        # Daily tracking
        self._current_date = date.today()
        self._daily_pnl = 0.0
        self._daily_starting_equity = float(initial_equity)

        # Open trade tracking
        self._open_trades_count = 0

    @property
    def global_max_notional_per_trade(self) -> float:
        return self.thresholds.global_max_notional_per_trade

    @global_max_notional_per_trade.setter
    def global_max_notional_per_trade(self, val: float):
        self.thresholds.global_max_notional_per_trade = val

    @property
    def max_global_orders_per_second(self) -> int:
        return self.thresholds.max_global_orders_per_second

    @max_global_orders_per_second.setter
    def max_global_orders_per_second(self, val: int):
        self.thresholds.max_global_orders_per_second = val

    @property
    def max_user_orders_per_second(self) -> int:
        return self.thresholds.max_user_orders_per_second

    @max_user_orders_per_second.setter
    def max_user_orders_per_second(self, val: int):
        self.thresholds.max_user_orders_per_second = val

    @property
    def duplicate_order_cooldown(self) -> float:
        return self.thresholds.duplicate_order_cooldown

    @duplicate_order_cooldown.setter
    def duplicate_order_cooldown(self, val: float):
        self.thresholds.duplicate_order_cooldown = val

    @property
    def max_drawdown(self) -> float:
        return self.thresholds.max_drawdown

    @max_drawdown.setter
    def max_drawdown(self, val: float):
        self.thresholds.max_drawdown = val

    @property
    def max_daily_loss_pct(self) -> float:
        return self.thresholds.max_daily_loss_pct

    @max_daily_loss_pct.setter
    def max_daily_loss_pct(self, val: float):
        self.thresholds.max_daily_loss_pct = val

    @property
    def weekly_loss_limit(self) -> float:
        return self.thresholds.weekly_loss_limit

    @weekly_loss_limit.setter
    def weekly_loss_limit(self, val: float):
        self.thresholds.weekly_loss_limit = val

    @property
    def monthly_loss_limit(self) -> float:
        return self.thresholds.monthly_loss_limit

    @monthly_loss_limit.setter
    def monthly_loss_limit(self, val: float):
        self.thresholds.monthly_loss_limit = val

    @property
    def max_position_size_pct(self) -> float:
        return self.thresholds.max_position_size_pct

    @max_position_size_pct.setter
    def max_position_size_pct(self, val: float):
        self.thresholds.max_position_size_pct = val

    @property
    def max_open_trades(self) -> int:
        return self.thresholds.max_open_trades

    @max_open_trades.setter
    def max_open_trades(self, val: int):
        self.thresholds.max_open_trades = val

    @property
    def max_risk_per_trade(self) -> float:
        return self.thresholds.max_risk_per_trade

    @max_risk_per_trade.setter
    def max_risk_per_trade(self, val: float):
        self.thresholds.max_risk_per_trade = val

    @property
    def cluster_limit(self) -> float:
        return self.thresholds.cluster_limit

    @cluster_limit.setter
    def cluster_limit(self, val: float):
        self.thresholds.cluster_limit = val

    def set_total_capital(self, amount: float):
        """Set total available account capital."""
        self.total_capital = float(amount)
        self.current_equity = float(amount)

    def allocate_capital(self, strategy_id: str, amount: float) -> bool:
        """Allocate capital to a strategy."""
        current_total_allocated = sum(self.strategy_allocations.values())
        existing = self.strategy_allocations.get(strategy_id, 0.0)
        new_total = current_total_allocated - existing + amount
        if new_total > self.total_capital:
            return False
        self.strategy_allocations[strategy_id] = amount
        return True

    def deallocate_capital(self, strategy_id: str):
        """Deallocate capital from a strategy."""
        self.strategy_allocations.pop(strategy_id, None)

    def position_size(self, price: float) -> float:
        """Calculate position size based on 2% risk per trade."""
        if price <= 0:
            return 0.0
        return (self.current_equity * self.max_risk_per_trade) / price

    def update_equity(self, pnl: float):
        """Update equity with PnL and track daily performance."""
        self.current_equity += pnl
        self._daily_pnl += pnl
        self.peak_equity = max(self.peak_equity, self.current_equity)

    def drawdown(self) -> float:
        """Calculate current drawdown from peak equity."""
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity

    def _reset_daily_if_needed(self):
        """Reset daily counters if date changed."""
        today = date.today()
        if today != self._current_date:
            self._current_date = today
            self._daily_pnl = 0.0
            self._daily_starting_equity = self.current_equity

    def daily_loss_pct(self) -> float:
        """Calculate today's loss percentage against starting equity."""
        self._reset_daily_if_needed()
        if self._daily_starting_equity <= 0:
            return 0.0
        daily_loss = -min(0.0, self._daily_pnl)
        return daily_loss / self._daily_starting_equity

    def can_trade(self) -> Tuple[bool, str]:
        """Check if trading is allowed based on drawdown and daily loss limits."""
        self._reset_daily_if_needed()
        if self.drawdown() > self.max_drawdown:
            return False, "🛑 MAX DRAWDOWN HIT - Trading blocked"
        if self.daily_loss_pct() > self.max_daily_loss_pct:
            return False, f"🛑 MAX DAILY LOSS HIT ({self.daily_loss_pct()*100:.2f}%) - Trading blocked"
        return True, "✅ Risk checks passed"

    def can_open_position(
        self,
        position_value: float,
        open_trades_count: int = 0,
    ) -> Tuple[bool, str]:
        """Check if proposed position can be opened."""
        self._reset_daily_if_needed()
        max_position_value = self.current_equity * self.max_position_size_pct
        if position_value > max_position_value:
            return False, (
                f"🛡️ MAX POSITION SIZE EXCEEDED | Position: ${position_value:.2f} | "
                f"Max allowed: ${max_position_value:.2f} (10% of ${self.current_equity:.2f})"
            )
        allowed, msg = self.can_trade()
        if not allowed:
            return False, msg
        effective_open = open_trades_count or self._open_trades_count
        if effective_open >= self.max_open_trades:
            return False, (
                f"🛡️ MAX OPEN TRADES EXCEEDED | Current: {effective_open} | "
                f"Max allowed: {self.max_open_trades}"
            )
        return True, "✅ All guardrails passed - Trade allowed"

    def record_trade_open(self):
        """Record trade open."""
        self._open_trades_count += 1

    def record_trade_close(self):
        """Record trade close."""
        self._open_trades_count = max(0, self._open_trades_count - 1)

    def update_open_trades_count(self, count: int):
        """Update open trade count."""
        self._open_trades_count = count

    def get_risk_status(self) -> dict:
        """Get current risk status summary."""
        self._reset_daily_if_needed()
        return {
            "current_equity": self.current_equity,
            "peak_equity": self.peak_equity,
            "drawdown_pct": self.drawdown() * 100,
            "daily_pnl": self._daily_pnl,
            "daily_loss_pct": self.daily_loss_pct() * 100,
            "max_daily_loss_pct": self.max_daily_loss_pct * 100,
            "open_trades": self._open_trades_count,
            "max_open_trades": self.max_open_trades,
            "position_limit_pct": self.max_position_size_pct * 100,
        }

    async def validate_trade_request(
        self,
        request: TradeRequest,
    ) -> Tuple[RiskVerdict, str]:
        """Master circuit breaker for trade validation using structured TradeRequest and RiskThresholds."""
        # Load user-specific risk settings
        user_settings = _load_user_risk_settings(request.user_id)
        if user_settings:
            # Override thresholds with user-specific settings
            effective_max_daily_loss = user_settings["max_daily_loss"] / self.total_capital if self.total_capital > 0 else self.thresholds.max_daily_loss_pct
            effective_max_positions = user_settings["max_positions"]
            effective_max_leverage = user_settings["max_leverage"]
        else:
            # Use default thresholds
            effective_max_daily_loss = self.thresholds.max_daily_loss_pct
            effective_max_positions = self.thresholds.max_open_trades
            effective_max_leverage = self.thresholds.base_max_leverage

        notional = request.amount * request.current_price
        signature = f"{request.user_id}_{request.symbol}_{request.side}"
        now = time.time()

        async with self._lock:
            # 1. Duplicate spam guard
            last_time = self._last_trade_sig.get(signature, 0.0)
            if now - last_time < self.thresholds.duplicate_order_cooldown:
                return (
                    RiskVerdict.REJECT_DUPLICATE_SPAM,
                    f"Duplicate {request.side} signal for {request.symbol} suppressed (cooldown).",
                )

            # 2. Rate limits
            cutoff = now - 1.0
            while self._global_timestamps and self._global_timestamps[0] < cutoff:
                self._global_timestamps.popleft()
            while (
                self._user_timestamps[request.user_id]
                and self._user_timestamps[request.user_id][0] < cutoff
            ):
                self._user_timestamps[request.user_id].popleft()

            if len(self._global_timestamps) >= self.thresholds.max_global_orders_per_second:
                return (
                    RiskVerdict.REJECT_GLOBAL_RATE_LIMIT,
                    "Global order rate limit reached.",
                )
            if len(self._user_timestamps[request.user_id]) >= self.thresholds.max_user_orders_per_second:
                return (
                    RiskVerdict.REJECT_USER_RATE_LIMIT,
                    f"User {request.user_id} rate limit reached.",
                )

            # 3. Flash crash anomaly
            if request.recent_prices:
                sma_val = sum(request.recent_prices) / len(request.recent_prices)
                if sma_val > 0 and abs(request.current_price - sma_val) / sma_val > self.thresholds.flash_crash_price_deviation_pct:
                    return (
                        RiskVerdict.REJECT_FLASH_CRASH,
                        f"Flash crash anomaly detected: price {request.current_price} deviates >{self.thresholds.flash_crash_price_deviation_pct*100:.0f}% from SMA {sma_val:.2f}.",
                    )

            # 4. Account health & drawdown/loss limits
            if not request.is_reduce_only:
                if request.current_drawdown_pct > self.thresholds.max_drawdown:
                    return (
                        RiskVerdict.REJECT_DRAWDOWN,
                        f"Account drawdown >{self.thresholds.max_drawdown*100:.0f}%. All new position opens are locked.",
                    )
                if request.daily_pnl_pct < -effective_max_daily_loss:
                    return (
                        RiskVerdict.REJECT_DAILY_LOSS,
                        f"Daily loss limit ({effective_max_daily_loss*100:.0f}%) triggered. New positions halted for today.",
                    )
                if request.weekly_pnl_pct < -self.thresholds.weekly_loss_limit:
                    return (
                        RiskVerdict.REJECT_WEEKLY_LOSS,
                        f"Weekly loss limit ({self.thresholds.weekly_loss_limit*100:.0f}%) triggered. New positions halted.",
                    )
                if request.monthly_pnl_pct < -self.thresholds.monthly_loss_limit:
                    return (
                        RiskVerdict.REJECT_MONTHLY_LOSS,
                        f"Monthly loss limit ({self.thresholds.monthly_loss_limit*100:.0f}%) triggered. New positions halted.",
                    )
                effective_open = request.open_trades_count or self._open_trades_count
                if effective_open >= effective_max_positions:
                    return (
                        RiskVerdict.REJECT_MAX_OPEN_TRADES,
                        f"Max concurrent open trades limit ({effective_max_positions}) reached.",
                    )

            # 5. Leverage scaling
            if not request.is_reduce_only:
                if request.current_drawdown_pct >= self.thresholds.drawdown_tier_1_pct:
                    max_allowed_lev = min(self.thresholds.drawdown_tier_1_max_leverage, effective_max_leverage)
                elif request.current_drawdown_pct >= self.thresholds.drawdown_tier_2_pct:
                    max_allowed_lev = min(self.thresholds.drawdown_tier_2_max_leverage, effective_max_leverage)
                elif request.current_drawdown_pct >= self.thresholds.drawdown_tier_3_pct:
                    max_allowed_lev = min(self.thresholds.drawdown_tier_3_max_leverage, effective_max_leverage)
                else:
                    max_allowed_lev = effective_max_leverage

                if request.leverage > max_allowed_lev:
                    return (
                        RiskVerdict.REJECT_LEVERAGE_SCALING,
                        f"Leverage {request.leverage}x exceeds scaled limit of {max_allowed_lev}x based on drawdown {request.current_drawdown_pct:.2%}.",
                    )

            # 6. Notional cap & Max Position Size check
            if notional > self.thresholds.max_position_size_pct * self.total_capital:
                return (
                    RiskVerdict.REJECT_MAX_POSITION_SIZE,
                    f"Position size ${notional:,.2f} exceeds {self.thresholds.max_position_size_pct*100:.0f}% of total capital limit (${self.thresholds.max_position_size_pct * self.total_capital:,.2f}).",
                )

            if notional > self.thresholds.global_max_notional_per_trade:
                return (
                    RiskVerdict.REJECT_MAX_NOTIONAL,
                    f"Trade value ${notional:,.2f} exceeds global hard cap of ${self.thresholds.global_max_notional_per_trade:,}.",
                )

            # 7. Tier limits
            tier_key = str(request.user_tier or "free").lower().strip()
            tier = self.tier_limits.get(tier_key, self.tier_limits["free"])
            if notional > tier["max_trade"]:
                return (
                    RiskVerdict.REJECT_TIER_LIMIT,
                    f"Trade value ${notional:,.2f} exceeds {request.user_tier} tier limit of ${tier['max_trade']:,}.",
                )

            if not request.is_reduce_only:
                if (request.current_exposure + notional) > tier["max_exposure"]:
                    return (
                        RiskVerdict.REJECT_OVER_EXPOSURE,
                        f"Opening this position would exceed {request.user_tier} tier max exposure of ${tier['max_exposure']:,}.",
                    )

            # 8. Capital Allocator
            if not request.is_reduce_only and request.strategy_id is not None:
                if request.strategy_id not in self.strategy_allocations:
                    return (
                        RiskVerdict.REJECT_CAPITAL_ALLOCATOR_LIMIT,
                        f"Strategy {request.strategy_id} is not allocated capital in the centralized allocator.",
                    )
                allocated = self.strategy_allocations[request.strategy_id]
                if (request.strategy_exposure + notional) > allocated:
                    return (
                        RiskVerdict.REJECT_CAPITAL_ALLOCATOR_LIMIT,
                        f"Strategy {request.strategy_id} exposure (${request.strategy_exposure + notional:,.2f}) would exceed allocated capital of ${allocated:,.2f}.",
                    )

            # 9. Correlation Cluster
            if not request.is_reduce_only and request.symbol:
                base_asset = request.symbol.split("/")[0].split("-")[0].upper()
                cluster = self.correlation_clusters.get(base_asset)
                if cluster and request.cluster_exposures:
                    curr_cluster_exp = request.cluster_exposures.get(cluster, 0.0)
                    if (curr_cluster_exp + notional) > self.thresholds.cluster_limit:
                        return (
                            RiskVerdict.REJECT_CORRELATION_CLUSTER,
                            f"Correlation cluster {cluster} exposure limit reached (Limit: ${self.thresholds.cluster_limit:,.2f}).",
                        )

            # 10. Strategy Exposure limit
            if not request.is_reduce_only:
                max_strat_limit = self.thresholds.max_strategy_exposure_pct * self.total_capital
                if (request.strategy_exposure + notional) > max_strat_limit:
                    return (
                        RiskVerdict.REJECT_MAX_STRATEGY_EXPOSURE,
                        f"Strategy exposure (${request.strategy_exposure + notional:,.2f}) exceeds {self.thresholds.max_strategy_exposure_pct * 100:.0f}% total capital limit (${max_strat_limit:,.2f}).",
                    )

            # 11. PASS
            self._last_trade_sig[signature] = now
            self._global_timestamps.append(now)
            self._user_timestamps[request.user_id].append(now)

        return RiskVerdict.PASS, "Trade approved by Institutional Risk Engine."

    def get_diagnostics(self) -> dict:
        """Admin endpoint - returns current rate-limit state."""
        return {
            "global_orders_last_second": len(self._global_timestamps),
            "global_capacity": self.max_global_orders_per_second,
            "active_user_queues": len(self._user_timestamps),
        }


# Class aliases for zero-breakage consolidation
RiskManager = InstitutionalRiskManager
