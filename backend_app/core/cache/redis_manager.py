"""
core/cache/redis_manager.py — SHARED REDIS CONNECTION POOL

STEP 4: REDIS ARCHITECTURE — CONNECTION POOL FIX

This module provides a unified interface to the shared Redis connection pool.
All Redis operations MUST go through this manager to ensure:
- Shared connection pool (no per-request connections)
- No connection leaks
- No race conditions
- High performance

MANDATORY IMPORT:
    from backend_app.core.cache.redis_manager import redis_manager
    redis_client = redis_manager.get_client()

STRICT RULES:
- ❌ NO: redis.Redis.from_url(...)
- ❌ NO: await redis_client.close()
- ✅ YES: redis_manager.get_client()
"""

from typing import Optional, Any


class SharedRedisManager:
    """
    Wrapper around backend RedisManager providing unified interface.
    
    This ensures all code uses the shared connection pool.
    """
    
    _instance: Optional['SharedRedisManager'] = None
    _redis_manager = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
    
    async def _ensure_manager(self):
        """Ensure the backend manager is initialized."""
        if self._redis_manager is None:
            from backend_app.backend.redis_manager import get_redis_manager
            self._redis_manager = await get_redis_manager()
    
    async def get_client(self) -> Optional[object]:
        """
        Get the shared Redis client (cache database by default).
        
        Returns:
            Redis client from shared pool
            
        Usage:
            redis_client = await redis_manager.get_client()
            await redis_client.setex(key, ttl, value)
        """
        await self._ensure_manager()
        return self._redis_manager.cache if self._redis_manager else None
    
    async def get_queue_client(self) -> Optional[object]:
        """Get queue database client (DB 1)."""
        await self._ensure_manager()
        return self._redis_manager.queue if self._redis_manager else None
    
    async def get_events_client(self) -> Optional[object]:
        """Get events database client (DB 2)."""
        await self._ensure_manager()
        return self._redis_manager.events if self._redis_manager else None
    
    def get_client_sync(self) -> Optional[object]:
        """
        Synchronous version for when async context is not available.
        Only use when absolutely necessary.
        """
        if self._redis_manager is None:
            raise RuntimeError(
                "RedisManager not initialized. "
                "Call await redis_manager.get_client() first in async context."
            )
        return self._redis_manager.cache
    
    # Convenience methods that wrap the underlying manager
    
    async def cache_get(self, key: str) -> Optional[str]:
        await self._ensure_manager()
        return await self._redis_manager.cache_get(key)
    
    async def cache_set(self, key: str, value: str, ttl: int = 300) -> bool:
        await self._ensure_manager()
        return await self._redis_manager.cache_set(key, value, ttl)
    
    async def cache_delete(self, key: str) -> bool:
        await self._ensure_manager()
        return await self._redis_manager.cache_delete(key)
    
    async def cache_get_json(self, key: str) -> Optional[dict]:
        await self._ensure_manager()
        return await self._redis_manager.cache_get_json(key)
    
    async def cache_set_json(self, key: str, value: dict, ttl: int = 300) -> bool:
        await self._ensure_manager()
        return await self._redis_manager.cache_set_json(key, value, ttl)
    
    async def disconnect(self):
        """Disconnect from Redis (delegates to underlying manager)."""
        if self._redis_manager:
            await self._redis_manager.close()
    
    async def connect(self):
        """Connect to Redis (delegates to underlying manager)."""
        await self._ensure_manager()
        if self._redis_manager and not self._redis_manager._cache:
            await self._redis_manager.initialize()
    
    async def ping(self) -> bool:
        """Ping Redis to test connectivity."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            result = await self._redis_manager.cache.ping()
            return bool(result)
        return False

    async def keys(self, pattern: str = "*") -> list:
        """Proxy to cache keys method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.keys(pattern)
        return []

    async def get(self, key: str):
        """Proxy to cache get method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.get(key)
        return None

    async def set(self, key: str, value: Any, **kwargs):
        """Proxy to cache set method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.set(key, value, **kwargs)
        return None

    async def delete(self, *keys: str):
        """Proxy to cache delete method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.delete(*keys)
        return 0

    async def xadd(self, stream: str, payload: dict):
        """Proxy to events xadd method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.events:
            import json
            body = {
                k: json.dumps(v) if not isinstance(v, str) else v
                for k, v in payload.items()
            }
            try:
                return await self._redis_manager.events.xadd(stream, body)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"XADD failed: {e}")
                return None
        return None

    async def setex(self, key: str, ttl: int, value: Any):
        """Proxy to cache setex method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.setex(key, ttl, value)
        return None

    async def zremrangebyscore(self, key: str, min: Any, max: Any) -> int:
        """Proxy to cache zremrangebyscore method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.zremrangebyscore(key, min, max)
        return 0

    async def zcard(self, key: str) -> int:
        """Proxy to cache zcard method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.zcard(key)
        return 0

    async def zrange(self, key: str, start: int, end: int, **kwargs) -> list:
        """Proxy to cache zrange method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.zrange(key, start, end, **kwargs)
        return []

    async def zadd(self, key: str, mapping: dict, *args, **kwargs) -> int:
        """Proxy to cache zadd method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.zadd(key, mapping, *args, **kwargs)
        return 0

    async def expire(self, key: str, time: int, *args, **kwargs) -> bool:
        """Proxy to cache expire method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.expire(key, time, *args, **kwargs)
        return False

    async def incr(self, key: str, amount: int = 1) -> int:
        """Proxy to cache incr method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.incr(key, amount)
        return 0

    async def decr(self, key: str, amount: int = 1) -> int:
        """Proxy to cache decr method."""
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            return await self._redis_manager.cache.decr(key, amount)
        return 0

    @property
    def pool(self):
        """Return self for compatibility (health check calls pool.ping())."""
        return self


# Global singleton instance
redis_manager = SharedRedisManager()
