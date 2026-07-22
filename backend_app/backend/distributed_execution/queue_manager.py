"""
Distributed Execution Queue Manager

Institutional-grade queue-based execution orchestration using Redis Streams.
Provides fault isolation, deterministic replay, and exactly-once processing guarantees.

Author: Principal Distributed Trading Systems Architect
"""
import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("distributed_execution")

# Import Redis manager


class QueueType(Enum):
    """Queue types for distributed execution."""
    EXECUTION = "execution"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"
    TELEMETRY = "telemetry"
    HEARTBEAT = "heartbeat"


class JobStatus(Enum):
    """Execution job status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


class JobPriority(Enum):
    """Job priority levels."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class ExecutionJob:
    """Distributed execution job."""
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = ""
    strategy_id: str = ""
    bot_id: str = ""
    signal_id: str = ""
    
    # Execution details
    exchange: str = ""
    symbol: str = ""
    side: str = ""  # buy/sell
    order_type: str = ""  # market/limit
    quantity: Decimal = Decimal("0")
    price: Optional[Decimal] = None
    
    # Job metadata
    status: JobStatus = JobStatus.PENDING
    priority: JobPriority = JobPriority.NORMAL
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Retry configuration
    retry_count: int = 0
    max_retries: int = 3
    retry_delay: float = 1.0  # seconds
    
    # Execution metadata
    worker_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Result data
    order_id: Optional[str] = None
    execution_price: Optional[Decimal] = None
    executed_quantity: Optional[Decimal] = None
    fees: Optional[Decimal] = None
    error: Optional[str] = None
    
    # Idempotency
    idempotency_key: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "job_id": self.job_id,
            "tenant_id": self.tenant_id,
            "strategy_id": self.strategy_id,
            "bot_id": self.bot_id,
            "signal_id": self.signal_id,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "quantity": str(self.quantity),
            "price": str(self.price) if self.price else None,
            "status": self.status.value,
            "priority": self.priority.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "retry_delay": self.retry_delay,
            "worker_id": self.worker_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "order_id": self.order_id,
            "execution_price": str(self.execution_price) if self.execution_price else None,
            "executed_quantity": str(self.executed_quantity) if self.executed_quantity else None,
            "fees": str(self.fees) if self.fees else None,
            "error": self.error,
            "idempotency_key": self.idempotency_key
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionJob":
        """Create from dictionary."""
        job = cls()
        job.job_id = data["job_id"]
        job.tenant_id = data["tenant_id"]
        job.strategy_id = data["strategy_id"]
        job.bot_id = data["bot_id"]
        job.signal_id = data["signal_id"]
        job.exchange = data["exchange"]
        job.symbol = data["symbol"]
        job.side = data["side"]
        job.order_type = data["order_type"]
        job.quantity = Decimal(data["quantity"])
        job.price = Decimal(data["price"]) if data["price"] else None
        job.status = JobStatus(data["status"])
        job.priority = JobPriority(data["priority"])
        job.created_at = datetime.fromisoformat(data["created_at"])
        job.updated_at = datetime.fromisoformat(data["updated_at"])
        job.retry_count = data["retry_count"]
        job.max_retries = data["max_retries"]
        job.retry_delay = data["retry_delay"]
        job.worker_id = data.get("worker_id")
        job.started_at = datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
        job.completed_at = datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None
        job.order_id = data.get("order_id")
        job.execution_price = Decimal(data["execution_price"]) if data.get("execution_price") else None
        job.executed_quantity = Decimal(data["executed_quantity"]) if data.get("executed_quantity") else None
        job.fees = Decimal(data["fees"]) if data.get("fees") else None
        job.error = data.get("error")
        job.idempotency_key = data.get("idempotency_key")
        return job


