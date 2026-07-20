# Database Multi-Tenant Migration Guide

**Date:** May 1, 2026

---

## Migration Strategy

We'll use **Row-Level Security (RLS)** with `tenant_id` columns for PostgreSQL. This provides:
- ✅ Data isolation at database level
- ✅ Minimal schema changes
- ✅ Shared resources (efficient)
- ✅ Easy to implement and audit

---

## Migration Scripts

### Step 1: Add Tenant Columns

```sql
-- Migration: Add tenant_id to all tables
-- Run this first

BEGIN;

-- Add tenant_id column to all existing tables
ALTER TABLE positions ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'legacy';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'legacy';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'legacy';
ALTER TABLE portfolios ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'legacy';
ALTER TABLE strategies ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'legacy';
ALTER TABLE backtests ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'legacy';
ALTER TABLE dag_sessions ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'legacy';
ALTER TABLE risk_limits ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'legacy';
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64) NOT NULL DEFAULT 'legacy';

-- Add indexes for performance
CREATE INDEX CONCURRENTLY idx_positions_tenant_id ON positions(tenant_id);
CREATE INDEX CONCURRENTLY idx_orders_tenant_id ON orders(tenant_id);
CREATE INDEX CONCURRENTLY idx_trades_tenant_id ON trades(tenant_id);
CREATE INDEX CONCURRENTLY idx_portfolios_tenant_id ON portfolios(tenant_id);
CREATE INDEX CONCURRENTLY idx_strategies_tenant_id ON strategies(tenant_id);
CREATE INDEX CONCURRENTLY idx_backtests_tenant_id ON backtests(tenant_id);
CREATE INDEX CONCURRENTLY idx_dag_sessions_tenant_id ON dag_sessions(tenant_id);
CREATE INDEX CONCURRENTLY idx_risk_limits_tenant_id ON risk_limits(tenant_id);
CREATE INDEX CONCURRENTLY idx_notifications_tenant_id ON notifications(tenant_id);

COMMIT;
```

### Step 2: Enable Row-Level Security

```sql
-- Enable RLS on all tables
ALTER TABLE positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolios ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategies ENABLE ROW LEVEL SECURITY;
ALTER TABLE backtests ENABLE ROW LEVEL SECURITY;
ALTER TABLE dag_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

-- Create RLS policies for tenant isolation
CREATE POLICY tenant_isolation_positions ON positions
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));

CREATE POLICY tenant_isolation_orders ON orders
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));

CREATE POLICY tenant_isolation_trades ON trades
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));

CREATE POLICY tenant_isolation_portfolios ON portfolios
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));

CREATE POLICY tenant_isolation_strategies ON strategies
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));

CREATE POLICY tenant_isolation_backtests ON backtests
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));

CREATE POLICY tenant_isolation_dag_sessions ON dag_sessions
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));

CREATE POLICY tenant_isolation_risk_limits ON risk_limits
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));

CREATE POLICY tenant_isolation_notifications ON notifications
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));

-- Allow users to only see their own data
ALTER POLICY tenant_isolation_positions ON positions
    TO PUBLIC
    USING (tenant_id = current_setting('app.current_tenant_id', TRUE));
```

### Step 3: Create Database Functions

```sql
-- Function to set tenant context
CREATE OR REPLACE FUNCTION set_tenant_context(tenant_id VARCHAR(64))
RETURNS VOID AS $$
BEGIN
    PERFORM set_config('app.current_tenant_id', tenant_id, FALSE);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to get current tenant
CREATE OR REPLACE FUNCTION get_tenant_context()
RETURNS VARCHAR(64) AS $$
BEGIN
    RETURN current_setting('app.current_tenant_id', TRUE);
END;
$$ LANGUAGE plpgsql;

-- Audit trigger function
CREATE OR REPLACE FUNCTION audit_tenant_access()
RETURNS TRIGGER AS $$
BEGIN
    -- Log any cross-tenant access attempts
    IF NEW.tenant_id != current_setting('app.current_tenant_id', TRUE) THEN
        INSERT INTO security_audit_log (
            event_type,
            tenant_id,
            table_name,
            operation,
            attempted_by,
            timestamp
        ) VALUES (
            'cross_tenant_access_attempt',
            NEW.tenant_id,
            TG_TABLE_NAME,
            TG_OP,
            current_setting('app.current_tenant_id', TRUE),
            NOW()
        );
        
        RAISE EXCEPTION 'Cross-tenant access denied: % tried to access % resource',
            current_setting('app.current_tenant_id', TRUE),
            NEW.tenant_id;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create audit log table
CREATE TABLE IF NOT EXISTS security_audit_log (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(64) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    table_name VARCHAR(64) NOT NULL,
    operation VARCHAR(16) NOT NULL,
    attempted_by VARCHAR(64) NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    details JSONB
);

CREATE INDEX idx_security_audit_tenant ON security_audit_log(tenant_id);
CREATE INDEX idx_security_audit_timestamp ON security_audit_log(timestamp);
```

