# 🔴 CRITICAL FIXES REQUIRED - IMMEDIATE ACTION

**DO NOT DEPLOY TO PRODUCTION WITHOUT THESE FIXES**

---

## FIX 1: tenant_id Variable Order (CRITICAL - C1)

**File:** `routers/orders.py`  
**Problem:** `tenant_id` is used before it's defined, causing `NameError` on every order execution attempt.

### Current (BROKEN):
```python
@router.post("/execute", response_model=OrderExecutionResponse)
async def execute_order(...):
    # ... safety checks ...
    
    # STEP 7.11: ExecutionGuard validation
    try:
        # Get Redis client
        redis_client = redis.Redis.from_url("redis://localhost:6379", decode_responses=False)
        
        # Build signal for validation
        signal = {...}
        
        # Run ExecutionGuard validation
        guard = ExecutionGuard(redis_client)
        validation_report = await guard.validate_trade(
            tenant_id=str(tenant_id),  # ❌ tenant_id NOT DEFINED YET!
            signal=signal,
            ...
        )
    
    # ... later in function ...
    tenant_id = UUID(user["id"])  # ❌ Defined too late!
```

### Fixed:
```python
@router.post("/execute", response_model=OrderExecutionResponse)
async def execute_order(...):
    # 🚫 SYSTEM FREEZE CHECK - Block all execution
    safety_check = SafetyMonitor.check_execution_allowed("manual_order")
    if safety_check:
        ...
    
    # ✅ DEFINE tenant_id IMMEDIATELY after user check
    tenant_id = UUID(user["id"])
    strategy_id = "manual_order"
    
    # Check risk limits
    if risk:
        allowed, reason = risk.check_limits(user["id"], body.symbol, abs(body.quantity))
        ...
    
    # STEP 7.11: ExecutionGuard validation
    try:
        redis_client = redis.Redis.from_url("redis://localhost:6379", decode_responses=False)
        
        signal = {
            "symbol": body.symbol.upper(),
            "side": "buy" if body.quantity > 0 else "sell",
            "size": str(abs(body.quantity)),
            "price": str(body.price) if hasattr(body, 'price') and body.price else "0",
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        guard = ExecutionGuard(redis_client)
        validation_report = await guard.validate_trade(
            tenant_id=str(tenant_id),  # ✅ NOW DEFINED
            signal=signal,
            portfolio_state={...},  # Fetch real portfolio data here!
            market_state={...}
        )
```

---

## FIX 2: Replace Deprecated OrderEngine (CRITICAL - C2)

**Files:** `routers/orders.py`, `routers/portfolio.py`  
**Problem:** Using deprecated `OrderEngine` that bypasses safety checks and has no idempotency.

### Current (BROKEN) in `routers/orders.py`:
```python
from backend.order_execution_engine import OrderEngine  # DEPRECATED!

async def _build_order_engine(user_id: str, vault, exchange_id: str = "binance"):
    keys = vault.load_decrypted_keys(user_id, exchange_id)
    exchange = await get_or_create_exchange(...)
    return OrderEngine(exchange), exchange  # ❌ UNSAFE
```

### Fixed:
```python
from core.unified_execution_engine import UnifiedExecutionEngine  # ✅ SAFE

async def _build_execution_engine(user_id: str, vault, exchange_id: str = "binance"):
    keys = vault.load_decrypted_keys(user_id, exchange_id)
    exchange = await get_or_create_exchange(...)
    engine = UnifiedExecutionEngine(exchange)  # ✅ SAFE
    return engine, exchange

# Update all usages:
# order_engine, _ = await _build_order_engine(...)  # ❌ OLD
engine, _ = await _build_execution_engine(...)  # ✅ NEW
```

---

## FIX 3: Add Idempotency to Stop-Loss/Take-Profit (CRITICAL - C5)

**File:** `routers/orders.py`  
**Problem:** Stop-loss and take-profit endpoints bypass ALL safety checks.

