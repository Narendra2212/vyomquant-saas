# Multi-Tenant Architecture

**Date:** May 1, 2026

---

## Overview

Converting the trading platform to a **multi-tenant architecture** with complete user isolation:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MULTI-TENANT PLATFORM                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│   │   User A     │    │   User B     │    │   User C     │                  │
│   │  (Tenant 1)  │    │  (Tenant 2)  │    │  (Tenant 3)  │                  │
│   └──────┬───────┘    └──────┬───────┘    └──────┬───────┘                  │
│          │                    │                    │                          │
│          └────────────────────┼────────────────────┘                          │
│                               │                                              │
│          ┌────────────────────▼────────────────────┐                         │
│          │          TENANT ISOLATION LAYER            │                         │
│          │                                          │                         │
│          │  • Auth Middleware (JWT + Tenant ID)    │                         │
│          │  • Request Routing (user_{user_id}:*)   │                         │
│          │  • Resource Quota Enforcement            │                         │
│          │  • Cross-Tenant Access Prevention         │                         │
│          └────────────────────┬────────────────────┘                         │
│                               │                                              │
│   ┌───────────────────────────┼───────────────────────────┐                  │
│   │                           │                           │                  │
│   ▼                           ▼                           ▼                  │
│ ┌─────────────────────────────────────────────────────────┐                  │
│ │              PER-TENANT RESOURCE ISOLATION              │                  │
│ │                                                         │                  │
│ │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │                  │
│ │  │   DAG Engine │  │   Execution  │  │   Portfolio  │ │                  │
│ │  │   user_A:*   │  │   user_A:*   │  │   user_A:*   │ │                  │
│ │  └──────────────┘  └──────────────┘  └──────────────┘ │                  │
│ │                                                         │                  │
│ │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │                  │
│ │  │    State     │  │   Position   │  │    Risk      │ │                  │
│ │  │   user_A:*   │  │   user_A:*   │  │   user_A:*   │ │                  │
│ │  └──────────────┘  └──────────────┘  └──────────────┘ │                  │
│ │                                                         │                  │
│ └─────────────────────────────────────────────────────────┘                  │
│                               │                                              │
│                               ▼                                              │
│ ┌─────────────────────────────────────────────────────────┐                  │
│ │              STORAGE ISOLATION STRATEGY                  │                  │
│ │                                                         │                  │
│ │  Redis: user:{user_id}:dag:*                          │                  │
│ │         user:{user_id}:state:*                        │                  │
│ │         user:{user_id}:positions:*                     │                  │
│ │                                                         │                  │
│ │  PostgreSQL: Schema per user OR tenant_id column      │                  │
│ │              - dag_executions (tenant_id)            │                  │
│ │              - positions (tenant_id)                 │                  │
│ │              - orders (tenant_id)                      │                  │
│ │                                                         │                  │
│ │  S3: user-{user_id}/backups/*                          │                  │
│ │                                                         │                  │
│ └─────────────────────────────────────────────────────────┘                  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Isolation Strategies

### 1. Namespace Isolation (Redis)

```
Key Pattern: {resource_type}:{user_id}:{entity_id}

Examples:
  dag:user_123:session_abc           → User 123's DAG session
  state:user_123:BTCUSDT:snapshot    → User 123's BTC state
  position:user_123:pos_001          → User 123's position
  portfolio:user_123:allocation      → User 123's portfolio
  execution:user_123:order_001       → User 123's order
  validation:user_123:BTC:report     → User 123's validation report
  
Benefits:
  ✓ Complete key isolation
  ✓ Easy tenant data cleanup (DEL user_123:*)
  ✓ Scan by tenant (SCAN user_123:*)
```

### 2. Schema Isolation (Database)

```python
# Option A: Row-Level Security (RLS) - RECOMMENDED
CREATE TABLE positions (
    id UUID PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,  -- User ID
    symbol VARCHAR(32),
    side VARCHAR(8),
    quantity DECIMAL,
    entry_price DECIMAL,
    created_at TIMESTAMP
);

-- Enable RLS
ALTER TABLE positions ENABLE ROW LEVEL SECURITY;

-- Create policy (applied automatically)
CREATE POLICY tenant_isolation ON positions
    USING (tenant_id = current_setting('app.current_user_id')::VARCHAR);

-- Set tenant context before queries
SET app.current_user_id = 'user_123';
```

```python
# Option B: Schema Per Tenant (for enterprise)
CREATE SCHEMA user_123;
CREATE TABLE user_123.positions (...);
CREATE TABLE user_123.orders (...);
CREATE TABLE user_123.dag_sessions (...);

Benefits:
  Option A: Simpler ops, shared resources, easy migration
  Option B: Complete isolation, easier compliance, per-tenant scaling
```

### 3. API Isolation (FastAPI)

```python
# Middleware extracts tenant from JWT
async def tenant_middleware(request: Request, call_next):
    # Extract user_id from JWT
    token = request.headers.get("Authorization")
    user_id = decode_jwt(token)["sub"]
    
    # Inject into request state
    request.state.user_id = user_id
    request.state.tenant_id = user_id
    
    # Check resource limits
    await enforce_quota(user_id)
    
    return await call_next(request)

# Dependency injection for tenant context
async def get_tenant(request: Request) -> TenantContext:
    return TenantContext(
        user_id=request.state.user_id,
        permissions=request.state.permissions
    )

# Router with automatic tenant scoping
@router.get("/positions")
async def get_positions(tenant: TenantContext = Depends(get_tenant)):
    # Automatically filtered by tenant
    return await portfolio_service.get_positions(tenant.user_id)
```

---

## Resource Limits & Quotas

### Per-User Limits

```python
@dataclass
class TenantQuota:
    """Resource quotas per tenant."""
    # DAG Limits
    max_dag_sessions: int = 5
    max_dag_nodes: int = 50
    max_symbols_per_dag: int = 10
    
    # Execution Limits
    max_orders_per_minute: int = 60
    max_positions: int = 50
    max_daily_trades: int = 100
    
    # Portfolio Limits
    max_capital: float = 100000.0
    max_leverage: float = 3.0
    max_symbol_concentration: float = 0.3  # 30%
    
    # Storage Limits
    max_redis_memory_mb: int = 100
    max_db_storage_gb: float = 1.0
    data_retention_days: int = 90
    
    # Compute Limits
    max_backtest_parallel: int = 3
    max_websocket_connections: int = 5
```

### Quota Enforcement

```python
class QuotaEnforcer:
    """Enforces resource quotas per tenant."""
    
    async def check_dag_session_limit(self, user_id: str) -> bool:
        quota = await self.get_quota(user_id)
        current = await redis.scard(f"user:{user_id}:dag:sessions")
        
        if current >= quota.max_dag_sessions:
            raise QuotaExceededError(
                f"DAG session limit reached: {current}/{quota.max_dag_sessions}"
            )
        return True
    
    async def check_rate_limit(self, user_id: str, action: str) -> bool:
        key = f"rate_limit:user:{user_id}:{action}"
        current = await redis.incr(key)
        
        if current == 1:
            await redis.expire(key, 60)  # 1-minute window
        
        quota = await self.get_quota(user_id)
        limit = getattr(quota, f"max_{action}_per_minute", 60)
        
        if current > limit:
            raise RateLimitExceededError(
                f"Rate limit exceeded: {current}/{limit} per minute"
            )
        return True
    
    async def check_position_limit(self, user_id: str) -> bool:
        quota = await self.get_quota(user_id)
        current = await db.fetchval(
            "SELECT COUNT(*) FROM positions WHERE tenant_id = $1",
            user_id
        )
        
        if current >= quota.max_positions:
            raise QuotaExceededError(
                f"Position limit reached: {current}/{quota.max_positions}"
            )
        return True
```

---

## Security Model

### 1. Authentication & Authorization

```python
class TenantContext:
    """Tenant context for all operations."""
    user_id: str
    tenant_id: str
    permissions: List[str]
    quota: TenantQuota
    
    def can_access_resource(self, resource_owner_id: str) -> bool:
        """Check if tenant can access resource."""
        return self.user_id == resource_owner_id or "admin" in self.permissions

# JWT claims structure
{
    "sub": "user_123",           # User ID (tenant ID)
    "tenant_id": "user_123",      # Tenant identifier
    "plan": "premium",           # Subscription plan
    "permissions": [
        "dag:execute",
        "portfolio:manage",
        "orders:create"
    ],
    "quota": {
        "max_dag_sessions": 10,
        "max_positions": 100
    },
    "iat": 1234567890,
    "exp": 1234571490
}
```

### 2. Cross-Tenant Protection

```python
# Middleware ensures no cross-tenant access
async def cross_tenant_protection(request: Request, call_next):
    tenant = request.state.tenant_id
    
    # Check all resource IDs in request
    resource_ids = extract_resource_ids(request)
    
    for resource_id in resource_ids:
        owner = await get_resource_owner(resource_id)
        if owner != tenant:
            # Attempted cross-tenant access
            await log_security_event(
                "cross_tenant_access_attempt",
                tenant=tenant,
                target_resource=resource_id,
                target_owner=owner
            )
            raise HTTPException(403, "Access denied")
    
    return await call_next(request)
```

### 3. Data Encryption

```python
# Per-tenant encryption keys
class TenantEncryption:
    """Per-tenant data encryption."""
    
    def encrypt_for_tenant(self, user_id: str, data: bytes) -> bytes:
        key = self.get_or_create_key(user_id)
        return encrypt(data, key)
    
    def decrypt_for_tenant(self, user_id: str, encrypted: bytes) -> bytes:
        key = self.get_key(user_id)
        return decrypt(encrypted, key)

# Usage
encrypted_position = tenant_encryption.encrypt_for_tenant(
    user_id,
    json.dumps(position_data).encode()
)
```

---

## Implementation Plan

### Phase 1: Foundation (Week 1-2)

**Tasks:**
1. **Create Tenant Context System**
   - `core/tenant.py` - TenantContext, TenantQuota
   - Middleware for JWT extraction
   - Dependency injection setup

2. **Update Database Schema**
   - Add `tenant_id` to all tables
   - Enable Row-Level Security
   - Create migration scripts

3. **Update Redis Key Patterns**
   - Create key builder utilities
   - Migration script for existing keys
   - Namespace isolation verification

### Phase 2: Service Layer (Week 3-4)

**Tasks:**
1. **Update DAG Engine**
   - Add tenant context to all operations
   - Per-tenant session management
   - Resource quota integration

2. **Update Portfolio Manager**
   - Tenant-scoped positions
   - Per-tenant capital allocation
   - Cross-tenant isolation

3. **Update Execution Engine**
   - Tenant-scoped orders
   - Rate limiting per user
   - Position limits enforcement

4. **Update State Persistence**
   - Namespaced Redis keys
   - Tenant-scoped checkpoints
   - Per-tenant event sourcing

### Phase 3: API Layer (Week 5-6)

**Tasks:**
1. **Update All Routers**
   - Add tenant dependency to all endpoints
   - Verify resource ownership
   - Update response filtering

2. **Add Quota Endpoints**
   - GET /api/tenant/quota - Current usage
   - GET /api/tenant/limits - Quota limits
   - WebSocket for quota alerts

3. **Admin Endpoints**
   - GET /api/admin/tenants - List tenants
   - PUT /api/admin/tenants/{id}/quota - Update quotas
   - GET /api/admin/tenants/{id}/usage - Resource usage

### Phase 4: Testing & Migration (Week 7-8)

**Tasks:**
1. **Isolation Testing**
   - Cross-tenant access attempts
   - Resource limit enforcement
   - Data leakage tests

2. **Performance Testing**
   - Multi-tenant load testing
   - Redis key pattern performance
   - Database RLS overhead

3. **Migration**
   - Data migration scripts
   - Key migration for Redis
   - Zero-downtime deployment

---

## Code Changes Required

### 1. Core Tenant Module

```python
# core/tenant.py
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class TenantContext:
    """Context for all tenant-scoped operations."""
    user_id: str
    tenant_id: str
    email: str
    plan: str
    permissions: List[str]
    quota: "TenantQuota"
    
    def can(self, permission: str) -> bool:
        return permission in self.permissions or "admin" in self.permissions
    
    def owns_resource(self, resource_owner_id: str) -> bool:
        return self.user_id == resource_owner_id

@dataclass
class TenantQuota:
    """Resource limits for tenant."""
    # Add all quota fields
    max_dag_sessions: int = 5
    max_positions: int = 50
    max_capital: float = 100000.0

# Dependency
async def get_tenant(
    request: Request,
    token: str = Depends(oauth2_scheme)
) -> TenantContext:
    """Extract tenant from JWT."""
    payload = decode_jwt(token)
    
    return TenantContext(
        user_id=payload["sub"],
        tenant_id=payload["tenant_id"],
        email=payload.get("email"),
        plan=payload.get("plan", "free"),
        permissions=payload.get("permissions", []),
        quota=TenantQuota(**payload.get("quota", {}))
    )
```

### 2. Updated Services

```python
# backend/dag_engine.py
class TenantDAGEngine:
    """Tenant-isolated DAG execution."""
    
    def __init__(self, tenant: TenantContext):
        self.tenant = tenant
        self.key_prefix = f"user:{tenant.user_id}:dag"
    
    async def create_session(self, dag_config: DAGConfig) -> DAGSession:
        # Check quota
        await quota_enforcer.check_dag_session_limit(self.tenant.user_id)
        
        # Create with tenant prefix
        session_id = f"{self.key_prefix}:session:{uuid.uuid4()}"
        
        # Store in Redis with tenant namespace
        await redis.hset(
            f"{self.key_prefix}:sessions",
            session_id,
            json.dumps(dag_config.to_dict())
        )
        
        return DAGSession(id=session_id, tenant=self.tenant)
```

```python
# backend/portfolio_management.py
class TenantPortfolioManager:
    """Tenant-isolated portfolio management."""
    
    def __init__(self, tenant: TenantContext):
        self.tenant = tenant
        self.key_prefix = f"user:{tenant.user_id}:portfolio"
    
    async def get_positions(self) -> List[Position]:
        # Automatically filtered by tenant
        rows = await db.fetch(
            "SELECT * FROM positions WHERE tenant_id = $1",
            self.tenant.user_id
        )
        return [Position.from_row(r) for r in rows]
    
    async def open_position(self, symbol: str, side: str, qty: float):
        # Check quota
        await quota_enforcer.check_position_limit(self.tenant.user_id)
        
        # Create position
        await db.execute(
            """
            INSERT INTO positions (tenant_id, symbol, side, quantity)
            VALUES ($1, $2, $3, $4)
            """,
            self.tenant.user_id, symbol, side, qty
        )
```

### 3. Updated Routers

```python
# routers/dag.py
router = APIRouter(prefix="/api/dag", tags=["dag"])

@router.post("/sessions")
async def create_dag_session(
    config: DAGConfig,
    tenant: TenantContext = Depends(get_tenant)
):
    """Create tenant-scoped DAG session."""
    engine = TenantDAGEngine(tenant)
    session = await engine.create_session(config)
    return {"session_id": session.id, "tenant": tenant.user_id}

@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    tenant: TenantContext = Depends(get_tenant)
):
    """Get session - verifies tenant ownership."""
    engine = TenantDAGEngine(tenant)
    
    # Verify ownership
    if not session_id.startswith(f"user:{tenant.user_id}:"):
        raise HTTPException(403, "Access denied")
    
    session = await engine.get_session(session_id)
    return session
```

---

## Deployment Architecture

### Kubernetes Multi-Tenancy

```yaml
# Namespace per tenant (optional)
apiVersion: v1
kind: Namespace
metadata:
  name: tenant-user-123
  labels:
    tenant-id: user-123
    plan: premium

---
# Or shared namespace with tenant labels
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dag-engine
  labels:
    app: dag-engine
spec:
  replicas: 3
  template:
    metadata:
      labels:
        app: dag-engine
    spec:
      containers:
      - name: dag
        image: trading-platform:latest
        env:
        - name: TENANT_ISOLATION_MODE
          value: "strict"
        - name: REDIS_KEY_PREFIX
          value: "user:{tenant_id}"
```

### Redis Cluster Setup

```
# Database per tenant (0-15 available)
User A: Redis DB 0
User B: Redis DB 1
...

# Or key prefix approach (more common)
Redis DB 0:
  user_123:dag:session_abc
  user_123:state:BTCUSDT
  user_456:dag:session_xyz
  user_456:state:ETHUSDT
```

---

## Monitoring & Observability

### Per-Tenant Metrics

```python
# Prometheus metrics with tenant labels
dag_sessions_active = Gauge(
    "dag_sessions_active",
    "Active DAG sessions",
    ["tenant_id"]
)

positions_total = Gauge(
    "positions_total",
    "Total positions",
    ["tenant_id"]
)

quota_usage = Gauge(
    "quota_usage_percent",
    "Quota usage percentage",
    ["tenant_id", "resource_type"]
)

# Usage
dag_sessions_active.labels(tenant_id=user_id).set(session_count)
```

### Tenant Activity Logs

```json
{
  "timestamp": "2026-05-01T10:30:00Z",
  "tenant_id": "user_123",
  "event_type": "dag_session_created",
  "resource_id": "user_123:dag:session_abc",
  "quota_before": {"dag_sessions": 2},
  "quota_after": {"dag_sessions": 3},
  "ip_address": "192.168.1.100",
  "user_agent": "..."
}
```

---

## Summary

### Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Redis Keys** | Prefix with `user:{id}` | Simple, fast, easy cleanup |
| **Database** | Row-Level Security | Shared resources, easier ops |
| **Auth** | JWT with tenant claims | Stateless, scalable |
| **Quotas** | Enforced at service layer | Flexible, testable |
| **Encryption** | Per-tenant keys | Compliance, security |

### Implementation Phases

| Phase | Duration | Focus |
|-------|----------|-------|
| 1 | 2 weeks | Foundation, context system |
| 2 | 2 weeks | Service layer updates |
| 3 | 2 weeks | API layer, admin tools |
| 4 | 2 weeks | Testing, migration |

**Total: 8 weeks for full multi-tenant conversion**

---

## Next Steps

1. **Create `core/tenant.py`** - Tenant context foundation
2. **Update database migrations** - Add tenant_id columns
3. **Implement Redis key builder** - Consistent namespacing
4. **Update all services** - Inject tenant context
5. **Add quota enforcement** - Resource limits
6. **Security audit** - Cross-tenant access testing
