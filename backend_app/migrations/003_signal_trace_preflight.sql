-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 9K: Production Read-Only Database Preflight
-- ══════════════════════════════════════════════════════════════════════════
--
-- This file contains ONLY SELECT/catalog queries to inspect production schema.
-- NO DDL, NO DML, NO modifications of any kind.
--
-- Purpose: Verify production database state before executing
--          003_signal_trace_restoration.sql
-- ══════════════════════════════════════════════════════════════════════════

-- ══════════════════════════════════════════════════════════════════════════
-- 1. REQUIRED TABLE EXISTENCE
-- ══════════════════════════════════════════════════════════════════════════

SELECT 
    table_schema,
    table_name,
    'EXISTS' as status
FROM information_schema.tables
WHERE table_schema IN ('public', 'auth')
AND table_name IN ('strategies', 'strategy_versions', 'strategy_deployments', 'signals', 'signal_events', 'exchanges', 'users')
ORDER BY table_schema, table_name;

-- ══════════════════════════════════════════════════════════════════════════
-- 2. STRATEGIES SCHEMA
-- ══════════════════════════════════════════════════════════════════════════

SELECT 
    column_name,
    data_type,
    udt_name,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
AND table_name = 'strategies'
AND column_name IN ('id', 'user_id', 'exchange', 'current_version', 'environment', 'status')
ORDER BY ordinal_position;

-- Full strategies schema (all columns)
SELECT 
    column_name,
    data_type,
    udt_name,
    is_nullable,
    column_default,
    ordinal_position
FROM information_schema.columns
WHERE table_schema = 'public'
AND table_name = 'strategies'
ORDER BY ordinal_position;

-- ══════════════════════════════════════════════════════════════════════════
-- 3. STRATEGY VERSIONS
-- ══════════════════════════════════════════════════════════════════════════

SELECT 
    'strategy_versions' as table_name,
    CASE 
        WHEN EXISTS (
            SELECT 1 FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = 'strategy_versions'
        ) THEN 'EXISTS'
        ELSE 'MISSING'
    END as status;

-- If exists, show all columns
SELECT 
    column_name,
    data_type,
    udt_name,
    is_nullable,
    column_default,
    ordinal_position
FROM information_schema.columns
WHERE table_schema = 'public'
AND table_name = 'strategy_versions'
ORDER BY ordinal_position;

-- ══════════════════════════════════════════════════════════════════════════
-- 4. STRATEGY DEPLOYMENTS
-- ══════════════════════════════════════════════════════════════════════════

SELECT 
    'strategy_deployments' as table_name,
    CASE 
        WHEN EXISTS (
            SELECT 1 FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = 'strategy_deployments'
        ) THEN 'EXISTS'
        ELSE 'MISSING'
    END as status;

-- If exists, show all columns (focus on critical ones)
SELECT 
    column_name,
    data_type,
    udt_name,
    is_nullable,
    column_default,
    ordinal_position
FROM information_schema.columns
WHERE table_schema = 'public'
AND table_name = 'strategy_deployments'
AND column_name IN (
    'id', 'strategy_id', 'user_id', 'version_id', 'version', 
    'environment', 'exchange_id', 'exchange_symbol', 'worker_region', 
    'worker_id', 'status', 'initial_capital'
)
ORDER BY ordinal_position;

-- Full strategy_deployments schema
SELECT 
    column_name,
    data_type,
    udt_name,
    is_nullable,
    column_default,
    ordinal_position
FROM information_schema.columns
WHERE table_schema = 'public'
AND table_name = 'strategy_deployments'
ORDER BY ordinal_position;

-- ══════════════════════════════════════════════════════════════════════════
-- 5. SIGNALS
-- ══════════════════════════════════════════════════════════════════════════

SELECT 
    'signals' as table_name,
    CASE 
        WHEN EXISTS (
            SELECT 1 FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = 'signals'
        ) THEN 'EXISTS'
        ELSE 'MISSING'
    END as status;

-- If exists, show all columns
SELECT 
    column_name,
    data_type,
    udt_name,
    is_nullable,
    column_default,
    ordinal_position
FROM information_schema.columns
WHERE table_schema = 'public'
AND table_name = 'signals'
ORDER BY ordinal_position;

-- ══════════════════════════════════════════════════════════════════════════
-- 6. FOREIGN KEYS
-- ══════════════════════════════════════════════════════════════════════════

SELECT 
    tc.table_name,
    tc.constraint_name,
    kcu.column_name,
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name,
    tc.constraint_type
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu 
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage AS ccu 
    ON ccu.constraint_name = tc.constraint_name
WHERE tc.table_schema = 'public'
AND tc.table_name IN ('strategies', 'strategy_versions', 'strategy_deployments', 'signals')
AND tc.constraint_type = 'FOREIGN KEY'
ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position;

-- ══════════════════════════════════════════════════════════════════════════
-- 7. INDEXES
-- ══════════════════════════════════════════════════════════════════════════

SELECT 
    schemaname,
    tablename,
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
AND tablename IN ('strategies', 'strategy_versions', 'strategy_deployments', 'signals')
ORDER BY tablename, indexname;

-- ══════════════════════════════════════════════════════════════════════════
-- 8. RLS STATUS
-- ══════════════════════════════════════════════════════════════════════════

