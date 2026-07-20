# DAG Tasks Table Migration Guide

**Date:** May 1, 2026

---

## Overview

Production-grade PostgreSQL table `dag_tasks` for durable DAG task storage with full multi-tenant isolation.

---

## Table Schema

### Columns

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `task_id` | `UUID` | `PRIMARY KEY`, `gen_random_uuid()` | Unique task identifier |
| `tenant_id` | `UUID` | `NOT NULL`, indexed | Multi-tenant isolation key |
| `status` | `ENUM` | `NOT NULL DEFAULT 'pending'` | Task lifecycle status |
| `priority` | `INTEGER` | `NOT NULL DEFAULT 5`, `CHECK (1-10)` | 1=highest, 10=lowest |
| `dag_config` | `JSONB` | `NOT NULL` | DAG nodes, edges, symbols |
| `progress` | `FLOAT` | `NOT NULL DEFAULT 0`, `CHECK (0-100)` | Execution progress % |
| `result` | `JSONB` | Nullable | Execution results |
| `error` | `TEXT` | Nullable | Error message on failure |
| `retry_count` | `INTEGER` | `NOT NULL DEFAULT 0` | Current retry attempts |
| `max_retries` | `INTEGER` | `NOT NULL DEFAULT 3` | Max allowed retries |
| `created_at` | `TIMESTAMP TZ` | `NOT NULL DEFAULT CURRENT_TIMESTAMP` | Creation time |
| `started_at` | `TIMESTAMP TZ` | Nullable | Execution start time |
| `completed_at` | `TIMESTAMP TZ` | Nullable | Completion time |
| `last_heartbeat` | `TIMESTAMP TZ` | Nullable | Worker heartbeat |

### ENUM Type: `task_status`

```sql
CREATE TYPE task_status AS ENUM (
    'pending',      -- Waiting in queue
    'assigned',     -- Claimed by worker
    'running',      -- Currently executing
    'completed',    -- Successfully finished
    'failed',       -- Execution failed
    'cancelled'     -- Manually cancelled
);
```

---

## Indexes

| Index Name | Columns | Type | Purpose |
|------------|---------|------|---------|
| `idx_dag_tasks_tenant_status` | `tenant_id`, `status` | B-tree | Filter by tenant and status |
| `idx_dag_tasks_created_at` | `created_at DESC` | B-tree | Sort by creation time |
| `idx_dag_tasks_status` | `status` | B-tree | Filter by status |
| `idx_dag_tasks_tenant_created` | `tenant_id`, `created_at DESC` | B-tree | Tenant task history |
| `idx_dag_tasks_status_created` | `status`, `created_at ASC` | B-tree (partial) | Pending queue ordering |
| `idx_dag_tasks_heartbeat` | `last_heartbeat` | B-tree (partial) | Stale task detection |
| `idx_dag_tasks_priority` | `tenant_id`, `priority ASC`, `created_at ASC` | B-tree (partial) | Priority queue |
| `idx_dag_tasks_config_gin` | `dag_config` | GIN | JSONB queries |
| `idx_dag_tasks_result_gin` | `result` | GIN (partial) | Result queries |

---

## Constraints

### Check Constraints

```sql
-- Priority range (1-10)
CHECK (priority >= 1 AND priority <= 10)

-- Progress range (0.0-100.0)
CHECK (progress >= 0.0 AND progress <= 100.0)

-- Non-negative retry counts
CHECK (retry_count >= 0)
CHECK (max_retries >= 0)

-- Completion order validation
CHECK (completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at))

-- Start order validation
CHECK (started_at IS NULL OR started_at >= created_at)
```

### Status Transition Validation

```sql
-- Valid transitions enforced by trigger:
PENDING    → ASSIGNED, CANCELLED
ASSIGNED   → RUNNING, CANCELLED
RUNNING    → COMPLETED, FAILED, CANCELLED
TERMINAL   → (no transitions allowed)
```

---

## Row-Level Security (RLS)

### Policies

```sql
-- Enable RLS
ALTER TABLE dag_tasks ENABLE ROW LEVEL SECURITY;

-- Tenant isolation (all operations)
CREATE POLICY dag_tasks_tenant_isolation ON dag_tasks
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Tenant insert restriction
CREATE POLICY dag_tasks_tenant_insert ON dag_tasks
    FOR INSERT WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Tenant update restriction
CREATE POLICY dag_tasks_tenant_update ON dag_tasks
    FOR UPDATE USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- Tenant delete restriction
CREATE POLICY dag_tasks_tenant_delete ON dag_tasks
    FOR DELETE USING (tenant_id = current_setting('app.current_tenant_id')::UUID);
```

