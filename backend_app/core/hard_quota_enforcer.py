"""
core/hard_quota_enforcer.py — DEPRECATED. DO NOT WIRE. See QUOTA-01.

THIS MODULE IS NOT THE PRODUCT'S QUOTA ENFORCEMENT AND MUST NOT BECOME IT
========================================================================
Quotas ARE enforced, before the side effect, by ``core/subscription_dependencies.py``,
carried as FastAPI ``Depends(...)`` on the mutation routes. This class is a parallel,
never-invoked second implementation of overlapping limits. It has zero call sites and is
kept only because several of the DIMENSIONS it names (orders per minute, daily trades,
active orders, aggregate capital, concentration) have no counterpart anywhere else, so
deleting the file would also delete the written record of those gaps.

The audit finding QUOTA-01 originally read "15 HardQuotaEnforcer methods with zero real
call sites", and the natural inference — that quotas are unenforced — was wrong. The
prescribed remediation, wiring this class, would have been a DEFECT. Three reasons, the
third decisive:

1. ITS LIMITS COME FROM A DIFFERENT CATALOGUE. ``tenant.quota`` is ``TenantQuota``
   (``core/tenant.py``), keyed by a separate plan ladder (``TenantPlan``: free/basic/
   professional/enterprise) from the subscription catalogue the wired path reads
   (``SubscriptionEngine._PLANS``, keyed by ``Plan``). Two sources of truth for one
   number means a user could be refused by one and admitted by the other depending on
   which ran first.

2. ITS TENANT MODEL IS THE VESTIGIAL ONE. ``core/dependencies.py`` collapses
   ``tenant_id`` to ``user_id``, which is the model finding TENANT-01 documents.

3. ITS USAGE COUNTING FAILS OPEN. This is the one that settles it, and it is NOT
   visible from the ``except`` blocks in this file — read alone, ``_get_current_count``
   and ``_increment_count`` look fail-CLOSED, because each raises in production when
   Redis misbehaves. Those branches never execute. The facade swallows first:
   ``core/cache/redis_manager.py``'s ``get()`` catches every exception, logs
   "Cache GET failed" and returns ``None``; its ``incr()`` catches and returns ``0``. No
   exception ever reaches this module. So during a Redis outage every count here reads
   ZERO and every limit ADMITS.

   The wired path does the opposite: an unreadable usage figure raises ``UsageReadFailed``
   and becomes a 503 (``subscription_dependencies.py``, "a figure that could not be read
   is not a figure of zero"). Wiring this class would therefore have installed a
   FAIL-OPEN quota path alongside a working fail-closed one — strictly worse than the
   gap it was supposed to close.

CONSEQUENCES OF THAT DECISION, IN CODE
--------------------------------------
* Instantiating this class emits a ``DeprecationWarning`` naming this docstring, so a
  future attempt to wire it surfaces at the first test run rather than in an audit.
* The two live services that constructed it and never used it
  (``core/dag_task_queue.py``, ``core/entitlement_engine.py``) no longer do.
* ``tests/test_quota01_enforcement_is_wired.py`` asserts the call-site list stays empty
  and that the fail-open mechanism above is still what the code does.

IF THESE LIMITS ARE WANTED, THIS IS NOT THE WAY TO GET THEM
-----------------------------------------------------------
The order-rate, daily-trade and active-order ceilings are real gaps and matter the moment
live trading is certified. Closing them means adding the resource to the subscription
catalogue and enforcing it on the wired, fail-closed path — not reviving this file. See
``staging/QUOTA_ENFORCEMENT_MAP.md`` §4 and
``staging/PHASE10_WSC_QUOTA_CONNECTION_SAFETY.md``.

Original description, retained for reference:
  Enforces quotas inside DAG engine, execution engine, and portfolio manager.
  Ensures limits are respected even if middleware is bypassed.
"""

