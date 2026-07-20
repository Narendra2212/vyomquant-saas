# Hard Quota Enforcement Integration Guide

**Date:** May 1, 2026

---

## Overview

Hard quota enforcement has been added at the **service layer** to ensure limits are respected even when middleware is bypassed.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    HARD QUOTA ENFORCEMENT ARCHITECTURE                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Layer 1: Middleware (Soft Check)                                           │
│  ├── Quick validation for early rejection                                   │
│ └── Reduces unnecessary processing                                          │
│                                                                              │
│                    ↓ (bypass possible via internal calls)                   │
│                                                                              │
│  Layer 2: Service Layer (HARD ENFORCEMENT) ⭐                               │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │                                                                      │    │
│  │   DAG Engine          Execution Engine        Portfolio Manager     │    │
│  │   ┌──────────────┐    ┌──────────────┐        ┌──────────────┐      │    │
│  │   │ 1. Check     │    │ 1. Check     │        │ 1. Check     │      │    │
│  │   │    Sessions  │    │    Rate      │        │    Positions │      │    │
│  │   │ 2. Check     │    │ 2. Check     │        │ 2. Check     │      │    │
│  │   │    Nodes     │    │    Daily     │        │    Capital   │      │    │
│  │   │ 3. Check     │    │    Trades    │        │ 3. Check     │      │    │
│  │   │    Symbols   │    │ 3. Check     │        │    Leverage  │      │    │
│  │   │ 4. Check     │    │    Active    │        │              │      │    │
│  │   │    Indicators│    │    Orders    │        │              │      │    │
│  │   └──────────────┘    └──────────────┘        └──────────────┘      │    │
│  │                                                                      │    │
│  │   ALL throw structured errors                                        │    │
│  │   ALL log violations to audit log                                    │    │
│  │   ALL work independently of middleware                               │    │
│  │                                                                      │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Integration Examples

### 1. DAG Engine Integration

```python
# backend/dag_engine.py

from core.hard_quota_enforcer import hard_quota_enforcer
from core.quota_errors import DAGQuotaExceededError
from core.tenant import TenantContext
from core.tenant_middleware import EnforcementContext

class TenantDAGEngine:
    """DAG engine with hard quota enforcement."""
    
    def __init__(self, tenant: TenantContext):
        self.tenant = tenant
        self.enforcer = hard_quota_enforcer
    
    async def create_session(self, dag_config: DAGConfig, request_id: str = None):
        """Create DAG session with hard quota enforcement."""
        
        ctx = EnforcementContext(
            tenant=self.tenant,
            operation="create_dag_session",
            request_id=request_id
        )
        
        # HARD ENFORCEMENT: Check session limit
        await self.enforcer.enforce_dag_session_limit(self.tenant, ctx)
        
        # HARD ENFORCEMENT: Check node count
        await self.enforcer.enforce_dag_node_limit(
            self.tenant, 
            len(dag_config.nodes),
            ctx
        )
        
        # HARD ENFORCEMENT: Check symbol count
        await self.enforcer.enforce_symbol_limit(
            self.tenant,
            len(dag_config.symbols),
            ctx
        )
        
        # HARD ENFORCEMENT: Check indicator count
        indicator_count = sum(
            1 for node in dag_config.nodes 
            if node.type == NodeType.INDICATOR
        )
        await self.enforcer.enforce_indicator_limit(
            self.tenant,
            indicator_count,
            ctx
        )
        
        # Create session
        session = await self._create_session_internal(dag_config)
        
        # Track in Redis for quota counting
        await redis_manager.sadd(
            TenantKeyBuilder.dag_sessions_set(self.tenant.user_id),
            session.id
        )
        
        return session
    
    async def run_backtest(self, dag_config: DAGConfig, request_id: str = None):
        """Run backtest with hard quota enforcement."""
        
        ctx = EnforcementContext(
            tenant=self.tenant,
            operation="run_backtest",
            request_id=request_id
        )
        
        # HARD ENFORCEMENT: Check concurrent backtest limit
        await self.enforcer.enforce_backtest_parallel_limit(self.tenant, ctx)
        
        # Track concurrent operation
        async with self.enforcer.track_concurrent_operation(
            self.tenant, "backtest", str(uuid.uuid4()), ctx
        ):
            # Run backtest
            result = await self._run_backtest_internal(dag_config)
            
        return result
```

### 2. Execution Engine Integration

