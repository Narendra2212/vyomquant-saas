"""
Position Engine

STEP 4.2 — PORTFOLIO + PnL CONSISTENCY

Updates positions from execution fills.
Handles incremental position updates with financial precision.

Update Flow:
┌─────────────────────────────────────────────────────────────────┐
│  update_position_from_fill()                                     │
│                                                                  │
│  1. Determine position side from execution:                      │
│     buy  order with no position → OPEN LONG                     │
│     sell order with no position → OPEN SHORT                    │
│     buy  order with SHORT position → REDUCE/FLIP SHORT          │
│     sell order with LONG position  → REDUCE/FLIP LONG           │
│                                                                  │
│  2. IF position exists:                                          │
│     └─ update_position_incremental()                            │
│                                                                  │
│  3. IF position does NOT exist:                                  │
│     └─ create_position_from_fill()                              │
│                                                                  │
│  4. UPDATE position:                                             │
│     ├─ size (add or subtract)                                   │
│     ├─ avg_entry_price (recalculate if adding)                  │
│     ├─ realized_pnl (if closing portion)                        │
│     └─ unrealized_pnl (recalculate)                             │
│                                                                  │
│  5. PERSIST to DB                                               │
│                                                                  │
│  6. RETURN updated position                                     │
└─────────────────────────────────────────────────────────────────┘
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
import logging

from sqlalchemy.orm import Session

from backend_app.core.position_model import (
    PositionModel, PositionSide, PositionStatus,
    PositionCalculator, PositionRepository, get_position_repository,
    PositionUpdate, PositionCreate
)
from backend_app.core.models.execution_record import ExecutionRecordModel

logger = logging.getLogger(__name__)


class PositionEngine:
    """
    Position update engine.
    
    STEP 4.2: Updates positions from execution fills.
    STEP 4.3: Handles partial fills incrementally.
    
    Guarantees:
    1. Position updated IMMEDIATELY on every fill
    2. Financial calculations use Decimal precision
    3. Average price recalculated correctly
    4. PnL tracked accurately
    
    Usage:
        engine = PositionEngine(db_session)
        
        # Update from fill
        position = await engine.update_position_from_fill(execution_record)
    """
    
    PRECISION = Decimal('0.00000001')
    
    def __init__(self, db_session: Session):
        """Initialize position engine."""
        self.db = db_session
        self.repo = get_position_repository(db_session)
        self.calculator = PositionCalculator()
    
    async def update_position_from_fill(
        self,
        execution_record: ExecutionRecordModel,
        fill_size: Optional[str] = None,
        fill_price: Optional[str] = None
    ) -> Optional[PositionModel]:
        """
        STEP 4.2: Update or create position from execution fill.
        
        STEP 4.3: Handles partial fills incrementally.
        
        Args:
            execution_record: Execution record with fill data
            fill_size: Override fill size (for partial fills)
            fill_price: Override fill price (for partial fills)
            
        Returns:
            Updated or created position
        """
        try:
            # Extract fill data
            tenant_id = execution_record.tenant_id
            strategy_id = execution_record.strategy_id
            symbol = execution_record.symbol
            execution_side = execution_record.side  # 'buy' or 'sell'
            
            # Use provided values or from execution record
            size_to_fill = Decimal(fill_size or execution_record.filled_size or execution_record.size)
            fill_price_val = Decimal(fill_price or execution_record.avg_price or "0")
            
            if size_to_fill <= 0:
                logger.warning(f"No fill size for execution {execution_record.execution_id}")
                return None
            
            # Determine position side from execution side
            # buy → LONG (acquiring asset)
            # sell → SHORT (selling asset we don't have, or reducing long)
            if execution_side == 'buy':
                position_side = PositionSide.LONG
            else:
                position_side = PositionSide.SHORT
            
            logger.info(
                f"POSITION UPDATE: execution={execution_record.execution_id} | "
                f"{symbol} {execution_side} | "
                f"fill_size={size_to_fill} @ {fill_price_val}"
            )
            
            # Find existing open position
            existing_position = self.repo.find_open_position(
                tenant_id=tenant_id,
                strategy_id=strategy_id,
                symbol=symbol,
                side=position_side
            )
            
            if existing_position:
                # Update existing position
                position = await self._update_position_incremental(
                    position=existing_position,
                    fill_size=size_to_fill,
                    fill_price=fill_price_val,
                    execution_side=execution_side
                )
            else:
                # Check for opposite side position (reducing or flipping)
                opposite_side = PositionSide.SHORT if position_side == PositionSide.LONG else PositionSide.LONG
                opposite_position = self.repo.find_open_position(
                    tenant_id=tenant_id,
                    strategy_id=strategy_id,
                    symbol=symbol,
                    side=opposite_side
                )
                
                if opposite_position:
                    # Reducing or flipping opposite position
                    position = await self._handle_position_reduction(
                        position=opposite_position,
                        reducing_size=size_to_fill,
                        reducing_price=fill_price_val,
                        execution_side=execution_side
                    )
                else:
                    # Create new position
                    position = await self._create_position_from_fill(
                        execution_record=execution_record,
                        fill_size=size_to_fill,
                        fill_price=fill_price_val,
                        position_side=position_side
                    )
            
            # STEP 4.9: REAL-TIME UPDATE - position updated immediately
            logger.info(
                f"POSITION REAL-TIME UPDATE: {position.position_id} | "
                f"size={position.size} | "
                f"avg_price={position.avg_entry_price} | "
                f"unrealized_pnl={position.unrealized_pnl} | "
                f"realized_pnl={position.realized_pnl}"
            )
            
            return position
            
        except Exception as e:
            logger.error(
                f"Failed to update position from fill: {execution_record.execution_id} | {e}",
                exc_info=True
            )
            raise
    
    async def _create_position_from_fill(
        self,
        execution_record: ExecutionRecordModel,
        fill_size: Decimal,
        fill_price: Decimal,
        position_side: PositionSide
    ) -> PositionModel:
        """
        STEP 4.2: Create new position from fill.
        """
        position_id = f"pos_{execution_record.execution_id}"
        
        # Create position
        position_data = PositionCreate(
            position_id=position_id,
            tenant_id=execution_record.tenant_id,
            strategy_id=execution_record.strategy_id,
            symbol=execution_record.symbol,
            side=position_side,
            size=str(fill_size),
            avg_entry_price=str(fill_price),
            unrealized_pnl="0.0",
            realized_pnl="0.0",
            status=PositionStatus.OPEN
        )
        
        position = self.repo.create(position_data)
        
        logger.info(
            f"POSITION CREATED FROM FILL: {position_id} | "
            f"{position.symbol} {position_side.value} | "
            f"size={fill_size} @ {fill_price}"
        )
        
        return position
    
    async def _update_position_incremental(
        self,
        position: PositionModel,
        fill_size: Decimal,
        fill_price: Decimal,
        execution_side: str
    ) -> PositionModel:
        """
        STEP 4.3: Update position incrementally from fill.
        
        This is called for each partial or complete fill.
        Does NOT wait for full fill.
        """
        current_size = Decimal(position.size)
        current_avg_price = Decimal(position.avg_entry_price)
        current_realized_pnl = Decimal(position.realized_pnl)
        
        # Calculate new average price (adding to position)
        new_avg_price = self.calculator.calculate_new_avg_entry(
            current_size=current_size,
            current_avg_price=current_avg_price,
            fill_size=fill_size,
            fill_price=fill_price
        )
        
        # New total size
        new_size = current_size + fill_size
        
        # Realized PnL doesn't change when adding to position
        new_realized_pnl = current_realized_pnl
        
        # Update position
        update_data = PositionUpdate(
            size=str(new_size),
            avg_entry_price=str(new_avg_price),
            realized_pnl=str(new_realized_pnl)
        )
        
        updated_position = self.repo.update(
            position_id=position.position_id,
            tenant_id=position.tenant_id,
            data=update_data
        )
        
        logger.info(
            f"POSITION INCREMENTAL UPDATE: {position.position_id} | "
            f"added {fill_size} @ {fill_price} | "
            f"new_size={new_size} | "
            f"new_avg_price={new_avg_price}"
        )
        
        return updated_position
    
    async def _handle_position_reduction(
        self,
        position: PositionModel,
        reducing_size: Decimal,
        reducing_price: Decimal,
        execution_side: str
    ) -> PositionModel:
        """
        STEP 5.1: Hardened position reduction/flip logic.
        
        Handles:
        - Partial reduction (same side, smaller size)
        - Full close (size goes to 0)
        - Position flip (size crosses 0, opens opposite side)
        
        CRITICAL: Position flip is TWO distinct operations:
        1. CLOSE existing position (record realized PnL, save to trade_history)
        2. OPEN new opposite position (fresh entry_price, fresh size)
        
        NEVER:
        - Overwrite cost_basis of existing position
        - Mix long/short in same position record
        - Carry over realized_pnl to new position
        """
        current_size = Decimal(position.size)
        current_avg_price = Decimal(position.avg_entry_price)
        current_realized_pnl = Decimal(position.realized_pnl)
        position_side = position.side
        
        # STEP 5.1: Calculate realized PnL from closing portion
        # For position flip, we close the ENTIRE current position first
        if reducing_size >= current_size:
            # Closing full position (or flipping)
            closing_pnl = self.calculator.calculate_realized_pnl(
                exit_size=current_size,  # Close entire position
                exit_price=reducing_price,
                avg_entry_price=current_avg_price,
                side=position_side
            )
            
            # Total realized PnL for the closed position
            total_realized_pnl = current_realized_pnl + closing_pnl
            
            # Calculate flip size (remaining after closing current position)
            flip_size = reducing_size - current_size
            
            if flip_size > 0:
                # ═══════════════════════════════════════════════════════════
                # POSITION FLIP: Two distinct atomic operations
                # ═══════════════════════════════════════════════════════════
                
                # OPERATION 1: CLOSE existing position completely
                closed_position = self.repo.close_position(
                    position_id=position.position_id,
                    tenant_id=position.tenant_id,
                    final_realized_pnl=str(total_realized_pnl)
                )
                
                # STEP 5.1: Record trade in trade_history for audit
                await self._record_trade(
                    position=position,
                    exit_price=reducing_price,
                    exit_size=current_size,
                    realized_pnl=total_realized_pnl,
                    close_reason="position_flip_close"
                )
                
                # OPERATION 2: OPEN new opposite position with REMAINING size
                opposite_side = PositionSide.SHORT if position_side == PositionSide.LONG else PositionSide.LONG
                
                # Create execution record for the flip portion
                flip_execution = ExecutionRecordModel(
                    execution_id=f"flip_{position.position_id}_{datetime.now().timestamp()}",
                    tenant_id=position.tenant_id,
                    strategy_id=position.strategy_id,
                    symbol=position.symbol,
                    side='sell' if opposite_side == PositionSide.SHORT else 'buy',
                    size=str(flip_size),
                    filled_size=str(flip_size),
                    avg_price=str(reducing_price)
                )
                
                flipped_position = await self._create_position_from_fill(
                    execution_record=flip_execution,
                    fill_size=flip_size,
                    fill_price=reducing_price,
                    position_side=opposite_side
                )
                
                # STEP 5.1: Record trade for new position open
                await self._record_trade(
                    position=flipped_position,
                    exit_price=reducing_price,
                    exit_size=flip_size,
                    realized_pnl=Decimal("0"),  # Fresh position, no realized PnL yet
                    close_reason="position_flip_open"
                )
                
                logger.info(
                    f"🔄 POSITION FLIPPED (STEP 5.1): {position.position_id} [{position_side.value}] → "
                    f"{flipped_position.position_id} [{opposite_side.value}] | "
                    f"closed_size={current_size} @ {reducing_price} | "
                    f"realized_pnl={total_realized_pnl} | "
                    f"new_position_size={flip_size} @ {reducing_price}"
                )
                
                return flipped_position
            else:
                # Full close (no flip, size exactly matched)
                closed_position = self.repo.close_position(
                    position_id=position.position_id,
                    tenant_id=position.tenant_id,
                    final_realized_pnl=str(total_realized_pnl)
                )
                
                await self._record_trade(
                    position=position,
                    exit_price=reducing_price,
                    exit_size=current_size,
                    realized_pnl=total_realized_pnl,
                    close_reason="full_close"
                )
                
                logger.info(
                    f"✅ POSITION CLOSED (STEP 5.1): {position.position_id} | "
                    f"size={current_size} @ {reducing_price} | "
                    f"realized_pnl={total_realized_pnl}"
                )
                
                return closed_position
        else:
            # ═══════════════════════════════════════════════════════════
            # PARTIAL REDUCTION: Reduce position size, update realized PnL
            # ═══════════════════════════════════════════════════════════
            
            closing_pnl = self.calculator.calculate_realized_pnl(
                exit_size=reducing_size,
                exit_price=reducing_price,
                avg_entry_price=current_avg_price,
                side=position_side
            )
            
            # Update realized PnL (accumulates)
            new_realized_pnl = current_realized_pnl + closing_pnl
            
            # New remaining size
            new_size = current_size - reducing_size
            
            # Update position with new size and accumulated realized PnL
            # IMPORTANT: avg_entry_price stays the same (we didn't add to position)
            update_data = PositionUpdate(
                size=str(new_size),
                realized_pnl=str(new_realized_pnl)
                # DO NOT update avg_entry_price - we reduced, not added
            )
            
            updated_position = self.repo.update(
                position_id=position.position_id,
                tenant_id=position.tenant_id,
                data=update_data
            )
            
            await self._record_trade(
                position=position,
                exit_price=reducing_price,
                exit_size=reducing_size,
                realized_pnl=closing_pnl,
                close_reason="partial_close"
            )
            
            logger.info(
                f"📉 POSITION PARTIALLY CLOSED (STEP 5.1): {position.position_id} | "
                f"closed {reducing_size} @ {reducing_price} | "
                f"realized_pnl={closing_pnl} | "
                f"remaining={new_size} | "
                f"avg_entry_price unchanged={current_avg_price}"
            )
            
            return updated_position
    
    async def _record_trade(
        self,
        position: PositionModel,
        exit_price: Decimal,
        exit_size: Decimal,
        realized_pnl: Decimal,
        close_reason: str
    ):
        """
        STEP 5.1: Record trade in trade history for audit trail.
        
        This ensures every position transition is traceable and auditable.
        """
        try:
            # Import trade history model
            from backend_app.core.models.trade_history import TradeHistoryModel, TradeCreate
            
            trade_data = TradeCreate(
                trade_id=f"trade_{position.position_id}_{datetime.now().timestamp()}",
                tenant_id=position.tenant_id,
                strategy_id=position.strategy_id,
                symbol=position.symbol,
                side=position.side.value,
                entry_price=position.avg_entry_price,
                exit_price=str(exit_price),
                size=str(exit_size),
                realized_pnl=str(realized_pnl),
                close_reason=close_reason,
                closed_at=datetime.now()
            )
            
            # Persist to trade history
            trade = TradeHistoryModel(**trade_data.dict())
            self.db.add(trade)
            self.db.commit()
            
            logger.debug(
                f"TRADE RECORDED: {trade.trade_id} | "
                f"{position.symbol} {position.side.value} | "
                f"exit={exit_price} | "
                f"realized_pnl={realized_pnl} | "
                f"reason={close_reason}"
            )
            
        except Exception as e:
            # Don't fail position update if trade recording fails
            # But log error for investigation
            logger.error(f"Failed to record trade for position {position.position_id}: {e}")
    
    async def update_unrealized_pnl(
        self,
        position: PositionModel,
        current_market_price: Decimal
    ) -> PositionModel:
        """
        Update unrealized PnL for a position.
        
        Formula: (current_price - avg_entry) × size (for long)
        """
        size = Decimal(position.size)
        avg_price = Decimal(position.avg_entry_price)
        
        unrealized_pnl = self.calculator.calculate_unrealized_pnl(
            size=size,
            avg_entry_price=avg_price,
            current_price=current_market_price,
            side=position.side
        )
        
        update_data = PositionUpdate(
            unrealized_pnl=str(unrealized_pnl)
        )
        
        updated_position = self.repo.update(
            position_id=position.position_id,
            tenant_id=position.tenant_id,
            data=update_data
        )
        
        logger.debug(
            f"UNREALIZED PnL UPDATE: {position.position_id} | "
            f"market_price={current_market_price} | "
            f"unrealized_pnl={unrealized_pnl}"
        )
        
        return updated_position
    
    async def update_all_positions_unrealized_pnl(
        self,
        tenant_id: str,
        price_lookup: Dict[str, Decimal]
    ) -> Dict[str, PositionModel]:
        """
        Update unrealized PnL for all open positions.
        
        Args:
            tenant_id: Tenant UUID
            price_lookup: Dict of symbol -> current_price
            
        Returns:
            Dict of position_id -> updated_position
        """
        open_positions = self.repo.list_open_positions(
            tenant_id=tenant_id
        )
        
        updated_positions = {}
        
        for position in open_positions:
            symbol = position.symbol
            current_price = price_lookup.get(symbol)
            
            if current_price:
                updated = await self.update_unrealized_pnl(position, current_price)
                updated_positions[position.position_id] = updated
            else:
                logger.warning(f"No market price for {symbol}, skipping PnL update")
        
        return updated_positions


# Global instance
def get_position_engine(db_session: Session) -> PositionEngine:
    """Get position engine instance."""
    return PositionEngine(db_session)
