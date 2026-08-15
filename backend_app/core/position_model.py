"""
Position Model

STEP 4.1 — PORTFOLIO + PnL CONSISTENCY

Tracks positions with financial correctness.
Each position represents an open holding in a symbol.

Position Lifecycle:
┌─────────────────────────────────────────────────────────────────┐
│  Position Model                                                  │
│                                                                  │
│  OPEN position:                                                  │
│  ├─ position_id: unique identifier                               │
│  ├─ tenant_id: owner                                             │
│  ├─ strategy_id: strategy that opened position                   │
│  ├─ symbol: BTCUSD, ETHUSD, etc.                                 │
│  ├─ side: long or short                                          │
│  ├─ size: quantity held                                          │
│  ├─ avg_entry_price: average entry price                        │
│  ├─ unrealized_pnl: profit/loss not yet realized                │
│  ├─ realized_pnl: profit/loss from closed portion             │
│  ├─ status: open                                                  │
│  └─ created_at: when position opened                              │
│                                                                  │
│  CLOSED position:                                                │
│  ├─ size: 0                                                      │
│  ├─ unrealized_pnl: 0                                            │
│  ├─ realized_pnl: final PnL                                      │
│  ├─ status: closed                                               │
│  └─ closed_at: when position closed                             │
└─────────────────────────────────────────────────────────────────┘
"""

import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, validator
from sqlalchemy import Column, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from backend_app.core.database import Base

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class PositionSide(str, Enum):
    """Position side - long or short."""
    LONG = "long"
    SHORT = "short"


class PositionStatus(str, Enum):
    """
    STEP 5.3: Position state machine states.
    
    Valid transitions:
        OPEN → PARTIAL → CLOSED
        OPEN → CLOSED (full close)
        PARTIAL → CLOSED (complete close)
    
    Invalid transitions (raise InvalidStateTransition):
        CLOSED → OPEN (must create new position)
        CLOSED → PARTIAL (impossible)
        PARTIAL → OPEN (redundant)
    """
    OPEN = "open"           # Position fully open, no partial closes
    PARTIAL = "partial"     # Position partially closed, still holding some
    CLOSED = "closed"       # Position fully closed, size = 0
    LIQUIDATED = "liquidated"  # Position liquidated (forced close)


class InvalidStateTransition(Exception):
    """
    STEP 5.3: Raised when attempting an invalid position state transition.
    
    Example:
        CLOSED → OPEN (invalid - must create new position)
        CLOSED → PARTIAL (impossible)
    """
    pass


class NegativePositionSize(Exception):
    """
    STEP 5.3: Raised when position size would become negative.
    
    This prevents data corruption from over-closing positions.
    """
    pass


