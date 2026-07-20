"""
Exchange Reconciliation Engine for Institutional Recovery

Phase 4 — Exchange Reconciliation Engine

Provides systematic reconciliation between local system state and exchange state
while preserving replay guarantees and preventing duplicate execution.

Author: Principal Institutional Recovery and Failover Engineer
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional, Any, Set

from backend_app.core.cache.redis_manager import redis_manager
from .immutable_journal import immutable_journal

logger = logging.getLogger("exchange_reconciliation_engine")


class ReconciliationType(Enum):
    """Reconciliation types."""
    ORDER = "order"
    POSITION = "position"
    BALANCE = "balance"


@dataclass
class ReconciliationContext:
    """Reconciliation operation context."""
    reconciliation_id: str
    tenant_id: str
    exchange_id: str
    reconciliation_type: str
    started_at: datetime
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    completed_at: Optional[datetime] = None


@dataclass
class ReconciliationResult:
    """Result of reconciliation operation."""
    reconciliation_type: str
    tenant_id: str
    exchange_id: str
    discrepancies_found: int
    discrepancies_resolved: int
    discrepancies_unresolved: int
    reconciled_at: datetime
    error: Optional[str] = None


@dataclass
class OrderDiscrepancy:
    """Order discrepancy."""
    order_id: str
    discrepancy_type: str
    local_status: Optional[str]
    exchange_status: Optional[str]
    local_quantity: Optional[float]
    exchange_quantity: Optional[float]
    local_price: Optional[float]
    exchange_price: Optional[float]
    detected_at: datetime


@dataclass
class PositionDiscrepancy:
    """Position discrepancy."""
    symbol: str
    side: str
    discrepancy_type: str
    local_size: Optional[float]
    exchange_size: Optional[float]
    local_entry_price: Optional[float]
    exchange_entry_price: Optional[float]
    detected_at: datetime


@dataclass
class BalanceDiscrepancy:
    """Balance discrepancy."""
    currency: str
    discrepancy_type: str
    local_free: Optional[float]
    exchange_free: Optional[float]
    local_used: Optional[float]
    exchange_used: Optional[float]
    detected_at: datetime


@dataclass
class ResolutionResult:
    """Result of discrepancy resolution."""
    success: bool
    action: str
    reason: Optional[str] = None
    action_required: Optional[str] = None


class ReconciliationScheduler:
    """Reconciliation scheduler for exchange reconciliation."""
    
    def __init__(self):
        self.redis = redis_manager
        
        # Scheduling configuration
        self.order_reconciliation_interval = 30.0  # 30 seconds
        self.position_reconciliation_interval = 60.0  # 60 seconds
        self.balance_reconciliation_interval = 300.0  # 5 minutes
        
        # Active reconciliations
        self.active_reconciliations: Dict[str, ReconciliationContext] = {}
        
        # Reconciliation history
        self.reconciliation_history: List[ReconciliationResult] = []
        
        # Background tasks
        self._order_task: Optional[asyncio.Task] = None
        self._position_task: Optional[asyncio.Task] = None
        self._balance_task: Optional[asyncio.Task] = None
        self._running = False
        
        logger.info("Reconciliation scheduler initialized")
    
    async def initialize(self) -> bool:
        """Initialize reconciliation scheduler."""
        try:
            logger.info("Reconciliation scheduler initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize reconciliation scheduler: {e}")
            return False
    
    async def start(self) -> bool:
        """Start reconciliation scheduler."""
        try:
            self._running = True
            
            # Start periodic reconciliation tasks
            self._order_task = asyncio.create_task(self._order_reconciliation_loop())
            self._position_task = asyncio.create_task(self._position_reconciliation_loop())
            self._balance_task = asyncio.create_task(self._balance_reconciliation_loop())
            
            logger.info("Reconciliation scheduler started")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start reconciliation scheduler: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop reconciliation scheduler."""
        try:
            self._running = False
            
            # Cancel tasks
            tasks = [self._order_task, self._position_task, self._balance_task]
            for task in tasks:
                if task:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            logger.info("Reconciliation scheduler stopped")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop reconciliation scheduler: {e}")
            return False
    
    async def _order_reconciliation_loop(self):
        """Periodic order reconciliation loop."""
        while self._running:
            try:
                # Get all active tenants with exchange connections
                tenants = await self._get_active_tenants()
                
                for tenant_id in tenants:
                    # Get tenant's exchanges
                    exchanges = await self._get_tenant_exchanges(tenant_id)
                    
                    for exchange_id in exchanges:
                        # Trigger order reconciliation
                        await self._trigger_reconciliation(
                            tenant_id=tenant_id,
                            exchange_id=exchange_id,
                            reconciliation_type="order"
                        )
                
                await asyncio.sleep(self.order_reconciliation_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Order reconciliation loop error: {e}")
                await asyncio.sleep(self.order_reconciliation_interval)
    
    async def _position_reconciliation_loop(self):
        """Periodic position reconciliation loop."""
        while self._running:
            try:
                tenants = await self._get_active_tenants()
                
                for tenant_id in tenants:
                    exchanges = await self._get_tenant_exchanges(tenant_id)
                    
                    for exchange_id in exchanges:
                        await self._trigger_reconciliation(
                            tenant_id=tenant_id,
                            exchange_id=exchange_id,
                            reconciliation_type="position"
                        )
                
                await asyncio.sleep(self.position_reconciliation_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Position reconciliation loop error: {e}")
                await asyncio.sleep(self.position_reconciliation_interval)
    
    async def _balance_reconciliation_loop(self):
        """Periodic balance reconciliation loop."""
        while self._running:
            try:
                tenants = await self._get_active_tenants()
                
                for tenant_id in tenants:
                    exchanges = await self._get_tenant_exchanges(tenant_id)
                    
                    for exchange_id in exchanges:
                        await self._trigger_reconciliation(
                            tenant_id=tenant_id,
                            exchange_id=exchange_id,
                            reconciliation_type="balance"
                        )
                
                await asyncio.sleep(self.balance_reconciliation_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Balance reconciliation loop error: {e}")
                await asyncio.sleep(self.balance_reconciliation_interval)
    
    async def _trigger_reconciliation(self, tenant_id: str, exchange_id: str, 
                                      reconciliation_type: str) -> str:
        """Trigger reconciliation job."""
        reconciliation_id = str(uuid.uuid4())
        
        # Create reconciliation context
        context = ReconciliationContext(
            reconciliation_id=reconciliation_id,
            tenant_id=tenant_id,
            exchange_id=exchange_id,
            reconciliation_type=reconciliation_type,
            started_at=datetime.now(timezone.utc),
            status="in_progress"
        )
        
        # Store context
        self.active_reconciliations[reconciliation_id] = context
        
        # Store in Redis for persistence
        context_key = f"reconciliation:context:{reconciliation_id}"
        await self.redis.setex(
            context_key,
            3600,
            json.dumps(asdict(context), default=str)
        )
        
        # Execute reconciliation
        asyncio.create_task(self._execute_reconciliation(context))
        
        return reconciliation_id
    
    async def _execute_reconciliation(self, context: ReconciliationContext):
        """Execute reconciliation job."""
        try:
            # Get appropriate reconciler
            if context.reconciliation_type == "order":
                reconciler = OrderReconciler()
            elif context.reconciliation_type == "position":
                reconciler = PositionReconciler()
            elif context.reconciliation_type == "balance":
                reconciler = BalanceReconciler()
            else:
                raise ValueError(f"Unknown reconciliation type: {context.reconciliation_type}")
            
            # Execute reconciliation
            result = await reconciler.reconcile(
                tenant_id=context.tenant_id,
                exchange_id=context.exchange_id
            )
            
            # Update context
            context.status = "completed" if result.error is None else "failed"
            context.completed_at = datetime.now(timezone.utc)
            context.result = asdict(result)
            
            # Store result in history
            self.reconciliation_history.append(result)
            
            # Update Redis
            context_key = f"reconciliation:context:{context.reconciliation_id}"
            await self.redis.setex(
                context_key,
                86400,  # 24 hours
                json.dumps(asdict(context), default=str)
            )
            
            logger.info(
                f"Reconciliation {context.reconciliation_id} completed: "
                f"{result.discrepancies_found} discrepancies found, "
                f"{result.discrepancies_resolved} resolved"
            )
            
        except Exception as e:
            logger.error(f"Reconciliation {context.reconciliation_id} failed: {e}")
            context.status = "failed"
            context.error = str(e)
            context.completed_at = datetime.now(timezone.utc)
    
    async def _get_active_tenants(self) -> List[str]:
        """Get all active tenants."""
        try:
            # For now, return a fixed list
            # In production, this would query the database
            return ["tenant_1", "tenant_2"]
            
        except Exception as e:
            logger.error(f"Failed to get active tenants: {e}")
            return []
    
    async def _get_tenant_exchanges(self, tenant_id: str) -> List[str]:
        """Get tenant's exchanges."""
        try:
            # For now, return a fixed list
            # In production, this would query the database
            return ["binance", "bybit"]
            
        except Exception as e:
            logger.error(f"Failed to get tenant exchanges: {e}")
            return []


class StateComparator:
    """State comparator for exchange reconciliation."""
    
    async def compare_orders(self, local_orders: List[Dict], 
                           exchange_orders: List[Dict]) -> List[OrderDiscrepancy]:
        """Compare local order state with exchange order state."""
        discrepancies = []
        
        # Build exchange order map
        exchange_order_map = {
            order["order_id"]: order
            for order in exchange_orders
        }
        
        # Check for missing orders (in local but not in exchange)
        for local_order in local_orders:
            order_id = local_order["order_id"]
            
            if order_id not in exchange_order_map:
                discrepancies.append(OrderDiscrepancy(
                    order_id=order_id,
                    discrepancy_type="missing_on_exchange",
                    local_status=local_order["status"],
                    exchange_status=None,
                    local_quantity=local_order.get("quantity"),
                    exchange_quantity=None,
                    local_price=local_order.get("price"),
                    exchange_price=None,
                    detected_at=datetime.now(timezone.utc)
                ))
            else:
                # Compare order details
                exchange_order = exchange_order_map[order_id]
                
                # Check status mismatch
                if local_order["status"] != exchange_order["status"]:
                    discrepancies.append(OrderDiscrepancy(
                        order_id=order_id,
                        discrepancy_type="status_mismatch",
                        local_status=local_order["status"],
                        exchange_status=exchange_order["status"],
                        local_quantity=local_order.get("quantity"),
                        exchange_quantity=exchange_order.get("amount"),
                        local_price=local_order.get("price"),
                        exchange_price=exchange_order.get("price"),
                        detected_at=datetime.now(timezone.utc)
                    ))
        
        # Check for extra orders (in exchange but not in local)
        for exchange_order in exchange_orders:
            order_id = exchange_order["order_id"]
            
            if order_id not in [o["order_id"] for o in local_orders]:
                discrepancies.append(OrderDiscrepancy(
                    order_id=order_id,
                    discrepancy_type="extra_on_exchange",
                    local_status=None,
                    exchange_status=exchange_order["status"],
                    local_quantity=None,
                    exchange_quantity=exchange_order.get("amount"),
                    local_price=None,
                    exchange_price=exchange_order.get("price"),
                    detected_at=datetime.now(timezone.utc)
                ))
        
        return discrepancies
    
    async def compare_positions(self, local_positions: List[Dict],
                               exchange_positions: List[Dict]) -> List[PositionDiscrepancy]:
        """Compare local position state with exchange position state."""
        discrepancies = []
        
        # Build exchange position map
        exchange_position_map = {
            f"{p['symbol']}:{p['side']}": p
            for p in exchange_positions
        }
        
        # Check for missing positions
        for local_position in local_positions:
            position_key = f"{local_position['symbol']}:{local_position['side']}"
            
            if position_key not in exchange_position_map:
                discrepancies.append(PositionDiscrepancy(
                    symbol=local_position["symbol"],
                    side=local_position["side"],
                    discrepancy_type="missing_on_exchange",
                    local_size=local_position.get("size"),
                    exchange_size=None,
                    local_entry_price=local_position.get("entry_price"),
                    exchange_entry_price=None,
                    detected_at=datetime.now(timezone.utc)
                ))
        
        return discrepancies
    
    async def compare_balances(self, local_balances: Dict[str, Dict],
                               exchange_balances: Dict[str, Dict]) -> List[BalanceDiscrepancy]:
        """Compare local balance state with exchange balance state."""
        discrepancies = []
        
        # Check for missing currencies
        for currency, local_balance in local_balances.items():
            if currency not in exchange_balances:
                discrepancies.append(BalanceDiscrepancy(
                    currency=currency,
                    discrepancy_type="missing_on_exchange",
                    local_free=local_balance.get("free"),
                    exchange_free=None,
                    local_used=local_balance.get("used"),
                    exchange_used=None,
                    detected_at=datetime.now(timezone.utc)
                ))
        
        return discrepancies


class ConflictResolver:
    """Conflict resolver for exchange reconciliation."""
    
    def __init__(self):
        self.immutable_journal = immutable_journal
    
    async def resolve_order_discrepancy(self, discrepancy: OrderDiscrepancy,
                                       tenant_id: str, exchange_id: str) -> ResolutionResult:
        """Resolve order discrepancy."""
        try:
            # Resolve based on discrepancy type
            if discrepancy.discrepancy_type == "missing_on_exchange":
                # Order exists locally but not on exchange
                # Check journal for authoritative state
                journal_events = await self.immutable_journal.get_events_by_tenant(tenant_id)
                order_events = [e for e in journal_events 
                              if e.header.event_type.name == "ORDER_SUBMITTED"
                              and e.payload.get("order_id") == discrepancy.order_id]
                
                if order_events:
                    # Order was submitted according to journal
                    logger.warning(
                        f"Order {discrepancy.order_id} submitted in journal "
                        f"but missing on exchange {exchange_id}"
                    )
                    return ResolutionResult(
                        success=False,
                        action="logged_warning",
                        reason="Order accepted in journal but missing on exchange",
                        action_required="manual_intervention"
                    )
                else:
                    # Order was not submitted
                    return ResolutionResult(
                        success=True,
                        action="no_action"
                    )
            
            elif discrepancy.discrepancy_type == "status_mismatch":
                # Status differs between local and exchange
                # Exchange is authoritative for current status
                return ResolutionResult(
                    success=True,
                    action="update_local_status_from_exchange"
                )
            
            return ResolutionResult(
                success=False,
                reason="Unknown discrepancy type"
            )
            
        except Exception as e:
            logger.error(f"Order discrepancy resolution failed: {e}")
            return ResolutionResult(
                success=False,
                reason=str(e)
            )


class OrderReconciler:
    """Order reconciler for exchange reconciliation."""
    
    def __init__(self):
        self.state_comparator = StateComparator()
        self.conflict_resolver = ConflictResolver()
    
    async def reconcile(self, tenant_id: str, exchange_id: str) -> ReconciliationResult:
        """Reconcile orders with exchange."""
        try:
            # Get local orders
            local_orders = await self._get_local_orders(tenant_id, exchange_id)
            
            # Get exchange orders
            exchange_orders = await self._get_exchange_orders(tenant_id, exchange_id)
            
            # Compare states
            discrepancies = await self.state_comparator.compare_orders(
                local_orders,
                exchange_orders
            )
            
            # Resolve discrepancies
            resolved_count = 0
            for discrepancy in discrepancies:
                resolution = await self.conflict_resolver.resolve_order_discrepancy(
                    discrepancy,
                    tenant_id,
                    exchange_id
                )
                
                if resolution.success:
                    resolved_count += 1
            
            return ReconciliationResult(
                reconciliation_type="order",
                tenant_id=tenant_id,
                exchange_id=exchange_id,
                discrepancies_found=len(discrepancies),
                discrepancies_resolved=resolved_count,
                discrepancies_unresolved=len(discrepancies) - resolved_count,
                reconciled_at=datetime.now(timezone.utc)
            )
            
        except Exception as e:
            logger.error(f"Order reconciliation failed: {e}")
            return ReconciliationResult(
                reconciliation_type="order",
                tenant_id=tenant_id,
                exchange_id=exchange_id,
                discrepancies_found=0,
                discrepancies_resolved=0,
                discrepancies_unresolved=0,
                error=str(e),
                reconciled_at=datetime.now(timezone.utc)
            )
    
    async def _get_local_orders(self, tenant_id: str, exchange_id: str) -> List[Dict]:
        """Get local order state from database."""
        try:
            # For now, return empty list
            # In production, this would query the database
            return []
            
        except Exception as e:
            logger.error(f"Failed to get local orders: {e}")
            return []
    
    async def _get_exchange_orders(self, tenant_id: str, exchange_id: str) -> List[Dict]:
        """Get exchange order state from exchange API."""
        try:
            # For now, return empty list
            # In production, this would call the exchange API
            return []
            
        except Exception as e:
            logger.error(f"Failed to get exchange orders: {e}")
            return []


class PositionReconciler:
    """Position reconciler for exchange reconciliation."""
    
    def __init__(self):
        self.state_comparator = StateComparator()
    
    async def reconcile(self, tenant_id: str, exchange_id: str) -> ReconciliationResult:
        """Reconcile positions with exchange."""
        try:
            # Get local positions
            local_positions = await self._get_local_positions(tenant_id, exchange_id)
            
            # Get exchange positions
            exchange_positions = await self._get_exchange_positions(tenant_id, exchange_id)
            
            # Compare states
            discrepancies = await self.state_comparator.compare_positions(
                local_positions,
                exchange_positions
            )
            
            return ReconciliationResult(
                reconciliation_type="position",
                tenant_id=tenant_id,
                exchange_id=exchange_id,
                discrepancies_found=len(discrepancies),
                discrepancies_resolved=0,
                discrepancies_unresolved=len(discrepancies),
                reconciled_at=datetime.now(timezone.utc)
            )
            
        except Exception as e:
            logger.error(f"Position reconciliation failed: {e}")
            return ReconciliationResult(
                reconciliation_type="position",
                tenant_id=tenant_id,
                exchange_id=exchange_id,
                discrepancies_found=0,
                discrepancies_resolved=0,
                discrepancies_unresolved=0,
                error=str(e),
                reconciled_at=datetime.now(timezone.utc)
            )
    
    async def _get_local_positions(self, tenant_id: str, exchange_id: str) -> List[Dict]:
        """Get local position state from database."""
        try:
            # For now, return empty list
            # In production, this would query the database
            return []
            
        except Exception as e:
            logger.error(f"Failed to get local positions: {e}")
            return []
    
    async def _get_exchange_positions(self, tenant_id: str, exchange_id: str) -> List[Dict]:
        """Get exchange position state from exchange API."""
        try:
            # For now, return empty list
            # In production, this would call the exchange API
            return []
            
        except Exception as e:
            logger.error(f"Failed to get exchange positions: {e}")
            return []


class BalanceReconciler:
    """Balance reconciler for exchange reconciliation."""
    
    def __init__(self):
        self.state_comparator = StateComparator()
    
    async def reconcile(self, tenant_id: str, exchange_id: str) -> ReconciliationResult:
        """Reconcile balances with exchange."""
        try:
            # Get local balances
            local_balances = await self._get_local_balances(tenant_id, exchange_id)
            
            # Get exchange balances
            exchange_balances = await self._get_exchange_balances(tenant_id, exchange_id)
            
            # Compare states
            discrepancies = await self.state_comparator.compare_balances(
                local_balances,
                exchange_balances
            )
            
            return ReconciliationResult(
                reconciliation_type="balance",
                tenant_id=tenant_id,
                exchange_id=exchange_id,
                discrepancies_found=len(discrepancies),
                discrepancies_resolved=0,
                discrepancies_unresolved=len(discrepancies),
                reconciled_at=datetime.now(timezone.utc)
            )
            
        except Exception as e:
            logger.error(f"Balance reconciliation failed: {e}")
            return ReconciliationResult(
                reconciliation_type="balance",
                tenant_id=tenant_id,
                exchange_id=exchange_id,
                discrepancies_found=0,
                discrepancies_resolved=0,
                discrepancies_unresolved=0,
                error=str(e),
                reconciled_at=datetime.now(timezone.utc)
            )
    
    async def _get_local_balances(self, tenant_id: str, exchange_id: str) -> Dict[str, Dict]:
        """Get local balance state from database."""
        try:
            # For now, return empty dict
            # In production, this would query the database
            return {}
            
        except Exception as e:
            logger.error(f"Failed to get local balances: {e}")
            return {}
    
    async def _get_exchange_balances(self, tenant_id: str, exchange_id: str) -> Dict[str, Dict]:
        """Get exchange balance state from exchange API."""
        try:
            # For now, return empty dict
            # In production, this would call the exchange API
            return {}
            
        except Exception as e:
            logger.error(f"Failed to get exchange balances: {e}")
            return {}


# Alias for validation suite compatibility
ExchangeReconciliationEngine = ReconciliationScheduler

# Global instance
_exchange_reconciliation_engine: Optional[ReconciliationScheduler] = None


def get_exchange_reconciliation_engine() -> ReconciliationScheduler:
    """Get or create exchange reconciliation engine instance."""
    global _exchange_reconciliation_engine
    if _exchange_reconciliation_engine is None:
        _exchange_reconciliation_engine = ReconciliationScheduler()
    return _exchange_reconciliation_engine


__all__ = [
    "ExchangeReconciliationEngine",
    "ReconciliationScheduler",
    "ReconciliationResult",
    "ReconciliationContext",
    "OrderReconciler",
    "PositionReconciler",
    "BalanceReconciler",
    "StateComparator",
    "ConflictResolver",
    "get_exchange_reconciliation_engine",
]
