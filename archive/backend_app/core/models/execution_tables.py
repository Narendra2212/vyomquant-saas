from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Integer, String, Uuid

from backend_app.core.database_pool import Base

#: ``tenant_id`` IS A UUID, AND SAYING SO IN ONE PLACE IS THE POINT.
#:
#: These columns were ``String(36)`` while ``profiles.id`` -- the identity they are meant
#: to reference -- is ``uuid``, and PostgreSQL has no equality operator between the two.
#: That is why five of the seven constraints in
#: ``alembic/versions/add_foreign_keys_20260817.py`` could not be created, and therefore
#: why tenant ownership on orders, positions, fills and dag_tasks had no referential
#: integrity at all: a row could name a profile that never existed.
#:
#: ``sa.Uuid`` rather than ``postgresql.UUID`` because the 11,000-test lane runs on
#: SQLite, where the PostgreSQL-specific type does not render. The generic type becomes a
#: native ``uuid`` on PostgreSQL and ``CHAR(32)`` on SQLite.
#:
#: ``as_uuid=False`` rather than the default, and this is the part that must not be
#: "tidied up" later: it keeps the Python-side value a plain ``str``, so every existing
#: comparison still works. ``routers/dag_tasks.py:149`` reads
#: ``task.tenant_id != tenant["id"]`` with no ``str()`` on either side -- with
#: ``as_uuid=True`` that compares a ``uuid.UUID`` to a ``str``, never matches, and the
#: endpoint answers 403 to the task's rightful owner. Verified on both dialects that a
#: hyphenated string round-trips byte-identical.
TenantId = Uuid(as_uuid=False)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    
    idempotency_key = Column(String(128), primary_key=True)
    tenant_id = Column(TenantId, primary_key=True)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class TransactionCheckpoint(Base):
    __tablename__ = "transaction_checkpoints"
    
    checkpoint_id = Column(String(36), primary_key=True)
    transaction_id = Column(String(36), index=True)
    tenant_id = Column(TenantId, index=True)
    state_snapshot = Column(JSON, nullable=True)
    operations_snapshot = Column(JSON, nullable=True)
    checksum = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class TransactionRecord(Base):
    __tablename__ = "transaction_records"
    
    transaction_id = Column(String(36), primary_key=True)
    tenant_id = Column(TenantId, index=True)
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
    tenant_id = Column(TenantId, index=True)
    checkpoint_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Fill(Base):
    __tablename__ = "fills"
    
    id = Column(String(36), primary_key=True)
    tenant_id = Column(TenantId, index=True)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Position(Base):
    __tablename__ = "positions"
    
    id = Column(String(36), primary_key=True)
    tenant_id = Column(TenantId, index=True)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Order(Base):
    __tablename__ = "orders"
    
    id = Column(String(36), primary_key=True)
    tenant_id = Column(TenantId, index=True)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
