# -*- coding: utf-8 -*-
from __future__ import annotations
"""
core/models/execution_record.py - Execution Record Models.

Pydantic and SQLAlchemy models for the execution_records table.
Tracks individual strategy executions (orders/trades).
"""
import hashlib
import logging
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional, Tuple, List
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, validator
from sqlalchemy import JSON, Column, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Index, String, bindparam, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from backend_app.core.database import Base
from backend_app.core.metrics import execution_metrics

logger = logging.getLogger("ExecutionRecord")


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION ID GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

#: DUP-02. The action returned when a retry is refused because the previous attempt MAY
#: already have reached the venue.
#:
#: Distinct from `skip_failed_no_retry`, which means "this failed and policy forbids
#: retrying it". This one means "this may be live at the venue, so resubmitting could
#: place a second real order". The two call for different operator responses — the first
#: is a policy outcome, the second needs reconciliation — so they are different values.
AMBIGUOUS_SUBMISSION_ACTION = "skip_ambiguous_submission"


def submission_may_have_reached_venue(record: Any) -> bool:
    """Could the venue already hold an order for *record*?

    DUP-02. The one question that decides whether an automatic retry is safe.

    `submitted_at` is the evidence, and it is trustworthy for a specific reason:
    `mark_submitted` commits it in its OWN transaction immediately before
    `create_order`, so it survives the process dying mid-submission, and its
    `WHERE submitted_at IS NULL` predicate keeps the first instant authoritative. Its
    absence is therefore positive evidence that the venue call was never reached — the
    same inference `classify_stale_executions` already draws to separate
    `failed_before_submission` from `ambiguous_submission`. Reusing it keeps one
    definition of ambiguity rather than inventing a second.

    `UNKNOWN` returns True regardless of `submitted_at`. That status is written by
    `OrderWatchdog` when it **could not establish** whether the order exists — most often
    because the venue was unreachable. Deferring to `submitted_at` there would rest the
    decision on evidence the status itself declares insufficient.

    An unreadable record or status also returns True. A retry authorisation is the
    dangerous answer, so anything this function cannot positively clear is refused.
    """
    if record is None:
        return True
    try:
        status = getattr(record, "status", None)
        if status is None:
            # A status that cannot be read cannot be shown to be safe.
            return True
        if status == ExecutionStatus.UNKNOWN:
            return True
        return getattr(record, "submitted_at", None) is not None
    except Exception:  # noqa: BLE001 - refuse rather than propagate into a retry grant
        return True


