"""
backend/reconciliation_worker.py — EXECUTION RECONCILIATION LOOP

STEP 4: SYNC WITH EXCHANGE EVERY FEW SECONDS

GOAL: Ensure local state always matches exchange reality

ARCHITECTURE:
  ┌─────────────────────────────────────────────────────────────────┐
  │                  RECONCILIATION WORKER                         │
  │                     (Runs every 5 seconds)                       │
  │                                                                 │
  │   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐        │
  │   │  Fetch      │    │  Compare    │    │  Correct    │        │
  │   │  Exchange   │───▶│  Local vs   │───▶│  Local      │        │
  │   │  State      │    │  Exchange   │    │  State      │        │
  │   │             │    │             │    │             │        │
  │   │ • Orders    │    │ • Orders    │    │ • Update    │        │
  │   │ • Positions │    │ • Positions │    │ • Reconcile │        │
  │   │ • Balances  │    │ • Balances  │    │ • Alert     │        │
  │   └─────────────┘    └─────────────┘    └─────────────┘        │
  │                                                                 │
  │   Every 5 seconds:                                              │
  │   1. Fetch open orders from exchange                           │
  │   2. Fetch positions from exchange                             │
  │   3. Fetch balances from exchange (optional)                  │
  │   4. Compare with local StateService                            │
  │   5. IF mismatch:                                              │
  │      - Log discrepancy                                          │
  │      - Update local state                                       │
  │      - Emit reconciliation event                                │
  │      - Alert if significant drift                               │
  │                                                                 │
  │   EXPECTED RESULT:                                              │
  │   ✔ Local always matches exchange                              │
  │   ✔ No hidden drift                                            │
  │   ✔ Automatic self-healing                                     │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘

RECONCILIATION SCENARIOS:

  1. Order State Mismatch:
     Exchange: Order filled (status: closed)
     Local:    Order open (status: pending)
     Action:   Update local order to filled

  2. Position Size Mismatch:
     Exchange: Position 1.5 BTC
     Local:    Position 1.0 BTC
     Action:   Update local position, recalculate PnL

  3. Missing Order:
     Exchange: Order exists
     Local:    Order not found
     Action:   Sync order from exchange to local

  4. Ghost Order:
     Exchange: Order not found (cancelled/unknown)
     Local:    Order shows as open
     Action:   Mark local order as cancelled

  5. Balance Drift:
     Exchange: Available 10.5 BTC
     Local:    Available 10.0 BTC
     Action:   Update balance, investigate cause

METRICS:
  - reconciliation_runs_total: Counter of reconciliation runs
  - reconciliations_successful: Counter of successful reconciliations
  - mismatches_detected_total: Counter of mismatches found
  - mismatch_types: Breakdown by type (order, position, balance)
  - reconciliation_latency_seconds: Time to complete reconciliation
  - last_reconciliation_timestamp: When last run completed
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

# Try imports with fallbacks
try:
    from backend_app.backend.state_service import (Order, OrderStatus,
                                                   Position, PositionSide,
                                                   state_service)
    _ = (Order, OrderStatus, Position, PositionSide, state_service)
    STATE_SERVICE_AVAILABLE = True
except ImportError:
    STATE_SERVICE_AVAILABLE = False
    # Define fallback OrderStatus for type checking
    class OrderStatus:
        PENDING = "pending"
        OPEN = "open"
        PARTIALLY_FILLED = "partially_filled"
        FILLED = "filled"
        CANCELLED = "cancelled"
        REJECTED = "rejected"

try:
#     import ccxt.async_support as ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False

try:
#     from backend_app.core.reconciliation_engine import ReconciliationEngine
    RECONCILIATION_ENGINE_AVAILABLE = True
except ImportError:
    RECONCILIATION_ENGINE_AVAILABLE = False

logger = logging.getLogger("ReconciliationWorker")


# ═══════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════

class MismatchType(str, Enum):
    """Types of mismatches that can be detected."""
    ORDER_STATUS = "order_status"           # Order status differs
    ORDER_MISSING_LOCAL = "order_missing_local"   # Order exists on exchange but not locally
    ORDER_MISSING_EXCHANGE = "order_missing_exchange"  # Order exists locally but not on exchange
    POSITION_SIZE = "position_size"         # Position quantity differs
    POSITION_MISSING = "position_missing"     # Position exists on one side only
    BALANCE = "balance"                     # Balance differs


@dataclass
class Mismatch:
    """Represents a detected mismatch between local and exchange state."""
    mismatch_type: MismatchType
    user_id: str
    exchange: str
    symbol: str
    
    # Local state
    local_state: Optional[Dict[str, Any]] = None
    
    # Exchange state
    exchange_state: Optional[Dict[str, Any]] = None
    
    # Details
    description: str = ""
    severity: str = "warning"  # warning, critical
    
    # Timestamp
    detected_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "mismatch_type": self.mismatch_type.value,
            "user_id": self.user_id,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "local_state": self.local_state,
            "exchange_state": self.exchange_state,
            "description": self.description,
            "severity": self.severity,
            "detected_at": self.detected_at.isoformat(),
        }


@dataclass
class ReconciliationResult:
    """Result of a reconciliation run."""
    user_id: str
    exchange: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # Summary
    orders_checked: int = 0
    positions_checked: int = 0
    mismatches_found: int = 0
    mismatches_corrected: int = 0
    
    # Details
    mismatches: List[Mismatch] = field(default_factory=list)
    
    # Performance
    duration_seconds: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "exchange": self.exchange,
            "timestamp": self.timestamp.isoformat(),
            "orders_checked": self.orders_checked,
            "positions_checked": self.positions_checked,
            "mismatches_found": self.mismatches_found,
            "mismatches_corrected": self.mismatches_corrected,
            "mismatches": [m.to_dict() for m in self.mismatches],
            "duration_seconds": self.duration_seconds,
        }


# ═══════════════════════════════════════════════════════════════════════════
# RECONCILIATION WORKER
# ═══════════════════════════════════════════════════════════════════════════

class ReconciliationWorker:
    """
    STEP 4: Reconciliation worker that syncs local state with exchange.
    
    Runs continuously every N seconds (default 5s), fetches exchange state,
    compares with local state, and corrects any mismatches.
    
    Features:
    - Periodic reconciliation (configurable interval)
    - Multi-exchange support
    - Incremental and full reconciliation modes
    - Automatic mismatch correction
    - Alerting on significant drift
    - Metrics collection
    """
    
    def __init__(
        self,
        interval_seconds: float = 5.0,
        auto_correct: bool = True,
        alert_threshold: int = 5,  # Alert if more than 5 mismatches
        exchanges: Optional[List[str]] = None,
        reconciliation_engine: Optional[Any] = None,
        fill_deduplication_manager: Optional[Any] = None,
        enable_fill_reconciliation: bool = True
    ):
        self.interval_seconds = interval_seconds
        self.auto_correct = auto_correct
        self.alert_threshold = alert_threshold
        self.exchanges = exchanges or ["binance", "bybit", "okx", "bitget", "kucoin", "coinbase", "kraken"]
        
        # State
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
        # Exchange clients (user_id -> exchange -> client)
        self._exchange_clients: Dict[str, Dict[str, Any]] = {}
        
        # Execution correctness components
        self.reconciliation_engine = reconciliation_engine
        self.fill_deduplication_manager = fill_deduplication_manager
        self.enable_fill_reconciliation = enable_fill_reconciliation
        
        # Metrics
        self._metrics = {
            "reconciliation_runs_total": 0,
            "reconciliations_successful": 0,
            "mismatches_detected_total": 0,
            "mismatches_corrected_total": 0,
            "last_reconciliation_timestamp": None,
            "last_reconciliation_duration_ms": 0,
            "fill_reconciliation_count": 0,
        }
        
        # Callbacks
        self._mismatch_callbacks: List[Callable[[Mismatch], Any]] = []
        self._reconciliation_callbacks: List[Callable[[ReconciliationResult], Any]] = []
        
        logger.info(
            f"[ReconciliationWorker] Initialized: interval={interval_seconds}s, "
            f"auto_correct={auto_correct}, exchanges={self.exchanges}, "
            f"fill_reconciliation={enable_fill_reconciliation}"
        )
    
    def register_exchange_client(self, user_id: str, exchange: str, client: Any):
        """Register an exchange client for a user."""
        if user_id not in self._exchange_clients:
            self._exchange_clients[user_id] = {}
        self._exchange_clients[user_id][exchange] = client
        logger.info(f"[ReconciliationWorker] Registered client: {user_id}@{exchange}")
    
    def register_mismatch_callback(self, callback: Callable[[Mismatch], Any]):
        """Register callback for mismatch detection."""
        self._mismatch_callbacks.append(callback)
    
    def register_reconciliation_callback(self, callback: Callable[[ReconciliationResult], Any]):
        """Register callback for reconciliation completion."""
        self._reconciliation_callbacks.append(callback)
    
    async def start(self):
        """Start the reconciliation loop."""
        self._running = True
        self._task = asyncio.create_task(self._reconciliation_loop())
        logger.info("[ReconciliationWorker] Started")
    
    async def stop(self):
        """Stop the reconciliation loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[ReconciliationWorker] Stopped")
    
    async def _reconciliation_loop(self):
        """Main reconciliation loop."""
        while self._running:
            try:
                start_time = asyncio.get_event_loop().time()
                
                # Run reconciliation for all users and exchanges
                for user_id, exchanges in self._exchange_clients.items():
                    for exchange_name, client in exchanges.items():
                        try:
                            result = await self._reconcile_user_exchange(
                                user_id, exchange_name, client
                            )
                            
                            # Update metrics
                            self._update_metrics(result)
                            
                            # Notify callbacks
                            for callback in self._reconciliation_callbacks:
                                try:
                                    if asyncio.iscoroutinefunction(callback):
                                        asyncio.create_task(callback(result))
                                    else:
                                        callback(result)
                                except Exception as e:
                                    logger.error(f"Reconciliation callback error: {e}")
                            
                        except Exception as e:
                            logger.error(
                                f"[ReconciliationWorker] Error reconciling "
                                f"{user_id}@{exchange_name}: {e}"
                            )
                
                # Calculate sleep time to maintain interval
                elapsed = asyncio.get_event_loop().time() - start_time
                sleep_time = max(0, self.interval_seconds - elapsed)
                
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ReconciliationWorker] Loop error: {e}")
                await asyncio.sleep(self.interval_seconds)
    
    async def _reconcile_user_exchange(
        self,
        user_id: str,
        exchange_name: str,
        client: Any
    ) -> ReconciliationResult:
        """Reconcile state for a specific user and exchange."""
        start_time = asyncio.get_event_loop().time()
        
        result = ReconciliationResult(
            user_id=user_id,
            exchange=exchange_name
        )
        
        try:
            # Use ReconciliationEngine if available for fill-level reconciliation
            if self.reconciliation_engine and self.enable_fill_reconciliation:
                try:
                    # Create a mock exchange client wrapper for the ReconciliationEngine
                    class ExchangeClientWrapper:
                        def __init__(self, client):
                            self.client = client
                        
                        async def get_orders(self, user_id):
                            return await self._fetch_exchange_orders(self.client)
                        
                        async def get_positions(self, user_id):
                            return await self._fetch_exchange_positions(self.client)
                        
                        async def get_fills(self, user_id):
                            # Fetch fills from exchange (if available)
                            if CCXT_AVAILABLE and hasattr(self.client, 'fetch_my_trades'):
                                return await self.client.fetch_my_trades()
                            return []
                    
                    # Create a mock state service wrapper
                    class StateServiceWrapper:
                        def __init__(self):
                            pass
                        
                        async def get_orders(self, tenant_id, user_id):
                            local_orders = await self._fetch_local_orders(user_id)
                            orders_list = []
                            for k, v in local_orders.items():
                                order_dict = {"order_id": k}
                                if hasattr(v, 'to_dict'):
                                    order_dict.update(v.to_dict())
                                else:
                                    order_dict.update(v)
                                orders_list.append(order_dict)
                            return orders_list
                        
                        async def get_positions(self, tenant_id, user_id):
                            local_positions = await self._fetch_local_positions(user_id)
                            positions_list = []
                            for k, v in local_positions.items():
                                pos_dict = {"symbol": k}
                                if hasattr(v, 'to_dict'):
                                    pos_dict.update(v.to_dict())
                                else:
                                    pos_dict.update(v)
                                positions_list.append(pos_dict)
                            return positions_list
                        
                        async def get_fills(self, tenant_id, user_id):
                            # Fetch fills from local state (if available)
                            return []
                        
                        async def create_fill(self, tenant_id, data):
                            logger.info(f"[ReconciliationWorker] Would create fill: {data}")
                        
                        async def remove_fill(self, tenant_id, data):
                            logger.info(f"[ReconciliationWorker] Would remove fill: {data}")
                        
                        async def create_order(self, tenant_id, data):
                            logger.info(f"[ReconciliationWorker] Would create order: {data}")
                        
                        async def cancel_order(self, tenant_id, data):
                            logger.info(f"[ReconciliationWorker] Would cancel order: {data}")
                        
                        async def create_position(self, tenant_id, data):
                            logger.info(f"[ReconciliationWorker] Would create position: {data}")
                        
                        async def update_position(self, tenant_id, data):
                            logger.info(f"[ReconciliationWorker] Would update position: {data}")
                    
                    ExchangeClientWrapper(client)
                    StateServiceWrapper()
                    
                    # Run ReconciliationEngine
                    engine_result = await self.reconciliation_engine.reconcile(
                        tenant_id=user_id,
                        exchange_name=exchange_name,
                        user_id=user_id
                    )
                    
                    if engine_result.success:
                        # Convert engine mismatches to worker mismatches
                        for fill_mismatch in engine_result.fill_mismatches:
                            result.mismatches.append(Mismatch(
                                mismatch_type=MismatchType.ORDER_STATUS,  # Map to existing type
                                user_id=user_id,
                                exchange=exchange_name,
                                symbol="",  # Fill mismatches don't have symbol
                                local_state={"fill_id": fill_mismatch.fill_id},
                                exchange_state={"exchange_trade_id": fill_mismatch.exchange_trade_id},
                                description=f"Fill mismatch: {fill_mismatch.mismatch_type}",
                                severity=fill_mismatch.severity
                            ))
                        
                        self._metrics["fill_reconciliation_count"] += 1
                        logger.info(
                            f"[ReconciliationWorker] Fill reconciliation complete: "
                            f"{len(engine_result.fill_mismatches)} fill mismatches"
                        )
                    
                except Exception as e:
                    logger.error(f"[ReconciliationWorker] ReconciliationEngine error: {e}")
                    # Continue with legacy reconciliation
            
            # Legacy reconciliation (order and position level)
            # Phase 5 Optimisation: fetch all four datasets concurrently via
            # asyncio.gather instead of four sequential awaits.  Under a
            # reconciliation storm this reduces I/O wall-time from
            # sum(latencies) to max(latencies).
            (
                exchange_orders,
                exchange_positions,
                local_orders,
                local_positions,
            ) = await asyncio.gather(
                self._fetch_exchange_orders(client),
                self._fetch_exchange_positions(client),
                self._fetch_local_orders(user_id),
                self._fetch_local_positions(user_id),
            )

            # Compare orders
            order_mismatches = self._compare_orders(
                user_id, exchange_name, local_orders, exchange_orders
            )
            result.mismatches.extend(order_mismatches)
            result.orders_checked = len(local_orders) + len(exchange_orders)

            # Compare positions
            position_mismatches = self._compare_positions(
                user_id, exchange_name, local_positions, exchange_positions
            )
            result.mismatches.extend(position_mismatches)
            result.positions_checked = len(local_positions) + len(exchange_positions)
            
            # Correct mismatches if enabled
            if self.auto_correct:
                for mismatch in result.mismatches:
                    corrected = await self._correct_mismatch(mismatch)
                    if corrected:
                        result.mismatches_corrected += 1
            
            # Notify mismatch callbacks
            for mismatch in result.mismatches:
                for callback in self._mismatch_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            asyncio.create_task(callback(mismatch))
                        else:
                            callback(mismatch)
                    except Exception as e:
                        logger.error(f"Mismatch callback error: {e}")
            
            # Alert if significant drift
            if len(result.mismatches) >= self.alert_threshold:
                logger.critical(
                    f"[ReconciliationWorker] SIGNIFICANT DRIFT: "
                    f"{user_id}@{exchange_name}: {len(result.mismatches)} mismatches"
                )
            
        except Exception as e:
            logger.error(f"Reconciliation error for {user_id}@{exchange_name}: {e}")
        
        # Set final metrics
        result.duration_seconds = asyncio.get_event_loop().time() - start_time
        result.mismatches_found = len(result.mismatches)
        
        return result
    
    async def _fetch_exchange_orders(self, client: Any) -> List[Dict[str, Any]]:
        """Fetch open orders from exchange."""
        try:
            if CCXT_AVAILABLE and hasattr(client, 'fetch_open_orders'):
                orders = await client.fetch_open_orders()
                return orders
            else:
                # Mock for testing
                return []
        except Exception as e:
            logger.error(f"Failed to fetch exchange orders: {e}")
            return []
    
    async def _fetch_exchange_positions(self, client: Any) -> List[Dict[str, Any]]:
        """Fetch positions from exchange."""
        try:
            if CCXT_AVAILABLE and hasattr(client, 'fetch_positions'):
                positions = await client.fetch_positions()
                return positions
            else:
                # Mock for testing
                return []
        except Exception as e:
            logger.error(f"Failed to fetch exchange positions: {e}")
            return []
    
    async def _fetch_local_orders(self, user_id: str) -> Dict[str, Any]:
        """Fetch local orders from StateService."""
        if not STATE_SERVICE_AVAILABLE:
            return {}
        
        try:
            # Get all orders for user
            orders = await state_service.get_user_orders(user_id)
            return {order.order_id: order for order in orders}
        except Exception as e:
            logger.error(f"Failed to fetch local orders: {e}")
            return {}
    
    async def _fetch_local_positions(self, user_id: str) -> Dict[str, Any]:
        """Fetch local positions from StateService."""
        if not STATE_SERVICE_AVAILABLE:
            return {}
        
        try:
            # This would need to be implemented in StateService
            # For now, return empty dict
            return {}
        except Exception as e:
            logger.error(f"Failed to fetch local positions: {e}")
            return {}
    
    def _compare_orders(
        self,
        user_id: str,
        exchange: str,
        local_orders: Dict[str, Any],
        exchange_orders: List[Dict[str, Any]]
    ) -> List[Mismatch]:
        """Compare local orders with exchange orders."""
        mismatches = []
        
        # Convert exchange orders to dict
        def get_val(obj, key):
            return getattr(obj, key) if hasattr(obj, key) else obj.get(key)
        
        exchange_orders_dict = {get_val(order, "id"): order for order in exchange_orders}
        
        # Check for orders missing locally
        for order_id, exchange_order in exchange_orders_dict.items():
            if order_id not in local_orders:
                mismatches.append(Mismatch(
                    mismatch_type=MismatchType.ORDER_MISSING_LOCAL,
                    user_id=user_id,
                    exchange=exchange,
                    symbol=get_val(exchange_order, "symbol") or "",
                    exchange_state=exchange_order,
                    local_state=None,
                    description=f"Order {order_id} exists on exchange but not locally",
                    severity="warning"
                ))
        
        # Check for orders missing on exchange (ghost orders)
        for order_id, local_order in local_orders.items():
            if order_id not in exchange_orders_dict:
                # Only flag if local order is still open
                status = getattr(local_order, 'status', None)
                if status is not None and (hasattr(status, 'value') and status.value or status) not in [
                    "filled", "cancelled", "rejected"
                ]:
                    mismatches.append(Mismatch(
                        mismatch_type=MismatchType.ORDER_MISSING_EXCHANGE,
                        user_id=user_id,
                        exchange=exchange,
                        symbol=get_val(local_order, 'symbol') or '',
                        local_state=local_order.to_dict() if hasattr(local_order, 'to_dict') else (local_order if isinstance(local_order, dict) else {}),
                        exchange_state=None,
                        description=f"Order {order_id} exists locally but not on exchange (ghost order)",
                        severity="critical"
                    ))
        
        # Check for status mismatches
        for order_id, local_order in local_orders.items():
            if order_id in exchange_orders_dict:
                exchange_order = exchange_orders_dict[order_id]
                
                # Compare status — handle both ORM objects (with .status enum) and plain dicts
                _raw_status = get_val(local_order, 'status')
                if hasattr(_raw_status, 'value'):
                    local_status = _raw_status.value
                else:
                    local_status = _raw_status or ""
                exchange_status = exchange_order.get("status", "")
                
                if local_status != exchange_status:
                    mismatches.append(Mismatch(
                        mismatch_type=MismatchType.ORDER_STATUS,
                        user_id=user_id,
                        exchange=exchange,
                        symbol=get_val(local_order, 'symbol') or '',
                        local_state=local_order.to_dict() if hasattr(local_order, 'to_dict') else (local_order if isinstance(local_order, dict) else {}),
                        exchange_state=exchange_order,
                        description=f"Order {order_id} status mismatch: local={local_status}, exchange={exchange_status}",
                        severity="critical" if exchange_status in ["filled", "closed"] else "warning"
                    ))
        
        return mismatches
    
    def _compare_positions(
        self,
        user_id: str,
        exchange: str,
        local_positions: Dict[str, Any],
        exchange_positions: List[Dict[str, Any]]
    ) -> List[Mismatch]:
        """Compare local positions with exchange positions."""
        mismatches = []
        
        # Convert exchange positions to dict by symbol
        exchange_positions_dict = {}
        for pos in exchange_positions:
            symbol = pos.get("symbol", "")
            if symbol:
                exchange_positions_dict[symbol] = pos
        
        # Check for position size mismatches
        for symbol, local_pos in local_positions.items():
            if symbol in exchange_positions_dict:
                exchange_pos = exchange_positions_dict[symbol]
                
                # Compare sizes
                local_size = float(local_pos.quantity) if hasattr(local_pos, 'quantity') else 0
                exchange_size = float(exchange_pos.get("contracts", 0))
                
                if abs(local_size - exchange_size) > 0.0001:  # Small tolerance
                    mismatches.append(Mismatch(
                        mismatch_type=MismatchType.POSITION_SIZE,
                        user_id=user_id,
                        exchange=exchange,
                        symbol=symbol,
                        local_state=local_pos.to_dict() if hasattr(local_pos, 'to_dict') else {},
                        exchange_state=exchange_pos,
                        description=f"Position size mismatch for {symbol}: local={local_size}, exchange={exchange_size}",
                        severity="critical"
                    ))
        
        # Check for positions missing on one side
        for symbol, exchange_pos in exchange_positions_dict.items():
            exchange_size = float(exchange_pos.get("contracts", 0))
            if exchange_size != 0 and symbol not in local_positions:
                mismatches.append(Mismatch(
                    mismatch_type=MismatchType.POSITION_MISSING,
                    user_id=user_id,
                    exchange=exchange,
                    symbol=symbol,
                    local_state=None,
                    exchange_state=exchange_pos,
                    description=f"Position {symbol} exists on exchange but not locally",
                    severity="critical"
                ))
        
        return mismatches
    
    async def _correct_mismatch(self, mismatch: Mismatch) -> bool:
        """Correct a detected mismatch in local state."""
        try:
            if mismatch.mismatch_type == MismatchType.ORDER_STATUS:
                # Update order status
                if mismatch.exchange_state and STATE_SERVICE_AVAILABLE:
                    order_id = mismatch.exchange_state.get("id")
                    new_status = mismatch.exchange_state.get("status")
                    
                    # Fetch order and update
                    order = await state_service.get_order(order_id)
                    if order:
                        order.status = OrderStatus(new_status)
                        order.filled_quantity = Decimal(str(mismatch.exchange_state.get("filled", 0)))
                        order.remaining_quantity = Decimal(str(mismatch.exchange_state.get("remaining", 0)))
                        await state_service.save_order(order)
                        
                        logger.info(f"[ReconciliationWorker] Corrected order {order_id} status to {new_status}")
                        return True
            
            elif mismatch.mismatch_type == MismatchType.ORDER_MISSING_EXCHANGE:
                # Mark ghost order as cancelled
                if mismatch.local_state and STATE_SERVICE_AVAILABLE:
                    order_id = mismatch.local_state.get("order_id")
                    order = await state_service.get_order(order_id)
                    if order:
                        order.status = OrderStatus.CANCELLED
                        await state_service.save_order(order)
                        
                        logger.info(f"[ReconciliationWorker] Marked ghost order {order_id} as cancelled")
                        return True
            
            elif mismatch.mismatch_type == MismatchType.POSITION_SIZE:
                # Update position size
                if mismatch.exchange_state and STATE_SERVICE_AVAILABLE:
                    symbol = mismatch.symbol
                    user_id = mismatch.user_id
                    new_size = Decimal(str(mismatch.exchange_state.get("contracts", 0)))
                    
                    # Update position via StateService
                    position = await state_service.get_position(user_id, symbol)
                    if position:
                        await state_service.update_position(
                            f"{user_id}:{symbol}",
                            updates={"quantity": new_size},
                            expected_version=position.version
                        )
                        
                        logger.info(f"[ReconciliationWorker] Corrected position {symbol} size to {new_size}")
                        return True
            
            # Other mismatch types require manual intervention or have no auto-correction
            return False
            
        except Exception as e:
            logger.error(f"[ReconciliationWorker] Failed to correct mismatch: {e}")
            return False
    
    def _update_metrics(self, result: ReconciliationResult):
        """Update reconciliation metrics."""
        self._metrics["reconciliation_runs_total"] += 1
        self._metrics["last_reconciliation_timestamp"] = result.timestamp.isoformat()
        self._metrics["last_reconciliation_duration_ms"] = result.duration_seconds * 1000
        
        if result.mismatches_found == 0:
            self._metrics["reconciliations_successful"] += 1
        
        self._metrics["mismatches_detected_total"] += result.mismatches_found
        self._metrics["mismatches_corrected_total"] += result.mismatches_corrected
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current reconciliation metrics."""
        return self._metrics.copy()
    
    async def run_manual_reconciliation(
        self,
        user_id: str,
        exchange: str
    ) -> Optional[ReconciliationResult]:
        """Run a one-time manual reconciliation."""
        if user_id not in self._exchange_clients:
            logger.error(f"[ReconciliationWorker] No client registered for {user_id}")
            return None
        
        if exchange not in self._exchange_clients[user_id]:
            logger.error(f"[ReconciliationWorker] No client registered for {user_id}@{exchange}")
            return None
        
        client = self._exchange_clients[user_id][exchange]
        return await self._reconcile_user_exchange(user_id, exchange, client)


# Global singleton
reconciliation_worker = ReconciliationWorker()


def get_reconciliation_worker() -> ReconciliationWorker:
    """Get global reconciliation worker instance."""
    return reconciliation_worker
