"""
core/models/reconciliation.py — Reconciliation Mismatch Persistence

Stores every divergence detected between local execution-record state
and exchange-reported order state so that:

  1. Mismatches are never silently dropped.
  2. The escalation scheduler can query and act on unresolved mismatches.
  3. Post-mortems have a full audit trail.

SCHEMA:
  reconciliation_mismatches
  ─────────────────────────
  id                  SERIAL PK
  mismatch_id         VARCHAR(32)  UNIQUE  — deterministic SHA-256 prefix
  tenant_id           UUID        NOT NULL
  execution_id        VARCHAR     NOT NULL
  order_id            VARCHAR     nullable  — exchange order id
  symbol              VARCHAR     NOT NULL
  side                VARCHAR     NOT NULL  — buy | sell
  field               VARCHAR     NOT NULL  — which field diverged (size, side, symbol, status)
  local_value         TEXT        NOT NULL  — value in our DB
  exchange_value      TEXT        NOT NULL  — value reported by exchange
  severity            VARCHAR     NOT NULL  — critical | high | medium
  status              VARCHAR     NOT NULL  — new | escalated | resolved | suppressed
  resolved_at         TIMESTAMP   nullable
  resolved_by         VARCHAR     nullable
  resolution_notes    TEXT        nullable
  escalation_count    INTEGER     NOT NULL DEFAULT 0
  last_escalated_at   TIMESTAMP   nullable
  kill_switch_triggered BOOLEAN   NOT NULL DEFAULT FALSE
  raw_local_state     JSONB       nullable  — full snapshot at detection time
  raw_exchange_state  JSONB       nullable
  detected_at         TIMESTAMP   NOT NULL
  created_at          TIMESTAMP   NOT NULL
  updated_at          TIMESTAMP   NOT NULL
"""

import hashlib
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import JSON, Boolean, Column, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from backend_app.core.database import Base

logger = logging.getLogger("ReconciliationMismatch")


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class MismatchSeverity(str, Enum):
    CRITICAL = "critical"   # size or symbol mismatch — immediate kill switch
    HIGH = "high"           # side mismatch
    MEDIUM = "medium"       # status or fill-price divergence


class MismatchStatus(str, Enum):
    NEW = "new"                 # just detected, not yet acted on
    ESCALATED = "escalated"     # alert sent / kill switch triggered
    RESOLVED = "resolved"       # manually confirmed and resolved
    SUPPRESSED = "suppressed"   # operator determined it's a false positive


