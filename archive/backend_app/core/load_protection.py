"""
core/load_protection.py — Load Protection System

🔴 STEP 9 — LOAD PROTECTION

Prevents system overload by limiting concurrent executions.

ADD:
- max concurrent executions per user
- max global executions

IF EXCEEDED:
- reject execution

EXPECTED RESULT:
✔ No system overload
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Dict, Optional

from backend_app.backend.redis_manager import redis_manager

logger = logging.getLogger("LoadProtection")


class LoadLimitExceeded(Exception):
    """Raised when load limit is exceeded."""
    pass


@dataclass
class LoadStatus:
    """Current load status."""
    allowed: bool
    user_concurrent: int
    global_concurrent: int
    user_limit: int
    global_limit: int
    user_remaining: int
    global_remaining: int


class LoadProtection:
    """
    🔴 STEP 9 — LOAD PROTECTION
    
    Redis-backed load protection to prevent system overload.
    
    LIMITS:
        - Per user: MAX_CONCURRENT_PER_USER (default: 5)
        - Global: MAX_GLOBAL_CONCURRENT (default: 50)
    
    KEYS:
        - load:user:{user_id}  - Current concurrent executions per user
        - load:global          - Total concurrent executions across all users
    
    ALGORITHM:
        1. INCR user counter
        2. INCR global counter
        3. If either > limit: DECR both and reject
        4. On completion: DECR both
    
    EXPECTED RESULT:
        ✔ No system overload
    """
    
    # Maximum concurrent executions per user
    MAX_CONCURRENT_PER_USER = 5
    
    # Maximum global concurrent executions
    MAX_GLOBAL_CONCURRENT = 50
    
    # Redis key TTL (seconds) - prevents stuck counters
    COUNTER_TTL_SECONDS = 300  # 5 minutes
    
    def __init__(self):
        self._user_key_prefix = "load:user"
        self._global_key = "load:global"
    
    def _get_user_key(self, user_id: str) -> str:
        """Generate Redis key for user load counter."""
        return f"{self._user_key_prefix}:{user_id}"
    
    async def check_load(
        self,
        user_id: str,
        max_per_user: Optional[int] = None,
        max_global: Optional[int] = None
    ) -> LoadStatus:
        """
        Check current load status without acquiring slot.
        
        Args:
            user_id: User identifier
            max_per_user: Optional override for per-user limit
            max_global: Optional override for global limit
            
        Returns:
            LoadStatus with current load information
        """
        try:
            user_key = self._get_user_key(user_id)
            
            # Get current counts
            user_count = await redis_manager.get(user_key)
            global_count = await redis_manager.get(self._global_key)
            
            user_concurrent = int(user_count) if user_count else 0
            global_concurrent = int(global_count) if global_count else 0
            
            user_limit = max_per_user or self.MAX_CONCURRENT_PER_USER
            global_limit = max_global or self.MAX_GLOBAL_CONCURRENT
            
            user_remaining = max(0, user_limit - user_concurrent)
            global_remaining = max(0, global_limit - global_concurrent)
            
            allowed = user_concurrent < user_limit and global_concurrent < global_limit
            
            return LoadStatus(
                allowed=allowed,
                user_concurrent=user_concurrent,
                global_concurrent=global_concurrent,
                user_limit=user_limit,
                global_limit=global_limit,
                user_remaining=user_remaining,
                global_remaining=global_remaining
            )
        
        except Exception as e:
            logger.error(f"STEP 9: Load check failed: {e}")
            # Fail-safe: allow execution
            return LoadStatus(
                allowed=True,
                user_concurrent=0,
                global_concurrent=0,
                user_limit=0,
                global_limit=0,
                user_remaining=0,
                global_remaining=0
            )
    
    async def acquire_execution_slot(
        self,
        user_id: str,
        max_per_user: Optional[int] = None,
        max_global: Optional[int] = None
    ) -> bool:
        """
        Acquire an execution slot.
        
        Atomically increments counters and checks limits.
        
        Args:
            user_id: User identifier
            max_per_user: Optional per-user limit override
            max_global: Optional global limit override
            
        Returns:
            True if slot acquired, False otherwise
            
        Raises:
            LoadLimitExceeded: If limits exceeded
        """
        try:
            user_key = self._get_user_key(user_id)
            user_limit = max_per_user or self.MAX_CONCURRENT_PER_USER
            global_limit = max_global or self.MAX_GLOBAL_CONCURRENT
            
            # Use pipeline for atomic operations
            pipe = await redis_manager.pipeline()
            
            # Increment both counters
            pipe.incr(user_key)
            pipe.incr(self._global_key)
            
            # Set TTL on keys (prevents stuck counters)
            pipe.expire(user_key, self.COUNTER_TTL_SECONDS)
            pipe.expire(self._global_key, self.COUNTER_TTL_SECONDS)
            
            results = await pipe.execute()
            
            user_count = results[0]
            global_count = results[1]
            
            # Check if limits exceeded
            if user_count > user_limit:
                # Reject - decrement counters
                await self._decrement_counters(user_id)
                logger.warning(
                    f"🔴 STEP 9: Per-user limit exceeded for {user_id}: "
                    f"{user_count}/{user_limit}"
                )
                raise LoadLimitExceeded(
                    f"Per-user concurrent execution limit exceeded: "
                    f"{user_count}/{user_limit}"
                )
            
            if global_count > global_limit:
                # Reject - decrement counters
                await self._decrement_counters(user_id)
                logger.warning(
                    f"🔴 STEP 9: Global limit exceeded: "
                    f"{global_count}/{global_limit}"
                )
                raise LoadLimitExceeded(
                    f"Global concurrent execution limit exceeded: "
                    f"{global_count}/{global_limit}"
                )
            
            logger.debug(
                f"STEP 9: Execution slot acquired for {user_id}: "
                f"user={user_count}/{user_limit}, global={global_count}/{global_limit}"
            )
            
            return True
        
        except LoadLimitExceeded:
            raise
        except Exception as e:
            logger.error(f"STEP 9: Failed to acquire execution slot: {e}")
            # Fail-safe: allow execution
            return True
    
    async def release_execution_slot(self, user_id: str):
        """
        Release an execution slot.
        
        Decrements both user and global counters.
        """
        try:
            await self._decrement_counters(user_id)
            logger.debug(f"STEP 9: Execution slot released for {user_id}")
        
        except Exception as e:
            logger.error(f"STEP 9: Failed to release execution slot: {e}")
    
    async def _decrement_counters(self, user_id: str):
        """Decrement user and global counters."""
        try:
            user_key = self._get_user_key(user_id)
            
            pipe = await redis_manager.pipeline()
            pipe.decr(user_key)
            pipe.decr(self._global_key)
            
            await pipe.execute()
        
        except Exception as e:
            logger.error(f"STEP 9: Failed to decrement counters: {e}")
    
    @asynccontextmanager
    async def execution_slot(
        self,
        user_id: str,
        max_per_user: Optional[int] = None,
        max_global: Optional[int] = None
    ):
        """
        Context manager for execution slot.
        
        Usage:
            async with load_protection.execution_slot(user_id):
                await execute_order(...)
        
        Automatically releases slot on completion or exception.
        """
        try:
            # Acquire slot
            await self.acquire_execution_slot(user_id, max_per_user, max_global)
            
            # Yield control
            yield
            
        finally:
            # Always release slot
            await self.release_execution_slot(user_id)
    
    async def get_stats(self) -> Dict[str, any]:
        """Get load protection statistics."""
        try:
            # Get all user keys
            user_keys = await redis_manager.keys(f"{self._user_key_prefix}:*")
            
            user_stats = {}
            total_user_count = 0
            
            for key in user_keys:
                count = await redis_manager.get(key)
                user_id = key.decode().split(":")[-1] if isinstance(key, bytes) else key.split(":")[-1]
                user_count = int(count) if count else 0
                user_stats[user_id] = user_count
                total_user_count += user_count
            
            global_count = await redis_manager.get(self._global_key)
            global_concurrent = int(global_count) if global_count else 0
            
            return {
                "user_stats": user_stats,
                "total_user_executions": total_user_count,
                "global_executions": global_concurrent,
                "max_per_user": self.MAX_CONCURRENT_PER_USER,
                "max_global": self.MAX_GLOBAL_CONCURRENT,
                "utilization_percent": (global_concurrent / self.MAX_GLOBAL_CONCURRENT) * 100
            }
        
        except Exception as e:
            logger.error(f"STEP 9: Failed to get stats: {e}")
            return {}


# Global singleton instance
_load_protection: Optional[LoadProtection] = None


def get_load_protection() -> LoadProtection:
    """Get or create the load protection instance."""
    global _load_protection
    if _load_protection is None:
        _load_protection = LoadProtection()
    return _load_protection


# Convenience exports
__all__ = [
    "LoadProtection",
    "LoadStatus",
    "LoadLimitExceeded",
    "get_load_protection",
]
