-- Tenant Isolation Validation Script
-- Principal Institutional Multi-Tenant Database Security Engineer
-- Date: 2026-05-30
-- Objective: Create test tenants and verify cross-tenant access prevention

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 1: CREATE TEST TENANTS
-- ══════════════════════════════════════════════════════════════════════════

-- Create tenant_A profile
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM profiles WHERE id = '00000000-0000-0000-0000-000000000001') THEN
        INSERT INTO profiles (id, email, tenant_id, created_at, updated_at)
        VALUES (
            '00000000-0000-0000-0000-000000000001',
            'tenant_a@test.com',
            '00000000-0000-0000-0000-000000000001',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        );
        RAISE NOTICE 'Created tenant_A profile';
    ELSE
        RAISE NOTICE 'tenant_A profile already exists';
    END IF;
END $$;

-- Create tenant_B profile
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM profiles WHERE id = '00000000-0000-0000-0000-000000000002') THEN
        INSERT INTO profiles (id, email, tenant_id, created_at, updated_at)
        VALUES (
            '00000000-0000-0000-0000-000000000002',
            'tenant_b@test.com',
            '00000000-0000-0000-0000-000000000002',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        );
        RAISE NOTICE 'Created tenant_B profile';
    ELSE
        RAISE NOTICE 'tenant_B profile already exists';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 2: CREATE TEST DATA FOR EACH TENANT
-- ══════════════════════════════════════════════════════════════════════════

