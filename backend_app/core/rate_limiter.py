"""
core/rate_limiter.py — RATE LIMITING SYSTEM

STEP 5: PREVENT USER OVERLOAD

LIMITS (per user):
  - max 5 trades/sec
  - max 100 trades/min
  - max 20 open positions

STORAGE:
  - rate_limit:user:{id}:trades:second
  - rate_limit:user:{id}:trades:minute
  - rate_limit:user:{id}:positions

USAGE:
    from backend_app.core.rate_limiter import rate_limiter, RateLimitExceeded
    
    try:
        await rate_limiter.check_trade_allowed(user_id="user-123")
    except RateLimitExceeded as e:
        return {"error": e.message}
    
    # Check position limit
    can_open = await rate_limiter.can_open_position(user_id="user-123")
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict

try:
    from backend_app.core.cache import redis_manager
except ImportError:
    from backend_app.core.cache import redis_manager

logger = logging.getLogger("RateLimiter")


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""
    
    def __init__(self, limit_type: str, current: int, max_allowed: int, retry_after: float):
        self.limit_type = limit_type
        self.current = current
        self.max_allowed = max_allowed
        self.retry_after = retry_after
        self.message = f"Rate limit exceeded: {limit_type} ({current}/{max_allowed}). Retry after {retry_after:.1f}s"
        super().__init__(self.message)


@dataclass
class RateLimitStatus:
    """Current rate limit status for a user."""
    user_id: str
    trades_per_second: int
    trades_per_second_limit: int
    trades_per_minute: int
    trades_per_minute_limit: int
    open_positions: int
    max_positions: int
    allowed: bool
    violations: list


class RateLimiter:
    """
    STEP 5: Rate Limiting System
    
    Prevents user overload by enforcing:
    - 5 trades/second max
    - 100 trades/minute max
    - 20 open positions max
    
    Uses Redis for distributed rate limiting across multiple workers.
    """
    
    # Default limits
    DEFAULT_TRADES_PER_SECOND = 5
    DEFAULT_TRADES_PER_MINUTE = 100
    DEFAULT_MAX_POSITIONS = 20
    
    def __init__(self):
        self.redis = redis_manager
        self._lock = asyncio.Lock()
    
    def _get_keys(self, user_id: str) -> Dict[str, str]:
        """Get Redis keys for a user."""
        return {
            "trades_second": f"rate_limit:user:{user_id}:trades:second",
            "trades_minute": f"rate_limit:user:{user_id}:trades:minute",
            "positions": f"rate_limit:user:{user_id}:positions",
        }
    
    async def check_trade_allowed(self, user_id: str) -> bool:
        """
        Check if user can execute a trade.
        
        Raises:
            RateLimitExceeded: If any limit is exceeded
        
        Returns:
            True if allowed
        """
        keys = self._get_keys(user_id)
        now = time.time()
        violations = []
        
        # Check trades per second (sliding window)
        second_key = keys["trades_second"]
        second_window_start = now - 1.0  # 1 second window
        
        # Remove old entries (older than 1 second)
        await self.redis.zremrangebyscore(second_key, 0, second_window_start)
        
        # Count trades in current second window
        trades_second = await self.redis.zcard(second_key)
        
        if trades_second >= self.DEFAULT_TRADES_PER_SECOND:
            # Find oldest trade in window to calculate retry_after
            oldest = await self.redis.zrange(second_key, 0, 0, withscores=True)
            retry_after = 1.0 - (now - oldest[0][1]) if oldest else 1.0
            violations.append(("trades_per_second", trades_second, self.DEFAULT_TRADES_PER_SECOND, retry_after))
        
        # Check trades per minute (sliding window)
        minute_key = keys["trades_minute"]
        minute_window_start = now - 60.0  # 60 second window
        
        # Remove old entries (older than 60 seconds)
        await self.redis.zremrangebyscore(minute_key, 0, minute_window_start)
        
        # Count trades in current minute window
        trades_minute = await self.redis.zcard(minute_key)
        
        if trades_minute >= self.DEFAULT_TRADES_PER_MINUTE:
            # Find oldest trade in window
            oldest = await self.redis.zrange(minute_key, 0, 0, withscores=True)
            retry_after = 60.0 - (now - oldest[0][1]) if oldest else 60.0
            violations.append(("trades_per_minute", trades_minute, self.DEFAULT_TRADES_PER_MINUTE, retry_after))
        
        # Raise if any violations
        if violations:
            # Report the most restrictive violation (shortest retry_after)
            violations.sort(key=lambda x: x[3])
            v = violations[0]
            raise RateLimitExceeded(v[0], v[1], v[2], v[3])
        
        # Record this trade attempt
        # Use a unique member name to allow multiple entries with the same timestamp
        member_id = f"{now}:{time.perf_counter()}"
        await self.redis.zadd(second_key, {member_id: now})
        await self.redis.zadd(minute_key, {member_id: now})
        
        # Set expiry on keys (to auto-cleanup)
        await self.redis.expire(second_key, 2)
        await self.redis.expire(minute_key, 70)
        
        logger.debug(f"[RateLimiter] Trade allowed for user {user_id} "
                    f"({trades_second}/{self.DEFAULT_TRADES_PER_SECOND} per sec, "
                    f"{trades_minute}/{self.DEFAULT_TRADES_PER_MINUTE} per min)")
        
        return True
    
    async def can_open_position(self, user_id: str) -> bool:
        """
        Check if user can open a new position.
        
        Returns:
            True if under position limit
        """
        current_positions = await self.get_open_position_count(user_id)
        return current_positions < self.DEFAULT_MAX_POSITIONS
    
    async def check_position_limit(self, user_id: str) -> bool:
        """
        Check position limit and raise if exceeded.
        
        Raises:
            RateLimitExceeded: If position limit reached
        """
        current_positions = await self.get_open_position_count(user_id)
        
        if current_positions >= self.DEFAULT_MAX_POSITIONS:
            raise RateLimitExceeded(
                "open_positions",
                current_positions,
                self.DEFAULT_MAX_POSITIONS,
                retry_after=0  # Positions must be closed manually
            )
        
        return True
    
    async def get_open_position_count(self, user_id: str) -> int:
        """Get current open position count for user."""
        keys = self._get_keys(user_id)
        count = await self.redis.get(keys["positions"])
        return int(count) if count else 0
    
    async def increment_position_count(self, user_id: str) -> int:
        """Increment open position count. Returns new count."""
        keys = self._get_keys(user_id)
        new_count = await self.redis.incr(keys["positions"])
        return new_count
    
    async def decrement_position_count(self, user_id: str) -> int:
        """Decrement open position count. Returns new count."""
        keys = self._get_keys(user_id)
        current = await self.redis.get(keys["positions"])
        if current and int(current) > 0:
            new_count = await self.redis.decr(keys["positions"])
            return new_count
        return 0
    
    async def set_position_count(self, user_id: str, count: int):
        """Set exact position count (useful for reconciliation)."""
        keys = self._get_keys(user_id)
        await self.redis.set(keys["positions"], str(count))
    
    async def get_status(self, user_id: str) -> RateLimitStatus:
        """Get current rate limit status for a user."""
        keys = self._get_keys(user_id)
        now = time.time()
        violations = []
        
        # Check trades per second
        second_key = keys["trades_second"]
        second_window_start = now - 1.0
        await self.redis.zremrangebyscore(second_key, 0, second_window_start)
        trades_second = await self.redis.zcard(second_key)
        
        # Check trades per minute
        minute_key = keys["trades_minute"]
        minute_window_start = now - 60.0
        await self.redis.zremrangebyscore(minute_key, 0, minute_window_start)
        trades_minute = await self.redis.zcard(minute_key)
        
        # Check positions
        open_positions = await self.get_open_position_count(user_id)
        
        # Check violations
        if trades_second >= self.DEFAULT_TRADES_PER_SECOND:
            violations.append(f"trades_per_second ({trades_second}/{self.DEFAULT_TRADES_PER_SECOND})")
        if trades_minute >= self.DEFAULT_TRADES_PER_MINUTE:
            violations.append(f"trades_per_minute ({trades_minute}/{self.DEFAULT_TRADES_PER_MINUTE})")
        if open_positions >= self.DEFAULT_MAX_POSITIONS:
            violations.append(f"open_positions ({open_positions}/{self.DEFAULT_MAX_POSITIONS})")
        
        return RateLimitStatus(
            user_id=user_id,
            trades_per_second=trades_second,
            trades_per_second_limit=self.DEFAULT_TRADES_PER_SECOND,
            trades_per_minute=trades_minute,
            trades_per_minute_limit=self.DEFAULT_TRADES_PER_MINUTE,
            open_positions=open_positions,
            max_positions=self.DEFAULT_MAX_POSITIONS,
            allowed=len(violations) == 0,
            violations=violations
        )
    
    async def reset_limits(self, user_id: str):
        """Reset all rate limits for a user (admin only)."""
        keys = self._get_keys(user_id)
        for key in keys.values():
            await self.redis.delete(key)
        logger.info(f"[RateLimiter] Reset limits for user {user_id}")
    
    async def get_all_user_limits(self) -> Dict[str, RateLimitStatus]:
        """Get rate limit status for all users (admin only)."""
        # This is expensive - use sparingly
        pattern = "rate_limit:user:*:trades:second"
        keys = await self.redis.keys(pattern)
        
        results = {}
        for key in keys:
            # Extract user_id from key
            parts = key.split(":")
            if len(parts) >= 3:
                user_id = parts[2]
                results[user_id] = await self.get_status(user_id)
        
        return results


# Global singleton
rate_limiter = RateLimiter()
