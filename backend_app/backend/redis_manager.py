"""
backend/redis_manager.py — REDIS DATABASE MANAGER

STEP 3: REDIS ARCHITECTURE — SPLIT REDIS USAGE

Manages 3 separate Redis databases for different use cases:
  - DB 0: Cache (short-lived, LRU eviction)
  - DB 1: Task Queue (persistent, AOF enabled)
  - DB 2: Events/Streams (time-series, capped)

BENEFITS:
  - No contention between cache and queue operations
  - Different eviction policies per use case
  - Better performance isolation
  - Easier monitoring and debugging
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Reconnect cooldown (seconds). Env override: REDIS_RECONNECT_COOLDOWN
_RECONNECT_COOLDOWN: float = float(os.getenv("REDIS_RECONNECT_COOLDOWN", "3.0"))

# =============================================================================
# REDIS DATABASE CONFIGURATION
# =============================================================================

# Database numbers (0-15 available, we use 0, 1, 2)
REDIS_DB_CACHE = 0    # Short-lived cache data
REDIS_DB_QUEUE = 1    # Task queue (persistent)
REDIS_DB_EVENTS = 2   # Event streams and time-series

# Redis URL from environment
# REDIS_URL takes precedence over individual host/port/password
REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)


def get_redis_url(db: int = 0) -> str:
    """Get Redis URL for specific database.

    If REDIS_URL is provided, it is used as the base URL with the DB number appended.
    Otherwise, constructs URL from REDIS_HOST, REDIS_PORT, REDIS_PASSWORD.

    Supports both redis:// and rediss:// schemes for TLS.
    """
    if REDIS_URL:
        # Parse existing REDIS_URL and append DB number
        # Handle URLs with existing path or query parameters
        from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

        parsed = urlparse(REDIS_URL)

        # Extract existing query parameters
        query_params = parse_qs(parsed.query)

        # Set DB via query parameter or path
        # Redis-py supports both ?db=0 and /0 syntax
        query_params['db'] = str(db)

        # Reconstruct URL with new DB parameter
        new_query = urlencode(query_params, doseq=True)
        new_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))

        return new_url
    else:
        # Fallback to host/port/password construction
        auth = f":{REDIS_PASSWORD}@" if REDIS_PASSWORD else ""
        return f"redis://{auth}{REDIS_HOST}:{REDIS_PORT}/{db}"


class RedisManager:
    """
    Manages multiple Redis database connections.
    
    Provides separate clients for:
    - Cache (DB 0): Fast access, LRU eviction
    - Queue (DB 1): Reliable task queue, AOF persistence
    - Events (DB 2): Time-series data, streams
    """
    
    _instance: Optional['RedisManager'] = None
    _instance_lock: Optional[asyncio.Lock] = None

    @classmethod
    def _get_instance_lock(cls) -> asyncio.Lock:
        """Lazily initialize instance lock — must be created inside a running event loop."""
        if cls._instance_lock is None:
            cls._instance_lock = asyncio.Lock()
        return cls._instance_lock

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._cache: Optional[aioredis.Redis] = None
        self._queue: Optional[aioredis.Redis] = None
        self._events: Optional[aioredis.Redis] = None
        self._connected: bool = False
        self._last_failed_at: float = 0.0   # monotonic timestamp of last failed connect()
        self._reconnect_lock: Optional[asyncio.Lock] = None  # per-instance debounce lock
        self._initialized = True

    def _get_reconnect_lock(self) -> asyncio.Lock:
        """Return the per-instance reconnect lock, lazily created inside the event loop."""
        if self._reconnect_lock is None:
            self._reconnect_lock = asyncio.Lock()
        return self._reconnect_lock
    
    async def initialize(self) -> bool:
        """
        Initialize all Redis connections.
        Returns True on success, False on failure (does not raise).
        """
        try:
            # Cache (DB 0) - short-lived, LRU eviction
            cache = await aioredis.from_url(
                get_redis_url(REDIS_DB_CACHE),
                encoding="utf-8",
                decode_responses=True,
                max_connections=50
            )

            # Queue (DB 1) - persistent, AOF
            queue = await aioredis.from_url(
                get_redis_url(REDIS_DB_QUEUE),
                encoding="utf-8",
                decode_responses=True,
                max_connections=50
            )

            # Events (DB 2) - time-series, streams
            events = await aioredis.from_url(
                get_redis_url(REDIS_DB_EVENTS),
                encoding="utf-8",
                decode_responses=True,
                max_connections=50
            )

            # Verify connectivity before committing to self.*
            await cache.ping()
            await queue.ping()
            await events.ping()

            self._cache = cache
            self._queue = queue
            self._events = events
            self._connected = True
            logger.info(
                f"[RedisManager] Connected to 3 databases: "
                f"cache(DB{REDIS_DB_CACHE}), queue(DB{REDIS_DB_QUEUE}), events(DB{REDIS_DB_EVENTS})"
            )
            return True

        except Exception as e:
            logger.error(f"[RedisManager] Failed to connect: {e}")
            self._connected = False
            self._last_failed_at = time.monotonic()
            return False

    async def try_reconnect(self) -> bool:
        """
        Debounced reconnection. At most one reconnection attempt runs at a time;
        other concurrent callers skip the attempt if already in-flight or
        if within the cooldown window following the most recent failure.

        Returns True if the manager is now connected, False otherwise.
        """
        if self._connected:
            return True

        now = time.monotonic()
        if now - self._last_failed_at < _RECONNECT_COOLDOWN:
            return False  # Still in cooldown — skip attempt

        lock = self._get_reconnect_lock()
        if lock.locked():
            # Another coroutine is already in a reconnection attempt — skip
            return self._connected

        async with lock:
            # Double-check inside lock in case a prior winner already succeeded
            if self._connected:
                return True

            now = time.monotonic()
            if now - self._last_failed_at < _RECONNECT_COOLDOWN:
                return False

            return await self.initialize()
    
    async def close(self):
        """Close all Redis connections."""
        if self._cache:
            await self._cache.close()
        if self._queue:
            await self._queue.close()
        if self._events:
            await self._events.close()
        logger.info("[RedisManager] All connections closed")
    
    @property
    def cache(self) -> Optional[aioredis.Redis]:
        """Get cache database client (DB 0). Returns None if not connected."""
        return self._cache

    @property
    def queue(self) -> Optional[aioredis.Redis]:
        """Get queue database client (DB 1). Returns None if not connected."""
        return self._queue

    @property
    def events(self) -> Optional[aioredis.Redis]:
        """Get events database client (DB 2). Returns None if not connected."""
        return self._events
    
    # =============================================================================
    # CACHE OPERATIONS (DB 0)
    # =============================================================================
    
    async def cache_get(self, key: str) -> Optional[str]:
        """Get value from cache."""
        try:
            return await self.cache.get(key)
        except Exception as e:
            logger.error(f"[Cache] Get error for {key}: {e}")
            return None
    
    async def cache_set(
        self,
        key: str,
        value: str,
        ttl: int = 300,  # 5 minutes default
    ) -> bool:
        """Set value in cache with TTL."""
        try:
            await self.cache.setex(key, ttl, value)
            return True
        except Exception as e:
            logger.error(f"[Cache] Set error for {key}: {e}")
            return False
    
    async def cache_delete(self, key: str) -> bool:
        """Delete value from cache."""
        try:
            await self.cache.delete(key)
            return True
        except Exception as e:
            logger.error(f"[Cache] Delete error for {key}: {e}")
            return False
    
    async def cache_get_json(self, key: str) -> Optional[Dict]:
        """Get JSON value from cache."""
        data = await self.cache_get(key)
        if data:
            return json.loads(data)
        return None
    
    async def cache_set_json(
        self,
        key: str,
        value: Dict,
        ttl: int = 300
    ) -> bool:
        """Set JSON value in cache."""
        return await self.cache_set(key, json.dumps(value), ttl)
    
    # =============================================================================
    # QUEUE OPERATIONS (DB 1)
    # =============================================================================
    
    async def queue_push(
        self,
        queue_name: str,
        item: Dict[str, Any]
    ) -> bool:
        """Push item to queue."""
        try:
            await self.queue.lpush(queue_name, json.dumps(item))
            return True
        except Exception as e:
            logger.error(f"[Queue] Push error for {queue_name}: {e}")
            return False
    
    async def queue_pop(
        self,
        queue_name: str,
        timeout: int = 0
    ) -> Optional[Dict]:
        """Pop item from queue (blocking if timeout > 0)."""
        try:
            if timeout > 0:
                result = await self.queue.brpop(queue_name, timeout=timeout)
                if result:
                    _, data = result
                    return json.loads(data)
                return None
            else:
                data = await self.queue.rpop(queue_name)
                if data:
                    return json.loads(data)
                return None
        except Exception as e:
            logger.error(f"[Queue] Pop error for {queue_name}: {e}")
            return None
    
    async def queue_length(self, queue_name: str) -> int:
        """Get queue length."""
        try:
            return await self.queue.llen(queue_name)
        except Exception as e:
            logger.error(f"[Queue] Length error for {queue_name}: {e}")
            return 0
    
    async def queue_peek(
        self,
        queue_name: str,
        count: int = 1
    ) -> List[Dict]:
        """Peek at queue items without removing."""
        try:
            items = await self.queue.lrange(queue_name, 0, count - 1)
            return [json.loads(item) for item in items]
        except Exception as e:
            logger.error(f"[Queue] Peek error for {queue_name}: {e}")
            return []
    
    async def queue_clear(self, queue_name: str) -> bool:
        """Clear all items from queue."""
        try:
            await self.queue.delete(queue_name)
            return True
        except Exception as e:
            logger.error(f"[Queue] Clear error for {queue_name}: {e}")
            return False
    
    # =============================================================================
    # EVENT OPERATIONS (DB 2)
    # =============================================================================
    
    async def event_publish(
        self,
        channel: str,
        event: Dict[str, Any]
    ) -> bool:
        """Publish event to channel."""
        try:
            await self.events.publish(channel, json.dumps(event))
            return True
        except Exception as e:
            logger.error(f"[Events] Publish error for {channel}: {e}")
            return False
    
    async def event_subscribe(self, *channels: str):
        """Subscribe to event channels."""
        try:
            pubsub = self.events.pubsub()
            await pubsub.subscribe(*channels)
            return pubsub
        except Exception as e:
            logger.error(f"[Events] Subscribe error: {e}")
            raise
    
    async def stream_add(
        self,
        stream_name: str,
        data: Dict[str, Any],
        maxlen: int = 10000,
        approximate: bool = True
    ) -> str:
        """Add entry to stream (time-series data)."""
        try:
            # Convert data to stream fields
            fields = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) 
                     for k, v in data.items()}
            
            entry_id = await self.events.xadd(
                stream_name,
                fields,
                maxlen=maxlen,
                approximate=approximate
            )
            return entry_id
        except Exception as e:
            logger.error(f"[Events] Stream add error for {stream_name}: {e}")
            raise
    
    async def stream_read(
        self,
        stream_name: str,
        count: int = 100,
        last_id: str = "0"
    ) -> List[Dict]:
        """Read entries from stream."""
        try:
            entries = await self.events.xread({stream_name: last_id}, count=count)
            results = []
            for stream, items in entries:
                for entry_id, fields in items:
                    # Parse fields
                    parsed = {k: json.loads(v) if v.startswith('{') or v.startswith('[') else v 
                             for k, v in fields.items()}
                    parsed['_id'] = entry_id
                    parsed['_stream'] = stream.decode() if isinstance(stream, bytes) else stream
                    results.append(parsed)
            return results
        except Exception as e:
            logger.error(f"[Events] Stream read error for {stream_name}: {e}")
            return []
    
    async def stream_range(
        self,
        stream_name: str,
        start: str = "-",
        end: str = "+",
        count: int = 100
    ) -> List[Dict]:
        """Get range of entries from stream."""
        try:
            entries = await self.events.xrange(stream_name, start, end, count=count)
            results = []
            for entry_id, fields in entries:
                parsed = {k: json.loads(v) if v.startswith('{') or v.startswith('[') else v 
                         for k, v in fields.items()}
                parsed['_id'] = entry_id
                results.append(parsed)
            return results
        except Exception as e:
            logger.error(f"[Events] Stream range error for {stream_name}: {e}")
            return []
    
    async def stream_trim(self, stream_name: str, maxlen: int = 10000) -> int:
        """Trim stream to maximum length."""
        try:
            return await self.events.xtrim(stream_name, maxlen=maxlen, approximate=True)
        except Exception as e:
            logger.error(f"[Events] Stream trim error for {stream_name}: {e}")
            return 0
    
    # =============================================================================
    # HEALTH CHECKS
    # =============================================================================
    
    async def health_check(self) -> Dict[str, Any]:
        """Check health of all Redis databases."""
        health = {
            "cache": {"status": "unknown"},
            "queue": {"status": "unknown"},
            "events": {"status": "unknown"},
        }
        
        try:
            await self.cache.ping()
            health["cache"]["status"] = "healthy"
            info = await self.cache.info("memory")
            health["cache"]["used_memory"] = info.get("used_memory_human", "unknown")
        except Exception as e:
            health["cache"]["status"] = "unhealthy"
            health["cache"]["error"] = str(e)
        
        try:
            await self.queue.ping()
            health["queue"]["status"] = "healthy"
            info = await self.queue.info("memory")
            health["queue"]["used_memory"] = info.get("used_memory_human", "unknown")
        except Exception as e:
            health["queue"]["status"] = "unhealthy"
            health["queue"]["error"] = str(e)
        
        try:
            await self.events.ping()
            health["events"]["status"] = "healthy"
            info = await self.events.info("memory")
            health["events"]["used_memory"] = info.get("used_memory_human", "unknown")
        except Exception as e:
            health["events"]["status"] = "unhealthy"
            health["events"]["error"] = str(e)
        
        return health


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

_redis_manager: Optional[RedisManager] = None
# Module-level debounce lock — lazily initialised inside an active event loop
_get_manager_lock: Optional[asyncio.Lock] = None
_get_manager_last_failed: float = 0.0


def _ensure_get_manager_lock() -> asyncio.Lock:
    global _get_manager_lock
    if _get_manager_lock is None:
        _get_manager_lock = asyncio.Lock()
    return _get_manager_lock


async def get_redis_manager() -> Optional[RedisManager]:
    """
    Get or create the global RedisManager instance.

    Debounce-safe: if initialization previously failed, concurrent callers
    share a single retry attempt instead of each spawning their own.
    Returns None (and logs a warning) if Redis is unavailable, so callers
    can fail-soft without exceptions propagating.
    """
    global _redis_manager, _get_manager_last_failed

    # Fast path — already initialised and connected
    if _redis_manager is not None and _redis_manager._connected:
        return _redis_manager

    now = time.monotonic()
    if now - _get_manager_last_failed < _RECONNECT_COOLDOWN:
        return _redis_manager  # Still in cooldown; return whatever we have (may be None)

    lock = _ensure_get_manager_lock()
    if lock.locked():
        return _redis_manager  # Another coroutine is already attempting — skip

    async with lock:
        # Double-check after acquiring
        if _redis_manager is not None and _redis_manager._connected:
            return _redis_manager

        now = time.monotonic()
        if now - _get_manager_last_failed < _RECONNECT_COOLDOWN:
            return _redis_manager

        if _redis_manager is None:
            _redis_manager = RedisManager()

        success = await _redis_manager.initialize()
        if not success:
            _get_manager_last_failed = time.monotonic()
            logger.warning("[RedisManager] Initialization failed; will retry after cooldown")

    return _redis_manager


class RedisManagerCompatProxy:
    """Compatibility proxy to allow calling redis methods directly on redis_manager."""
    def __getattr__(self, name):
        from backend_app.backend.redis_manager import _redis_manager
        if _redis_manager is not None:
            if hasattr(_redis_manager, name):
                return getattr(_redis_manager, name)
            if _redis_manager._cache is not None and hasattr(_redis_manager._cache, name):
                return getattr(_redis_manager._cache, name)
            if _redis_manager._queue is not None and hasattr(_redis_manager._queue, name):
                return getattr(_redis_manager._queue, name)
        raise AttributeError(f"redis_manager has no attribute '{name}' and is not initialized yet")

redis_manager = RedisManagerCompatProxy()