```python
# backend/execution_engine_production.py

from core.hard_quota_enforcer import hard_quota_enforcer
from core.quota_errors import ExecutionQuotaExceededError, RateLimitExceededError
from core.tenant import TenantContext
from core.tenant_middleware import EnforcementContext

class TenantProductionExecutionEngine:
    """Execution engine with hard quota enforcement."""
    
    def __init__(self, tenant: TenantContext):
        self.tenant = tenant
        self.enforcer = hard_quota_enforcer
    
    async def create_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        request_id: str = None
    ) -> Order:
        """Create order with hard quota enforcement."""
        
        ctx = EnforcementContext(
            tenant=self.tenant,
            operation="create_order",
            request_id=request_id
        )
        
        # HARD ENFORCEMENT: Check rate limit (orders per minute)
        await self.enforcer.enforce_order_rate_limit(self.tenant, ctx)
        
        # HARD ENFORCEMENT: Check daily trade limit
        await self.enforcer.enforce_daily_trade_limit(self.tenant, ctx)
        
        # HARD ENFORCEMENT: Check active order limit
        await self.enforcer.enforce_active_order_limit(self.tenant, ctx)
        
        # Create order
        order = await self._create_order_internal(symbol, side, quantity)
        
        # Track for quota monitoring
        await self.enforcer.track_order_creation(
            self.tenant, order.id, ctx
        )
        
        return order
    
    async def batch_create_orders(
        self,
        orders: List[OrderRequest],
        request_id: str = None
    ) -> List[Order]:
        """Batch create orders with quota enforcement."""
        
        ctx = EnforcementContext(
            tenant=self.tenant,
            operation="batch_create_orders",
            request_id=request_id
        )
        
        # HARD ENFORCEMENT: Check if batch would exceed rate limit
        current = int(await redis_manager.get(
            TenantKeyBuilder.rate_limit(self.tenant.user_id, "orders", "1m")
        ) or 0)
        
        if current + len(orders) > self.tenant.quota.max_orders_per_minute:
            raise RateLimitExceededError(
                details=QuotaViolationDetails(...),
                window="1m",
                retry_after=60
            )
        
        # Create orders
        results = []
        for order_req in orders:
            order = await self.create_order(
                order_req.symbol,
                order_req.side,
                order_req.quantity,
                request_id
            )
            results.append(order)
        
        return results
```

### 3. Portfolio Manager Integration

```python
# backend/portfolio_management.py

from core.hard_quota_enforcer import hard_quota_enforcer
from core.quota_errors import (
    PortfolioQuotaExceededError,
    CapitalQuotaExceededError
)
from core.tenant import TenantContext
from core.tenant_middleware import EnforcementContext

class TenantPortfolioManager:
    """Portfolio manager with hard quota enforcement."""
    
    def __init__(self, tenant: TenantContext):
        self.tenant = tenant
        self.enforcer = hard_quota_enforcer
    
    async def open_position(
        self,
        symbol: str,
        side: PositionSide,
        quantity: float,
        price: float,
        request_id: str = None
    ) -> Position:
        """Open position with hard quota enforcement."""
        
        ctx = EnforcementContext(
            tenant=self.tenant,
            operation="open_position",
            request_id=request_id
        )
        
        # HARD ENFORCEMENT: Check position limit
        await self.enforcer.enforce_position_limit(self.tenant, ctx)
        
        # HARD ENFORCEMENT: Check capital allocation limit
        position_value = quantity * price
        await self.enforcer.enforce_capital_limit(
            self.tenant,
            position_value,
            ctx
        )
        
        # HARD ENFORCEMENT: Check leverage limit
        # Get current leverage
        current_leverage = await self._calculate_current_leverage()
        await self.enforcer.enforce_leverage_limit(
            self.tenant,
            current_leverage,
            ctx
        )
        
        # HARD ENFORCEMENT: Check concentration limit
        portfolio_value = await self._get_portfolio_value()
        await self.enforcer.enforce_concentration_limit(
            self.tenant,
            symbol,
            position_value,
            portfolio_value,
            ctx
        )
        
        # Open position
        position = await self._open_position_internal(symbol, side, quantity, price)
        
        # Update capital tracking
        await self.enforcer.update_allocated_capital(
            self.tenant,
            position_value
        )
        
        # Track in Redis
        await redis_manager.sadd(
            TenantKeyBuilder.positions_set(self.tenant.user_id),
            position.id
        )
        
        return position
    
    async def allocate_capital(
        self,
        strategy_id: str,
        amount: float,
        request_id: str = None
    ):
        """Allocate capital with hard quota enforcement."""
        
        ctx = EnforcementContext(
            tenant=self.tenant,
            operation="allocate_capital",
            request_id=request_id
        )
        
        # HARD ENFORCEMENT: Check capital limit
        await self.enforcer.enforce_capital_limit(self.tenant, amount, ctx)
        
        # Perform allocation
        await self._allocate_capital_internal(strategy_id, amount)
        
        # Update tracking
        await self.enforcer.update_allocated_capital(self.tenant, amount)
    
    async def close_position(
        self,
        position_id: str,
        request_id: str = None
    ):
        """Close position and update capital tracking."""
        
        position = await self._get_position(position_id)
        position_value = position.quantity * position.current_price
        
        # Close position
        result = await self._close_position_internal(position_id)
        
        # Update capital tracking (release capital)
        await self.enforcer.update_allocated_capital(
            self.tenant,
            -position_value  # Negative = release
        )
        
        # Remove from tracking
        await redis_manager.srem(
            TenantKeyBuilder.positions_set(self.tenant.user_id),
            position_id
        )
        
        return result
```

