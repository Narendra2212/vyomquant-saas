"""
Exchange Reconciliation Service

STEP 3.4 — ORDER CONSISTENCY + EXCHANGE RECONCILIATION

Ensures system state ALWAYS matches exchange reality.
Prevents "ghost orders" and detects discrepancies.

Reconciliation Loop:
┌─────────────────────────────────────────────────────────────────┐
│  Every 5-10 seconds                                              │
│                                                                  │
│  1. Fetch open orders from exchange                               │
│  2. Fetch open orders from DB                                    │
│  3. Compare and reconcile:                                        │
│                                                                  │
│     Exchange has order, DB missing:                               │
│     → INSERT into DB (ghost order detected)                       │
│                                                                  │
│     DB has order, exchange missing:                             │
│     → Mark FILLED or CANCELLED (reconciliation)                   │
│                                                                  │
│     Both have order but mismatch:                                 │
│     → Update filled_size, avg_price, status                       │
│                                                                  │
│  4. Log all discrepancies                                         │
└─────────────────────────────────────────────────────────────────┘
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend_app.core.models.execution_record import (ExecutionRecordModel,
                                                      ExecutionStatus)
from backend_app.core.order_state_machine import (OrderStateMachine,
                                                  get_order_state_machine,
                                                  transition_to_cancelled,
                                                  transition_to_filled,
                                                  transition_to_partial_fill)

logger = logging.getLogger(__name__)


class ReconciliationAction(Enum):
    """Actions from reconciliation process."""
    INSERT_GHOST_ORDER = "insert_ghost_order"
    MARK_FILLED = "mark_filled"
    MARK_CANCELLED = "mark_cancelled"
    UPDATE_FILL_DATA = "update_fill_data"
    NO_ACTION = "no_action"


@dataclass
class ReconciliationResult:
    """Result of a reconciliation operation."""
    execution_id: str
    action: ReconciliationAction
    old_state: Optional[str]
    new_state: Optional[str]
    exchange_order_id: Optional[str]
    filled_size: Optional[float]
    avg_price: Optional[float]
    discrepancy: Optional[str] = None


class ExchangeReconciliationService:
    """
    Service for reconciling orders between system and exchange.
    
    Runs continuously in background to ensure consistency.
    
    Usage:
        service = ExchangeReconciliationService(db_session, exchange_client)
        
        # Start reconciliation loop
        await service.start_reconciliation_loop(
            tenant_id="tenant-123",
            interval_seconds=5
        )
        
        # Or run single reconciliation
        results = await service.reconcile_open_orders(tenant_id="tenant-123")
    """
    
    def __init__(
        self,
        db_session: Session,
        exchange_client: Optional[Any] = None,
        state_machine: Optional[OrderStateMachine] = None
    ):
        """
        Initialize reconciliation service.
        
        Args:
            db_session: SQLAlchemy session for DB operations
            exchange_client: Exchange API client (ccxt or custom)
            state_machine: Order state machine instance
        """
        self.db = db_session
        self.exchange = exchange_client
        self.state_machine = state_machine or get_order_state_machine()
        self._running = False
        self._reconciliation_task: Optional[asyncio.Task] = None
        
        logger.info("ExchangeReconciliationService initialized")
    
    async def reconcile_open_orders(
        self,
        tenant_id: str,
        exchange_id: Optional[str] = None
    ) -> List[ReconciliationResult]:
        """
        STEP 3.4: Reconcile open orders between system and exchange.
        
        1. Fetch open orders from exchange
        2. Fetch open orders from DB
        3. Compare and resolve discrepancies
        
        Args:
            tenant_id: Tenant UUID
            exchange_id: Optional exchange identifier for filtering
            
        Returns:
            List of reconciliation results
        """
        logger.info(f"Starting reconciliation for tenant {tenant_id}")
        results: List[ReconciliationResult] = []
        
        try:
            # 1. Fetch orders from exchange
            exchange_orders = await self._fetch_exchange_orders(tenant_id, exchange_id)
            logger.debug(f"Fetched {len(exchange_orders)} orders from exchange")
            
            # 2. Fetch active orders from DB
            db_orders = self._fetch_db_active_orders(tenant_id, exchange_id)
            logger.debug(f"Fetched {len(db_orders)} active orders from DB")
            
            # Build lookup maps
            exchange_orders_by_id = {o['order_id']: o for o in exchange_orders}
            {o.execution_id: o for o in db_orders}
            
            # 3. Compare and reconcile
            
            # CASE 1: Exchange has order, DB missing → INSERT (ghost order)
            for order_id, exchange_order in exchange_orders_by_id.items():
                matching_db_order = self._find_db_order_by_exchange_id(db_orders, order_id)
                
                if not matching_db_order:
                    result = await self._handle_ghost_order(
                        tenant_id, exchange_order
                    )
                    results.append(result)
                    logger.warning(
                        f"GHOST ORDER DETECTED: order_id={order_id} | "
                        f"Inserted into DB with status={result.new_state}"
                    )
            
            # CASE 2 & 3: DB has order, check against exchange
            for db_order in db_orders:
                exchange_order = exchange_orders_by_id.get(db_order.order_id)
                
                if not exchange_order:
                    # Exchange doesn't have order → FILLED or CANCELLED
                    result = await self._handle_missing_exchange_order(db_order)
                    results.append(result)
                else:
                    # Both have order → Check for fill data mismatch
                    result = await self._handle_fill_mismatch(db_order, exchange_order)
                    if result.action != ReconciliationAction.NO_ACTION:
                        results.append(result)
            
            # Update last sync timestamp for all reconciled orders
            self._update_sync_timestamp([r.execution_id for r in results])
            
            logger.info(
                f"Reconciliation complete: {len(results)} actions, "
                f"{len([r for r in results if r.action != ReconciliationAction.NO_ACTION])} discrepancies"
            )
            
        except Exception as e:
            logger.error(f"Reconciliation failed: {e}", exc_info=True)
            raise
        
        return results
    
    async def _fetch_exchange_orders(
        self,
        tenant_id: str,
        exchange_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch open orders from exchange API.
        
        Returns list of orders with:
        - order_id: Exchange order ID
        - symbol: Trading symbol
        - side: buy/sell
        - size: Total order size
        - filled_size: Amount filled
        - remaining_size: Amount remaining
        - avg_price: Average fill price
        - status: Exchange status
        """
        if not self.exchange:
            logger.warning("No exchange client configured, returning empty list")
            return []
        
        try:
            # This would call the actual exchange API
            # Format depends on exchange (ccxt, custom, etc.)
            orders = await self.exchange.fetch_open_orders()
            
            # Normalize to common format
            normalized = []
            for order in orders:
                normalized.append({
                    'order_id': order.get('id'),
                    'symbol': order.get('symbol', '').upper(),
                    'side': order.get('side', '').lower(),
                    'size': float(order.get('amount', 0)),
                    'filled_size': float(order.get('filled', 0)),
                    'remaining_size': float(order.get('remaining', 0)),
                    'avg_price': float(order.get('average', 0) or order.get('price', 0)),
                    'status': order.get('status', 'open'),
                    'raw': order  # Keep raw data for reference
                })
            
            return normalized
            
        except Exception as e:
            # BUG-FIX UK-01: NEVER return [] on exchange API error.
            # An empty list causes reconcile_open_orders() to treat every DB-active
            # order as absent from the exchange and cancel all of them.
            # Transient errors (timeout, 429, 500) must propagate so the caller aborts.
            logger.error(f"Failed to fetch exchange orders: {e}")
            raise
    
    def _fetch_db_active_orders(
        self,
        tenant_id: str,
        exchange_id: Optional[str] = None
    ) -> List[ExecutionRecordModel]:
        """Fetch active orders from DB (not in terminal state)."""
        query = self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.tenant_id == UUID(tenant_id),
            ExecutionRecordModel.status.in_([
                ExecutionStatus.PENDING.value,
                ExecutionStatus.EXECUTING.value
            ])
        )
        
        if exchange_id:
            query = query.filter(ExecutionRecordModel.exchange_id == exchange_id)
        
        return query.all()
    
    def _find_db_order_by_exchange_id(
        self,
        db_orders: List[ExecutionRecordModel],
        exchange_order_id: str
    ) -> Optional[ExecutionRecordModel]:
        """Find DB order matching exchange order_id."""
        for order in db_orders:
            if order.order_id == exchange_order_id:
                return order
        return None
    
    async def _handle_ghost_order(
        self,
        tenant_id: str,
        exchange_order: Dict[str, Any]
    ) -> ReconciliationResult:
        """
        CASE 1: Exchange has order, DB missing → INSERT into DB.
        
        This is a "ghost order" - exists on exchange but not in our system.
        Can happen due to:
        - Network issues during order creation
        - Race conditions
        - Manual exchange trading
        """
        # Generate execution_id for this order
        execution_id = f"exec_ghost_{exchange_order['order_id']}"
        
        # Create DB record
        new_record = ExecutionRecordModel(
            execution_id=execution_id,
            tenant_id=UUID(tenant_id),
            task_id=None,
            strategy_id="reconciled",  # Unknown, from reconciliation
            symbol=exchange_order['symbol'],
            side=exchange_order['side'],
            size=str(exchange_order['size']),
            price=None,  # Unknown for ghost orders
            status=ExecutionStatus.EXECUTING,
            order_id=exchange_order['order_id'],
            filled_size=str(exchange_order['filled_size']),
            avg_price=str(exchange_order['avg_price']) if exchange_order['avg_price'] else None,
            remaining_size=str(exchange_order['remaining_size']),
            exchange_status=exchange_order['status'],
            result={"source": "reconciliation", "note": "Ghost order detected"}
        )
        
        self.db.add(new_record)
        self.db.commit()
        
        # Update state machine
        current_state = self.state_machine.get_current_state(execution_id)
        if not current_state:
            # Initialize state (ghost order is already submitted/pending on exchange)
            from backend_app.core.order_state_machine import OrderState
            self.state_machine._current_states[execution_id] = OrderState.SUBMITTED
        
        return ReconciliationResult(
            execution_id=execution_id,
            action=ReconciliationAction.INSERT_GHOST_ORDER,
            old_state=None,
            new_state="submitted",
            exchange_order_id=exchange_order['order_id'],
            filled_size=exchange_order['filled_size'],
            avg_price=exchange_order['avg_price']
        )
    
    async def _handle_missing_exchange_order(
        self,
        db_order: ExecutionRecordModel
    ) -> ReconciliationResult:
        """
        CASE 2: DB has order, missing from open_orders → Query specific order or reconcile.
        
        Order no longer exists in open_orders list. Could be:
        - FILLED (instant market fill or completed limit)
        - CANCELLED (user or system cancelled)
        - EXPIRED/REJECTED
        """
        # BUG-FIX ORD-03: Query authoritative order status from exchange first if supported
        if self.exchange and hasattr(self.exchange, "fetch_order") and db_order.order_id:
            try:
                raw_order = await self.exchange.fetch_order(db_order.order_id, db_order.symbol)
                if isinstance(raw_order, dict):
                    raw_status = str(raw_order.get("status", "")).lower()
                    filled_val = float(raw_order.get("filled", 0) or 0)
                    avg_p = float(raw_order.get("average", 0) or raw_order.get("price", 0) or 0)
                    
                    if raw_status in ["closed", "filled"] or filled_val >= float(db_order.size or 0) * 0.99999:
                        transition_to_filled(
                            execution_id=db_order.execution_id,
                            filled_size=filled_val,
                            avg_price=avg_p,
                            exchange_order_id=db_order.order_id,
                            reason="Reconciled via fetch_order: Order confirmed filled on exchange"
                        )
                        db_order.status = ExecutionStatus.COMPLETED
                        db_order.filled_size = str(filled_val)
                        db_order.avg_price = str(avg_p)
                        db_order.filled_at = datetime.utcnow()
                        self.db.commit()
                        
                        return ReconciliationResult(
                            execution_id=db_order.execution_id,
                            action=ReconciliationAction.MARK_FILLED,
                            old_state=db_order.status,
                            new_state="filled",
                            exchange_order_id=db_order.order_id,
                            filled_size=filled_val,
                            avg_price=avg_p,
                            discrepancy="Order closed on exchange (verified via fetch_order)"
                        )
                    elif raw_status in ["canceled", "cancelled", "expired"]:
                        transition_to_cancelled(
                            execution_id=db_order.execution_id,
                            cancelled_by="exchange",
                            reason="Reconciled via fetch_order: Order cancelled on exchange"
                        )
                        db_order.status = ExecutionStatus.FAILED
                        self.db.commit()
                        
                        return ReconciliationResult(
                            execution_id=db_order.execution_id,
                            action=ReconciliationAction.MARK_CANCELLED,
                            old_state=db_order.status,
                            new_state="cancelled",
                            exchange_order_id=db_order.order_id,
                            filled_size=filled_val,
                            avg_price=avg_p if avg_p > 0 else None,
                            discrepancy="Order cancelled on exchange (verified via fetch_order)"
                        )
                    else:
                        # Unrecognized or missing status in response -> preserve state for retry
                        logger.warning(f"Unrecognized status '{raw_status}' from fetch_order for {db_order.execution_id}; preserving state")
                        return ReconciliationResult(
                            execution_id=db_order.execution_id,
                            action=ReconciliationAction.NO_ACTION,
                            old_state=db_order.status,
                            new_state=db_order.status,
                            exchange_order_id=db_order.order_id,
                            filled_size=float(db_order.filled_size or 0),
                            avg_price=float(db_order.avg_price or 0) if db_order.avg_price else None,
                            discrepancy=f"Unrecognized status '{raw_status}' from fetch_order; state preserved for retry"
                        )
                else:
                    # fetch_order returned non-dict (e.g. None) -> preserve state for retry
                    logger.warning(f"fetch_order returned non-dict response for {db_order.execution_id}; preserving state")
                    return ReconciliationResult(
                        execution_id=db_order.execution_id,
                        action=ReconciliationAction.NO_ACTION,
                        old_state=db_order.status,
                        new_state=db_order.status,
                        exchange_order_id=db_order.order_id,
                        filled_size=float(db_order.filled_size or 0),
                        avg_price=float(db_order.avg_price or 0) if db_order.avg_price else None,
                        discrepancy="fetch_order returned non-dict response; state preserved for retry"
                    )
            except Exception as e:
                # BUG-FIX REC-01: Network timeout or transient exchange error must NOT destroy local state
                logger.warning(f"Failed to fetch individual order status for {db_order.execution_id} due to transient error: {e}")
                return ReconciliationResult(
                    execution_id=db_order.execution_id,
                    action=ReconciliationAction.NO_ACTION,
                    old_state=db_order.status,
                    new_state=db_order.status,
                    exchange_order_id=db_order.order_id,
                    filled_size=float(db_order.filled_size or 0),
                    avg_price=float(db_order.avg_price or 0) if db_order.avg_price else None,
                    discrepancy=f"Exchange status uncertain due to transient error ({e}); state preserved for next reconciliation cycle"
                )

        # Fallback when fetch_order unavailable or order does not exist
        filled_size = float(db_order.filled_size or 0)
        
        if filled_size > 0:
            # Assume FILLED (conservative - better than leaving hanging)
            transition_to_filled(
                execution_id=db_order.execution_id,
                filled_size=filled_size,
                avg_price=float(db_order.avg_price or 0),
                exchange_order_id=db_order.order_id or "unknown",
                reason="Reconciled: Order not found on exchange, marked as filled"
            )
            
            # Update DB
            db_order.status = ExecutionStatus.COMPLETED
            db_order.filled_at = datetime.utcnow()
            self.db.commit()
            
            return ReconciliationResult(
                execution_id=db_order.execution_id,
                action=ReconciliationAction.MARK_FILLED,
                old_state=db_order.status,
                new_state="filled",
                exchange_order_id=db_order.order_id,
                filled_size=filled_size,
                avg_price=float(db_order.avg_price or 0),
                discrepancy="Order not found on exchange"
            )
        else:
            # No fill recorded -> CANCELLED
            transition_to_cancelled(
                execution_id=db_order.execution_id,
                cancelled_by="reconciliation",
                reason="Reconciled: Order not found on exchange, assumed cancelled"
            )
            
            # Update DB (use FAILED status for cancellation)
            db_order.status = ExecutionStatus.FAILED
            self.db.commit()
            
            return ReconciliationResult(
                execution_id=db_order.execution_id,
                action=ReconciliationAction.MARK_CANCELLED,
                old_state=db_order.status,
                new_state="cancelled",
                exchange_order_id=db_order.order_id,
                filled_size=0,
                avg_price=None,
                discrepancy="Order not found on exchange, no fill recorded"
            )
    
    async def _handle_fill_mismatch(
        self,
        db_order: ExecutionRecordModel,
        exchange_order: Dict[str, Any]
    ) -> ReconciliationResult:
        """
        CASE 3: Both have order → Update fill data if mismatch.
        """
        db_filled = float(db_order.filled_size or 0)
        exchange_filled = exchange_order['filled_size']
        
        # Check for mismatch
        if abs(db_filled - exchange_filled) > 1e-9:  # Allow for float precision
            # Update DB with exchange data (exchange is source of truth)
            db_order.filled_size = str(exchange_filled)
            db_order.remaining_size = str(exchange_order['remaining_size'])
            db_order.avg_price = str(exchange_order['avg_price']) if exchange_order['avg_price'] else None
            db_order.exchange_status = exchange_order['status']
            
            # Check if fully filled
            total_size = float(db_order.size or 0)
            if exchange_filled >= total_size * 0.999999:  # Allow for rounding
                # Transition to FILLED
                transition_to_filled(
                    execution_id=db_order.execution_id,
                    filled_size=exchange_filled,
                    avg_price=exchange_order['avg_price'],
                    exchange_order_id=db_order.order_id or "unknown",
                    reason="Reconciled: Order fully filled on exchange"
                )
                db_order.status = ExecutionStatus.COMPLETED
                db_order.filled_at = datetime.utcnow()
            else:
                # Transition to PARTIALLY_FILLED
                transition_to_partial_fill(
                    execution_id=db_order.execution_id,
                    filled_size=exchange_filled,
                    remaining_size=exchange_order['remaining_size'],
                    avg_price=exchange_order['avg_price'],
                    exchange_order_id=db_order.order_id or "unknown",
                    reason="Reconciled: Updated fill data from exchange"
                )
            
            self.db.commit()
            
            return ReconciliationResult(
                execution_id=db_order.execution_id,
                action=ReconciliationAction.UPDATE_FILL_DATA,
                old_state=f"filled={db_filled}",
                new_state=f"filled={exchange_filled}",
                exchange_order_id=db_order.order_id,
                filled_size=exchange_filled,
                avg_price=exchange_order['avg_price'],
                discrepancy=f"Fill mismatch: DB={db_filled}, Exchange={exchange_filled}"
            )
        
        # No mismatch
        return ReconciliationResult(
            execution_id=db_order.execution_id,
            action=ReconciliationAction.NO_ACTION,
            old_state=None,
            new_state=None,
            exchange_order_id=db_order.order_id,
            filled_size=exchange_filled,
            avg_price=exchange_order['avg_price']
        )
    
    def _update_sync_timestamp(self, execution_ids: List[str]):
        """Update last_exchange_sync timestamp for reconciled orders."""
        if not execution_ids:
            return
        
        self.db.execute(
            text("""
                UPDATE execution_records
                SET last_exchange_sync = CURRENT_TIMESTAMP
                WHERE execution_id = ANY(:execution_ids)
            """),
            {"execution_ids": execution_ids}
        )
        self.db.commit()
    
    async def start_reconciliation_loop(
        self,
        tenant_id: str,
        interval_seconds: int = 5,
        exchange_id: Optional[str] = None
    ):
        """
        STEP 3.5: Start continuous reconciliation loop.
        
        Runs every `interval_seconds` to keep system in sync with exchange.
        
        Args:
            tenant_id: Tenant to reconcile
            interval_seconds: Seconds between reconciliation runs (default: 5)
            exchange_id: Optional exchange filter
        """
        self._running = True
        logger.info(
            f"Starting reconciliation loop for tenant {tenant_id} "
            f"(interval={interval_seconds}s)"
        )
        
        while self._running:
            try:
                start_time = datetime.utcnow()
                
                # Run reconciliation
                results = await self.reconcile_open_orders(tenant_id, exchange_id)
                
                # Log summary
                discrepancies = [r for r in results if r.action != ReconciliationAction.NO_ACTION]
                if discrepancies:
                    logger.warning(
                        f"Reconciliation found {len(discrepancies)} discrepancies: "
                        f"{[r.action.value for r in discrepancies]}"
                    )
                
                # Calculate sleep time to maintain interval
                elapsed = (datetime.utcnow() - start_time).total_seconds()
                sleep_time = max(0, interval_seconds - elapsed)
                
                await asyncio.sleep(sleep_time)
                
            except Exception as e:
                logger.error(f"Reconciliation loop error: {e}", exc_info=True)
                await asyncio.sleep(interval_seconds)  # Continue after error
    
    def stop_reconciliation_loop(self):
        """Stop the reconciliation loop."""
        self._running = False
        logger.info("Reconciliation loop stopping...")
        
        if self._reconciliation_task and not self._reconciliation_task.done():
            self._reconciliation_task.cancel()
    
    async def check_order_exists_on_exchange(
        self,
        order_id: str,
        tenant_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        STEP 3.7: Check if order exists on exchange before retry.
        
        This prevents duplicate submissions when retrying failed orders.
        
        Args:
            order_id: Exchange order ID
            tenant_id: Tenant UUID
            
        Returns:
            Order data if exists, None if not found
        """
        if not self.exchange:
            logger.warning("No exchange client, cannot check order existence")
            return None
        
        try:
            # Try to fetch order from exchange
            order = await self.exchange.fetch_order(order_id)
            
            if order:
                logger.info(f"Order {order_id} exists on exchange")
                return {
                    'order_id': order.get('id'),
                    'status': order.get('status'),
                    'filled': float(order.get('filled', 0)),
                    'remaining': float(order.get('remaining', 0)),
                    'average': float(order.get('average', 0))
                }
            
            return None
            
        except Exception as e:
            # Order not found or error
            logger.debug(f"Order {order_id} not found on exchange: {e}")
            return None


# Global service instance
_reconciliation_service: Optional[ExchangeReconciliationService] = None


def get_reconciliation_service(
    db_session: Session,
    exchange_client: Optional[Any] = None
) -> ExchangeReconciliationService:
    """Get or create global reconciliation service."""
    global _reconciliation_service
    if _reconciliation_service is None:
        _reconciliation_service = ExchangeReconciliationService(db_session, exchange_client)
    return _reconciliation_service
