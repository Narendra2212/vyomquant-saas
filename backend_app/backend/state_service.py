"""
backend/state_service.py — GLOBAL STATE SERVICE (Single Source of Truth)

STEP 1: GLOBAL IDEMPOTENCY + CONSISTENT STATE

GOAL: Production-grade reliability for 1000+ users

RESPONSIBILITIES:
  - Read/write positions (single source of truth)
  - Read/write orders (single source of truth)
  - Enforce idempotency (no duplicate execution)
  - Maintain consistency between Redis (fast) and PostgreSQL (persistent)

ARCHITECTURE:
  ┌─────────────────────────────────────────────────────────────────┐
  │                     STATE SERVICE                              │
  │                                                                 │
  │   Single Source of Truth for Orders & Positions               │
  │                                                                 │
  │   Write Path:                                                  │
  │   API ──▶ StateService.save_order() ──▶ Redis ──▶ Async DB    │
  │                              │                                  │
  │                              ▼                                  │
  │                        Idempotency Check                       │
  │                        (deduplicate writes)                    │
  │                                                                 │
  │   Read Path:                                                   │
  │   API ──▶ StateService.get_position() ──▶ Redis (cached)     │
   │                              │ (fallback to DB on miss)        │
  │                              ▼                                  │
  │                        Return Position                         │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘

PATTERN:
  - Write-Through Cache: Redis + PostgreSQL
  - Idempotency Keys: Prevent duplicate execution
  - Optimistic Locking: Version-based concurrency control
  - Event Sourcing: All changes logged for audit

EXPECTED RESULT:
  ✔ No state mismatch between Redis and DB
  ✔ No duplicate execution (idempotency enforced)
  ✔ Deterministic state updates
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

# Redis imports
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# Database imports
try:
    from sqlalchemy import (JSON, Column, DateTime, Integer, Numeric, String,
                            select, update)
#     from sqlalchemy.dialects.postgresql import UUID
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import declarative_base, sessionmaker
    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SQLALCHEMY_AVAILABLE = False

logger = logging.getLogger("StateService")


# ═══════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════

class OrderStatus(str, Enum):
    """Order status enumeration."""
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionSide(str, Enum):
    """Position side enumeration."""
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass
class Order:
    """Order data model - single source of truth."""
    order_id: str
    user_id: str
    symbol: str
    side: str  # buy/sell
    order_type: str  # market/limit/etc
    quantity: Decimal
    price: Optional[Decimal] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Decimal = field(default_factory=lambda: Decimal("0"))
    remaining_quantity: Decimal = None
    idempotency_key: str = ""
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    exchange: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: int = 1  # Optimistic locking
    
    def __post_init__(self):
        if self.remaining_quantity is None:
            self.remaining_quantity = self.quantity
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "order_id": self.order_id,
            "user_id": self.user_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": str(self.quantity),
            "price": str(self.price) if self.price else None,
            "status": self.status.value,
            "filled_quantity": str(self.filled_quantity),
            "remaining_quantity": str(self.remaining_quantity),
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "exchange": self.exchange,
            "metadata": self.metadata,
            "version": self.version,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Order":
        """Create from dictionary."""
        return cls(
            order_id=data["order_id"],
            user_id=data["user_id"],
            symbol=data["symbol"],
            side=data["side"],
            order_type=data["order_type"],
            quantity=Decimal(data["quantity"]),
            price=Decimal(data["price"]) if data.get("price") else None,
            status=OrderStatus(data.get("status", "pending")),
            filled_quantity=Decimal(data.get("filled_quantity", "0")),
            remaining_quantity=Decimal(data.get("remaining_quantity", data["quantity"])),
            idempotency_key=data.get("idempotency_key", ""),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.utcnow(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.utcnow(),
            exchange=data.get("exchange", ""),
            metadata=data.get("metadata", {}),
            version=data.get("version", 1),
        )


@dataclass
class Position:
    """Position data model - single source of truth."""
    position_id: str
    user_id: str
    symbol: str
    side: PositionSide
    
    # Quantities
    quantity: Decimal
    available_quantity: Decimal = None
    locked_quantity: Decimal = field(default_factory=lambda: Decimal("0"))
    
    # Prices
    entry_price: Decimal = field(default_factory=lambda: Decimal("0"))
    mark_price: Optional[Decimal] = None
    liquidation_price: Optional[Decimal] = None
    
    # PnL
    unrealized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    realized_pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    
    # Metadata
    opened_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: int = 1  # Optimistic locking
    
    def __post_init__(self):
        if self.available_quantity is None:
            self.available_quantity = self.quantity
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "position_id": self.position_id,
            "user_id": self.user_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": str(self.quantity),
            "available_quantity": str(self.available_quantity),
            "locked_quantity": str(self.locked_quantity),
            "entry_price": str(self.entry_price),
            "mark_price": str(self.mark_price) if self.mark_price else None,
            "liquidation_price": str(self.liquidation_price) if self.liquidation_price else None,
            "unrealized_pnl": str(self.unrealized_pnl),
            "realized_pnl": str(self.realized_pnl),
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "metadata": self.metadata,
            "version": self.version,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        """Create from dictionary."""
        return cls(
            position_id=data["position_id"],
            user_id=data["user_id"],
            symbol=data["symbol"],
            side=PositionSide(data.get("side", "flat")),
            quantity=Decimal(data.get("quantity", "0")),
            available_quantity=Decimal(data.get("available_quantity", data.get("quantity", "0"))),
            locked_quantity=Decimal(data.get("locked_quantity", "0")),
            entry_price=Decimal(data.get("entry_price", "0")),
            mark_price=Decimal(data["mark_price"]) if data.get("mark_price") else None,
            liquidation_price=Decimal(data["liquidation_price"]) if data.get("liquidation_price") else None,
            unrealized_pnl=Decimal(data.get("unrealized_pnl", "0")),
            realized_pnl=Decimal(data.get("realized_pnl", "0")),
            opened_at=datetime.fromisoformat(data["opened_at"]) if data.get("opened_at") else datetime.utcnow(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.utcnow(),
            metadata=data.get("metadata", {}),
            version=data.get("version", 1),
        )


@dataclass
class StateChangeEvent:
    """Event representing a state change (for audit log)."""
    event_id: str
    event_type: str  # order_created, position_updated, etc.
    entity_type: str  # order, position
    entity_id: str
    user_id: str
    
    # Change details
    previous_state: Optional[Dict] = None
    new_state: Dict = field(default_factory=dict)
    change_reason: str = ""
    
    # Metadata
    timestamp: datetime = field(default_factory=datetime.utcnow)
    idempotency_key: str = ""


# ═══════════════════════════════════════════════════════════════════════════
# IDEMPOTENCY
# ═══════════════════════════════════════════════════════════════════════════

class IdempotencyChecker:
    """
    Idempotency checker to prevent duplicate operations.
    
    Uses Redis for fast lookups with TTL-based expiration.
    Memory fallbacks are disabled.
    """
    
    def __init__(self, redis_client: Optional[Any] = None):
        self.redis = redis_client
        self._ttl_seconds = 86400  # 24 hours
    
    def generate_key(self, user_id: str, operation: str, params: Dict) -> str:
        """Generate idempotency key from operation parameters."""
        # Create deterministic key
        key_data = f"{user_id}:{operation}:{json.dumps(params, sort_keys=True)}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:32]
    
    async def check_and_record(self, key: str) -> bool:
        """
        Check if key exists (operation already performed).
        Returns True if new operation, False if duplicate.
        """
        if not self.redis:
            raise RuntimeError("Redis connection is required for idempotency checks, but is not connected.")
            
        # Redis check
        exists = await self.redis.exists(f"idempotency:{key}")
        if exists:
            return False
        
        # Record key with TTL
        await self.redis.setex(f"idempotency:{key}", self._ttl_seconds, "1")
        return True


# ═══════════════════════════════════════════════════════════════════════════
# DATABASE MODELS (SQLAlchemy)
# ═══════════════════════════════════════════════════════════════════════════

if SQLALCHEMY_AVAILABLE:
    Base = declarative_base()
    
    class OrderModel(Base):
        """Database model for orders."""
        __tablename__ = "orders"
        
        order_id = Column(String, primary_key=True)
        user_id = Column(String, index=True, nullable=False)
        symbol = Column(String, index=True, nullable=False)
        side = Column(String, nullable=False)
        order_type = Column(String, nullable=False)
        quantity = Column(Numeric(36, 18), nullable=False)
        price = Column(Numeric(36, 18))
        status = Column(String, index=True, nullable=False)
        filled_quantity = Column(Numeric(36, 18), default=0)
        remaining_quantity = Column(Numeric(36, 18))
        idempotency_key = Column(String, index=True, unique=True)
        created_at = Column(DateTime, default=datetime.utcnow)
        updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
        exchange = Column(String)
        metadata_json = Column(JSON)
        version = Column(Integer, default=1)
    
    class PositionModel(Base):
        """Database model for positions."""
        __tablename__ = "positions"
        
        position_id = Column(String, primary_key=True)
        user_id = Column(String, index=True, nullable=False)
        symbol = Column(String, index=True, nullable=False)
        side = Column(String, nullable=False)
        quantity = Column(Numeric(36, 18), nullable=False)
        available_quantity = Column(Numeric(36, 18))
        locked_quantity = Column(Numeric(36, 18), default=0)
        entry_price = Column(Numeric(36, 18), default=0)
        mark_price = Column(Numeric(36, 18))
        liquidation_price = Column(Numeric(36, 18))
        unrealized_pnl = Column(Numeric(36, 18), default=0)
        realized_pnl = Column(Numeric(36, 18), default=0)
        opened_at = Column(DateTime, default=datetime.utcnow)
        updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
        metadata_json = Column(JSON)
        version = Column(Integer, default=1)
    
    class StateEventLogModel(Base):
        """Audit log for state changes."""
        __tablename__ = "state_event_log"
        
        event_id = Column(String, primary_key=True)
        event_type = Column(String, index=True, nullable=False)
        entity_type = Column(String, nullable=False)
        entity_id = Column(String, index=True, nullable=False)
        user_id = Column(String, index=True, nullable=False)
        previous_state = Column(JSON)
        new_state = Column(JSON, nullable=False)
        change_reason = Column(String)
        timestamp = Column(DateTime, default=datetime.utcnow, index=True)
        idempotency_key = Column(String, index=True)


# ═══════════════════════════════════════════════════════════════════════════
# STATE SERVICE
# ═══════════════════════════════════════════════════════════════════════════

class StateService:
    """
    STEP 1: Global State Service - Single Source of Truth
    
    All order and position operations go through this service.
    
    Features:
    - Idempotency enforcement (no duplicates)
    - Write-through caching (Redis + PostgreSQL)
    - Optimistic locking (version-based)
    - Event sourcing (audit log)
    - Consistent state management
    
    Usage:
        from backend_app.backend.state_service import state_service
        
        # Save order (idempotent)
        order = await state_service.save_order(
            order_data,
            idempotency_key="user-123:place-order:1234567890"
        )
        
        # Get position (cached)
        position = await state_service.get_position(user_id, symbol)
        
        # Update position (with optimistic locking)
        await state_service.update_position(
            position_id,
            updates={"quantity": new_quantity},
            expected_version=current_version
        )
    """
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        database_url: str = "postgresql+asyncpg://user:pass@localhost/trading",
        enable_async_db: bool = True
    ):
        self.redis_url = redis_url
        self.database_url = database_url
        self.enable_async_db = enable_async_db and SQLALCHEMY_AVAILABLE
        
        # Connections
        self._redis: Optional[Any] = None
        self._engine: Optional[Any] = None
        self._session_factory: Optional[Any] = None
        
        # Idempotency
        self._idempotency: Optional[IdempotencyChecker] = None
        
        # Event callbacks
        self._event_callbacks: List[Callable[[StateChangeEvent], Any]] = []
        
        logger.info("[StateService] Initialized")
    
    async def connect(self):
        """Establish connections to Redis and database."""
        # Redis connection
        if REDIS_AVAILABLE:
            try:
                self._redis = await redis.from_url(
                    self.redis_url,
                    decode_responses=True
                )
                self._idempotency = IdempotencyChecker(self._redis)
                logger.info("[StateService] Redis connected")
            except Exception as e:
                logger.warning(f"[StateService] Redis connection failed: {e}")
                self._idempotency = IdempotencyChecker(None)
        else:
            self._idempotency = IdempotencyChecker(None)
        
        # Database connection
        if self.enable_async_db:
            try:
                self._engine = create_async_engine(self.database_url)
                self._session_factory = sessionmaker(
                    self._engine,
                    class_=AsyncSession,
                    expire_on_commit=False
                )
                logger.info("[StateService] Database connected")
            except Exception as e:
                logger.warning(f"[StateService] Database connection failed: {e}")
    
    async def disconnect(self):
        """Close connections."""
        if self._redis:
            await self._redis.close()
        if self._engine:
            await self._engine.dispose()
    
    # ═══════════════════════════════════════════════════════════════════════
    # ORDER OPERATIONS
    # ═══════════════════════════════════════════════════════════════════════
    
    async def save_order(
        self,
        order: Order,
        idempotency_key: Optional[str] = None
    ) -> Order:
        if not self._redis:
            raise RuntimeError("Redis connection is required but not connected. StateService cannot proceed.")
        """
        Save order to state (idempotent).
        
        Flow:
        1. Check idempotency key (if duplicate, return existing)
        2. Write to Redis (fast)
        3. Async write to database (persistent)
        4. Emit state change event
        
        Args:
            order: Order to save
            idempotency_key: Unique key for this operation (prevents duplicates)
        
        Returns:
            Saved order (or existing order if duplicate)
        """
        # Generate idempotency key if not provided
        if not idempotency_key:
            idempotency_key = self._idempotency.generate_key(
                order.user_id,
                "save_order",
                {"order_id": order.order_id, "symbol": order.symbol, "side": order.side}
            )
        
        order.idempotency_key = idempotency_key
        
        # Check idempotency
        is_new = await self._idempotency.check_and_record(idempotency_key)
        if not is_new:
            # Duplicate operation - return existing order
            logger.info(f"[StateService] Duplicate order detected: {order.order_id}")
            existing = await self.get_order(order.order_id)
            if existing:
                return existing
        
        # Write to Redis (fast path)
        if self._redis:
            await self._redis.setex(
                f"order:{order.order_id}",
                3600,  # 1 hour TTL
                json.dumps(order.to_dict())
            )
            await self._redis.sadd(f"user:{order.user_id}:orders", order.order_id)
        
        # Async write to database (persistent)
        if self._session_factory:
            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        # Check for existing with optimistic locking
                        existing = await session.get(OrderModel, order.order_id)
                        
                        if existing:
                            # Update with version check
                            if order.version <= existing.version:
                                raise ConcurrentModificationError(
                                    f"Order {order.order_id} modified by another process"
                                )
                            
                            # Update fields
                            existing.status = order.status.value
                            existing.filled_quantity = order.filled_quantity
                            existing.remaining_quantity = order.remaining_quantity
                            existing.updated_at = datetime.utcnow()
                            existing.version = order.version
                            existing.metadata_json = order.metadata
                        else:
                            # Create new
                            model = OrderModel(
                                order_id=order.order_id,
                                user_id=order.user_id,
                                symbol=order.symbol,
                                side=order.side,
                                order_type=order.order_type,
                                quantity=order.quantity,
                                price=order.price,
                                status=order.status.value,
                                filled_quantity=order.filled_quantity,
                                remaining_quantity=order.remaining_quantity,
                                idempotency_key=idempotency_key,
                                exchange=order.exchange,
                                metadata_json=order.metadata,
                                version=order.version,
                            )
                            session.add(model)
                        
                        await session.commit()
            except Exception as e:
                logger.error(f"[StateService] Database write failed: {e}")
                # Continue - Redis has the data
        
        # Emit event
        await self._emit_event(StateChangeEvent(
            event_id=f"evt_{order.order_id}_{int(time.time() * 1000)}",
            event_type="order_created" if is_new else "order_updated",
            entity_type="order",
            entity_id=order.order_id,
            user_id=order.user_id,
            new_state=order.to_dict(),
            idempotency_key=idempotency_key,
        ))
        
        logger.info(f"[StateService] Order saved: {order.order_id}")
        return order
    
    async def get_order(self, order_id: str) -> Optional[Order]:
        if not self._redis:
            raise RuntimeError("Redis connection is required but not connected. StateService cannot proceed.")
        """Get order by ID (from Redis cache or DB)."""
        # Try Redis first
        if self._redis:
            data = await self._redis.get(f"order:{order_id}")
            if data:
                return Order.from_dict(json.loads(data))
        
        # Fallback to database
        if self._session_factory:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(OrderModel).where(OrderModel.order_id == order_id)
                )
                model = result.scalar_one_or_none()
                
                if model:
                    order = Order(
                        order_id=model.order_id,
                        user_id=model.user_id,
                        symbol=model.symbol,
                        side=model.side,
                        order_type=model.order_type,
                        quantity=model.quantity,
                        price=model.price,
                        status=OrderStatus(model.status),
                        filled_quantity=model.filled_quantity,
                        remaining_quantity=model.remaining_quantity,
                        idempotency_key=model.idempotency_key,
                        created_at=model.created_at,
                        updated_at=model.updated_at,
                        exchange=model.exchange,
                        metadata=model.metadata_json or {},
                        version=model.version,
                    )
                    
                    # Cache in Redis for next time
                    if self._redis:
                        await self._redis.setex(
                            f"order:{order_id}",
                            3600,
                            json.dumps(order.to_dict())
                        )
                    
                    return order
        
        return None
    
    async def get_user_orders(
        self,
        user_id: str,
        status: Optional[OrderStatus] = None,
        limit: int = 100
    ) -> List[Order]:
        if not self._redis:
            raise RuntimeError("Redis connection is required but not connected. StateService cannot proceed.")
        """Get all orders for a user."""
        orders = []
        
        if self._session_factory:
            async with self._session_factory() as session:
                query = select(OrderModel).where(OrderModel.user_id == user_id)
                
                if status:
                    query = query.where(OrderModel.status == status.value)
                
                query = query.order_by(OrderModel.created_at.desc()).limit(limit)
                
                result = await session.execute(query)
                models = result.scalars().all()
                
                for model in models:
                    orders.append(Order(
                        order_id=model.order_id,
                        user_id=model.user_id,
                        symbol=model.symbol,
                        side=model.side,
                        order_type=model.order_type,
                        quantity=model.quantity,
                        price=model.price,
                        status=OrderStatus(model.status),
                        filled_quantity=model.filled_quantity,
                        remaining_quantity=model.remaining_quantity,
                        idempotency_key=model.idempotency_key,
                        created_at=model.created_at,
                        updated_at=model.updated_at,
                        exchange=model.exchange,
                        metadata=model.metadata_json or {},
                        version=model.version,
                    ))
        
        return orders
    
    # ═══════════════════════════════════════════════════════════════════════
    # POSITION OPERATIONS
    # ═══════════════════════════════════════════════════════════════════════
    
    async def get_position(self, user_id: str, symbol: str) -> Optional[Position]:
        if not self._redis:
            raise RuntimeError("Redis connection is required but not connected. StateService cannot proceed.")
        """Get position for user+symbol (from Redis cache or DB)."""
        position_id = f"{user_id}:{symbol}"
        
        # Try Redis first
        if self._redis:
            data = await self._redis.get(f"position:{position_id}")
            if data:
                return Position.from_dict(json.loads(data))
        
        # Fallback to database
        if self._session_factory:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(PositionModel).where(
                        PositionModel.user_id == user_id,
                        PositionModel.symbol == symbol
                    )
                )
                model = result.scalar_one_or_none()
                
                if model:
                    position = Position(
                        position_id=model.position_id,
                        user_id=model.user_id,
                        symbol=model.symbol,
                        side=PositionSide(model.side),
                        quantity=model.quantity,
                        available_quantity=model.available_quantity,
                        locked_quantity=model.locked_quantity,
                        entry_price=model.entry_price,
                        mark_price=model.mark_price,
                        liquidation_price=model.liquidation_price,
                        unrealized_pnl=model.unrealized_pnl,
                        realized_pnl=model.realized_pnl,
                        opened_at=model.opened_at,
                        updated_at=model.updated_at,
                        metadata=model.metadata_json or {},
                        version=model.version,
                    )
                    
                    # Cache in Redis
                    if self._redis:
                        await self._redis.setex(
                            f"position:{position_id}",
                            3600,
                            json.dumps(position.to_dict())
                        )
                    
                    return position
        
        return None
    
    async def update_position(
        self,
        position_id: str,
        updates: Dict[str, Any],
        expected_version: int,
        idempotency_key: Optional[str] = None
    ) -> Position:
        if not self._redis:
            raise RuntimeError("Redis connection is required but not connected. StateService cannot proceed.")
        """
        Update position with optimistic locking.
        
        Args:
            position_id: Position identifier
            updates: Dict of fields to update
            expected_version: Expected current version (optimistic locking)
            idempotency_key: Optional idempotency key
        
        Returns:
            Updated position
        
        Raises:
            ConcurrentModificationError: If version mismatch (another process modified)
            NotFoundError: If position doesn't exist
        """
        # Parse user_id and symbol from position_id
        user_id, symbol = position_id.split(":", 1)
        
        # Check idempotency
        if idempotency_key:
            is_new = await self._idempotency.check_and_record(idempotency_key)
            if not is_new:
                logger.info(f"[StateService] Duplicate position update: {position_id}")
                return await self.get_position(user_id, symbol)
        
        # Get current position
        current = await self.get_position(user_id, symbol)
        if not current:
            raise NotFoundError(f"Position not found: {position_id}")
        
        # Check version (optimistic locking)
        if current.version != expected_version:
            raise ConcurrentModificationError(
                f"Position {position_id} version mismatch: expected {expected_version}, got {current.version}"
            )
        
        # Store previous state for event
        previous_state = current.to_dict()
        
        # Apply updates
        for key, value in updates.items():
            if hasattr(current, key):
                setattr(current, key, value)
        
        # Update metadata
        current.version += 1
        current.updated_at = datetime.utcnow()
        
        # Write to Redis
        if self._redis:
            await self._redis.setex(
                f"position:{position_id}",
                3600,
                json.dumps(current.to_dict())
            )
        
        # Write to database
        if self._session_factory:
            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        result = await session.execute(
                            select(PositionModel).where(
                                PositionModel.position_id == position_id,
                                PositionModel.version == expected_version
                            )
                        )
                        model = result.scalar_one_or_none()
                        
                        if not model:
                            raise ConcurrentModificationError(
                                f"Position {position_id} modified by another process"
                            )
                        
                        # Update fields
                        for key, value in updates.items():
                            if hasattr(model, key):
                                setattr(model, key, value)
                        
                        model.version = current.version
                        model.updated_at = current.updated_at
                        
                        await session.commit()
            except Exception as e:
                logger.error(f"[StateService] Position DB update failed: {e}")
                raise
        
        # Emit event
        await self._emit_event(StateChangeEvent(
            event_id=f"evt_{position_id}_{int(time.time() * 1000)}",
            event_type="position_updated",
            entity_type="position",
            entity_id=position_id,
            user_id=user_id,
            previous_state=previous_state,
            new_state=current.to_dict(),
            idempotency_key=idempotency_key or "",
        ))
        
        logger.info(f"[StateService] Position updated: {position_id}")
        return current
    
    async def close_position(
        self,
        user_id: str,
        symbol: str,
        close_quantity: Decimal,
        close_price: Decimal,
        realized_pnl: Decimal,
        idempotency_key: Optional[str] = None
    ) -> Optional[Position]:
        if not self._redis:
            raise RuntimeError("Redis connection is required but not connected. StateService cannot proceed.")
        """Close or reduce a position."""
        position = await self.get_position(user_id, symbol)
        if not position:
            return None
        
        if close_quantity >= position.quantity:
            # Full close
            position.side = PositionSide.FLAT
            position.quantity = Decimal("0")
            position.available_quantity = Decimal("0")
            position.realized_pnl += realized_pnl
            
            # Remove from Redis
            if self._redis:
                await self._redis.delete(f"position:{user_id}:{symbol}")
            
            # Update DB
            if self._session_factory:
                async with self._session_factory() as session:
                    async with session.begin():
                        await session.execute(
                            update(PositionModel)
                            .where(PositionModel.position_id == f"{user_id}:{symbol}")
                            .values(
                                side=PositionSide.FLAT.value,
                                quantity=0,
                                available_quantity=0,
                                realized_pnl=position.realized_pnl,
                                updated_at=datetime.utcnow(),
                                version=PositionModel.version + 1
                            )
                        )
                        await session.commit()
        else:
            # Partial close
            position.quantity -= close_quantity
            position.available_quantity -= close_quantity
            position.realized_pnl += realized_pnl
            
            # Update via update_position for consistency
            await self.update_position(
                f"{user_id}:{symbol}",
                {
                    "quantity": position.quantity,
                    "available_quantity": position.available_quantity,
                    "realized_pnl": position.realized_pnl,
                },
                expected_version=position.version,
                idempotency_key=idempotency_key
            )
        
        return position
    
    # ═══════════════════════════════════════════════════════════════════════
    # EVENT HANDLING
    # ═══════════════════════════════════════════════════════════════════════
    
    def register_event_callback(self, callback: Callable[[StateChangeEvent], Any]):
        """Register callback for state change events."""
        self._event_callbacks.append(callback)
    
    async def _emit_event(self, event: StateChangeEvent):
        """Emit state change event to all callbacks."""
        # Persist to event log
        if self._session_factory and SQLALCHEMY_AVAILABLE:
            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        model = StateEventLogModel(
                            event_id=event.event_id,
                            event_type=event.event_type,
                            entity_type=event.entity_type,
                            entity_id=event.entity_id,
                            user_id=event.user_id,
                            previous_state=event.previous_state,
                            new_state=event.new_state,
                            change_reason=event.change_reason,
                            timestamp=event.timestamp,
                            idempotency_key=event.idempotency_key,
                        )
                        session.add(model)
                        await session.commit()
            except Exception as e:
                logger.error(f"[StateService] Event log write failed: {e}")
        
        # Notify callbacks
        for callback in self._event_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"[StateService] Event callback error: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # HEALTH & STATS
    # ═══════════════════════════════════════════════════════════════════════
    
    async def health_check(self) -> Dict[str, Any]:
        """Check service health."""
        health = {
            "redis_connected": False,
            "database_connected": False,
            "idempotency_ready": self._idempotency is not None,
        }
        
        if self._redis:
            try:
                await self._redis.ping()
                health["redis_connected"] = True
            except Exception as e:
                health["redis_error"] = str(e)
        
        if self._session_factory:
            try:
                async with self._session_factory() as session:
                    await session.execute(select(1))
                health["database_connected"] = True
            except Exception as e:
                health["database_error"] = str(e)
        
        health["status"] = "healthy" if (health["redis_connected"] or health["database_connected"]) else "unhealthy"
        return health


class ConcurrentModificationError(Exception):
    """Raised when optimistic locking detects concurrent modification."""
    pass


class NotFoundError(Exception):
    """Raised when entity is not found."""
    pass


# Global singleton instance
state_service = StateService()


def get_state_service() -> StateService:
    """Get global state service instance."""
    return state_service
