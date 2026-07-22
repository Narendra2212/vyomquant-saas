"""
Distributed Execution Package

Institutional-grade distributed execution architecture with:
- Queue-based orchestration
- Isolated execution workers
- Fault isolation and recovery
- Deterministic replay
- Exactly-once processing guarantees

Author: Principal Distributed Trading Systems Architect
"""

from .execution_worker import (ExchangeGateway, ExecutionWorker,
                               IdempotencyManager, WorkerPool, WorkerState)
from .job_persistence import JobPersistence, job_persistence
from .orchestrator import ExecutionOrchestrator, execution_orchestrator
from .queue_manager import (DistributedQueueManager, ExecutionJob, JobPriority,
                            JobStatus, QueueBackend, QueueType,
                            RedisQueueBackend, queue_manager)

__all__ = [
    # Queue Management
    "QueueType", "JobStatus", "JobPriority", "ExecutionJob",
    "QueueBackend", "RedisQueueBackend", "DistributedQueueManager",
    "queue_manager",
    
    # Execution Workers
    "WorkerState", "ExchangeGateway", "IdempotencyManager",
    "ExecutionWorker", "WorkerPool",
    
    # Job Persistence
    "JobPersistence", "job_persistence",
    
    # Orchestrator
    "ExecutionOrchestrator", "execution_orchestrator"
]
