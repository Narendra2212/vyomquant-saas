# RLS Hardening Audit

**Principal Institutional Execution Consistency Engineer**

**Audit ID:** RLSHA-1716200000  
**Date:** 2026-05-20  
**Platform:** ALGO22 Quantitative Trading Platform  
**Objective:** Comprehensive RLS audit for tenant isolation hardening

---

## Executive Summary

This audit evaluates the Row-Level Security (RLS) implementation across all database tables for tenant isolation. The audit identifies critical gaps in RLS enforcement, tenant propagation, and service role safety guards.

**Overall RLS Status:** ⚠️ PARTIALLY IMPLEMENTED (60/100)

**Critical Findings:**
- Not all tables have RLS enabled
- RLS policies use user-level instead of tenant-level isolation
- No tenant_id column propagation verification
- No JWT claim propagation verification
- No service role safety guards
- No tenant context validation in execution flows

---

## 1. Database Tables Audit

### 1.1 profiles Table

**File:** `backend/distributed_execution/rls_policies_migration.sql`

**RLS Status:** ✅ ENABLED

**Policies:**
```sql
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own profile"
ON profiles
FOR SELECT
USING (auth.uid()::text = id::text);

CREATE POLICY "Users can update own profile"
ON profiles
FOR UPDATE
USING (auth.uid()::text = id::text);

CREATE POLICY "Service role can read all profiles"
ON profiles
FOR SELECT
TO service_role
USING (true);

CREATE POLICY "Service role can update all profiles"
ON profiles
FOR UPDATE
TO service_role
USING (true);
```

**Tenant Isolation:** ⚠️ USER-LEVEL ONLY

**Issues:**
- Policies use `auth.uid()` for user-level isolation
- No tenant-level isolation policy
- tenant_id column added but not used in policies

**Gap Score:** 50/100

### 1.2 execution_records Table

**File:** `migrations/002_create_execution_records.sql`

**RLS Status:** ✅ ENABLED

**Policies:**
```sql
ALTER TABLE execution_records ENABLE ROW LEVEL SECURITY;

CREATE POLICY execution_records_tenant_isolation_policy ON execution_records
    FOR ALL
    USING (tenant_id::TEXT = current_setting('app.current_tenant_id', true));
```

**Tenant Isolation:** ✅ TENANT-LEVEL

**Issues:**
- Policy uses `current_setting('app.current_tenant_id')` but no verification that this is set
- No service role policy
- No insert/update/delete specific policies

**Gap Score:** 75/100

### 1.3 dag_tasks Table

**File:** `migrations/001_create_dag_tasks_table.sql`

**RLS Status:** ✅ ENABLED

**Policies:**
```sql
ALTER TABLE dag_tasks ENABLE ROW LEVEL SECURITY;

CREATE POLICY dag_tasks_tenant_isolation ON dag_tasks
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY dag_tasks_tenant_insert ON dag_tasks
    FOR INSERT 
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY dag_tasks_tenant_update ON dag_tasks
    FOR UPDATE
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY dag_tasks_tenant_delete ON dag_tasks
    FOR DELETE
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

**Tenant Isolation:** ✅ TENANT-LEVEL

**Issues:**
- Policy uses `current_setting('app.current_tenant_id')` but no verification that this is set
- No service role policy
- No fallback if setting is not set

**Gap Score:** 75/100

### 1.4 strategies Table

**File:** `backend/distributed_execution/rls_policies_migration.sql`

**RLS Status:** ❌ COMMENTED OUT

**Policies:**
```sql
-- ALTER TABLE strategies ENABLE ROW LEVEL SECURITY;

-- CREATE POLICY "Users can read own strategies"
-- ON strategies
-- FOR SELECT
-- USING (auth.uid()::text = user_id::text);
```

**Tenant Isolation:** ❌ NOT IMPLEMENTED

**Issues:**
- RLS not enabled
- Policies commented out
- No tenant-level isolation

**Gap Score:** 0/100

### 1.5 orders Table

**File:** `backend/distributed_execution/rls_policies_migration.sql`

**RLS Status:** ❌ COMMENTED OUT

**Policies:**
```sql
-- ALTER TABLE orders ENABLE ROW LEVEL SECURITY;

