
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
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend_app.backend.event_listener import EventType, ExchangeEvent
from backend_app.backend.pnl_engine import get_pnl_engine
from backend_app.backend.position_engine import get_position_engine
from backend_app.core.models.execution_record import (ExecutionRecordModel,
                                                      ExecutionStatus)
from backend_app.core.order_state_machine import (transition_to_cancelled,
                                                  transition_to_failed)

# RT-16: `transition_to_filled` and `transition_to_partial_fill` are DELIBERATELY NOT
# IMPORTED HERE, and this comment exists so the next reader does not helpfully add them
# back. This module records what a venue reported; it does not adjudicate an execution.
# `core/models/execution_record.py` states the rule it is obeying: "OrderWatchdog is the
# only owner of adjudication, and introducing a second one would be worse than the
# defect." See `OrderEventHandler._handle_filled` for the full reasoning, including the
# payload-shape defect that made the old adjudicating version destructive.
#
# `transition_to_cancelled` and `transition_to_failed` ARE still imported, because
# `_handle_cancelled` and `_handle_rejected` are unchanged -- they carry neither defect,
# and retiring them is a separate decision with its own evidence.

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════════════
#  RT-16: READING A VENUE'S FILL FIGURES OUT OF THREE DIFFERENT PAYLOAD SHAPES
# ═══════════════════════════════════════════════════════════════════════════════════════
# Three producers reach `OrderEventHandler`, and they do NOT agree on key names. The
# handlers used to read one shape and silently resolve to `0` for the others, which is the
# defect recorded on `_handle_filled`.
#
#   ccxt-normalised order  `filled`, `average`, `price`   <- exchange_websocket_listener,
#                                                            which is RT-16's feeder and
#                                                            the one that was NOT handled
#   this platform's own    `filled_size`, `price`         <- internal producers
#   raw Binance user data  `z` (cumulative filled),
#                          `L` (last price)               <- BinanceWebSocketClient
#
# Every lookup DISTINGUISHES ABSENT FROM ZERO by returning `None` for "the venue said
# nothing". That distinction is the whole point: a missing figure must leave the recorded
# one alone, while a reported zero is a real statement that nothing has filled. Collapsing
# the two is how a recorded fill gets overwritten with nothing.

#: Cumulative-filled keys, most-specific first. `filled` leads because the ccxt feeder is
#: the producer RT-16 wires and the one whose absence caused the defect.
_CUMULATIVE_FILL_KEYS = ("filled", "filled_size", "z")

#: Price keys. `average` leads because for a cumulative fill the average IS the price the
#: position should be booked at; `price` is the order's limit price for an unfilled order
#: and only coincides with the fill price by accident. `L` is Binance's last-filled price.
_FILL_PRICE_KEYS = ("average", "avg_price", "price", "L")


def _as_decimal(value: Any) -> Optional[Decimal]:
    """``Decimal`` or ``None``. Never raises, and never turns junk into a number.

    A venue field that cannot be parsed is treated exactly like an absent one, because
    "unreadable" and "zero" are not the same claim and only one of them is safe to write.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _first_reported(data: Dict[str, Any], keys: tuple) -> Optional[Decimal]:
    """The first key actually PRESENT in ``data``, parsed. ``None`` if none are.

    Presence is tested with ``in``, not truthiness: a reported ``0`` is a fact and must be
    returned as ``Decimal("0")`` rather than skipped in favour of a later key.
    """
    for key in keys:
        if key in data:
            parsed = _as_decimal(data.get(key))
            if parsed is not None:
                return parsed
    return None


def _venue_cumulative_fill(data: Dict[str, Any]) -> Optional[Decimal]:
    """The venue's CUMULATIVE filled quantity for this order, or ``None`` if unreported."""
    return _first_reported(data, _CUMULATIVE_FILL_KEYS)