### Current (BROKEN):
```python
@router.post("/stop-loss")
async def place_stop_loss(body: StopLossRequest, ...):
    order_engine, _ = await _build_order_engine(user["id"], vault, exchange_id)
    return await order_engine.place_stop_loss(...)  # ❌ NO SAFETY CHECKS!

@router.post("/take-profit")
async def place_take_profit(body: TakeProfitRequest, ...):
    order_engine, _ = await _build_order_engine(user["id"], vault, exchange_id)
    return await order_engine.place_take_profit(...)  # ❌ NO SAFETY CHECKS!
```

### Fixed:
```python
@router.post("/stop-loss")
async def place_stop_loss(
    body: StopLossRequest,
    exchange_id: str = Query("binance"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
    risk=Depends(get_risk),
):
    # ✅ Add safety checks
    safety_check = SafetyMonitor.check_execution_allowed("stop_loss")
    if safety_check:
        raise HTTPException(status_code=503, detail=safety_check)
    
    # ✅ Add ExecutionGuard validation
    tenant_id = UUID(user["id"])
    engine, exchange = await _build_execution_engine(user["id"], vault, exchange_id)
    
    # Execute with idempotency
    execution_id = idempotency_key or generate_execution_id(...)
    result = await engine.execute_conditional_order_with_idempotency(
        tenant_id=tenant_id,
        order_type="stop_loss",
        symbol=body.symbol,
        side=body.side.value,
        amount=body.amount,
        trigger_price=body.stop_price,
        execution_id=execution_id
    )
    return result
```

---

## FIX 4: Use Shared Redis Connection (CRITICAL - C4)

**File:** `routers/orders.py`  
**Problem:** Creating new Redis client on every request.

### Current (BROKEN):
```python
try:
    redis_client = redis.Redis.from_url("redis://localhost:6379", decode_responses=False)
    guard = ExecutionGuard(redis_client)
    validation_report = await guard.validate_trade(...)
    ...
finally:
    await redis_client.close()  # ❌ Per-request overhead
```

### Fixed:
```python
from core.cache import redis_manager  # ✅ Shared pool

@router.post("/execute", response_model=OrderExecutionResponse)
async def execute_order(...):
    # ... 
    try:
        # ✅ Use shared connection pool
        redis_client = redis_manager.get_client()
        guard = ExecutionGuard(redis_client)
        validation_report = await guard.validate_trade(
            tenant_id=str(tenant_id),
            signal=signal,
            portfolio_state=portfolio_state,
            market_state=market_state
        )
        ...
    finally:
        # ✅ No close needed - connection pool managed
        pass
```

---

## FIX 5: Use Real Portfolio Data (HIGH - H8)

**File:** `routers/orders.py`  
**Problem:** ExecutionGuard uses hardcoded $1M balance instead of actual user balance.

### Current (BROKEN):
```python
portfolio_state = {
    "available_balance": "1000000",  # ❌ HARDCODED!
    "total_equity": "1000000",
    "total_exposure": "0",
    "positions": {},
    "daily_pnl": "0",
}
```

### Fixed:
```python
from backend.portfolio_management import PortfolioManager

async def get_portfolio_state(user_id: str, exchange_id: str) -> dict:
    """Fetch real portfolio state for execution validation."""
    portfolio_mgr = PortfolioManager()
    portfolio = await portfolio_mgr.get_portfolio(user_id, exchange_id)
    
    return {
        "available_balance": str(portfolio.available_balance),
        "total_equity": str(portfolio.total_equity),
        "total_exposure": str(portfolio.total_exposure),
        "positions": {
            p.symbol: {
                "size": str(p.size),
                "entry_price": str(p.entry_price),
                "unrealized_pnl": str(p.unrealized_pnl)
            }
            for p in portfolio.positions
        },
        "daily_pnl": str(portfolio.daily_pnl),
    }

# In execute_order():
portfolio_state = await get_portfolio_state(user["id"], exchange_id)
```

---

## FIX 6: Rename Conflicting SafetyMonitor Classes (HIGH - H1)

**Files:** `core/safety_config.py`, `core/safety_monitor.py`  
**Problem:** Two different `SafetyMonitor` classes with same name.

### Current (BROKEN):
```python
# core/safety_config.py
class SafetyMonitor:
    @staticmethod
    def check_execution_allowed(operation: str) -> Optional[str]: ...

# core/safety_monitor.py
class SafetyMonitor:
    def log_blocked_execution(...): ...
```