### Step 4: Update Python Models

```python
# core/database_models.py - Add tenant_id to all models

from sqlalchemy import Column, String, DateTime, Float, Integer, JSON, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid

Base = declarative_base()

class TenantMixin:
    """Mixin to add tenant_id to all models."""
    
    tenant_id = Column(String(64), nullable=False, index=True)
    
    @classmethod
    def for_tenant(cls, tenant_id: str):
        """Filter query by tenant."""
        return cls.query.filter(cls.tenant_id == tenant_id)

class Position(Base, TenantMixin):
    __tablename__ = 'positions'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)  # From mixin
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)
    quantity = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    current_price = Column(Float)
    unrealized_pnl = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': str(self.id),
            'tenant_id': self.tenant_id,
            'symbol': self.symbol,
            'side': self.side,
            'quantity': self.quantity,
            'entry_price': self.entry_price,
        }

class Order(Base, TenantMixin):
    __tablename__ = 'orders'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)
    type = Column(String(16), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float)
    status = Column(String(16), default='pending')
    created_at = Column(DateTime, default=datetime.utcnow)
    
class Trade(Base, TenantMixin):
    __tablename__ = 'trades'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    order_id = Column(UUID(as_uuid=True), nullable=False)
    symbol = Column(String(32), nullable=False)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    realized_pnl = Column(Float)
    executed_at = Column(DateTime, default=datetime.utcnow)

class DAGSession(Base, TenantMixin):
    __tablename__ = 'dag_sessions'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    session_name = Column(String(128))
    status = Column(String(16), default='active')
    config = Column(JSONB)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_activity = Column(DateTime)

class Strategy(Base, TenantMixin):
    __tablename__ = 'strategies'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    type = Column(String(32), nullable=False)
    config = Column(JSONB)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class RiskLimit(Base, TenantMixin):
    __tablename__ = 'risk_limits'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    strategy_id = Column(String(64))
    max_position_size = Column(Float)
    max_daily_trades = Column(Integer)
    max_drawdown_pct = Column(Float)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
```

### Step 5: Database Session with Tenant Context

```python
# core/database_session.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextvars import ContextVar
from fastapi import Depends

from core.tenant import TenantContext

# Context variable for current tenant
tenant_context: ContextVar[str] = ContextVar('tenant_id', default=None)

class TenantSession:
    """Database session with automatic tenant context."""
    
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
    
    def get_session(self, tenant_id: str) -> Session:
        """Get database session with tenant context set."""
        session = self.SessionLocal()
        
        # Set tenant context for RLS
        session.execute(
            "SELECT set_tenant_context(:tenant_id)",
            {"tenant_id": tenant_id}
        )
        
        return session
    
    async def get_tenant_session(
        self,
        tenant: TenantContext = Depends(get_tenant)
    ) -> Session:
        """FastAPI dependency for tenant-scoped session."""
        session = self.get_session(tenant.user_id)
        try:
            yield session
        finally:
            session.close()

# Global instance
db_session = TenantSession(DATABASE_URL)
```

---

## Application Code Changes

### Repository Pattern with Tenant Isolation

```python
# repositories/base.py

from sqlalchemy.orm import Session
from core.tenant import TenantContext

class TenantRepository:
    """Base repository with tenant isolation."""
    
    def __init__(self, session: Session, tenant: TenantContext):
        self.session = session
        self.tenant = tenant
    
    def _tenant_query(self, model):
        """Filter query by tenant."""
        return self.session.query(model).filter(
            model.tenant_id == self.tenant.user_id
        )
    
    def _set_tenant(self, entity):
        """Set tenant_id on entity before save."""
        entity.tenant_id = self.tenant.user_id
        return entity

class PositionRepository(TenantRepository):
    """Position repository with tenant isolation."""
    
    def get_all(self):
        """Get all positions for tenant."""
        return self._tenant_query(Position).all()
    
    def get_by_id(self, position_id: str):
        """Get position by ID (tenant-scoped)."""
        return self._tenant_query(Position).filter(
            Position.id == position_id
        ).first()
    
    def create(self, position_data: dict):
        """Create position for tenant."""
        position = Position(**position_data)
        self._set_tenant(position)
        self.session.add(position)
        self.session.commit()
        return position
    
    def delete(self, position_id: str):
        """Delete position (verifies ownership)."""
        position = self.get_by_id(position_id)
        if not position:
            raise NotFoundError(f"Position {position_id} not found")
        
        self.session.delete(position)
        self.session.commit()
```

