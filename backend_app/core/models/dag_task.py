"""
core/models/dag_task.py — DAG Task Models.

Pydantic and SQLAlchemy models for the dag_tasks table.
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Text, 
    ForeignKey, Index, Enum as SQLEnum, func, JSON
)

# Import Base from core.database to ensure table creation works
from backend_app.core.database import Base


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════

class TaskStatus(str, Enum):
    """Task status enumeration."""
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskPriority(int, Enum):
    """Task priority enumeration."""
    CRITICAL = 1
    HIGH = 3
    NORMAL = 5
    LOW = 7
    BACKGROUND = 10


# ═══════════════════════════════════════════════════════════════════════════
# SQLALCHEMY ORM MODEL
# ═══════════════════════════════════════════════════════════════════════════

class DAGTaskModel(Base):
    """SQLAlchemy model for dag_tasks table."""
    
    __tablename__ = "dag_tasks"
    
    # Primary identification
    task_id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id = Column(String(36), nullable=False, index=True)
    
    # Task status and priority
    status = Column(
        SQLEnum(TaskStatus, name="task_status", create_type=False),
        nullable=False,
        default=TaskStatus.PENDING
    )
    priority = Column(Integer, nullable=False, default=5)
    
    # DAG configuration and data
    dag_config = Column(JSON, nullable=False)
    progress = Column(Float, nullable=False, default=0.0)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    
    # Retry configuration
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    
    # Timestamps
    created_at = Column(
        DateTime(timezone=True), 
        nullable=False, 
        default=func.now()
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    last_heartbeat = Column(DateTime(timezone=True), nullable=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=True)
    
    # Worker assignment
    worker_id = Column(String(64), nullable=True, index=True)
    
    # Table args for additional indexes (SQLite-compatible)
    __table_args__ = (
        # Composite index: tenant_id + status
        Index("idx_dag_tasks_tenant_status", "tenant_id", "status"),
        # Index on created_at for sorting
        Index("idx_dag_tasks_created_at", "created_at"),
        # Index on status
        Index("idx_dag_tasks_status", "status"),
        # Composite index: tenant_id + created_at
        Index("idx_dag_tasks_tenant_created", "tenant_id", "created_at"),
        # Index for priority-based queue
        Index("idx_dag_tasks_priority", "tenant_id", "priority", "created_at"),
    )
    
    def __repr__(self) -> str:
        return f"<DAGTaskModel(task_id={self.task_id}, status={self.status}, tenant_id={self.tenant_id})>"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        return {
            "task_id": str(self.task_id),
            "tenant_id": str(self.tenant_id),
            "status": self.status.value if isinstance(self.status, TaskStatus) else self.status,
            "priority": self.priority,
            "dag_config": self.dag_config,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
        }


# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════

class DAGConfig(BaseModel):
    """DAG configuration model."""
    model_config = ConfigDict(extra="allow")
    
    nodes: List[Dict[str, Any]] = Field(..., description="DAG nodes")
    edges: List[Dict[str, Any]] = Field(..., description="DAG edges")
    symbols: List[str] = Field(default_factory=list, description="Trading symbols")
    timeframe: Optional[str] = Field(None, description="Data timeframe")
    

class DAGTaskCreate(BaseModel):
    """Model for creating a new DAG task."""
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "dag_config": {
                "nodes": [{"id": "input", "type": "input"}],
                "edges": [],
                "symbols": ["BTCUSDT"],
                "timeframe": "1h"
            },
            "priority": 5,
            "max_retries": 3
        }
    })
    
    dag_config: DAGConfig = Field(..., description="DAG configuration")
    priority: int = Field(default=5, ge=1, le=10, description="Task priority 1-10")
    max_retries: int = Field(default=3, ge=0, le=10, description="Maximum retry attempts")
    scheduled_for: Optional[datetime] = Field(None, description="Scheduled execution time")


class DAGTaskUpdate(BaseModel):
    """Model for updating a DAG task."""
    model_config = ConfigDict(extra="forbid")
    
    status: Optional[TaskStatus] = Field(None, description="Task status")
    progress: Optional[float] = Field(None, ge=0.0, le=100.0, description="Execution progress")
    result: Optional[Dict[str, Any]] = Field(None, description="Execution result")
    error: Optional[str] = Field(None, description="Error message")
    retry_count: Optional[int] = Field(None, ge=0, description="Retry count")
    started_at: Optional[datetime] = Field(None, description="Start timestamp")
    completed_at: Optional[datetime] = Field(None, description="Completion timestamp")
    last_heartbeat: Optional[datetime] = Field(None, description="Last heartbeat")


class DAGTaskResponse(BaseModel):
    """Response model for DAG task."""
    model_config = ConfigDict(from_attributes=True)
    
    task_id: UUID = Field(..., description="Task unique identifier")
    tenant_id: UUID = Field(..., description="Tenant identifier")
    status: TaskStatus = Field(..., description="Current task status")
    priority: int = Field(..., description="Task priority")
    dag_config: DAGConfig = Field(..., description="DAG configuration")
    progress: float = Field(default=0.0, description="Execution progress")
    result: Optional[Dict[str, Any]] = Field(None, description="Execution result")
    error: Optional[str] = Field(None, description="Error message")
    retry_count: int = Field(default=0, description="Number of retries")
    max_retries: int = Field(default=3, description="Maximum retries allowed")
    created_at: datetime = Field(..., description="Creation timestamp")
    started_at: Optional[datetime] = Field(None, description="Start timestamp")
    completed_at: Optional[datetime] = Field(None, description="Completion timestamp")
    last_heartbeat: Optional[datetime] = Field(None, description="Last heartbeat")
    
    @property
    def is_terminal(self) -> bool:
        """Check if task is in terminal state."""
        return self.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]
    
    @property
    def is_active(self) -> bool:
        """Check if task is currently active."""
        return self.status in [TaskStatus.ASSIGNED, TaskStatus.RUNNING]
    
    @property
    def execution_time_seconds(self) -> Optional[float]:
        """Calculate execution time in seconds."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        elif self.started_at:
            return (datetime.utcnow() - self.started_at).total_seconds()
        return None
    
    @property
    def wait_time_seconds(self) -> float:
        """Calculate wait time in queue."""
        if self.started_at:
            return (self.started_at - self.created_at).total_seconds()
        return (datetime.utcnow() - self.created_at).total_seconds()


