"""
Execution Job Persistence Layer

Institutional-grade job persistence with Redis for durability,
deterministic replay, and audit trail capabilities.

Author: Principal Distributed Trading Systems Architect
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import logging

from .queue_manager import ExecutionJob, JobStatus
from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("job_persistence")


class JobPersistence:
    """Persistent storage for execution jobs with audit trail."""
    
    def __init__(self):
        self.redis = redis_manager
        self.key_prefix = "execution_job:"
        self.audit_prefix = "job_audit:"
        self.index_prefix = "job_index:"
        
        # TTL configuration
        self.job_ttl = 86400 * 30  # 30 days
        self.audit_ttl = 86400 * 90  # 90 days
        self.index_ttl = 86400 * 7  # 7 days
    
    async def save_job(self, job: ExecutionJob) -> bool:
        """Save job to persistent storage."""
        try:
            # Save job data
            job_key = f"{self.key_prefix}{job.job_id}"
            job_data = job.to_dict()
            
            pipe = self.redis.pipeline()
            
            # Save job
            pipe.setex(job_key, self.job_ttl, json.dumps(job_data))
            
            # Update indexes
            await self._update_indexes(pipe, job)
            
            # Save audit trail
            await self._save_audit_trail(pipe, job, "saved")
            
            await pipe.execute()
            
            logger.debug(f"Saved job {job.job_id} to persistent storage")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save job {job.job_id}: {e}")
            return False
    
    async def load_job(self, job_id: str) -> Optional[ExecutionJob]:
        """Load job from persistent storage."""
        try:
            job_key = f"{self.key_prefix}{job_id}"
            job_data = await self.redis.get(job_key)
            
            if not job_data:
                return None
            
            job_dict = json.loads(job_data)
            job = ExecutionJob.from_dict(job_dict)
            
            logger.debug(f"Loaded job {job_id} from persistent storage")
            return job
            
        except Exception as e:
            logger.error(f"Failed to load job {job_id}: {e}")
            return None
    
    async def update_job_status(self, job_id: str, status: JobStatus, 
                              worker_id: Optional[str] = None, 
                              error: Optional[str] = None,
                              result: Optional[Dict[str, Any]] = None) -> bool:
        """Update job status with audit trail."""
        try:
            job = await self.load_job(job_id)
            if not job:
                logger.error(f"Job {job_id} not found for status update")
                return False
            
            # Update job fields
            old_status = job.status
            job.status = status
            job.updated_at = datetime.now(timezone.utc)
            
            if worker_id:
                job.worker_id = worker_id
            
            if error:
                job.error = error
            
            if result:
                job.order_id = result.get("order_id")
                job.execution_price = result.get("execution_price")
                job.executed_quantity = result.get("executed_quantity")
                job.fees = result.get("fees")
            
            if status == JobStatus.PROCESSING:
                job.started_at = datetime.now(timezone.utc)
            elif status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.DEAD_LETTER]:
                job.completed_at = datetime.now(timezone.utc)
            
            # Save updated job
            success = await self.save_job(job)
            
            if success:
                # Save audit trail
                audit_data = {
                    "job_id": job_id,
                    "old_status": old_status.value,
                    "new_status": status.value,
                    "worker_id": worker_id,
                    "error": error,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                await self._save_audit_event(job_id, "status_update", audit_data)
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to update job {job_id} status: {e}")
            return False
    
    async def get_jobs_by_tenant(self, tenant_id: str, 
                                status: Optional[JobStatus] = None,
                                limit: int = 100,
                                offset: int = 0) -> List[ExecutionJob]:
        """Get jobs for a tenant with optional status filter."""
        try:
            index_key = f"{self.index_prefix}tenant:{tenant_id}"
            if status:
                index_key += f":status:{status.value}"
            
            # Get job IDs from index
            job_ids = await self.redis.lrange(index_key, offset, offset + limit - 1)
            
            # Load jobs
            jobs = []
            for job_id in job_ids:
                job = await self.load_job(job_id.decode())
                if job:
                    jobs.append(job)
            
            return jobs
            
        except Exception as e:
            logger.error(f"Failed to get jobs for tenant {tenant_id}: {e}")
            return []
    
    async def get_jobs_by_strategy(self, strategy_id: str, limit: int = 100) -> List[ExecutionJob]:
        """Get jobs for a strategy."""
        try:
            index_key = f"{self.index_prefix}strategy:{strategy_id}"
            job_ids = await self.redis.lrange(index_key, 0, limit - 1)
            
            jobs = []
            for job_id in job_ids:
                job = await self.load_job(job_id.decode())
                if job:
                    jobs.append(job)
            
            return jobs
            
        except Exception as e:
            logger.error(f"Failed to get jobs for strategy {strategy_id}: {e}")
            return []
    
    async def get_jobs_by_status(self, status: JobStatus, limit: int = 100) -> List[ExecutionJob]:
        """Get jobs by status."""
        try:
            index_key = f"{self.index_prefix}status:{status.value}"
            job_ids = await self.redis.lrange(index_key, 0, limit - 1)
            
            jobs = []
            for job_id in job_ids:
                job = await self.load_job(job_id.decode())
                if job:
                    jobs.append(job)
            
            return jobs
            
        except Exception as e:
            logger.error(f"Failed to get jobs by status {status.value}: {e}")
            return []
    
    async def get_job_audit_trail(self, job_id: str) -> List[Dict[str, Any]]:
        """Get audit trail for a job."""
        try:
            audit_key = f"{self.audit_prefix}{job_id}"
            audit_events = await self.redis.lrange(audit_key, 0, -1)
            
            trail = []
            for event in audit_events:
                trail.append(json.loads(event.decode()))
            
            return trail
            
        except Exception as e:
            logger.error(f"Failed to get audit trail for job {job_id}: {e}")
            return []
    
    async def get_job_statistics(self, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """Get job statistics."""
        try:
            stats = {
                "total_jobs": 0,
                "by_status": {},
                "by_exchange": {},
                "by_tenant": {},
                "success_rate": 0.0,
                "avg_execution_time": 0.0
            }
            
            # Get all job IDs from main index
            if tenant_id:
                index_key = f"{self.index_prefix}tenant:{tenant_id}"
            else:
                index_key = f"{self.index_prefix}all"
            
            job_ids = await self.redis.lrange(index_key, 0, -1)
            stats["total_jobs"] = len(job_ids)
            
            # Analyze jobs
            completed_count = 0
            total_execution_time = 0
            
            for job_id in job_ids:
                job = await self.load_job(job_id.decode())
                if not job:
                    continue
                
                # Status stats
                status = job.status.value
                stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
                
                # Exchange stats
                exchange = job.exchange
                stats["by_exchange"][exchange] = stats["by_exchange"].get(exchange, 0) + 1
                
                # Tenant stats
                tenant = job.tenant_id
                stats["by_tenant"][tenant] = stats["by_tenant"].get(tenant, 0) + 1
                
                # Success rate
                if job.status == JobStatus.COMPLETED:
                    completed_count += 1
                    if job.started_at and job.completed_at:
                        execution_time = (job.completed_at - job.started_at).total_seconds()
                        total_execution_time += execution_time
            
            # Calculate derived stats
            if stats["total_jobs"] > 0:
                stats["success_rate"] = (completed_count / stats["total_jobs"]) * 100
            
            if completed_count > 0:
                stats["avg_execution_time"] = total_execution_time / completed_count
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get job statistics: {e}")
            return {}
    
    async def _update_indexes(self, pipe, job: ExecutionJob):
        """Update search indexes for job."""
        # Tenant index
        tenant_key = f"{self.index_prefix}tenant:{job.tenant_id}"
        pipe.lpush(tenant_key, job.job_id)
        pipe.expire(tenant_key, self.index_ttl)
        
        # Tenant + status index
        tenant_status_key = f"{self.index_prefix}tenant:{job.tenant_id}:status:{job.status.value}"
        pipe.lpush(tenant_status_key, job.job_id)
        pipe.expire(tenant_status_key, self.index_ttl)
        
        # Strategy index
        strategy_key = f"{self.index_prefix}strategy:{job.strategy_id}"
        pipe.lpush(strategy_key, job.job_id)
        pipe.expire(strategy_key, self.index_ttl)
        
        # Status index
        status_key = f"{self.index_prefix}status:{job.status.value}"
        pipe.lpush(status_key, job.job_id)
        pipe.expire(status_key, self.index_ttl)
        
        # Exchange index
        exchange_key = f"{self.index_prefix}exchange:{job.exchange}"
        pipe.lpush(exchange_key, job.job_id)
        pipe.expire(exchange_key, self.index_ttl)
        
        # All jobs index
        all_key = f"{self.index_prefix}all"
        pipe.lpush(all_key, job.job_id)
        pipe.expire(all_key, self.index_ttl)
    
    async def _save_audit_trail(self, pipe, job: ExecutionJob, action: str):
        """Save audit trail entry."""
        audit_key = f"{self.audit_prefix}{job.job_id}"
        audit_data = {
            "action": action,
            "job_data": job.to_dict(),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        pipe.lpush(audit_key, json.dumps(audit_data))
        pipe.expire(audit_key, self.audit_ttl)
    
    async def _save_audit_event(self, job_id: str, event_type: str, data: Dict[str, Any]):
        """Save specific audit event."""
        try:
            audit_key = f"{self.audit_prefix}{job_id}"
            audit_data = {
                "event_type": event_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            await self.redis.lpush(audit_key, json.dumps(audit_data))
            await self.redis.expire(audit_key, self.audit_ttl)
            
        except Exception as e:
            logger.error(f"Failed to save audit event for job {job_id}: {e}")
    
    async def cleanup_expired_jobs(self) -> int:
        """Clean up expired jobs and indexes."""
        try:
            cleaned_count = 0
            
            # Get all job keys
            job_keys = await self.redis.keys(f"{self.key_prefix}*")
            
            for key in job_keys:
                ttl = await self.redis.ttl(key)
                if ttl == -1:  # No TTL set
                    await self.redis.expire(key, self.job_ttl)
                    cleaned_count += 1
            
            logger.info(f"Cleaned up {cleaned_count} expired jobs")
            return cleaned_count
            
        except Exception as e:
            logger.error(f"Failed to cleanup expired jobs: {e}")
            return 0
    
    async def replay_job(self, job_id: str) -> Optional[ExecutionJob]:
        """Replay a job from persistent storage."""
        try:
            job = await self.load_job(job_id)
            if not job:
                return None
            
            # Create new job with same parameters but new ID
            replay_job = ExecutionJob()
            replay_job.tenant_id = job.tenant_id
            replay_job.strategy_id = job.strategy_id
            replay_job.bot_id = job.bot_id
            replay_job.signal_id = job.signal_id
            replay_job.exchange = job.exchange
            replay_job.symbol = job.symbol
            replay_job.side = job.side
            replay_job.order_type = job.order_type
            replay_job.quantity = job.quantity
            replay_job.price = job.price
            replay_job.priority = job.priority
            replay_job.idempotency_key = job.idempotency_key
            
            # Mark as replay
            replay_job.signal_id = f"replay_{job.signal_id}"
            
            # Save replay job
            await self.save_job(replay_job)
            
            # Save audit event
            await self._save_audit_event(job_id, "replayed", {
                "replay_job_id": replay_job.job_id,
                "original_job_id": job_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            logger.info(f"Replayed job {job_id} as {replay_job.job_id}")
            return replay_job
            
        except Exception as e:
            logger.error(f"Failed to replay job {job_id}: {e}")
            return None


# Global job persistence instance
job_persistence = JobPersistence()
