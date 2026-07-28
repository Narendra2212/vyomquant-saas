-- Migration: Create execution_records table for strategy execution tracking
-- Created: 2026-05-01

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
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT execution_records_symbol_check CHECK (symbol <> ''),
    CONSTRAINT execution_records_strategy_id_check CHECK (strategy_id <> '')
);

CREATE INDEX IF NOT EXISTS idx_execution_records_tenant_symbol 
    ON execution_records (tenant_id, symbol);

CREATE INDEX IF NOT EXISTS idx_execution_records_task_id 
    ON execution_records (task_id);

CREATE INDEX IF NOT EXISTS idx_execution_records_status 
    ON execution_records (status);

CREATE INDEX IF NOT EXISTS idx_execution_records_tenant_status 
    ON execution_records (tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_execution_records_created_at 
    ON execution_records (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_execution_records_active 
    ON execution_records (tenant_id, created_at DESC) 
    WHERE status IN ('pending', 'executing');

ALTER TABLE execution_records ENABLE ROW LEVEL SECURITY;

CREATE POLICY execution_records_tenant_isolation_policy ON execution_records
    FOR ALL
    USING (tenant_id::TEXT = current_setting('app.current_tenant_id', true));

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

CREATE OR REPLACE FUNCTION log_execution_status_transition()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
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