class DAGTaskListResponse(BaseModel):
    """Response model for list of DAG tasks."""
    tasks: List[DAGTaskResponse]
    total: int
    pending: int
    running: int
    completed: int
    failed: int
    cancelled: int


class DAGTaskStats(BaseModel):
    """Statistics for DAG tasks."""
    tenant_id: UUID
    pending_count: int
    assigned_count: int
    running_count: int
    completed_count: int
    failed_count: int
    cancelled_count: int
    total_count: int
    avg_execution_time_seconds: Optional[float]
    last_task_created: Optional[datetime]


class DAGTaskQueuePosition(BaseModel):
    """Task position in queue."""
    task_id: UUID
    position: int
    estimated_wait_seconds: Optional[int]
    tasks_ahead: int


class DAGTaskHeartbeat(BaseModel):
    """Heartbeat update for running task."""
    task_id: UUID
    progress: float = Field(..., ge=0.0, le=100.0)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class DAGTaskRetryPolicy(BaseModel):
    """Retry policy configuration."""
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_strategy: str = Field(default="exponential", pattern="^(exponential|linear|fixed)$")
    initial_delay_seconds: float = Field(default=1.0, ge=0.0)
    max_delay_seconds: float = Field(default=300.0, ge=0.0)


# ═══════════════════════════════════════════════════════════════════════════
# REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════

from sqlalchemy.orm import Session
from sqlalchemy import and_, desc, asc
from typing import List, Optional


