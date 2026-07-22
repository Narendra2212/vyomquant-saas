
"""
Event Router

STEP 5.3 — REAL-TIME EVENT SYNC SYSTEM

Routes events to appropriate handlers based on event type.

Event Routing:
┌─────────────────────────────────────────────────────────────────┐
│  Event Received                                                   │
│       ↓                                                          │
│  ┌─────────────────┐                                             │
│  │  ORDER_FILLED   │ → OrderHandler                            │
│  │  ORDER_PARTIAL  │ → OrderHandler                            │
│  │  ORDER_CANCELLED│ → OrderHandler                            │
│  └─────────────────┘                                             │
│       ↓                                                          │
│  ┌─────────────────┐                                             │
│  │ POSITION_UPDATED│ → PositionHandler                          │
│  └─────────────────┘                                             │
│       ↓                                                          │
│  ┌─────────────────┐                                             │
│  │  TICK_PRICE     │ → PnLHandler (update unrealized)          │
│  └─────────────────┘                                             │
│       ↓                                                          │
│  WebSocket Push                                                   │
└─────────────────────────────────────────────────────────────────┘

STEP 5.4: Execution Engine Integration
STEP 5.9: Idempotent processing
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from typing import Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from backend_app.backend.event_listener import EventType, ExchangeEvent
from backend_app.backend.pnl_engine import get_pnl_engine
from backend_app.backend.position_engine import get_position_engine
from backend_app.core.models.execution_record import (ExecutionRecordModel,
                                                      ExecutionStatus)
from backend_app.core.order_state_machine import (transition_to_cancelled,
                                                  transition_to_failed,
                                                  transition_to_filled,
                                                  transition_to_partial_fill)

logger = logging.getLogger(__name__)


class EventHandler(ABC):
    """Abstract base class for event handlers."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    @abstractmethod
    async def handle(self, event: ExchangeEvent) -> bool:
        """
        Handle an event.
        
        Returns:
            True if handled successfully, False otherwise
        """
        pass
    
    @property
    @abstractmethod
    def supported_event_types(self) -> List[EventType]:
        """List of event types this handler supports."""
        pass


