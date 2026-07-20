-- Migration: 001_create_dag_tasks_table.sql
-- Date: May 1, 2026
-- Description: Production-grade DAG tasks table for durability

-- Create ENUM type for task status
CREATE TYPE task_status AS ENUM (
    'pending',
    'assigned',
    'running',
    'completed',
    'failed',
    'cancelled'
);

-- Main DAG tasks table
CREATE TABLE dag_tasks (
    -- Primary identification
    task_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    
    -- Task status and priority
    status task_status NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 5,
    
    -- DAG configuration and data
    dag_config JSONB NOT NULL,
    progress FLOAT NOT NULL DEFAULT 0.0,
    result JSONB,
    error TEXT,
    
    -- Retry configuration
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    last_heartbeat TIMESTAMP WITH TIME ZONE,
    
    -- Constraints
    CONSTRAINT chk_priority_range CHECK (priority >= 1 AND priority <= 10),
    CONSTRAINT chk_progress_range CHECK (progress >= 0.0 AND progress <= 100.0),
    CONSTRAINT chk_retry_count CHECK (retry_count >= 0),
    CONSTRAINT chk_max_retries CHECK (max_retries >= 0),
    
    -- Ensure completed_at is after started_at
    CONSTRAINT chk_completion_order CHECK (
        completed_at IS NULL OR 
        (started_at IS NOT NULL AND completed_at >= started_at)
    ),
    
    -- Ensure started_at is after created_at
    CONSTRAINT chk_start_order CHECK (
        started_at IS NULL OR 
        started_at >= created_at
    )
);

-- Create indexes for efficient querying
-- Index 1: tenant_id + status (for filtering tasks by tenant and status)
CREATE INDEX idx_dag_tasks_tenant_status 
    ON dag_tasks (tenant_id, status);

-- Index 2: created_at (for sorting and pagination)
CREATE INDEX idx_dag_tasks_created_at 
    ON dag_tasks (created_at DESC);

-- Index 3: status (for status-based filtering)
CREATE INDEX idx_dag_tasks_status 
    ON dag_tasks (status);

-- Index 4: tenant_id + created_at (for tenant task history)
CREATE INDEX idx_dag_tasks_tenant_created 
    ON dag_tasks (tenant_id, created_at DESC);

-- Index 5: status + created_at (for queue processing - pending tasks by age)
CREATE INDEX idx_dag_tasks_status_created 
    ON dag_tasks (status, created_at ASC) 
    WHERE status = 'pending';

-- Index 6: last_heartbeat (for detecting stale tasks)
CREATE INDEX idx_dag_tasks_heartbeat 
    ON dag_tasks (last_heartbeat) 
    WHERE status IN ('assigned', 'running');

-- Index 7: priority (for task ordering within tenant)
CREATE INDEX idx_dag_tasks_priority 
    ON dag_tasks (tenant_id, priority ASC, created_at ASC) 
    WHERE status = 'pending';

-- Index 8: GIN index on dag_config for JSONB queries
CREATE INDEX idx_dag_tasks_config_gin 
    ON dag_tasks USING GIN (dag_config);

-- Index 9: GIN index on result for JSONB queries
CREATE INDEX idx_dag_tasks_result_gin 
    ON dag_tasks USING GIN (result) 
    WHERE result IS NOT NULL;

-- Enable Row-Level Security for multi-tenant isolation
ALTER TABLE dag_tasks ENABLE ROW LEVEL SECURITY;

-- Create RLS policy for tenant isolation
CREATE POLICY dag_tasks_tenant_isolation ON dag_tasks
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Create RLS policy for insert (tenants can only insert their own tasks)
CREATE POLICY dag_tasks_tenant_insert ON dag_tasks
    FOR INSERT 
    WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Create RLS policy for update (tenants can only update their own tasks)
CREATE POLICY dag_tasks_tenant_update ON dag_tasks
    FOR UPDATE
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Create RLS policy for delete (tenants can only delete their own tasks)
CREATE POLICY dag_tasks_tenant_delete ON dag_tasks
    FOR DELETE
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Create function to automatically update last_heartbeat
CREATE OR REPLACE FUNCTION update_task_heartbeat()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_heartbeat = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to update heartbeat on status change to running
CREATE TRIGGER trg_dag_tasks_heartbeat
    BEFORE UPDATE ON dag_tasks
    FOR EACH ROW
    WHEN (NEW.status = 'running'::task_status)
    EXECUTE FUNCTION update_task_heartbeat();

