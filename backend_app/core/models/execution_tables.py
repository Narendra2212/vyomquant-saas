import json
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, JSON, DateTime
from backend_app.core.database_pool import Base

class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    
    idempotency_key = Column(String(128), primary_key=True)
    tenant_id = Column(String(36), primary_key=True)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class TransactionCheckpoint(Base):
    __tablename__ = "transaction_checkpoints"
    
    checkpoint_id = Column(String(36), primary_key=True)
    transaction_id = Column(String(36), index=True)
    tenant_id = Column(String(36), index=True)
    state_snapshot = Column(JSON, nullable=True)
    operations_snapshot = Column(JSON, nullable=True)
    checksum = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class TransactionRecord(Base):
    __tablename__ = "transaction_records"
    
    transaction_id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), index=True)
    state = Column(String(32))
    operations_count = Column(Integer, default=0)
    checksum = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

class TransactionRollback(Base):
    __tablename__ = "transaction_rollbacks"
    
    rollback_id = Column(String(36), primary_key=True)
    transaction_id = Column(String(36), index=True)
    tenant_id = Column(String(36), index=True)
    checkpoint_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Fill(Base):
    __tablename__ = "fills"
    
    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), index=True)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Position(Base):
    __tablename__ = "positions"
    
    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), index=True)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(String(36), primary_key=True)
    tenant_id = Column(String(36), index=True)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
