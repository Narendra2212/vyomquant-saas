"""
backend/exchange_websocket_listener.py — Exchange Event-Driven Fill System

🔴 STEP 3 — EXCHANGE EVENT-DRIVEN FILL SYSTEM

Provides WebSocket-based real-time order updates and fill notifications
from cryptocurrency exchanges.

FLOW:
    exchange WS → event handler → update DB → notify system

RULE:
    WebSocket = primary source of truth
    Polling = fallback only

EXPECTED RESULT:
    ✔ Near real-time fill sync
    ✔ No polling delays
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Dict, Optional, Set

from sqlalchemy import and_

from backend_app.backend.connection_engine import get_or_create_exchange
from backend_app.backend.event_listener import get_event_listener
from backend_app.backend.redis_manager import redis_manager

logger = logging.getLogger("ExchangeWebSocketListener")


class OrderEventType(Enum):
    """Types of order events from exchange WebSocket."""
    ORDER_CREATED = "order_created"
    ORDER_UPDATED = "order_updated"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_FILLED = "order_filled"
    PARTIAL_FILL = "partial_fill"
    TRADE_EXECUTED = "trade_executed"


@dataclass
class OrderUpdateEvent:
    """Order update event from exchange WebSocket."""
    event_type: OrderEventType
    exchange_id: str
    tenant_id: str
    symbol: str
    order_id: str
    client_order_id: Optional[str]
    status: str
    side: str
    price: Optional[Decimal]
    amount: Optional[Decimal]
    filled: Optional[Decimal]
    remaining: Optional[Decimal]
    average_price: Optional[Decimal]
    timestamp: datetime
    raw_data: Dict[str, Any]


@dataclass
class FillEvent:
    """Fill event from exchange WebSocket."""
    exchange_id: str
    tenant_id: str
    symbol: str
    order_id: str
    trade_id: str
    side: str
    amount: Decimal
    price: Decimal
    fee: Optional[Decimal]
    fee_currency: Optional[str]
    timestamp: datetime
    raw_data: Dict[str, Any]


class ExchangeWebSocketListener:
    """
    🔴 STEP 3 — EXCHANGE EVENT-DRIVEN FILL SYSTEM
    
    WebSocket listener for real-time order updates and fills from exchanges.
    
    PRIMARY SOURCE OF TRUTH:
        WebSocket events are processed immediately and update the database
        in real-time. Polling is used ONLY as a fallback.
    
    ARCHITECTURE:
        ┌─────────────┐     ┌──────────────┐     ┌──────────┐     ┌──────────┐
        │ Exchange WS │────→│ Event Handler │────→│ Update DB │────→│ Notify   │
        └─────────────┘     └──────────────┘     └──────────┘     └──────────┘
                                   ↑                    ↓
                                   └──────── Fallback (poll after 5s delay)
    
    RULES:
        1. WebSocket events are PRIMARY
        2. Database updated within 100ms of event receipt
        3. Polling only triggers if no WS event within 5 seconds
        4. All fills published to event bus for downstream consumers
    
    EXPECTED RESULT:
        ✔ Near real-time fill sync (< 100ms latency)
        ✔ No polling delays for fills
    """
    
    # Timeout before falling back to polling
    WS_FALLBACK_TIMEOUT_SECONDS = 5
    
    # Health check interval
    HEALTH_CHECK_INTERVAL_SECONDS = 30
    
    def __init__(self):
        self._listeners: Dict[str, asyncio.Task] = {}
        self._order_callbacks: Dict[str, Callable] = {}
        self._fill_callbacks: Dict[str, Callable] = {}
        self._last_event_time: Dict[str, datetime] = {}
        self._running = False
        self._health_check_task: Optional[asyncio.Task] = None
        self._fallback_tasks: Dict[str, asyncio.Task] = {}
    
    async def start_listening(
        self,
        tenant_id: str,
        exchange_id: str,
        symbols: Optional[Set[str]] = None
    ):
        """
        Start WebSocket listener for a tenant's exchange connection.
        
        Args:
            tenant_id: Tenant identifier
            exchange_id: Exchange identifier (e.g., "binance")
            symbols: Set of symbols to watch (None for all)
        """
        listener_key = f"{tenant_id}:{exchange_id}"
        
        if listener_key in self._listeners:
            logger.warning(f"STEP 3: Listener already active for {listener_key}")
            return
        
        logger.info(f"STEP 3: Starting WebSocket listener for {listener_key}")
        
        # Start the WebSocket listener task
        task = asyncio.create_task(
            self._listen_for_orders(tenant_id, exchange_id, symbols),
            name=f"ws_listener:{listener_key}"
        )
        self._listeners[listener_key] = task
        
        # Start fallback polling monitor
        fallback_task = asyncio.create_task(
            self._fallback_monitor(tenant_id, exchange_id),
            name=f"fallback:{listener_key}"
        )
        self._fallback_tasks[listener_key] = fallback_task
        
        logger.info(f"STEP 3: WebSocket listener started for {listener_key}")
    
    async def stop_listening(self, tenant_id: str, exchange_id: str):
        """Stop WebSocket listener for a tenant."""
        listener_key = f"{tenant_id}:{exchange_id}"
        
        # Cancel WebSocket listener
        if listener_key in self._listeners:
            self._listeners[listener_key].cancel()
            try:
                await self._listeners[listener_key]
            except asyncio.CancelledError:
                pass
            del self._listeners[listener_key]
        
        # Cancel fallback monitor
        if listener_key in self._fallback_tasks:
            self._fallback_tasks[listener_key].cancel()
            try:
                await self._fallback_tasks[listener_key]
            except asyncio.CancelledError:
                pass
            del self._fallback_tasks[listener_key]
        
        logger.info(f"STEP 3: WebSocket listener stopped for {listener_key}")
    
    async def _listen_for_orders(
        self,
        tenant_id: str,
        exchange_id: str,
        symbols: Optional[Set[str]]
    ):
        """
        Main WebSocket listener loop for order updates.
        
        Uses CCXT.pro's watch_orders() to receive real-time order updates.
        """
        listener_key = f"{tenant_id}:{exchange_id}"
        
        try:
            # Get or create exchange connection
            exchange = await get_or_create_exchange(
                user_id=tenant_id,
                exchange_id=exchange_id
            )
            
            logger.info(f"STEP 3: WebSocket connected for {listener_key}")
            
            while self._running:
                try:
                    # Watch for order updates (CCXT.pro WebSocket)
                    # This is a blocking call that returns when new orders arrive
                    orders = await exchange.watch_orders()
                    
                    # Update last event time
                    self._last_event_time[listener_key] = datetime.utcnow()
                    
                    # Process each order update
                    for order in orders:
                        await self._handle_order_update(tenant_id, exchange_id, order)
                    
                except Exception as e:
                    logger.error(f"STEP 3: WebSocket error for {listener_key}: {e}")
                    # Wait before reconnecting
                    await asyncio.sleep(1)
                    
        except asyncio.CancelledError:
            logger.info(f"STEP 3: WebSocket listener cancelled for {listener_key}")
        except Exception as e:
            logger.critical(f"STEP 3: Fatal WebSocket error for {listener_key}: {e}")
    
    async def _handle_order_update(
        self,
        tenant_id: str,
        exchange_id: str,
        order_data: Dict[str, Any]
    ):
        """
        Process order update from WebSocket.
        
        FLOW: exchange WS → update DB → notify system
        """
        try:
            # Parse order data
            order_id = order_data.get("id") or order_data.get("orderId")
            client_order_id = order_data.get("clientOrderId")
            symbol = order_data.get("symbol")
            status = order_data.get("status")
            side = order_data.get("side")
            
            logger.debug(
                f"STEP 3: Order update received: {order_id} on {symbol} status={status}"
            )
            
            # Create event object
            event = OrderUpdateEvent(
                event_type=self._map_order_status(status),
                exchange_id=exchange_id,
                tenant_id=tenant_id,
                symbol=symbol,
                order_id=order_id,
                client_order_id=client_order_id,
                status=status,
                side=side,
                price=Decimal(str(order_data.get("price", 0))) if order_data.get("price") else None,
                amount=Decimal(str(order_data.get("amount", 0))) if order_data.get("amount") else None,
                filled=Decimal(str(order_data.get("filled", 0))) if order_data.get("filled") else None,
                remaining=Decimal(str(order_data.get("remaining", 0))) if order_data.get("remaining") else None,
                average_price=Decimal(str(order_data.get("average", 0))) if order_data.get("average") else None,
                timestamp=datetime.utcnow(),
                raw_data=order_data
            )
            
            # STEP 3: Update database immediately
            await self._update_order_in_db(event)
            
            # STEP 3: Publish event to event bus
            await self._publish_order_event(event)
            
            # STEP 3: Handle fills separately if this is a fill event
            if event.event_type in (OrderEventType.ORDER_FILLED, OrderEventType.PARTIAL_FILL):
                await self._handle_fill_event(event, order_data)
            
            logger.info(
                f"STEP 3: Order update processed: {order_id} status={status} "
                f"(DB updated in <100ms)"
            )
            
        except Exception as e:
            logger.error(f"STEP 3: Error handling order update: {e}", exc_info=True)
    
    @staticmethod
    def _stable_trade_id(order_id: str, trade: Dict[str, Any]) -> Optional[str]:
        """A deduplication identity for one fill, stable across replays.

        FILL-01. The venue's own trade id is used whenever it supplies one — that is the
        only truly authoritative identity, and it is what makes a redelivered event
        collapse onto the row it already wrote.

        The previous code was `trade.get("id", "unknown")`. When a venue omitted the id,
        **every** such fill was booked under the literal string ``"unknown"``, so two
        genuinely distinct partial fills collided on one identity and the second was
        either lost or overwrote the first, depending on the persistence path.

        When no venue id exists, a deterministic digest over the fill's own invariants is
        derived instead: order id, timestamp, amount and price. That is:

          * **stable** — a replay of the same event yields the same id, preserving
            idempotency, which is the property that matters most here;
          * **distinct** — two different fills differ in at least one of those fields in
            all but one case.

        That remaining case is stated rather than hidden: two fills of identical size at
        an identical price within the same millisecond produce the same digest and are
        collapsed into one. Collapsing is the safe direction — under-booking a fill is
        detectable by reconciliation against the venue, whereas double-booking one
        inflates a position with no record that it happened. Returns ``None`` when even
        that cannot be formed, so the caller refuses rather than invents an identity.
        """
        venue_id = trade.get("id")
        if venue_id not in (None, "", "unknown"):
            return str(venue_id)

        timestamp = trade.get("timestamp")
        amount = trade.get("amount")
        price = trade.get("price")
        if timestamp is None or amount is None or price is None:
            return None

        material = "\x1f".join(str(x) for x in (order_id, timestamp, amount, price))
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
        logger.warning(
            "FILL-01: venue supplied no trade id for order %s; derived the deterministic "
            "identity %s from (order, timestamp, amount, price). Two fills of identical "
            "size and price in the same millisecond would collapse into one — "
            "deliberately, since under-booking is recoverable by reconciliation and "
            "double-booking is not.",
            order_id, digest,
        )
        return f"derived-{digest}"

    async def _handle_fill_event(
        self,
        order_event: OrderUpdateEvent,
        raw_order_data: Dict[str, Any]
    ):
        """
        Process fill event from order update.
        
        Creates FillEvent and updates database with trade details using atomic manager.
        """
        try:
            from backend_app.backend.transactional_execution_manager import \
                TransactionalExecutionManager
            from backend_app.core.database_pool import db_pool

            # ═══════════════════════════════════════════════════════════════════
            # FILL-01: CUMULATIVE AND INCREMENTAL QUANTITIES ARE NOT INTERCHANGEABLE
            # ═══════════════════════════════════════════════════════════════════
            # ESTABLISHED from ccxt 4.3.92's own `Exchange.safe_order`, which DERIVES the
            # order-level figure by summing the trade-level ones:
            #
            #     filled = '0'
            #     for i in range(len(trades)):
            #         tradeAmount = self.safe_string(trades[i], 'amount')
            #         if parseFilled and (tradeAmount is not None):
            #             filled = Precise.string_add(filled, tradeAmount)
            #
            # so:
            #     trade['amount']   -> INCREMENTAL, that trade only, with its own `id`
            #     order['filled']   -> CUMULATIVE, the running total for the order
            #
            # Demonstrated: three trades of 1.0 / 1.0 / 0.5 parse to filled == 2.5.
            # Evidence: staging/probe_p11bc_fill_semantics.py ->
            # staging/p11bc_fill_semantics.txt
            #
            # THE DEFECT THIS REPLACES. When `trades` was empty but `lastTradeTimestamp`
            # was set, this method synthesised a single trade as
            #
            #     {"id": raw_order_data["id"], "amount": raw_order_data["filled"], ...}
            #
            # which takes the CUMULATIVE quantity and books it through the INCREMENTAL
            # path, under the ORDER's id. Two consequences, both money-affecting:
            #
            #   * a second such event re-books everything already booked, because
            #     `filled` is a running total rather than a delta;
            #   * the id is identical on every event for that order, so deduplication on
            #     trade id cannot tell a NEW partial fill from a REPLAY of the last one.
            #
            # WHY IT IS REFUSED RATHER THAN CONVERTED. Turning a cumulative figure into a
            # delta needs to know what has already been booked, and that is not in the
            # event. Reading it back from the database here would be a read-then-write
            # race against the very events this handler processes concurrently. So the
            # ambiguous shape is recorded for reconciliation instead of being guessed at
            # — the order-level cumulative figure is still persisted by
            # `_update_order_in_db`, which is the correct home for a cumulative number.
            trades = raw_order_data.get("trades", [])

            if not trades:
                if raw_order_data.get("lastTradeTimestamp"):
                    logger.warning(
                        "FILL-01: %s reported a fill timestamp for order %s with no "
                        "per-trade detail. The order-level 'filled' figure is CUMULATIVE "
                        "and cannot be booked as an incremental fill without knowing "
                        "what has already been recorded, so NO fill row was written. "
                        "The cumulative total is still persisted on the order. This "
                        "execution requires reconciliation against the venue's trade "
                        "history (fetch_my_trades).",
                        order_event.exchange_id, order_event.order_id,
                    )
                return

            for trade in trades:
                # A fill must carry a usable quantity and a usable price. `Decimal(str(
                # trade.get("price", 0)))` previously defaulted BOTH to 0, which books a
                # fill of zero size, or a real quantity at a price of zero — either
                # corrupts position and PnL accounting silently.
                raw_amount = trade.get("amount")
                raw_price = trade.get("price")
                if raw_amount is None or raw_price is None:
                    logger.error(
                        "FILL-01: refusing to book a fill for order %s with missing "
                        "detail (amount=%r, price=%r). A fill without a usable price "
                        "cannot be valued. Requires reconciliation.",
                        order_event.order_id, raw_amount, raw_price,
                    )
                    continue
                try:
                    amount = Decimal(str(raw_amount))
                    price = Decimal(str(raw_price))
                except (InvalidOperation, ValueError, TypeError):
                    logger.error(
                        "FILL-01: refusing to book a fill for order %s with "
                        "unparseable detail (amount=%r, price=%r).",
                        order_event.order_id, raw_amount, raw_price,
                    )
                    continue
                if amount <= 0 or price <= 0:
                    logger.error(
                        "FILL-01: refusing to book a non-positive fill for order %s "
                        "(amount=%s, price=%s).",
                        order_event.order_id, amount, price,
                    )
                    continue

                trade_id = self._stable_trade_id(order_event.order_id, trade)
                if trade_id is None:
                    logger.error(
                        "FILL-01: refusing to book a fill for order %s because no "
                        "stable trade identity could be derived; booking it would make "
                        "a replay indistinguishable from a new fill.",
                        order_event.order_id,
                    )
                    continue

                fill_event = FillEvent(
                    exchange_id=order_event.exchange_id,
                    tenant_id=order_event.tenant_id,
                    symbol=order_event.symbol,
                    order_id=order_event.order_id,
                    trade_id=trade_id,
                    side=order_event.side,
                    amount=amount,
                    price=price,
                    fee=Decimal(str(trade.get("fee", {}).get("cost", 0))) if trade.get("fee") else None,
                    fee_currency=trade.get("fee", {}).get("currency") if trade.get("fee") else None,
                    timestamp=datetime.utcnow(),
                    raw_data=trade
                )
                
                # Update database with fill atomically
                with db_pool.get_session() as session:
                    import uuid
                    manager = TransactionalExecutionManager(session, tenant_id=uuid.UUID(order_event.tenant_id))
                    fill_data = {
                        "fill_id": fill_event.trade_id,
                        "order_id": fill_event.order_id,
                        "symbol": fill_event.symbol,
                        "quantity": float(fill_event.amount),
                        "price": float(fill_event.price),
                        "side": fill_event.side
                    }
                    await manager.handle_fill(fill_data)
                
                # Publish fill event
                await self._publish_fill_event(fill_event)
                
                logger.info(
                    f"STEP 3: Fill processed atomically: {fill_event.trade_id} "
                    f"{fill_event.amount} @ {fill_event.price}"
                )
        
        except Exception as e:
            logger.error(f"STEP 3: Error handling fill event: {e}", exc_info=True)
    
    async def _update_order_in_db(self, event: OrderUpdateEvent):
        """Update order status in database."""
        try:
            from backend_app.core.database_pool import get_db_session
            from backend_app.core.models.execution_record import \
                ExecutionRecordModel
            
            async with get_db_session() as db:
                # Find the order
                order = db.query(ExecutionRecordModel).filter(
                    and_(
                        ExecutionRecordModel.order_id == event.order_id,
                        ExecutionRecordModel.tenant_id == event.tenant_id
                    )
                ).first()
                
                if order:
                    # ═══════════════════════════════════════════════════════════════
                    # FILL-01: AN ABSENT FIGURE IS NOT A ZERO, AND FILLS DO NOT SHRINK
                    # ═══════════════════════════════════════════════════════════════
                    # `order.filled_size = str(event.filled) if event.filled else "0"`
                    # had two distinct faults, both of which destroy recorded state:
                    #
                    #   1. ABSENT READ AS ZERO. The guard is truthiness, so an event that
                    #      does not report `filled` — `None` — overwrote a previously
                    #      recorded cumulative fill with "0". The order would then read
                    #      as unfilled while the position existed at the venue. Same for
                    #      `avg_price`: a later event lacking an average erased the price
                    #      a real fill had been booked at.
                    #
                    #   2. NO MONOTONICITY. `filled` is CUMULATIVE (see the FILL-01 note
                    #      in `_handle_fill_event`), so it can only ever increase for a
                    #      given order. A late or out-of-order event carrying a stale,
                    #      smaller total was written straight over a larger one.
                    #
                    # Both are fixed by the same rule: only ever move the cumulative
                    # figure FORWARD, and leave a field untouched when the event says
                    # nothing about it. Absent and zero are different facts.
                    order.status = event.status

                    if event.filled is not None:
                        try:
                            incoming = Decimal(str(event.filled))
                            recorded = Decimal(str(order.filled_size or "0"))
                        except (InvalidOperation, ValueError, TypeError):
                            incoming = recorded = None
                        if incoming is not None:
                            if incoming >= recorded:
                                order.filled_size = str(event.filled)
                                # `remaining` and `average` describe the same snapshot as
                                # `filled`, so they move together with it or not at all.
                                # Mixing a new `filled` with a stale `remaining` would
                                # make the row internally inconsistent.
                                if event.remaining is not None:
                                    order.remaining_size = str(event.remaining)
                                if event.average_price is not None:
                                    order.avg_price = str(event.average_price)
                            else:
                                logger.warning(
                                    "FILL-01: ignoring an out-of-order update for order "
                                    "%s — it reports a cumulative filled of %s, below "
                                    "the %s already recorded. Cumulative fills cannot "
                                    "decrease, so this event is stale.",
                                    event.order_id, incoming, recorded,
                                )
                    order.last_exchange_sync = datetime.utcnow()
                    
                    db.commit()
                    
                    # Cache in Redis for fast lookups
                    cache_key = f"order:{event.tenant_id}:{event.order_id}"
                    await redis_manager.setex(
                        cache_key,
                        300,  # 5 minute TTL
                        json.dumps({
                            "status": event.status,
                            "filled": str(event.filled) if event.filled else "0",
                            "timestamp": datetime.utcnow().isoformat()
                        })
                    )
                else:
                    logger.warning(
                        f"STEP 3: Order not found in DB: {event.order_id}"
                    )
        
        except Exception as e:
            logger.error(f"STEP 3: Error updating order in DB: {e}")
    
    async def _update_fill_in_db(self, event: FillEvent):
        """Update fill/trade in database (Deprecated - handled via handle_fill)."""
        pass
    
    async def _publish_order_event(self, event: OrderUpdateEvent):
        """Publish order update to event bus."""
        try:
            event_bus = get_event_listener()
            from backend_app.backend.event_listener import (EventType,
                                                            ExchangeEvent)
            
            evt_type = EventType.ORDER_UPDATED
            if event.event_type == OrderEventType.ORDER_FILLED:
                evt_type = EventType.ORDER_FILLED
            elif event.event_type == OrderEventType.PARTIAL_FILL:
                evt_type = EventType.ORDER_PARTIAL
            elif event.event_type == OrderEventType.ORDER_CANCELLED:
                evt_type = EventType.ORDER_CANCELLED
                
            ex_event = ExchangeEvent(
                event_id=f"ws_{event.exchange_id}_{event.order_id}_{event.timestamp.timestamp()}",
                event_type=evt_type,
                exchange_id=event.exchange_id,
                tenant_id=event.tenant_id,
                order_id=event.order_id,
                symbol=event.symbol,
                data=event.raw_data,
                exchange_timestamp=event.timestamp
            )
            event_bus._on_exchange_event(ex_event)
        
        except Exception as e:
            logger.error(f"STEP 3: Error publishing order event: {e}")
    
    async def _publish_fill_event(self, event: FillEvent):
        """Publish fill event to event bus."""
        try:
            event_bus = get_event_listener()
            from backend_app.backend.event_listener import (EventType,
                                                            ExchangeEvent)
            
            ex_event = ExchangeEvent(
                event_id=f"ws_fill_{event.exchange_id}_{event.trade_id}",
                event_type=EventType.ORDER_FILLED,
                exchange_id=event.exchange_id,
                tenant_id=event.tenant_id,
                order_id=event.order_id,
                symbol=event.symbol,
                data=event.raw_data,
                exchange_timestamp=event.timestamp
            )
            event_bus._on_exchange_event(ex_event)
        
        except Exception as e:
            logger.error(f"STEP 3: Error publishing fill event: {e}")
    
    async def _fallback_monitor(self, tenant_id: str, exchange_id: str):
        """
        Fallback polling monitor.
        
        RULE: Polling = fallback only (triggered if no WS event for 5 seconds)
        """
        listener_key = f"{tenant_id}:{exchange_id}"
        
        while self._running:
            try:
                await asyncio.sleep(self.WS_FALLBACK_TIMEOUT_SECONDS)
                
                # Check when last event was received
                last_event = self._last_event_time.get(listener_key)
                
                if last_event:
                    time_since_last = (datetime.utcnow() - last_event).total_seconds()
                    
                    if time_since_last > self.WS_FALLBACK_TIMEOUT_SECONDS:
                        logger.warning(
                            f"STEP 3: No WS event for {time_since_last:.1f}s, "
                            f"triggering fallback poll for {listener_key}"
                        )
                        
                        # Trigger fallback polling
                        await self._fallback_poll(tenant_id, exchange_id)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"STEP 3: Fallback monitor error: {e}")
    
    async def _fallback_poll(self, tenant_id: str, exchange_id: str):
        """
        Fallback polling for orders.
        
        Only runs if WebSocket hasn't delivered updates within timeout.
        """
        # ═══════════════════════════════════════════════════════════════════
        # THIS FALLBACK IS NOT IMPLEMENTED, AND NOW SAYS SO
        # ═══════════════════════════════════════════════════════════════════
        # Phase 10 / WS-D. This body used to be:
        #
        #     from backend_app.backend.exchange_reconciliation import \
        #         ExchangeReconciliationEngine
        #     reconciler = ExchangeReconciliationEngine()
        #     await reconciler.reconcile_open_orders(tenant_id, exchange_id)
        #
        # `exchange_reconciliation` defines no `ExchangeReconciliationEngine` -- the class
        # is `ExchangeReconciliationService` -- and its constructor requires a
        # `db_session`, which no call here supplied. So every invocation raised
        # ImportError, the enclosing `except Exception` logged it as
        # "STEP 3: Fallback poll error", and the line read like a transient exchange
        # problem. This fallback has never once run.
        #
        # It is NOT repaired by correcting the name. Doing that would start the
        # reconciler from the WebSocket timeout path with no session, no tenant
        # enumeration and no exchange client -- and a client-less service is exactly the
        # configuration that `_fetch_exchange_orders` now refuses, because reporting zero
        # open orders would resolve every active order. Wiring this reconciler up is a
        # separate decision with duplicate-worker and ghost-order-idempotency questions
        # attached; see the module docstring of
        # tests/test_p10d_exchange_reconciliation_fails_safely.py.
        #
        # Until then the honest behaviour is to stop pretending a safety net exists. The
        # WebSocket gap is still covered by `OrderWatchdog`, which IS started in
        # main.py's lifespan and runs its own reconciliation loop over the same
        # execution_records rows.
        logger.warning(
            "STEP 3: WebSocket fallback poll is NOT IMPLEMENTED for %s:%s — no "
            "reconciler is wired to this path. Stale-order coverage comes from "
            "OrderWatchdog (started in main.py's lifespan), not from here.",
            tenant_id, exchange_id,
        )
    
    def _map_order_status(self, status: str) -> OrderEventType:
        """Map exchange order status to event type."""
        status_map = {
            "open": OrderEventType.ORDER_UPDATED,
            "closed": OrderEventType.ORDER_FILLED,
            "canceled": OrderEventType.ORDER_CANCELLED,
            "cancelled": OrderEventType.ORDER_CANCELLED,
            "filled": OrderEventType.ORDER_FILLED,
            "partially_filled": OrderEventType.PARTIAL_FILL,
            "pending": OrderEventType.ORDER_CREATED,
            "new": OrderEventType.ORDER_CREATED,
        }
        return status_map.get(status.lower(), OrderEventType.ORDER_UPDATED)
    
    async def start(self):
        """Start the WebSocket listener manager."""
        self._running = True
        logger.info("STEP 3: Exchange WebSocket listener manager started")
    
    async def stop(self):
        """Stop all WebSocket listeners."""
        self._running = False
        
        # Cancel all listeners
        for key, task in list(self._listeners.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Cancel all fallback tasks
        for key, task in list(self._fallback_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self._listeners.clear()
        self._fallback_tasks.clear()
        
        logger.info("STEP 3: Exchange WebSocket listener manager stopped")


# Global singleton instance
_websocket_listener: Optional[ExchangeWebSocketListener] = None


def get_exchange_websocket_listener() -> ExchangeWebSocketListener:
    """Get or create the exchange WebSocket listener instance."""
    global _websocket_listener
    if _websocket_listener is None:
        _websocket_listener = ExchangeWebSocketListener()
    return _websocket_listener


# Convenience exports
__all__ = [
    "ExchangeWebSocketListener",
    "OrderUpdateEvent",
    "FillEvent",
    "OrderEventType",
    "get_exchange_websocket_listener",
]
