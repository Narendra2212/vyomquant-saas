-- RLS Emergency Remediation Migration (Approved Plan)
-- Role: Principal Institutional Multi-Tenant Database Security Engineer
-- Date: 2026-06-06
-- Objective: Enable strict tenant isolation on all tables using Supabase-native auth.uid()
-- FIX: CREATE POLICY inside DO $$ blocks now uses EXECUTE (PL/pgSQL DDL requirement)

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
        EXECUTE 'ALTER TABLE strategies ENABLE ROW LEVEL SECURITY';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'processed_orders') THEN
        EXECUTE 'ALTER TABLE processed_orders ENABLE ROW LEVEL SECURITY';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exchange_keys') THEN
        EXECUTE 'ALTER TABLE exchange_keys ENABLE ROW LEVEL SECURITY';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 3: CREATE STATELESS SUPABASE-NATIVE POLICIES
-- ══════════════════════════════════════════════════════════════════════════

-- 1. Profiles Table Policies (top-level: table always exists)
CREATE POLICY "profiles_authenticated_owner" ON profiles
    FOR ALL
    TO authenticated
    USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);

-- 2. Strategies Table Policies (Conditional — EXECUTE required for DDL in DO blocks)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'strategies') THEN
        EXECUTE $policy$
            CREATE POLICY "strategies_authenticated_owner" ON strategies
                FOR ALL
                TO authenticated
                USING (auth.uid() = user_id)
                WITH CHECK (auth.uid() = user_id)
        $policy$;
    END IF;
END $$;

-- 3. Processed Orders Table Policies (Conditional)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'processed_orders') THEN
        EXECUTE $policy$
            CREATE POLICY "processed_orders_authenticated_owner" ON processed_orders
                FOR ALL
                TO authenticated
                USING (auth.uid() = user_id)
                WITH CHECK (auth.uid() = user_id)
        $policy$;
    END IF;
END $$;

-- 4. Exchange Keys Table Policies (Conditional)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exchange_keys') THEN
        EXECUTE $policy$
            CREATE POLICY "exchange_keys_authenticated_owner" ON exchange_keys
                FOR ALL
                TO authenticated
                USING (auth.uid() = user_id)
                WITH CHECK (auth.uid() = user_id)
        $policy$;
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 4: INDEX OPTIMIZATIONS (Prevent Full Table Scans)
-- ══════════════════════════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_profiles_id ON profiles(id);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'strategies') THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_strategies_user_id ON strategies(user_id)';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'processed_orders') THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_processed_orders_user_id ON processed_orders(user_id)';
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exchange_keys') THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_exchange_keys_user_id ON exchange_keys(user_id)';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 5: ATOMIC BILLING INCREMENT FUNCTION
-- ══════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION increment_ml_addon(target_user_id UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE profiles
    SET ml_addons_purchased = COALESCE(ml_addons_purchased, 0) + 1
    WHERE id = target_user_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMIT;