def generate_execution_id(
    tenant_id: UUID,
    strategy_id: str,
    symbol: str,
    timestamp: datetime,
    side: Optional[str] = None,
    qty: Optional[float] = None,
    price: Optional[float] = None,
    execution_interval_minutes: int = 5
) -> str:
    """
    Generate deterministic execution ID based on tenant, strategy, symbol, time bucket,
    and optionally trade details (side, qty, price) to prevent collisions.
    
    Returns:
        Deterministic execution ID (first 16 chars of SHA256 hex digest)
    """
    # Round timestamp to nearest execution interval bucket
    # Example: 10:32:45 with 5-min interval -> 10:30:00
    total_minutes = timestamp.hour * 60 + timestamp.minute
    bucket_minutes = (total_minutes // execution_interval_minutes) * execution_interval_minutes
    bucket_hour = bucket_minutes // 60
    bucket_minute = bucket_minutes % 60
    
    time_bucket = timestamp.replace(
        hour=bucket_hour,
        minute=bucket_minute,
        second=0,
        microsecond=0
    )
    
    # Create canonical string representation
    # Format: "tenant:strategy:symbol:time_bucket:side:qty:price"
    canonical_parts = [
        str(tenant_id),
        strategy_id,
        symbol.upper(),
        time_bucket.isoformat()
    ]
    if side is not None:
        canonical_parts.append(str(side).upper())
    if qty is not None:
        canonical_parts.append(f"{float(qty):.8f}")
    if price is not None:
        canonical_parts.append(f"{float(price):.8f}")
        
    canonical_string = ":".join(canonical_parts)
    
    # Compute SHA256 hash
    hash_object = hashlib.sha256(canonical_string.encode('utf-8'))
    hash_hex = hash_object.hexdigest()
    
    # Use first 16 characters of hash (sufficient for uniqueness, prevents overly long IDs)
    # Prefix with "exec_" to identify as execution record
    execution_id = f"exec_{hash_hex[:16]}"
    
    return execution_id


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class ExecutionStatus(str, Enum):
    """Execution status enumeration."""
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"



class ExecutionSide(str, Enum):
    """Execution side (buy/sell)."""
    BUY = "buy"
    SELL = "sell"


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ExecutionRecordBase(BaseModel):
    """Base execution record model."""
    execution_id: str = Field(..., description="Unique execution identifier")
    tenant_id: UUID = Field(..., description="Tenant UUID for isolation")
    task_id: Optional[UUID] = Field(None, description="Associated DAG task ID")
    strategy_id: str = Field(..., description="Strategy identifier")
    symbol: str = Field(..., description="Trading symbol (e.g., BTCUSDT)")
    side: ExecutionSide = Field(..., description="Buy or sell")
    size: str = Field("0", description="Order size")
    price: Optional[str] = Field(None, description="Order price")
    status: ExecutionStatus = Field(default=ExecutionStatus.PENDING)
    order_id: Optional[str] = Field(None, description="Exchange order ID")
    result: Optional[Dict[str, Any]] = Field(None, description="Execution result JSON")
    
    @validator('symbol')
    def validate_symbol(cls, v):
        if not v or not v.strip():
            raise ValueError('Symbol cannot be empty')
        return v.strip().upper()
    
    @validator('strategy_id')
    def validate_strategy_id(cls, v):
        if not v or not v.strip():
            raise ValueError('Strategy ID cannot be empty')
        return v.strip()
    
    @validator('side')
    def validate_side(cls, v):
        return v.lower() if isinstance(v, str) else v


class ExecutionRecordCreate(ExecutionRecordBase):
    """Model for creating a new execution record."""
    pass


class ExecutionRecordUpdate(BaseModel):
    """Model for updating an execution record."""
    status: Optional[ExecutionStatus] = None
    order_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class ExecutionRecordInDB(ExecutionRecordBase):
    """Model for execution record as stored in database."""
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ExecutionRecordResponse(ExecutionRecordInDB):
    """Model for API response."""
    duration_seconds: Optional[int] = Field(None, description="Execution duration")


class ExecutionStats(BaseModel):
    """Execution statistics for a tenant."""
    tenant_id: UUID
    pending_count: int
    executing_count: int
    completed_count: int
    failed_count: int
    total_count: int
    last_execution_at: Optional[datetime]


class ExecutionListResponse(BaseModel):
    """Paginated list of executions."""
    items: list[ExecutionRecordResponse]
    total: int
    page: int
    page_size: int


# ═══════════════════════════════════════════════════════════════════════════════
# SQLALCHEMY ORM MODEL
# ═══════════════════════════════════════════════════════════════════════════════

class ExecutionRecordModel(Base):
    """
    SQLAlchemy ORM model for execution_records table.
    
    Tracks individual strategy executions (orders/trades) with full
    order lifecycle management for exchange reconciliation.
    
    STEP 3.2 — EXECUTION RECORD SCHEMA
    """
    __tablename__ = "execution_records"
    
    # Primary key
    execution_id = Column(String, primary_key=True)  # STEP 3: PK
    
    # Tenant isolation
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    
    # Relationships
    task_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)
    strategy_id = Column(String, nullable=False)
    
    # Execution details (STEP 3.2: size, price added)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)  # 'buy' or 'sell'
    size = Column(String, nullable=False)  # STEP 3.2: Order size (as string for precision)
    price = Column(String, nullable=True)   # STEP 3.2: Order price (null for market orders)
    
    # Order state (STEP 3.2: Enhanced status tracking)
    status = Column(SQLEnum(ExecutionStatus), nullable=False, default=ExecutionStatus.PENDING)
    
    # STEP 3.2: Exchange reconciliation fields
    #
    # TRADE-12. `order_id`'s comment said "Exchange order ID" and the application wrote
    # exactly that into it -- but the column carries
    # `FOREIGN KEY (order_id) REFERENCES orders(id)` (added by
    # `alembic/versions/add_foreign_keys_20260817.py`), and `public.orders` holds THIS
    # platform's orders. A venue-assigned id is not one of those, so the write is refused
    # with 23503. Measured in
    # staging/probe_phase9_order_id_fk_blocks_completion.py:
    #     VENUE_NUMERIC_ID = REJECTED / VENUE_STRING_ID = REJECTED /
    #     EXISTING_ORDERS_ID = ACCEPTED / EXECUTION_RECORDS_WITH_ORDER_ID = 0
    # Two identifiers were conflated into one column; they are now separate.
    order_id = Column(String, nullable=True, index=True)  # THIS platform's order (FK -> orders.id)
    venue_order_id = Column(String, nullable=True, index=True)  # the EXCHANGE's id; no FK
    filled_size = Column(String, nullable=True)  # Amount filled so far
    avg_price = Column(String, nullable=True)    # Average fill price
    remaining_size = Column(String, nullable=True)  # Remaining to fill
    
    # STEP 3.2: Exchange metadata for reconciliation
    exchange_id = Column(String, nullable=True)  # Exchange identifier (binance, coinbase, etc.)
    last_exchange_sync = Column(DateTime(timezone=True), nullable=True)  # Last sync timestamp
    exchange_status = Column(String, nullable=True)  # Exchange's reported status
    
    # Results
    result = Column(JSON, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    submitted_at = Column(DateTime(timezone=True), nullable=True)  # STEP 3.2: When sent to exchange
    filled_at = Column(DateTime(timezone=True), nullable=True)     # STEP 3.2: When completely filled
    
    # Indexes for STEP 3 queries
    __table_args__ = (
        Index('idx_execution_records_tenant_symbol', 'tenant_id', 'symbol'),
        Index('idx_execution_records_task_id', 'task_id'),
        Index('idx_execution_records_status', 'status'),
        Index('idx_execution_records_tenant_status', 'tenant_id', 'status'),
        Index('idx_execution_records_created_at', 'created_at'),
        # STEP 3.2: New indexes for reconciliation
        Index('idx_execution_records_order_id', 'order_id'),
        Index('idx_execution_records_exchange_sync', 'last_exchange_sync'),
        Index('idx_execution_records_active', 'tenant_id', 'status', 
              postgresql_where=text("status IN ('pending', 'executing')")),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# REPOSITORY PATTERN
# ═══════════════════════════════════════════════════════════════════════════════



class ExecutionRecordRepository:
    """Repository for execution record CRUD operations."""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def check_idempotent_execution(
        self,
        tenant_id: UUID,
        strategy_id: str,
        symbol: str,
        timestamp: datetime,
        side: ExecutionSide,
        qty: Optional[float] = None,
        price: Optional[float] = None,
        task_id: Optional[UUID] = None,
        execution_interval_minutes: int = 5,
        allow_failed_retry: bool = True
    ) -> tuple[str, str, Optional[Dict[str, Any]]]:
        """
        Idempotent execution check - prevents duplicate trade executions.
        
        This function implements the idempotency pattern for trade execution:
        1. Generates deterministic execution_id from inputs
        2. Checks PostgreSQL for existing execution
        3. Returns action based on existing status:
           - 'completed' → SKIP, return existing result
           - 'executing' → SKIP (already running)
           - 'failed' → ALLOW_RETRY (if enabled)
           - NOT EXISTS → INSERT pending, EXECUTE
        
        Args:
            tenant_id: Tenant UUID for isolation
            strategy_id: Strategy identifier
            symbol: Trading symbol (e.g., BTCUSDT)
            timestamp: Execution timestamp (rounded to bucket)
            side: Buy or sell
            task_id: Optional associated DAG task ID
            execution_interval_minutes: Time bucket for idempotency (default 5)
            allow_failed_retry: Whether to allow retrying failed executions
        
        Returns:
            Tuple of (execution_id, action, existing_result):
            - execution_id: Generated deterministic ID
            - action: 'skip_return_result' | 'skip_already_running' | 'allow_retry' | 'execute'
            - existing_result: Dict with result if completed, None otherwise
        
        Example:
            >>> execution_id, action, result = repo.check_idempotent_execution(
            ...     tenant_id=UUID("..."),
            ...     strategy_id="mean_reversion",
            ...     symbol="BTCUSDT",
            ...     timestamp=datetime.utcnow(),
            ...     side=ExecutionSide.BUY
            ... )
            >>> if action == 'execute':
            ...     # Proceed with execution
            ...     execute_trade(...)
            ...     repo.update_status(execution_id, tenant_id, ExecutionStatus.COMPLETED, result=...)
            >>> elif action == 'skip_return_result':
            ...     # Return cached result
            ...     return result
        """
        # Generate deterministic execution_id
        execution_id = generate_execution_id(
            tenant_id=tenant_id,
            strategy_id=strategy_id,
            symbol=symbol,
            timestamp=timestamp,
            side=side.value if hasattr(side, 'value') else str(side),
            qty=qty,
            price=price,
            execution_interval_minutes=execution_interval_minutes
        )
        
        # Record execution attempt
        execution_metrics.record_attempt(
            tenant_id=str(tenant_id),
            status="pending"
        )
        
        # Check PostgreSQL for existing execution
        existing = self.get_by_id(execution_id, tenant_id)
        
        if existing:
            status = existing.status
            
            # CASE 1: Already completed → SKIP, return existing result
            if status == ExecutionStatus.COMPLETED:
                # Record metrics
                execution_metrics.record_duplicate_skipped(
                    tenant_id=str(tenant_id),
                    execution_id=execution_id
                )
                
                logger.info(
                    f"IDEMPOTENT_SKIP_COMPLETED: execution_id={execution_id} "
                    f"symbol={symbol} side={side.value}",
                    extra={
                        "event": "IDEMPOTENT_SKIP_COMPLETED",
                        "execution_id": execution_id,
                        "symbol": symbol,
                        "side": side.value,
                        "status": status.value,
                    }
                )
                return (
                    execution_id,
                    "skip_return_result",
                    existing.result
                )
            
            # CASE 2: Currently executing → SKIP (already running)
            if status == ExecutionStatus.EXECUTING:
                logger.info(
                    f"IDEMPOTENT_SKIP_EXECUTING: execution_id={execution_id} "
                    f"symbol={symbol} side={side.value}",
                    extra={
                        "event": "IDEMPOTENT_SKIP_EXECUTING",
                        "execution_id": execution_id,
                        "symbol": symbol,
                        "side": side.value,
                        "status": status.value,
                    }
                )
                return (
                    execution_id,
                    "skip_already_running",
                    None
                )
            
            # ═══════════════════════════════════════════════════════════════════
            # DUP-02: A SUBMISSION THAT MAY HAVE REACHED THE VENUE IS NEVER RETRIED
            # ═══════════════════════════════════════════════════════════════════
            # Checked BEFORE the FAILED and PENDING cases below, because both of those
            # returned `allow_retry` — and `execute_with_idempotency` had no branch for
            # that value, so it fell through to `claim_execution` and resubmitted. CASE 3
            # additionally reset the row to PENDING, which is what made the claim succeed
            # a second time.
            #
            # The exposure: `place_order` classifies a lost response as `NetworkError` and
            # returns `status="rejected"`, after which the engine records FAILED. That is
            # an ambiguous outcome recorded as a determinate one. The next tick inside the
            # same 5-minute bucket derives the SAME execution_id, was granted
            # `allow_retry`, and sent `create_order` again. On a venue that does not
            # honour our `clientOrderId` — measured: coinbase overwrites it, kraken
            # ignores it — nothing at the venue can refuse the duplicate.
            #
            # Deliberately NOT treating every FAILED row as ambiguous: a row with
            # `submitted_at IS NULL` provably never reached `create_order`, so the
            # existing retry policy still applies to it in full. Over-refusing here would
            # block legitimate first submissions, which is a trading outage rather than a
            # safety improvement.
            #
            # No state change and no recovery action is taken here. This function decides;
            # `OrderWatchdog` is the only owner of adjudication, and introducing a second
            # one would be worse than the defect.
            #
            # Regression: tests/test_dup02_ambiguous_submission_is_not_retried.py
            if status != ExecutionStatus.COMPLETED and submission_may_have_reached_venue(
                existing
            ):
                logger.critical(
                    "DUP-02_AMBIGUOUS_SUBMISSION_NOT_RETRIED: execution_id=%s "
                    "symbol=%s side=%s status=%s submitted_at=%s order_id=%s — this "
                    "execution may already be live at the venue, so it was NOT "
                    "resubmitted. It requires reconciliation against the venue.",
                    execution_id, symbol,
                    getattr(side, "value", side), status,
                    getattr(existing, "submitted_at", None),
                    getattr(existing, "order_id", None),
                    extra={
                        "event": "DUP-02_AMBIGUOUS_SUBMISSION_NOT_RETRIED",
                        "execution_id": execution_id,
                        "symbol": symbol,
                        "status": str(status),
                    },
                )
                return (
                    execution_id,
                    AMBIGUOUS_SUBMISSION_ACTION,
                    existing.result if existing is not None else None,
                )

            # CASE 3: Failed → Allow retry (if enabled)
            # CRITICAL: Reuse SAME execution_id to prevent duplicate trades on retry
            # The deterministic execution_id ensures the same trade request always
            # maps to the same record, preventing double execution.
            if status == ExecutionStatus.FAILED:
                if allow_failed_retry:
                    logger.info(
                        f"IDEMPOTENT_ALLOW_RETRY: execution_id={execution_id} "
                        f"symbol={symbol} side={side.value} "
                        f"error={existing.result.get('error') if existing.result else 'unknown'}",
                        extra={
                            "event": "IDEMPOTENT_ALLOW_RETRY",
                            "execution_id": execution_id,
                            "symbol": symbol,
                            "side": side.value,
                            "previous_error": existing.result.get('error') if existing.result else None,
                        }
                    )
                    # Reset status to 'pending' for retry
                    # SAME execution_id is reused - this is KEY for idempotency
                    self.update_status(
                        execution_id=execution_id,
                        tenant_id=tenant_id,
                        status=ExecutionStatus.PENDING
                    )
                    return (
                        execution_id,  # SAME ID on retry
                        "allow_retry",
                        None
                    )
                else:
                    logger.warning(
                        f"IDEMPOTENT_RETRY_DISABLED: execution_id={execution_id} "
                        f"symbol={symbol} side={side.value}",
                        extra={
                            "event": "IDEMPOTENT_RETRY_DISABLED",
                            "execution_id": execution_id,
                            "symbol": symbol,
                            "side": side.value,
                        }
                    )
                    return (
                        execution_id,
                        "skip_failed_no_retry",
                        existing.result
                    )
            
            # CASE 4: Pending → Something went wrong, allow retry
            if status == ExecutionStatus.PENDING:
                logger.warning(
                    f"IDEMPOTENT_STALE_PENDING: execution_id={execution_id} "
                    f"symbol={symbol} side={side.value} "
                    f"age_hours={(datetime.utcnow() - existing.created_at).total_seconds() / 3600:.1f}",
                    extra={
                        "event": "IDEMPOTENT_STALE_PENDING",
                        "execution_id": execution_id,
                        "symbol": symbol,
                        "side": side.value,
                        "created_at": existing.created_at.isoformat(),
                    }
                )
                return (
                    execution_id,
                    "allow_retry",
                    None
                )
        
        # CASE 5: NOT EXISTS → INSERT pending record, EXECUTE
        logger.info(
            f"IDEMPOTENT_NEW_EXECUTION: execution_id={execution_id} "
            f"symbol={symbol} side={side.value}",
            extra={
                "event": "IDEMPOTENT_NEW_EXECUTION",
                "execution_id": execution_id,
                "symbol": symbol,
                "side": side.value,
                "strategy_id": strategy_id,
            }
        )
        
        # Insert new pending record
        new_record = ExecutionRecordModel(
            execution_id=execution_id,
            tenant_id=tenant_id,
            task_id=task_id,
            strategy_id=strategy_id,
            symbol=symbol.upper(),
            side=side.value,
            size=str(qty) if qty is not None else "0",
            price=str(price) if price is not None else None,
            status=ExecutionStatus.PENDING,
            order_id=None,
            result=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        
        try:
            self.db.add(new_record)
            self.db.commit()
            self.db.refresh(new_record)
            return (
                execution_id,
                "execute",
                None
            )
        except Exception as e:
            self.db.rollback()
            # Concurrency race: Another worker inserted this record simultaneously
            existing = self.get_by_id(execution_id, tenant_id)
            if existing:
                if existing.status == ExecutionStatus.COMPLETED:
                    return (execution_id, "skip_return_result", existing.result)
                elif existing.status == ExecutionStatus.EXECUTING:
                    return (execution_id, "skip_already_running", None)
                # DUP-02. The same guard as the primary path. This branch is reached when
                # a concurrent worker inserted the row between the existence check and
                # this INSERT, so it decides for exactly the racing case the guard most
                # needs to cover — and it previously granted `allow_retry` here too.
                if existing.status != ExecutionStatus.COMPLETED and (
                    submission_may_have_reached_venue(existing)
                ):
                    logger.critical(
                        "DUP-02_AMBIGUOUS_SUBMISSION_NOT_RETRIED (insert-conflict "
                        "path): execution_id=%s status=%s submitted_at=%s — not "
                        "resubmitted; requires reconciliation.",
                        execution_id, existing.status, existing.submitted_at,
                    )
                    return (
                        execution_id,
                        AMBIGUOUS_SUBMISSION_ACTION,
                        existing.result,
                    )
                if existing.status == ExecutionStatus.PENDING:
                    return (execution_id, "allow_retry", None)
                elif existing.status == ExecutionStatus.FAILED:
                    if allow_failed_retry:
                        return (execution_id, "allow_retry", None)
                    return (execution_id, "skip_failed_no_retry", existing.result)
            raise e
    
    def get_by_id(self, execution_id: str, tenant_id: UUID) -> Optional[ExecutionRecordModel]:
        """Get execution record by ID and tenant."""
        return self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.execution_id == execution_id,
            ExecutionRecordModel.tenant_id == tenant_id
        ).first()
    
    def get_by_task_id(self, task_id: UUID, tenant_id: UUID) -> list[ExecutionRecordModel]:
        """Get all executions for a task."""
        return self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.task_id == task_id,
            ExecutionRecordModel.tenant_id == tenant_id
        ).order_by(ExecutionRecordModel.created_at.desc()).all()
    
    def list_by_tenant(
        self,
        tenant_id: UUID,
        status: Optional[ExecutionStatus] = None,
        symbol: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> tuple[list[ExecutionRecordModel], int]:
        """List executions for tenant with optional filters."""
        query = self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.tenant_id == tenant_id
        )
        
        if status:
            query = query.filter(ExecutionRecordModel.status == status)
        
        if symbol:
            query = query.filter(ExecutionRecordModel.symbol == symbol.upper())
        
        total = query.count()
        
        results = query.order_by(
            ExecutionRecordModel.created_at.desc()
        ).offset(offset).limit(limit).all()
        
        return results, total
    
    def create(self, data: ExecutionRecordCreate, auto_commit: bool = True) -> ExecutionRecordModel:
        """Create a new execution record."""
        side_val = data.side.value if hasattr(data.side, 'value') else str(data.side)
        db_record = ExecutionRecordModel(
            execution_id=data.execution_id,
            tenant_id=data.tenant_id,
            task_id=data.task_id,
            strategy_id=data.strategy_id,
            symbol=data.symbol.upper(),
            side=side_val,
            size=data.size,
            price=data.price,
            status=data.status,
            order_id=data.order_id,
            result=data.result,
        )
        
        self.db.add(db_record)
        if auto_commit:
            self.db.commit()
            self.db.refresh(db_record)
        else:
            self.db.flush()
        
        return db_record
    
    def update(
        self,
        execution_id: str,
        tenant_id: UUID,
        data: ExecutionRecordUpdate
    ) -> Optional[ExecutionRecordModel]:
        """Update an execution record."""
        record = self.get_by_id(execution_id, tenant_id)
        if not record:
            return None
        
        update_data = data.dict(exclude_unset=True)
        
        for field, value in update_data.items():
            if field == 'status' and value:
                value = value.value if hasattr(value, 'value') else value
            setattr(record, field, value)
        
        self.db.commit()
        self.db.refresh(record)
        
        return record
    
    def claim_execution(
        self,
        execution_id: str,
        tenant_id: UUID
    ) -> tuple[bool, Optional[ExecutionRecordModel]]:
        """
        Atomically claim execution for processing.
        
        Updates status from 'pending' to 'executing' only if currently 'pending'.
        This ensures only ONE worker executes a given trade (optimistic locking).
        
        Args:
            execution_id: Execution identifier
            tenant_id: Tenant UUID for isolation
        
        Returns:
            Tuple of (success, record):
            - success: True if claimed (rows_affected == 1), False otherwise
            - record: Updated record if claimed, None otherwise
        
        Example:
            >>> claimed, record = repo.claim_execution("exec_abc123", tenant_id)
            >>> if claimed:
            ...     # Only this worker executes
            ...     result = await execute_trade(...)
            ...     repo.update_status(execution_id, tenant_id, ExecutionStatus.COMPLETED, result=result)
            ... else:
            ...     # Another worker already executing
            ...     logger.info("Execution already claimed by another worker")
        """
        # Atomic UPDATE with status check (optimistic locking)
        #
        # TRADE-06. THIS BRANCH USED TO BE UNREACHABLE ON THE PROVISIONED SCHEMA.
        # The predicate was `(status = 'PENDING' OR status = 'pending')`, and
        # `execution_records.status` is the enum `executionstatus` whose labels are
        # UPPERCASE only (`PENDING, EXECUTING, COMPLETED, FAILED, UNKNOWN`). PostgreSQL
        # cannot parse `'pending'` as a value of that type, so the statement raised
        # `invalid input value for enum executionstatus: "pending"` on EVERY call. The
        # `except` below caught it, logged "falling back", and the ORM path did the work.
        #
        # The outcome was still correct — the ORM fallback is a single UPDATE guarded by
        # rowcount, so the one-worker guarantee held — but the documented atomic
        # `UPDATE ... RETURNING` path had never executed here, and every single execution
        # claim emitted a warning. Measured with staging/probe_claim_execution_branch.py:
        #   RAW_LOWERCASE_PREDICATE=REJECTED ... invalid input value for enum
        #   RAW_UPPER_CAST_PREDICATE=ACCEPTED
        #   CLAIM_EXECUTION_RESULT claimed=True   FALLBACK_WARNINGS=1
        #
        # `upper(status::text)` compares correctly whether the column is an enum or a
        # varchar, which is what the two-literal form was reaching for.
        #
        # The `SET` clause said `'executing'`, lowercase, which this type cannot represent
        # either. BOTH halves of the statement were unparseable, which is corroboration
        # that it had never once succeeded rather than having regressed recently.
        # Regression: tests/test_trade01_concurrent_exposure_postgres.py
        dialect = self.db.bind.dialect.name if self.db and self.db.bind else "sqlite"
        if dialect == "postgresql":
            try:
                result = self.db.execute(
                    text("""
                        UPDATE execution_records
                        SET status = 'EXECUTING',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE execution_id = :execution_id
                        AND tenant_id = :tenant_id
                        AND upper(status::text) = 'PENDING'
                        RETURNING *
                    """).bindparams(
                        bindparam("tenant_id", type_=PG_UUID(as_uuid=True))
                    ),
                    {
                        "execution_id": execution_id,
                        "tenant_id": tenant_id,
                    }
                )
                updated_row = result.fetchone()
                self.db.commit()
                
                if updated_row:
                    record = ExecutionRecordModel(
                        execution_id=updated_row.execution_id,
                        tenant_id=updated_row.tenant_id,
                        task_id=updated_row.task_id,
                        strategy_id=updated_row.strategy_id,
                        symbol=updated_row.symbol,
                        side=updated_row.side,
                        size=updated_row.size,
                        price=updated_row.price,
                        status=ExecutionStatus.EXECUTING,
                        order_id=updated_row.order_id,
                        result=updated_row.result,
                        created_at=updated_row.created_at,
                        updated_at=updated_row.updated_at,
                    )
                    return (True, record)
                return (False, None)
            except Exception as e:
                self.db.rollback()
                logger.warning(f"PostgreSQL claim execution RETURNING failed, falling back: {e}")

        # Dialect-agnostic atomic query UPDATE with rowcount (safe across SQLite, Postgres, MySQL)
        try:
            tenant_val = tenant_id if not isinstance(tenant_id, str) else UUID(tenant_id)
            rows_updated = self.db.query(ExecutionRecordModel).filter(
                ExecutionRecordModel.execution_id == execution_id,
                ExecutionRecordModel.tenant_id == tenant_val,
                ExecutionRecordModel.status.in_([ExecutionStatus.PENDING, "pending", "PENDING"])
            ).update(
                {
                    ExecutionRecordModel.status: ExecutionStatus.EXECUTING,
                    ExecutionRecordModel.updated_at: datetime.utcnow()
                },
                synchronize_session=False
            )
            self.db.commit()
            if rows_updated > 0:
                record = self.get_by_id(execution_id, tenant_id)
                return (True, record)
            return (False, None)
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error claiming execution {execution_id}: {e}")
            return (False, None)
    
    #: How long an `executing` row is counted as in-flight exposure before it is treated
    #: as abandoned by a crashed worker. A row that is still `executing` after this long
    #: did not reach a terminal status, and holding its notional against the account
    #: forever would ratchet available capacity down to zero after enough crashes.
    #:
    #: 300s rather than `bot_ownership.STALE_AFTER_SECONDS` (90s): that value is tuned to
    #: a 15s heartbeat, and an execution has no heartbeat. 300s is longer than any venue
    #: submission plus retry budget on this path, so a live order in progress is never
    #: mistaken for a dead one.
    CAPACITY_STALE_AFTER_SECONDS = 300

    def claim_execution_within_capacity(
        self,
        execution_id: str,
        tenant_id: UUID,
        *,
        notional: Decimal,
        ceiling: Decimal,
        stale_after_seconds: Optional[int] = None,
    ) -> tuple[bool, Optional[ExecutionRecordModel], Optional[str]]:
        """Claim an execution ONLY if its notional still fits the account's ceiling.

        TRADE-01. WHAT WAS WRONG
        ------------------------
        The only capital bound on the live path was a read-then-compare in
        `execution_engine.py` (`_notional > _ceiling`, where `_ceiling` is
        `self.current_equity * self.max_leverage` read out of PROCESS MEMORY). Nothing was
        decremented, reserved or locked, and `generate_execution_id` folds quantity and
        price into the canonical string — so two orders that differ in size or price get
        DIFFERENT execution ids and DIFFERENT Redis locks, pass the identical ceiling
        independently, and both proceed. Two orders at 60% of the ceiling each commit
        120% of it. There was no serialisation point anywhere on the path: no
        `FOR UPDATE`, no advisory lock, no exposure table.

        THE RESERVATION, AND WHY IT IS THIS SHAPE
        -----------------------------------------
        The reservation IS the `executing` row. No new table and no new state: the row
        this method claims is the record of the capacity it consumes, so there is nothing
        that can leak independently of the execution it belongs to.

        One transaction does three things in order:

        1. `pg_advisory_xact_lock` keyed on the tenant. This is the serialisation point
           that did not exist. Two concurrent attempts for the same account queue here, so
           the second one's SUM sees the first one's claim. Transaction-scoped, so it is
           released by COMMIT or ROLLBACK and a crashed worker cannot hold it.
        2. SUM the in-flight notional for the tenant — rows in `PENDING` or `EXECUTING`,
           excluding this execution, newer than the stale cutoff.
        3. Refuse if `committed + notional > ceiling`; otherwise perform the SAME
           `UPDATE … WHERE status = 'PENDING'` claim `claim_execution` performs.

        WHAT IS SUMMED, AND WHAT IS NOT
        -------------------------------
        * `EXECUTING` rows only. `PENDING` rows are recorded intents, not admitted
          exposure, and counting them makes two intents block each other for good — see
          the long comment at the SUM itself for why excluding them is still safe.
        * `COMPLETED` rows are NOT counted. A completed order's exposure belongs to
          equity and position accounting, which `current_equity` already reflects.
          Counting them too would shrink available capacity monotonically over the
          lifetime of the account.
        * `status = 'EXECUTING'` is served by `idx_execution_records_active`, a partial
          index on `(tenant_id, status)` whose predicate is
          `status = 'PENDING' OR status = 'EXECUTING'`; the query's condition implies the
          index predicate, so the index applies. The enum `executionstatus` carries
          UPPERCASE labels (`PENDING, EXECUTING, COMPLETED, FAILED, UNKNOWN`), measured
          from the live catalogue — a lowercase literal is not merely a non-match, it is
          an unparseable value for the type (see TRADE-06 on `claim_execution`).
        * `size` and `price` are `character varying` on this table, not numeric, so the
          casts are required rather than defensive: `ABS(size)` fails outright with
          "function abs(character varying) does not exist".
        * TRADE-11, CLOSED. `price` is nullable and a market order carries no usable
          price. This used to mean `size * NULL` is NULL, SUM skips it, and such a row
          contributed NOTHING to committed exposure — it vanished from the aggregate
          entirely. Under-reporting committed exposure is the one direction a capital
          check must never fail in, because it admits more risk than the ceiling allows
          and does so silently. Those rows are now COUNTED and the reservation REFUSES
          while any exist, since the true exposure is unknowable. `'0'` counts as
          unusable too: a zero price is an unknown notional, not a zero one.
          `execution_engine._capacity_bounds_for` applies the matching rule to the
          incoming order, so neither side of the comparison can be silently zero.
        * A row whose `size`/`price` text is not castable raises, which rolls back and
          refuses. That is the correct direction for a capital check.

        Returns:
            `(claimed, record, refusal_reason)`. `refusal_reason` is None when claimed,
            and is a human-readable explanation naming the figures when refused.

        Regression: tests/test_trade01_concurrent_exposure_postgres.py
        """
        stale = (
            self.CAPACITY_STALE_AFTER_SECONDS
            if stale_after_seconds is None
            else int(stale_after_seconds)
        )
        dialect = self.db.bind.dialect.name if self.db and self.db.bind else "sqlite"

        if dialect != "postgresql":
            # No advisory locks, so no serialisation point. The judgement is the same one
            # `core/bot_ownership.claim_bot` makes for a missing claim store, and it
            # reuses the same detector so the two cannot drift: is there a second process
            # to coordinate with?
            from backend_app.core.runtime_env import local_state_is_acceptable

            if local_state_is_acceptable():
                logger.warning(
                    "TRADE-01: %s has no advisory locks, so concurrent exposure cannot be "
                    "serialised. This is a declared local/test environment, so the claim "
                    "falls back to the single-row check. That is NOT safe with more than "
                    "one process.",
                    dialect,
                )
                claimed, record = self.claim_execution(execution_id, tenant_id)
                return (claimed, record, None if claimed else "claim contention")

            logger.error(
                "TRADE-01: refusing execution %s. The configured database is %s, which "
                "offers no advisory lock, so two concurrent orders cannot be serialised "
                "against the same account ceiling. Point DATABASE_URL at PostgreSQL, or "
                "declare a local environment with DEV_MODE=true / ENV=development.",
                execution_id, dialect,
            )
            return (
                False,
                None,
                f"Capacity reservation unavailable: the database dialect is {dialect}, "
                f"which cannot serialise concurrent exposure checks. Refused rather than "
                f"permitting unbounded concurrent notional.",
            )

        try:
            # 1. SERIALISE PER TENANT. Everything below runs with this held.
            self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": f"aerora:exposure:{tenant_id}"},
            )

            # 2. AUTHORITATIVE ADMITTED EXPOSURE, read under the lock.
            #
            # `EXECUTING` ONLY — NOT `PENDING`, AND THAT IS LOAD-BEARING.
            # A `PENDING` row is an order that has been RECORDED, not admitted: nothing
            # has been claimed and nothing has been sent to a venue. Counting pending
            # intents looks more conservative and is actually broken, because two recorded
            # intents of 60% each then block EACH OTHER — both see the other's notional,
            # both are refused, and the account can never use capacity it genuinely has.
            # That is a liveness failure, and it is not hypothetical: it is what the first
            # version of this method did, and two of its own tests caught it.
            #
            # Counting only admitted exposure is still SAFE, and the advisory lock above
            # is why. `PENDING -> EXECUTING` happens nowhere except the claim below, and
            # the claim runs under this lock after this SUM. So for a second order to be
            # admitted it must take the lock, re-read this SUM with the first order
            # already `EXECUTING`, and be measured against it. The invariant enforced is
            #     sum(notional of EXECUTING rows) <= ceiling
            # which is the quantity that corresponds to real exposure.
            # TRADE-11. A ROW WHOSE NOTIONAL CANNOT BE COMPUTED IS COUNTED AS UNUSABLE,
            # NOT AS ZERO.
            #
            # This query used to be the SUM alone, and `price` is nullable: `size * NULL`
            # is NULL, SUM SKIPS NULL, so a priceless `EXECUTING` row contributed nothing
            # to committed exposure and simply disappeared from the aggregate. The
            # method's own docstring conceded it ("a market order contributes nothing to
            # committed exposure") and a test pinned it as expected behaviour.
            #
            # Under-reporting committed exposure is the one direction a capital check must
            # never fail in: it admits MORE risk than the ceiling allows, silently, and
            # the more such rows exist the wider the gap. Two independent producers of
            # them were measured: a live market order (written as price '0' or NULL), and
            # `exchange_reconciliation`'s ghost-order insert, which sets `price=None`
            # explicitly.
            #
            # So the unusable rows are COUNTED and the caller refuses if there are any,
            # rather than quietly summing around them. `'0'` is deliberately included as
            # unusable: a zero price is not a zero-notional position, it is an unknown
            # one. Note this is strictly a fail-closed change -- it can only refuse
            # orders that previously passed, never admit ones that previously failed.
            row = self.db.execute(
                text("""
                    SELECT COALESCE(
                               SUM(ABS(size::numeric) * ABS(NULLIF(price, '')::numeric)),
                               0
                           ) AS committed,
                           COUNT(*) FILTER (
                               WHERE price IS NULL
                                  OR btrim(price) = ''
                                  OR NULLIF(btrim(price), '')::numeric = 0
                           ) AS unusable
                      FROM execution_records
                     WHERE tenant_id = :tenant_id
                       AND status = 'EXECUTING'
                       AND execution_id <> :execution_id
                       AND created_at > now() - make_interval(secs => :stale)
                """).bindparams(bindparam("tenant_id", type_=PG_UUID(as_uuid=True))),
                {
                    "tenant_id": tenant_id,
                    "execution_id": execution_id,
                    "stale": stale,
                },
            ).fetchone()

            committed = row.committed if row is not None else 0
            unusable = int(row.unusable) if row is not None else 0

            if unusable:
                # FAIL CLOSED. The true committed exposure is at least the sum computed
                # above and is not knowable, so no new order can be shown to fit.
                self.db.rollback()
                reason = (
                    f"Capacity reservation refused: this account has {unusable} in-flight "
                    f"execution(s) whose notional cannot be computed (no usable price), "
                    f"so its committed exposure is unknown and at least "
                    f"{Decimal(str(committed or 0))}. An order cannot be shown to fit a "
                    f"ceiling that cannot be measured. Refused before any exchange call."
                )
                logger.critical(
                    "TRADE-11: refusing execution %s for tenant %s: %d in-flight row(s) "
                    "have no usable price, so committed exposure is unmeasurable "
                    "(known-at-least %s, ceiling %s).",
                    execution_id, tenant_id, unusable, committed, ceiling,
                )
                return (False, None, reason)

            committed_notional = Decimal(str(committed if committed is not None else 0))
            requested = Decimal(str(notional))
            limit = Decimal(str(ceiling))

            # 3. DECIDE, still holding the lock.
            if committed_notional + requested > limit:
                self.db.rollback()
                reason = (
                    f"Capacity reservation refused: this account already has "
                    f"{committed_notional} of in-flight notional and this order adds "
                    f"{requested}, which together exceed the fundable ceiling {limit}. "
                    f"Refused before any exchange call."
                )
                logger.critical(
                    "TRADE-01: refusing execution %s for tenant %s: in-flight %s + "
                    "requested %s > ceiling %s.",
                    execution_id, tenant_id, committed_notional, requested, limit,
                )
                return (False, None, reason)

            # 4. THE CLAIM ITSELF — identical predicate to `claim_execution`, so the
            #    single-worker guarantee is unchanged; it is now simply inside the
            #    capacity decision rather than beside it.
            #
            # `upper(status::text) = 'PENDING'` and NOT
            # `(status = 'PENDING' OR status = 'pending')`. `execution_records.status` is
            # the enum `executionstatus`, whose labels are UPPERCASE only, so PostgreSQL
            # cannot parse the lowercase literal as a value of that type at all — it
            # raises `invalid input value for enum executionstatus: "pending"` before the
            # UPDATE runs. Casting to text makes the comparison work whether the column is
            # an enum or a varchar, which is what the lowercase alternative was reaching
            # for. See TRADE-06: `claim_execution` above still carried the unparseable
            # form, which meant its PostgreSQL branch raised on EVERY call and silently
            # fell through to the ORM path.
            updated_row = self.db.execute(
                text("""
                    UPDATE execution_records
                       SET status = 'EXECUTING',
                           updated_at = CURRENT_TIMESTAMP
                     WHERE execution_id = :execution_id
                       AND tenant_id = :tenant_id
                       AND upper(status::text) = 'PENDING'
                    RETURNING *
                """).bindparams(bindparam("tenant_id", type_=PG_UUID(as_uuid=True))),
                {"execution_id": execution_id, "tenant_id": tenant_id},
            ).fetchone()

            if updated_row is None:
                # Another worker claimed this exact execution first. Commit (nothing was
                # written) so the advisory lock is released promptly.
                self.db.commit()
                return (
                    False,
                    None,
                    "Another worker acquired the execution claim first.",
                )

            self.db.commit()
            record = ExecutionRecordModel(
                execution_id=updated_row.execution_id,
                tenant_id=updated_row.tenant_id,
                task_id=updated_row.task_id,
                strategy_id=updated_row.strategy_id,
                symbol=updated_row.symbol,
                side=updated_row.side,
                size=updated_row.size,
                price=updated_row.price,
                status=ExecutionStatus.EXECUTING,
                order_id=updated_row.order_id,
                result=updated_row.result,
                created_at=updated_row.created_at,
                updated_at=updated_row.updated_at,
            )
            return (True, record, None)

        except Exception as exc:
            # FAILS CLOSED. Unlike `claim_execution`, there is deliberately no
            # dialect-agnostic fallback here: the fallback would be a claim with no
            # capacity check, which is the defect this method exists to close.
            self.db.rollback()
            logger.critical(
                "TRADE-01: capacity reservation for execution %s failed (%s: %s). "
                "Refusing rather than claiming without a capacity check.",
                execution_id, exc.__class__.__name__, exc,
            )
            return (
                False,
                None,
                f"Capacity reservation could not be completed "
                f"({exc.__class__.__name__}). Refused before any exchange call.",
            )

    #: How old an `EXECUTING` row must be before the recovery path will adjudicate it.
    #:
    #: TRADE-07. Deliberately LONGER than `CAPACITY_STALE_AFTER_SECONDS` (300s), and the
    #: ordering matters. At 300s a stuck row stops holding CAPACITY, which is a reversible
    #: accounting decision -- if the worker was merely slow, the only cost is that the
    #: account could briefly oversubscribe. At this threshold the row's STATUS is changed,
    #: which is close to irreversible and which also decides whether a venue
    #: reconciliation is attempted. Adjudicating no earlier than capacity release means a
    #: row is never declared dead while it is still being counted as alive.
    #:
    #: 900s rather than the OrderWatchdog's 60s for the same reason that watchdog cannot
    #: help here: it reconciles rows that already HAVE a venue order id, so a 60s poll is
    #: cheap and safe. This path adjudicates rows with no order id, where the answer may
    #: be "the venue may hold an order we cannot see", and being wrong creates duplicate
    #: exposure. A long threshold costs a stuck row sitting unresolved for longer; a short
    #: one risks adjudicating a live submission.
    RECOVERY_STALE_AFTER_SECONDS = 900

    #: The four outcomes `classify_stale_executions` can return. Named constants rather
    #: than bare strings so a caller cannot branch on a typo and silently take the
    #: resubmit-safe path for an ambiguous row.
    RECOVERY_FAILED_BEFORE_SUBMISSION = "failed_before_submission"
    RECOVERY_AMBIGUOUS_SUBMISSION = "ambiguous_submission"
    RECOVERY_SUBMITTED_WITH_ORDER_ID = "submitted_with_order_id"
    RECOVERY_NOT_STALE = "not_stale"

    def mark_submitted(
        self,
        execution_id: str,
        tenant_id: UUID,
        *,
        exchange_id: Optional[str] = None,
    ) -> bool:
        """Record that this execution is ABOUT TO BE sent to a venue. Returns success.

        TRADE-07. THIS IS THE EVIDENCE THAT DID NOT EXIST.
        --------------------------------------------------
        `submitted_at` is declared in this model and in
        `alembic/versions/d97ffff9c3bb_execution_schema_rebuild.py`, and before Phase 9
        **no code in the entire application ever wrote it**. Measured.

        Its absence is the whole of TRADE-07. The write ordering on the live path was:

            1. INSERT row, status PENDING, order_id NULL
            2. UPDATE status -> EXECUTING                 (the claim)
            3. ... place_order(), which performs NO database write at all ...
            4. UPDATE status -> COMPLETED, order_id = <venue id>

        So a process that died anywhere inside step 3 left a row with `order_id`,
        `submitted_at`, `exchange_id`, `last_exchange_sync` and `result` ALL NULL --
        meaning **nothing distinguished "crashed before `create_order`" from "crashed
        after `create_order` but before its response arrived"**. The first is safe to
        fail; the second may correspond to a real order resting at the venue. With no way
        to tell them apart, every safe action was unavailable:

        * failing the row risks the next signal opening a duplicate position;
        * retrying it risks a second real order;
        * `OrderWatchdog`, which exists to rescue stale rows and IS running, filters on
          `order_id IS NOT NULL` -- so it cannot see this population at all;
        * `check_idempotent_execution` returns `skip_already_running` for EXECUTING with
          no age test, so the row blocked that (tenant, strategy, symbol, bucket, side,
          qty, price) **forever**.

        Writing one timestamp immediately before the venue call splits that undecidable
        state into two decidable ones.

        IT MUST BE ITS OWN TRANSACTION, AND THAT IS NOT INCIDENTAL. The caller supplies a
        session of its own; this method deliberately does not reuse it. The evidence has
        to be COMMITTED before the irreversible venue write, because the failure it
        guards against is the process disappearing. Sharing the caller's transaction
        would mean a later rollback erases the record of a submission that really
        happened -- reintroducing the exact ambiguity at the exact moment it matters.

        `WHERE submitted_at IS NULL` makes it idempotent and makes the FIRST submission
        instant authoritative: a retry of the same execution cannot overwrite the original
        timestamp and make an old submission look recent.
        """
        try:
            dialect = self.db.bind.dialect.name if self.db and self.db.bind else "sqlite"
            if dialect == "postgresql":
                result = self.db.execute(
                    text("""
                        UPDATE execution_records
                           SET submitted_at = CURRENT_TIMESTAMP,
                               exchange_id  = COALESCE(:exchange_id, exchange_id),
                               updated_at   = CURRENT_TIMESTAMP
                         WHERE execution_id = :execution_id
                           AND tenant_id = :tenant_id
                           AND submitted_at IS NULL
                        RETURNING execution_id
                    """).bindparams(
                        bindparam("tenant_id", type_=PG_UUID(as_uuid=True))
                    ),
                    {
                        "execution_id": execution_id,
                        "tenant_id": tenant_id,
                        "exchange_id": exchange_id,
                    },
                ).fetchone()
                self.db.commit()
                # `None` means the row was already marked -- a retry. That is success for
                # this method's purpose: submission evidence exists either way.
                return True

            record = self.get_by_id(execution_id, tenant_id)
            if record is None:
                # No persisted row to stamp. On the production live path this cannot
                # happen -- `check_idempotent_execution` INSERTs the PENDING row before
                # anything calls this -- so a missing row means the caller drove
                # `_execute_trade_internal` directly without persisting one, which the
                # live-order unit tests do as a documented shortcut. The WRITE MECHANISM
                # is healthy; there is simply nothing to update, which is exactly what the
                # PostgreSQL branch above reports (its `UPDATE ... RETURNING` matches zero
                # rows yet commits and returns True). Returning True here keeps the two
                # dialects consistent for the same input; a genuine write FAILURE still
                # raises and is caught below as False. The real precondition -- that the
                # row exists -- is established by the gateway, not re-asserted here.
                return True
            if record.submitted_at is None:
                record.submitted_at = datetime.utcnow()
                if exchange_id and not record.exchange_id:
                    record.exchange_id = exchange_id
                self.db.commit()
            return True
        except Exception as exc:
            self.db.rollback()
            logger.critical(
                "TRADE-07: could not record submission evidence for execution %s "
                "(%s: %s). The caller MUST refuse the order: without this timestamp a "
                "crash during submission is indistinguishable from a crash before it, "
                "and the row becomes unrecoverable.",
                execution_id, exc.__class__.__name__, exc,
            )
            return False

    def classify_stale_executions(
        self,
        tenant_id: Optional[UUID] = None,
        *,
        stale_after_seconds: Optional[int] = None,
        limit: int = 200,
    ) -> list[dict]:
        """Adjudicate `EXECUTING` rows that have outlived the recovery threshold.

        TRADE-07. Returns one dict per stale row with a `classification` drawn from the
        four `RECOVERY_*` constants, and NO state change -- deciding and acting are
        separated so the decision can be inspected, logged and tested without a venue.

        The three live classifications:

        ``failed_before_submission`` (`submitted_at IS NULL`)
            The process died before the venue call. No order can exist, because
            `mark_submitted` is committed BEFORE `create_order` and nothing else writes
            that column. Safe to fail.

        ``ambiguous_submission`` (`submitted_at` set, `order_id IS NULL`)
            The order was sent and the response never came back. The venue MAY hold it.
            This must never be failed and must never be resubmitted on the strength of
            this row alone; it requires reconciliation against the venue using the
            `clientOrderId`, which is the deterministic `execution_id`.

        ``submitted_with_order_id`` (`order_id` set)
            Already in `OrderWatchdog`'s population -- it reconciles by venue order id.
            Returned for completeness so a caller can see the row was considered, and
            deliberately NOT acted on here, so the two recovery paths cannot both move the
            same row.

        Read-only, and ordered oldest-first so a bounded run makes progress on the worst
        rows rather than an arbitrary subset.
        """
        stale = (
            self.RECOVERY_STALE_AFTER_SECONDS
            if stale_after_seconds is None
            else int(stale_after_seconds)
        )
        dialect = self.db.bind.dialect.name if self.db and self.db.bind else "sqlite"
        if dialect != "postgresql":
            # `make_interval` is PostgreSQL-specific and this path adjudicates money
            # state. Returning nothing is the fail-safe: no classification means no
            # transition, so a non-PostgreSQL deployment simply has no recovery rather
            # than a wrong one.
            logger.warning(
                "TRADE-07: stale-execution recovery is PostgreSQL-only; dialect is %s, "
                "so no rows were classified and none will be transitioned.", dialect,
            )
            return []

        params: Dict[str, Any] = {"stale": stale, "limit": int(limit)}
        tenant_clause = ""
        if tenant_id is not None:
            tenant_clause = " AND tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        statement = text(f"""
            SELECT execution_id,
                   tenant_id,
                   symbol,
                   side,
                   size,
                   price,
                   exchange_id,
                   order_id,
                   submitted_at,
                   created_at,
                   EXTRACT(EPOCH FROM (now() - created_at)) AS age_seconds
              FROM execution_records
             WHERE status = 'EXECUTING'
               AND created_at < now() - make_interval(secs => :stale)
               {tenant_clause}
             ORDER BY created_at ASC
             LIMIT :limit
        """)  # nosec B608 - tenant_clause is a fixed literal, not interpolated input
        if tenant_id is not None:
            statement = statement.bindparams(
                bindparam("tenant_id", type_=PG_UUID(as_uuid=True))
            )

        rows = self.db.execute(statement, params).fetchall()

        classified: list[dict] = []
        for row in rows:
            if row.order_id is not None:
                classification = self.RECOVERY_SUBMITTED_WITH_ORDER_ID
            elif row.submitted_at is None:
                classification = self.RECOVERY_FAILED_BEFORE_SUBMISSION
            else:
                classification = self.RECOVERY_AMBIGUOUS_SUBMISSION

            classified.append({
                "execution_id": row.execution_id,
                "tenant_id": row.tenant_id,
                "symbol": row.symbol,
                "side": row.side,
                "size": row.size,
                "price": row.price,
                "exchange_id": row.exchange_id,
                "order_id": row.order_id,
                "submitted_at": row.submitted_at,
                "created_at": row.created_at,
                "age_seconds": float(row.age_seconds or 0),
                "classification": classification,
            })
        return classified

    def resolve_stale_execution(
        self,
        execution_id: str,
        tenant_id: UUID,
        *,
        status: ExecutionStatus,
        classification: str,
        detail: str,
        order_id: Optional[str] = None,
    ) -> bool:
        """Move one adjudicated stale row to a terminal-or-unresolved status.

        TRADE-07. Transitions ONLY from `EXECUTING`, in one statement, so two concurrent
        recovery workers cannot both act on the same row and so a row that a live worker
        has meanwhile completed is left alone. That is what makes the state change
        transactionally consistent with the lifecycle rather than racing it.

        The permitted destinations are deliberately narrow:

        * ``FAILED`` -- only for `failed_before_submission`, where no venue order can
          exist.
        * ``COMPLETED`` -- only when a venue reconciliation produced a real order id.
        * ``UNKNOWN`` -- the safe unresolved state for anything ambiguous. It is a real
          enum label, it releases the idempotency block (so the strategy is not frozen on
          that bucket forever) and it does NOT claim the order succeeded or failed.
          `OrderWatchdog` already uses UNKNOWN for exactly this purpose with the comment
          "DO NOT ASSUME FILLED".

        Anything else is refused rather than written, because the set of states a recovery
        path may assign is a safety property.
        """
        permitted = {
            ExecutionStatus.FAILED,
            ExecutionStatus.COMPLETED,
            ExecutionStatus.UNKNOWN,
        }
        if status not in permitted:
            logger.critical(
                "TRADE-07: refusing to move execution %s to %s from the recovery path. "
                "Recovery may only assign FAILED (no submission happened), COMPLETED "
                "(a venue order was found) or UNKNOWN (unresolved).",
                execution_id, status,
            )
            return False

        if status is ExecutionStatus.COMPLETED and not order_id:
            # A recovery path must not declare an order filled without the venue's own
            # identifier for it. Without this guard "COMPLETED" would be assertable from
            # the absence of information, which is how a phantom fill enters the ledger.
            logger.critical(
                "TRADE-07: refusing to complete execution %s without a venue order id. "
                "A fill is only recordable with the venue's own identifier for it.",
                execution_id,
            )
            return False

        try:
            updated = self.db.execute(
                text("""
                    UPDATE execution_records
                       SET status = :status,
                           venue_order_id = COALESCE(:venue_order_id, venue_order_id),
                           last_exchange_sync = CURRENT_TIMESTAMP,
                           exchange_status = :detail,
                           updated_at = CURRENT_TIMESTAMP
                     WHERE execution_id = :execution_id
                       AND tenant_id = :tenant_id
                       AND upper(status::text) = 'EXECUTING'
                    RETURNING execution_id
                """).bindparams(bindparam("tenant_id", type_=PG_UUID(as_uuid=True))),
                {
                    "status": status.name,
                    # TRADE-12: `venue_order_id`, not `order_id`. What a venue
                    # reconciliation returns is the EXCHANGE's identifier, and `order_id`
                    # is foreign-keyed to this platform's own `orders` table -- so writing
                    # it there refuses the whole statement with 23503 and leaves the row
                    # stuck, which is the state recovery exists to clear.
                    "venue_order_id": order_id,
                    # `exchange_status`, not `exchange_status_message`. The watchdog
                    # assigns the latter at two call sites and it is NOT a column on this
                    # model, so those assignments have always been silently discarded.
                    "detail": f"{classification}: {detail}"[:500],
                    "execution_id": execution_id,
                    "tenant_id": tenant_id,
                },
            ).fetchone()
            self.db.commit()
            if updated is None:
                logger.info(
                    "TRADE-07: execution %s was no longer EXECUTING when recovery tried "
                    "to resolve it; another worker or a live completion got there first. "
                    "Left alone.", execution_id,
                )
                return False
            logger.warning(
                "TRADE-07: recovery moved execution %s to %s (%s: %s).",
                execution_id, status.name, classification, detail,
            )
            return True
        except Exception as exc:
            self.db.rollback()
            logger.critical(
                "TRADE-07: failed to resolve stale execution %s (%s: %s). The row stays "
                "EXECUTING, which is the safe direction: it will be re-adjudicated.",
                execution_id, exc.__class__.__name__, exc,
            )
            return False

    def update_status(
        self,
        execution_id: str,
        tenant_id: UUID,
        status: ExecutionStatus,
        order_id: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        venue_order_id: Optional[str] = None,
    ) -> Optional[ExecutionRecordModel]:
        """Update execution status (common operation).

        TRADE-12. ``venue_order_id`` is the EXCHANGE's identifier and ``order_id`` is this
        platform's. They used to be the same parameter, and the caller on the success path
        passed a venue id -- which `fk_execution_records_order_id` refuses with 23503.
        Because this method commits, that failure also aborted the caller's transaction,
        so the enclosing handler's own `update_status(..., FAILED, ...)` failed too and
        the exception escaped `execute_with_idempotency`, leaving the row EXECUTING with
        `order_id IS NULL` after a real fill. See the column comment on
        `ExecutionRecordModel.order_id`.
        """
        record = self.get_by_id(execution_id, tenant_id)
        if not record:
            return None
        
        record.status = status
        
        if order_id is not None:
            record.order_id = order_id

        if venue_order_id is not None:
            record.venue_order_id = venue_order_id
        
        if result is not None:
            record.result = result
        
        self.db.commit()
        self.db.refresh(record)
        
        return record
    
    def get_stats(self, tenant_id: UUID) -> ExecutionStats:
        """Get execution statistics for tenant."""
        result = self.db.execute(
            text("""
                SELECT 
                    COUNT(*) FILTER (WHERE status = 'PENDING') as pending_count,
                    COUNT(*) FILTER (WHERE status = 'EXECUTING') as executing_count,
                    COUNT(*) FILTER (WHERE status = 'COMPLETED') as completed_count,
                    COUNT(*) FILTER (WHERE status = 'FAILED') as failed_count,
                    COUNT(*) as total_count,
                    MAX(created_at) as last_execution_at
                FROM execution_records
                WHERE tenant_id = :tenant_id
            """).bindparams(
                bindparam("tenant_id", type_=PG_UUID(as_uuid=True))
            ),
            {"tenant_id": tenant_id}
        ).fetchone()
        
        return ExecutionStats(
            tenant_id=tenant_id,
            pending_count=result.pending_count or 0,
            executing_count=result.executing_count or 0,
            completed_count=result.completed_count or 0,
            failed_count=result.failed_count or 0,
            total_count=result.total_count or 0,
            last_execution_at=result.last_execution_at,
        )

    def get_last_execution_at_by_strategy(
        self, tenant_id: UUID
    ) -> Dict[str, Optional[datetime]]:
        """``strategy_id`` -> the tenant's most recent execution for it. One query, always.

        vyomquant-ui-redesign BC-4 (that spec's task 12.4, design.md §7.2, Requirements 4.1,
        19.1, 19.2). ``GET /api/strategies`` publishes ``last_execution_at`` per row and needs
        this map to do it.

        SAME DEFINITION AS :meth:`get_stats`, NOT A SECOND ONE
            ``get_stats`` already answers "when did this tenant last execute" as
            ``MAX(created_at)`` over ``execution_records``. This is that expression with
            ``strategy_id`` added to the projection and a ``GROUP BY`` under it, so the
            per-strategy figure and the tenant-wide figure cannot disagree: the tenant-wide
            one is by construction the maximum of the values returned here. ``created_at`` is
            the execution's own recorded instant, not a clock read at projection time, so
            nothing here can invent a timestamp.

        ONE AGGREGATE, NOT ONE QUERY PER STRATEGY
            The whole map is read in a single grouped statement and the caller indexes into
            it, rather than asking per row. A per-strategy read would put an N+1 on a list
            page that renders every strategy a user owns.

        A STRATEGY WITH NO EXECUTIONS IS SIMPLY ABSENT FROM THE RESULT
            ``GROUP BY`` emits no row for a group with no rows, so the caller reads a missing
            key as ``None``. This deliberately returns no entry rather than an entry set to
            ``None``: "never executed" and "executed at an unknown time" are different
            claims, and only the first one is true here.

        THE RESULT COLUMNS ARE TYPED, UNLIKE :meth:`get_stats`'
            ``.columns(...)`` is declared so ``last_execution_at`` comes back as a ``datetime``
            on every driver. A bare ``text()`` leaves an aggregate untyped, and a driver that
            hands back the stored string then travels all the way onto the wire as one - a
            silent per-deployment change in the published type of a field. ``get_stats`` can
            omit this because its value is consumed by a Pydantic model that coerces; this
            one is projected straight into a JSON response.

        Read-only. Issues no write and opens no transaction of its own beyond the implicit
        read, so it cannot affect order execution or reconciliation.
        """
        statement = text("""
                SELECT
                    strategy_id,
                    MAX(created_at) as last_execution_at
                FROM execution_records
                WHERE tenant_id = :tenant_id
                GROUP BY strategy_id
            """).bindparams(
            bindparam("tenant_id", type_=PG_UUID(as_uuid=True))
        ).columns(
            strategy_id=String,
            last_execution_at=DateTime(timezone=True),
        )

        rows = self.db.execute(statement, {"tenant_id": tenant_id}).fetchall()

        latest: Dict[str, Optional[datetime]] = {}
        for row in rows:
            if row.strategy_id is None or row.last_execution_at is None:
                continue
            latest[str(row.strategy_id)] = row.last_execution_at
        return latest


# Global instance factory
def get_execution_record_repository(db_session: Session) -> ExecutionRecordRepository:
    """Get execution record repository instance."""
    return ExecutionRecordRepository(db_session)