class DAGTaskRepository:
    """Repository for DAG task database operations."""
    
    def __init__(self, session: Session):
        self.session = session
    
    def create(self, task: DAGTaskCreate, tenant_id: UUID) -> DAGTaskModel:
        """Create a new DAG task."""
        db_task = DAGTaskModel(
            task_id=uuid4(),
            tenant_id=tenant_id,
            status=TaskStatus.PENDING,
            priority=task.priority,
            dag_config=task.dag_config.model_dump(),
            max_retries=task.max_retries,
        )
        self.session.add(db_task)
        self.session.commit()
        self.session.refresh(db_task)
        return db_task
    
    def get_by_id(self, task_id: UUID, tenant_id: UUID) -> Optional[DAGTaskModel]:
        """Get task by ID with tenant isolation."""
        return self.session.query(DAGTaskModel).filter(
            and_(
                DAGTaskModel.task_id == task_id,
                DAGTaskModel.tenant_id == tenant_id
            )
        ).first()
    
    def get_by_tenant(
        self, 
        tenant_id: UUID, 
        status: Optional[TaskStatus] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[DAGTaskModel]:
        """Get tasks for tenant with optional status filter."""
        query = self.session.query(DAGTaskModel).filter(
            DAGTaskModel.tenant_id == tenant_id
        )
        
        if status:
            query = query.filter(DAGTaskModel.status == status)
        
        return query.order_by(desc(DAGTaskModel.created_at)).limit(limit).offset(offset).all()
    
    def update(self, task_id: UUID, tenant_id: UUID, updates: DAGTaskUpdate) -> Optional[DAGTaskModel]:
        """Update task with validation."""
        db_task = self.get_by_id(task_id, tenant_id)
        if not db_task:
            return None
        
        update_data = updates.model_dump(exclude_unset=True)
        
        for key, value in update_data.items():
            if value is not None:
                setattr(db_task, key, value)
        
        self.session.commit()
        self.session.refresh(db_task)
        return db_task
    
    def update_status(
        self, 
        task_id: UUID, 
        tenant_id: UUID, 
        status: TaskStatus,
        **kwargs
    ) -> Optional[DAGTaskModel]:
        """Update task status with related fields."""
        db_task = self.get_by_id(task_id, tenant_id)
        if not db_task:
            return None
        
        db_task.status = status
        
        if status == TaskStatus.RUNNING and not db_task.started_at:
            db_task.started_at = datetime.utcnow()
        
        if status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            db_task.completed_at = datetime.utcnow()
        
        for key, value in kwargs.items():
            if hasattr(db_task, key):
                setattr(db_task, key, value)
        
        self.session.commit()
        self.session.refresh(db_task)
        return db_task
    
    def update_heartbeat(self, task_id: UUID, tenant_id: UUID, progress: float) -> bool:
        """Update task heartbeat and progress."""
        db_task = self.get_by_id(task_id, tenant_id)
        if not db_task:
            return False
        
        db_task.last_heartbeat = datetime.utcnow()
        db_task.progress = progress
        
        self.session.commit()
        return True
    
    def delete(self, task_id: UUID, tenant_id: UUID) -> bool:
        """Delete task."""
        db_task = self.get_by_id(task_id, tenant_id)
        if not db_task:
            return False
        
        self.session.delete(db_task)
        self.session.commit()
        return True
    
    def get_pending_tasks(self, tenant_id: UUID, limit: int = 10) -> List[DAGTaskModel]:
        """Get pending tasks for processing."""
        return self.session.query(DAGTaskModel).filter(
            and_(
                DAGTaskModel.tenant_id == tenant_id,
                DAGTaskModel.status == TaskStatus.PENDING
            )
        ).order_by(
            asc(DAGTaskModel.priority),
            asc(DAGTaskModel.created_at)
        ).limit(limit).all()
    
    def get_stale_tasks(self, threshold_minutes: int = 5) -> List[DAGTaskModel]:
        """Get tasks with stale heartbeat."""
        threshold = datetime.utcnow() - timedelta(minutes=threshold_minutes)
        
        return self.session.query(DAGTaskModel).filter(
            and_(
                DAGTaskModel.status.in_([TaskStatus.ASSIGNED, TaskStatus.RUNNING]),
                DAGTaskModel.last_heartbeat < threshold
            )
        ).all()
    
    def get_stats(self, tenant_id: UUID) -> DAGTaskStats:
        """Get task statistics for tenant."""
        from sqlalchemy import func
        
        result = self.session.query(
            func.count().filter(DAGTaskModel.status == TaskStatus.PENDING).label('pending'),
            func.count().filter(DAGTaskModel.status == TaskStatus.ASSIGNED).label('assigned'),
            func.count().filter(DAGTaskModel.status == TaskStatus.RUNNING).label('running'),
            func.count().filter(DAGTaskModel.status == TaskStatus.COMPLETED).label('completed'),
            func.count().filter(DAGTaskModel.status == TaskStatus.FAILED).label('failed'),
            func.count().filter(DAGTaskModel.status == TaskStatus.CANCELLED).label('cancelled'),
            func.count().label('total'),
            func.avg(func.extract('epoch', DAGTaskModel.completed_at - DAGTaskModel.started_at))
                .filter(DAGTaskModel.status == TaskStatus.COMPLETED).label('avg_execution'),
            func.max(DAGTaskModel.created_at).label('last_created')
        ).filter(
            DAGTaskModel.tenant_id == tenant_id
        ).first()
        
        return DAGTaskStats(
            tenant_id=tenant_id,
            pending_count=result.pending or 0,
            assigned_count=result.assigned or 0,
            running_count=result.running or 0,
            completed_count=result.completed or 0,
            failed_count=result.failed or 0,
            cancelled_count=result.cancelled or 0,
            total_count=result.total or 0,
            avg_execution_time_seconds=float(result.avg_execution) if result.avg_execution else None,
            last_task_created=result.last_created
        )
    
    def cleanup_old_tasks(self, tenant_id: UUID, days: int = 30) -> int:
        """Delete old completed/failed/cancelled tasks."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        result = self.session.query(DAGTaskModel).filter(
            and_(
                DAGTaskModel.tenant_id == tenant_id,
                DAGTaskModel.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]),
                DAGTaskModel.completed_at < cutoff
            )
        ).delete(synchronize_session=False)
        
        self.session.commit()
        return result


from datetime import timedelta
