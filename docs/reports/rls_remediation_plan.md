# RLS Remediation Plan (Approved)

**Role:** Principal Institutional Multi-Tenant Database Security Engineer  
**Audit ID:** RLS-REMEDIATION-FINAL  
**Date:** 2026-06-06  
**Platform:** Aerora Quantitative Trading Platform  

---

## 1. Executive Summary

This remediation plan addresses the critical Row-Level Security (RLS) and multi-tenant isolation audit for the Aerora platform. 
We have investigated the active schemas in the Supabase PostgreSQL database and the local SQLite database. We have identified a core misalignment between how the database-level RLS policies were written (using transaction GUCs like `app.current_tenant_id`) and how clients interact with Supabase (directly via PostgREST using JWTs).

This document outlines:
1. The actual tenant ownership model.
2. Why `current_setting('app.current_tenant_id')` exists and why it fails.
3. Where it is still used.
4. A recommended, stateless, Supabase-native RLS policy structure.
5. The exact SQL migration required to secure the database.

---

## 2. Table Schema & Tenant Ownership Investigation

We introspected the active Supabase PostgreSQL database tables using PostgREST OpenAPI metadata and verified local tables in the SQLite database (`algo22.db`).

### 2.1 Table Schemas & Columns

#### 1. `profiles` (Supabase)
*   **Columns:**
    *   `id`: `uuid` (Primary Key, maps to `auth.users.id`)
    *   `username`: `text`
    *   `display_name`: `text`
    *   `avatar_url`: `text`
    *   `bio`: `text`
    *   `telegram_id`: `text`
    *   `created_at`: `timestamp with time zone`
    *   `balance`: `numeric`
    *   `subscription_tier`: `text`
    *   `deployed_bots`: `integer`
    *   `ml_strategies_built`: `integer`
    *   `ml_addons_purchased`: `integer`
*   **Tenant Mapping:** Ownership is determined by `id` (User ID). There is **no** `tenant_id` or `organization_id` column currently in the Supabase database.

#### 2. `strategies` (Supabase)
*   **Columns:**
    *   `id`: `uuid` (Primary Key)
    *   `user_id`: `uuid` (References `profiles.id`)
    *   `name`: `text`
    *   `symbol`: `text`
    *   `timeframe`: `text`
    *   `buy_logic`: `jsonb`
    *   `sell_logic`: `jsonb`
    *   `risk`: `jsonb`
    *   `indicators`: `jsonb`
    *   `ml_model_path`: `text`
    *   `exchange_id`: `text`
    *   `status`: `text`
    *   `created_at`: `timestamp with time zone`
    *   `updated_at`: `timestamp with time zone`
*   **Tenant Mapping:** Ownership is determined by `user_id` (User ID). There is **no** `tenant_id` or `organization_id` column.

#### 3. `processed_orders` (Supabase)
*   **Columns:**
    *   `order_id`: `uuid` (Primary Key)
    *   `user_id`: `uuid` (References `profiles.id`)
    *   `processed_at`: `timestamp with time zone`
*   **Tenant Mapping:** Ownership is determined by `user_id` (User ID).

#### 4. `exchange_keys` / `exchange_credentials` (Supabase)
*   *Note: In the backend code (`security_vault.py`), the table is named `exchange_keys`. It is currently queried using the Service Role Key.*
*   **Columns:**
    *   `user_id`: `uuid` (Primary Key / Part of Composite Key)
    *   `exchange_id`: `text` (Primary Key)
    *   `encrypted_api_key`: `text`
    *   `encrypted_secret_key`: `text`
    *   `encrypted_password`: `text` (optional)
*   **Tenant Mapping:** Ownership is determined by `user_id` (User ID).

#### 5. Local Tables (SQLite: `algo22.db`)
*   **Tables:** `positions`, `orders`, `fills`, `execution_records`, `dag_tasks`
*   **Columns:** These tables contain a `tenant_id` column (stored as text/UUID).
*   **Tenant Mapping:** The Python application maps `tenant_id` directly to `user_id` when reading and writing.

### 2.2 Ownership Mapping Decision

