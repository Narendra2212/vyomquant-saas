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
    
    @property
    def redis(self):
        """Return self for compatibility with redis_manager.redis pattern."""
        return self
    
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

    async def getdel(self, key: str) -> Optional[str]:
        """Read a key and remove it in one step (Redis GETDEL).

        ``dict.pop`` is a single bytecode-level operation on a plain dict and this
        coroutine awaits nothing, so no other task can observe the value between the
        read and the removal. That is the same guarantee real GETDEL gives, which is
        what makes this usable for a single-use credential.
        """
        return self._store.pop(key, None)

    async def incr(self, key: str) -> int:
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    async def decr(self, key: str) -> int:
        val = int(self._store.get(key, 0)) - 1
        self._store[key] = str(val)
        return val

    async def incrby(self, key: str, amount: int) -> int:
        val = int(self._store.get(key, 0)) + amount
        self._store[key] = str(val)
        return val

    async def decrby(self, key: str, amount: int) -> int:
        val = int(self._store.get(key, 0)) - amount
        self._store[key] = str(val)
        return val

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

    async def srem(self, key: str, *members: Any) -> int:
        s = self._store.get(key)
        if not isinstance(s, set):
            return 0
        removed = 0
        for m in members:
            if m in s:
                s.remove(m)
                removed += 1
        return removed

    async def scard(self, key: str) -> int:
        s = self._store.get(key)
        return len(s) if isinstance(s, set) else 0

    async def smembers(self, key: str) -> set:
        s = self._store.get(key)
        return set(s) if isinstance(s, set) else set()

    async def eval_lua(self, script: str, keys: list, args: list) -> list:
        """
        FIN-CRITICAL-004 FIX: Execute Redis Lua script atomically.
        
        This method provides Lua script execution for atomic operations.
        In DEV_MODE, it provides a simplified implementation.
        """
        # Simplified implementation for DEV_MODE mock
        # In production, this would use actual Redis EVAL command
        key = keys[0] if keys else None
        lock_payload = args[0] if args else None
        
        if not key:
            return [0, False]
        
        current_value = self._store.get(key)
        
        # Check if key exists and is a completed result
        if current_value and not str(current_value).startswith('processing'):
            return [1, current_value]  # Return cached result
        
        # If key doesn't exist, set processing lock
        if not current_value:
            self._store[key] = lock_payload
            return [0, lock_payload]  # Lock acquired
        
        # Key exists and is processing - lock not acquired
        return [0, False]

    async def eval(self, script: str, num_keys: int, *keys_and_args) -> list:
        """
        FIN-CRITICAL-004 FIX: Standard Redis EVAL interface.
        
        This provides the standard Redis EVAL interface that matches the aioredis API.
        """
        # Convert keys_and_args to proper format
        keys = list(keys_and_args[:num_keys]) if num_keys > 0 else []
        args = list(keys_and_args[num_keys:]) if num_keys > 0 else list(keys_and_args)
        
        return await self.eval_lua(script, keys, args)

    async def lpush(self, key: str, *values: Any) -> int:
        if key not in self._store or not isinstance(self._store[key], list):
            self._store[key] = []
        for v in values:
            self._store[key].insert(0, str(v))
        return len(self._store[key])

    async def rpush(self, key: str, *values: Any) -> int:
        if key not in self._store or not isinstance(self._store[key], list):
            self._store[key] = []
        for v in values:
            self._store[key].append(str(v))
        return len(self._store[key])

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

    async def lrange(self, key: str, start: int, end: int) -> list:
        lst = self._store.get(key, [])
        if not isinstance(lst, list):
            return []
        if end == -1:
            return lst[start:]
        return lst[start:end + 1]

    async def llen(self, key: str) -> int:
        lst = self._store.get(key, [])
        return len(lst) if isinstance(lst, list) else 0

    async def rpush(self, key: str, *values: Any) -> int:
        if key not in self._store or not isinstance(self._store[key], list):
            self._store[key] = []
        for v in values:
            self._store[key].append(str(v))
        return len(self._store[key])

    async def lpop(self, key: str) -> Optional[str]:
        lst = self._store.get(key, [])
        if isinstance(lst, list) and lst:
            return lst.pop(0)
        return None

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
        import fnmatch
        return [k for k in self._store.keys() if pattern == "*" or fnmatch.fnmatch(k, pattern)]
    
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
    
    async def xreadgroup(self, group: str, consumer: str, streams: dict, count: int = 10, block: int = 1000):
        """Mock xreadgroup for streams - returns empty list"""
        return []
    
    async def xack(self, stream: str, group: str, *ids: str):
        """Mock xack for streams - always returns 0"""
        return 0
    
    async def xgroup_create(self, stream: str, group: str, id: str = "$", mkstream: bool = False):
        """Mock xgroup_create for streams - no-op"""
        return True
    
    async def zpopmin(self, key: str, count: int = 1):
        """Mock zpopmin for sorted sets - returns empty list"""
        return []
    
    async def zadd(self, key: str, mapping: dict, *args, **kwargs) -> int:
        if key not in self._store or not isinstance(self._store[key], dict):
            self._store[key] = {}
        added = 0
        for member, score in mapping.items():
            if member not in self._store[key]:
                added += 1
            self._store[key][member] = float(score)
        return added

    async def zrem(self, key: str, *members: Any) -> int:
        d = self._store.get(key)
        if not isinstance(d, dict):
            return 0
        removed = 0
        for m in members:
            if m in d:
                del d[m]
                removed += 1
        return removed

    async def zcard(self, key: str) -> int:
        d = self._store.get(key)
        return len(d) if isinstance(d, dict) else 0

    async def zrange(self, key: str, start: int, end: int, **kwargs) -> list:
        d = self._store.get(key)
        if not isinstance(d, dict):
            return []
        sorted_items = sorted(d.items(), key=lambda x: x[1])
        keys = [item[0] for item in sorted_items]
        if end == -1:
            return keys[start:]
        return keys[start:end + 1]

    async def zrangebyscore(self, key: str, min: Any, max: Any, count: Optional[int] = None, **kwargs) -> list:
        d = self._store.get(key)
        if not isinstance(d, dict):
            return []
        matching = [
            (k, v) for k, v in d.items()
            if (min == '-inf' or v >= float(min)) and (max == '+inf' or v <= float(max))
        ]
        sorted_items = sorted(matching, key=lambda x: x[1])
        keys = [item[0] for item in sorted_items]
        if count is not None:
            keys = keys[:count]
        return keys

    async def zremrangebyscore(self, key: str, min: Any, max: Any) -> int:
        d = self._store.get(key)
        if not isinstance(d, dict):
            return 0
        to_remove = [k for k, v in d.items() if (min == '-inf' or v >= float(min)) and (max == '+inf' or v <= float(max))]
        for k in to_remove:
            del d[k]
        return len(to_remove)

    async def incr(self, key: str, amount: int = 1) -> int:
        val = int(self._store.get(key, 0)) + amount
        self._store[key] = str(val)
        return val

    async def decr(self, key: str, amount: int = 1) -> int:
        val = int(self._store.get(key, 0)) - amount
        self._store[key] = str(val)
        return val

    async def incrbyfloat(self, key: str, amount: float) -> float:
        val = float(self._store.get(key, 0.0)) + float(amount)
        self._store[key] = str(val)
        return val



