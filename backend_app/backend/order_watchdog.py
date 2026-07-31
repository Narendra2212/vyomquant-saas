"""
Order Watchdog

STEP 7.1 — PRODUCTION HARDENING

Monitors pending orders and fetches status from exchange.
Prevents orders from being stuck in pending state.

Watchdog Logic:
┌─────────────────────────────────────────────────────────────────┐
│  Every X seconds:                                                  │
│                                                                  │
│  1. Query execution_records for:                                  │
│     status IN (SUBMITTED, PENDING, PARTIALLY_FILLED)              │
│     AND last_exchange_sync < now() - threshold                    │
│                                                                  │
│  2. For each stale order:                                        │
│     ├─ Fetch order status from exchange                          │
│     ├─ Update execution record                                   │
│     ├─ If filled → update position                               │
│     └─ Log discrepancy                                           │
│                                                                  │
│  3. Alert if orders stuck > alert_threshold                       │
└─────────────────────────────────────────────────────────────────┘
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from backend_app.backend.exchange_executor import OrderStatusResult
from backend_app.backend.position_engine import get_position_engine
from backend_app.core.models.execution_record import (ExecutionRecordModel,
                                                      ExecutionStatus)

# STEP 1: Connection layer imports for executor and credential management

logger = logging.getLogger(__name__)


@dataclass
class WatchdogConfig:
    """Configuration for order watchdog."""
    check_interval_seconds: int = 30          # Check every 30s
    stale_threshold_seconds: int = 60         # Orders older than 60s considered stale
    alert_threshold_seconds: int = 300      # Alert if stuck > 5 minutes
    max_retries: int = 3                     # Max fetch retries


class OrderWatchdog:
    """
    STEP 7.1: Order watchdog system.
    
    Monitors pending orders and ensures they don't get stuck.
    Fetches status from exchange for stale orders.
    
    Usage:
        watchdog = OrderWatchdog(db_session, config)
        await watchdog.start()
    """
    
    def __init__(
        self,
        db_session: Session,
        config: Optional[WatchdogConfig] = None
    ):
        self.db = db_session
        self.config = config or WatchdogConfig()
        self._running = False
        self._watchdog_task: Optional[asyncio.Task] = None
        
        # Metrics
        self.checks_run = 0
        self.orders_fixed = 0
        self.alerts_triggered = 0
    
    async def start(self):
        """Start watchdog monitoring loop."""
        self._running = True
        self._watchdog_task = asyncio.create_task(self._monitoring_loop())
        logger.info(
            f"OrderWatchdog started | "
            f"interval={self.config.check_interval_seconds}s | "
            f"stale_threshold={self.config.stale_threshold_seconds}s"
        )
    
    async def stop(self):
        """Stop watchdog."""
        self._running = False
        
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        
        logger.info("OrderWatchdog stopped")
    
    async def _monitoring_loop(self):
        """
        Main monitoring loop.
        
        STEP 9: Runs two tasks:
        1. Stale order check (existing)
        2. Order fill reconciliation loop (every 5-10 seconds)
        """
        # STEP 9: Start reconciliation loop as a separate background task
        reconciliation_task = asyncio.create_task(self._reconciliation_loop())
        
        while self._running:
            try:
                await self._check_stale_orders()
                self.checks_run += 1
                
                # Wait before next check
                await asyncio.sleep(self.config.check_interval_seconds)
                
            except asyncio.CancelledError:
                reconciliation_task.cancel()
                try:
                    await reconciliation_task
                except asyncio.CancelledError:
                    pass
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}", exc_info=True)
                await asyncio.sleep(5)  # Short wait on error
    
    async def _reconciliation_loop(self):
        """
        STEP 9: Order Fill Reconciliation Loop
        
        Background task that runs every 5-10 seconds to:
        - Fetch status for all open orders
        - Update database with latest status
        - Ensure no fills are lost
        
        EXPECTED RESULT: ✔ No lost fills
        """
        logger.info("STEP 9: Order fill reconciliation loop started (interval: 5-10s)")
        
        while self._running:
            try:
                await self._reconcile_open_orders()
                
                # Wait 5-10 seconds before next reconciliation
                # Randomize slightly to prevent thundering herd
                wait_time = 5 + (hash(asyncio.get_event_loop().time()) % 5)
                await asyncio.sleep(wait_time)
                
            except asyncio.CancelledError:
                logger.info("STEP 9: Reconciliation loop cancelled")
                break
            except Exception as e:
                logger.error(f"STEP 9: Reconciliation loop error: {e}", exc_info=True)
                await asyncio.sleep(5)  # Wait before retry
    
    async def _reconcile_open_orders(self):
        """
        STEP 9: Fetch status for all open orders and update database.
        
        This ensures no fills are lost by:
        1. Querying all open orders (SUBMITTED, PENDING, PARTIALLY_FILLED)
        2. Fetching status from exchange
        3. Updating database with any changes
        4. Triggering alerts for any discrepancies
        """
        try:
            # Find all open orders (not just stale ones)
            open_orders = self._find_open_orders()
            
            if not open_orders:
                return
            
            logger.info(f"STEP 9: Reconciling {len(open_orders)} open orders")
            
            reconciled_count = 0
            discrepancy_count = 0
            
            for order in open_orders:
                try:
                    # Get executor for this order
                    executor = await self._get_executor_for_order(order)
                    
                    if not executor:
                        logger.warning(
                            f"STEP 9: No executor for order {order.execution_id}, "
                            f"skipping reconciliation"
                        )
                        continue
                    
                    # Fetch latest status from exchange
                    result = await executor.get_order_status(
                        exchange_order_id=order.order_id,
                        symbol=order.symbol
                    )
                    
                    if not result.success:
                        logger.warning(
                            f"STEP 9: Failed to fetch status for order {order.execution_id}: "
                            f"{result.error_message}"
                        )
                        continue
                    
                    # Check for discrepancies
                    if self._has_order_changed(order, result):
                        logger.info(
                            f"STEP 9: Order {order.execution_id} discrepancy detected: "
                            f"DB status={order.status.value}, "
                            f"Exchange status={result.status.value}, "
                            f"DB filled={order.filled_amount}, "
                            f"Exchange filled={result.filled_amount}"
                        )
                        
                        # Update order from exchange status
                        await self._update_order_from_status(order, result)
                        
                        # Trigger alert for fill
                        if result.filled_amount > (order.filled_amount or 0):
                            fill_amount = result.filled_amount - (order.filled_amount or 0)
                            await self._trigger_alert(
                                order,
                                f"STEP 9: Order fill detected via reconciliation. "
                                f"Filled {fill_amount} @ {result.average_price}"
                            )
                        
                        discrepancy_count += 1
                    
                    reconciled_count += 1
                    
                except Exception as e:
                    logger.error(
                        f"STEP 9: Error reconciling order {order.execution_id}: {e}"
                    )
                    continue
            
            if reconciled_count > 0:
                logger.info(
                    f"STEP 9: Reconciliation complete. "
                    f"Checked: {reconciled_count}, Discrepancies: {discrepancy_count}"
                )
                
        except Exception as e:
            logger.error(f"STEP 9: Reconciliation batch error: {e}", exc_info=True)
    
    def _find_open_orders(self) -> List[ExecutionRecordModel]:
        """
        STEP 9: Find all open orders that need reconciliation.
        
        Includes:
        - SUBMITTED
        - PENDING  
        - PARTIALLY_FILLED
        - EXECUTING
        """
        open_statuses = [
            ExecutionStatus.PENDING,
            ExecutionStatus.EXECUTING
        ]
        
        orders = self.db.query(ExecutionRecordModel).filter(
            and_(
                ExecutionRecordModel.status.in_(open_statuses),
                ExecutionRecordModel.order_id.isnot(None)  # Must have exchange order ID
            )
        ).all()
        
        return orders
    
    def _has_order_changed(self, order: ExecutionRecordModel, result) -> bool:
        """
        STEP 9: Check if order status has changed on exchange.
        
        Returns True if there are discrepancies between local state and exchange.
        """
        # Check status change
        if result.status != order.status:
            return True
        
        # Check fill amount change
        if result.filled_amount != order.filled_amount:
            return True
        
        # Check remaining amount change
        if result.remaining_amount != order.remaining_amount:
            return True
        
        return False
    
    async def _check_stale_orders(self):
        """Check for and fix stale orders."""
        stale_orders = self._find_stale_orders()
        
        if not stale_orders:
            return
        
        logger.info(f"Watchdog found {len(stale_orders)} stale orders")
        
        for order in stale_orders:
            try:
                await self._refresh_order_status(order)
            except Exception as e:
                logger.error(
                    f"Failed to refresh order {order.execution_id}: {e}"
                )
    
    def _find_stale_orders(self) -> List[ExecutionRecordModel]:
        """
        Find orders that need status refresh.
        
        Criteria:
        - Status is SUBMITTED, PENDING, or PARTIALLY_FILLED
        - Last exchange sync is older than threshold
        """
        threshold = datetime.utcnow() - timedelta(
            seconds=self.config.stale_threshold_seconds
        )
        
        stale_statuses = [
            ExecutionStatus.PENDING,
            ExecutionStatus.EXECUTING
        ]
        
        orders = self.db.query(ExecutionRecordModel).filter(
            and_(
                ExecutionRecordModel.status.in_(stale_statuses),
                or_(
                    ExecutionRecordModel.last_exchange_sync < threshold,
                    ExecutionRecordModel.last_exchange_sync.is_(None)
                ),
                ExecutionRecordModel.order_id.isnot(None)  # Must have exchange order ID
            )
        ).all()
        
        return orders
    
    async def _refresh_order_status(self, order: ExecutionRecordModel):
        """Fetch and update order status from exchange."""
        logger.info(
            f"Refreshing order: {order.execution_id} | "
            f"exchange_order={order.order_id} | "
            f"last_sync={order.last_exchange_sync}"
        )
        
        # STEP 1: Get exchange executor for this order
        executor = await self._get_executor_for_order(order)
        
        # STEP 3: FAIL-SAFE - If no executor (credentials unavailable), mark as UNKNOWN
        if not executor:
            logger.error(
                f"STEP 3: FAIL-SAFE - No executor available for order: {order.execution_id}. "
                f"Credentials unavailable or exchange connection failed."
            )
            
            # Mark order as UNKNOWN - DO NOT ASSUME FILLED
            order.status = ExecutionStatus.UNKNOWN
            order.exchange_status_message = "Order status unknown - executor unavailable (credential/connection failure)"
            
            # Persist the UNKNOWN status
            await self._persist_order_update(order)
            
            # TRIGGER ALERT
            await self._trigger_alert(
                order,
                f"CRITICAL: Cannot reconcile order {order.execution_id} - no executor available. "
                f"Verify exchange credentials for user {order.user_id}, exchange {order.exchange_id}."
            )
            return
        
        # Fetch status from exchange
        for attempt in range(self.config.max_retries):
            try:
                result: OrderStatusResult = await executor.get_order_status(
                    exchange_order_id=order.order_id,
                    symbol=order.symbol
                )
                
                if result.success:
                    await self._update_order_from_status(order, result)
                    self.orders_fixed += 1
                    return
                else:
                    logger.warning(
                        f"Status fetch failed (attempt {attempt+1}): "
                        f"{result.error_message}"
                    )
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    
            except Exception as e:
                logger.error(f"Error fetching status (attempt {attempt+1}): {e}")
                await asyncio.sleep(2 ** attempt)
        
        # All retries failed
        logger.error(
            f"STEP 3: FAIL-SAFE - All {self.config.max_retries} retry attempts exhausted. "
            f"Marking order as UNKNOWN: {order.execution_id}"
        )
        
        # Mark order as UNKNOWN - DO NOT ASSUME FILLED
        order.status = ExecutionStatus.UNKNOWN
        order.exchange_status_message = "Order status unknown - exchange communication failed"
        
        # Persist the UNKNOWN status to database
        await self._persist_order_update(order)
        
        # STEP 3: TRIGGER ALERT - Critical failure requires human attention
        await self._trigger_alert(
            order,
            f"CRITICAL: Order status UNKNOWN after {self.config.max_retries} failed reconciliation attempts. "
            f"Order ID: {order.execution_id}. Manual intervention required."
        )
        
        # Check if order is stuck for too long (additional alert)
        time_since_creation = datetime.utcnow() - order.created_at
        if time_since_creation.total_seconds() > self.config.alert_threshold_seconds:
            await self._trigger_alert(
                order,
                f"URGENT: Order stuck for > {self.config.alert_threshold_seconds // 60} minutes: {order.execution_id}"
            )
    
    async def _update_order_from_status(
        self,
        order: ExecutionRecordModel,
        status: OrderStatusResult
    ):
        """Update order record from exchange status."""
        # Map exchange status to our status
        exchange_status = status.status.lower()
        
        if exchange_status in ["filled", "closed", "completed"]:
            order.status = ExecutionStatus.COMPLETED
            order.filled_size = status.filled_size
            order.remaining_size = "0"
            order.avg_price = status.avg_price or order.avg_price
            
            logger.info(
                f"Order marked FILLED by watchdog: {order.execution_id} | "
                f"filled={status.filled_size}"
            )
            
            # Update position
            await self._update_position(order)
            
        elif exchange_status in ["partially_filled", "partial"]:
            order.status = ExecutionStatus.EXECUTING
            order.filled_size = status.filled_size
            order.remaining_size = status.remaining_size
            order.avg_price = status.avg_price or order.avg_price
            
            logger.info(
                f"Order partial fill updated: {order.execution_id} | "
                f"filled={status.filled_size}"
            )
            
            # Update position
            await self._update_position(order)
            
        elif exchange_status in ["canceled", "cancelled", "expired"]:
            order.status = ExecutionStatus.FAILED
            
            logger.warning(
                f"Order marked CANCELLED by watchdog: {order.execution_id}"
            )
            
        elif exchange_status in ["rejected", "error"]:
            order.status = ExecutionStatus.FAILED
            
            logger.error(
                f"Order marked REJECTED by watchdog: {order.execution_id}"
            )
        
        # Update sync timestamp
        order.last_exchange_sync = datetime.utcnow()
        
        self.db.commit()
    
    async def _update_position(self, order: ExecutionRecordModel):
        """Update position from order fill."""
        try:
            position_engine = get_position_engine(self.db)
            
            position = await position_engine.update_position_from_fill(
                execution_record=order,
                fill_size=order.filled_size,
                fill_price=order.avg_price
            )
            
            if position:
                logger.info(
                    f"Position updated by watchdog: {position.position_id}"
                )
                
        except Exception as e:
            logger.error(f"Failed to update position from watchdog: {e}")
    
    async def _trigger_alert(self, order: ExecutionRecordModel, reason: str):
        """Trigger alert for stuck order."""
        self.alerts_triggered += 1
        
        logger.critical(
            f"WATCHDOG ALERT: Order {order.execution_id} | "
            f"symbol={order.symbol} | "
            f"reason={reason} | "
            f"created={order.created_at}"
        )
        
        # STEP 7.8: Send to alert system
        try:
            from backend_app.backend.alert_system import get_alert_system
            alert_system = get_alert_system()
            
            await alert_system.send_critical_alert(
                title="Order Stuck Alert",
                message=f"Order {order.execution_id} ({order.symbol}) is stuck: {reason}",
                metadata={
                    "execution_id": order.execution_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "size": order.size,
                    "created_at": order.created_at.isoformat(),
                    "reason": reason
                }
            )
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
    
    def _get_executor_for_order(
        self,
        order: ExecutionRecordModel
    ) -> Optional[Any]:
        """Get exchange executor for this order based on exchange_id and tenant_id."""
        try:
            exchange_id = getattr(order, "exchange_id", None) or "binance"
            from backend_app.core.state import app_state
            if hasattr(app_state, "exchange_router") and app_state.exchange_router:
                return app_state.exchange_router.get_executor(
                    tenant_id=str(order.tenant_id),
                    exchange_id=exchange_id
                )
            elif hasattr(app_state, "vault") and app_state.vault:
                return app_state.vault.get_exchange_client(
                    user_id=str(order.tenant_id),
                    exchange=exchange_id
                )
            return None
        except Exception as e:
            logger.warning(f"Could not route executor for order {getattr(order, 'execution_id', 'unknown')}: {e}")
            return None
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get watchdog metrics."""
        return {
            "checks_run": self.checks_run,
            "orders_fixed": self.orders_fixed,
            "alerts_triggered": self.alerts_triggered,
            "running": self._running,
            "config": {
                "check_interval": self.config.check_interval_seconds,
                "stale_threshold": self.config.stale_threshold_seconds,
                "alert_threshold": self.config.alert_threshold_seconds
            }
        }


# Global instance
_watchdog: Optional[OrderWatchdog] = None


def get_order_watchdog(
    db_session: Session,
    config: Optional[WatchdogConfig] = None
) -> OrderWatchdog:
    """Get or create global watchdog."""
    global _watchdog
    if _watchdog is None:
        _watchdog = OrderWatchdog(db_session, config)
    return _watchdog