---

## Triggers

### 1. Heartbeat Auto-Update

```sql
-- Updates last_heartbeat when status becomes RUNNING
CREATE TRIGGER trg_dag_tasks_heartbeat
    BEFORE UPDATE ON dag_tasks
    FOR EACH ROW
    WHEN (NEW.status = 'running'::task_status)
    EXECUTE FUNCTION update_task_heartbeat();
```

### 2. Status Transition Validation

```sql
-- Enforces valid status transitions
CREATE TRIGGER trg_dag_tasks_status_validation
    BEFORE UPDATE ON dag_tasks
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION validate_status_transition();
```

---

## Views

### 1. Pending Tasks Queue

```sql
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
```

### 2. Running Tasks with Heartbeat Status

```sql
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
```

### 3. Tenant Statistics

```sql
CREATE VIEW v_tenant_task_stats AS
SELECT 
    tenant_id,
    COUNT(*) FILTER (WHERE status = 'pending') as pending_count,
    COUNT(*) FILTER (WHERE status = 'running') as running_count,
    COUNT(*) FILTER (WHERE status = 'completed') as completed_count,
    COUNT(*) FILTER (WHERE status = 'failed') as failed_count,
    COUNT(*) as total_count,
    AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) 
        FILTER (WHERE status = 'completed') as avg_execution_seconds
FROM dag_tasks
GROUP BY tenant_id;
```

---

## Migration Script

### Run Migration

```bash
# Using psql
psql -U your_user -d your_database -f migrations/001_create_dag_tasks_table.sql

# Using Alembic (if using SQLAlchemy migrations)
alembic upgrade head
```

### Verify Migration

```sql
-- Check table exists
\dt dag_tasks

-- Check indexes
\di idx_dag_tasks*

-- Check constraints
\d dag_tasks

-- Check RLS policies
\dP dag_tasks

-- Test RLS (should return 0 rows without tenant context)
SELECT COUNT(*) FROM dag_tasks;

-- Test with tenant context
SET app.current_tenant_id = 'your-tenant-uuid';
SELECT COUNT(*) FROM dag_tasks;
```

---

## Pydantic Models

### 1. DAGTaskCreate

```python
class DAGTaskCreate(BaseModel):
    dag_config: DAGConfig
    priority: int = Field(default=5, ge=1, le=10)
    max_retries: int = Field(default=3, ge=0, le=10)
    scheduled_for: Optional[datetime] = None
```

### 2. DAGTaskUpdate

```python
class DAGTaskUpdate(BaseModel):
    status: Optional[TaskStatus] = None
    progress: Optional[float] = Field(None, ge=0.0, le=100.0)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retry_count: Optional[int] = Field(None, ge=0)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_heartbeat: Optional[datetime] = None
```

### 3. DAGTaskResponse

```python
class DAGTaskResponse(BaseModel):
    task_id: UUID
    tenant_id: UUID
    status: TaskStatus
    priority: int
    dag_config: DAGConfig
    progress: float
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    retry_count: int
    max_retries: int
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    last_heartbeat: Optional[datetime]
    
    @property
    def is_terminal(self) -> bool: ...
    
    @property
    def execution_time_seconds(self) -> Optional[float]: ...
```

---

## SQLAlchemy ORM Model

```python
class DAGTaskModel(Base):
    __tablename__ = "dag_tasks"
    
    task_id = Column(SQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(SQLUUID(as_uuid=True), nullable=False, index=True)
    status = Column(SQLEnum(TaskStatus), nullable=False, default=TaskStatus.PENDING)
    priority = Column(Integer, nullable=False, default=5)
    dag_config = Column(JSONB, nullable=False)
    progress = Column(Float, nullable=False, default=0.0)
    result = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    last_heartbeat = Column(DateTime(timezone=True), nullable=True)
```

---

## Repository Pattern

