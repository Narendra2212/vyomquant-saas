"""
core/distributed_rate_limiter.py — Distributed Rate Limiting Layer

🔴 STEP 6 — DISTRIBUTED RATE LIMITING

Moves all rate limits to Redis to ensure consistent enforcement
across all pods in the distributed system.

IMPLEMENTATION:
    key = f"rate:{user_id}:orders"
    count = await redis.incr(key)
    if count > limit:
        block

EXPECTED RESULT:
    ✔ No multi-pod bypass
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Optional

from backend_app.backend.redis_manager import redis_manager

logger = logging.getLogger("DistributedRateLimiter")


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""
    pass


class RateLimitType(Enum):
    """Types of rate limits."""
    ORDERS_PER_MINUTE = "orders_per_minute"
    SIGNALS_PER_MINUTE = "signals_per_minute"
    API_CALLS_PER_SECOND = "api_calls_per_second"
    WEBSOCKET_CONNECTIONS = "websocket_connections"


@dataclass
class RateLimitConfig:
    """Configuration for a rate limit."""
    limit: int
    window_seconds: int
    key_prefix: str


@dataclass
class RateLimitStatus:
    """Current status of rate limit."""
    allowed: bool
    current_count: int
    limit: int
    remaining: int
    reset_time: datetime
    window_seconds: int


class DistributedRateLimiter:
    """
    🔴 STEP 6 — DISTRIBUTED RATE LIMITING
    
    Redis-backed distributed rate limiter that prevents multi-pod bypass.
    
    ARCHITECTURE:
        ┌─────────┐     ┌─────────┐     ┌─────────┐
        │  Pod 1  │────→│  Redis  │←────│  Pod 2  │
        └─────────┘     └─────────┘     └─────────┘
                              ↑
                              └──── Shared Rate Counter
    
    KEY FORMAT:
        rate:{user_id}:{limit_type}:{window_timestamp}
        
        Example: rate:tenant-123:orders:1699123456
    
    ALGORITHM:
        1. Generate key with current time window
        2. INCR key in Redis (atomic operation)
        3. SET EXPIRE if first request in window
        4. If count > limit: block request
        5. Return remaining quota
    
    RATE LIMIT TYPES:
        - orders_per_minute: Max orders per user per minute
        - signals_per_minute: Max signals per strategy per minute
        - api_calls_per_second: Max API calls per user per second
        - websocket_connections: Max concurrent WebSocket connections
    
    EXPECTED RESULT:
        ✔ No multi-pod bypass
    """
    
    # Default rate limits
    DEFAULT_LIMITS: Dict[RateLimitType, RateLimitConfig] = {
        RateLimitType.ORDERS_PER_MINUTE: RateLimitConfig(
            limit=60,  # 60 orders per minute
            window_seconds=60,
            key_prefix="orders"
        ),
        RateLimitType.SIGNALS_PER_MINUTE: RateLimitConfig(
            limit=120,  # 120 signals per minute
            window_seconds=60,
            key_prefix="signals"
        ),
        RateLimitType.API_CALLS_PER_SECOND: RateLimitConfig(
            limit=10,  # 10 API calls per second
            window_seconds=1,
            key_prefix="api_calls"
        ),
        RateLimitType.WEBSOCKET_CONNECTIONS: RateLimitConfig(
            limit=20,  # 20 concurrent WebSocket connections
            window_seconds=60,
            key_prefix="ws_connections"
        )
    }
    
    def __init__(self):
        self._custom_limits: Dict[RateLimitType, RateLimitConfig] = {}
    
    def _generate_key(
        self,
        user_id: str,
        limit_type: RateLimitType,
        window_timestamp: int
    ) -> str:
        """Generate Redis key for rate limit."""
        config = self._custom_limits.get(limit_type) or self.DEFAULT_LIMITS[limit_type]
        return f"rate:{user_id}:{config.key_prefix}:{window_timestamp}"
    
    async def check_rate_limit(
        self,
        user_id: str,
        limit_type: RateLimitType,
        custom_limit: Optional[int] = None
    ) -> RateLimitStatus:
        """
        Check if request is within rate limit.
        
        Args:
            user_id: User identifier
            limit_type: Type of rate limit
            custom_limit: Optional custom limit override
            
        Returns:
            RateLimitStatus with current status
            
        Raises:
            RateLimitExceeded: If limit exceeded
        """
        try:
            # Get limit configuration
            config = self._custom_limits.get(limit_type) or self.DEFAULT_LIMITS[limit_type]
            limit = custom_limit or config.limit
            window_seconds = config.window_seconds
            
            # Calculate current time window
            now = datetime.utcnow()
            window_timestamp = int(now.timestamp() / window_seconds)
            
            # Generate Redis key
            key = self._generate_key(user_id, limit_type, window_timestamp)
            
            # Atomically increment counter
            # This is the CRITICAL operation - atomic across all pods
            pipe = await redis_manager.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds + 1)  # +1s buffer
            results = await pipe.execute()
            
            current_count = results[0]
            
            # Calculate remaining
            remaining = max(0, limit - current_count)
            reset_time = now + timedelta(
                seconds=window_seconds - (now.timestamp() % window_seconds)
            )
            
            # Check if limit exceeded
            allowed = current_count <= limit
            
            if not allowed:
                logger.warning(
                    f"🔴 STEP 6: Rate limit exceeded for {user_id}: "
                    f"{limit_type.value} = {current_count}/{limit}"
                )
            else:
                logger.debug(
                    f"STEP 6: Rate limit check for {user_id}: "
                    f"{limit_type.value} = {current_count}/{limit}"
                )
            
            return RateLimitStatus(
                allowed=allowed,
                current_count=current_count,
                limit=limit,
                remaining=remaining,
                reset_time=reset_time,
                window_seconds=window_seconds
            )
        
        except Exception as e:
            logger.error(f"STEP 6: Rate limit check failed: {e}")
            # Fail-safe: allow request on error
            return RateLimitStatus(
                allowed=True,
                current_count=0,
                limit=0,
                remaining=0,
                reset_time=datetime.utcnow(),
                window_seconds=0
            )
    
    async def acquire(
        self,
        user_id: str,
        limit_type: RateLimitType,
        custom_limit: Optional[int] = None
    ) -> bool:
        """
        Acquire rate limit quota.
        
        Returns True if allowed, raises RateLimitExceeded if not.
        
        Args:
            user_id: User identifier
            limit_type: Type of rate limit
            custom_limit: Optional custom limit override
            
        Returns:
            True if request allowed
            
        Raises:
            RateLimitExceeded: If limit exceeded
        """
        status = await self.check_rate_limit(user_id, limit_type, custom_limit)
        
        if not status.allowed:
            raise RateLimitExceeded(
                f"Rate limit exceeded: {limit_type.value} "
                f"({status.current_count}/{status.limit}). "
                f"Reset at {status.reset_time.isoformat()}"
            )
        
        return True
    
    async def get_status(
        self,
        user_id: str,
        limit_type: RateLimitType
    ) -> RateLimitStatus:
        """
        Get current rate limit status without consuming quota.
        
        Args:
            user_id: User identifier
            limit_type: Type of rate limit
            
        Returns:
            RateLimitStatus with current status
        """
        try:
            config = self._custom_limits.get(limit_type) or self.DEFAULT_LIMITS[limit_type]
            
            now = datetime.utcnow()
            window_timestamp = int(now.timestamp() / config.window_seconds)
            key = self._generate_key(user_id, limit_type, window_timestamp)
            
            # Get current count
            count = await redis_manager.get(key)
            current_count = int(count) if count else 0
            
            remaining = max(0, config.limit - current_count)
            reset_time = now + timedelta(
                seconds=config.window_seconds - (now.timestamp() % config.window_seconds)
            )
            
            return RateLimitStatus(
                allowed=current_count <= config.limit,
                current_count=current_count,
                limit=config.limit,
                remaining=remaining,
                reset_time=reset_time,
                window_seconds=config.window_seconds
            )
        
        except Exception as e:
            logger.error(f"STEP 6: Failed to get rate limit status: {e}")
            return RateLimitStatus(
                allowed=True,
                current_count=0,
                limit=0,
                remaining=0,
                reset_time=datetime.utcnow(),
                window_seconds=0
            )
    
    def set_custom_limit(
        self,
        limit_type: RateLimitType,
        limit: int,
        window_seconds: int
    ):
        """
        Set custom rate limit configuration.
        
        Args:
            limit_type: Type of rate limit
            limit: Maximum requests allowed
            window_seconds: Time window in seconds
        """
        self._custom_limits[limit_type] = RateLimitConfig(
            limit=limit,
            window_seconds=window_seconds,
            key_prefix=self.DEFAULT_LIMITS[limit_type].key_prefix
        )
        logger.info(
            f"STEP 6: Custom rate limit set for {limit_type.value}: "
            f"{limit}/{window_seconds}s"
        )
    
    async def reset(
        self,
        user_id: str,
        limit_type: RateLimitType
    ):
        """
        Reset rate limit counter for a user.
        
        Args:
            user_id: User identifier
            limit_type: Type of rate limit
        """
        try:
            config = self._custom_limits.get(limit_type) or self.DEFAULT_LIMITS[limit_type]
            
            now = datetime.utcnow()
            window_timestamp = int(now.timestamp() / config.window_seconds)
            key = self._generate_key(user_id, limit_type, window_timestamp)
            
            await redis_manager.delete(key)
            logger.info(f"STEP 6: Rate limit reset for {user_id}: {limit_type.value}")
        
        except Exception as e:
            logger.error(f"STEP 6: Failed to reset rate limit: {e}")


# Global singleton instance
_distributed_rate_limiter: Optional[DistributedRateLimiter] = None


def get_distributed_rate_limiter() -> DistributedRateLimiter:
    """Get or create the distributed rate limiter instance."""
    global _distributed_rate_limiter
    if _distributed_rate_limiter is None:
        _distributed_rate_limiter = DistributedRateLimiter()
    return _distributed_rate_limiter


# Convenience exports
__all__ = [
    "DistributedRateLimiter",
    "RateLimitStatus",
    "RateLimitConfig",
    "RateLimitType",
    "RateLimitExceeded",
    "get_distributed_rate_limiter",
]