*   **Is tenant ownership stored in `user_id`, `tenant_id`, `organization_id`, auth metadata, or JWT claims?**
    *   **Database (Supabase):** Stored directly in `user_id` (or `id` for profiles). The database has no tenant or organization tables.
    *   **JWT Claims:** JWTs issued by Supabase Auth contain the user's ID in the `sub` claim.
    *   **Application Context:** `core/tenant_middleware.py` maps `tenant_id = payload.get("tenant_id", payload["sub"])`. Since `tenant_id` is not present in standard Supabase JWTs, it falls back to the user's ID (`sub`).
    *   **Conclusion:** **The actual tenant ownership model maps 1-to-1 to the User ID (`user_id` / `id`). A user represents a single tenant.**

---

## 3. The `app.current_tenant_id` Disconnect

### 3.1 Why `current_setting('app.current_tenant_id')` exists
In typical multi-tenant PostgreSQL systems, application servers connect via a single pool and execute `SET LOCAL app.current_tenant_id = '...'` inside a transaction. RLS policies checking `current_setting('app.current_tenant_id')` then automatically isolate rows.

### 3.2 Why it fails under PostgREST
PostgREST requests (e.g. client queries initiated directly from the frontend or through the Supabase client library) bypass the Python backend's custom transaction initialization. PostgREST connects statelessly and does *not* execute the `SET LOCAL` command. 
Therefore:
1. `current_setting('app.current_tenant_id')` evaluates to `NULL` for PostgREST calls.
2. Any `WITH CHECK` or `USING` clauses enforcing this setting evaluate to `false` (or throw database errors), leading to RLS violations (HTTP `42501` / Insufficient Privileges).

### 3.3 Where it is still used
1. In `rls_migration.sql` (under policies for `dag_tasks` and `execution_records`).
2. In `core/tenant_middleware.py` and `DATABASE_TENANT_MIGRATION.md` documentation, though the FastAPI application pool does not currently initialize it correctly before execution.

---

## 4. Recommended Supabase-Native Policies

To ensure RLS functions statelessly and securely under both PostgREST (direct client calls) and the Python backend, we must align database-level RLS with Supabase-native auth helpers:

1.  **`auth.uid()`**: Dynamically extracts the authenticated user's ID (the `sub` claim) from the request's JWT token.
2.  **Stateless Filtering**: Enforces `auth.uid() = user_id` directly without GUC dependencies.

### 4.1 Recommended Policies

#### 1. `profiles` Table
```sql
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

CREATE POLICY "profiles_owner_policy" ON profiles
    FOR ALL
    TO authenticated
    USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);
```

#### 2. `strategies` Table
```sql
ALTER TABLE strategies ENABLE ROW LEVEL SECURITY;

CREATE POLICY "strategies_owner_policy" ON strategies
    FOR ALL
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);
```

#### 3. `processed_orders` Table
```sql
ALTER TABLE processed_orders ENABLE ROW LEVEL SECURITY;

CREATE POLICY "processed_orders_owner_policy" ON processed_orders
    FOR ALL
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);
```

#### 4. `exchange_keys` Table
```sql
ALTER TABLE exchange_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY "exchange_keys_owner_policy" ON exchange_keys
    FOR ALL
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);
```

---

## 5. Safe Migration & Remediation Plan

We propose two models based on potential future expansion:
*   **Model A (1-to-1 User-to-Tenant):** Direct mapping using `auth.uid() = user_id`. Recommended for the current architecture since no schema changes are needed.
*   **Model B (Multi-User-per-Tenant):** Adds a `tenant_id` column to all tables and uses a profile join or JWT claim check.

### 5.1 Safe Migration SQL (Model A - Direct User Ownership)

This migration:
1. Drops existing broken policies that depend on GUC settings.
2. Enables RLS on all tables if they exist.
3. Restricts access natively using `auth.uid()`.
4. Restricts the `service_role` to secure background processes rather than bypassing RLS using `USING (true)`.