class PositionStateMachine:
    """
    STEP 5.3: Position State Machine
    
    Enforces valid state transitions and validates position lifecycle.
    
    States:
        OPEN:     Position fully open, size > 0, no partial closes
        PARTIAL:  Position partially closed, 0 < size < original_size
        CLOSED:   Position fully closed, size = 0
    
    Valid Transitions:
        OPEN → PARTIAL:   Partial close (reduces size, adds to realized_pnl)
        OPEN → CLOSED:    Full close (size → 0, all PnL realized)
        PARTIAL → CLOSED: Complete remaining close
    
    Invalid Transitions (raise InvalidStateTransition):
        CLOSED → *:       Cannot transition from closed (terminal state)
        * → OPEN:         OPEN is only initial state
        PARTIAL → OPEN:   Cannot revert to OPEN from PARTIAL
        
    Validation Rules:
        1. No negative size at any point
        2. No transitions from CLOSED state
        3. Size must decrease when closing (never increase)
    """
    
    # Define valid transitions as adjacency list
    VALID_TRANSITIONS = {
        PositionStatus.OPEN: [PositionStatus.PARTIAL, PositionStatus.CLOSED, PositionStatus.LIQUIDATED],
        PositionStatus.PARTIAL: [PositionStatus.CLOSED, PositionStatus.LIQUIDATED],
        PositionStatus.CLOSED: [],  # Terminal state - no exits
        PositionStatus.LIQUIDATED: [],  # Terminal state - no exits
    }
    
    @classmethod
    def validate_transition(
        cls,
        from_state: PositionStatus,
        to_state: PositionStatus,
        current_size: Decimal,
        new_size: Decimal,
        original_size: Decimal
    ):
        """
        Validate a state transition and size change.
        
        Args:
            from_state: Current state
            to_state: Target state
            current_size: Size before transition
            new_size: Size after transition
            original_size: Size at position open
            
        Raises:
            InvalidStateTransition: If transition is invalid
            NegativePositionSize: If new_size would be negative
        """
        # STEP 5.3: Check 1 - No negative size allowed
        if new_size < Decimal('0'):
            raise NegativePositionSize(
                f"Cannot reduce position below zero: "
                f"current={current_size}, requested reduction would result in {new_size}"
            )
        
        # STEP 5.3: Check 2 - Validate state transition
        valid_next_states = cls.VALID_TRANSITIONS.get(from_state, [])
        if to_state not in valid_next_states:
            raise InvalidStateTransition(
                f"Invalid state transition: {from_state.value} → {to_state.value}. "
                f"Valid transitions from {from_state.value}: "
                f"{[s.value for s in valid_next_states] if valid_next_states else 'NONE (terminal state)'}"
            )
        
        # STEP 5.3: Check 3 - Size must decrease when closing
        if to_state in [PositionStatus.CLOSED, PositionStatus.PARTIAL, PositionStatus.LIQUIDATED]:
            if new_size >= current_size:
                raise InvalidStateTransition(
                    f"Closing transition requires size reduction: "
                    f"current={current_size}, new={new_size}"
                )
        
        # STEP 5.3: Check 4 - State-specific validation
        if to_state == PositionStatus.CLOSED and new_size != Decimal('0'):
            raise InvalidStateTransition(
                f"CLOSED state requires size=0, got {new_size}"
            )
        
        if to_state == PositionStatus.PARTIAL:
            if new_size == Decimal('0'):
                raise InvalidStateTransition(
                    f"PARTIAL state requires size > 0, got {new_size}. Use CLOSED instead."
                )
            if new_size >= original_size:
                raise InvalidStateTransition(
                    f"PARTIAL state requires size < original_size ({original_size}), got {new_size}"
                )
        
        # Transition is valid
        return True
    
    @classmethod
    def determine_state_from_size(
        cls,
        original_size: Decimal,
        current_size: Decimal,
        previous_state: PositionStatus
    ) -> PositionStatus:
        """
        Determine appropriate state based on size.
        
        Args:
            original_size: Size when position was opened
            current_size: Current size after operation
            previous_state: State before this operation
            
        Returns:
            Appropriate PositionStatus
        """
        if current_size == Decimal('0'):
            return PositionStatus.CLOSED
        elif current_size < original_size and previous_state == PositionStatus.OPEN:
            return PositionStatus.PARTIAL
        elif current_size < original_size and previous_state == PositionStatus.PARTIAL:
            # Still partial, or transition to closed if size is now 0
            return PositionStatus.PARTIAL if current_size > 0 else PositionStatus.CLOSED
        elif current_size == original_size:
            return PositionStatus.OPEN
        else:
            # Size increased - this is an increase, not a close
            return previous_state
    
    @classmethod
    def is_terminal_state(cls, state: PositionStatus) -> bool:
        """Check if state is terminal (no further transitions allowed)."""
        return state in [PositionStatus.CLOSED, PositionStatus.LIQUIDATED]
    
    @classmethod
    def can_close(cls, state: PositionStatus) -> bool:
        """Check if position can be closed from current state."""
        return state in [PositionStatus.OPEN, PositionStatus.PARTIAL]


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class PositionBase(BaseModel):
    """Base position model."""
    position_id: Optional[str] = Field(default=None, description="Unique position identifier")
    tenant_id: UUID = Field(..., description="Tenant UUID")
    strategy_id: str = Field(..., description="Strategy identifier")
    exchange_id: Optional[str] = Field(default=None, description="Exchange identifier")
    symbol: str = Field(..., description="Trading symbol (e.g., BTCUSD)")
    side: PositionSide = Field(..., description="long or short")
    
    # Position size and price (stored as string for precision)
    size: str = Field(..., description="Position size")
    avg_entry_price: str = Field(..., description="Average entry price")
    
    # PnL tracking (stored as string for precision)
    unrealized_pnl: str = Field(default="0.0", description="Unrealized profit/loss")
    realized_pnl: str = Field(default="0.0", description="Realized profit/loss")
    
    # STEP 5.3: Position state machine
    status: PositionStatus = Field(default=PositionStatus.OPEN)
    original_size: str = Field(default="0", description="Original size at position open (for partial tracking)")
    
    @validator('symbol')
    def validate_symbol(cls, v):
        if not v or not v.strip():
            raise ValueError('Symbol cannot be empty')
        return v.strip().upper()
    
    @validator('size')
    def validate_size_non_negative(cls, v):
        """STEP 5.3: Prevent negative position sizes."""
        size_dec = Decimal(v)
        if size_dec < Decimal('0'):
            raise ValueError(f'Position size cannot be negative: {v}')
        return v
    
    @validator('size', 'avg_entry_price', 'unrealized_pnl', 'realized_pnl')
    def validate_decimal(cls, v):
        """Ensure numeric values are valid decimals."""
        if isinstance(v, (int, float)):
            v = str(v)
        try:
            Decimal(v)
        except Exception:
            raise ValueError(f'Invalid decimal value: {v}')
        return v
    
    class Config:
        json_encoders = {
            UUID: lambda v: str(v),
            Decimal: lambda v: str(v)
        }


