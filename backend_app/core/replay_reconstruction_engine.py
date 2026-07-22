"""
Replay Reconstruction Engine

Principal Institutional Execution Consistency Engineer

This module implements actual replay reconstruction to rebuild state from
persisted events. It provides:

- Position reconstruction from journal
- Execution reconstruction from journal
- Order reconstruction from journal
- Fill reconstruction from journal
- State restoration to live system
- Worker restart integration

CRITICAL: Worker restart must rebuild identical state from journal.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReconstructedPosition:
    """Reconstructed position from event journal."""
    position_id: str
    tenant_id: str
    strategy_id: str
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    status: str
    opened_at: datetime
    last_updated: datetime
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ReconstructedOrder:
    """Reconstructed order from event journal."""
    order_id: str
    tenant_id: str
    strategy_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal
    status: str
    filled_quantity: Decimal
    remaining_quantity: Decimal
    created_at: datetime
    updated_at: datetime
    fills: List[Dict[str, Any]] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.fills is None:
            self.fills = []
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ReconstructedExecution:
    """Reconstructed execution from event journal."""
    execution_id: str
    tenant_id: str
    strategy_id: str
    signal_id: str
    symbol: str
    side: str
    size: Decimal
    price: Decimal
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ReconstructionResult:
    """Result of state reconstruction."""
    success: bool
    positions: List[ReconstructedPosition]
    orders: List[ReconstructedOrder]
    executions: List[ReconstructedExecution]
    fills: List[Dict[str, Any]]
    events_processed: int
    errors: List[str]
    reconstruction_time: datetime
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class ReplayReconstructionEngine:
    """
    Reconstructs execution state from persisted event journal.
    
    Unlike the read-only replay engine, this engine actually restores
    state to the live system, enabling worker restart recovery.
    """
    
    def __init__(
        self,
        event_journal_client: Any,
        state_service: Any,
        fill_deduplication_manager: Any,
        enable_fill_deduplication: bool = True
    ):
        """
        Initialize replay reconstruction engine.
        
        Args:
            event_journal_client: Client for reading event journal
            state_service: Service for restoring state to live system
            fill_deduplication_manager: Fill deduplication manager for replay safety
            enable_fill_deduplication: Enable fill deduplication during replay
        """
        self.event_journal = event_journal_client
        self.state_service = state_service
        self.fill_deduplication_manager = fill_deduplication_manager
        self.enable_fill_deduplication = enable_fill_deduplication
        
    async def reconstruct_state(
        self,
        tenant_id: str,
        strategy_id: Optional[str] = None,
        from_sequence: Optional[int] = None,
        to_sequence: Optional[int] = None,
        restore_to_live: bool = True
    ) -> ReconstructionResult:
        """
        Reconstruct state from event journal.
        
        This method:
        1. Fetches events from journal
        2. Applies events to reconstruct state
        3. Optionally restores state to live system
        4. Returns reconstruction result
        
        Args:
            tenant_id: Tenant identifier
            strategy_id: Optional strategy filter
            from_sequence: Start sequence number (inclusive)
            to_sequence: End sequence number (inclusive)
            restore_to_live: If True, restore state to live system
            
        Returns:
            ReconstructionResult with reconstructed state
        """
        start_time = datetime.now(timezone.utc)
        errors = []
        
        # Initialize reconstructed state
        reconstructed_positions: Dict[str, ReconstructedPosition] = {}
        reconstructed_orders: Dict[str, ReconstructedOrder] = {}
        reconstructed_executions: Dict[str, ReconstructedExecution] = {}
        reconstructed_fills: List[Dict[str, Any]] = []
        
        try:
            # Fetch events from journal
            events = await self._fetch_events(
                tenant_id=tenant_id,
                strategy_id=strategy_id,
                from_sequence=from_sequence,
                to_sequence=to_sequence
            )
            
            logger.info(
                f"[ReplayReconstructionEngine] Fetched {len(events)} events for reconstruction"
            )
            
            # Process events in sequence order
            for event in events:
                try:
                    await self._apply_event(
                        event=event,
                        positions=reconstructed_positions,
                        orders=reconstructed_orders,
                        executions=reconstructed_executions,
                        fills=reconstructed_fills
                    )
                except Exception as e:
                    error_msg = f"Error applying event {event.get('sequence')}: {e}"
                    logger.error(f"[ReplayReconstructionEngine] {error_msg}")
                    errors.append(error_msg)
            
            # Convert to lists
            positions_list = list(reconstructed_positions.values())
            orders_list = list(reconstructed_orders.values())
            executions_list = list(reconstructed_executions.values())
            
            # Restore to live system if requested
            if restore_to_live:
                await self._restore_state_to_live(
                    tenant_id=tenant_id,
                    positions=positions_list,
                    orders=orders_list,
                    executions=executions_list,
                    fills=reconstructed_fills
                )
            
            logger.info(
                f"[ReplayReconstructionEngine] Reconstruction complete: "
                f"{len(positions_list)} positions, {len(orders_list)} orders, "
                f"{len(executions_list)} executions, {len(reconstructed_fills)} fills"
            )
            
            return ReconstructionResult(
                success=len(errors) == 0,
                positions=positions_list,
                orders=orders_list,
                executions=executions_list,
                fills=reconstructed_fills,
                events_processed=len(events),
                errors=errors,
                reconstruction_time=start_time,
                metadata={
                    "tenant_id": tenant_id,
                    "strategy_id": strategy_id,
                    "from_sequence": from_sequence,
                    "to_sequence": to_sequence,
                    "restore_to_live": restore_to_live
                }
            )
            
        except Exception as e:
            error_msg = f"Reconstruction failed: {e}"
            logger.error(f"[ReplayReconstructionEngine] {error_msg}")
            errors.append(error_msg)
            
            return ReconstructionResult(
                success=False,
                positions=list(reconstructed_positions.values()),
                orders=list(reconstructed_orders.values()),
                executions=list(reconstructed_executions.values()),
                fills=reconstructed_fills,
                events_processed=0,
                errors=errors,
                reconstruction_time=start_time,
                metadata={"error": str(e)}
            )
    
    async def _fetch_events(
        self,
        tenant_id: str,
        strategy_id: Optional[str] = None,
        from_sequence: Optional[int] = None,
        to_sequence: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch events from event journal.
        
        Args:
            tenant_id: Tenant identifier
            strategy_id: Optional strategy filter
            from_sequence: Start sequence number
            to_sequence: End sequence number
            
        Returns:
            List of events in sequence order
        """
        try:
            # Build query filters
            filters = {"tenant_id": tenant_id}
            if strategy_id:
                filters["strategy_id"] = strategy_id
            if from_sequence is not None:
                filters["sequence_gte"] = from_sequence
            if to_sequence is not None:
                filters["sequence_lte"] = to_sequence
            
            # Fetch events from journal
            events = await self.event_journal.query_events(filters)
            
            # Sort by sequence
            events.sort(key=lambda e: e.get("sequence", 0))
            
            return events
            
        except Exception as e:
            logger.error(f"[ReplayReconstructionEngine] Error fetching events: {e}")
            raise
    
    async def _apply_event(
        self,
        event: Dict[str, Any],
        positions: Dict[str, ReconstructedPosition],
        orders: Dict[str, ReconstructedOrder],
        executions: Dict[str, ReconstructedExecution],
        fills: List[Dict[str, Any]]
    ) -> None:
        """
        Apply event to reconstructed state.
        
        Args:
            event: Event to apply
            positions: Reconstructed positions dict
            orders: Reconstructed orders dict
            executions: Reconstructed executions dict
            fills: Reconstructed fills list
        """
        event_type = event.get("event_type")
        payload = event.get("payload", {})
        
        if event_type == "ORDER_SUBMITTED":
            await self._apply_order_submitted(payload, orders)
        elif event_type == "ORDER_ACCEPTED":
            await self._apply_order_accepted(payload, orders)
        elif event_type == "ORDER_FILLED":
            await self._apply_order_filled(payload, orders, positions, fills)
        elif event_type == "ORDER_CANCELLED":
            await self._apply_order_cancelled(payload, orders)
        elif event_type == "POSITION_OPENED":
            await self._apply_position_opened(payload, positions)
        elif event_type == "POSITION_UPDATED":
            await self._apply_position_updated(payload, positions)
        elif event_type == "POSITION_CLOSED":
            await self._apply_position_closed(payload, positions)
        elif event_type == "EXECUTION_SUBMITTED":
            await self._apply_execution_submitted(payload, executions)
        elif event_type == "EXECUTION_COMPLETED":
            await self._apply_execution_completed(payload, executions)
        elif event_type == "FILL_RECEIVED":
            await self._apply_fill_received(payload, fills)
        else:
            logger.warning(f"[ReplayReconstructionEngine] Unknown event type: {event_type}")
    
    async def _apply_order_submitted(
        self,
        payload: Dict[str, Any],
        orders: Dict[str, ReconstructedOrder]
    ) -> None:
        """Apply order submitted event."""
        order_id = payload.get("order_id")
        if not order_id:
            return
        
        order = ReconstructedOrder(
            order_id=order_id,
            tenant_id=payload.get("tenant_id"),
            strategy_id=payload.get("strategy_id"),
            symbol=payload.get("symbol"),
            side=payload.get("side"),
            order_type=payload.get("order_type"),
            quantity=Decimal(str(payload.get("quantity", "0"))),
            price=Decimal(str(payload.get("price", "0"))),
            status="submitted",
            filled_quantity=Decimal("0"),
            remaining_quantity=Decimal(str(payload.get("quantity", "0"))),
            created_at=datetime.fromisoformat(payload.get("timestamp", datetime.now(timezone.utc).isoformat())),
            updated_at=datetime.now(timezone.utc),
            metadata=payload.get("metadata", {})
        )
        
        orders[order_id] = order
    
    async def _apply_order_accepted(
        self,
        payload: Dict[str, Any],
        orders: Dict[str, ReconstructedOrder]
    ) -> None:
        """Apply order accepted event."""
        order_id = payload.get("order_id")
        if order_id in orders:
            orders[order_id].status = "accepted"
            orders[order_id].updated_at = datetime.now(timezone.utc)
    
    async def _apply_order_filled(
        self,
        payload: Dict[str, Any],
        orders: Dict[str, ReconstructedOrder],
        positions: Dict[str, ReconstructedPosition],
        fills: List[Dict[str, Any]]
    ) -> None:
        """Apply order filled event."""
        order_id = payload.get("order_id")
        if order_id not in orders:
            return
        
        order = orders[order_id]
        filled_quantity = Decimal(str(payload.get("filled_quantity", "0")))
        fill_price = Decimal(str(payload.get("fill_price", "0")))
        
        # Update order
        order.filled_quantity += filled_quantity
        order.remaining_quantity = order.quantity - order.filled_quantity
        order.updated_at = datetime.now(timezone.utc)
        
        # Add fill to order
        fill_record = {
            "fill_id": payload.get("fill_id"),
            "order_id": order_id,
            "filled_quantity": str(filled_quantity),
            "fill_price": str(fill_price),
            "timestamp": payload.get("timestamp"),
            "exchange_trade_id": payload.get("exchange_trade_id")
        }
        order.fills.append(fill_record)
        fills.append(fill_record)
        
        # Update position
        position_id = payload.get("position_id")
        if position_id and position_id in positions:
            position = positions[position_id]
            if order.side == "buy":
                position.quantity += filled_quantity
            else:
                position.quantity -= filled_quantity
            position.last_updated = datetime.now(timezone.utc)
    
    async def _apply_order_cancelled(
        self,
        payload: Dict[str, Any],
        orders: Dict[str, ReconstructedOrder]
    ) -> None:
        """Apply order cancelled event."""
        order_id = payload.get("order_id")
        if order_id in orders:
            orders[order_id].status = "cancelled"
            orders[order_id].updated_at = datetime.now(timezone.utc)
    
    async def _apply_position_opened(
        self,
        payload: Dict[str, Any],
        positions: Dict[str, ReconstructedPosition]
    ) -> None:
        """Apply position opened event."""
        position_id = payload.get("position_id")
        if not position_id:
            return
        
        position = ReconstructedPosition(
            position_id=position_id,
            tenant_id=payload.get("tenant_id"),
            strategy_id=payload.get("strategy_id"),
            symbol=payload.get("symbol"),
            side=payload.get("side"),
            quantity=Decimal(str(payload.get("quantity", "0"))),
            entry_price=Decimal(str(payload.get("entry_price", "0"))),
            current_price=Decimal(str(payload.get("current_price", "0"))),
            unrealized_pnl=Decimal(str(payload.get("unrealized_pnl", "0"))),
            realized_pnl=Decimal(str(payload.get("realized_pnl", "0"))),
            status="open",
            opened_at=datetime.fromisoformat(payload.get("timestamp", datetime.now(timezone.utc).isoformat())),
            last_updated=datetime.now(timezone.utc),
            metadata=payload.get("metadata", {})
        )
        
        positions[position_id] = position
    
    async def _apply_position_updated(
        self,
        payload: Dict[str, Any],
        positions: Dict[str, ReconstructedPosition]
    ) -> None:
        """Apply position updated event."""
        position_id = payload.get("position_id")
        if position_id in positions:
            position = positions[position_id]
            position.quantity = Decimal(str(payload.get("quantity", position.quantity)))
            position.current_price = Decimal(str(payload.get("current_price", position.current_price)))
            position.unrealized_pnl = Decimal(str(payload.get("unrealized_pnl", position.unrealized_pnl)))
            position.realized_pnl = Decimal(str(payload.get("realized_pnl", position.realized_pnl)))
            position.last_updated = datetime.now(timezone.utc)
    
    async def _apply_position_closed(
        self,
        payload: Dict[str, Any],
        positions: Dict[str, ReconstructedPosition]
    ) -> None:
        """Apply position closed event."""
        position_id = payload.get("position_id")
        if position_id in positions:
            positions[position_id].status = "closed"
            positions[position_id].realized_pnl = Decimal(str(payload.get("realized_pnl", positions[position_id].realized_pnl)))
            positions[position_id].last_updated = datetime.now(timezone.utc)
    
    async def _apply_execution_submitted(
        self,
        payload: Dict[str, Any],
        executions: Dict[str, ReconstructedExecution]
    ) -> None:
        """Apply execution submitted event."""
        execution_id = payload.get("execution_id")
        if not execution_id:
            return
        
        execution = ReconstructedExecution(
            execution_id=execution_id,
            tenant_id=payload.get("tenant_id"),
            strategy_id=payload.get("strategy_id"),
            signal_id=payload.get("signal_id"),
            symbol=payload.get("symbol"),
            side=payload.get("side"),
            size=Decimal(str(payload.get("size", "0"))),
            price=Decimal(str(payload.get("price", "0"))),
            status="submitted",
            created_at=datetime.fromisoformat(payload.get("timestamp", datetime.now(timezone.utc).isoformat())),
            metadata=payload.get("metadata", {})
        )
        
        executions[execution_id] = execution
    
    async def _apply_execution_completed(
        self,
        payload: Dict[str, Any],
        executions: Dict[str, ReconstructedExecution]
    ) -> None:
        """Apply execution completed event."""
        execution_id = payload.get("execution_id")
        if execution_id in executions:
            executions[execution_id].status = "completed"
            executions[execution_id].completed_at = datetime.fromisoformat(
                payload.get("timestamp", datetime.now(timezone.utc).isoformat())
            )
    
    async def _apply_fill_received(
        self,
        payload: Dict[str, Any],
        fills: List[Dict[str, Any]]
    ) -> None:
        """Apply fill received event."""
        # Fill deduplication check during replay
        if self.enable_fill_deduplication and self.fill_deduplication_manager:
            fill_hash = payload.get("fill_hash")
            tenant_id = payload.get("tenant_id")
            
            if fill_hash and tenant_id:
                is_duplicate = await self.fill_deduplication_manager.is_duplicate_fill(
                    tenant_id=tenant_id,
                    fill_hash=fill_hash
                )
                
                if is_duplicate:
                    logger.warning(
                        f"[ReplayReconstructionEngine] Duplicate fill during replay: "
                        f"fill_hash={fill_hash}"
                    )
                    return  # Skip duplicate fill
        
        # Add fill to reconstructed fills
        fills.append(payload)
    
    async def _restore_state_to_live(
        self,
        tenant_id: str,
        positions: List[ReconstructedPosition],
        orders: List[ReconstructedOrder],
        executions: List[ReconstructedExecution],
        fills: List[Dict[str, Any]]
    ) -> None:
        """
        Restore reconstructed state to live system.
        
        Args:
            tenant_id: Tenant identifier
            positions: Reconstructed positions
            orders: Reconstructed orders
            executions: Reconstructed executions
            fills: Reconstructed fills
        """
        try:
            # Restore positions
            for position in positions:
                await self.state_service.restore_position(
                    tenant_id=tenant_id,
                    position_id=position.position_id,
                    position_data=asdict(position)
                )
            
            # Restore orders
            for order in orders:
                await self.state_service.restore_order(
                    tenant_id=tenant_id,
                    order_id=order.order_id,
                    order_data=asdict(order)
                )
            
            # Restore executions
            for execution in executions:
                await self.state_service.restore_execution(
                    tenant_id=tenant_id,
                    execution_id=execution.execution_id,
                    execution_data=asdict(execution)
                )
            
            # Restore fills (with deduplication)
            if self.enable_fill_deduplication and self.fill_deduplication_manager:
                for fill in fills:
                    await self.fill_deduplication_manager.process_fill(
                        tenant_id=tenant_id,
                        order_id=fill.get("order_id"),
                        symbol=fill.get("symbol"),
                        side=fill.get("side"),
                        filled_quantity=Decimal(str(fill.get("filled_quantity", "0"))),
                        fill_price=Decimal(str(fill.get("fill_price", "0"))),
                        timestamp=datetime.fromisoformat(fill.get("timestamp", datetime.now(timezone.utc).isoformat())),
                        exchange_trade_id=fill.get("exchange_trade_id"),
                        fee=Decimal(str(fill.get("fee", "0"))),
                        fee_currency=fill.get("fee_currency", ""),
                        metadata=fill.get("metadata", {})
                    )
            
            logger.info(
                f"[ReplayReconstructionEngine] State restored to live system: "
                f"{len(positions)} positions, {len(orders)} orders, "
                f"{len(executions)} executions, {len(fills)} fills"
            )
            
        except Exception as e:
            logger.error(f"[ReplayReconstructionEngine] Error restoring state to live: {e}")
            raise


# Factory function for dependency injection
def create_replay_reconstruction_engine(
    event_journal_client: Any,
    state_service: Any,
    fill_deduplication_manager: Any,
    enable_fill_deduplication: bool = True
) -> ReplayReconstructionEngine:
    """
    Factory function to create ReplayReconstructionEngine.
    
    Args:
        event_journal_client: Client for reading event journal
        state_service: Service for restoring state to live system
        fill_deduplication_manager: Fill deduplication manager
        enable_fill_deduplication: Enable fill deduplication during replay
        
    Returns:
        Configured ReplayReconstructionEngine instance
    """
    return ReplayReconstructionEngine(
        event_journal_client=event_journal_client,
        state_service=state_service,
        fill_deduplication_manager=fill_deduplication_manager,
        enable_fill_deduplication=enable_fill_deduplication
    )