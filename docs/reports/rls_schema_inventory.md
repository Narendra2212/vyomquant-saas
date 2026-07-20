# RLS Schema Inventory

**Principal Institutional Multi-Tenant Database Security Engineer**

**Audit ID:** RLS-SCHEMA-1716200000  
**Date:** 2026-05-30  
**Platform:** ALGO22 Quantitative Trading Platform  
**Objective:** Audit actual database schema for RLS emergency remediation

---

## Executive Summary

This inventory documents the actual database schema as found in migration files and model definitions. The audit reveals critical gaps in RLS enforcement and tenant isolation.

**Audit Status:** ❌ CRITICAL GAPS IDENTIFIED

---

## 1. Table Existence Verification

### 1.1 Tables Found in Migrations

| Table | Migration File | RLS Enabled | tenant_id Column | Status |
|-------|----------------|-------------|------------------|--------|
| dag_tasks | 001_create_dag_tasks_table.sql | ✅ YES | ✅ YES | ✅ VERIFIED |
| execution_records | 002_create_execution_records.sql | ✅ YES | ✅ YES | ✅ VERIFIED |
| profiles | rls_policies_migration.sql | ✅ YES | ✅ YES | ⚠️ USER-LEVEL POLICIES |
| strategies | rls_policies_migration.sql | ❌ COMMENTED OUT | ❌ COMMENTED OUT | ❌ NOT VERIFIED |
| orders | rls_policies_migration.sql | ❌ COMMENTED OUT | ❌ COMMENTED OUT | ❌ NOT VERIFIED |
| positions | rls_policies_migration.sql | ❌ COMMENTED OUT | ❌ COMMENTED OUT | ❌ NOT VERIFIED |
| exchange_credentials | rls_policies_migration.sql | ❌ COMMENTED OUT | ❌ COMMENTED OUT | ❌ NOT VERIFIED |

### 1.2 Tables Found in Models

| Table | Model File | RLS Enabled | tenant_id Column | Status |
|-------|------------|-------------|------------------|--------|
| dag_tasks | core/models/dag_task.py | ✅ YES | ✅ YES | ✅ VERIFIED |
| execution_records | core/models/execution_record.py | ✅ YES | ✅ YES | ✅ VERIFIED |

### 1.3 Tables Not Found

| Table | Status | Reason |
|-------|--------|--------|
| fills | ❌ NOT FOUND | No migration or model found |
| checkpoints | ❌ NOT FOUND | No migration or model found |
| snapshots | ❌ NOT FOUND | No migration or model found |

---

## 2. Detailed Table Analysis

### 2.1 dag_tasks

**Migration:** `001_create_dag_tasks_table.sql`

**Schema:**
```sql
CREATE TABLE dag_tasks (
    task_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    status task_status NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 5,
    dag_config JSONB NOT NULL,
    progress FLOAT NOT NULL DEFAULT 0.0,
    result JSONB,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    last_heartbeat TIMESTAMP WITH TIME ZONE
);
```

**RLS Status:** ✅ ENABLED

**RLS Policies:**
```sql
-- Policy: Tenant isolation for SELECT
CREATE POLICY dag_tasks_tenant_isolation ON dag_tasks
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Policy: Tenant isolation for INSERT
CREATE POLICY dag_tasks_tenant_insert ON dag_tasks
    FOR INSERT 
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Policy: Tenant isolation for UPDATE
CREATE POLICY dag_tasks_tenant_update ON dag_tasks
    FOR UPDATE
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Policy: Tenant isolation for DELETE
CREATE POLICY dag_tasks_tenant_delete ON dag_tasks
    FOR DELETE
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

**Service Role Protection:** ❌ MISSING

**Classification:** ⚠️ NEEDS HARDENING

**Issues:**
- No service role policies defined
- Service role can bypass tenant isolation

---

### 2.2 execution_records

**Migration:** `002_create_execution_records.sql`

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS execution_records (
    execution_id TEXT PRIMARY KEY,
    tenant_id UUID NOT NULL,
    task_id UUID,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    status execution_status NOT NULL DEFAULT 'pending',
    order_id TEXT,
    result JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

**RLS Status:** ✅ ENABLED

**RLS Policies:**
```sql
-- Policy: Tenant isolation for ALL operations
CREATE POLICY execution_records_tenant_isolation_policy ON execution_records
    FOR ALL
    USING (tenant_id::TEXT = current_setting('app.current_tenant_id', true));
