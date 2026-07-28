"""
core/audit_trail.py — Structured Logging + Audit Trail System

🔴 STEP 7 — STRUCTURED LOGGING + AUDIT TRAIL

Provides comprehensive audit trail for every order with full traceability.

EVERY ORDER MUST LOG:
- user_id
- strategy_id
- signal
- decision reason
- execution_id
- exchange response

STORE:
- DB + log system

EXPECTED RESULT:
✔ Full traceability
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from backend_app.backend.redis_manager import redis_manager

logger = logging.getLogger("OrderAuditTrail")


class AuditEventType(Enum):
    """Types of audit events."""
    SIGNAL_RECEIVED = "signal_received"
    DECISION_MADE = "decision_made"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_CONFIRMED = "order_confirmed"
    ORDER_FAILED = "order_failed"
    FILL_RECEIVED = "fill_received"
    RECONCILIATION_COMPLETE = "reconciliation_complete"


@dataclass
class OrderAuditRecord:
    """
    Complete audit record for an order.
    
    Fields:
        audit_id: Unique audit record identifier
        timestamp: Event timestamp
        event_type: Type of audit event
        user_id: User/tenant identifier
        strategy_id: Strategy identifier
        execution_id: Execution identifier
        signal: Signal that triggered the order
        decision_reason: Reason for the decision
        symbol: Trading symbol
        side: Buy or sell
        size: Order size
        price: Order price
        order_type: Market or limit
        exchange_id: Exchange identifier
        exchange_response: Raw exchange response
        client_order_id: Client order ID
        exchange_order_id: Exchange-assigned order ID
        status: Order status
        filled_amount: Amount filled
        remaining_amount: Amount remaining
        average_price: Average fill price
        fee: Trading fee
        error_message: Error message if failed
        metadata: Additional metadata
    """
    audit_id: str
    timestamp: datetime
    event_type: AuditEventType
    user_id: str
    strategy_id: str
    execution_id: Optional[str]
    signal: Optional[Dict[str, Any]]
    decision_reason: Optional[str]
    symbol: str
    side: str
    size: float
    price: Optional[float]
    order_type: str
    exchange_id: str
    exchange_response: Optional[Dict[str, Any]]
    client_order_id: Optional[str]
    exchange_order_id: Optional[str]
    status: Optional[str]
    filled_amount: Optional[float]
    remaining_amount: Optional[float]
    average_price: Optional[float]
    fee: Optional[float]
    error_message: Optional[str]
    metadata: Optional[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        data = asdict(self)
        # Convert enum to string
        data['event_type'] = self.event_type.value
        # Convert datetime to ISO format
        data['timestamp'] = self.timestamp.isoformat()
        # Convert nested dicts to JSON strings for DB storage
        if self.signal:
            data['signal'] = json.dumps(self.signal)
        if self.exchange_response:
            data['exchange_response'] = json.dumps(self.exchange_response)
        if self.metadata:
            data['metadata'] = json.dumps(self.metadata)
        return data

    def to_log_entry(self) -> str:
        """Convert to structured log entry."""
        return json.dumps({
            "audit_id": self.audit_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "user_id": self.user_id,
            "strategy_id": self.strategy_id,
            "execution_id": self.execution_id,
            "signal": self.signal,
            "decision_reason": self.decision_reason,
            "symbol": self.symbol,
            "side": self.side,
            "size": self.size,
            "price": self.price,
            "order_type": self.order_type,
            "exchange_id": self.exchange_id,
            "exchange_response": self.exchange_response,
            "client_order_id": self.client_order_id,
            "exchange_order_id": self.exchange_order_id,
            "status": self.status,
            "filled_amount": self.filled_amount,
            "remaining_amount": self.remaining_amount,
            "average_price": self.average_price,
            "fee": self.fee,
            "error_message": self.error_message,
            "metadata": self.metadata
        }, default=str)


# ═══════════════════════════════════════════════════════════════════════════════
# EVENT DATACLASS HIERARCHY FOR ORDER LIFECYCLE AUDITING
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BaseOrderLifecycleEvent:
    """Base class for all order lifecycle audit events."""
    user_id: str
    strategy_id: str
    symbol: str
    side: str
    size: float = 0.0
    audit_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    execution_id: Optional[str] = None
    signal: Optional[Dict[str, Any]] = None
    decision_reason: Optional[str] = None
    price: Optional[float] = None
    order_type: str = "unknown"
    exchange_id: str = "unknown"
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class SignalReceivedEvent(BaseOrderLifecycleEvent):
    """Event representing initial signal reception."""
    pass


@dataclass
class DecisionEvent(BaseOrderLifecycleEvent):
    """Event representing decision to execute an order."""
    pass


@dataclass
class OrderSubmittedEvent(BaseOrderLifecycleEvent):
    """Event representing order submission to exchange."""
    client_order_id: Optional[str] = None
    exchange_response: Optional[Dict[str, Any]] = None


@dataclass
class OrderConfirmedEvent(BaseOrderLifecycleEvent):
    """Event representing order confirmation from exchange."""
    client_order_id: Optional[str] = None
    exchange_order_id: Optional[str] = None
    exchange_response: Optional[Dict[str, Any]] = None
    status: str = "confirmed"
    filled_amount: float = 0.0
    remaining_amount: float = 0.0
    average_price: Optional[float] = None
    fee: Optional[float] = None


@dataclass
class OrderFailedEvent(BaseOrderLifecycleEvent):
    """Event representing order execution failure."""
    client_order_id: Optional[str] = None
    error_message: str = "Unknown error"
    exchange_response: Optional[Dict[str, Any]] = None


@dataclass
class FillEvent(BaseOrderLifecycleEvent):
    """Event representing individual trade fill execution."""
    exchange_order_id: str = ""
    trade_id: str = ""
    fill_amount: float = 0.0
    fill_price: float = 0.0
    fee: float = 0.0
    exchange_response: Optional[Dict[str, Any]] = None


class OrderAuditLogger:
    """
    🔴 STEP 7 — STRUCTURED LOGGING + AUDIT TRAIL
    
    Comprehensive audit logging system for order execution.
    
    LOGS EVERY ORDER WITH:
        - user_id
        - strategy_id
        - signal
        - decision reason
        - execution_id
        - exchange response
    
    STORAGE:
        - Database: Persistent storage with query capability
        - Log system: Structured JSON logs for aggregation
        - Redis: Fast lookup cache
    
    EXPECTED RESULT:
        ✔ Full traceability
    """
    
    def __init__(self):
        self._audit_sequence = 0
    
    def _generate_audit_id(self) -> str:
        """Generate unique audit ID."""
        self._audit_sequence += 1
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return f"AUDIT-{timestamp}-{self._audit_sequence:06d}-{uuid.uuid4().hex[:8]}"
    
    async def log_signal_received(
        self,
        event: SignalReceivedEvent,
    ) -> str:
        """
        Log signal received event.
        This is the FIRST event in the audit trail.
        """
        audit_id = event.audit_id or self._generate_audit_id()
        timestamp = event.timestamp or datetime.utcnow()

        record = OrderAuditRecord(
            audit_id=audit_id,
            timestamp=timestamp,
            event_type=AuditEventType.SIGNAL_RECEIVED,
            user_id=event.user_id,
            strategy_id=event.strategy_id,
            execution_id=event.execution_id,
            signal=event.signal,
            decision_reason=event.decision_reason,
            symbol=event.symbol,
            side=event.side,
            size=event.size,
            price=event.price,
            order_type=event.order_type,
            exchange_id=event.exchange_id,
            exchange_response=None,
            client_order_id=None,
            exchange_order_id=None,
            status=None,
            filled_amount=None,
            remaining_amount=None,
            average_price=None,
            fee=None,
            error_message=None,
            metadata=event.metadata,
        )

        await self._store_record(record)
        return audit_id

    async def log_decision(
        self,
        event: DecisionEvent,
    ):
        """
        Log decision made event.
        Records the decision to execute an order.
        """
        audit_id = event.audit_id or self._generate_audit_id()
        timestamp = event.timestamp or datetime.utcnow()

        record = OrderAuditRecord(
            audit_id=audit_id,
            timestamp=timestamp,
            event_type=AuditEventType.DECISION_MADE,
            user_id=event.user_id,
            strategy_id=event.strategy_id,
            execution_id=event.execution_id,
            signal=event.signal,
            decision_reason=event.decision_reason,
            symbol=event.symbol,
            side=event.side,
            size=event.size,
            price=event.price,
            order_type=event.order_type,
            exchange_id=event.exchange_id,
            exchange_response=None,
            client_order_id=None,
            exchange_order_id=None,
            status="decided",
            filled_amount=None,
            remaining_amount=None,
            average_price=None,
            fee=None,
            error_message=None,
            metadata=event.metadata,
        )

        await self._store_record(record)

    async def log_order_submitted(
        self,
        event: OrderSubmittedEvent,
    ):
        """
        Log order submitted to exchange.
        Captures the exchange response immediately.
        """
        audit_id = event.audit_id or self._generate_audit_id()
        timestamp = event.timestamp or datetime.utcnow()

        exchange_order_id = None
        if event.exchange_response and isinstance(event.exchange_response, dict):
            exchange_order_id = event.exchange_response.get("id")

        record = OrderAuditRecord(
            audit_id=audit_id,
            timestamp=timestamp,
            event_type=AuditEventType.ORDER_SUBMITTED,
            user_id=event.user_id,
            strategy_id=event.strategy_id,
            execution_id=event.execution_id,
            signal=event.signal,
            decision_reason=event.decision_reason,
            symbol=event.symbol,
            side=event.side,
            size=event.size,
            price=event.price,
            order_type=event.order_type,
            exchange_id=event.exchange_id,
            exchange_response=event.exchange_response,
            client_order_id=event.client_order_id,
            exchange_order_id=exchange_order_id,
            status="submitted",
            filled_amount=None,
            remaining_amount=None,
            average_price=None,
            fee=None,
            error_message=None,
            metadata=event.metadata,
        )

        await self._store_record(record)

    async def log_order_confirmed(
        self,
        event: OrderConfirmedEvent,
    ):
        """
        Log order confirmed by exchange.
        Records the final state after confirmation.
        """
        audit_id = event.audit_id or self._generate_audit_id()
        timestamp = event.timestamp or datetime.utcnow()

        record = OrderAuditRecord(
            audit_id=audit_id,
            timestamp=timestamp,
            event_type=AuditEventType.ORDER_CONFIRMED,
            user_id=event.user_id,
            strategy_id=event.strategy_id,
            execution_id=event.execution_id,
            signal=event.signal,
            decision_reason=event.decision_reason,
            symbol=event.symbol,
            side=event.side,
            size=event.size,
            price=event.price,
            order_type=event.order_type,
            exchange_id=event.exchange_id,
            exchange_response=event.exchange_response,
            client_order_id=event.client_order_id,
            exchange_order_id=event.exchange_order_id,
            status=event.status,
            filled_amount=event.filled_amount,
            remaining_amount=event.remaining_amount,
            average_price=event.average_price,
            fee=event.fee,
            error_message=None,
            metadata=event.metadata,
        )

        await self._store_record(record)

    async def log_order_failed(
        self,
        event: OrderFailedEvent,
    ):
        """
        Log order failed.
        Records failure with full context.
        """
        audit_id = event.audit_id or self._generate_audit_id()
        timestamp = event.timestamp or datetime.utcnow()

        record = OrderAuditRecord(
            audit_id=audit_id,
            timestamp=timestamp,
            event_type=AuditEventType.ORDER_FAILED,
            user_id=event.user_id,
            strategy_id=event.strategy_id,
            execution_id=event.execution_id,
            signal=event.signal,
            decision_reason=event.decision_reason,
            symbol=event.symbol,
            side=event.side,
            size=event.size,
            price=event.price,
            order_type=event.order_type,
            exchange_id=event.exchange_id,
            exchange_response=event.exchange_response,
            client_order_id=event.client_order_id,
            exchange_order_id=None,
            status="failed",
            filled_amount=None,
            remaining_amount=None,
            average_price=None,
            fee=None,
            error_message=event.error_message,
            metadata=event.metadata,
        )

        await self._store_record(record)

    async def log_fill(
        self,
        event: FillEvent,
    ):
        """
        Log fill received.
        Records each individual fill/trade.
        """
        audit_id = event.audit_id or self._generate_audit_id()
        timestamp = event.timestamp or datetime.utcnow()

        record = OrderAuditRecord(
            audit_id=audit_id,
            timestamp=timestamp,
            event_type=AuditEventType.FILL_RECEIVED,
            user_id=event.user_id,
            strategy_id=event.strategy_id,
            execution_id=event.execution_id,
            signal=event.signal,
            decision_reason=event.decision_reason,
            symbol=event.symbol,
            side=event.side,
            size=event.fill_amount or event.size,
            price=event.fill_price or event.price,
            order_type="fill",
            exchange_id=event.exchange_id,
            exchange_response=event.exchange_response,
            client_order_id=None,
            exchange_order_id=event.exchange_order_id,
            status="filled",
            filled_amount=event.fill_amount,
            remaining_amount=None,
            average_price=event.fill_price,
            fee=event.fee,
            error_message=None,
            metadata={**(event.metadata or {}), "trade_id": event.trade_id},
        )

        await self._store_record(record)
    
    async def _store_record(self, record: OrderAuditRecord):
        """
        Store audit record to all storage systems.
        
        STORAGE:
            1. Database - Persistent storage
            2. Log system - Structured JSON logs
            3. Redis - Fast lookup cache
        """
        try:
            # 1. Store to database
            await self._store_to_db(record)
            
            # 2. Write to log system
            self._write_to_log(record)
            
            # 3. Cache in Redis for fast lookup
            await self._cache_in_redis(record)
            
        except Exception as e:
            logger.error(f"STEP 7: Failed to store audit record: {e}")
            # Continue - don't fail the order because of audit logging
    
    async def _store_to_db(self, record: OrderAuditRecord):
        """Store audit record to database."""
        try:
            from backend_app.backend.database import get_db_session
            from backend_app.backend.models import \
                AuditLogModel  # Assuming this model exists
            
            async with get_db_session() as db:
                data = record.to_dict()
                audit_entry = AuditLogModel(**data)
                db.add(audit_entry)
                db.commit()
        
        except Exception as e:
            logger.error(f"STEP 7: Failed to store to DB: {e}")
    
    def _write_to_log(self, record: OrderAuditRecord):
        """Write audit record to structured log."""
        try:
            log_entry = record.to_log_entry()
            
            # Use appropriate log level based on event type
            if record.event_type == AuditEventType.ORDER_FAILED:
                logger.error(log_entry)
            elif record.event_type in (AuditEventType.ORDER_CONFIRMED, AuditEventType.FILL_RECEIVED):
                logger.info(log_entry)
            else:
                logger.debug(log_entry)
        
        except Exception as e:
            logger.error(f"STEP 7: Failed to write to log: {e}")
    
    async def _cache_in_redis(self, record: OrderAuditRecord):
        """Cache audit record in Redis for fast lookup."""
        try:
            # Store by execution_id for fast lookup
            if record.execution_id:
                key = f"audit:{record.execution_id}:{record.event_type.value}"
                await redis_manager.setex(
                    key,
                    86400,  # 24 hour TTL
                    json.dumps(record.to_dict(), default=str)
                )
        
        except Exception as e:
            logger.error(f"STEP 7: Failed to cache in Redis: {e}")
    
    async def get_audit_trail(
        self,
        execution_id: str
    ) -> List[OrderAuditRecord]:
        """
        Get complete audit trail for an execution.
        
        Returns all audit records for the execution in chronological order.
        """
        try:
            from backend_app.backend.database import get_db_session
            from backend_app.backend.models import AuditLogModel
            
            async with get_db_session() as db:
                records = db.query(AuditLogModel).filter(
                    AuditLogModel.execution_id == execution_id
                ).order_by(AuditLogModel.timestamp).all()
                
                return [self._record_from_db(r) for r in records]
        
        except Exception as e:
            logger.error(f"STEP 7: Failed to get audit trail: {e}")
            return []
    
    def _record_from_db(self, db_record) -> OrderAuditRecord:
        """Convert DB record to OrderAuditRecord."""
        return OrderAuditRecord(
            audit_id=db_record.audit_id,
            timestamp=db_record.timestamp,
            event_type=AuditEventType(db_record.event_type),
            user_id=db_record.user_id,
            strategy_id=db_record.strategy_id,
            execution_id=db_record.execution_id,
            signal=json.loads(db_record.signal) if db_record.signal else None,
            decision_reason=db_record.decision_reason,
            symbol=db_record.symbol,
            side=db_record.side,
            size=db_record.size,
            price=db_record.price,
            order_type=db_record.order_type,
            exchange_id=db_record.exchange_id,
            exchange_response=json.loads(db_record.exchange_response) if db_record.exchange_response else None,
            client_order_id=db_record.client_order_id,
            exchange_order_id=db_record.exchange_order_id,
            status=db_record.status,
            filled_amount=db_record.filled_amount,
            remaining_amount=db_record.remaining_amount,
            average_price=db_record.average_price,
            fee=db_record.fee,
            error_message=db_record.error_message,
            metadata=json.loads(db_record.metadata) if db_record.metadata else None
        )


# Global singleton instance
_audit_logger: Optional[OrderAuditLogger] = None


def get_order_audit_logger() -> OrderAuditLogger:
    """Get or create the order audit logger instance."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = OrderAuditLogger()
    return _audit_logger


# Convenience exports
__all__ = [
    "OrderAuditLogger",
    "OrderAuditRecord",
    "AuditEventType",
    "BaseOrderLifecycleEvent",
    "SignalReceivedEvent",
    "DecisionEvent",
    "OrderSubmittedEvent",
    "OrderConfirmedEvent",
    "OrderFailedEvent",
    "FillEvent",
    "get_order_audit_logger",
]