```python
class DAGTaskRepository:
    def __init__(self, session: Session):
        self.session = session
    
    def create(self, task: DAGTaskCreate, tenant_id: UUID) -> DAGTaskModel:
        """Create new task."""
        db_task = DAGTaskModel(...)
        self.session.add(db_task)
        self.session.commit()
        return db_task
    
    def get_by_id(self, task_id: UUID, tenant_id: UUID) -> Optional[DAGTaskModel]:
        """Get task by ID (tenant-scoped)."""
        return self.session.query(DAGTaskModel).filter(
            DAGTaskModel.task_id == task_id,
            DAGTaskModel.tenant_id == tenant_id
        ).first()
    
    def update_status(self, task_id: UUID, tenant_id: UUID, 
                      status: TaskStatus, **kwargs) -> Optional[DAGTaskModel]:
        """Update task status with validation."""
        db_task = self.get_by_id(task_id, tenant_id)
        if not db_task:
            return None
        
        db_task.status = status
        # Auto-set timestamps based on status
        if status == TaskStatus.RUNNING and not db_task.started_at:
            db_task.started_at = datetime.utcnow()
        if status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            db_task.completed_at = datetime.utcnow()
        
        self.session.commit()
        return db_task
    
    def get_pending_tasks(self, tenant_id: UUID, limit: int = 10) -> List[DAGTaskModel]:
        """Get pending tasks for queue processing."""
        return self.session.query(DAGTaskModel).filter(
            DAGTaskModel.tenant_id == tenant_id,
            DAGTaskModel.status == TaskStatus.PENDING
        ).order_by(
            DAGTaskModel.priority.asc(),
            DAGTaskModel.created_at.asc()
        ).limit(limit).all()
    
    def get_stale_tasks(self, threshold_minutes: int = 5) -> List[DAGTaskModel]:
        """Get tasks with stale heartbeat."""
        threshold = datetime.utcnow() - timedelta(minutes=threshold_minutes)
        return self.session.query(DAGTaskModel).filter(
            DAGTaskModel.status.in_([TaskStatus.ASSIGNED, TaskStatus.RUNNING]),
            DAGTaskModel.last_heartbeat < threshold
        ).all()
    
    def get_stats(self, tenant_id: UUID) -> DAGTaskStats:
        """Get task statistics for tenant."""
        # Returns counts by status, avg execution time, etc.
        pass
    
    def cleanup_old_tasks(self, tenant_id: UUID, days: int = 30) -> int:
        """Delete old completed tasks."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        result = self.session.query(DAGTaskModel).filter(
            DAGTaskModel.tenant_id == tenant_id,
            DAGTaskModel.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED]),
            DAGTaskModel.completed_at < cutoff
        ).delete()
        self.session.commit()
        return result
```

---

## Usage Examples

### Create Task

```python
from core.models.dag_task import DAGTaskRepository, DAGTaskCreate
from core.database_session import get_session

async def create_dag_task():
    session = get_session()
    repo = DAGTaskRepository(session)
    
    task_create = DAGTaskCreate(
        dag_config={
            "nodes": [{"id": "rsi", "type": "indicator"}],
            "edges": [],
            "symbols": ["BTCUSDT"],
            "timeframe": "1h"
        },
        priority=3,
        max_retries=3
    )
    
    task = repo.create(task_create, tenant_id=uuid.UUID("tenant-123"))
    return task.task_id
```

### Update Progress

```python
# Update heartbeat and progress
repo.update_heartbeat(
    task_id=task_id,
    tenant_id=tenant_id,
    progress=65.0
)
```

### Query Pending Queue

```python
pending = repo.get_pending_tasks(tenant_id=tenant_id, limit=10)
for task in pending:
    print(f"Task {task.task_id}: priority={task.priority}, wait={task.wait_time_seconds}s")
```

### Handle Stale Tasks

```python
stale_tasks = repo.get_stale_tasks(threshold_minutes=10)
for task in stale_tasks:
    # Mark as failed, trigger retry
    repo.update_status(
        task.task_id,
        task.tenant_id,
        TaskStatus.FAILED,
        error="Worker timeout - no heartbeat"
    )
```

---

## Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `migrations/001_create_dag_tasks_table.sql` | ~250 | PostgreSQL migration |
| `core/models/dag_task.py` | ~450 | Pydantic + SQLAlchemy models |
| `DAG_TASKS_TABLE_MIGRATION.md` | ~500 | Documentation (this file) |

---

## Summary

### ✅ Production Features

- ✅ **UUID Primary Keys** - `gen_random_uuid()`
- ✅ **Multi-tenant Isolation** - `tenant_id` with RLS policies
- ✅ **Status ENUM** - Validated state machine
- ✅ **9 Indexes** - Optimized for common queries
- ✅ **6 Check Constraints** - Data integrity
- ✅ **2 Triggers** - Heartbeat + status validation
- ✅ **3 Views** - Common query patterns
- ✅ **JSONB** - Flexible DAG config and results
- ✅ **Repository Pattern** - Clean database access

**Status: Production-ready DAG tasks table**