### Fixed:
```python
# core/safety_config.py - KEEP THIS ONE (execution blocking)
class SafetyMonitor:
    @staticmethod
    def check_execution_allowed(operation: str) -> Optional[str]: ...

# core/safety_monitor.py - RENAME THIS ONE
class ExecutionAuditor:  # ✅ RENAMED
    """Audits execution attempts for compliance and debugging."""
    def log_blocked_execution(...): ...
    def log_enabled_execution(...): ...

# Update all imports:
# from core.safety_monitor import safety_monitor  # ❌ OLD
from core.safety_monitor import execution_auditor  # ✅ NEW
```

---

## FIX 7: Add DEV_MODE Production Block (HIGH - H2)

**File:** `core/dependencies.py`  
**Problem:** DEV_MODE can bypass authentication if accidentally enabled.

### Current (BROKEN):
```python
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() == "true" or \
           os.environ.get("ENV", "").lower() == "development"

# No protection against production use
```

### Fixed:
```python
import os

# Detect environment
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() == "true" or \
           os.environ.get("ENV", "").lower() == "development"

# ✅ BLOCK DEV_MODE in production
PRODUCTION_HOSTS = ["algo22.io", "app.algo22.io", "api.algo22.io"]
CURRENT_HOST = os.environ.get("HOSTNAME", "")
IS_PRODUCTION_HOST = any(h in CURRENT_HOST for h in PRODUCTION_HOSTS)

if DEV_MODE and IS_PRODUCTION_HOST:
    raise RuntimeError(
        "🚫 CRITICAL: DEV_MODE is enabled on production host! "
        "This would bypass authentication. Aborting startup."
    )

# ✅ Require explicit production confirmation
if not DEV_MODE:
    prod_confirm = os.environ.get("AERORA_PRODUCTION_CONFIRM", "false")
    if prod_confirm.lower() != "true":
        raise RuntimeError(
            "Production mode requires AERORA_PRODUCTION_CONFIRM=true"
        )
```

---

## FIX 8: Use Parameterized SQL Queries (CRITICAL - C3)

**File:** `routers/portfolio.py`  
**Problem:** String concatenation in SQL queries (SQL injection risk).

### Current (BROKEN):
```python
query = (
    "SELECT * FROM executions WHERE user_id = '"
    + safe_uid
    + "'"
    + sym_clause
    + " ORDER BY timestamp DESC LIMIT "
    + str(int(limit))
    + ";"
)
result = await telemetry.execute_query(query)
```

### Fixed:
```python
# Use parameterized queries (QuestDB supports ? placeholders)
query = "SELECT * FROM executions WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?"
result = await telemetry.execute_query(query, [safe_uid, int(limit)])

# Or use SQL builder with proper escaping
from sqlalchemy import text

query = text("""
    SELECT * FROM executions 
    WHERE user_id = :user_id 
    ORDER BY timestamp DESC 
    LIMIT :limit
""")
result = await telemetry.execute_query(query, {"user_id": safe_uid, "limit": int(limit)})
```

---

## TESTING CHECKLIST

After implementing fixes, verify:

- [ ] Order execution with valid idempotency key succeeds
- [ ] Duplicate order with same idempotency key returns cached result
- [ ] Stop-loss order requires idempotency key
- [ ] Take-profit order requires idempotency key
- [ ] System freeze blocks all execution paths
- [ ] Redis failure triggers circuit breaker
- [ ] Invalid symbol returns 400 error
- [ ] SQL injection attempt is blocked
- [ ] DEV_MODE cannot start on production host
- [ ] Real portfolio balance used for validation

---

## DEPLOYMENT SEQUENCE

1. **Phase 1:** Fix C1, C4, C5 (immediate - fixes crashes)
2. **Phase 2:** Fix C2, C3, H8 (replace deprecated components)
3. **Phase 3:** Fix H1, H2 (cleanup and hardening)
4. **Phase 4:** Full integration testing
5. **Phase 5:** Security penetration testing
6. **Phase 6:** Staged rollout (1% → 10% → 100%)

**DO NOT enable `LIVE_TRADING_ENABLED` until all phases complete successfully.**
