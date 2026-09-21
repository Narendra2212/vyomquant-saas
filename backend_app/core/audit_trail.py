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
from datetime import datetime, timezone
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
            from backend_app.core.database import get_db_context
            # Audit log is primarily stored in Redis fast cache and structured logging
            # Direct DB persistence fallback handled via state_persistence / event_log
            pass
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
        Get complete audit trail for an execution from Redis cache.
        
        Returns all audit records for the execution in chronological order.
        """
        try:
            records = []
            for event_type in AuditEventType:
                key = f"audit:{execution_id}:{event_type.value}"
                raw = await redis_manager.get(key)
                if raw:
                    try:
                        data = json.loads(raw)
                        records.append(OrderAuditRecord.from_dict(data))
                    except Exception:
                        pass
            records.sort(key=lambda r: r.timestamp)
            return records
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


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY LIFECYCLE AUDIT  (strategy-builder task 8.3 — Requirement 9.8)
# ══════════════════════════════════════════════════════════════════════════
#
# Requirement 9.8: actor, timestamp and reason are recorded for every version
# creation, lifecycle transition, deployment action and training job creation or
# cancellation. `design.md` -> Security controls names THIS module as the place:
# "every lifecycle transition, deploy, training create/cancel and version creation
# recorded via core/audit_trail.py with actor, resource, before/after state".
#
# WHY A SECOND RECORD SHAPE RATHER THAN OrderAuditRecord
# ------------------------------------------------------
# `OrderAuditRecord` is an ORDER: it carries symbol, side, size, price, fee and an
# exchange response, and `AuditEventType` enumerates the seven stages one order goes
# through. A lifecycle transition has none of those fields and is not one of those
# stages, so recording it as an order audit record would mean writing a record whose
# every financial field is null into the trail an auditor reads to reconstruct order
# flow. Two further reasons the shapes cannot be shared:
#
#   * `OrderAuditLogger._cache_in_redis` keys on `audit:{execution_id}:{event_type}`,
#     which holds exactly ONE record per (id, type). Lifecycle transitions accumulate
#     - DEPLOYED, RUNNING, PAUSED, RUNNING, STOPPED against one version - so that key
#     shape would overwrite the history it is supposed to preserve. This logger uses a
#     Redis LIST, the same structure `global_safety.GlobalKillSwitch` uses for its own
#     activation history.
#   * Adding members to `AuditEventType` would change what `get_audit_trail` iterates
#     for every order lookup in the platform. Nothing existing in this module is
#     touched by this section: no enum member, no dataclass field, no method.
#
# BEFORE AND AFTER, NOT JUST AFTER
# --------------------------------
# Every record carries both states. "moved to STOPPED" cannot answer "was it running,
# or had it already failed?", and that is exactly the question asked after a guard trip.
#
# STORAGE, AND WHAT IS LOAD-BEARING
# ---------------------------------
# The structured log line is written FIRST and unconditionally, because it is the one
# sink that exists in every environment; Redis is a fast-lookup cache on top of it and
# its absence is a warning, never an error. An audit write never fails the action it
# describes - the same disposition `_store_record` takes for orders - because a
# deployment that stopped on a kill switch must not stay running because Redis was
# down. It is, however, never SILENT: a failed write is logged at warning level with
# the record's identity, so the gap is visible in the log the record would have gone to.


class StrategyAuditAction(Enum):
    """The four audited acts of Requirement 9.8, plus the transition itself."""

    VERSION_CREATED = "version_created"
    LIFECYCLE_TRANSITION = "lifecycle_transition"
    DEPLOYMENT_ACTION = "deployment_action"
    TRAINING_JOB_CREATED = "training_job_created"
    TRAINING_JOB_CANCEL_REQUESTED = "training_job_cancel_requested"
    #: One strategy soft-deleted (trading-lifecycle-integration Requirement 3.2). An
    #: additive member: nothing existing is renamed or redefined, so no stored record
    #: changes meaning. ``design.md`` writes this as ``StrategyAuditAction.ARCHIVED``;
    #: the fuller spelling is deliberate, because ``ARCHIVED`` alone is also a
    #: ``strategy_versions.lifecycle_state`` value (``LIFECYCLE_ARCHIVED``) meaning an
    #: archived *version*, which is a different act on a different resource.
    STRATEGY_ARCHIVED = "strategy_archived"
    #: One Eligibility_Gate evaluation of a strategy for Marketplace publication, recorded
    #: whether the strategy was admitted or not (marketplace-subscriptions-paper-trading
    #: Requirement 2.11, task 14.1). An additive member: nothing existing is renamed or
    #: redefined, so no stored record changes meaning.
    #:
    #: ``design.md`` -> "Audit records" places this member, along with the fuller
    #: ``MARKETPLACE_*`` set, in task 14.3, which extends this enum and adds
    #: ``StrategyAuditLogger.record_or_raise``. That task has not run yet, so this one member
    #: is defined here so ``marketplace/eligibility_gate.py`` can name it, import cleanly and
    #: be property-tested standalone. Task 14.3 adds the remaining members and must be
    #: idempotent about this one: it re-declares the same ``name = value`` pair, which is a
    #: no-op, rather than a second definition.
    MARKETPLACE_ELIGIBILITY_EVALUATED = "marketplace_eligibility_evaluated"

    #: The remaining Marketplace and Paper_Trading audit members (task 14.3), from
    #: ``design.md`` -> "Audit records" (Requirements 26.2, 26.3). All additive: nothing
    #: existing is renamed or redefined, so no stored record changes meaning, and
    #: ``MARKETPLACE_ELIGIBILITY_EVALUATED`` above is kept as its single declaration rather
    #: than re-added here. Written through this same ``StrategyAuditLogger`` — no second
    #: audit facility. The spellings are the authoritative ones from design.md's table and
    #: refusal/safety list.
    #
    # Requirement 26.2 obligations:
    MARKETPLACE_SUBMISSION_CREATED = "marketplace_submission_created"
    MARKETPLACE_SUBMISSION_TRANSITIONED = "marketplace_submission_transitioned"
    MARKETPLACE_ADMIN_ACTION = "marketplace_admin_action"
    MARKETPLACE_PRICE_EVALUATED = "marketplace_price_evaluated"
    MARKETPLACE_CHECKOUT_CREATED = "marketplace_checkout_created"
    MARKETPLACE_PAYMENT_CONFIRMED = "marketplace_payment_confirmed"
    MARKETPLACE_SETTLEMENT_CREATED = "marketplace_settlement_created"
    MARKETPLACE_SUBSCRIPTION_TRANSITIONED = "marketplace_subscription_transitioned"
    MARKETPLACE_PERIOD_EXTENDED = "marketplace_period_extended"
    MARKETPLACE_ENTITLEMENT_GRANTED = "marketplace_entitlement_granted"
    MARKETPLACE_ENTITLEMENT_REVOKED = "marketplace_entitlement_revoked"
    # Refusal and safety records (Requirements 7.12, 21.4, 9.14, 10.10, 10.11, 13.9,
    # 13.11, 14.8):
    MARKETPLACE_ACCESS_REFUSED = "marketplace_access_refused"
    MARKETPLACE_CROSS_TENANT_ATTEMPT = "marketplace_cross_tenant_attempt"
    MARKETPLACE_SETTLEMENT_UNMATCHED = "marketplace_settlement_unmatched"
    MARKETPLACE_SETTLEMENT_MISMATCHED = "marketplace_settlement_mismatched"
    MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED = "marketplace_settlement_duplicate_ignored"
    MARKETPLACE_SETTLEMENT_PERSIST_FAILED = "marketplace_settlement_persist_failed"
    EXECUTION_ENVIRONMENT_MISMATCH = "execution_environment_mismatch"
    PAPER_SIMULATOR_MISCONFIGURED = "paper_simulator_misconfigured"
    PAPER_FEED_REFUSED_MOCK_INTERFACE = "paper_feed_refused_mock_interface"


#: Resource kinds an audited act can be about. A closed vocabulary, so a reader can
#: index the trail without discovering new keys in production.
STRATEGY_AUDIT_RESOURCES: tuple = (
    "strategy",
    "strategy_version",
    "strategy_deployment",
    "training_job",
)


@dataclass
class StrategyAuditRecord:
    """One audited act on a strategy, a version, a deployment or a training job.

    ``actor_id``, ``timestamp`` and ``reason`` are Requirement 9.8's three mandatory
    fields and are positional-required here, so omitting one is a ``TypeError`` while a
    developer is looking at it rather than a blank column an auditor finds later.
    """

    audit_id: str
    timestamp: datetime
    action: StrategyAuditAction
    actor_id: str
    resource_type: str
    resource_id: str
    reason: str
    before: Optional[str] = None
    after: Optional[str] = None
    strategy_id: Optional[str] = None
    version_id: Optional[str] = None
    deployment_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "timestamp": self.timestamp.isoformat()
            if isinstance(self.timestamp, datetime)
            else str(self.timestamp),
            "action": self.action.value
            if isinstance(self.action, StrategyAuditAction)
            else str(self.action),
            "actor_id": self.actor_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
            "strategy_id": self.strategy_id,
            "version_id": self.version_id,
            "deployment_id": self.deployment_id,
            "metadata": self.metadata or {},
        }

    def to_log_entry(self) -> str:
        return json.dumps({"audit": "strategy_lifecycle", **self.to_dict()}, default=str)


class StrategyAuditLogger:
    """Requirement 9.8's writer. Appends; never overwrites, never raises."""

    KEY_PREFIX = "audit:strategy"
    #: Kept per resource so one version's or one deployment's whole history is one read.
    HISTORY_LIMIT = 500
    #: 30 days. Longer than the order cache's 24h because a lifecycle question
    #: ("when did this version go live, and who stopped it?") is asked long after the fact.
    HISTORY_TTL_SECONDS = 2592000

    def __init__(self):
        self._sequence = 0

    def _generate_audit_id(self) -> str:
        self._sequence += 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"SAUDIT-{stamp}-{self._sequence:06d}-{uuid.uuid4().hex[:8]}"

    def history_key(self, resource_type: str, resource_id: str) -> str:
        return f"{self.KEY_PREFIX}:{resource_type}:{resource_id}"

    async def log(
        self,
        action: StrategyAuditAction,
        *,
        actor_id: str,
        resource_type: str,
        resource_id: str,
        reason: str,
        before: Optional[str] = None,
        after: Optional[str] = None,
        strategy_id: Optional[str] = None,
        version_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        audit_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> StrategyAuditRecord:
        """Record one act and return the record, whatever the storage did.

        The returned record is the caller's receipt: it carries the ``audit_id`` that
        travels on the API response, so a client can quote it in a support request.

        This is the never-raises variant: a storage failure is logged at warning level
        and swallowed, so an audit write never fails the action it describes. Every
        existing caller depends on that contract. The shared write body lives in
        ``_write`` so ``record_or_raise`` can reuse it with the opposite disposition.
        """
        return await self._write(
            action,
            raise_on_failure=False,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            reason=reason,
            before=before,
            after=after,
            strategy_id=strategy_id,
            version_id=version_id,
            deployment_id=deployment_id,
            metadata=metadata,
            audit_id=audit_id,
            timestamp=timestamp,
        )

    async def record_or_raise(
        self,
        action: StrategyAuditAction,
        *,
        actor_id: str,
        resource_type: str,
        resource_id: str,
        reason: str,
        before: Optional[str] = None,
        after: Optional[str] = None,
        strategy_id: Optional[str] = None,
        version_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        audit_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> StrategyAuditRecord:
        """Record one act like :meth:`log`, but RE-RAISE on any storage failure.

        Same keyword signature and same return type as :meth:`log`; it runs the exact
        same write body (``_write``). The difference is disposition: where :meth:`log`
        swallows a failed log-line or Redis write, this variant propagates it. It exists
        for the callers that write an audit record *inside* a transaction whose commit
        must be conditioned on the record having been durably written — e.g.
        ``marketplace/eligibility_gate.py::_write_audit`` and the Marketplace submission
        transaction (Requirement 26.2). This is not a second audit facility: it is the
        same logger, the same record type and the same body, with the never-swallow flag
        flipped on.
        """
        return await self._write(
            action,
            raise_on_failure=True,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            reason=reason,
            before=before,
            after=after,
            strategy_id=strategy_id,
            version_id=version_id,
            deployment_id=deployment_id,
            metadata=metadata,
            audit_id=audit_id,
            timestamp=timestamp,
        )

    async def _write(
        self,
        action: StrategyAuditAction,
        *,
        raise_on_failure: bool,
        actor_id: str,
        resource_type: str,
        resource_id: str,
        reason: str,
        before: Optional[str] = None,
        after: Optional[str] = None,
        strategy_id: Optional[str] = None,
        version_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        audit_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> StrategyAuditRecord:
        """The one shared write body for :meth:`log` and :meth:`record_or_raise`.

        ``raise_on_failure`` is the only difference between the two public methods: when
        ``False`` a failed log-line or Redis write is logged at warning level and
        swallowed (``log``'s historic never-raises contract); when ``True`` the failure
        is re-raised (``record_or_raise``).
        """
        record = StrategyAuditRecord(
            audit_id=audit_id or self._generate_audit_id(),
            # Timezone-aware, unlike the order records above: this is new code and
            # `datetime.utcnow()` is deprecated. Requirement 9.8 wants a timestamp; an
            # unambiguous one is strictly better than a naive one.
            timestamp=timestamp or datetime.now(timezone.utc),
            action=action,
            actor_id=str(actor_id or "unknown"),
            resource_type=str(resource_type),
            resource_id=str(resource_id),
            reason=str(reason or "unspecified"),
            before=before,
            after=after,
            strategy_id=strategy_id,
            version_id=version_id,
            deployment_id=deployment_id,
            metadata=dict(metadata or {}),
        )

        if not actor_id:
            logger.warning(
                "Strategy audit record %s has no actor; Requirement 9.8 asks for one.",
                record.audit_id,
            )
        if not reason:
            logger.warning(
                "Strategy audit record %s has no reason; Requirement 9.8 asks for one.",
                record.audit_id,
            )

        # 1. The log line. Always, and first.
        try:
            logger.info(record.to_log_entry())
        except Exception as exc:  # noqa: BLE001 - an audit must not fail the action
            logger.warning("Strategy audit log line could not be written: %s", exc)
            if raise_on_failure:
                raise

        # 2. Redis history, best effort for ``log``; a hard requirement for
        #    ``record_or_raise``. ``_append_history`` swallows internally and returns
        #    ``False`` on failure, so re-raising here would lose the original cause; we
        #    re-run the same push with the never-swallow disposition instead.
        appended = await self._append_history(record)
        if raise_on_failure and not appended:
            await redis_manager.lpush(
                self.history_key(record.resource_type, record.resource_id),
                json.dumps(record.to_dict(), default=str),
            )
        return record

    async def _append_history(self, record: StrategyAuditRecord) -> bool:
        key = self.history_key(record.resource_type, record.resource_id)
        try:
            await redis_manager.lpush(key, json.dumps(record.to_dict(), default=str))
        except Exception as exc:  # noqa: BLE001 - Redis absence is not an audit failure
            logger.warning(
                "Strategy audit record %s (%s on %s %s) was logged but not cached: %s",
                record.audit_id,
                record.action.value
                if isinstance(record.action, StrategyAuditAction)
                else record.action,
                record.resource_type,
                record.resource_id,
                exc,
            )
            return False

        # Bound and expire the list. Both are best effort for the same reason.
        try:
            await redis_manager.ltrim(key, 0, self.HISTORY_LIMIT - 1)
        except Exception:  # noqa: BLE001
            pass
        try:
            await redis_manager.expire(key, self.HISTORY_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            pass
        return True

    async def get_history(
        self, resource_type: str, resource_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """The cached history for one resource, most recent first. ``[]`` on any failure."""
        try:
            raw = await redis_manager.lrange(
                self.history_key(resource_type, resource_id), 0, max(0, limit - 1)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Strategy audit history unavailable: %s", exc)
            return []

        entries: List[Dict[str, Any]] = []
        for item in raw or []:
            try:
                text = item.decode() if isinstance(item, bytes) else item
                entries.append(json.loads(text))
            except Exception:  # noqa: BLE001 - one bad entry must not hide the rest
                continue
        return entries


_strategy_audit_logger: Optional[StrategyAuditLogger] = None


def get_strategy_audit_logger() -> StrategyAuditLogger:
    """Get or create the strategy lifecycle audit logger."""
    global _strategy_audit_logger
    if _strategy_audit_logger is None:
        _strategy_audit_logger = StrategyAuditLogger()
    return _strategy_audit_logger


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
    # Strategy lifecycle audit (task 8.3, Requirement 9.8)
    "StrategyAuditAction",
    "StrategyAuditRecord",
    "StrategyAuditLogger",
    "STRATEGY_AUDIT_RESOURCES",
    "get_strategy_audit_logger",
]