-- CREATE POLICY "Users can read own orders"
-- ON orders
-- FOR SELECT
-- USING (auth.uid()::text = user_id::text);
```

**Tenant Isolation:** ❌ NOT IMPLEMENTED

**Issues:**
- RLS not enabled
- Policies commented out
- No tenant-level isolation

**Gap Score:** 0/100

### 1.6 positions Table

**File:** `backend/distributed_execution/rls_policies_migration.sql`

**RLS Status:** ❌ COMMENTED OUT

**Policies:**
```sql
-- ALTER TABLE positions ENABLE ROW LEVEL SECURITY;

-- CREATE POLICY "Users can read own positions"
-- ON positions
-- FOR SELECT
-- USING (auth.uid()::text = user_id::text);
```

**Tenant Isolation:** ❌ NOT IMPLEMENTED

**Issues:**
- RLS not enabled
- Policies commented out
- No tenant-level isolation

**Gap Score:** 0/100

### 1.7 exchange_credentials Table

**File:** `backend/distributed_execution/rls_policies_migration.sql`

**RLS Status:** ❌ COMMENTED OUT

**Policies:**
```sql
-- ALTER TABLE exchange_credentials ENABLE ROW LEVEL SECURITY;

-- CREATE POLICY "Users can read their own credentials"
-- ON exchange_credentials
-- FOR SELECT
-- USING (auth.uid()::text = user_id::text);
```

**Tenant Isolation:** ❌ NOT IMPLEMENTED

**Issues:**
- RLS not enabled
- Policies commented out
- No tenant-level isolation

**Gap Score:** 0/100

---

## 2. Tenant Propagation Audit

### 2.1 JWT Claim Propagation

**File:** `core/dependencies.py`

**Current Implementation:**
```python
async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    supabase=Depends(get_supabase),
) -> dict:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Bearer token required.",
        )
    
    token = credentials.credentials
    
    try:
        response = supabase.auth.get_user(credentials.credentials)
        if not response or not response.user:
            raise ValueError("Invalid token")
        return response.user.__dict__
    except Exception as e:
        logger.warning(f"Auth failure: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please sign in again.",
        )
```

**Issues:**
- JWT decoded but tenant_id claim not extracted
- No tenant_id propagation to database context
- No verification of tenant_id in JWT

**Gap Score:** 25/100

### 2.2 WebSocket Tenant Context

**File:** `core/websocket_auth.py`

**Current Implementation:**
```python
async def _validate_token(self, token: str, claimed_user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated"
        )
        
        user_id = payload.get("sub")
        email = payload.get("email", "")
        
        if not user_id:
            logger.warning("[WS/Auth] Invalid token: missing sub claim")
            return None
        
        # Verify user ID matches claimed user ID (if provided)
        if claimed_user_id and user_id != claimed_user_id:
            logger.warning(f"[WS/Auth] User ID mismatch: claimed {claimed_user_id}, token {user_id}")
            return None
        
        return {
            "id": user_id,
            "email": email,
            "payload": payload
        }
```

**Issues:**
- tenant_id not extracted from JWT payload
- No tenant context validation
- No tenant isolation on WebSocket connections

**Gap Score:** 25/100

### 2.3 Replay Tenant Context

**File:** `backend/state_persistence.py`

**Current Implementation:**
```python
@dataclass
class StateSnapshot:
    session_id: str
    symbol: str
    timestamp: datetime
    checkpoint_type: CheckpointType
    # ... no tenant_id field
```

**Issues:**
- No tenant_id field in StateSnapshot
- No tenant isolation in state persistence
- No tenant validation on state restoration

**Gap Score:** 0/100

### 2.4 Execution Tenant Context

**File:** `core/models/execution_record.py`

**Current Implementation:**
```python
class ExecutionRecordModel(Base):
    __tablename__ = "execution_records"
    
    execution_id = Column(String, primary_key=True)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    # ...
```

**Issues:**
- tenant_id column exists in model
- No tenant context propagation from JWT
- No tenant validation in repository methods

**Gap Score:** 50/100

---

## 3. Service Role Safety Audit

### 3.1 Service Role Usage

**File:** `core/dependencies.py`

**Current Implementation:**
```python
def get_supabase():
    global _supabase_client
    if _supabase_client is None:
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        
        if not supabase_url or not supabase_key:
            logger.error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY not set")
            raise RuntimeError("Supabase credentials required. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY environment variables.")
        
        try:
            from supabase import create_client, Client as SupabaseClient
            _supabase_client = create_client(supabase_url, supabase_key)
            logger.info("Supabase client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to create Supabase client: {e}")
            raise RuntimeError(f"Failed to initialize Supabase: {e}")
    
    return _supabase_client