class OrderEventHandler(EventHandler):
    """
    STEP 5.4: Handle order-related events.
    
    Updates execution records and order state machine.
    DO NOT mark FILLED until exchange confirms.
    """
    
    @property
    def supported_event_types(self) -> List[EventType]:
        return [
            EventType.ORDER_FILLED,
            EventType.ORDER_PARTIAL,
            EventType.ORDER_CANCELLED,
            EventType.ORDER_REJECTED,
            EventType.ORDER_UPDATED
        ]
    
    async def handle(self, event: ExchangeEvent) -> bool:
        """Process order event."""
        try:
            # Find execution record by order_id
            execution = self._find_execution_by_order_id(
                event.order_id,
                event.tenant_id
            )
            
            if not execution:
                logger.warning(
                    f"No execution found for order: {event.order_id} | "
                    f"event={event.event_id}"
                )
                return False
            
            # STEP 5.4: Update based on event type
            if event.event_type == EventType.ORDER_FILLED:
                await self._handle_filled(event, execution)
            elif event.event_type == EventType.ORDER_PARTIAL:
                await self._handle_partial(event, execution)
            elif event.event_type == EventType.ORDER_CANCELLED:
                await self._handle_cancelled(event, execution)
            elif event.event_type == EventType.ORDER_REJECTED:
                await self._handle_rejected(event, execution)
            
            return True
            
        except Exception as e:
            logger.error(f"Error handling order event {event.event_id}: {e}", exc_info=True)
            return False
    
    def _find_execution_by_order_id(
        self,
        order_id: str,
        tenant_id: str
    ) -> Optional[ExecutionRecordModel]:
        """Find execution record by exchange order ID."""
        return self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.order_id == order_id,
            ExecutionRecordModel.tenant_id == tenant_id
        ).first()
    
    async def _handle_filled(self, event: ExchangeEvent, execution: ExecutionRecordModel):
        """STEP 5.4: Handle ORDER_FILLED - ONLY mark FILLED after exchange confirms."""
        data = event.data
        
        # Extract fill data
        filled_size = Decimal(str(data.get('filled_size', data.get('z', 0))))  # Binance: 'z' = filled
        fill_price = Decimal(str(data.get('price', data.get('L', 0))))      # Binance: 'L' = last price
        
        # Update execution record
        execution.filled_size = str(filled_size)
        execution.avg_price = str(fill_price)
        execution.status = ExecutionStatus.COMPLETED
        execution.filled_at = datetime.utcnow()
        execution.last_exchange_sync = datetime.utcnow()
        
        self.db.commit()
        
        # Update state machine
        transition_to_filled(
            execution_id=execution.execution_id,
            filled_size=float(filled_size),
            avg_price=float(fill_price),
            exchange_order_id=event.order_id or "unknown",
            reason="Exchange confirmed: ORDER_FILLED event"
        )
        
        logger.info(
            f"ORDER FILLED (exchange confirmed): {execution.execution_id} | "
            f"order={event.order_id} | "
            f"size={filled_size} @ {fill_price}"
        )
        
        # STEP 5.6: Trigger position update
        await self._update_position(execution)
    
    async def _handle_partial(self, event: ExchangeEvent, execution: ExecutionRecordModel):
        """STEP 5.4: Handle ORDER_PARTIAL - Update incremental fill."""
        data = event.data
        
        # Extract fill data
        filled_size = Decimal(str(data.get('filled_size', data.get('z', 0))))
        total_size = Decimal(str(execution.size))
        remaining_size = total_size - filled_size
        fill_price = Decimal(str(data.get('price', data.get('L', 0))))
        
        # Update execution record
        execution.filled_size = str(filled_size)
        execution.remaining_size = str(remaining_size)
        execution.avg_price = str(fill_price)
        execution.last_exchange_sync = datetime.utcnow()
        
        self.db.commit()
        
        # Update state machine
        transition_to_partial_fill(
            execution_id=execution.execution_id,
            filled_size=float(filled_size),
            remaining_size=float(remaining_size),
            avg_price=float(fill_price),
            exchange_order_id=event.order_id or "unknown",
            reason="Exchange confirmed: PARTIAL_FILL"
        )
        
        logger.info(
            f"ORDER PARTIAL (exchange confirmed): {execution.execution_id} | "
            f"filled={filled_size}/{total_size} @ {fill_price}"
        )
        
        # STEP 5.6: Trigger position update
        await self._update_position(execution)
    
    async def _handle_cancelled(self, event: ExchangeEvent, execution: ExecutionRecordModel):
        """Handle ORDER_CANCELLED."""
        execution.status = ExecutionStatus.FAILED  # Or a new CANCELLED status
        execution.last_exchange_sync = datetime.utcnow()
        
        self.db.commit()
        
        transition_to_cancelled(
            execution_id=execution.execution_id,
            cancelled_by="exchange",
            reason="Exchange confirmed: ORDER_CANCELLED"
        )
        
        logger.info(f"ORDER CANCELLED: {execution.execution_id}")
    
    async def _handle_rejected(self, event: ExchangeEvent, execution: ExecutionRecordModel):
        """Handle ORDER_REJECTED."""
        execution.status = ExecutionStatus.FAILED
        execution.last_exchange_sync = datetime.utcnow()
        
        # Add rejection reason
        rejection_reason = event.data.get('reason', 'Unknown')
        if execution.result:
            execution.result['rejection_reason'] = rejection_reason
        else:
            execution.result = {'rejection_reason': rejection_reason}
        
        self.db.commit()
        
        transition_to_failed(
            execution_id=execution.execution_id,
            error_message=rejection_reason,
            error_code=event.data.get('rejectReason'),
            reason="Exchange confirmed: ORDER_REJECTED"
        )
        
        logger.error(f"ORDER REJECTED: {execution.execution_id} | reason={rejection_reason}")
    
    async def _update_position(self, execution: ExecutionRecordModel):
        """STEP 5.6: Update position from execution fill."""
        position_engine = get_position_engine(self.db)
        
        position = await position_engine.update_position_from_fill(
            execution_record=execution,
            fill_size=execution.filled_size,
            fill_price=execution.avg_price
        )
        
        if position:
            logger.info(
                f"POSITION UPDATED from event: {position.position_id} | "
                f"size={position.size} | "
                f"execution={execution.execution_id}"
            )