class PositionCreate(PositionBase):
    """Model for creating a new position."""
    pass


class PositionUpdate(BaseModel):
    """Model for updating a position."""
    size: Optional[str] = None
    avg_entry_price: Optional[str] = None
    unrealized_pnl: Optional[str] = None
    realized_pnl: Optional[str] = None
    status: Optional[PositionStatus] = None


class PositionInDB(PositionBase):
    """Position model as stored in database."""
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


class PositionPnL(BaseModel):
    """PnL breakdown for a position."""
    position_id: str
    symbol: str
    side: PositionSide
    size: str
    avg_entry_price: str
    current_price: str
    unrealized_pnl: str
    unrealized_pnl_pct: str
    realized_pnl: str
    total_pnl: str


class PositionFill(BaseModel):
    """Record of a fill that affected this position."""
    execution_id: str
    fill_size: str
    fill_price: str
    fill_time: datetime
    realized_pnl_from_fill: str = "0.0"


class PositionResponse(PositionInDB):
    """API response model with computed fields."""
    current_price: Optional[str] = None
    total_pnl: str = "0.0"
    fill_history: List[PositionFill] = []


# ═══════════════════════════════════════════════════════════════════════════════
# SQLALCHEMY ORM MODEL
# ═══════════════════════════════════════════════════════════════════════════════

class PositionModel(Base):
    """
    SQLAlchemy ORM model for positions table.
    
    STEP 4.1 — Position tracking with financial precision.
    
    All numeric values stored as strings (Decimal) to maintain precision.
    """
    __tablename__ = "positions"
    
    # Primary key
    position_id = Column(String, primary_key=True)
    
    # Tenant isolation
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    
    # Relationships
    strategy_id = Column(String, nullable=False, index=True)
    exchange_id = Column(String, nullable=True, index=True)
    
    # Position details
    symbol = Column(String, nullable=False)
    side = Column(SQLEnum(PositionSide), nullable=False)
    
    # STEP 4: Financial precision - store as strings
    size = Column(String, nullable=False)  # "0.10000000"
    avg_entry_price = Column(String, nullable=False)  # "50000.00000000"
    
    # PnL tracking
    unrealized_pnl = Column(String, nullable=False, default="0.0")
    realized_pnl = Column(String, nullable=False, default="0.0")
    
    # Status
    status = Column(SQLEnum(PositionStatus), nullable=False, default=PositionStatus.OPEN)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Indexes for STEP 4 queries
    __table_args__ = (
        Index('idx_positions_tenant_symbol', 'tenant_id', 'symbol'),
        Index('idx_positions_tenant_strategy', 'tenant_id', 'strategy_id'),
        Index('idx_positions_status', 'status'),
        Index('idx_positions_open', 'tenant_id', 'status', 
              postgresql_where=(status == 'open')),
    )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "position_id": self.position_id,
            "tenant_id": str(self.tenant_id),
            "strategy_id": self.strategy_id,
            "exchange_id": self.exchange_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "size": self.size,
            "avg_entry_price": self.avg_entry_price,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None
        }


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION CALCULATIONS
# ═══════════════════════════════════════════════════════════════════════════════

