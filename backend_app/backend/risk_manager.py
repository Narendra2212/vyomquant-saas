"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — BACKEND: risk_manager.py                                      ║
║                                                                          ║
║  Institutional-grade circuit breaker for all trade requests.             ║
║  Pure stateful computation — zero HTTP/WS code.                          ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  RM-1  Race condition: dry-run check + state update not atomic           ║
║  RM-2  is_reduce_only = (side=='sell') is wrong for short-open orders    ║
║  RM-3  No asyncio.Lock protecting the deques under high concurrency      ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from enum import Enum
from typing import Optional

logger = logging.getLogger("RiskManager")


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


class InstitutionalRiskManager:
    """
    Thread-safe, async-safe multi-tenant risk engine.
    One shared instance for ALL bots on the server (injected via global state).
    """

    def __init__(self):
        # ── Global system limits ────────────────────────────────────────
        self.global_max_notional_per_trade = 100_000  # $100k hard cap
        self.max_global_orders_per_second = 45  # below Binance's 50/s

        # ── Per-user limits ─────────────────────────────────────────────
        self.max_user_orders_per_second = 5
        self.duplicate_order_cooldown = 2.0  # seconds

        # ── State (protected by asyncio.Lock) ───────────────────────────
        self._global_timestamps = deque()
        self._user_timestamps = defaultdict(deque)
        self._last_trade_sig = defaultdict(float)

        # FIX RM-1 + RM-3: Single lock makes check+commit atomic
        self._lock = asyncio.Lock()

        # ── Subscription tier limits ────────────────────────────────────
        self.tier_limits = {
            "free": {"max_trade": 1_000, "max_exposure": 5_000},
            "starter": {"max_trade": 5_000, "max_exposure": 25_000},
            "pro": {"max_trade": 25_000, "max_exposure": 100_000},
            "whale": {"max_trade": 250_000, "max_exposure": 1_000_000},
            "institutional": {"max_trade": float("inf"), "max_exposure": float("inf")},
        }

        # ── Centralized Capital Allocator (Priority 2) ──────────────────
        self.total_capital = 100_000.0
        self.strategy_allocations = {}  # strategy_id -> allocated_capital

        # ── Correlation Clusters (Priority 3) ───────────────────────────
        self.correlation_clusters = {
            "BTC": "crypto_major",
            "ETH": "crypto_major",
            "SOL": "crypto_alt",
        }
        self.cluster_limit = 50_000.0  # Max exposure per cluster

        # ── Extended Loss Limits ────────────────────────────────────────
        self.weekly_loss_limit = 0.10  # 10%
        self.monthly_loss_limit = 0.20  # 20%

    def set_total_capital(self, amount: float):
        """Set the total available account capital."""
        self.total_capital = amount

    def allocate_capital(self, strategy_id: str, amount: float) -> bool:
        """
        Allocate capital to a strategy.
        Returns False if the new total allocation exceeds total_capital.
        """
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

    async def validate_trade_request(
        self,
        user_id: str,
        user_tier: str,
        symbol: str,
        side: str,
        amount: float,
        current_price: float,
        current_exposure: float,
        current_drawdown_pct: float,
        daily_pnl_pct: float,
        is_reduce_only: bool = False,  # FIX RM-2: explicit flag, not inferred from side
        strategy_id: Optional[str] = None,
        strategy_exposure: float = 0.0,
        weekly_pnl_pct: float = 0.0,
        monthly_pnl_pct: float = 0.0,
        leverage: float = 1.0,
        cluster_exposures: Optional[dict] = None,
        recent_prices: Optional[list] = None,
    ) -> tuple[RiskVerdict, str]:
        """
        The master circuit breaker.
        FIX RM-1: The entire check+commit sequence is atomic under self._lock.
        FIX RM-2: is_reduce_only is passed explicitly by BotRunner — a sell
                  can mean 'open short' (not reduce-only) or 'close long' (reduce-only).
        """
        notional = amount * current_price
        signature = f"{user_id}_{symbol}_{side}"
        now = time.time()

        async with self._lock:  # FIX RM-1+RM-3: atomic block
            # ── 1. Duplicate spam guard ───────────────────────────────────
            last_time = self._last_trade_sig.get(signature, 0.0)
            if now - last_time < self.duplicate_order_cooldown:
                return (
                    RiskVerdict.REJECT_DUPLICATE_SPAM,
                    f"Duplicate {side} signal for {symbol} suppressed (cooldown).",
                )

            # ── 2. Rate limits ────────────────────────────────────────────
            cutoff = now - 1.0

            while self._global_timestamps and self._global_timestamps[0] < cutoff:
                self._global_timestamps.popleft()
            while (
                self._user_timestamps[user_id]
                and self._user_timestamps[user_id][0] < cutoff
            ):
                self._user_timestamps[user_id].popleft()

            if len(self._global_timestamps) >= self.max_global_orders_per_second:
                return (
                    RiskVerdict.REJECT_GLOBAL_RATE_LIMIT,
                    "Global order rate limit reached.",
                )

            if len(self._user_timestamps[user_id]) >= self.max_user_orders_per_second:
                return (
                    RiskVerdict.REJECT_USER_RATE_LIMIT,
                    f"User {user_id} rate limit reached.",
                )

            # ── 3. Flash-crash Anomaly Filtering (Priority 5) ─────────────
            if recent_prices:
                sma_val = sum(recent_prices) / len(recent_prices)
                if sma_val > 0 and abs(current_price - sma_val) / sma_val > 0.10:
                    return (
                        RiskVerdict.REJECT_FLASH_CRASH,
                        f"Flash crash anomaly detected: price {current_price} deviates >10% from SMA {sma_val:.2f}.",
                    )

            # ── 4. Account health & Drawdown / loss limits (Priority 4 / daily/weekly/monthly limits) ──
            if not is_reduce_only:
                if current_drawdown_pct > 0.15:
                    return (
                        RiskVerdict.REJECT_DRAWDOWN,
                        "Account drawdown >15%. All new position opens are locked.",
                    )
                if daily_pnl_pct < -0.05:
                    return (
                        RiskVerdict.REJECT_DAILY_LOSS,
                        "Daily loss limit (5%) triggered. New positions halted for today.",
                    )
                if weekly_pnl_pct < -0.10:
                    return (
                        RiskVerdict.REJECT_WEEKLY_LOSS,
                        "Weekly loss limit (10%) triggered. New positions halted.",
                    )
                if monthly_pnl_pct < -0.20:
                    return (
                        RiskVerdict.REJECT_MONTHLY_LOSS,
                        "Monthly loss limit (20%) triggered. New positions halted.",
                    )

            # ── 5. Leverage scaling based on drawdown (Priority 4) ────────
            if not is_reduce_only:
                if current_drawdown_pct >= 0.15:
                    max_allowed_lev = 1.0
                elif current_drawdown_pct >= 0.10:
                    max_allowed_lev = 2.0
                elif current_drawdown_pct >= 0.05:
                    max_allowed_lev = 5.0
                else:
                    max_allowed_lev = 10.0
                
                if leverage > max_allowed_lev:
                    return (
                        RiskVerdict.REJECT_LEVERAGE_SCALING,
                        f"Leverage {leverage}x exceeds scaled limit of {max_allowed_lev}x based on drawdown {current_drawdown_pct:.2%}.",
                    )

            # ── 6. Fat-finger / notional cap & Max Position Size check ────
            if notional > 0.10 * self.total_capital:
                return (
                    RiskVerdict.REJECT_MAX_POSITION_SIZE,
                    f"Position size ${notional:,.2f} exceeds 10% of total capital limit (${0.10 * self.total_capital:,.2f}).",
                )

            if notional > self.global_max_notional_per_trade:
                return (
                    RiskVerdict.REJECT_MAX_NOTIONAL,
                    f"Trade value ${notional:,.2f} exceeds global hard cap of ${self.global_max_notional_per_trade:,}.",
                )

            # ── 7. Tier & Portfolio Exposure limits ────────────────────────
            tier = self.tier_limits.get(user_tier, self.tier_limits["free"])

            if notional > tier["max_trade"]:
                return (
                    RiskVerdict.REJECT_TIER_LIMIT,
                    f"Trade value ${notional:,.2f} exceeds {user_tier} tier limit of ${tier['max_trade']:,}.",
                )

            # Exposure check only applies when OPENING/INCREASING a position
            if not is_reduce_only:
                if (current_exposure + notional) > tier["max_exposure"]:
                    return (
                        RiskVerdict.REJECT_OVER_EXPOSURE,
                        f"Opening this position would exceed {user_tier} tier max exposure of ${tier['max_exposure']:,}.",
                    )

            # ── 8. Centralized Capital Allocator (Priority 2) ──────────────
            if not is_reduce_only and strategy_id is not None:
                if strategy_id not in self.strategy_allocations:
                    return (
                        RiskVerdict.REJECT_CAPITAL_ALLOCATOR_LIMIT,
                        f"Strategy {strategy_id} is not allocated capital in the centralized allocator.",
                    )
                allocated = self.strategy_allocations[strategy_id]
                if (strategy_exposure + notional) > allocated:
                    return (
                        RiskVerdict.REJECT_CAPITAL_ALLOCATOR_LIMIT,
                        f"Strategy {strategy_id} exposure (${strategy_exposure + notional:,.2f}) would exceed allocated capital of ${allocated:,.2f}.",
                    )

            # ── 9. Correlation-Cluster Exposure controls (Priority 3) ──────
            if not is_reduce_only and symbol:
                base_asset = symbol.split("/")[0].split("-")[0].upper()
                cluster = self.correlation_clusters.get(base_asset)
                if cluster and cluster_exposures:
                    curr_cluster_exp = cluster_exposures.get(cluster, 0.0)
                    if (curr_cluster_exp + notional) > self.cluster_limit:
                        return (
                            RiskVerdict.REJECT_CORRELATION_CLUSTER,
                            f"Correlation cluster {cluster} exposure limit reached (Limit: ${self.cluster_limit:,.2f}).",
                        )

            # ── 10. Strategy Exposure limit ───────────────────────────────
            if not is_reduce_only:
                max_strat_limit = 0.30 * self.total_capital
                if (strategy_exposure + notional) > max_strat_limit:
                    return (
                        RiskVerdict.REJECT_MAX_STRATEGY_EXPOSURE,
                        f"Strategy exposure (${strategy_exposure + notional:,.2f}) exceeds 30% total capital limit (${max_strat_limit:,.2f}).",
                    )

            # ── 11. PASS — commit to state now that all checks passed ─────
            self._last_trade_sig[signature] = now
            self._global_timestamps.append(now)
            self._user_timestamps[user_id].append(now)

        return RiskVerdict.PASS, "Trade approved by Institutional Risk Engine."

    def get_diagnostics(self) -> dict:
        """Admin endpoint — returns current rate-limit state."""
        return {
            "global_orders_last_second": len(self._global_timestamps),
            "global_capacity": self.max_global_orders_per_second,
            "active_user_queues": len(self._user_timestamps),
        }
