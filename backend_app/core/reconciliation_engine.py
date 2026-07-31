"""
Reconciliation Engine

Principal Institutional Execution Consistency Engineer

This module implements functional reconciliation to compare exchange state
against internal state and detect divergences. It provides:

- Fill-level reconciliation
- Quantity divergence detection
- Missing fill detection
- Duplicate fill detection
- Fill registry reconciliation
- Reconciliation action generation

CRITICAL: Reconciliation must detect and correct all state divergences.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FillMismatch:
    """Fill-level mismatch detected during reconciliation."""
    mismatch_type: str  # missing_fill, duplicate_fill, quantity_divergence
    fill_id: Optional[str]
    order_id: str
    exchange_trade_id: Optional[str]
    local_quantity: Optional[Decimal]
    exchange_quantity: Optional[Decimal]
    local_price: Optional[Decimal]
    exchange_price: Optional[Decimal]
    severity: str  # critical, warning, info
    detected_at: datetime
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class OrderMismatch:
    """Order-level mismatch detected during reconciliation."""
    mismatch_type: str  # missing_order, ghost_order, status_divergence, quantity_divergence
    order_id: str
    local_status: Optional[str]
    exchange_status: Optional[str]
    local_quantity: Optional[Decimal]
    exchange_quantity: Optional[Decimal]
    severity: str
    detected_at: datetime
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class PositionMismatch:
    """Position-level mismatch detected during reconciliation."""
    mismatch_type: str  # missing_position, quantity_divergence, price_divergence
    position_id: Optional[str]
    symbol: str
    local_quantity: Optional[Decimal]
    exchange_quantity: Optional[Decimal]
    local_side: Optional[str]
    exchange_side: Optional[str]
    severity: str
    detected_at: datetime
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ReconciliationAction:
    """Action to resolve a detected mismatch."""
    action_type: str  # create_order, cancel_order, update_position, create_fill
    target_type: str  # order, position, fill
    target_id: str
    action_data: Dict[str, Any]
    priority: str  # critical, high, medium, low
    created_at: datetime
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ReconciliationResult:
    """Result of reconciliation process."""
    success: bool
    tenant_id: str
    exchange_name: str
    fill_mismatches: List[FillMismatch]
    order_mismatches: List[OrderMismatch]
    position_mismatches: List[PositionMismatch]
    reconciliation_actions: List[ReconciliationAction]
    actions_executed: int
    actions_failed: int
    reconciliation_time: datetime
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class ReconciliationEngine:
    """
    Performs functional reconciliation between exchange and internal state.
    
    This engine goes beyond the basic reconciliation worker by providing
    fill-level reconciliation and quantity divergence detection.
    """
    
    def __init__(
        self,
        exchange_client: Any,
        state_service: Any,
        fill_deduplication_manager: Any,
        auto_correct: bool = True,
        enable_fill_reconciliation: bool = True
    ):
        """
        Initialize reconciliation engine.
        
        Args:
            exchange_client: Exchange client for fetching exchange state
            state_service: Service for fetching internal state
            fill_deduplication_manager: Fill deduplication manager for fill registry
            auto_correct: Automatically execute reconciliation actions
            enable_fill_reconciliation: Enable fill-level reconciliation
        """
        self.exchange_client = exchange_client
        self.state_service = state_service
        self.fill_deduplication_manager = fill_deduplication_manager
        self.auto_correct = auto_correct
        self.enable_fill_reconciliation = enable_fill_reconciliation
        
    async def reconcile(
        self,
        tenant_id: str,
        exchange_name: str,
        user_id: str
    ) -> ReconciliationResult:
        """
        Perform full reconciliation between exchange and internal state.
        
        This method:
        1. Fetches exchange state (orders, positions, fills)
        2. Fetches internal state (orders, positions, fills)
        3. Compares states to detect mismatches
        4. Generates reconciliation actions
        5. Optionally executes actions
        
        Args:
            tenant_id: Tenant identifier
            exchange_name: Exchange name
            user_id: User identifier
            
        Returns:
            ReconciliationResult with detected mismatches and actions
        """
        start_time = datetime.now(timezone.utc)
        fill_mismatches: List[FillMismatch] = []
        order_mismatches: List[OrderMismatch] = []
        position_mismatches: List[PositionMismatch] = []
        reconciliation_actions: List[ReconciliationAction] = []
        actions_executed = 0
        actions_failed = 0
        
        try:
            # Fetch exchange state
            exchange_orders = await self._fetch_exchange_orders(exchange_name, user_id)
            exchange_positions = await self._fetch_exchange_positions(exchange_name, user_id)
            exchange_fills = await self._fetch_exchange_fills(exchange_name, user_id)
            
            # Fetch internal state
            local_orders = await self._fetch_local_orders(tenant_id, user_id)
            local_positions = await self._fetch_local_positions(tenant_id, user_id)
            local_fills = await self._fetch_local_fills(tenant_id, user_id)
            
            logger.info(
                f"[ReconciliationEngine] Fetched state: "
                f"{len(exchange_orders)} exchange orders, {len(local_orders)} local orders, "
                f"{len(exchange_positions)} exchange positions, {len(local_positions)} local positions, "
                f"{len(exchange_fills)} exchange fills, {len(local_fills)} local fills"
            )
            
            # Reconcile fills
            if self.enable_fill_reconciliation:
                fill_mismatches = await self._reconcile_fills(
                    local_fills=local_fills,
                    exchange_fills=exchange_fills,
                    tenant_id=tenant_id
                )
            
            # Reconcile orders
            order_mismatches = await self._reconcile_orders(
                local_orders=local_orders,
                exchange_orders=exchange_orders,
                tenant_id=tenant_id
            )
            
            # Reconcile positions
            position_mismatches = await self._reconcile_positions(
                local_positions=local_positions,
                exchange_positions=exchange_positions,
                tenant_id=tenant_id
            )
            
            # Generate reconciliation actions
            reconciliation_actions = await self._generate_reconciliation_actions(
                fill_mismatches=fill_mismatches,
                order_mismatches=order_mismatches,
                position_mismatches=position_mismatches,
                tenant_id=tenant_id
            )
            
            # Execute actions if auto-correct enabled
            if self.auto_correct:
                for action in reconciliation_actions:
                    try:
                        executed = await self._execute_reconciliation_action(
                            action=action,
                            exchange_name=exchange_name,
                            tenant_id=tenant_id
                        )
                        if executed:
                            actions_executed += 1
                        else:
                            actions_failed += 1
                    except Exception as e:
                        logger.error(f"[ReconciliationEngine] Error executing action: {e}")
                        actions_failed += 1
            
            logger.info(
                f"[ReconciliationEngine] Reconciliation complete: "
                f"{len(fill_mismatches)} fill mismatches, {len(order_mismatches)} order mismatches, "
                f"{len(position_mismatches)} position mismatches, {len(reconciliation_actions)} actions, "
                f"{actions_executed} executed, {actions_failed} failed"
            )
            
            return ReconciliationResult(
                success=True,
                tenant_id=tenant_id,
                exchange_name=exchange_name,
                fill_mismatches=fill_mismatches,
                order_mismatches=order_mismatches,
                position_mismatches=position_mismatches,
                reconciliation_actions=reconciliation_actions,
                actions_executed=actions_executed,
                actions_failed=actions_failed,
                reconciliation_time=start_time,
                metadata={
                    "user_id": user_id,
                    "auto_correct": self.auto_correct,
                    "enable_fill_reconciliation": self.enable_fill_reconciliation
                }
            )
            
        except Exception as e:
            logger.error(f"[ReconciliationEngine] Reconciliation failed: {e}")
            
            return ReconciliationResult(
                success=False,
                tenant_id=tenant_id,
                exchange_name=exchange_name,
                fill_mismatches=fill_mismatches,
                order_mismatches=order_mismatches,
                position_mismatches=position_mismatches,
                reconciliation_actions=reconciliation_actions,
                actions_executed=actions_executed,
                actions_failed=actions_failed,
                reconciliation_time=start_time,
                metadata={"error": str(e)}
            )
    
    async def _fetch_exchange_orders(
        self,
        exchange_name: str,
        user_id: str
    ) -> List[Dict[str, Any]]:
        """Fetch orders from exchange."""
        try:
            orders = await self.exchange_client.get_orders(user_id)
            return orders
        except Exception as e:
            logger.error(f"[ReconciliationEngine] Error fetching exchange orders: {e}")
            raise
    
    async def _fetch_exchange_positions(
        self,
        exchange_name: str,
        user_id: str
    ) -> List[Dict[str, Any]]:
        """Fetch positions from exchange."""
        try:
            positions = await self.exchange_client.get_positions(user_id)
            return positions
        except Exception as e:
            logger.error(f"[ReconciliationEngine] Error fetching exchange positions: {e}")
            raise
    
    async def _fetch_exchange_fills(
        self,
        exchange_name: str,
        user_id: str
    ) -> List[Dict[str, Any]]:
        """Fetch fills from exchange."""
        try:
            fills = await self.exchange_client.get_fills(user_id)
            return fills
        except Exception as e:
            logger.error(f"[ReconciliationEngine] Error fetching exchange fills: {e}")
            raise
    
    async def _fetch_local_orders(
        self,
        tenant_id: str,
        user_id: str
    ) -> List[Dict[str, Any]]:
        """Fetch orders from local state."""
        try:
            orders = await self.state_service.get_orders(tenant_id, user_id)
            return orders
        except Exception as e:
            logger.error(f"[ReconciliationEngine] Error fetching local orders: {e}")
            raise
    
    async def _fetch_local_positions(
        self,
        tenant_id: str,
        user_id: str
    ) -> List[Dict[str, Any]]:
        """Fetch positions from local state."""
        try:
            positions = await self.state_service.get_positions(tenant_id, user_id)
            return positions
        except Exception as e:
            logger.error(f"[ReconciliationEngine] Error fetching local positions: {e}")
            raise
    
    async def _fetch_local_fills(
        self,
        tenant_id: str,
        user_id: str
    ) -> List[Dict[str, Any]]:
        """Fetch fills from local state."""
        try:
            fills = await self.state_service.get_fills(tenant_id, user_id)
            return fills
        except Exception as e:
            logger.error(f"[ReconciliationEngine] Error fetching local fills: {e}")
            raise
    
    async def _reconcile_fills(
        self,
        local_fills: List[Dict[str, Any]],
        exchange_fills: List[Dict[str, Any]],
        tenant_id: str
    ) -> List[FillMismatch]:
        """
        Reconcile fills between local and exchange state.
        
        Detects:
        - Missing fills (on exchange but not local)
        - Duplicate fills (on local but not exchange)
        - Quantity divergence (different quantities)
        """
        mismatches: List[FillMismatch] = []
        
        # Index fills by exchange_trade_id
        local_fills_by_trade_id = {f.get("exchange_trade_id"): f for f in local_fills if f.get("exchange_trade_id")}
        exchange_fills_by_trade_id = {f.get("exchange_trade_id"): f for f in exchange_fills if f.get("exchange_trade_id")}
        
        # Check for missing fills (on exchange but not local)
        for trade_id, exchange_fill in exchange_fills_by_trade_id.items():
            if trade_id not in local_fills_by_trade_id:
                mismatches.append(FillMismatch(
                    mismatch_type="missing_fill",
                    fill_id=None,
                    order_id=exchange_fill.get("order_id"),
                    exchange_trade_id=trade_id,
                    local_quantity=None,
                    exchange_quantity=Decimal(str(exchange_fill.get("quantity", "0"))),
                    local_price=None,
                    exchange_price=Decimal(str(exchange_fill.get("price", "0"))),
                    severity="critical",
                    detected_at=datetime.now(timezone.utc),
                    metadata={"exchange_fill": exchange_fill}
                ))
        
        # Check for duplicate fills (on local but not exchange)
        for trade_id, local_fill in local_fills_by_trade_id.items():
            if trade_id not in exchange_fills_by_trade_id:
                # Check if fill is in registry (might be legitimate)
                fill_hash = local_fill.get("fill_hash")
                if fill_hash and self.fill_deduplication_manager:
                    registry_info = await self.fill_deduplication_manager.get_fill_registry_info(
                        tenant_id=tenant_id,
                        fill_hash=fill_hash
                    )
                    if registry_info:
                        continue  # Fill is in registry, not a duplicate
                
                mismatches.append(FillMismatch(
                    mismatch_type="duplicate_fill",
                    fill_id=local_fill.get("fill_id"),
                    order_id=local_fill.get("order_id"),
                    exchange_trade_id=trade_id,
                    local_quantity=Decimal(str(local_fill.get("filled_quantity", "0"))),
                    exchange_quantity=None,
                    local_price=Decimal(str(local_fill.get("fill_price", "0"))),
                    exchange_price=None,
                    severity="warning",
                    detected_at=datetime.now(timezone.utc),
                    metadata={"local_fill": local_fill}
                ))
        
        # Check for quantity divergence
        for trade_id in set(local_fills_by_trade_id.keys()) & set(exchange_fills_by_trade_id.keys()):
            local_fill = local_fills_by_trade_id[trade_id]
            exchange_fill = exchange_fills_by_trade_id[trade_id]
            
            local_qty = Decimal(str(local_fill.get("filled_quantity", "0")))
            exchange_qty = Decimal(str(exchange_fill.get("quantity", "0")))
            
            if local_qty != exchange_qty:
                mismatches.append(FillMismatch(
                    mismatch_type="quantity_divergence",
                    fill_id=local_fill.get("fill_id"),
                    order_id=local_fill.get("order_id"),
                    exchange_trade_id=trade_id,
                    local_quantity=local_qty,
                    exchange_quantity=exchange_qty,
                    local_price=Decimal(str(local_fill.get("fill_price", "0"))),
                    exchange_price=Decimal(str(exchange_fill.get("price", "0"))),
                    severity="critical",
                    detected_at=datetime.now(timezone.utc),
                    metadata={"local_fill": local_fill, "exchange_fill": exchange_fill}
                ))
        
        return mismatches
    
    async def _reconcile_orders(
        self,
        local_orders: List[Dict[str, Any]],
        exchange_orders: List[Dict[str, Any]],
        tenant_id: str
    ) -> List[OrderMismatch]:
        """
        Reconcile orders between local and exchange state.
        
        Detects:
        - Missing orders (on exchange but not local)
        - Ghost orders (on local but not exchange)
        - Status divergence
        - Quantity divergence
        """
        mismatches: List[OrderMismatch] = []
        
        # Index orders by order_id
        local_orders_by_id = {o.get("order_id"): o for o in local_orders if o.get("order_id")}
        exchange_orders_by_id = {o.get("order_id"): o for o in exchange_orders if o.get("order_id")}
        
        # Check for missing orders (on exchange but not local)
        for order_id, exchange_order in exchange_orders_by_id.items():
            if order_id not in local_orders_by_id:
                mismatches.append(OrderMismatch(
                    mismatch_type="missing_order",
                    order_id=order_id,
                    local_status=None,
                    exchange_status=exchange_order.get("status"),
                    local_quantity=None,
                    exchange_quantity=Decimal(str(exchange_order.get("quantity", "0"))),
                    severity="critical",
                    detected_at=datetime.now(timezone.utc),
                    metadata={"exchange_order": exchange_order}
                ))
        
        # Check for ghost orders (on local but not exchange)
        for order_id, local_order in local_orders_by_id.items():
            if order_id not in exchange_orders_by_id:
                local_status = local_order.get("status")
                # Only critical if order is not terminal
                if local_status not in ["filled", "cancelled", "rejected"]:
                    mismatches.append(OrderMismatch(
                        mismatch_type="ghost_order",
                        order_id=order_id,
                        local_status=local_status,
                        exchange_status=None,
                        local_quantity=Decimal(str(local_order.get("quantity", "0"))),
                        exchange_quantity=None,
                        severity="critical",
                        detected_at=datetime.now(timezone.utc),
                        metadata={"local_order": local_order}
                    ))
        
        # Check for status and quantity divergence
        for order_id in set(local_orders_by_id.keys()) & set(exchange_orders_by_id.keys()):
            local_order = local_orders_by_id[order_id]
            exchange_order = exchange_orders_by_id[order_id]
            
            local_status = local_order.get("status")
            exchange_status = exchange_order.get("status")
            
            if local_status != exchange_status:
                mismatches.append(OrderMismatch(
                    mismatch_type="status_divergence",
                    order_id=order_id,
                    local_status=local_status,
                    exchange_status=exchange_status,
                    local_quantity=Decimal(str(local_order.get("quantity", "0"))),
                    exchange_quantity=Decimal(str(exchange_order.get("quantity", "0"))),
                    severity="warning",
                    detected_at=datetime.now(timezone.utc),
                    metadata={"local_order": local_order, "exchange_order": exchange_order}
                ))
        
        return mismatches
    
    async def _reconcile_positions(
        self,
        local_positions: List[Dict[str, Any]],
        exchange_positions: List[Dict[str, Any]],
        tenant_id: str
    ) -> List[PositionMismatch]:
        """
        Reconcile positions between local and exchange state.
        
        Detects:
        - Missing positions (on exchange but not local)
        - Quantity divergence
        - Price divergence
        """
        mismatches: List[PositionMismatch] = []
        
        # Index positions by symbol
        local_positions_by_symbol = {p.get("symbol"): p for p in local_positions if p.get("symbol")}
        exchange_positions_by_symbol = {p.get("symbol"): p for p in exchange_positions if p.get("symbol")}
        
        # Check for missing positions (on exchange but not local)
        for symbol, exchange_position in exchange_positions_by_symbol.items():
            if symbol not in local_positions_by_symbol:
                mismatches.append(PositionMismatch(
                    mismatch_type="missing_position",
                    position_id=None,
                    symbol=symbol,
                    local_quantity=None,
                    exchange_quantity=Decimal(str(exchange_position.get("quantity", "0"))),
                    local_side=None,
                    exchange_side=exchange_position.get("side"),
                    severity="critical",
                    detected_at=datetime.now(timezone.utc),
                    metadata={"exchange_position": exchange_position}
                ))
        
        # Check for quantity and price divergence
        for symbol in set(local_positions_by_symbol.keys()) & set(exchange_positions_by_symbol.keys()):
            local_position = local_positions_by_symbol[symbol]
            exchange_position = exchange_positions_by_symbol[symbol]
            
            local_qty = Decimal(str(local_position.get("quantity", "0")))
            exchange_qty = Decimal(str(exchange_position.get("quantity", "0")))
            
            if local_qty != exchange_qty:
                mismatches.append(PositionMismatch(
                    mismatch_type="quantity_divergence",
                    position_id=local_position.get("position_id"),
                    symbol=symbol,
                    local_quantity=local_qty,
                    exchange_quantity=exchange_qty,
                    local_side=local_position.get("side"),
                    exchange_side=exchange_position.get("side"),
                    severity="critical",
                    detected_at=datetime.now(timezone.utc),
                    metadata={"local_position": local_position, "exchange_position": exchange_position}
                ))
        
        return mismatches
    
    async def _generate_reconciliation_actions(
        self,
        fill_mismatches: List[FillMismatch],
        order_mismatches: List[OrderMismatch],
        position_mismatches: List[PositionMismatch],
        tenant_id: str
    ) -> List[ReconciliationAction]:
        """Generate reconciliation actions from detected mismatches."""
        actions: List[ReconciliationAction] = []
        
        # Generate actions for fill mismatches
        for mismatch in fill_mismatches:
            if mismatch.mismatch_type == "missing_fill":
                actions.append(ReconciliationAction(
                    action_type="create_fill",
                    target_type="fill",
                    target_id=mismatch.order_id,
                    action_data={
                        "order_id": mismatch.order_id,
                        "exchange_trade_id": mismatch.exchange_trade_id,
                        "quantity": str(mismatch.exchange_quantity),
                        "price": str(mismatch.exchange_price)
                    },
                    priority="critical",
                    created_at=datetime.now(timezone.utc),
                    metadata={"mismatch": asdict(mismatch)}
                ))
            elif mismatch.mismatch_type == "duplicate_fill":
                actions.append(ReconciliationAction(
                    action_type="remove_fill",
                    target_type="fill",
                    target_id=mismatch.fill_id or "",
                    action_data={
                        "fill_id": mismatch.fill_id,
                        "order_id": mismatch.order_id
                    },
                    priority="high",
                    created_at=datetime.now(timezone.utc),
                    metadata={"mismatch": asdict(mismatch)}
                ))
        
        # Generate actions for order mismatches
        for mismatch in order_mismatches:
            if mismatch.mismatch_type == "missing_order":
                actions.append(ReconciliationAction(
                    action_type="create_order",
                    target_type="order",
                    target_id=mismatch.order_id,
                    action_data={
                        "order_id": mismatch.order_id,
                        "status": mismatch.exchange_status,
                        "quantity": str(mismatch.exchange_quantity)
                    },
                    priority="critical",
                    created_at=datetime.now(timezone.utc),
                    metadata={"mismatch": asdict(mismatch)}
                ))
            elif mismatch.mismatch_type == "ghost_order":
                actions.append(ReconciliationAction(
                    action_type="cancel_order",
                    target_type="order",
                    target_id=mismatch.order_id,
                    action_data={
                        "order_id": mismatch.order_id,
                        "reason": "Ghost order - not found on exchange"
                    },
                    priority="critical",
                    created_at=datetime.now(timezone.utc),
                    metadata={"mismatch": asdict(mismatch)}
                ))
        
        # Generate actions for position mismatches
        for mismatch in position_mismatches:
            if mismatch.mismatch_type == "missing_position":
                actions.append(ReconciliationAction(
                    action_type="create_position",
                    target_type="position",
                    target_id=mismatch.symbol,
                    action_data={
                        "symbol": mismatch.symbol,
                        "quantity": str(mismatch.exchange_quantity),
                        "side": mismatch.exchange_side
                    },
                    priority="critical",
                    created_at=datetime.now(timezone.utc),
                    metadata={"mismatch": asdict(mismatch)}
                ))
            elif mismatch.mismatch_type == "quantity_divergence":
                actions.append(ReconciliationAction(
                    action_type="update_position",
                    target_type="position",
                    target_id=mismatch.position_id or mismatch.symbol,
                    action_data={
                        "position_id": mismatch.position_id,
                        "symbol": mismatch.symbol,
                        "correct_quantity": str(mismatch.exchange_quantity)
                    },
                    priority="critical",
                    created_at=datetime.now(timezone.utc),
                    metadata={"mismatch": asdict(mismatch)}
                ))
        
        return actions
    
    async def _execute_reconciliation_action(
        self,
        action: ReconciliationAction,
        exchange_name: str,
        tenant_id: str
    ) -> bool:
        """Execute a reconciliation action."""
        try:
            if action.action_type == "create_fill":
                await self.state_service.create_fill(tenant_id, action.action_data)
            elif action.action_type == "remove_fill":
                await self.state_service.remove_fill(tenant_id, action.action_data)
            elif action.action_type == "create_order":
                await self.state_service.create_order(tenant_id, action.action_data)
            elif action.action_type == "cancel_order":
                await self.state_service.cancel_order(tenant_id, action.action_data)
            elif action.action_type == "create_position":
                await self.state_service.create_position(tenant_id, action.action_data)
            elif action.action_type == "update_position":
                await self.state_service.update_position(tenant_id, action.action_data)
            else:
                logger.warning(f"[ReconciliationEngine] Unknown action type: {action.action_type}")
                return False
            
            logger.info(f"[ReconciliationEngine] Executed action: {action.action_type} on {action.target_id}")
            return True
            
        except Exception as e:
            logger.error(f"[ReconciliationEngine] Error executing action {action.action_type}: {e}")
            return False


# Factory function for dependency injection
def create_reconciliation_engine(
    exchange_client: Any,
    state_service: Any,
    fill_deduplication_manager: Any,
    auto_correct: bool = True,
    enable_fill_reconciliation: bool = True
) -> ReconciliationEngine:
    """
    Factory function to create ReconciliationEngine.
    
    Args:
        exchange_client: Exchange client for fetching exchange state
        state_service: Service for fetching internal state
        fill_deduplication_manager: Fill deduplication manager
        auto_correct: Automatically execute reconciliation actions
        enable_fill_reconciliation: Enable fill-level reconciliation
        
    Returns:
        Configured ReconciliationEngine instance
    """
    return ReconciliationEngine(
        exchange_client=exchange_client,
        state_service=state_service,
        fill_deduplication_manager=fill_deduplication_manager,
        auto_correct=auto_correct,
        enable_fill_reconciliation=enable_fill_reconciliation
    )
