"""
core/cache/redis_manager.py — SHARED REDIS CONNECTION POOL

STEP 4: REDIS ARCHITECTURE — CONNECTION POOL FIX

This module provides a unified interface to the shared Redis connection pool.
All Redis operations MUST go through this manager to ensure:
- Shared connection pool (no per-request connections)
- No connection leaks
- No race conditions
- High performance
- DEV_MODE fallback for local development

MANDATORY IMPORT:
    from backend_app.core.cache.redis_manager import redis_manager
    redis_client = redis_manager.get_client()

STRICT RULES:
- ❌ NO: redis.Redis.from_url(...)
- ❌ NO: await redis_client.close()
- ✅ YES: redis_manager.get_client()
"""

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# DEV_MODE detection
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() == "true" or \
           os.environ.get("ENV", "").lower() == "development"


class MockRedisClient:
    """In-memory mock Redis for DEV_MODE"""
    
    def __init__(self):
        self._store = {}
        self._pubsub = {}
    
    async def get(self, key: str) -> Optional[str]:
        return self._store.get(key)
    
    async def set(self, key: str, value: str, ex: int = None, **kwargs):
        nx = kwargs.get('nx', False)
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True
    
    async def delete(self, key: str):
        self._store.pop(key, None)
        return True

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def setex(self, key: str, seconds: int, value: str):
        self._store[key] = value
        return True

    async def sismember(self, key: str, member: Any) -> bool:
        s = self._store.get(key, set())
        if not isinstance(s, set):
            return False
        return member in s

    async def sadd(self, key: str, *members: Any) -> int:
        if key not in self._store or not isinstance(self._store[key], set):
            self._store[key] = set()
        added = 0
        for m in members:
            if m not in self._store[key]:
                self._store[key].add(m)
                added += 1
        return added

    async def hset(self, key: str, name: str = None, value: str = None, mapping: dict = None) -> int:
        if key not in self._store or not isinstance(self._store[key], dict):
            self._store[key] = {}
        count = 0
        if mapping:
            for k, v in mapping.items():
                self._store[key][k] = str(v)
                count += 1
        elif name is not None:
            self._store[key][name] = str(value)
            count = 1
        return count

    async def hget(self, key: str, field: str) -> Optional[str]:
        d = self._store.get(key, {})
        return d.get(field) if isinstance(d, dict) else None

    async def hgetall(self, key: str) -> dict:
        d = self._store.get(key, {})
        return dict(d) if isinstance(d, dict) else {}

    async def expire(self, key: str, seconds: int) -> bool:
        return True
    
    async def publish(self, channel: str, message: str):
        logger.debug(f"MockRedis: publish to {channel}: {message}")
        return 1
    
    async def subscribe(self, channel: str):
        return MockRedisPubSub()
    
    async def ping(self):
        return True

    async def close(self):
        pass
    
    async def keys(self, pattern: str = "*") -> list:
        return [k for k in self._store.keys() if pattern == "*" or pattern in k]
    
    async def xadd(self, stream: str, data: dict, **kwargs):
        """Mock xadd for streams - stores in memory"""
        if stream not in self._store:
            self._store[stream] = []
        import time
        entry_id = f"{int(time.time() * 1000)}-0"
        self._store[stream].append((entry_id, data))
        return entry_id
    
    async def xrevrange(self, stream: str, count: int = None, **kwargs):
        """Mock xrevrange for streams - returns in reverse order"""
        if stream not in self._store:
            return []
        entries = self._store[stream][::-1]  # Reverse for xrevrange
        if count:
            entries = entries[:count]
        return entries


class MockRedisPubSub:
    """Mock pub/sub client"""
    
    async def get_message(self, ignore_subscribe_messages=False, timeout=0):
        return None
    
    async def listen(self):
        return iter([])
    
    async def close(self):
        pass


class PublishError(RuntimeError):
    """Raised when publishing a message to a durable or trading-critical Redis Stream fails."""
    pass


TRADING_CRITICAL_STREAMS = {"command_queue", "risk_signal", "execution_signal", "strategy_signal"}