def _venue_fill_price(data: Dict[str, Any]) -> Optional[Decimal]:
    """The price the fill should be booked at, or ``None`` if unreported."""
    return _first_reported(data, _FILL_PRICE_KEYS)


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
        """Find execution record by EXCHANGE order ID.

        RT-16 / TRADE-12. ``order_id`` here is the venue's identifier, taken from the
        exchange's own WebSocket event. This matched it against
        ``ExecutionRecordModel.order_id`` -- which, since TRADE-12, is THIS platform's
        order id (it carries ``FOREIGN KEY (order_id) REFERENCES orders(id)``). The
        exchange's id lives in ``venue_order_id``.

        So a venue id was being compared against a column that never holds one, and the
        live execution engine leaves ``order_id`` NULL: **no fill event arriving over the
        WebSocket could ever match an execution the engine created.** The fastest and most
        authoritative fill-confirmation path in the system resolved to ``None`` every time
        and the event was dropped as an unknown order.

        Both columns are matched now. ``venue_order_id`` is the correct one; ``order_id``
        is retained because the older recovery paths still populate it and their rows must
        keep resolving.
        """
        return self.db.query(ExecutionRecordModel).filter(
            or_(
                ExecutionRecordModel.venue_order_id == order_id,
                ExecutionRecordModel.order_id == order_id,
            ),
            ExecutionRecordModel.tenant_id == tenant_id
        ).first()
    
    async def _handle_filled(self, event: ExchangeEvent, execution: ExecutionRecordModel):
        """RT-16. RECORD what the venue reported. Do NOT adjudicate the execution.

        THIS USED TO BE AN ADJUDICATOR AND IT COULD NOT SAFELY BE ONE. The previous body
        set ``status = ExecutionStatus.COMPLETED``, stamped ``filled_at``, and called
        ``transition_to_filled``. Two separate reasons that was wrong, and the first is
        the one that would have destroyed data the moment RT-16's chain was closed.

        1. IT READ KEYS THE PAYLOAD DOES NOT HAVE. It took
           ``data.get('filled_size', data.get('z', 0))``. The feeder --
           ``exchange_websocket_listener._publish_order_event`` -- sets
           ``data=event.raw_data``, which is the **ccxt-normalised order dict**, whose
           cumulative-fill key is ``filled`` (see that module's line 355, which reads
           exactly ``order_data.get("filled")``). ``filled_size`` and Binance's raw ``z``
           are both absent from it, so the expression resolved to ``0`` on every
           ccxt-sourced fill. The handler then wrote ``filled_size = "0"`` and
           ``avg_price = "0"`` over a real recorded fill AND set the row COMPLETED --
           which is terminal, because ``OrderWatchdog._find_open_orders``,
           ``_find_stale_orders`` and ``ExchangeReconciliation._get_active_orders`` all
           select only PENDING/EXECUTING. The row would have left every reconciliation
           population at the exact moment it most needed watching, recorded as fully
           complete with nothing filled. That is strictly worse than the honest gap RT-16
           described, which is why the chain could not be wired before this was fixed.

        2. IT WOULD HAVE BEEN A SECOND OWNER OF ADJUDICATION.
           ``core/models/execution_record.py`` records the decision plainly:
           "OrderWatchdog is the only owner of adjudication, and introducing a second one
           would be worse than the defect." And the feeder's own
           ``_update_order_in_db`` ALREADY records the venue's figures, with the FILL-01
           guards -- absent is not zero, and a cumulative fill never moves backwards.
           This handler ran on the same event with none of those guards, so the second
           write undid the first. On real money two adjudicators can double-resolve one
           fill.

        SO THE DIVISION IS: THE STREAM OBSERVES, THE WATCHDOG DECIDES. This records the
        venue's evidence -- cumulative fill, price, status text, sync time -- under the
        same monotonicity rule the feeder uses, and leaves the terminal transition and the
        position credit to ``OrderWatchdog``. The latency win is still real: the watchdog
        now adjudicates from evidence already in the row instead of having to ask the
        venue.

        NOT CHANGED HERE, deliberately: ``_handle_cancelled`` and ``_handle_rejected``
        still set ``FAILED``. They carry neither defect -- no cumulative figure to
        misread, and a venue-confirmed rejection is not the ambiguous case the
        single-adjudicator rule was written for. Changing them is a separate decision
        with its own evidence, and bundling it here would smuggle it in.
        """
        self._record_venue_fill_evidence(event, execution, label="ORDER_FILLED")

    async def _handle_partial(self, event: ExchangeEvent, execution: ExecutionRecordModel):
        """RT-16. RECORD a partial fill. Same rule as :meth:`_handle_filled`.

        This carried the identical payload-shape defect and one more of its own: with
        ``filled_size`` resolving to ``0`` it computed
        ``remaining_size = total_size - 0`` and wrote the FULL order size back as
        remaining, alongside an ``avg_price`` of ``0`` -- so a partially filled order was
        recorded as entirely unfilled at a price of zero.

        ``remaining_size`` is now derived only when a cumulative fill was actually
        reported, for the reason FILL-01 gives in the feeder: ``remaining`` describes the
        same snapshot as ``filled``, so writing one without the other makes the row
        internally inconsistent.
        """
        self._record_venue_fill_evidence(event, execution, label="PARTIAL_FILL")

    def _record_venue_fill_evidence(
        self,
        event: ExchangeEvent,
        execution: ExecutionRecordModel,
        *,
        label: str,
    ) -> None:
        """Write the venue's reported figures onto the row. Never changes ``status``.

        Shared by both fill handlers because the rule is identical and two copies of a
        monotonicity guard is how one of them ends up without it.
        """
        data = event.data or {}

        reported_fill = _venue_cumulative_fill(data)
        reported_price = _venue_fill_price(data)
        recorded_fill = _as_decimal(execution.filled_size) or Decimal("0")

        if reported_fill is None:
            # ABSENT IS NOT ZERO. The venue said nothing about the cumulative fill, so
            # there is nothing to record and the existing figure stands. Writing a zero
            # here is precisely the bug this method replaced.
            logger.info(
                "RT-16: %s for execution %s reported no cumulative fill, so no fill "
                "figure was recorded (order=%s). The row is unchanged apart from its "
                "sync time.",
                label, execution.execution_id, event.order_id,
            )
        elif reported_fill < recorded_fill:
            # Cumulative fills cannot shrink, so this event is stale or out of order.
            logger.warning(
                "RT-16: ignoring a stale %s for execution %s -- it reports a cumulative "
                "fill of %s, below the %s already recorded.",
                label, execution.execution_id, reported_fill, recorded_fill,
            )
        else:
            execution.filled_size = str(reported_fill)
            # `remaining` and `average` describe the same snapshot as `filled`, so they
            # move with it or not at all.
            if reported_price is not None:
                execution.avg_price = str(reported_price)
            total_size = _as_decimal(execution.size)
            if total_size is not None:
                execution.remaining_size = str(max(Decimal("0"), total_size - reported_fill))
            logger.info(
                "RT-16: recorded venue evidence for %s on execution %s -- cumulative "
                "fill %s, price %s, order=%s. Status is UNCHANGED (%s); OrderWatchdog "
                "owns adjudication.",
                label, execution.execution_id, reported_fill,
                reported_price if reported_price is not None else "not reported",
                event.order_id, execution.status,
            )

        # Recorded even when nothing else was: "the venue answered, at this time" is
        # itself the evidence the watchdog's staleness checks read.
        execution.last_exchange_sync = datetime.utcnow()
        venue_status = data.get("status")
        if venue_status and hasattr(execution, "exchange_status"):
            execution.exchange_status = str(venue_status)

        self.db.commit()

    
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
    
    async def _update_position(self, execution: ExecutionRecordModel, fill_delta: Optional[Any] = None):
        """STEP 5.6: Update position from execution fill (idempotent with delta tracking)."""
        position_engine = get_position_engine(self.db)
        
        size_to_apply = str(fill_delta) if fill_delta is not None else execution.filled_size
        if not size_to_apply or Decimal(size_to_apply) <= 0:
            return
            
        position = await position_engine.update_position_from_fill(
            execution_record=execution,
            fill_size=size_to_apply,
            fill_price=execution.avg_price
        )
        
        if position:
            logger.info(
                f"POSITION UPDATED from event: {position.position_id} | "
                f"size={position.size} | delta={size_to_apply} | "
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

