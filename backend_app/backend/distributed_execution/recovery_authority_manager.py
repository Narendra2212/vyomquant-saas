"""
Recovery Authority Manager for Institutional Recovery

Phase 4 — Recovery Authority Manager

Provides centralized recovery authorization and conflict prevention while
preserving replay guarantees and preventing duplicate execution.

Author: Principal Institutional Recovery and Failover Engineer
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional, Any, Set
from collections import deque

from backend_app.core.cache.redis_manager import redis_manager
from .immutable_journal import immutable_journal

logger = logging.getLogger("recovery_authority_manager")


class RecoveryType(Enum):
    """Recovery operation types."""
    FAILOVER = "failover"
    RESTART = "restart"
    RECONCILIATION = "reconciliation"
    RESURRECTION = "resurrection"


class RecoveryStatus(Enum):
    """Recovery operation statuses."""
    PENDING = "pending"
    AUTHORIZED = "authorized"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class RecoveryRequest:
    """Recovery operation request."""
    recovery_id: str
    recovery_type: str
    resource_id: str
    tenant_id: str
    exchange_id: str
    requested_at: datetime
    requested_by: str
    priority: int
    metadata: Dict[str, Any]
    expected_state: Optional[Dict[str, Any]] = None
    sequence: Optional[int] = None


@dataclass
class RecoveryContext:
    """Recovery operation context."""
    recovery_id: str
    request: RecoveryRequest
    status: str
    authorized_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class RecoveryAuthorityManager:
    """Recovery authority manager for institutional recovery."""
    
    def __init__(self):
        self.redis = redis_manager
        self.immutable_journal = immutable_journal
        
        # Active recoveries
        self.active_recoveries: Dict[str, RecoveryContext] = {}
        
        # Recovery queue (priority queue)
        self.recovery_queue: deque = deque()
        
        # Conflict matrix (resources that cannot be recovered simultaneously)
        self.conflict_matrix: Dict[str, Set[str]] = {
            "coordinator": {"failover", "restart"},
            "worker": {"restart", "resurrection"},
            "queue": {"restart", "reassignment"},
            "exchange": {"reconciliation", "restart"},
            "journal": {"validation", "checkpoint"}
        }
        
        # Recovery history
        self.recovery_history: List[RecoveryContext] = []
        
        # Configuration
        self.authorization_timeout = 300  # 5 minutes
        self.max_concurrent_recoveries = 5
        self.history_retention_days = 30
        
        # Background tasks
        self._queue_processor_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        
        logger.info("Recovery authority manager initialized")
    
    async def initialize(self) -> bool:
        """Initialize recovery authority manager."""
        try:
            await self.immutable_journal.initialize()
            
            logger.info("Recovery authority manager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize recovery authority manager: {e}")
            return False
    
    async def start(self) -> bool:
        """Start recovery authority manager."""
        try:
            self._running = True
            
            # Start background tasks
            self._queue_processor_task = asyncio.create_task(self._process_recovery_queue())
            self._cleanup_task = asyncio.create_task(self._cleanup_old_recoveries())
            
            logger.info("Recovery authority manager started")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start recovery authority manager: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop recovery authority manager."""
        try:
            self._running = False
            
            # Cancel tasks
            tasks = [self._queue_processor_task, self._cleanup_task]
            for task in tasks:
                if task:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            logger.info("Recovery authority manager stopped")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop recovery authority manager: {e}")
            return False
    
    async def authorize_recovery(self, request: RecoveryRequest) -> bool:
        """Authorize recovery operation."""
        try:
            # Check for conflicts
            if self._has_conflicts(request):
                logger.warning(f"Recovery conflicts detected: {request.recovery_id}")
                return False
            
            # Check concurrent recovery limit
            if len(self.active_recoveries) >= self.max_concurrent_recoveries:
                logger.warning(f"Max concurrent recoveries reached: {request.recovery_id}")
                # Add to queue
                self.recovery_queue.append(request)
                return False
            
            # Validate journal state if required
            if request.recovery_type in ["failover", "restart"]:
                if not await self._validate_journal_state():
                    logger.warning(f"Journal state validation failed: {request.recovery_id}")
                    return False
            
            # Create recovery context
            context = RecoveryContext(
                recovery_id=request.recovery_id,
                request=request,
                status=RecoveryStatus.AUTHORIZED.value,
                authorized_at=datetime.now(timezone.utc)
            )
            
            # Add to active recoveries
            self.active_recoveries[request.recovery_id] = context
            
            # Store in Redis
            context_key = f"recovery:context:{request.recovery_id}"
            await self.redis.setex(
                context_key,
                self.authorization_timeout,
                json.dumps(asdict(context), default=str)
            )
            
            logger.info(f"Recovery authorized: {request.recovery_id}")
            return True
            
        except Exception as e:
            logger.error(f"Recovery authorization failed: {e}")
            return False
    
    async def start_recovery(self, recovery_id: str) -> bool:
        """Mark recovery as in progress."""
        try:
            if recovery_id not in self.active_recoveries:
                logger.warning(f"Recovery not found: {recovery_id}")
                return False
            
            context = self.active_recoveries[recovery_id]
            context.status = RecoveryStatus.IN_PROGRESS.value
            context.started_at = datetime.now(timezone.utc)
            
            # Update Redis
            context_key = f"recovery:context:{recovery_id}"
            await self.redis.setex(
                context_key,
                3600,
                json.dumps(asdict(context), default=str)
            )
            
            logger.info(f"Recovery started: {recovery_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start recovery: {e}")
            return False
    
    async def complete_recovery(self, recovery_id: str, result: Dict[str, Any]) -> bool:
        """Mark recovery as completed."""
        try:
            if recovery_id not in self.active_recoveries:
                logger.warning(f"Recovery not found: {recovery_id}")
                return False
            
            context = self.active_recoveries[recovery_id]
            context.status = RecoveryStatus.COMPLETED.value
            context.completed_at = datetime.now(timezone.utc)
            context.result = result
            
            # Move to history
            self.recovery_history.append(context)
            del self.active_recoveries[recovery_id]
            
            # Update Redis
            context_key = f"recovery:context:{recovery_id}"
            await self.redis.setex(
                context_key,
                86400 * self.history_retention_days,
                json.dumps(asdict(context), default=str)
            )
            
            logger.info(f"Recovery completed: {recovery_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to complete recovery: {e}")
            return False
    
    async def fail_recovery(self, recovery_id: str, error: str) -> bool:
        """Mark recovery as failed."""
        try:
            if recovery_id not in self.active_recoveries:
                logger.warning(f"Recovery not found: {recovery_id}")
                return False
            
            context = self.active_recoveries[recovery_id]
            context.status = RecoveryStatus.FAILED.value
            context.completed_at = datetime.now(timezone.utc)
            context.error = error
            
            # Move to history
            self.recovery_history.append(context)
            del self.active_recoveries[recovery_id]
            
            # Update Redis
            context_key = f"recovery:context:{recovery_id}"
            await self.redis.setex(
                context_key,
                86400 * self.history_retention_days,
                json.dumps(asdict(context), default=str)
            )
            
            logger.info(f"Recovery failed: {recovery_id} - {error}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to fail recovery: {e}")
            return False
    
    def _has_conflicts(self, request: RecoveryRequest) -> bool:
        """Check for recovery conflicts."""
        # Check resource conflicts
        for active in self.active_recoveries.values():
            if active.request.resource_id == request.resource_id:
                return True
            
            # Check conflict matrix
            if request.resource_id in self.conflict_matrix:
                conflicting_types = self.conflict_matrix[request.resource_id]
                if active.request.recovery_type in conflicting_types:
                    return True
        
        return False
    
    async def _validate_journal_state(self) -> bool:
        """Validate journal state before recovery."""
        try:
            # Verify journal integrity
            integrity_valid = await self.immutable_journal.verify_journal_integrity()
            
            if not integrity_valid:
                return False
            
            # Verify event signatures
            events = await self.immutable_journal.get_events_by_tenant("system")
            for event in events[:10]:  # Sample first 10 events
                signature_valid = await self.immutable_journal.verify_event_integrity(event)
                if not signature_valid:
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Journal state validation failed: {e}")
            return False
    
    async def _process_recovery_queue(self):
        """Process recovery queue."""
        while self._running:
            try:
                # Check if we can process more recoveries
                if len(self.active_recoveries) < self.max_concurrent_recoveries and self.recovery_queue:
                    # Get next request from queue
                    request = self.recovery_queue.popleft()
                    
                    # Try to authorize
                    authorized = await self.authorize_recovery(request)
                    
                    if not authorized:
                        # Put back in queue
                        self.recovery_queue.appendleft(request)
                
                await asyncio.sleep(1.0)  # Check every second
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Recovery queue processing error: {e}")
                await asyncio.sleep(1.0)
    
    async def _cleanup_old_recoveries(self):
        """Clean up old recoveries from history."""
        while self._running:
            try:
                # Clean up recoveries older than retention period
                cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.history_retention_days)
                
                self.recovery_history = [
                    context for context in self.recovery_history
                    if context.completed_at and context.completed_at > cutoff_date
                ]
                
                # Run cleanup daily
                await asyncio.sleep(86400)  # 24 hours
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Recovery cleanup error: {e}")
                await asyncio.sleep(3600)  # Retry in 1 hour
    
    async def get_recovery_status(self, recovery_id: str) -> Optional[Dict[str, Any]]:
        """Get recovery status."""
        try:
            # Check active recoveries
            if recovery_id in self.active_recoveries:
                context = self.active_recoveries[recovery_id]
                return asdict(context)
            
            # Check history
            for context in self.recovery_history:
                if context.recovery_id == recovery_id:
                    return asdict(context)
            
            # Check Redis
            context_key = f"recovery:context:{recovery_id}"
            context_data = await self.redis.get(context_key)
            
            if context_data:
                return json.loads(context_data)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get recovery status: {e}")
            return None
    
    async def get_active_recoveries(self) -> List[Dict[str, Any]]:
        """Get all active recoveries."""
        try:
            return [asdict(context) for context in self.active_recoveries.values()]
            
        except Exception as e:
            logger.error(f"Failed to get active recoveries: {e}")
            return []
    
    async def get_recovery_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recovery history."""
        try:
            history = self.recovery_history[-limit:]
            return [asdict(context) for context in history]
            
        except Exception as e:
            logger.error(f"Failed to get recovery history: {e}")
            return []
    
    async def get_authority_status(self) -> Dict[str, Any]:
        """Get recovery authority status."""
        return {
            "active_recoveries": len(self.active_recoveries),
            "queued_recoveries": len(self.recovery_queue),
            "history_count": len(self.recovery_history),
            "max_concurrent_recoveries": self.max_concurrent_recoveries,
            "authorization_timeout": self.authorization_timeout
        }


# Global instance
_recovery_authority_manager: Optional[RecoveryAuthorityManager] = None


def get_recovery_authority_manager() -> RecoveryAuthorityManager:
    """Get or create recovery authority manager instance."""
    global _recovery_authority_manager
    if _recovery_authority_manager is None:
        _recovery_authority_manager = RecoveryAuthorityManager()
    return _recovery_authority_manager