-- Create dag_tasks for tenant_A
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dag_tasks') THEN
        INSERT INTO dag_tasks (task_id, tenant_id, status, priority, dag_config, created_at)
        VALUES (
            gen_random_uuid(),
            '00000000-0000-0000-0000-000000000001',
            'pending',
            5,
            '{"nodes": [], "edges": []}'::jsonb,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Created dag_task for tenant_A';
    END IF;
END $$;

-- Create dag_tasks for tenant_B
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dag_tasks') THEN
        INSERT INTO dag_tasks (task_id, tenant_id, status, priority, dag_config, created_at)
        VALUES (
            gen_random_uuid(),
            '00000000-0000-0000-0000-000000000002',
            'pending',
            5,
            '{"nodes": [], "edges": []}'::jsonb,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Created dag_task for tenant_B';
    END IF;
END $$;

-- Create execution_records for tenant_A
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'execution_records') THEN
        INSERT INTO execution_records (execution_id, tenant_id, strategy_id, symbol, side, status, created_at, updated_at)
        VALUES (
            'exec_tenant_a_test',
            '00000000-0000-0000-0000-000000000001',
            'test_strategy',
            'BTCUSDT',
            'buy',
            'pending',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Created execution_record for tenant_A';
    END IF;
END $$;

-- Create execution_records for tenant_B
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'execution_records') THEN
        INSERT INTO execution_records (execution_id, tenant_id, strategy_id, symbol, side, status, created_at, updated_at)
        VALUES (
            'exec_tenant_b_test',
            '00000000-0000-0000-0000-000000000002',
            'test_strategy',
            'BTCUSDT',
            'buy',
            'pending',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT DO NOTHING;
        RAISE NOTICE 'Created execution_record for tenant_B';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 3: CROSS-TENANT ACCESS PREVENTION TESTS
-- ══════════════════════════════════════════════════════════════════════════

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 1: PROFILES TABLE - TENANT A CANNOT READ TENANT B
-- ══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    row_count INTEGER;
BEGIN
    -- Set context to tenant_A
    SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
    
    -- Try to read tenant_B's profiles
    SELECT COUNT(*) INTO row_count
    FROM profiles
    WHERE tenant_id = '00000000-0000-0000-0000-000000000002';
    
    IF row_count = 0 THEN
        RAISE NOTICE '✅ TEST PASSED: Tenant A cannot read Tenant B profiles (0 rows)';
    ELSE
        RAISE NOTICE '❌ TEST FAILED: Tenant A can read Tenant B profiles (% rows)', row_count;
    END IF;
    
    -- Reset context
    RESET app.current_tenant_id;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 2: PROFILES TABLE - TENANT A CANNOT INSERT TENANT B
-- ══════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    -- Set context to tenant_A
    SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
    
    -- Try to insert with tenant_B's tenant_id
    BEGIN
        INSERT INTO profiles (id, email, tenant_id, created_at, updated_at)
        VALUES (
            '00000000-0000-0000-0000-000000000003',
            'test@test.com',
            '00000000-0000-0000-0000-000000000002',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        );
        RAISE NOTICE '❌ TEST FAILED: Tenant A can insert Tenant B profiles';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE '✅ TEST PASSED: Tenant A cannot insert Tenant B profiles (RLS violation)';
    END;
    
    -- Reset context
    RESET app.current_tenant_id;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 3: PROFILES TABLE - TENANT A CANNOT UPDATE TENANT B
-- ══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    row_count INTEGER;
BEGIN
    -- Set context to tenant_A
    SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
    
    -- Try to update tenant_B's profiles
    UPDATE profiles
    SET email = 'hacked@test.com'
    WHERE tenant_id = '00000000-0000-0000-0000-000000000002';
    
    GET DIAGNOSTICS row_count = ROW_COUNT;
    
    IF row_count = 0 THEN
        RAISE NOTICE '✅ TEST PASSED: Tenant A cannot update Tenant B profiles (0 rows updated)';
    ELSE
        RAISE NOTICE '❌ TEST FAILED: Tenant A can update Tenant B profiles (% rows updated)', row_count;
    END IF;
    
    -- Reset context
    RESET app.current_tenant_id;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 4: PROFILES TABLE - TENANT A CANNOT DELETE TENANT B
-- ══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    row_count INTEGER;
BEGIN
    -- Set context to tenant_A
    SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
    
    -- Try to delete tenant_B's profiles
    DELETE FROM profiles
    WHERE tenant_id = '00000000-0000-0000-0000-000000000002';
    
    GET DIAGNOSTICS row_count = ROW_COUNT;
    
    IF row_count = 0 THEN
        RAISE NOTICE '✅ TEST PASSED: Tenant A cannot delete Tenant B profiles (0 rows deleted)';
    ELSE
        RAISE NOTICE '❌ TEST FAILED: Tenant A can delete Tenant B profiles (% rows deleted)', row_count;
    END IF;
    
    -- Reset context
    RESET app.current_tenant_id;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 5: DAG_TASKS TABLE - TENANT A CANNOT READ TENANT B
-- ══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    row_count INTEGER;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dag_tasks') THEN
        -- Set context to tenant_A
        SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
        
        -- Try to read tenant_B's dag_tasks
        SELECT COUNT(*) INTO row_count
        FROM dag_tasks
        WHERE tenant_id = '00000000-0000-0000-0000-000000000002';
        
        IF row_count = 0 THEN
            RAISE NOTICE '✅ TEST PASSED: Tenant A cannot read Tenant B dag_tasks (0 rows)';
        ELSE
            RAISE NOTICE '❌ TEST FAILED: Tenant A can read Tenant B dag_tasks (% rows)', row_count;
        END IF;
        
        -- Reset context
        RESET app.current_tenant_id;
    ELSE
        RAISE NOTICE '⚠️ TEST SKIPPED: dag_tasks table does not exist';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 6: DAG_TASKS TABLE - TENANT A CANNOT INSERT TENANT B
-- ══════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dag_tasks') THEN
        -- Set context to tenant_A
        SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
        
        -- Try to insert with tenant_B's tenant_id
        BEGIN
            INSERT INTO dag_tasks (task_id, tenant_id, status, priority, dag_config, created_at)
            VALUES (
                gen_random_uuid(),
                '00000000-0000-0000-0000-000000000002',
                'pending',
                5,
                '{"nodes": [], "edges": []}'::jsonb,
                CURRENT_TIMESTAMP
            );
            RAISE NOTICE '❌ TEST FAILED: Tenant A can insert Tenant B dag_tasks';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE '✅ TEST PASSED: Tenant A cannot insert Tenant B dag_tasks (RLS violation)';
        END;
        
        -- Reset context
        RESET app.current_tenant_id;
    ELSE
        RAISE NOTICE '⚠️ TEST SKIPPED: dag_tasks table does not exist';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 7: EXECUTION_RECORDS TABLE - TENANT A CANNOT READ TENANT B
-- ══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    row_count INTEGER;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'execution_records') THEN
        -- Set context to tenant_A
        SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
        
        -- Try to read tenant_B's execution_records
        SELECT COUNT(*) INTO row_count
        FROM execution_records
        WHERE tenant_id = '00000000-0000-0000-0000-000000000002';
        
        IF row_count = 0 THEN
            RAISE NOTICE '✅ TEST PASSED: Tenant A cannot read Tenant B execution_records (0 rows)';
        ELSE
            RAISE NOTICE '❌ TEST FAILED: Tenant A can read Tenant B execution_records (% rows)', row_count;
        END IF;
        
        -- Reset context
        RESET app.current_tenant_id;
    ELSE
        RAISE NOTICE '⚠️ TEST SKIPPED: execution_records table does not exist';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 8: EXECUTION_RECORDS TABLE - TENANT A CANNOT INSERT TENANT B
-- ══════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'execution_records') THEN
        -- Set context to tenant_A
        SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
        
        -- Try to insert with tenant_B's tenant_id
        BEGIN
            INSERT INTO execution_records (execution_id, tenant_id, strategy_id, symbol, side, status, created_at, updated_at)
            VALUES (
                'exec_test_tenant_b',
                '00000000-0000-0000-0000-000000000002',
                'test_strategy',
                'BTCUSDT',
                'buy',
                'pending',
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            );
            RAISE NOTICE '❌ TEST FAILED: Tenant A can insert Tenant B execution_records';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE '✅ TEST PASSED: Tenant A cannot insert Tenant B execution_records (RLS violation)';
        END;
        
        -- Reset context
        RESET app.current_tenant_id;
    ELSE
        RAISE NOTICE '⚠️ TEST SKIPPED: execution_records table does not exist';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 9: STRATEGIES TABLE - TENANT A CANNOT READ TENANT B (IF TABLE EXISTS)
