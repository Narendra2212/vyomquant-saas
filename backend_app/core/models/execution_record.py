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
    order_id = Column(String, nullable=True, index=True)  # Exchange order ID
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
                elif existing.status == ExecutionStatus.PENDING:
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
        dialect = self.db.bind.dialect.name if self.db and self.db.bind else "sqlite"
        if dialect == "postgresql":
            try:
                result = self.db.execute(
                    text("""
                        UPDATE execution_records
                        SET status = 'executing',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE execution_id = :execution_id
                        AND tenant_id = :tenant_id
                        AND (status = 'PENDING' OR status = 'pending')
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
    
    def update_status(
        self,
        execution_id: str,
        tenant_id: UUID,
        status: ExecutionStatus,
        order_id: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None
    ) -> Optional[ExecutionRecordModel]:
        """Update execution status (common operation)."""
        record = self.get_by_id(execution_id, tenant_id)
        if not record:
            return None
        
        record.status = status
        
        if order_id is not None:
            record.order_id = order_id
        
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


# Global instance factory
def get_execution_record_repository(db_session: Session) -> ExecutionRecordRepository:
    """Get execution record repository instance."""
    return ExecutionRecordRepository(db_session)