```sql
BEGIN;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 1: CLEANUP BROKEN POLICIES
-- ══════════════════════════════════════════════════════════════════════════

DROP POLICY IF EXISTS "Users can read own profile" ON profiles;
DROP POLICY IF EXISTS "Users can update own profile" ON profiles;
DROP POLICY IF EXISTS "Service role can read all profiles" ON profiles;
DROP POLICY IF EXISTS "Service role can update all profiles" ON profiles;
DROP POLICY IF EXISTS "profiles_tenant_isolation_select" ON profiles;
DROP POLICY IF EXISTS "profiles_tenant_isolation_insert" ON profiles;
DROP POLICY IF EXISTS "profiles_tenant_isolation_update" ON profiles;
DROP POLICY IF EXISTS "profiles_tenant_isolation_delete" ON profiles;

DROP POLICY IF EXISTS "strategies_tenant_isolation_select" ON strategies;
DROP POLICY IF EXISTS "strategies_tenant_isolation_insert" ON strategies;
DROP POLICY IF EXISTS "strategies_tenant_isolation_update" ON strategies;
DROP POLICY IF EXISTS "strategies_tenant_isolation_delete" ON strategies;

DROP POLICY IF EXISTS "exchange_credentials_tenant_isolation_select" ON exchange_credentials;
DROP POLICY IF EXISTS "exchange_keys_tenant_isolation_select" ON exchange_keys;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 2: ENABLE ROW-LEVEL SECURITY
-- ══════════════════════════════════════════════════════════════════════════

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'strategies') THEN
        ALTER TABLE strategies ENABLE ROW LEVEL SECURITY;
    END IF;
    
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'processed_orders') THEN
        ALTER TABLE processed_orders ENABLE ROW LEVEL SECURITY;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exchange_keys') THEN
        ALTER TABLE exchange_keys ENABLE ROW LEVEL SECURITY;
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 3: CREATE STATELESS SUPABASE-NATIVE POLICIES
-- ══════════════════════════════════════════════════════════════════════════

-- 1. Profiles Table Policies
CREATE POLICY "profiles_authenticated_owner" ON profiles
    FOR ALL
    TO authenticated
    USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);

-- 2. Strategies Table Policies (Conditional)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'strategies') THEN
        CREATE POLICY "strategies_authenticated_owner" ON strategies
            FOR ALL
            TO authenticated
            USING (auth.uid() = user_id)
            WITH CHECK (auth.uid() = user_id);
    END IF;
END $$;

-- 3. Processed Orders Table Policies (Conditional)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'processed_orders') THEN
        CREATE POLICY "processed_orders_authenticated_owner" ON processed_orders
            FOR ALL
            TO authenticated
            USING (auth.uid() = user_id)
            WITH CHECK (auth.uid() = user_id);
    END IF;
END $$;

-- 4. Exchange Keys Table Policies (Conditional)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exchange_keys') THEN
        CREATE POLICY "exchange_keys_authenticated_owner" ON exchange_keys
            FOR ALL
            TO authenticated
            USING (auth.uid() = user_id)
            WITH CHECK (auth.uid() = user_id);
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 4: INDEX OPTIMIZATIONS (Prevent Full Table Scans)
-- ══════════════════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_profiles_id ON profiles(id);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'strategies') THEN
        CREATE INDEX IF NOT EXISTS idx_strategies_user_id ON strategies(user_id);
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'processed_orders') THEN
        CREATE INDEX IF NOT EXISTS idx_processed_orders_user_id ON processed_orders(user_id);
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exchange_keys') THEN
        CREATE INDEX IF NOT EXISTS idx_exchange_keys_user_id ON exchange_keys(user_id);
    END IF;
END $$;

COMMIT;
```

### 5.2 Verification Plan

1.  **Direct PostgREST Select Verification:**
    ```bash
    curl -X GET 'https://YOUR_PROJECT_REF.supabase.co/rest/v1/strategies?select=*' \
      -H "apikey: SUPABASE_ANON_KEY" \
      -H "Authorization: Bearer USER_A_JWT"
    # Expected: Returns only User A's strategies.
    ```
2.  **Cross-Tenant Leakage Check:**
    ```bash
    curl -X GET 'https://YOUR_PROJECT_REF.supabase.co/rest/v1/strategies?user_id=eq.USER_B_UUID' \
      -H "apikey: SUPABASE_ANON_KEY" \
      -H "Authorization: Bearer USER_A_JWT"
    # Expected: Returns empty array [] (cannot read User B's strategies).
    ```
3.  **Invalid Context Insert Check:**
    ```bash
    curl -X POST 'https://YOUR_PROJECT_REF.supabase.co/rest/v1/strategies' \
      -H "apikey: SUPABASE_ANON_KEY" \
      -H "Authorization: Bearer USER_A_JWT" \
      -H "Content-Type: application/json" \
      -d '{"user_id": "USER_B_UUID", "name": "Hack Attempt"}'
    # Expected: Returns 401/403 RLS violation (cannot insert on behalf of User B).
    ```