class SharedRedisManager:
    """
    Wrapper around backend RedisManager providing unified interface.
    
    This ensures all code uses the shared connection pool.
    Includes DEV_MODE fallback with MockRedisClient for local development.
    """
    
    _instance: Optional['SharedRedisManager'] = None
    _redis_manager = None
    _mock_client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._mock_client = MockRedisClient()
    
    async def _ensure_manager(self):
        """Ensure the backend manager is initialized. Delegates reconnection debouncing to get_redis_manager."""
        from backend_app.backend.redis_manager import get_redis_manager
        # Always call get_redis_manager; it is debounce-safe and handles reconnects internally
        mgr = await get_redis_manager()
        if mgr is not None:
            self._redis_manager = mgr
    
    async def get_client(self) -> Optional[object]:
        """
        Get the shared Redis client (cache database by default).
        
        DEV_MODE: Returns MockRedisClient if Redis unavailable or in DEV_MODE.
        
        Returns:
            Redis client from shared pool or mock client in DEV_MODE
            
        Usage:
            redis_client = await redis_manager.get_client()
            await redis_client.setex(key, ttl, value)
        """
        if DEV_MODE:
            logger.info("DEV_MODE: Using MockRedisClient")
            return self._mock_client
        
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
        if DEV_MODE:
            return await self._mock_client.get(key)
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            try:
                return await self._redis_manager.cache.get(key)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Cache GET failed for '{key}': {e}")
                return None
        return None

    async def set(self, key: str, value: Any, **kwargs):
        """Proxy to cache set method."""
        if DEV_MODE:
            return await self._mock_client.set(key, value, **kwargs)
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            try:
                return await self._redis_manager.cache.set(key, value, **kwargs)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Cache SET failed for '{key}': {e}")
                return False
        return False

    async def delete(self, *keys: str):
        """Proxy to cache delete method."""
        if DEV_MODE:
            return await self._mock_client.delete(*keys)
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            try:
                return await self._redis_manager.cache.delete(*keys)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Cache DELETE failed for '{keys}': {e}")
                return 0
        return 0
    
    async def sismember(self, key: str, member: Any) -> bool:
        """Proxy to sismember method."""
        if DEV_MODE:
            return await self._mock_client.sismember(key, member)
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            try:
                return await self._redis_manager.cache.sismember(key, member)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"SISMEMBER failed for '{key}': {e}")
                return False
        return False
    
    async def sadd(self, key: str, *members: Any) -> int:
        """Proxy to sadd method."""
        if DEV_MODE:
            return await self._mock_client.sadd(key, *members)
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            try:
                return await self._redis_manager.cache.sadd(key, *members)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"SADD failed for '{key}': {e}")
                return 0
        return 0
    
    async def expire(self, key: str, seconds: int) -> bool:
        """Proxy to expire method."""
        if DEV_MODE:
            return await self._mock_client.expire(key, seconds)
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            try:
                return await self._redis_manager.cache.expire(key, seconds)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"EXPIRE failed for '{key}': {e}")
                return False
        return False
    
    async def xadd(self, stream: str, data: dict, **kwargs):
        """Proxy to xadd method (for streams)."""
        if DEV_MODE:
            return await self._mock_client.xadd(stream, data, **kwargs)
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.events:
            try:
                import json
                body = {
                    k: json.dumps(v) if not isinstance(v, str) else v
                    for k, v in data.items()
                }
                return await self._redis_manager.events.xadd(stream, body, **kwargs)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"XADD failed for '{stream}': {e}")
                return None
        return None
    
    async def xrevrange(self, stream: str, count: int = None, **kwargs):
        """Proxy to xrevrange method (for streams)."""
        if DEV_MODE:
            return await self._mock_client.xrevrange(stream, count, **kwargs)
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.events:
            try:
                return await self._redis_manager.events.xrevrange(stream, count=count, **kwargs)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"XREVRANGE failed for '{stream}': {e}")
                return []
        return []

    async def xadd(
        self,
        stream: str,
        payload: dict,
        max_retries: int = 3,
        raise_on_error: Optional[bool] = None,
    ):
        """Proxy to events xadd method with retries and durable PublishError raising for critical streams."""
        await self._ensure_manager()
        is_critical = (raise_on_error is True) or (stream in TRADING_CRITICAL_STREAMS)
        attempts = max_retries if is_critical else 1
        last_exception = None

        if self._redis_manager and self._redis_manager.events:
            import asyncio
            import json
            body = {
                k: json.dumps(v) if not isinstance(v, str) else v
                for k, v in payload.items()
            }
            for attempt in range(1, attempts + 1):
                try:
                    entry_id = await self._redis_manager.events.xadd(stream, body)
                    if entry_id:
                        return entry_id
                except Exception as e:
                    last_exception = e
                    import logging
                    logging.getLogger(__name__).warning(
                        f"XADD attempt {attempt}/{attempts} failed for '{stream}': {e}"
                    )
                
                if attempt < attempts:
                    await asyncio.sleep(0.1 * (2 ** (attempt - 1)))

            if is_critical:
                error_msg = (
                    f"CRITICAL: Failed to publish message to stream '{stream}' "
                    f"after {attempts} attempts. Last error: {last_exception}"
                )
                import logging
                logging.getLogger(__name__).error(error_msg)
                try:
                    import sentry_sdk
                    sentry_sdk.capture_message(error_msg, level="error")
                except Exception:
                    pass
                raise PublishError(error_msg) from last_exception

        elif is_critical:
            error_msg = f"CRITICAL: Redis manager unavailable for trading stream '{stream}'"
            import logging
            logging.getLogger(__name__).error(error_msg)
            raise PublishError(error_msg)

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

    def pubsub(self):
        """Return pubsub instance from cache client or mock pubsub."""
        if DEV_MODE:
            return MockRedisPubSub()
        if self._redis_manager and self._redis_manager.cache:
            return self._redis_manager.cache.pubsub()
        return MockRedisPubSub()


# Global singleton instance
redis_manager = SharedRedisManager()