-- ══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    row_count INTEGER;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'strategies') THEN
        -- Set context to tenant_A
        SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
        
        -- Try to read tenant_B's strategies
        SELECT COUNT(*) INTO row_count
        FROM strategies
        WHERE tenant_id = '00000000-0000-0000-0000-000000000002';
        
        IF row_count = 0 THEN
            RAISE NOTICE '✅ TEST PASSED: Tenant A cannot read Tenant B strategies (0 rows)';
        ELSE
            RAISE NOTICE '❌ TEST FAILED: Tenant A can read Tenant B strategies (% rows)', row_count;
        END IF;
        
        -- Reset context
        RESET app.current_tenant_id;
    ELSE
        RAISE NOTICE '⚠️ TEST SKIPPED: strategies table does not exist';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 10: ORDERS TABLE - TENANT A CANNOT READ TENANT B (IF TABLE EXISTS)
-- ══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    row_count INTEGER;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'orders') THEN
        -- Set context to tenant_A
        SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
        
        -- Try to read tenant_B's orders
        SELECT COUNT(*) INTO row_count
        FROM orders
        WHERE tenant_id = '00000000-0000-0000-0000-000000000002';
        
        IF row_count = 0 THEN
            RAISE NOTICE '✅ TEST PASSED: Tenant A cannot read Tenant B orders (0 rows)';
        ELSE
            RAISE NOTICE '❌ TEST FAILED: Tenant A can read Tenant B orders (% rows)', row_count;
        END IF;
        
        -- Reset context
        RESET app.current_tenant_id;
    ELSE
        RAISE NOTICE '⚠️ TEST SKIPPED: orders table does not exist';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 11: POSITIONS TABLE - TENANT A CANNOT READ TENANT B (IF TABLE EXISTS)
-- ══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    row_count INTEGER;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'positions') THEN
        -- Set context to tenant_A
        SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
        
        -- Try to read tenant_B's positions
        SELECT COUNT(*) INTO row_count
        FROM positions
        WHERE tenant_id = '00000000-0000-0000-0000-000000000002';
        
        IF row_count = 0 THEN
            RAISE NOTICE '✅ TEST PASSED: Tenant A cannot read Tenant B positions (0 rows)';
        ELSE
            RAISE NOTICE '❌ TEST FAILED: Tenant A can read Tenant B positions (% rows)', row_count;
        END IF;
        
        -- Reset context
        RESET app.current_tenant_id;
    ELSE
        RAISE NOTICE '⚠️ TEST SKIPPED: positions table does not exist';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- TEST 12: EXCHANGE_CREDENTIALS TABLE - TENANT A CANNOT READ TENANT B (IF TABLE EXISTS)
-- ══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    row_count INTEGER;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'exchange_credentials') THEN
        -- Set context to tenant_A
        SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000001';
        
        -- Try to read tenant_B's exchange_credentials
        SELECT COUNT(*) INTO row_count
        FROM exchange_credentials
        WHERE tenant_id = '00000000-0000-0000-0000-000000000002';
        
        IF row_count = 0 THEN
            RAISE NOTICE '✅ TEST PASSED: Tenant A cannot read Tenant B exchange_credentials (0 rows)';
        ELSE
            RAISE NOTICE '❌ TEST FAILED: Tenant A can read Tenant B exchange_credentials (% rows)', row_count;
        END IF;
        
        -- Reset context
        RESET app.current_tenant_id;
    ELSE
        RAISE NOTICE '⚠️ TEST SKIPPED: exchange_credentials table does not exist';
    END IF;
END $$;

-- ══════════════════════════════════════════════════════════════════════════
-- PHASE 4: CLEANUP TEST DATA
-- ══════════════════════════════════════════════════════════════════════════

-- Clean up test profiles (optional - comment out to keep test data)
-- DELETE FROM profiles WHERE id IN ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002');

-- Clean up test dag_tasks (optional - comment out to keep test data)
-- DELETE FROM dag_tasks WHERE tenant_id IN ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002');

-- Clean up test execution_records (optional - comment out to keep test data)
-- DELETE FROM execution_records WHERE execution_id IN ('exec_tenant_a_test', 'exec_tenant_b_test');

-- ══════════════════════════════════════════════════════════════════════════
-- VALIDATION COMPLETE
-- ══════════════════════════════════════════════════════════════════════════