### Updated Routers

```python
# routers/positions.py

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.tenant import TenantContext, get_tenant
from core.database_session import db_session
from repositories.positions import PositionRepository

router = APIRouter(prefix="/api/positions", tags=["positions"])

@router.get("/")
async def get_positions(
    tenant: TenantContext = Depends(get_tenant),
    session: Session = Depends(db_session.get_tenant_session)
):
    """Get all positions for tenant (automatically filtered)."""
    repo = PositionRepository(session, tenant)
    positions = repo.get_all()
    return {"positions": [p.to_dict() for p in positions]}

@router.get("/{position_id}")
async def get_position(
    position_id: str,
    tenant: TenantContext = Depends(get_tenant),
    session: Session = Depends(db_session.get_tenant_session)
):
    """Get position by ID (verifies tenant ownership)."""
    repo = PositionRepository(session, tenant)
    position = repo.get_by_id(position_id)
    
    if not position:
        raise HTTPException(404, "Position not found")
    
    return position.to_dict()
```

---

## Testing Tenant Isolation

### Test Cases

```python
# tests/test_tenant_isolation.py

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from main import app
from core.tenant import TenantContext
from core.database_session import db_session

class TestTenantIsolation:
    """Test complete tenant isolation."""
    
    def test_cannot_access_other_tenant_data(self, client: TestClient):
        """Verify cross-tenant access is blocked."""
        
        # User A creates position
        token_a = create_test_token("user_a")
        response = client.post(
            "/api/positions",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"symbol": "BTCUSDT", "side": "long", "quantity": 1.0}
        )
        assert response.status_code == 201
        position_id = response.json()["id"]
        
        # User B tries to access User A's position
        token_b = create_test_token("user_b")
        response = client.get(
            f"/api/positions/{position_id}",
            headers={"Authorization": f"Bearer {token_b}"}
        )
        assert response.status_code == 404  # Not found (filtered)
        
    def test_database_rls_enforcement(self, db_session):
        """Verify database RLS policies work."""
        
        from core.database_session import db_session
        
        # Set tenant context to user_a
        session = db_session.get_session("user_a")
        
        # User A should see their positions
        positions = session.execute(
            "SELECT * FROM positions"
        ).fetchall()
        assert len(positions) > 0
        
        # Switch to user_b context
        session = db_session.get_session("user_b")
        
        # User B should see empty result for same query
        positions = session.execute(
            "SELECT * FROM positions"
        ).fetchall()
        assert len(positions) == 0
        
    def test_redis_key_isolation(self):
        """Verify Redis key namespace isolation."""
        
        from core.tenant import TenantKeyBuilder
        
        key_a = TenantKeyBuilder.position("user_a", "pos_001")
        key_b = TenantKeyBuilder.position("user_b", "pos_001")
        
        # Same position ID, different users
        assert key_a != key_b
        assert key_a == "user:user_a:position:pos_001"
        assert key_b == "user:user_b:position:pos_001"
        
    def test_quota_enforcement(self, client: TestClient):
        """Verify quota limits are enforced per tenant."""
        
        token = create_test_token("user_free_plan", plan="free")
        
        # Free plan: max 3 positions
        for i in range(3):
            response = client.post(
                "/api/positions",
                headers={"Authorization": f"Bearer {token}"},
                json={"symbol": f"BTC{i}", "side": "long", "quantity": 1.0}
            )
            assert response.status_code == 201
        
        # 4th position should fail (quota exceeded)
        response = client.post(
            "/api/positions",
            headers={"Authorization": f"Bearer {token}"},
            json={"symbol": "ETH", "side": "long", "quantity": 1.0}
        )
        assert response.status_code == 429
        assert "quota" in response.json()["detail"].lower()
```

---

## Rollback Plan

If issues arise during migration:

```sql
-- Emergency rollback: Disable RLS
ALTER TABLE positions DISABLE ROW LEVEL SECURITY;
ALTER TABLE orders DISABLE ROW LEVEL SECURITY;
-- ... etc

-- Remove tenant columns (if needed)
ALTER TABLE positions DROP COLUMN tenant_id;
-- ... etc
```

---

## Post-Migration Checklist

- [ ] All tables have `tenant_id` column
- [ ] All indexes created
- [ ] RLS policies enabled on all tables
- [ ] Test cross-tenant access (should fail)
- [ ] Verify Redis key patterns
- [ ] Check quota enforcement
- [ ] Audit log capturing violations
- [ ] Performance benchmarks passed
- [ ] Backup/restore procedures updated