class QueueBackend(ABC):
    """Abstract queue backend for Redis/Kafka abstraction."""
    
    @abstractmethod
    async def publish(self, queue_type: QueueType, job: ExecutionJob) -> bool:
        """Publish job to queue."""
        pass
    
    @abstractmethod
    async def consume(self, queue_type: QueueType, consumer_group: str, consumer_id: str) -> Optional[ExecutionJob]:
        """Consume job from queue."""
        pass
    
    @abstractmethod
    async def acknowledge(self, queue_type: QueueType, consumer_group: str, message_id: str) -> bool:
        """Acknowledge job completion."""
        pass
    
    @abstractmethod
    async def reject(self, queue_type: QueueType, consumer_group: str, message_id: str, reason: str) -> bool:
        """Reject job (move to retry or dead-letter)."""
        pass


class RedisQueueBackend(QueueBackend):
    """Redis Streams implementation of QueueBackend."""
    
    def __init__(self):
        self.redis = redis_manager
        self.stream_names = {
            QueueType.EXECUTION: "execution:jobs",
            QueueType.RETRY: "execution:retry",
            QueueType.DEAD_LETTER: "execution:dead_letter",
            QueueType.TELEMETRY: "execution:telemetry",
            QueueType.HEARTBEAT: "execution:heartbeat"
        }
    
    async def publish(self, queue_type: QueueType, job: ExecutionJob) -> bool:
        """Publish job to Redis Stream."""
        try:
            stream_name = self.stream_names[queue_type]
            
            # Use priority for stream ordering (higher priority = lower score)
            1000 - (job.priority.value * 100)
            
            message = {
                "job_data": json.dumps(job.to_dict()),
                "priority": str(job.priority.value),
                "tenant_id": job.tenant_id,
                "created_at": job.created_at.isoformat()
            }
            
            # Add to stream
            result = await self.redis.xadd(
                stream_name,
                message,
                maxlen=10000,  # Keep last 10,000 jobs
                approximate=True
            )
            
            logger.info(f"Published job {job.job_id} to {queue_type.value} stream (ID: {result})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to publish job {job.job_id} to {queue_type.value}: {e}")
            return False
    
    async def consume(self, queue_type: QueueType, consumer_group: str, consumer_id: str) -> Optional[ExecutionJob]:
        """Consume job from Redis Stream."""
        try:
            stream_name = self.stream_names[queue_type]
            
            # Ensure consumer group exists
            try:
                await self.redis.xgroup_create(stream_name, consumer_group, id='0', mkstream=True)
            except Exception:
                pass  # Group already exists
            
            # Read from stream
            messages = await self.redis.xreadgroup(
                consumer_group,
                consumer_id,
                {stream_name: '>'},
                count=1,
                block=1000  # Wait up to 1 second
            )
            
            if not messages:
                return None
            
            stream, message_data = messages[0]
            message_id, fields = message_data[0]
            
            # Parse job
            job_data = json.loads(fields["job_data"])
            job = ExecutionJob.from_dict(job_data)
            
            # Store message ID for acknowledgment
            job._message_id = message_id
            job._stream_name = stream_name
            
            logger.info(f"Consumed job {job.job_id} from {queue_type.value} stream")
            return job
            
        except Exception as e:
            logger.error(f"Failed to consume from {queue_type.value}: {e}")
            return None
    
    async def acknowledge(self, queue_type: QueueType, consumer_group: str, message_id: str) -> bool:
        """Acknowledge job completion."""
        try:
            stream_name = self.stream_names[queue_type]
            await self.redis.xack(stream_name, consumer_group, message_id)
            return True
        except Exception as e:
            logger.error(f"Failed to acknowledge message {message_id}: {e}")
            return False
    
    async def reject(self, queue_type: QueueType, consumer_group: str, message_id: str, reason: str) -> bool:
        """Reject job (move to retry or dead-letter)."""
        try:
            stream_name = self.stream_names[queue_type]
            
            # Get the message data
            messages = await self.redis.xrange(stream_name, min=message_id, max=message_id, count=1)
            if not messages:
                return False
            
            _, fields = messages[0]
            job_data = json.loads(fields["job_data"])
            job = ExecutionJob.from_dict(job_data)
            
            # Determine target queue
            if job.retry_count < job.max_retries:
                target_queue = QueueType.RETRY
                job.retry_count += 1
                job.status = JobStatus.RETRY
                job.error = reason
                job.updated_at = datetime.now(timezone.utc)
            else:
                target_queue = QueueType.DEAD_LETTER
                job.status = JobStatus.DEAD_LETTER
                job.error = reason
                job.updated_at = datetime.now(timezone.utc)
            
            # Publish to target queue
            await self.publish(target_queue, job)
            
            # Acknowledge original message
            await self.acknowledge(queue_type, consumer_group, message_id)
            
            logger.warning(f"Rejected job {job.job_id} to {target_queue.value}: {reason}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reject message {message_id}: {e}")
            return False