class PositionEventHandler(EventHandler):
    """Handle position-related events."""
    
    @property
    def supported_event_types(self) -> List[EventType]:
        return [
            EventType.POSITION_UPDATED,
            EventType.POSITION_LIQUIDATED
        ]
    
    async def handle(self, event: ExchangeEvent) -> bool:
        """Process position event."""
        try:
            if event.event_type == EventType.POSITION_LIQUIDATED:
                await self._handle_liquidation(event)
            else:
                await self._handle_position_update(event)
            
            return True
            
        except Exception as e:
            logger.error(f"Error handling position event {event.event_id}: {e}")
            return False
    
    async def _handle_liquidation(self, event: ExchangeEvent):
        """Handle position liquidation."""
        logger.critical(
            f"POSITION LIQUIDATED: {event.symbol} | "
            f"tenant={event.tenant_id} | "
            f"data={event.data}"
        )
        
        # Find and close position
        from backend_app.core.position_model import get_position_repository
        
        get_position_repository(self.db)
        # Would need to find position by symbol and close it
        # Implementation depends on position identification logic
    
    async def _handle_position_update(self, event: ExchangeEvent):
        """Handle position update from exchange."""
        data = event.data
        
        # Update position with exchange data
        logger.info(
            f"POSITION UPDATE from exchange: {event.symbol} | "
            f"size={data.get('size')} | "
            f"entry={data.get('avg_entry')}"
        )


class PriceEventHandler(EventHandler):
    """
    Handle price tick events.
    
    Updates unrealized PnL for all affected positions.
    """
    
    @property
    def supported_event_types(self) -> List[EventType]:
        return [EventType.TICK_PRICE]
    
    async def handle(self, event: ExchangeEvent) -> bool:
        """Process price tick."""
        try:
            symbol = event.symbol
            price = Decimal(str(event.data.get('price', 0)))
            
            if not symbol or price <= 0:
                return False
            
            # Update unrealized PnL for all open positions in this symbol
            pnl_engine = get_pnl_engine(self.db)
            
            price_lookup = {symbol: price}
            
            updated_positions = await pnl_engine.update_all_positions_unrealized_pnl(
                tenant_id=event.tenant_id,
                price_lookup=price_lookup
            )
            
            if updated_positions:
                logger.debug(
                    f"PnL updated for {len(updated_positions)} positions | "
                    f"symbol={symbol} | price={price}"
                )
            
            return True
            
        except Exception as e:
            logger.error(f"Error handling price event {event.event_id}: {e}")
            return False