```

**Issues:**
- Service role key used for all operations
- No service role safety guards
- No audit logging for service role usage
- No tenant context enforcement for service role

**Gap Score:** 25/100

---

## 4. Critical Safety Violations

### 4.1 Tenant Isolation Violation

**Violation:** RLS not enabled on critical tables (strategies, orders, positions, exchange_credentials)

**Impact:** CRITICAL - Tenant data could be accessed across tenants

**Evidence:**
- strategies table: RLS commented out
- orders table: RLS commented out
- positions table: RLS commented out
- exchange_credentials table: RLS commented out

**Remediation:** Enable RLS on all tables with tenant-level policies

### 4.2 JWT Claim Propagation Violation

**Violation:** tenant_id not extracted from JWT or propagated to database context

**Impact:** CRITICAL - No tenant context in database operations

**Evidence:**
- get_current_user does not extract tenant_id
- No tenant_id propagation to app.current_tenant_id
- No tenant validation in database operations

**Remediation:** Extract tenant_id from JWT and propagate to database context

### 4.3 Service Role Safety Violation

**Violation:** Service role used without safety guards

**Impact:** HIGH - Service role could bypass RLS policies

**Evidence:**
- Service role key used for all operations
- No service role usage validation
- No audit logging for service role operations

**Remediation:** Implement service role safety guards and audit logging

### 4.4 WebSocket Tenant Context Violation

**Violation:** No tenant isolation on WebSocket connections

**Impact:** HIGH - WebSocket connections could access cross-tenant data

**Evidence:**
- WebSocket auth does not validate tenant_id
- No tenant context in WebSocket connections
- No tenant isolation in WebSocket message handling

**Remediation:** Implement tenant validation in WebSocket auth

---

## 5. Safety Recommendations

### 5.1 Immediate Actions (Critical)

1. **Enable RLS on All Tables**
   - Enable RLS on strategies table
   - Enable RLS on orders table
   - Enable RLS on positions table
   - Enable RLS on exchange_credentials table

2. **Implement Tenant-Level RLS Policies**
   - Replace user-level policies with tenant-level policies
   - Use tenant_id in all RLS policies
   - Add service role policies with tenant validation

3. **Extract and Propagate tenant_id**
   - Extract tenant_id from JWT in get_current_user
   - Set app.current_tenant_id in database context
   - Validate tenant_id in all database operations

4. **Implement Service Role Safety Guards**
   - Add service role usage validation
   - Add audit logging for service role operations
   - Enforce tenant context for service role

### 5.2 Short-Term Actions (High Priority)

1. **WebSocket Tenant Validation**
   - Extract tenant_id from JWT in WebSocket auth
   - Validate tenant context on WebSocket connection
   - Enforce tenant isolation in WebSocket message handling

2. **Replay Tenant Context**
   - Add tenant_id to StateSnapshot
   - Add tenant validation on state restoration
   - Enforce tenant isolation in state persistence

3. **Execution Tenant Validation**
   - Add tenant validation in repository methods
   - Add tenant context propagation in execution flows
   - Add tenant isolation in execution persistence

### 5.3 Long-Term Actions (Medium Priority)

1. **Tenant Context Monitoring**
   - Add tenant context metrics
   - Add tenant isolation validation
   - Add tenant cross-access detection

2. **Service Role Audit Trail**
   - Add comprehensive audit logging
   - Add service role usage monitoring
   - Add service role anomaly detection

---

## 6. Conclusion

The RLS implementation has significant gaps that prevent institutional-grade tenant isolation. The most critical issues are:

1. **RLS not enabled on critical tables** - Tenant data could be accessed across tenants
2. **No tenant_id propagation** - No tenant context in database operations
3. **No service role safety guards** - Service role could bypass RLS policies
4. **No WebSocket tenant validation** - WebSocket connections could access cross-tenant data

**Overall RLS Status:** ⚠️ PARTIALLY IMPLEMENTED (60/100)

**Recommendation:** Address all critical safety violations before production deployment.

---

**Audit Completed:** 2026-05-20  
**Auditor:** Principal Institutional Execution Consistency Engineer  
**Status:** CRITICAL SAFETY VIOLATIONS FOUND - IMMEDIATE ACTION REQUIRED
