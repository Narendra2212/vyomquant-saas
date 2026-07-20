-- RLS Emergency Remediation Rollback
-- Principal Institutional Multi-Tenant Database Security Engineer
-- Date: 2026-05-30
-- Objective: Rollback RLS emergency remediation if needed

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 1: DROP NEW TENANT-LEVEL POLICIES
-- ══════════════════════════════════════════════════════════════════════════

-- Drop profiles tenant-level policies
DROP POLICY IF EXISTS profiles_tenant_isolation_select ON profiles;
DROP POLICY IF EXISTS profiles_tenant_isolation_insert ON profiles;
DROP POLICY IF EXISTS profiles_tenant_isolation_update ON profiles;
DROP POLICY IF EXISTS profiles_tenant_isolation_delete ON profiles;

-- Drop strategies tenant-level policies (if table exists)
DROP POLICY IF EXISTS strategies_tenant_isolation_select ON strategies;
DROP POLICY IF EXISTS strategies_tenant_isolation_insert ON strategies;
DROP POLICY IF EXISTS strategies_tenant_isolation_update ON strategies;
DROP POLICY IF EXISTS strategies_tenant_isolation_delete ON strategies;

-- Drop orders tenant-level policies (if table exists)
DROP POLICY IF EXISTS orders_tenant_isolation_select ON orders;
DROP POLICY IF EXISTS orders_tenant_isolation_insert ON orders;
DROP POLICY IF EXISTS orders_tenant_isolation_update ON orders;
DROP POLICY IF EXISTS orders_tenant_isolation_delete ON orders;

-- Drop positions tenant-level policies (if table exists)
DROP POLICY IF EXISTS positions_tenant_isolation_select ON positions;
DROP POLICY IF EXISTS positions_tenant_isolation_insert ON positions;
DROP POLICY IF EXISTS positions_tenant_isolation_update ON positions;
DROP POLICY IF EXISTS positions_tenant_isolation_delete ON positions;

-- Drop exchange_keys tenant-level policies (if table exists)
DROP POLICY IF EXISTS exchange_keys_tenant_isolation_select ON exchange_keys;
DROP POLICY IF EXISTS exchange_keys_tenant_isolation_insert ON exchange_keys;
DROP POLICY IF EXISTS exchange_keys_tenant_isolation_update ON exchange_keys;
DROP POLICY IF EXISTS exchange_keys_tenant_isolation_delete ON exchange_keys;

-- Drop dag_tasks tenant-level policies
DROP POLICY IF EXISTS dag_tasks_tenant_isolation_select ON dag_tasks;
DROP POLICY IF EXISTS dag_tasks_tenant_isolation_insert ON dag_tasks;
DROP POLICY IF EXISTS dag_tasks_tenant_isolation_update ON dag_tasks;
DROP POLICY IF EXISTS dag_tasks_tenant_isolation_delete ON dag_tasks;

-- Drop execution_records tenant-level policies
DROP POLICY IF EXISTS execution_records_tenant_isolation_select ON execution_records;
DROP POLICY IF EXISTS execution_records_tenant_isolation_insert ON execution_records;
DROP POLICY IF EXISTS execution_records_tenant_isolation_update ON execution_records;
DROP POLICY IF EXISTS execution_records_tenant_isolation_delete ON execution_records;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 2: RESTORE ORIGINAL POLICIES
-- ══════════════════════════════════════════════════════════════════════════

-- Restore original profiles policies (user-level policies)
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

-- Restore original dag_tasks policies
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

-- Restore original execution_records policy
CREATE POLICY execution_records_tenant_isolation_policy ON execution_records
    FOR ALL
    USING (tenant_id::TEXT = current_setting('app.current_tenant_id', true));

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 3: DISABLE RLS ON TABLES THAT WERE ENABLED
-- ══════════════════════════════════════════════════════════════════════════

-- Disable RLS on strategies if table exists
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'strategies') THEN
        ALTER TABLE strategies DISABLE ROW LEVEL SECURITY;
    END IF;
END $$;

-- Disable RLS on orders if table exists
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'orders') THEN
        ALTER TABLE orders DISABLE ROW LEVEL SECURITY;
    END IF;
END $$;

-- Disable RLS on positions if table exists
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'positions') THEN
        ALTER TABLE positions DISABLE ROW LEVEL SECURITY;
    END IF;
END $$;

-- Disable RLS on exchange_keys if table exists
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exchange_keys') THEN
        ALTER TABLE exchange_keys DISABLE ROW LEVEL SECURITY;
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 4: REMOVE TENANT_ID COLUMNS (OPTIONAL - DANGEROUS)
-- ══════════════════════════════════════════════════════════════════════════

-- WARNING: Dropping tenant_id columns will cause data loss if any data was added
-- This section is commented out by default for safety
-- Uncomment only if you are certain you want to revert tenant_id columns

-- ALTER TABLE profiles DROP COLUMN IF EXISTS tenant_id;
-- ALTER TABLE strategies DROP COLUMN IF EXISTS tenant_id;
-- ALTER TABLE orders DROP COLUMN IF EXISTS tenant_id;
-- ALTER TABLE positions DROP COLUMN IF EXISTS tenant_id;
-- ALTER TABLE exchange_keys DROP COLUMN IF EXISTS tenant_id;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 5: DROP TENANT_ID INDEXES (OPTIONAL)
-- ══════════════════════════════════════════════════════════════════════════

-- WARNING: Dropping indexes will impact query performance
-- This section is commented out by default for safety
-- Uncomment only if you are certain you want to revert tenant_id indexes

-- DROP INDEX IF EXISTS idx_profiles_tenant_id;
-- DROP INDEX IF EXISTS idx_strategies_tenant_id;
-- DROP INDEX IF EXISTS idx_orders_tenant_id;
-- DROP INDEX IF EXISTS idx_positions_tenant_id;
-- DROP INDEX IF EXISTS idx_exchange_keys_tenant_id;

-- ══════════════════════════════════════════════════════════════════════════
-- ROLLBACK COMPLETE
-- ══════════════════════════════════════════════════════════════════════════