---

## Structured Error Response

### Error Format

```json
{
  "error": "DAG_QUOTA_EXCEEDED",
  "message": "DAG quota exceeded: dag_sessions (limit: 3, current: 3, attempted: 4)",
  "details": {
    "tenant_id": "user_123",
    "resource_type": "dag_sessions",
    "limit": 3,
    "current": 3,
    "attempted": 4,
    "plan": "basic",
    "timestamp": "2026-05-01T12:30:00",
    "request_id": "req_abc123",
    "context": {
      "current_sessions": 3,
      "max_allowed": 3
    }
  },
  "timestamp": "2026-05-01T12:30:00"
}
```

### Error Types by Engine

| Engine | Error Type | Code |
|--------|-----------|------|
| **DAG** | `DAGQuotaExceededError` | `DAG_QUOTA_EXCEEDED` |
| **DAG** | `ConcurrentOperationQuotaError` | `CONCURRENT_OPERATION_LIMIT` |
| **Execution** | `ExecutionQuotaExceededError` | `EXECUTION_QUOTA_EXCEEDED` |
| **Execution** | `RateLimitExceededError` | `RATE_LIMIT_EXCEEDED` |
| **Execution** | `DailyLimitExceededError` | `DAILY_LIMIT_EXCEEDED` |
| **Portfolio** | `PortfolioQuotaExceededError` | `PORTFOLIO_QUOTA_EXCEEDED` |
| **Portfolio** | `CapitalQuotaExceededError` | `CAPITAL_QUOTA_EXCEEDED` |

---

## Violation Logging

### Audit Log Structure

```json
{
  "type": "quota_violation",
  "error_type": "DAGQuotaExceededError",
  "error_code": "DAG_QUOTA_EXCEEDED",
  "message": "DAG quota exceeded: dag_sessions (limit: 3, current: 3, attempted: 4)",
  "tenant_id": "user_123",
  "operation": "create_dag_session",
  "request_id": "req_abc123",
  "timestamp": "2026-05-01T12:30:00",
  "details": {
    "current_sessions": 3,
    "max_allowed": 3
  },
  "severity": "WARNING"
}
```

### Log Destinations

1. **Application Logger** (`logger.warning()`)
   - Standard logging for monitoring
   
2. **Audit Channel** (Redis)
   - Key: `audit:quota_violations:{tenant_id}`
   - TTL: 30 days
   - Structure: LPUSH (newest first)

3. **Metrics** (Prometheus)
   - Counter: `quota_violations_total{tenant_id, error_type}`
   - Gauge: `quota_usage_percent{tenant_id, resource_type}`

---

## Testing Hard Enforcement

### Unit Test Example