-- Create function to validate status transitions
CREATE OR REPLACE FUNCTION validate_status_transition()
RETURNS TRIGGER AS $$
BEGIN
    -- Define valid status transitions
    IF OLD.status = 'pending' AND NEW.status NOT IN ('assigned', 'cancelled') THEN
        RAISE EXCEPTION 'Invalid transition: pending -> %', NEW.status;
    END IF;
    
    IF OLD.status = 'assigned' AND NEW.status NOT IN ('running', 'cancelled') THEN
        RAISE EXCEPTION 'Invalid transition: assigned -> %', NEW.status;
    END IF;
    
    IF OLD.status = 'running' AND NEW.status NOT IN ('completed', 'failed', 'cancelled') THEN
        RAISE EXCEPTION 'Invalid transition: running -> %', NEW.status;
    END IF;
    
    IF OLD.status IN ('completed', 'failed', 'cancelled') THEN
        RAISE EXCEPTION 'Cannot transition from terminal status: %', OLD.status;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for status transition validation
CREATE TRIGGER trg_dag_tasks_status_validation
    BEFORE UPDATE ON dag_tasks
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION validate_status_transition();

-- Create views for common queries

-- View: Pending tasks queue
CREATE VIEW v_pending_tasks AS
SELECT 
    task_id,
    tenant_id,
    priority,
    dag_config,
    created_at,
    EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - created_at)) as wait_seconds
FROM dag_tasks
WHERE status = 'pending'
ORDER BY tenant_id, priority ASC, created_at ASC;

-- View: Running tasks with heartbeat status
CREATE VIEW v_running_tasks AS
SELECT 
    task_id,
    tenant_id,
    status,
    progress,
    started_at,
    last_heartbeat,
    CASE 
        WHEN last_heartbeat IS NULL THEN 'no_heartbeat'
        WHEN last_heartbeat < CURRENT_TIMESTAMP - INTERVAL '5 minutes' THEN 'stale'
        ELSE 'healthy'
    END as heartbeat_status
FROM dag_tasks
WHERE status IN ('assigned', 'running')
ORDER BY started_at ASC;

-- View: Task statistics by tenant
CREATE VIEW v_tenant_task_stats AS
SELECT 
    tenant_id,
    COUNT(*) FILTER (WHERE status = 'pending') as pending_count,
    COUNT(*) FILTER (WHERE status = 'assigned') as assigned_count,
    COUNT(*) FILTER (WHERE status = 'running') as running_count,
    COUNT(*) FILTER (WHERE status = 'completed') as completed_count,
    COUNT(*) FILTER (WHERE status = 'failed') as failed_count,
    COUNT(*) FILTER (WHERE status = 'cancelled') as cancelled_count,
    COUNT(*) as total_count,
    AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) 
        FILTER (WHERE status = 'completed') as avg_execution_seconds,
    MAX(created_at) as last_task_created
FROM dag_tasks
GROUP BY tenant_id;

-- Add table comment
COMMENT ON TABLE dag_tasks IS 'Production DAG task queue with durability and multi-tenant isolation';

-- Add column comments
COMMENT ON COLUMN dag_tasks.task_id IS 'Unique task identifier (UUID v4)';
COMMENT ON COLUMN dag_tasks.tenant_id IS 'Tenant identifier for multi-tenant isolation';
COMMENT ON COLUMN dag_tasks.status IS 'Current task status (pending, assigned, running, completed, failed, cancelled)';
COMMENT ON COLUMN dag_tasks.priority IS 'Task priority 1-10 (1=highest, 10=lowest)';
COMMENT ON COLUMN dag_tasks.dag_config IS 'JSONB containing DAG nodes, edges, symbols, and execution parameters';
COMMENT ON COLUMN dag_tasks.progress IS 'Execution progress 0.0-100.0';
COMMENT ON COLUMN dag_tasks.result IS 'JSONB containing task execution results';
COMMENT ON COLUMN dag_tasks.error IS 'Error message if task failed';
COMMENT ON COLUMN dag_tasks.retry_count IS 'Number of retry attempts made';
COMMENT ON COLUMN dag_tasks.max_retries IS 'Maximum allowed retry attempts';
COMMENT ON COLUMN dag_tasks.created_at IS 'Task creation timestamp';
COMMENT ON COLUMN dag_tasks.started_at IS 'Task execution start timestamp';
COMMENT ON COLUMN dag_tasks.completed_at IS 'Task completion timestamp';
COMMENT ON COLUMN dag_tasks.last_heartbeat IS 'Last worker heartbeat timestamp for running tasks';