SELECT 
    c.relname AS table_name,
    c.relrowsecurity AS rls_enabled
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
AND c.relname IN ('strategies', 'strategy_versions', 'strategy_deployments', 'signals')
ORDER BY c.relname;

-- ══════════════════════════════════════════════════════════════════════════
-- 9. RLS POLICIES
-- ══════════════════════════════════════════════════════════════════════════

SELECT 
    schemaname,
    tablename,
    policyname,
    permissive,
    roles,
    cmd,
    qual,
    with_check
FROM pg_policies
WHERE schemaname = 'public'
AND tablename IN ('strategies', 'strategy_versions', 'strategy_deployments', 'signals')
ORDER BY tablename, policyname;

-- ══════════════════════════════════════════════════════════════════════════
-- 10. TRIGGERS
-- ══════════════════════════════════════════════════════════════════════════

SELECT 
    n.nspname AS schema,
    c.relname AS table_name,
    t.tgname AS trigger_name,
    CASE 
        WHEN t.tgenabled = 'O' THEN 'ENABLED'
        WHEN t.tgenabled = 'D' THEN 'DISABLED'
        WHEN t.tgenabled = 'R' THEN 'REPLICA'
        WHEN t.tgenabled = 'A' THEN 'ALWAYS'
        ELSE 'UNKNOWN'
    END AS enabled
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
AND c.relname IN ('strategies', 'strategy_versions', 'strategy_deployments', 'signals')
AND NOT t.tgisinternal
ORDER BY c.relname, t.tgname;

-- ══════════════════════════════════════════════════════════════════════════
-- 11. TRIGGER FUNCTIONS
-- ══════════════════════════════════════════════════════════════════════════

SELECT 
    'update_updated_at_column' as function_name,
    CASE 
        WHEN EXISTS (
            SELECT 1 FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
            AND p.proname = 'update_updated_at_column'
        ) THEN 'EXISTS'
        ELSE 'MISSING'
    END as status;

SELECT 
    'update_signals_updated_at' as function_name,
    CASE 
        WHEN EXISTS (
            SELECT 1 FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public'
            AND p.proname = 'update_signals_updated_at'
        ) THEN 'EXISTS'
        ELSE 'MISSING'
    END as status;

-- All trigger functions in public schema
SELECT 
    p.proname AS function_name,
    n.nspname AS schema
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
AND p.proname LIKE '%update%'
ORDER BY p.proname;

-- ══════════════════════════════════════════════════════════════════════════
-- 12. EXISTING DATA COUNTS (SAFE, NON-SENSITIVE)
-- ══════════════════════════════════════════════════════════════════════════

-- Strategies row count
SELECT 
    'strategies' as table_name,
    COUNT(*) as row_count
FROM public.strategies;

-- Strategy versions row count (if exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'strategy_versions') THEN
        RAISE NOTICE 'strategy_versions row count: %', (SELECT COUNT(*) FROM public.strategy_versions);
    ELSE
        RAISE NOTICE 'strategy_versions: MISSING';
    END IF;
END $$;

-- Strategy deployments row count (if exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'strategy_deployments') THEN
        RAISE NOTICE 'strategy_deployments row count: %', (SELECT COUNT(*) FROM public.strategy_deployments);
    ELSE
        RAISE NOTICE 'strategy_deployments: MISSING';
    END IF;
END $$;

-- Signals row count (if exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'signals') THEN
        RAISE NOTICE 'signals row count: %', (SELECT COUNT(*) FROM public.signals);
    ELSE
        RAISE NOTICE 'signals: MISSING';
    END IF;
END $$;

-- Signal events row count (if exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'signal_events') THEN
        RAISE NOTICE 'signal_events row count: %', (SELECT COUNT(*) FROM public.signal_events);
    ELSE
        RAISE NOTICE 'signal_events: MISSING';
    END IF;
END $$;

-- Exchanges row count (if exists)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'exchanges') THEN
        RAISE NOTICE 'exchanges row count: %', (SELECT COUNT(*) FROM public.exchanges);
    ELSE
        RAISE NOTICE 'exchanges: MISSING';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- 13. ADDITIONAL CHECKS: CONSTRAINTS
-- ══════════════════════════════════════════════════════════════════════════

-- Check for unique constraints
SELECT 
    tc.table_name,
    tc.constraint_name,
    tc.constraint_type
FROM information_schema.table_constraints tc
WHERE tc.table_schema = 'public'
AND tc.table_name IN ('strategies', 'strategy_versions', 'strategy_deployments', 'signals')
AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
ORDER BY tc.table_name, tc.constraint_name;

-- Check for check constraints
SELECT 
    tc.table_name,
    tc.constraint_name,
    cc.check_clause
FROM information_schema.table_constraints tc
JOIN information_schema.check_constraints cc ON cc.constraint_name = tc.constraint_name
WHERE tc.table_schema = 'public'
AND tc.table_name IN ('strategies', 'strategy_versions', 'strategy_deployments', 'signals')
AND tc.constraint_type = 'CHECK'
ORDER BY tc.table_name, tc.constraint_name;

-- ══════════════════════════════════════════════════════════════════════════
-- END OF PREFLIGHT
-- ══════════════════════════════════════════════════════════════════════════