```

**Service Role Protection:** ❌ MISSING

**Classification:** ⚠️ NEEDS HARDENING

**Issues:**
- No service role policies defined
- Service role can bypass tenant isolation
- Policy uses `tenant_id::TEXT` which may cause type issues

---

### 2.3 profiles

**Migration:** `rls_policies_migration.sql`

**Schema:** Not defined in migrations (assumed to exist from Supabase)

**RLS Status:** ✅ ENABLED

**RLS Policies:**
```sql
-- Policy: Users can only read their own profile (USER-LEVEL)
CREATE POLICY "Users can read own profile"
ON profiles
FOR SELECT
USING (auth.uid()::text = id::text);

-- Policy: Users can only update their own profile (USER-LEVEL)
CREATE POLICY "Users can update own profile"
ON profiles
FOR UPDATE
USING (auth.uid()::text = id::text);

-- Policy: Service role can read all profiles (UNSAFE)
CREATE POLICY "Service role can read all profiles"
ON profiles
FOR SELECT
TO service_role
USING (true);

-- Policy: Service role can update all profiles (UNSAFE)
CREATE POLICY "Service role can update all profiles"
ON profiles
FOR UPDATE
TO service_role
USING (true);
```

**Service Role Protection:** ❌ UNSAFE

**Classification:** ❌ BROKEN

**Issues:**
- User-level policies instead of tenant-level
- Service role bypasses with USING (true)
- No tenant_id enforcement

---

### 2.4 strategies

**Migration:** `rls_policies_migration.sql` (COMMENTED OUT)

**Schema:** Not defined in migrations

**RLS Status:** ❌ DISABLED (COMMENTED OUT)

**RLS Policies:** COMMENTED OUT

**Classification:** ❌ BROKEN

**Issues:**
- RLS enable statement commented out
- All policies commented out
- No tenant isolation

---

### 2.5 orders

**Migration:** `rls_policies_migration.sql` (COMMENTED OUT)

**Schema:** Not defined in migrations

**RLS Status:** ❌ DISABLED (COMMENTED OUT)

**RLS Policies:** COMMENTED OUT

**Classification:** ❌ BROKEN

**Issues:**
- RLS enable statement commented out
- All policies commented out
- No tenant isolation

---

### 2.6 positions

**Migration:** `rls_policies_migration.sql` (COMMENTED OUT)

**Schema:** Not defined in migrations

**RLS Status:** ❌ DISABLED (COMMENTED OUT)

**RLS Policies:** COMMENTED OUT

**Classification:** ❌ BROKEN

**Issues:**
- RLS enable statement commented out
- All policies commented out
- No tenant isolation

---

### 2.7 exchange_credentials

**Migration:** `rls_policies_migration.sql` (COMMENTED OUT)

**Schema:** Not defined in migrations

**RLS Status:** ❌ DISABLED (COMMENTED OUT)

**RLS Policies:** COMMENTED OUT

**Classification:** ❌ BROKEN

**Issues:**
- RLS enable statement commented out
- All policies commented out
- No tenant isolation

---

## 3. Missing Tables

### 3.1 fills

**Status:** ❌ NOT FOUND

**Impact:** No fill tracking table found

**Recommendation:** Create fills table with RLS enabled

---

### 3.2 checkpoints

**Status:** ❌ NOT FOUND

**Impact:** No checkpoint table for replay safety

**Recommendation:** Create checkpoints table with RLS enabled

---

### 3.3 snapshots

**Status:** ❌ NOT FOUND

**Impact:** No snapshot table for state restoration

**Recommendation:** Create snapshots table with RLS enabled

---

## 4. Critical Findings

### 4.1 RLS Disabled on Critical Tables

**Severity:** CRITICAL

**Affected Tables:**
- strategies
- orders
- positions
- exchange_credentials

**Impact:**
- No tenant isolation
- Cross-tenant access possible
- Data breach risk

**Remediation Priority:** URGENT

---

### 4.2 User-Level Policies Instead of Tenant-Level

**Severity:** CRITICAL

**Affected Tables:**
- profiles

**Impact:**
- Policies based on auth.uid() instead of tenant_id
- No tenant isolation
- Cross-tenant access possible

**Remediation Priority:** URGENT

---

### 4.3 Service Role Bypasses

**Severity:** CRITICAL

**Affected Tables:**
- profiles
- dag_tasks
- execution_records

**Impact:**
- Service role can bypass all RLS policies
- USING (true) allows full access
- No tenant isolation for service role

**Remediation Priority:** URGENT

---

### 4.4 Missing Service Role Protections

**Severity:** HIGH

**Affected Tables:**
- dag_tasks
- execution_records

**Impact:**
- No explicit service role policies
- Service role behavior undefined

**Remediation Priority:** HIGH

---

## 5. Schema Summary

### 5.1 Table Classification

| Table | RLS Status | Policy Type | Service Role | Classification |
|-------|------------|-------------|--------------|----------------|
| dag_tasks | ✅ ENABLED | TENANT-LEVEL | ❌ MISSING | ⚠️ NEEDS HARDENING |
| execution_records | ✅ ENABLED | TENANT-LEVEL | ❌ MISSING | ⚠️ NEEDS HARDENING |
| profiles | ✅ ENABLED | USER-LEVEL | ❌ UNSAFE | ❌ BROKEN |
| strategies | ❌ DISABLED | N/A | N/A | ❌ BROKEN |
| orders | ❌ DISABLED | N/A | N/A | ❌ BROKEN |
| positions | ❌ DISABLED | N/A | N/A | ❌ BROKEN |
| exchange_credentials | ❌ DISABLED | N/A | N/A | ❌ BROKEN |

**Summary:** 2/7 VERIFIED (28.6%), 2/7 NEEDS HARDENING (28.6%), 3/7 BROKEN (42.8%)

---

### 5.2 Required Actions

**Immediate (URGENT):**
1. Enable RLS on strategies, orders, positions, exchange_credentials
2. Replace user-level policies with tenant-level policies on profiles
3. Remove USING (true) service role bypasses
4. Add tenant_id columns to all tables
5. Implement tenant-scoped RLS policies

**Short-Term (HIGH):**
1. Add service role protections to dag_tasks
2. Add service role protections to execution_records
3. Create fills table with RLS
4. Create checkpoints table with RLS
5. Create snapshots table with RLS

---

## 6. Conclusion

**Overall Schema Status:** ❌ CRITICAL GAPS IDENTIFIED

**Summary:**
- 2/7 tables have RLS enabled with tenant-level policies
- 2/7 tables have RLS enabled but need service role protections
- 3/7 tables have RLS disabled (commented out)
- 1/7 table has user-level policies instead of tenant-level
- 3/7 tables have service role bypasses with USING (true)
- 3/7 tables are missing (fills, checkpoints, snapshots)

**Critical Findings:**
- RLS disabled on strategies, orders, positions, exchange_credentials
- User-level policies instead of tenant-level on profiles
- Service role bypasses with USING (true)
- Missing service role protections

**Recommendation:** DO NOT DEPLOY UNTIL CRITICAL FIXES

**Required Before Deployment:**
1. Enable RLS on all critical tables
2. Replace user-level policies with tenant-level policies
3. Remove service role bypasses
4. Add service role protections
5. Create missing tables with RLS

**Estimated Time to Fix:** 12-16 hours

---

**Audit Completed:** 2026-05-30  
**Auditor:** Principal Institutional Multi-Tenant Database Security Engineer  
**Status:** SCHEMA AUDIT COMPLETE - CRITICAL GAPS IDENTIFIED