import asyncio
import logging
import os
import warnings
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from backend_app.core.cache import redis_manager
from backend_app.core.quota_errors import (CapitalQuotaExceededError,
                                           ConcurrentOperationQuotaError,
                                           DAGQuotaExceededError,
                                           DailyLimitExceededError,
                                           ExecutionQuotaExceededError,
                                           PortfolioQuotaExceededError,
                                           QuotaViolationDetails,
                                           RateLimitExceededError,
                                           StorageQuotaExceededError)
from backend_app.core.tenant import TenantContext, TenantKeyBuilder

logger = logging.getLogger("HardQuotaEnforcer")


def _is_production() -> bool:
    """Check if running in production environment."""
    return os.getenv("ENV", "development").lower() == "production"


@dataclass
class EnforcementContext:
    """Context for quota enforcement operation."""
    tenant: TenantContext
    operation: str
    request_id: Optional[str] = None
    
    def to_log_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant.user_id,
            "operation": self.operation,
            "request_id": self.request_id,
            "timestamp": datetime.utcnow().isoformat(),
        }


#: The sentence the deprecation warning carries. Named so a test can assert on it without
#: duplicating the wording, and so the warning and the module docstring cannot drift.
DEPRECATION_MESSAGE = (
    "HardQuotaEnforcer is DEPRECATED and must not be wired (finding QUOTA-01). Quotas are "
    "enforced before the side effect by core/subscription_dependencies.py, which fails CLOSED "
    "on an unreadable usage figure. This class reads usage through the fail-SOFT redis_manager "
    "facade, so a Redis outage makes every count read zero and every limit admit; its limits "
    "also come from TenantQuota rather than the subscription catalogue. Wiring it would add a "
    "fail-open quota path beside a working fail-closed one. See the module docstring."
)