```python
import pytest
from unittest.mock import AsyncMock, patch

from core.hard_quota_enforcer import HardQuotaEnforcer
from core.quota_errors import DAGQuotaExceededError
from core.tenant import TenantContext, TenantPlan

class TestHardQuotaEnforcement:
    """Test that hard enforcement works even if middleware bypassed."""
    
    @pytest.fixture
    def free_tenant(self):
        """Create free tier tenant (limits: 1 DAG session)."""
        return TenantContext(
            user_id="user_free",
            tenant_id="user_free",
            email="free@example.com",
            plan=TenantPlan.FREE,
            permissions=["dag:execute"]
        )
    
    @pytest.fixture
    def enforcer(self):
        return HardQuotaEnforcer()
    
    @pytest.mark.asyncio
    async def test_dag_session_enforced_directly(self, enforcer, free_tenant):
        """Test that DAG session limit is enforced at service layer."""
        
        from core.tenant_middleware import EnforcementContext
        
        ctx = EnforcementContext(
            tenant=free_tenant,
            operation="test",
            request_id="test-123"
        )
        
        # Simulate tenant already at limit
        with patch.object(redis_manager, 'scard', return_value=1):
            # Should raise even without middleware check
            with pytest.raises(DAGQuotaExceededError) as exc_info:
                await enforcer.enforce_dag_session_limit(free_tenant, ctx)
            
            error = exc_info.value
            assert error.error_code == "DAG_QUOTA_EXCEEDED"
            assert error.details.limit == 1
            assert error.details.current == 1
    
    @pytest.mark.asyncio
    async def test_violation_logged(self, enforcer, free_tenant):
        """Test that violations are logged to audit."""
        
        ctx = EnforcementContext(
            tenant=free_tenant,
            operation="test",
            request_id="test-123"
        )
        
        with patch.object(redis_manager, 'scard', return_value=1):
            with patch.object(redis_manager, 'lpush') as mock_log:
                try:
                    await enforcer.enforce_dag_session_limit(free_tenant, ctx)
                except DAGQuotaExceededError:
                    pass
                
                # Verify violation was logged
                mock_log.assert_called_once()
                log_data = mock_log.call_args[0][1]
                assert log_data["type"] == "quota_violation"
                assert log_data["tenant_id"] == "user_free"
```

### Integration Test Example

```python
@pytest.mark.asyncio
async def test_internal_call_bypasses_middleware(self):
    """
    Verify that hard enforcement works even when calling 
    service methods directly (bypassing middleware).
    """
    
    # Create tenant at limit
    tenant = TenantContext(
        user_id="user_at_limit",
        tenant_id="user_at_limit",
        plan=TenantPlan.FREE,
    )
    
    # Pre-populate at limit
    await redis_manager.sadd(
        TenantKeyBuilder.dag_sessions_set(tenant.user_id),
        "session_1"
    )
    
    # Create engine (no middleware involved)
    engine = TenantDAGEngine(tenant)
    
    # Try to create session directly
    with pytest.raises(DAGQuotaExceededError):
        await engine.create_session(
            DAGConfig(nodes=[...]),
            request_id="direct-call"
        )
    
    # Verify it was blocked at service layer
    # (not by middleware)
```

---

## Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `core/quota_errors.py` | ~200 | Structured error classes |
| `core/hard_quota_enforcer.py` | ~500 | Hard enforcement implementation |
| `HARD_QUOTA_INTEGRATION.md` | ~400 | Integration guide (this file) |

---

## Summary

### ✅ Hard Enforcement Features

- ✅ **Service Layer Checks**: Enforced in DAG, Execution, Portfolio engines
- ✅ **Middleware Independence**: Works even when middleware bypassed
- ✅ **Structured Errors**: Complete error context for debugging
- ✅ **Audit Logging**: All violations logged with full context
- ✅ **15+ Quota Types**: Sessions, positions, rate limits, capital, etc.
- ✅ **Concurrent Tracking**: Context managers for operation tracking

### Quota Enforcement Matrix

| Resource | DAG Engine | Execution | Portfolio | Method |
|----------|-----------|-----------|-----------|--------|
| Sessions | ✅ | ❌ | ❌ | `enforce_dag_session_limit` |
| Nodes | ✅ | ❌ | ❌ | `enforce_dag_node_limit` |
| Symbols | ✅ | ❌ | ❌ | `enforce_symbol_limit` |
| Indicators | ✅ | ❌ | ❌ | `enforce_indicator_limit` |
| Backtests | ✅ | ❌ | ❌ | `enforce_backtest_parallel_limit` |
| Order Rate | ❌ | ✅ | ❌ | `enforce_order_rate_limit` |
| Daily Trades | ❌ | ✅ | ❌ | `enforce_daily_trade_limit` |
| Active Orders | ❌ | ✅ | ❌ | `enforce_active_order_limit` |
| Positions | ❌ | ❌ | ✅ | `enforce_position_limit` |
| Capital | ❌ | ❌ | ✅ | `enforce_capital_limit` |
| Leverage | ❌ | ❌ | ✅ | `enforce_leverage_limit` |
| Concentration | ❌ | ❌ | ✅ | `enforce_concentration_limit` |

**Status: Hard quota enforcement infrastructure complete**
