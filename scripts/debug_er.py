import sys
sys.path.insert(0, '.')

print("Step 1: import dependencies")
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple, List
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, Column, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Index, String, bindparam, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session
print("Step 2: import Base")
from backend_app.core.database import Base
print("--- AFTER import Base ---")
print("Step 3: import metrics")
try:
    from backend_app.core.metrics import execution_metrics
    print("Step 3.1: metrics imported successfully")
except BaseException as e:
    import traceback
    print("Step 3 FAILED with exception:", type(e), e)
    traceback.print_exc()

print("Step 4: define ExecutionStatus enum")
class ExecutionStatus(str, Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"

print("Step 5: define ExecutionRecordModel")
class ExecutionRecordModel(Base):
    __tablename__ = "execution_records"
    execution_id = Column(String, primary_key=True)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    task_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)
    strategy_id = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    size = Column(String, nullable=False)
    price = Column(String, nullable=True)
    status = Column(SQLEnum(ExecutionStatus), nullable=False, default=ExecutionStatus.PENDING)
    order_id = Column(String, nullable=True, index=True)
    filled_size = Column(String, nullable=True)
    avg_price = Column(String, nullable=True)
    remaining_size = Column(String, nullable=True)
    exchange_id = Column(String, nullable=True)
    last_exchange_sync = Column(DateTime(timezone=True), nullable=True)
    exchange_status = Column(String, nullable=True)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    filled_at = Column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        Index('idx_execution_records_tenant_symbol', 'tenant_id', 'symbol'),
        Index('idx_execution_records_task_id', 'task_id'),
        Index('idx_execution_records_status', 'status'),
        Index('idx_execution_records_tenant_status', 'tenant_id', 'status'),
        Index('idx_execution_records_created_at', 'created_at'),
        Index('idx_execution_records_order_id', 'order_id'),
        Index('idx_execution_records_exchange_sync', 'last_exchange_sync'),
    )

print("Step 6: ExecutionRecordModel defined successfully!")