class PositionCalculator:
    """
    Financial calculations for positions.
    
    All calculations use Decimal for precision.
    """
    
    PRECISION = Decimal('0.00000001')  # 8 decimal places
    
    @staticmethod
    def calculate_unrealized_pnl(
        size: Decimal,
        avg_entry_price: Decimal,
        current_price: Decimal,
        side: PositionSide
    ) -> Decimal:
        """
        Calculate unrealized PnL.
        
        Formula:
        Long:  (current_price - avg_entry) × size
        Short: (avg_entry - current_price) × size
        """
        if side == PositionSide.LONG:
            price_diff = current_price - avg_entry_price
        else:  # SHORT
            price_diff = avg_entry_price - current_price
        
        pnl = price_diff * size
        return pnl.quantize(PositionCalculator.PRECISION, rounding=ROUND_HALF_UP)
    
    @staticmethod
    def calculate_realized_pnl(
        exit_size: Decimal,
        exit_price: Decimal,
        avg_entry_price: Decimal,
        side: PositionSide
    ) -> Decimal:
        """
        Calculate realized PnL from a closing fill.
        
        Formula:
        Long:  (exit_price - avg_entry) × exit_size
        Short: (avg_entry - exit_price) × exit_size
        """
        if side == PositionSide.LONG:
            price_diff = exit_price - avg_entry_price
        else:  # SHORT
            price_diff = avg_entry_price - exit_price
        
        pnl = price_diff * exit_size
        return pnl.quantize(PositionCalculator.PRECISION, rounding=ROUND_HALF_UP)
    
    @staticmethod
    def calculate_new_avg_entry(
        current_size: Decimal,
        current_avg_price: Decimal,
        fill_size: Decimal,
        fill_price: Decimal
    ) -> Decimal:
        """
        Calculate new average entry price after adding to position.
        
        Formula: (current_size × current_avg + fill_size × fill_price) / total_size
        """
        total_size = current_size + fill_size
        if total_size == 0:
            return Decimal('0')
        
        total_cost = (current_size * current_avg_price) + (fill_size * fill_price)
        new_avg = total_cost / total_size
        
        return new_avg.quantize(PositionCalculator.PRECISION, rounding=ROUND_HALF_UP)
    
    @staticmethod
    def calculate_total_pnl(
        unrealized_pnl: Decimal,
        realized_pnl: Decimal
    ) -> Decimal:
        """Calculate total PnL."""
        total = unrealized_pnl + realized_pnl
        return total.quantize(PositionCalculator.PRECISION, rounding=ROUND_HALF_UP)


# ═══════════════════════════════════════════════════════════════════════════════
# REPOSITORY PATTERN
# ═══════════════════════════════════════════════════════════════════════════════

