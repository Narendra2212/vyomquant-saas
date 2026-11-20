"""core/models/trade_history.py -- the model IMPORT-03's broken import was reaching for.

THE DEFECT (audit)
    ``backend/position_engine.py::PositionEngine._record_trade`` has always done::

        from backend_app.core.models.trade_history import TradeCreate, TradeHistoryModel

    and this module never existed. Every call raised ``ModuleNotFoundError``, caught by
    ``_record_trade``'s own ``except Exception: logger.error(...)`` -- so no trade has ever
    been recorded through this path, silently, since the method was written.

WHAT THIS CLOSES, AND WHAT IT DOES NOT
    This restores the missing model so the import succeeds and a call to ``_record_trade``
    actually persists a row (migration 040 gives it a real, append-only table). It does
    NOT mean trade history is being recorded for any live or paper trade today:
    ``PositionEngine`` itself has no caller anywhere in the application (confirmed by
    ``grep -rn "PositionEngine(" backend_app/`` finding only its own file -- the same gap
    RT-16's own ledger entry records for the reconciliation side of this same class). This
    fix makes a currently-dead code path's import correct and its persistence real and
    tested, so whoever eventually wires ``PositionEngine`` into the live order path does
    not inherit a second, silent landmine on top of the first.

``TradeHistoryModel`` mirrors ``core/position_model.PositionModel``'s own conventions
(string PK, ``PG_UUID(as_uuid=True)`` tenant_id, money fields stored as exact decimal
strings never floats) rather than inventing a new style for the same database.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from backend_app.core.database import Base


class TradeHistoryModel(Base):
    """One closed / partial-close / position-flip trade event. Append-only (migration 040)."""

    __tablename__ = "trade_history"

    trade_id = Column(String, primary_key=True)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    strategy_id = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    entry_price = Column(String, nullable=False)
    exit_price = Column(String, nullable=False)
    size = Column(String, nullable=False)
    realized_pnl = Column(String, nullable=False)
    close_reason = Column(String, nullable=False)
    closed_at = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "tenant_id": str(self.tenant_id),
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "size": self.size,
            "realized_pnl": self.realized_pnl,
            "close_reason": self.close_reason,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
        }


class TradeCreate(BaseModel):
    """What ``_record_trade`` already builds (``trade_data.dict()`` feeds the ORM model
    directly) -- field names and types kept exactly as that call site already uses them."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    trade_id: str
    tenant_id: Any
    strategy_id: str
    symbol: str
    side: str
    entry_price: str
    exit_price: str
    size: str
    realized_pnl: str
    close_reason: str
    closed_at: datetime

    @field_validator("trade_id", "strategy_id", "symbol", "side", "close_reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not str(v or "").strip():
            raise ValueError("must not be blank")
        return v

    def dict(self, *args, **kwargs):  # noqa: A003 - _record_trade calls .dict() (Pydantic v1 style)
        return self.model_dump(*args, **kwargs)


__all__ = ["TradeHistoryModel", "TradeCreate"]