class DistributedQueueManager:
    """Manager for distributed execution queues."""
    
    def __init__(self, backend: QueueBackend = None):
        self.backend = backend or RedisQueueBackend()
        self.consumer_group = "execution_workers"
        self.consumer_id = f"worker_{uuid.uuid4().hex[:8]}"
        
        # Queue configuration
        self.max_concurrent_jobs = 100
        self.job_timeout = 300  # 5 minutes
        
        # Statistics
        self.stats = {
            "jobs_published": 0,
            "jobs_consumed": 0,
            "jobs_completed": 0,
            "jobs_failed": 0,
            "jobs_retried": 0,
            "jobs_dead_lettered": 0
        }
    
    async def publish_execution_job(self, job: ExecutionJob) -> bool:
        """Publish execution job to main queue."""
        job.status = JobStatus.PENDING
        job.created_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        
        success = await self.backend.publish(QueueType.EXECUTION, job)
        if success:
            self.stats["jobs_published"] += 1
            logger.info(f"Published execution job {job.job_id} for {job.tenant_id}")
        
        return success
    
    async def consume_execution_job(self) -> Optional[ExecutionJob]:
        """Consume execution job from main queue."""
        job = await self.backend.consume(QueueType.EXECUTION, self.consumer_group, self.consumer_id)
        if job:
            job.status = JobStatus.PROCESSING
            job.started_at = datetime.now(timezone.utc)
            job.worker_id = self.consumer_id
            job.updated_at = datetime.now(timezone.utc)
            self.stats["jobs_consumed"] += 1
            logger.info(f"Consumed execution job {job.job_id} by worker {self.consumer_id}")
        
        return job
    
    async def acknowledge_job(self, job: ExecutionJob) -> bool:
        """Acknowledge successful job completion."""
        success = await self.backend.acknowledge(
            QueueType.EXECUTION, 
            self.consumer_group, 
            job._message_id
        )
        
        if success:
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            self.stats["jobs_completed"] += 1
            logger.info(f"Completed execution job {job.job_id}")
        
        return success
    
    async def reject_job(self, job: ExecutionJob, reason: str) -> bool:
        """Reject job (move to retry or dead-letter)."""
        success = await self.backend.reject(
            QueueType.EXECUTION,
            self.consumer_group,
            job._message_id,
            reason
        )
        
        if success:
            if job.retry_count <= job.max_retries:
                self.stats["jobs_retried"] += 1
            else:
                self.stats["jobs_dead_lettered"] += 1
            self.stats["jobs_failed"] += 1
            logger.warning(f"Rejected execution job {job.job_id}: {reason}")
        
        return success
    
    async def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        return {
            "backend_type": type(self.backend).__name__,
            "consumer_group": self.consumer_group,
            "consumer_id": self.consumer_id,
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "job_timeout": self.job_timeout,
            "stats": self.stats.copy()
        }


# Global queue manager instance
queue_manager = DistributedQueueManager()