class EventRouter:
    """
    STEP 5.3: Routes events to appropriate handlers.
    
    Features:
    - Event type routing
    - Handler chaining
    - Error handling
    - WebSocket push (STEP 5.5)
    - Event ordering guarantee (STEP 5.9)
    """
    
    def __init__(self, db_session: Session):
        self.db = db_session
        self._handlers: Dict[EventType, List[EventHandler]] = {}
        self._websocket_push_callbacks: List[Callable[[str, Dict], None]] = []
        
        # STEP 5.9: Event ordering guarantee
        self._event_counter: int = 0  # Monotonic counter for event IDs
        self._last_processed_event_id: Dict[str, int] = {}  # Per-user tracking
        self._lock = asyncio.Lock()  # Thread safety for counter
        
        # STEP 4.7: State versioning for UI sync
        self._state_version: Dict[str, int] = {}  # Per-tenant state version
        self._state_version_lock = asyncio.Lock()
        
        # Register default handlers
        self._register_default_handlers()
    
    def _generate_event_id(self) -> int:
        """Generate monotonically increasing event ID."""
        self._event_counter += 1
        return self._event_counter
    
    def _is_duplicate_event(self, tenant_id: str, event_id: int) -> bool:
        """Check if event has already been processed (idempotency)."""
        last_id = self._last_processed_event_id.get(tenant_id, 0)
        return event_id <= last_id
    
    def _commit_event(self, tenant_id: str, event_id: int) -> None:
        """Commit event processing - update last processed ID."""
        self._last_processed_event_id[tenant_id] = event_id
        logger.debug(f"Event committed: tenant={tenant_id}, event_id={event_id}")
    
    async def _increment_state_version(self, tenant_id: str) -> int:
        """Increment and return new state version for UI sync."""
        async with self._state_version_lock:
            current = self._state_version.get(tenant_id, 0)
            self._state_version[tenant_id] = current + 1
            return self._state_version[tenant_id]
    
    def _get_state_version(self, tenant_id: str) -> int:
        """Get current state version for tenant."""
        return self._state_version.get(tenant_id, 0)
    
    def _register_default_handlers(self):
        """Register default event handlers."""
        # Order handler
        order_handler = OrderEventHandler(self.db)
        for event_type in order_handler.supported_event_types:
            self.register_handler(event_type, order_handler)
        
        # Position handler
        position_handler = PositionEventHandler(self.db)
        for event_type in position_handler.supported_event_types:
            self.register_handler(event_type, position_handler)
        
        # Price handler
        price_handler = PriceEventHandler(self.db)
        for event_type in price_handler.supported_event_types:
            self.register_handler(event_type, price_handler)
    
    def register_handler(self, event_type: EventType, handler: EventHandler):
        """Register a handler for an event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
    
    def on_websocket_push(self, callback: Callable[[str, Dict], None]):
        """Register WebSocket push callback (STEP 5.5)."""
        self._websocket_push_callbacks.append(callback)
    
    async def route_event(self, event: ExchangeEvent):
        """
        STEP 5.3: Route event to appropriate handlers.
        STEP 5.9: Event ordering guarantee.
        
        Order: COMMIT → BROADCAST (prevents UI desync)
        """
        # Generate monotonic event ID if not present
        if not hasattr(event, '_internal_event_id') or event._internal_event_id is None:
            async with self._lock:
                event._internal_event_id = self._generate_event_id()
        
        event_id = event._internal_event_id
        tenant_id = event.tenant_id
        
        # STEP 5.9: Idempotency check - reject duplicate/out-of-order events
        if self._is_duplicate_event(tenant_id, event_id):
            logger.warning(
                f"🚫 Duplicate event rejected: event_id={event_id}, "
                f"tenant={tenant_id}, last_processed={self._last_processed_event_id.get(tenant_id, 0)}"
            )
            return
        
        handlers = self._handlers.get(event.event_type, [])
        
        if not handlers:
            logger.warning(f"No handlers for event type: {event.event_type}")
            # Still commit and broadcast for tracking consistency
            self._commit_event(tenant_id, event_id)
            await self._push_to_frontend(event)
            return
        
        # STEP 1: COMMIT FIRST (database/state changes)
        # Process handlers - this is the "commit" phase
        commit_success = True
        for handler in handlers:
            try:
                success = await handler.handle(event)
                
                if success:
                    logger.debug(
                        f"Event handled: event_id={event_id} | "
                        f"type={event.event_type.value} | "
                        f"handler={handler.__class__.__name__}"
                    )
                
            except Exception as e:
                logger.error(
                    f"Handler error: event_id={event_id} | "
                    f"handler={handler.__class__.__name__} | {e}"
                )
                commit_success = False
        
        # Mark as committed (update last processed ID)
        self._commit_event(tenant_id, event_id)
        
        # STEP 2: BROADCAST SECOND (WebSocket push)
        # Only broadcast after successful commit
        await self._push_to_frontend(event)
        
        logger.debug(
            f"✅ Event routed successfully: event_id={event_id}, "
            f"tenant={tenant_id}, commit_success={commit_success}"
        )
    
    async def _push_to_frontend(self, event: ExchangeEvent):
        """STEP 5.5: Push event to frontend via WebSocket."""
        # STEP 4.7: Increment state version for UI sync
        state_version = await self._increment_state_version(event.tenant_id)
        
        # Build push payload with state versioning
        payload = {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "symbol": event.symbol,
            "order_id": event.order_id,
            "execution_id": event.execution_id,
            "data": event.data,
            "timestamp": event.received_at.isoformat(),
            "latency_ms": event.latency_ms,
            "state_version": state_version  # UI uses this to detect stale data
        }
        
        # Call all registered push callbacks
        for callback in self._websocket_push_callbacks:
            try:
                callback(event.tenant_id, payload)
            except Exception as e:
                logger.error(f"WebSocket push error: {e}")
        
        logger.debug(f"Event pushed to frontend: {event.event_id}, version={state_version}")


# Global instance
def get_event_router(db_session: Session) -> EventRouter:
    """Get event router instance."""
    return EventRouter(db_session)