class MockRedisPubSub:
    """Mock pub/sub client"""
    
    async def get_message(self, ignore_subscribe_messages=False, timeout=0):
        return None
    
    async def listen(self):
        return iter([])
    
    async def close(self):
        pass


#: Read-and-consume in one server-side step, for clients with no native ``GETDEL``.
#: Redis executes a script to completion before serving another command, so the GET and
#: the DEL here cannot interleave with a second redemption of the same key.
_GETDEL_LUA = (
    "local v = redis.call('GET', KEYS[1]) "
    "if v then redis.call('DEL', KEYS[1]) end "
    "return v"
)


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
    _test_mode = False  # Test-only seam to bypass reassignment for dependency injection
    
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
        # Test mode: skip reassignment to allow dependency injection
        if self._test_mode:
            return
        
        from backend_app.backend.redis_manager import get_redis_manager
        # Always call get_redis_manager; it is debounce-safe and handles reconnects internally
        mgr = await get_redis_manager()
        if mgr is not None:
            self._redis_manager = mgr
    
    def _set_test_mode(self, enabled: bool = True):
        """
        Test-only seam to enable/disable test mode.
        When enabled, _ensure_manager() skips reassignment to allow dependency injection.
        Must be called before any operations that would trigger _ensure_manager().
        """
        self._test_mode = enabled
    
    def _bypass_dev_mode(self, enabled: bool = True):
        """
        Test-only seam to bypass DEV_MODE check for testing fail-soft behavior.
        When enabled, get()/set()/delete() will exercise the real Redis path even in DEV_MODE.
        Must be called before any cache operations.
        """
        self._dev_mode_bypass = enabled
    
    async def get_client(self) -> Optional[object]:
        """
        Get the shared Redis client (cache database by default).
        
        DEV_MODE: Returns MockRedisClient if Redis unavailable or in DEV_MODE.
        PRODUCTION: FAIL-CLOSED if Redis unavailable for critical operations.
        
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
        
        # FAIL-CLOSED: Check if Redis is available in production
        if self._redis_manager is None or self._redis_manager.cache is None:
            env = os.environ.get("ENV", "development").lower()
            if env == "production":
                raise RuntimeError(
                    "CRITICAL: Redis unavailable in production. "
                    "This is a fail-closed safety mechanism to prevent "
                    "unsafe operation without critical infrastructure."
                )
            else:
                logger.warning("Redis unavailable in development, continuing with degraded behavior")
        
        return self._redis_manager.cache if self._redis_manager else None
    
    async def get_queue_client(self) -> Optional[object]:
        """Get queue database client (DB 1)."""
        await self._ensure_manager()
        
        # FAIL-CLOSED: Check if Redis is available in production
        if self._redis_manager is None or self._redis_manager.queue is None:
            env = os.environ.get("ENV", "development").lower()
            if env == "production":
                raise RuntimeError(
                    "CRITICAL: Redis queue unavailable in production. "
                    "This is a fail-closed safety mechanism to prevent "
                    "unsafe operation without critical infrastructure."
                )
        
        return self._redis_manager.queue if self._redis_manager else None
    
    async def get_events_client(self) -> Optional[object]:
        """Get events database client (DB 2)."""
        await self._ensure_manager()
        
        # FAIL-CLOSED: Check if Redis is available in production
        if self._redis_manager is None or self._redis_manager.events is None:
            env = os.environ.get("ENV", "development").lower()
            if env == "production":
                raise RuntimeError(
                    "CRITICAL: Redis events unavailable in production. "
                    "This is a fail-closed safety mechanism to prevent "
                    "unsafe operation without critical infrastructure."
                )
        
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
    
    async def eval_lua(self, script: str, keys: list, args: list) -> list:
        """
        Execute Redis Lua script atomically.
        
        This method provides Lua script execution for atomic operations.
        In DEV_MODE, it uses the mock client's implementation.
        In production, it delegates to the real Redis client.
        """
        client = await self._get_active_cache()
        if client is None:
            raise RuntimeError("Redis client not available for eval_lua")
        
        # Try to use the standard eval method first
        if hasattr(client, 'eval'):
            return await client.eval(script, len(keys), *keys, *args)
        # Fallback to eval_lua if available (for mock client)
        elif hasattr(client, 'eval_lua'):
            return await client.eval_lua(script, keys, args)
        else:
            raise AttributeError(f"Redis client {type(client)} does not support Lua script execution")
    
    async def eval(self, script: str, num_keys: int, *keys_and_args) -> list:
        """
        Standard Redis EVAL interface.
        
        This provides the standard Redis EVAL interface that matches the aioredis API.
        """
        client = await self._get_active_cache()
        if client is None:
            raise RuntimeError("Redis client not available for eval")
        
        if hasattr(client, 'eval'):
            return await client.eval(script, num_keys, *keys_and_args)
        elif hasattr(client, 'eval_lua'):
            # Convert to eval_lua format for mock client
            keys = list(keys_and_args[:num_keys]) if num_keys > 0 else []
            args = list(keys_and_args[num_keys:]) if num_keys > 0 else list(keys_and_args)
            return await client.eval_lua(script, keys, args)
        else:
            raise AttributeError(f"Redis client {type(client)} does not support Lua script execution")

    async def _get_active_cache(self):
        """Get active Redis cache client or mock client in non-production environments."""
        if DEV_MODE and not getattr(self, '_dev_mode_bypass', False):
            return self._mock_client
        
        await self._ensure_manager()
        if self._redis_manager and getattr(self._redis_manager, "cache", None):
            return self._redis_manager.cache
        
        from backend_app.core.safety_config import get_vyomquant_mode
        if get_vyomquant_mode() != "production":
            return self._mock_client
        return None

    async def keys(self, pattern: str = "*") -> list:
        """Proxy to cache keys method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.keys(pattern)
            except Exception as e:
                logger.warning(f"Cache KEYS failed: {e}")
        return []

    async def get(self, key: str):
        """Proxy to cache get method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.get(key)
            except Exception as e:
                logger.warning(f"Cache GET failed for '{key}': {e}")
        return None

    async def set(self, key: str, value: Any, **kwargs):
        """Proxy to cache set method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.set(key, value, **kwargs)
            except Exception as e:
                logger.warning(f"Cache SET failed for '{key}': {e}")
        return False

    async def delete(self, *keys: str):
        """Proxy to cache delete method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.delete(*keys)
            except Exception as e:
                logger.warning(f"Cache DELETE failed for '{keys}': {e}")
        return 0
    
    async def sismember(self, key: str, member: Any) -> bool:
        """Proxy to sismember method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.sismember(key, member)
            except Exception as e:
                logger.warning(f"SISMEMBER failed for '{key}': {e}")
        return False
    
    async def sadd(self, key: str, *members: Any) -> int:
        """Proxy to sadd method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.sadd(key, *members)
            except Exception as e:
                logger.warning(f"SADD failed for '{key}': {e}")
        return 0

    async def scard(self, key: str) -> int:
        """Proxy to scard method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.scard(key)
            except Exception as e:
                logger.warning(f"SCARD failed for '{key}': {e}")
        return 0

    async def smembers(self, key: str) -> set:
        """Proxy to smembers method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.smembers(key)
            except Exception as e:
                logger.warning(f"SMEMBERS failed for '{key}': {e}")
        return set()
    
    async def exists(self, key: str) -> bool:
        """Proxy to exists method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.exists(key)
            except Exception as e:
                logger.warning(f"EXISTS failed for '{key}': {e}")
        return False

    async def setex(self, key: str, ttl: int, value: Any):
        """Proxy to cache setex method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.setex(key, ttl, value)
            except Exception as e:
                logger.warning(f"SETEX failed for '{key}': {e}")
        return None

    async def getdel(self, key: str) -> Optional[str]:
        """Read a key and delete it **atomically**; returns the value, or ``None``.

        The read and the delete are one indivisible step, which is the whole point:
        this is the primitive a single-use credential is redeemed through, and a
        ``GET`` followed by a separate ``DEL`` leaves a window in which two concurrent
        redemptions both read the value before either removes it — i.e. the credential
        is used twice. Three paths, tried in order:

        1. Native ``GETDEL`` (Redis 6.2+, ``redis-py`` 4+, and ``MockRedisClient``).
        2. A one-shot Lua script, which Redis runs to completion without interleaving.
        3. Nothing — ``None``, with an error logged.

        (3) deliberately does **not** fall back to ``GET`` + ``DEL``. A caller asking
        for this method is asking for single-use semantics; quietly handing back a
        racy approximation would turn a replayable credential into a silent outcome
        rather than a loud one. Returning ``None`` fails the redemption closed.

        A missing client, a transport error and a genuinely absent key are all
        ``None``: from a caller's point of view "no value, and nothing was consumed".
        """
        client = await self._get_active_cache()
        if client is None:
            return None

        native = getattr(client, "getdel", None)
        if native is not None:
            try:
                return await native(key)
            except Exception as e:
                logger.warning(f"GETDEL failed for '{key}': {e}")
                return None

        evaluate = getattr(client, "eval", None)
        if evaluate is not None:
            try:
                value = await evaluate(_GETDEL_LUA, 1, key)
            except Exception as e:
                logger.warning(f"GETDEL (via EVAL) failed for '{key}': {e}")
                return None
            # A client whose ``eval`` is a stub rather than a real script evaluator
            # (several test doubles in this repo are) returns a shape this script
            # cannot produce. Treat that as "unsupported", never as a hit.
            if value is None or isinstance(value, (str, bytes)):
                return value
            logger.error(
                "Cache client %s returned %s from EVAL; its eval() is not a real "
                "script evaluator, so no atomic GETDEL is available.",
                type(client).__name__,
                type(value).__name__,
            )
            return None

        logger.error(
            "Cache client %s supports neither GETDEL nor EVAL, so a value cannot be "
            "read and consumed atomically; refusing rather than racing.",
            type(client).__name__,
        )
        return None

    async def incr(self, key: str) -> int:
        """Proxy to cache incr method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.incr(key)
            except Exception as e:
                logger.warning(f"INCR failed for '{key}': {e}")
        return 0

    async def decr(self, key: str) -> int:
        """Proxy to cache decr method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.decr(key)
            except Exception as e:
                logger.warning(f"DECR failed for '{key}': {e}")
        return 0

    async def incrby(self, key: str, amount: int) -> int:
        """Proxy to cache incrby method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.incrby(key, amount)
            except Exception as e:
                logger.warning(f"INCRBY failed for '{key}': {e}")
        return 0

    async def decrby(self, key: str, amount: int) -> int:
        """Proxy to cache decrby method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.decrby(key, amount)
            except Exception as e:
                logger.warning(f"DECRBY failed for '{key}': {e}")
        return 0

    async def lrange(self, key: str, start: int, end: int) -> list:
        """Proxy to lrange method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.lrange(key, start, end)
            except Exception as e:
                logger.warning(f"LRANGE failed for '{key}': {e}")
        return []

    async def llen(self, key: str) -> int:
        """Proxy to llen method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.llen(key)
            except Exception as e:
                logger.warning(f"LLEN failed for '{key}': {e}")
        return 0

    async def rpush(self, key: str, *values: Any) -> int:
        """Proxy to rpush method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.rpush(key, *values)
            except Exception as e:
                logger.warning(f"RPUSH failed for '{key}': {e}")
        return 0

    async def lpop(self, key: str) -> Optional[str]:
        """Proxy to lpop method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.lpop(key)
            except Exception as e:
                logger.warning(f"LPOP failed for '{key}': {e}")
        return None
    
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
        client = await self._get_active_cache()
        if client:
            try:
                return await client.setex(key, ttl, value)
            except Exception as e:
                logger.warning(f"Cache SETEX failed for '{key}': {e}")
        return None

    async def lrange(self, key: str, start: int, end: int) -> list:
        """Proxy to lrange method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.lrange(key, start, end)
            except Exception as e:
                logger.warning(f"LRANGE failed for '{key}': {e}")
        return []

    async def llen(self, key: str) -> int:
        """Proxy to llen method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.llen(key)
            except Exception as e:
                logger.warning(f"LLEN failed for '{key}': {e}")
        return 0

    async def lpush(self, key: str, *values: Any) -> int:
        """Proxy to lpush method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.lpush(key, *values)
            except Exception as e:
                logger.warning(f"LPUSH failed for '{key}': {e}")
        return 0

    async def rpush(self, key: str, *values: Any) -> int:
        """Proxy to rpush method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.rpush(key, *values)
            except Exception as e:
                logger.warning(f"RPUSH failed for '{key}': {e}")
        return 0

    async def lpop(self, key: str) -> Optional[str]:
        """Proxy to lpop method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.lpop(key)
            except Exception as e:
                logger.warning(f"LPOP failed for '{key}': {e}")
        return None

    async def zremrangebyscore(self, key: str, min: Any, max: Any) -> int:
        """Proxy to cache zremrangebyscore method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.zremrangebyscore(key, min, max)
            except Exception as e:
                logger.warning(f"ZREMRANGEBYSCORE failed for '{key}': {e}")
        return 0

    async def zcard(self, key: str) -> int:
        """Proxy to cache zcard method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.zcard(key)
            except Exception as e:
                logger.warning(f"ZCARD failed for '{key}': {e}")
        return 0

    async def zrange(self, key: str, start: int, end: int, **kwargs) -> list:
        """Proxy to cache zrange method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.zrange(key, start, end, **kwargs)
            except Exception as e:
                logger.warning(f"ZRANGE failed for '{key}': {e}")
        return []

    async def zadd(self, key: str, mapping: dict, *args, **kwargs) -> int:
        """Proxy to cache zadd method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.zadd(key, mapping, *args, **kwargs)
            except Exception as e:
                logger.warning(f"ZADD failed for '{key}': {e}")
        return 0

    async def expire(self, key: str, time: int, *args, **kwargs) -> bool:
        """Proxy to cache expire method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.expire(key, time, *args, **kwargs)
            except Exception as e:
                logger.warning(f"EXPIRE failed for '{key}': {e}")
        return False

    async def incr(self, key: str, amount: int = 1) -> int:
        """Proxy to cache incr method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.incr(key, amount)
            except Exception as e:
                logger.warning(f"INCR failed for '{key}': {e}")
        return 0

    async def decr(self, key: str, amount: int = 1) -> int:
        """Proxy to cache decr method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.decr(key, amount)
            except Exception as e:
                logger.warning(f"DECR failed for '{key}': {e}")
        return 0

    async def incrbyfloat(self, key: str, amount: float) -> float:
        """Proxy to cache incrbyfloat method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.incrbyfloat(key, amount)
            except Exception as e:
                logger.warning(f"INCRBYFLOAT failed for '{key}': {e}")
        return 0.0

    async def hset(self, key: str, name: str = None, value: str = None, mapping: dict = None) -> int:
        """Proxy to cache hset method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.hset(key, name=name, value=value, mapping=mapping)
            except Exception as e:
                logger.warning(f"HSET failed for '{key}': {e}")
        return 0

    async def hget(self, key: str, name: str) -> Optional[str]:
        """Proxy to cache hget method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.hget(key, name)
            except Exception as e:
                logger.warning(f"HGET failed for '{key}': {e}")
        return None

    async def hgetall(self, key: str) -> dict:
        """Proxy to cache hgetall method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.hgetall(key)
            except Exception as e:
                logger.warning(f"HGETALL failed for '{key}': {e}")
        return {}

    async def hdel(self, key: str, *names: str) -> int:
        """Proxy to cache hdel method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.hdel(key, *names)
            except Exception as e:
                logger.warning(f"HDEL failed for '{key}': {e}")
        return 0

    async def srem(self, key: str, *members: Any) -> int:
        """Proxy to cache srem method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.srem(key, *members)
            except Exception as e:
                logger.warning(f"SREM failed for '{key}': {e}")
        return 0

    async def zrem(self, key: str, *members: Any) -> int:
        """Proxy to cache zrem method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.zrem(key, *members)
            except Exception as e:
                logger.warning(f"ZREM failed for '{key}': {e}")
        return 0

    async def zrangebyscore(self, key: str, min: Any, max: Any, **kwargs) -> list:
        """Proxy to zrangebyscore method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.zrangebyscore(key, min, max, **kwargs)
            except Exception as e:
                logger.warning(f"ZRANGEBYSCORE failed for '{key}': {e}")
        return []

    async def zremrangebyscore(self, key: str, min: Any, max: Any) -> int:
        """Proxy to zremrangebyscore method."""
        client = await self._get_active_cache()
        if client:
            try:
                return await client.zremrangebyscore(key, min, max)
            except Exception as e:
                logger.warning(f"ZREMRANGEBYSCORE failed for '{key}': {e}")
        return 0

    @property
    def pool(self):
        """Return self for compatibility (health check calls pool.ping())."""
        return self

    @property
    def redis(self):
        """
        Return self for backward compatibility with legacy .redis access pattern.
        The legacy code expects a single client with both cache and stream methods.
        This proxy provides unified access to both cache and events operations.
        """
        return self

    def pubsub(self):
        """Return pubsub instance from cache client or mock pubsub."""
        if DEV_MODE:
            return MockRedisPubSub()
        if self._redis_manager and self._redis_manager.cache:
            return self._redis_manager.cache.pubsub()
        return MockRedisPubSub()

    async def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: dict,
        count: int = 10,
        block: int = 1000,
    ):
        """Read from a Redis stream consumer group with fail-soft behavior."""
        if DEV_MODE:
            return await self._mock_client.xreadgroup(group, consumer, streams, count, block)
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.events:
            try:
                return await self._redis_manager.events.xreadgroup(
                    group, consumer, streams, count=count, block=block
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Redis XREADGROUP failed for '{group}': {e}")
                return []
        return []

    async def xack(self, stream: str, group: str, *ids: str):
        """Acknowledge messages from a Redis stream consumer group with fail-soft behavior."""
        if DEV_MODE:
            return 0  # Mock: always return success
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.events:
            try:
                return await self._redis_manager.events.xack(stream, group, *ids)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Redis XACK failed for '{stream}': {e}")
                return 0
        return 0

    async def xgroup_create(self, stream: str, group: str, id: str = "$", mkstream: bool = False):
        """Create a Redis stream consumer group with fail-soft behavior."""
        if DEV_MODE:
            return True  # Mock: always return success
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.events:
            try:
                return await self._redis_manager.events.xgroup_create(stream, group, id=id, mkstream=mkstream)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Redis XGROUP_CREATE failed for '{stream}': {e}")
                return False
        return False

    async def zpopmin(self, key: str, count: int = 1):
        """Pop minimum scores from sorted set with fail-soft behavior."""
        if DEV_MODE:
            return await self._mock_client.zpopmin(key, count)
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            try:
                return await self._redis_manager.cache.zpopmin(key, count)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Redis ZPOPMIN failed for '{key}': {e}")
                return []
        return []

    async def publish(self, channel: str, message: str):
        """Publish to a Redis channel with fail-soft behavior."""
        if DEV_MODE:
            return await self._mock_client.publish(channel, message)
        
        await self._ensure_manager()
        if self._redis_manager and self._redis_manager.cache:
            try:
                return await self._redis_manager.cache.publish(channel, message)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Redis PUBLISH failed for '{channel}': {e}")
                return 0
        return 0


# Global singleton instance
redis_manager = SharedRedisManager()
