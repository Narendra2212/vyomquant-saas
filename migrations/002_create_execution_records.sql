-- Migration: Create execution_records table for strategy execution tracking
-- Created: 2026-05-01

-- ═══════════════════════════════════════════════════════════════════════════════
-- 1. CREATE ENUM TYPE FOR STATUS
-- ═══════════════════════════════════════════════════════════════════════════════
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'execution_status') THEN
        CREATE TYPE execution_status AS ENUM (
            'pending',     -- Waiting to be executed
            'executing',   -- Currently being executed
            'completed',   -- Successfully completed
            'failed'       -- Execution failed
        );
    END IF;
END $$;

-- ═══════════════════════════════════════════════════════════════════════════════
-- 2. CREATE EXECUTION_RECORDS TABLE
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS execution_records (
    -- Primary key
    execution_id TEXT PRIMARY KEY,
    
    -- Tenant isolation
    tenant_id UUID NOT NULL,
    
    -- Relationships
    task_id UUID,  -- References dag_tasks (nullable for standalone executions)
    strategy_id TEXT NOT NULL,
    
    -- Execution details
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    status execution_status NOT NULL DEFAULT 'pending',
    
    -- External references
    order_id TEXT,  -- Exchange order ID (nullable)
    
    -- Results
    result JSONB,  -- Execution result data
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    CONSTRAINT execution_records_symbol_check CHECK (symbol <> ''),
    CONSTRAINT execution_records_strategy_id_check CHECK (strategy_id <> '')
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 3. CREATE INDEXES
-- ═══════════════════════════════════════════════════════════════════════════════

-- Index on tenant_id + symbol (common query pattern)
CREATE INDEX IF NOT EXISTS idx_execution_records_tenant_symbol 
    ON execution_records (tenant_id, symbol);

-- Index on task_id (join with dag_tasks)
CREATE INDEX IF NOT EXISTS idx_execution_records_task_id 
    ON execution_records (task_id);

-- Index on status (filter by status)
CREATE INDEX IF NOT EXISTS idx_execution_records_status 
    ON execution_records (status);

-- Index on tenant_id + status (dashboard queries)
CREATE INDEX IF NOT EXISTS idx_execution_records_tenant_status 
    ON execution_records (tenant_id, status);

-- Index on created_at (time-based queries)
CREATE INDEX IF NOT EXISTS idx_execution_records_created_at 
    ON execution_records (created_at DESC);

-- Partial index for pending/executing (active executions)
CREATE INDEX IF NOT EXISTS idx_execution_records_active 
    ON execution_records (tenant_id, created_at DESC) 
    WHERE status IN ('pending', 'executing');

-- ═══════════════════════════════════════════════════════════════════════════════
-- 4. ROW-LEVEL SECURITY (RLS) POLICIES
-- ═══════════════════════════════════════════════════════════════════════════════

-- Enable RLS
ALTER TABLE execution_records ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only see their own tenant's executions
CREATE POLICY execution_records_tenant_isolation_policy ON execution_records
    FOR ALL
    USING (tenant_id::TEXT = current_setting('app.current_tenant_id', true));

-- ═══════════════════════════════════════════════════════════════════════════════
-- 5. TRIGGERS
-- ═══════════════════════════════════════════════════════════════════════════════

-- Trigger: Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_execution_records_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER execution_records_updated_at_trigger
    BEFORE UPDATE ON execution_records
    FOR EACH ROW
    EXECUTE FUNCTION update_execution_records_updated_at();

-- Trigger: Log status transitions
CREATE OR REPLACE FUNCTION log_execution_status_transition()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        -- Log the transition (optional - could write to audit table)
        RAISE NOTICE 'Execution % status changed: % -> %', 
            NEW.execution_id, OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER execution_records_status_transition_trigger
    BEFORE UPDATE ON execution_records
    FOR EACH ROW
    EXECUTE FUNCTION log_execution_status_transition();

-- ═══════════════════════════════════════════════════════════════════════════════
-- 6. VIEWS FOR COMMON QUERIES
-- ═══════════════════════════════════════════════════════════════════════════════

-- View: Active executions (pending or executing)
CREATE OR REPLACE VIEW active_executions AS
SELECT 
    execution_id,
    tenant_id,
    task_id,
    strategy_id,
    symbol,
    side,
    status,
    order_id,
    created_at,
    updated_at,
    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - created_at))::INTEGER as duration_seconds
FROM execution_records
WHERE status IN ('pending', 'executing');

-- View: Executions by tenant summary
CREATE OR REPLACE VIEW execution_stats_by_tenant AS
SELECT 
    tenant_id,
    COUNT(*) FILTER (WHERE status = 'pending') as pending_count,
    COUNT(*) FILTER (WHERE status = 'executing') as executing_count,
    COUNT(*) FILTER (WHERE status = 'completed') as completed_count,
    COUNT(*) FILTER (WHERE status = 'failed') as failed_count,
    COUNT(*) as total_count,
    MAX(created_at) as last_execution_at
FROM execution_records
GROUP BY tenant_id;

-- ═══════════════════════════════════════════════════════════════════════════════
-- 7. GRANTS
-- ═══════════════════════════════════════════════════════════════════════════════

-- Grant permissions to authenticated users
GRANT SELECT, INSERT, UPDATE ON execution_records TO authenticated;
GRANT SELECT, INSERT, UPDATE ON active_executions TO authenticated;
GRANT SELECT ON execution_stats_by_tenant TO authenticated;

-- Grant sequence permissions if any
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════════
-- MIGRATION COMPLETE
-- ═══════════════════════════════════════════════════════════════════════════════