# ═══════════════════════════════════════════════════════════════════════════════
# MISMATCH ID GENERATION (deterministic, idempotent inserts)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_mismatch_id(
    execution_id: str,
    field: str,
    detected_at: datetime,
    bucket_seconds: int = 60,
) -> str:
    """
    Deterministic mismatch ID — same (execution_id, field, time-bucket)
    always maps to the same mismatch_id so re-detection inside the same
    minute does not create duplicate rows.
    """
    bucket = (int(detected_at.timestamp()) // bucket_seconds) * bucket_seconds
    canonical = f"{execution_id}:{field}:{bucket}"
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"rmm_{digest[:24]}"


# ═══════════════════════════════════════════════════════════════════════════════
# SQLALCHEMY ORM MODEL
# ═══════════════════════════════════════════════════════════════════════════════

class ReconciliationMismatchModel(Base):
    """
    Persists every detected divergence between local state and exchange state.

    Every row is immutable after creation except for the mutable resolution
    columns: status, resolved_at, resolved_by, resolution_notes,
    escalation_count, last_escalated_at, kill_switch_triggered, updated_at.
    """
    __tablename__ = "reconciliation_mismatches"

    # Surrogate PK (auto-increment — avoids UUID generation latency)
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Deterministic natural key — prevents duplicate rows on re-detection
    mismatch_id = Column(String(32), unique=True, nullable=False, index=True)

    # Tenant isolation
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)

    # Execution context
    execution_id = Column(String, nullable=False, index=True)
    order_id = Column(String, nullable=True, index=True)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)

    # What diverged
    field = Column(String(64), nullable=False)           # e.g. "size", "status"
    local_value = Column(Text, nullable=False)
    exchange_value = Column(Text, nullable=False)

    # Severity / lifecycle
    severity = Column(
        SQLEnum(MismatchSeverity, name="mismatch_severity_enum"),
        nullable=False,
        default=MismatchSeverity.CRITICAL,
    )
    status = Column(
        SQLEnum(MismatchStatus, name="mismatch_status_enum"),
        nullable=False,
        default=MismatchStatus.NEW,
    )

    # Resolution tracking
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(128), nullable=True)
    resolution_notes = Column(Text, nullable=True)

    # Escalation tracking
    escalation_count = Column(Integer, nullable=False, default=0)
    last_escalated_at = Column(DateTime(timezone=True), nullable=True)
    kill_switch_triggered = Column(Boolean, nullable=False, default=False)

    # Full state snapshots at detection time (JSONB for PG, JSON for SQLite)
    raw_local_state = Column(JSON, nullable=True)
    raw_exchange_state = Column(JSON, nullable=True)

    # Timestamps
    detected_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    __table_args__ = (
        # Fast look-up of all open mismatches for a tenant
        Index("idx_rmm_tenant_status", "tenant_id", "status"),
        # Escalation scheduler queries NEW mismatches ordered by detected_at
        Index("idx_rmm_status_detected", "status", "detected_at"),
        # Audit: all mismatches for a given execution
        Index("idx_rmm_execution_id", "execution_id"),
        # Kill-switch post-mortem queries
        Index("idx_rmm_kill_switch", "kill_switch_triggered"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "mismatch_id": self.mismatch_id,
            "tenant_id": str(self.tenant_id),
            "execution_id": self.execution_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "field": self.field,
            "local_value": self.local_value,
            "exchange_value": self.exchange_value,
            "severity": self.severity.value if hasattr(self.severity, "value") else self.severity,
            "status": self.status.value if hasattr(self.status, "value") else self.status,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolved_by": self.resolved_by,
            "resolution_notes": self.resolution_notes,
            "escalation_count": self.escalation_count,
            "last_escalated_at": self.last_escalated_at.isoformat() if self.last_escalated_at else None,
            "kill_switch_triggered": self.kill_switch_triggered,
            "raw_local_state": self.raw_local_state,
            "raw_exchange_state": self.raw_exchange_state,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class ReconciliationMismatchCreate(BaseModel):
    tenant_id: UUID
    execution_id: str
    order_id: Optional[str] = None
    symbol: str
    side: str
    field: str
    local_value: str
    exchange_value: str
    severity: MismatchSeverity = MismatchSeverity.CRITICAL
    raw_local_state: Optional[Dict[str, Any]] = None
    raw_exchange_state: Optional[Dict[str, Any]] = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)


class ReconciliationMismatchUpdate(BaseModel):
    status: Optional[MismatchStatus] = None
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    resolution_notes: Optional[str] = None
    escalation_count: Optional[int] = None
    last_escalated_at: Optional[datetime] = None
    kill_switch_triggered: Optional[bool] = None


# ═══════════════════════════════════════════════════════════════════════════════
# REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════

class ReconciliationMismatchRepository:
    """
    Handles persistence of reconciliation mismatches.

    Uses idempotent upsert via the deterministic mismatch_id so that the
    same detection event from multiple pods never creates duplicate rows.
    """

    def __init__(self, db: Session):
        self.db = db

    # ── Create / upsert ───────────────────────────────────────────────────────

    def record_mismatch(
        self,
        data: ReconciliationMismatchCreate,
    ) -> ReconciliationMismatchModel:
        """
        Idempotently persist a mismatch.

        If a row with the same mismatch_id already exists (same execution, same
        field, same time-bucket) we return the existing row without modification.
        This prevents duplicate rows when multiple pods detect the same event.
        """
        mismatch_id = generate_mismatch_id(
            execution_id=data.execution_id,
            field=data.field,
            detected_at=data.detected_at,
        )

        # Idempotency: check if already persisted
        existing = self.db.query(ReconciliationMismatchModel).filter(
            ReconciliationMismatchModel.mismatch_id == mismatch_id
        ).first()

        if existing:
            logger.debug(f"[ReconciliationRepo] Mismatch already recorded: {mismatch_id}")
            return existing

        record = ReconciliationMismatchModel(
            mismatch_id=mismatch_id,
            tenant_id=data.tenant_id,
            execution_id=data.execution_id,
            order_id=data.order_id,
            symbol=data.symbol.upper(),
            side=data.side.lower(),
            field=data.field,
            local_value=str(data.local_value),
            exchange_value=str(data.exchange_value),
            severity=data.severity,
            status=MismatchStatus.NEW,
            escalation_count=0,
            kill_switch_triggered=False,
            raw_local_state=data.raw_local_state,
            raw_exchange_state=data.raw_exchange_state,
            detected_at=data.detected_at,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)

        logger.warning(
            f"[ReconciliationRepo] NEW MISMATCH RECORDED | "
            f"mismatch_id={mismatch_id} | execution_id={data.execution_id} | "
            f"field={data.field} | local={data.local_value} | "
            f"exchange={data.exchange_value} | severity={data.severity.value}"
        )
        return record

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_by_mismatch_id(self, mismatch_id: str) -> Optional[ReconciliationMismatchModel]:
        return self.db.query(ReconciliationMismatchModel).filter(
            ReconciliationMismatchModel.mismatch_id == mismatch_id
        ).first()

    def list_new(
        self,
        limit: int = 100,
        tenant_id: Optional[UUID] = None,
    ) -> List[ReconciliationMismatchModel]:
        """Return unresolved NEW mismatches ordered by detected_at ASC (oldest first)."""
        q = self.db.query(ReconciliationMismatchModel).filter(
            ReconciliationMismatchModel.status == MismatchStatus.NEW
        )
        if tenant_id:
            q = q.filter(ReconciliationMismatchModel.tenant_id == tenant_id)
        return q.order_by(ReconciliationMismatchModel.detected_at.asc()).limit(limit).all()

    def list_escalated(
        self,
        limit: int = 100,
        tenant_id: Optional[UUID] = None,
    ) -> List[ReconciliationMismatchModel]:
        """Return escalated but unresolved mismatches."""
        q = self.db.query(ReconciliationMismatchModel).filter(
            ReconciliationMismatchModel.status == MismatchStatus.ESCALATED
        )
        if tenant_id:
            q = q.filter(ReconciliationMismatchModel.tenant_id == tenant_id)
        return q.order_by(ReconciliationMismatchModel.detected_at.asc()).limit(limit).all()

    def count_unresolved(self, tenant_id: Optional[UUID] = None) -> int:
        q = self.db.query(ReconciliationMismatchModel).filter(
            ReconciliationMismatchModel.status.in_(
                [MismatchStatus.NEW, MismatchStatus.ESCALATED]
            )
        )
        if tenant_id:
            q = q.filter(ReconciliationMismatchModel.tenant_id == tenant_id)
        return q.count()

    # ── Updates ───────────────────────────────────────────────────────────────

    def mark_escalated(
        self,
        mismatch_id: str,
        kill_switch_triggered: bool = False,
    ) -> Optional[ReconciliationMismatchModel]:
        record = self.get_by_mismatch_id(mismatch_id)
        if not record:
            return None
        record.status = MismatchStatus.ESCALATED
        record.escalation_count = (record.escalation_count or 0) + 1
        record.last_escalated_at = datetime.utcnow()
        record.kill_switch_triggered = kill_switch_triggered
        record.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(record)
        return record

    def mark_resolved(
        self,
        mismatch_id: str,
        resolved_by: str,
        resolution_notes: str,
    ) -> Optional[ReconciliationMismatchModel]:
        record = self.get_by_mismatch_id(mismatch_id)
        if not record:
            return None
        record.status = MismatchStatus.RESOLVED
        record.resolved_at = datetime.utcnow()
        record.resolved_by = resolved_by
        record.resolution_notes = resolution_notes
        record.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(record)
        logger.info(
            f"[ReconciliationRepo] Mismatch RESOLVED | mismatch_id={mismatch_id} | "
            f"resolved_by={resolved_by}"
        )
        return record

    def mark_suppressed(
        self,
        mismatch_id: str,
        suppressed_by: str,
        notes: str,
    ) -> Optional[ReconciliationMismatchModel]:
        record = self.get_by_mismatch_id(mismatch_id)
        if not record:
            return None
        record.status = MismatchStatus.SUPPRESSED
        record.resolved_by = suppressed_by
        record.resolution_notes = notes
        record.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(record)
        return record


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

def get_reconciliation_repository(db: Session) -> ReconciliationMismatchRepository:
    """Factory — matches the pattern used by get_execution_record_repository."""
    return ReconciliationMismatchRepository(db)


__all__ = [
    "ReconciliationMismatchModel",
    "ReconciliationMismatchCreate",
    "ReconciliationMismatchUpdate",
    "ReconciliationMismatchRepository",
    "MismatchSeverity",
    "MismatchStatus",
    "generate_mismatch_id",
    "get_reconciliation_repository",
]
