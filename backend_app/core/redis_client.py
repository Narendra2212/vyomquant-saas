"""
core/redis_client.py — Redis connection manager.

DEV_MODE: Falls back to in-memory mock if Redis unavailable.
"""

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("Redis")

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
    
    async def set(self, key: str, value: str, expire: int = None, **kwargs):
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


class MockRedisPubSub:
    """Mock pub/sub client"""
    
    async def get_message(self, ignore_subscribe_messages=False, timeout=0):
        return None
    
    async def listen(self):
        return iter([])
    
    async def close(self):
        pass


class RedisClient:
    """Redis client with DEV_MODE fallback"""
    
    _instance: Optional[Any] = None
    
    def __init__(self):
        self._client: Optional[Any] = None
        self._mock = MockRedisClient()
    
    async def connect(self, url: str = None):
        """Connect to Redis or use mock in DEV_MODE"""
        if DEV_MODE and not url:
            logger.info("DEV_MODE: Using MockRedisClient")
            self._client = self._mock
            return
        
        try:
            import redis.asyncio as redis
            redis_url = url or os.environ.get("REDIS_URL", "redis://localhost:6379")
            self._client = await redis.from_url(redis_url, decode_responses=True)
            logger.info("Redis connected")
        except Exception as e:
            if DEV_MODE:
                logger.warning(f"DEV_MODE: Redis failed, using mock: {e}")
                self._client = self._mock
            else:
                raise
    
    def __getattr__(self, name):
        if self._client is None:
            if DEV_MODE:
                return getattr(self._mock, name)
            raise RuntimeError("RedisClient is not connected. Call connect() first.")
        return getattr(self._client, name)

    async def get(self, key: str) -> Optional[str]:
        if not self._client:
            await self.connect()
        return await self._client.get(key)
    
    async def set(self, key: str, value: str, expire: int = None, **kwargs):
        if not self._client:
            await self.connect()
        if expire is not None and 'ex' not in kwargs:
            kwargs['ex'] = expire
        return await self._client.set(key, value, **kwargs)
    
    async def delete(self, key: str):
        if not self._client:
            await self.connect()
        await self._client.delete(key)
    
    async def ping(self) -> bool:
        if not self._client:
            await self.connect()
        try:
            return await self._client.ping()
        except Exception:
            return False


# Global Redis client instance
redis_client = RedisClient()


async def test_redis() -> dict:
    """
    Test Redis connection and return status.
    DEV_MODE: Always returns OK with mock.
    """
    try:
        await redis_client.connect()
        is_connected = await redis_client.ping()
        return {
            "status": "ok" if is_connected else "disconnected",
            "type": "mock" if isinstance(redis_client._client, MockRedisClient) else "redis",
            "message": "Redis mock active" if isinstance(redis_client._client, MockRedisClient) else "Redis connected"
        }
    except Exception as e:
        if DEV_MODE:
            logger.warning(f"DEV_MODE: Redis test failed, using mock: {e}")
            return {
                "status": "ok",
                "type": "mock",
                "message": f"DEV_MODE mock active: {e}"
            }
        return {
            "status": "error",
            "type": "none",
            "message": str(e)
        }


async def get_cache(key: str) -> Optional[Any]:
    """Get value from cache"""
    data = await redis_client.get(key)
    if data:
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return data
    return None


async def set_cache(key: str, value: Any, expire: int = 300):
    """Set value in cache"""
    try:
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        await redis_client.set(key, str(value), expire=expire)
    except Exception as e:
        logger.warning(f"Cache set failed: {e}")