class HardQuotaEnforcer:
    """
    DEPRECATED — do not wire. See the module docstring and :data:`DEPRECATION_MESSAGE`.

    Retained as the written record of the limit DIMENSIONS this platform does not yet enforce
    (order rate, daily trades, active orders, aggregate capital, concentration, storage,
    websockets, DAG resources). Not retained as an enforcement mechanism: its usage counting
    fails open, which is why it is deprecated rather than wired.

    Original description, retained for reference:
      Hard quota enforcer for service-layer protection. This enforcer operates independently
      of middleware to ensure quotas are enforced even when middleware is bypassed, internal
      service calls are made, admin functions are invoked, or direct database access occurs.
      (The "even if middleware is bypassed" claim was never true in this repository: the class
      has never had a call site.)
    """

    def __init__(self):
        # `stacklevel=2` points the warning at whoever constructed this, which is the line that
        # would need removing. DeprecationWarning rather than a hard raise because the module
        # singleton below is built at import time, and an import-time exception in
        # `core/` would take the application down for a lint-level concern.
        warnings.warn(DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
        self._violation_count: Dict[str, int] = {}
        self._lock = asyncio.Lock()
    
    async def _get_current_count(self, key: str) -> int:
        """Get current count from Redis."""
        try:
            count = await redis_manager.get(key)
            return int(count) if count else 0
        except Exception as e:
            logger.error(f"Failed to get count for {key}: {e}")
            # FAIL-CLOSED: In production, Redis failure should prevent quota bypass
            if _is_production():
                raise RuntimeError(
                    f"CRITICAL: Redis unavailable during quota enforcement. "
                    f"Operation blocked to prevent quota bypass in production."
                )
            # In development, return 0 to allow operation
            return 0
    
    async def _increment_count(self, key: str, ttl: Optional[int] = None) -> int:
        """Increment counter and set TTL if new."""
        try:
            new_count = await redis_manager.incr(key)
            if new_count == 1 and ttl:
                await redis_manager.expire(key, ttl)
            return new_count
        except Exception as e:
            logger.error(f"Failed to increment count for {key}: {e}")
            # FAIL-CLOSED: In production, Redis failure should prevent quota bypass
            if _is_production():
                raise RuntimeError(
                    f"CRITICAL: Redis unavailable during quota enforcement. "
                    f"Operation blocked to prevent quota bypass in production."
                )
            # In development, return 0 to allow operation
            return 0
    
    async def _set_count(self, key: str, count: int, ttl: Optional[int] = None):
        """Set counter value."""
        try:
            await redis_manager.set(key, count)
            if ttl:
                await redis_manager.expire(key, ttl)
        except Exception as e:
            logger.error(f"Failed to set count for {key}: {e}")
    
    def _log_violation(
        self,
        error: Exception,
        ctx: EnforcementContext,
        details: Dict[str, Any]
    ):
        """Log quota violation with full context."""
        violation_data = {
            "type": "quota_violation",
            "error_type": error.__class__.__name__,
            "error_code": getattr(error, 'error_code', 'UNKNOWN'),
            "message": str(error),
            **ctx.to_log_dict(),
            "details": details,
            "severity": "WARNING",
        }
        
        # Log to application logger
        logger.warning(f"Quota violation: {violation_data}")
        
        # Log to dedicated audit channel
        try:
            audit_key = f"audit:quota_violations:{ctx.tenant.user_id}"
            async def _push_audit():
                try:
                    await redis_manager.lpush(audit_key, violation_data)
                    await redis_manager.expire(audit_key, 86400 * 30)
                except Exception as audit_err:
                    logger.error(f"Failed to log to audit: {audit_err}")
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_push_audit())
            except RuntimeError:
                pass
        except Exception as e:
            logger.error(f"Failed to log to audit: {e}")
        
        # Track violation count for alerting
        tenant_key = f"{ctx.tenant.user_id}:{error.__class__.__name__}"
        self._violation_count[tenant_key] = self._violation_count.get(tenant_key, 0) + 1
        
        # Alert on excessive violations
        if self._violation_count[tenant_key] >= 10:
            logger.error(
                f"Excessive quota violations for {ctx.tenant.user_id}: "
                f"{self._violation_count[tenant_key]} {error.__class__.__name__}"
            )
    
    def _create_violation_details(
        self,
        tenant: TenantContext,
        resource_type: str,
        limit: int,
        current: int,
        attempted: int,
        request_id: Optional[str] = None,
        context: Optional[Dict] = None
    ) -> QuotaViolationDetails:
        """Create standardized violation details."""
        return QuotaViolationDetails(
            tenant_id=tenant.user_id,
            resource_type=resource_type,
            limit=limit,
            current=current,
            attempted=attempted,
            plan=tenant.plan.value,
            timestamp=datetime.utcnow(),
            request_id=request_id,
            context=context,
        )
    
    # ═══════════════════════════════════════════════════════════════════════
    # DAG ENGINE QUOTA ENFORCEMENT
    # ═══════════════════════════════════════════════════════════════════════
    
    async def enforce_dag_session_limit(
        self,
        tenant: TenantContext,
        ctx: EnforcementContext
    ):
        """Enforce maximum DAG sessions per tenant."""
        key = TenantKeyBuilder.dag_sessions_set(tenant.user_id)
        current = await redis_manager.scard(key)
        limit = tenant.quota.max_dag_sessions
        
        if current >= limit:
            details = self._create_violation_details(
                tenant, "dag_sessions", limit, current, current + 1, ctx.request_id
            )
            error = DAGQuotaExceededError(details)
            self._log_violation(error, ctx, {
                "current_sessions": current,
                "max_allowed": limit,
            })
            raise error
        
        logger.debug(f"DAG session quota OK: {current}/{limit}")
    
    async def enforce_dag_node_limit(
        self,
        tenant: TenantContext,
        node_count: int,
        ctx: EnforcementContext
    ):
        """Enforce maximum DAG nodes per session."""
        limit = tenant.quota.max_dag_nodes
        
        if node_count > limit:
            details = self._create_violation_details(
                tenant, "dag_nodes", limit, 0, node_count, ctx.request_id,
                {"requested_nodes": node_count}
            )
            error = DAGQuotaExceededError(details)
            self._log_violation(error, ctx, {
                "requested_nodes": node_count,
                "max_allowed": limit,
            })
            raise error
        
        logger.debug(f"DAG node quota OK: {node_count}/{limit}")
    
    async def enforce_symbol_limit(
        self,
        tenant: TenantContext,
        symbol_count: int,
        ctx: EnforcementContext
    ):
        """Enforce maximum symbols per DAG."""
        limit = tenant.quota.max_symbols_per_dag
        
        if symbol_count > limit:
            details = self._create_violation_details(
                tenant, "symbols_per_dag", limit, 0, symbol_count, ctx.request_id,
                {"requested_symbols": symbol_count}
            )
            error = DAGQuotaExceededError(details)
            self._log_violation(error, ctx, {
                "requested_symbols": symbol_count,
                "max_allowed": limit,
            })
            raise error
        
        logger.debug(f"Symbol quota OK: {symbol_count}/{limit}")
    
    async def enforce_indicator_limit(
        self,
        tenant: TenantContext,
        indicator_count: int,
        ctx: EnforcementContext
    ):
        """Enforce maximum indicators per DAG."""
        limit = tenant.quota.max_indicators_per_dag
        
        if indicator_count > limit:
            details = self._create_violation_details(
                tenant, "indicators_per_dag", limit, 0, indicator_count, ctx.request_id,
                {"requested_indicators": indicator_count}
            )
            error = DAGQuotaExceededError(details)
            self._log_violation(error, ctx, {
                "requested_indicators": indicator_count,
                "max_allowed": limit,
            })
            raise error
        
        logger.debug(f"Indicator quota OK: {indicator_count}/{limit}")
    
    async def enforce_backtest_parallel_limit(
        self,
        tenant: TenantContext,
        ctx: EnforcementContext
    ):
        """Enforce maximum concurrent backtests."""
        key = f"user:{tenant.user_id}:backtests:active"
        current = await redis_manager.scard(key)
        limit = tenant.quota.max_backtest_parallel
        
        if current >= limit:
            details = self._create_violation_details(
                tenant, "concurrent_backtests", limit, current, current + 1, ctx.request_id
            )
            error = ConcurrentOperationQuotaError(details, "backtest")
            self._log_violation(error, ctx, {
                "active_backtests": current,
                "max_allowed": limit,
            })
            raise error
        
        logger.debug(f"Backtest parallel quota OK: {current}/{limit}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # EXECUTION ENGINE QUOTA ENFORCEMENT
    # ═══════════════════════════════════════════════════════════════════════
    
    async def enforce_order_rate_limit(
        self,
        tenant: TenantContext,
        ctx: EnforcementContext
    ):
        """Enforce orders per minute limit."""
        key = TenantKeyBuilder.rate_limit(tenant.user_id, "orders", "1m")
        current = await self._increment_count(key, 60)
        limit = tenant.quota.max_orders_per_minute
        
        if current > limit:
            # Get remaining TTL for retry-after header
            ttl = await redis_manager.ttl(key)
            retry_after = max(1, ttl) if ttl > 0 else 60
            
            details = self._create_violation_details(
                tenant, "orders_per_minute", limit, current, current, ctx.request_id
            )
            error = RateLimitExceededError(details, "1m", retry_after)
            self._log_violation(error, ctx, {
                "orders_this_minute": current,
                "limit": limit,
                "retry_after": retry_after,
            })
            raise error
        
        logger.debug(f"Order rate limit OK: {current}/{limit} per minute")
    
    async def enforce_daily_trade_limit(
        self,
        tenant: TenantContext,
        ctx: EnforcementContext
    ):
        """Enforce daily trade limit."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        key = f"user:{tenant.user_id}:daily_trades:{today}"
        current = await self._increment_count(key, 86400)  # 24h TTL
        limit = tenant.quota.max_daily_trades
        
        if current > limit:
            # Calculate reset time (midnight UTC)
            tomorrow = datetime.utcnow() + timedelta(days=1)
            reset_time = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
            
            details = self._create_violation_details(
                tenant, "daily_trades", limit, current, current, ctx.request_id
            )
            error = DailyLimitExceededError(details, "trades", reset_time)
            self._log_violation(error, ctx, {
                "trades_today": current,
                "limit": limit,
                "reset_time": reset_time.isoformat(),
            })
            raise error
        
        logger.debug(f"Daily trade limit OK: {current}/{limit}")
    
    async def enforce_active_order_limit(
        self,
        tenant: TenantContext,
        ctx: EnforcementContext
    ):
        """Enforce maximum active orders."""
        key = TenantKeyBuilder.execution_orders_set(tenant.user_id)
        
        # Count only non-final orders
        active_count = 0
        order_ids = await redis_manager.smembers(key)
        for order_id in order_ids:
            order_key = TenantKeyBuilder.execution_order(tenant.user_id, order_id)
            status = await redis_manager.hget(order_key, "status")
            if status and status not in ["filled", "cancelled", "rejected", "expired"]:
                active_count += 1
        
        limit = tenant.quota.max_active_orders
        
        if active_count >= limit:
            details = self._create_violation_details(
                tenant, "active_orders", limit, active_count, active_count + 1, ctx.request_id
            )
            error = ExecutionQuotaExceededError(details)
            self._log_violation(error, ctx, {
                "active_orders": active_count,
                "max_allowed": limit,
            })
            raise error
        
        logger.debug(f"Active order limit OK: {active_count}/{limit}")
    
    async def track_order_creation(
        self,
        tenant: TenantContext,
        order_id: str,
        ctx: EnforcementContext
    ):
        """Track order creation for quota monitoring."""
        key = TenantKeyBuilder.execution_orders_set(tenant.user_id)
        await redis_manager.sadd(key, order_id)
        logger.debug(f"Tracked order {order_id} for tenant {tenant.user_id}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # PORTFOLIO MANAGER QUOTA ENFORCEMENT
    # ═══════════════════════════════════════════════════════════════════════
    
    async def enforce_position_limit(
        self,
        tenant: TenantContext,
        ctx: EnforcementContext
    ):
        """Enforce maximum positions per tenant."""
        key = TenantKeyBuilder.positions_set(tenant.user_id)
        current = await redis_manager.scard(key)
        limit = tenant.quota.max_positions
        
        if current >= limit:
            details = self._create_violation_details(
                tenant, "positions", limit, current, current + 1, ctx.request_id
            )
            error = PortfolioQuotaExceededError(details)
            self._log_violation(error, ctx, {
                "current_positions": current,
                "max_allowed": limit,
            })
            raise error
        
        logger.debug(f"Position quota OK: {current}/{limit}")
    
    async def enforce_capital_limit(
        self,
        tenant: TenantContext,
        allocated_capital: float,
        ctx: EnforcementContext
    ):
        """Enforce maximum capital allocation per tenant."""
        # Get currently allocated capital
        key = f"user:{tenant.user_id}:portfolio:total_allocated"
        current_allocated = float(await redis_manager.get(key) or 0)
        
        limit = tenant.quota.max_capital
        new_total = current_allocated + allocated_capital
        
        if new_total > limit:
            available = limit - current_allocated
            details = self._create_violation_details(
                tenant, "capital_allocation", limit, current_allocated,
                new_total, ctx.request_id,
                {"requested": allocated_capital, "available": available}
            )
            error = CapitalQuotaExceededError(details, allocated_capital, available)
            self._log_violation(error, ctx, {
                "requested_capital": allocated_capital,
                "currently_allocated": current_allocated,
                "plan_limit": limit,
                "available": available,
            })
            raise error
        
        logger.debug(f"Capital quota OK: ${new_total:,.2f}/${limit:,.2f}")
    
    async def update_allocated_capital(
        self,
        tenant: TenantContext,
        delta: float
    ):
        """Update allocated capital tracking atomically."""
        key = f"user:{tenant.user_id}:portfolio:total_allocated"
        try:
            new_total = float(await redis_manager.incrbyfloat(key, delta))
            if new_total < 0:
                await redis_manager.set(key, 0.0)
                new_total = 0.0
            logger.debug(f"Updated capital allocation for {tenant.user_id}: ${new_total:,.2f}")
        except Exception as e:
            logger.error(f"Failed to update capital allocation for {tenant.user_id}: {e}")
    
    async def enforce_leverage_limit(
        self,
        tenant: TenantContext,
        requested_leverage: float,
        ctx: EnforcementContext
    ):
        """Enforce maximum leverage per tenant."""
        limit = tenant.quota.max_leverage
        
        if requested_leverage > limit:
            details = self._create_violation_details(
                tenant, "leverage", limit, 0, requested_leverage, ctx.request_id,
                {"requested_leverage": requested_leverage}
            )
            error = PortfolioQuotaExceededError(details)
            self._log_violation(error, ctx, {
                "requested_leverage": requested_leverage,
                "max_allowed": limit,
                "plan": tenant.plan.value,
            })
            raise error
        
        logger.debug(f"Leverage limit OK: {requested_leverage}x/{limit}x")
    
    async def enforce_concentration_limit(
        self,
        tenant: TenantContext,
        symbol: str,
        position_value: float,
        portfolio_value: float,
        ctx: EnforcementContext
    ):
        """Enforce maximum symbol concentration."""
        if portfolio_value <= 0:
            return
        
        concentration = position_value / portfolio_value
        limit = tenant.quota.max_symbol_concentration
        
        if concentration > limit:
            details = self._create_violation_details(
                tenant, "symbol_concentration", limit, 0, concentration, ctx.request_id,
                {"symbol": symbol, "concentration": concentration}
            )
            error = PortfolioQuotaExceededError(details)
            self._log_violation(error, ctx, {
                "symbol": symbol,
                "concentration_pct": concentration * 100,
                "max_allowed_pct": limit * 100,
                "position_value": position_value,
                "portfolio_value": portfolio_value,
            })
            raise error
        
        logger.debug(f"Concentration limit OK for {symbol}: {concentration:.1%}/{limit:.1%}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # STORAGE & COMPUTE QUOTA ENFORCEMENT
    # ═══════════════════════════════════════════════════════════════════════
    
    async def enforce_storage_limit(
        self,
        tenant: TenantContext,
        data_size_mb: float,
        ctx: EnforcementContext
    ):
        """Enforce Redis storage limit per tenant."""
        # Estimate current storage
        pattern = f"user:{tenant.user_id}:*"
        keys = await redis_manager.keys(pattern)
        
        used_mb = 0.0
        for key in keys[:100]:  # Sample first 100 keys
            size = await redis_manager.memory_usage(key)
            if size:
                used_mb += size / (1024 * 1024)
        
        # Extrapolate for large keyspaces
        if len(keys) > 100:
            used_mb = used_mb * (len(keys) / 100)
        
        limit = tenant.quota.max_redis_memory_mb
        
        if used_mb + data_size_mb > limit:
            details = self._create_violation_details(
                tenant, "storage", limit, int(used_mb), int(used_mb + data_size_mb), ctx.request_id
            )
            error = StorageQuotaExceededError(details, used_mb, data_size_mb)
            self._log_violation(error, ctx, {
                "used_mb": used_mb,
                "requested_mb": data_size_mb,
                "limit_mb": limit,
                "key_count": len(keys),
            })
            raise error
        
        logger.debug(f"Storage quota OK: {used_mb:.1f}MB + {data_size_mb:.1f}MB / {limit}MB")
    
    async def enforce_websocket_limit(
        self,
        tenant: TenantContext,
        ctx: EnforcementContext
    ):
        """Enforce WebSocket connection limit."""
        key = TenantKeyBuilder.websocket_connections_set(tenant.user_id)
        current = await redis_manager.scard(key)
        limit = tenant.quota.max_websocket_connections
        
        if current >= limit:
            details = self._create_violation_details(
                tenant, "websocket_connections", limit, current, current + 1, ctx.request_id
            )
            error = ConcurrentOperationQuotaError(details, "websocket")
            self._log_violation(error, ctx, {
                "active_connections": current,
                "max_allowed": limit,
            })
            raise error
        
        logger.debug(f"WebSocket quota OK: {current}/{limit}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # CONTEXT MANAGER FOR CONCURRENT OPERATIONS
    # ═══════════════════════════════════════════════════════════════════════
    
    @asynccontextmanager
    async def track_concurrent_operation(
        self,
        tenant: TenantContext,
        operation_type: str,
        operation_id: str,
        ctx: EnforcementContext
    ):
        """
        Context manager to track concurrent operations.
        
        Usage:
            async with enforcer.track_concurrent_operation(tenant, "backtest", id, ctx):
                await run_backtest()
        """
        key = f"user:{tenant.user_id}:concurrent:{operation_type}"
        
        try:
            # Register operation start
            await redis_manager.sadd(key, operation_id)
            await redis_manager.expire(key, 3600)  # 1h TTL
            
            yield
            
        finally:
            # Cleanup on exit
            await redis_manager.srem(key, operation_id)
    
    async def get_quota_status(
        self,
        tenant: TenantContext
    ) -> Dict[str, Any]:
        """Get current quota usage status for tenant."""
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")
        
        return {
            "tenant_id": tenant.user_id,
            "plan": tenant.plan.value,
            "timestamp": now.isoformat(),
            "quotas": {
                "dag_sessions": {
                    "limit": tenant.quota.max_dag_sessions,
                    "used": await redis_manager.scard(
                        TenantKeyBuilder.dag_sessions_set(tenant.user_id)
                    ),
                },
                "positions": {
                    "limit": tenant.quota.max_positions,
                    "used": await redis_manager.scard(
                        TenantKeyBuilder.positions_set(tenant.user_id)
                    ),
                },
                "orders_per_minute": {
                    "limit": tenant.quota.max_orders_per_minute,
                    "used": int(await redis_manager.get(
                        TenantKeyBuilder.rate_limit(tenant.user_id, "orders", "1m")
                    ) or 0),
                    "window": "1m",
                },
                "daily_trades": {
                    "limit": tenant.quota.max_daily_trades,
                    "used": int(await redis_manager.get(
                        f"user:{tenant.user_id}:daily_trades:{today}"
                    ) or 0),
                    "reset_time": (now + timedelta(days=1)).replace(
                        hour=0, minute=0, second=0
                    ).isoformat(),
                },
                "active_backtests": {
                    "limit": tenant.quota.max_backtest_parallel,
                    "used": await redis_manager.scard(
                        f"user:{tenant.user_id}:backtests:active"
                    ),
                },
                "websocket_connections": {
                    "limit": tenant.quota.max_websocket_connections,
                    "used": await redis_manager.scard(
                        TenantKeyBuilder.websocket_connections_set(tenant.user_id)
                    ),
                },
                "capital": {
                    "limit": tenant.quota.max_capital,
                    "allocated": float(await redis_manager.get(
                        f"user:{tenant.user_id}:portfolio:total_allocated"
                    ) or 0),
                    "unit": "USD",
                },
            },
        }


# ══════════════════════════════════════════════════════════════════════════
#  The singleton — now LAZY, so importing this module is not itself deprecated use
# ══════════════════════════════════════════════════════════════════════════
#
# This was `hard_quota_enforcer = HardQuotaEnforcer()` at module scope, which with the
# deprecation warning on `__init__` would fire on IMPORT — including the imports that only
# want the error types or this docstring, and including any test that merely reads the module.
# A warning that fires when nobody is doing anything wrong is a warning people filter out.
#
# So the instance is built on first access instead. `__getattr__` on a module is consulted only
# for names that are not already defined (PEP 562), so touching `hard_quota_enforcer` is what
# constructs it — and that IS the deprecated use the warning is for. The name is preserved so
# that nothing importing it breaks.

_hard_quota_enforcer_singleton: Optional["HardQuotaEnforcer"] = None


def __getattr__(name: str) -> Any:
    if name == "hard_quota_enforcer":
        global _hard_quota_enforcer_singleton
        if _hard_quota_enforcer_singleton is None:
            _hard_quota_enforcer_singleton = HardQuotaEnforcer()
        return _hard_quota_enforcer_singleton
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