class PositionRepository:
    """Repository for position CRUD operations."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def get_by_id(self, position_id: str, tenant_id: UUID) -> Optional[PositionModel]:
        """Get position by ID and tenant."""
        return self.db.query(PositionModel).filter(
            PositionModel.position_id == position_id,
            PositionModel.tenant_id == tenant_id
        ).first()
    
    def find_open_position(
        self,
        tenant_id: UUID,
        strategy_id: str,
        symbol: str,
        side: PositionSide
    ) -> Optional[PositionModel]:
        """
        Find open position by tenant, strategy, symbol, and side.
        
        STEP 4.2: Used to determine if we should update or create position.
        """
        return self.db.query(PositionModel).filter(
            PositionModel.tenant_id == tenant_id,
            PositionModel.strategy_id == strategy_id,
            PositionModel.symbol == symbol.upper(),
            PositionModel.side == side,
            PositionModel.status == PositionStatus.OPEN
        ).first()
    
    def list_open_positions(
        self,
        tenant_id: UUID,
        strategy_id: Optional[str] = None,
        symbol: Optional[str] = None
    ) -> List[PositionModel]:
        """List all open positions for tenant."""
        query = self.db.query(PositionModel).filter(
            PositionModel.tenant_id == tenant_id,
            PositionModel.status == PositionStatus.OPEN
        )
        
        if strategy_id:
            query = query.filter(PositionModel.strategy_id == strategy_id)
        
        if symbol:
            query = query.filter(PositionModel.symbol == symbol.upper())
        
        return query.all()
    
    def list_all_positions(
        self,
        tenant_id: UUID,
        status: Optional[PositionStatus] = None,
        limit: int = 100,
        offset: int = 0
    ) -> tuple[List[PositionModel], int]:
        """List positions with pagination."""
        query = self.db.query(PositionModel).filter(
            PositionModel.tenant_id == tenant_id
        )
        
        if status:
            query = query.filter(PositionModel.status == status)
        
        total = query.count()
        results = query.order_by(
            PositionModel.updated_at.desc()
        ).offset(offset).limit(limit).all()
        
        return results, total
    
    def create(self, data: PositionCreate, auto_commit: bool = True) -> PositionModel:
        """Create a new position."""
        position = PositionModel(
            position_id=data.position_id or f"pos_{uuid4().hex[:16]}",
            tenant_id=data.tenant_id,
            strategy_id=data.strategy_id,
            symbol=data.symbol.upper(),
            side=data.side,
            size=data.size,
            avg_entry_price=data.avg_entry_price,
            unrealized_pnl=data.unrealized_pnl,
            realized_pnl=data.realized_pnl,
            status=data.status
        )
        
        self.db.add(position)
        if auto_commit:
            self.db.commit()
            self.db.refresh(position)
        else:
            self.db.flush()
        
        logger.info(
            f"POSITION CREATED: {position.position_id} | "
            f"{position.symbol} {position.side.value} | "
            f"size={position.size} @ {position.avg_entry_price}"
        )
        
        return position
    
    def update(
        self,
        position_id: str,
        tenant_id: UUID,
        data: PositionUpdate,
        auto_commit: bool = True
    ) -> Optional[PositionModel]:
        """Update a position."""
        position = self.get_by_id(position_id, tenant_id)
        if not position:
            return None
        
        update_data = data.dict(exclude_unset=True)
        
        for field, value in update_data.items():
            if field == 'status' and value:
                value = value.value if hasattr(value, 'value') else value
            setattr(position, field, value)
        
        position.updated_at = datetime.utcnow()
        
        if auto_commit:
            self.db.commit()
            self.db.refresh(position)
        else:
            self.db.flush()
        
        logger.info(
            f"POSITION UPDATED: {position.position_id} | "
            f"size={position.size} | "
            f"unrealized_pnl={position.unrealized_pnl} | "
            f"realized_pnl={position.realized_pnl}"
        )
        
        return position
    
    def close_position(
        self,
        position_id: str,
        tenant_id: UUID,
        final_realized_pnl: str,
        auto_commit: bool = True
    ) -> Optional[PositionModel]:
        """Close a position."""
        position = self.get_by_id(position_id, tenant_id)
        if not position:
            return None
        
        position.size = "0"
        position.unrealized_pnl = "0"
        position.realized_pnl = final_realized_pnl
        position.status = PositionStatus.CLOSED
        position.closed_at = datetime.utcnow()
        position.updated_at = datetime.utcnow()
        
        if auto_commit:
            self.db.commit()
            self.db.refresh(position)
        else:
            self.db.flush()
        
        logger.info(
            f"POSITION CLOSED: {position.position_id} | "
            f"final_realized_pnl={final_realized_pnl}"
        )
        
        return position
    
    def get_position_summary(
        self,
        tenant_id: UUID
    ) -> Dict[str, Any]:
        """Get summary of all positions for tenant (including closed position realized PnL)."""
        all_positions = self.db.query(PositionModel).filter(
            PositionModel.tenant_id == tenant_id
        ).all()
        
        open_positions = [p for p in all_positions if p.status == PositionStatus.OPEN]
        
        total_unrealized = sum(
            Decimal(p.unrealized_pnl) for p in open_positions
        )
        # BUG-FIX PNL-02: Sum realized PnL across all tenant positions (both open and closed)
        total_realized = sum(
            Decimal(p.realized_pnl) for p in all_positions
        )
        
        exposure_by_symbol = {}
        for pos in open_positions:
            symbol = pos.symbol
            size = Decimal(pos.size)
            value = size * Decimal(pos.avg_entry_price)
            
            if symbol not in exposure_by_symbol:
                exposure_by_symbol[symbol] = Decimal('0')
            exposure_by_symbol[symbol] += value
        
        return {
            "open_position_count": len(open_positions),
            "total_unrealized_pnl": str(total_unrealized),
            "total_realized_pnl": str(total_realized),
            "total_pnl": str(total_unrealized + total_realized),
            "exposure_by_symbol": {
                k: str(v) for k, v in exposure_by_symbol.items()
            }
        }


# Global instance factory
def get_position_repository(db_session: Session) -> PositionRepository:
    """Get position repository instance."""
    return PositionRepository(db_session)
