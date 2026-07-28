"""Redis connection manager and lightweight event bus helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import redis.asyncio as redis

logger = logging.getLogger("RedisCache")

TRADING_CRITICAL_STREAMS = {"command_queue", "risk_signal", "execution_signal", "strategy_signal"}


class PublishError(RuntimeError):
    """Raised when publishing a message to a durable or trading-critical Redis Stream fails."""
    pass


class RedisClient:
    def __init__(self):
        self.pool: Optional[redis.Redis] = None
        self._redis_url: str = ""
        self._reconnect_lock: Optional[asyncio.Lock] = None
        self._last_reconnect_attempt: float = 0.0
        self._reconnect_cooldown: float = float(os.getenv("REDIS_RECONNECT_COOLDOWN", "3.0"))

    def _get_lock(self) -> asyncio.Lock:
        """Lazily initialize asyncio.Lock to ensure binding to the active event loop."""
        if self._reconnect_lock is None:
            self._reconnect_lock = asyncio.Lock()
        return self._reconnect_lock

    async def connect(self):
        self._redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        self._last_reconnect_attempt = time.monotonic()
        try:
            pool_candidate = redis.from_url(self._redis_url, decode_responses=True)
            await pool_candidate.ping()
            self.pool = pool_candidate
            logger.info("Redis Cache connected successfully.")
        except Exception as e:
            logger.warning(
                f"Redis Cache connection failed: {e}. "
                "API will fall back to in-process state where needed."
            )
            self.pool = None

    async def disconnect(self):
        if self.pool:
            try:
                await self.pool.close()
            except Exception:
                pass
            self.pool = None
            logger.info("Redis Cache disconnected.")

    async def _try_reconnect(self):
        """
        Debounced reconnection logic to prevent thundering-herd patterns during Redis outages.
        
        Guarantees:
        1. At most one reconnection attempt is in-flight at a time across all concurrent coroutines.
        2. Enforces a minimum cooldown period between failed reconnection attempts.
        3. Concurrent callers immediately reuse a successful connection or fail-soft if recently attempted.
        """
        if self.pool:
            return

        now = time.monotonic()
        if now - self._last_reconnect_attempt < self._reconnect_cooldown:
            return

        lock = self._get_lock()
        async with lock:
            # Double-check inside lock in case a prior waiter succeeded
            if self.pool:
                return

            now = time.monotonic()
            if now - self._last_reconnect_attempt < self._reconnect_cooldown:
                return

            await self.connect()

    async def get(self, key: str):
        if not self.pool:
            await self._try_reconnect()
        if not self.pool:
            return None
        try:
            return await self.pool.get(key)
        except Exception as e:
            logger.warning(f"Redis GET failed for '{key}': {e}")
            self.pool = None
            return None

    async def setex(self, key: str, ttl_seconds: int, value: str):
        if not self.pool:
            await self._try_reconnect()
        if not self.pool:
            return
        try:
            await self.pool.setex(key, ttl_seconds, value)
        except Exception as e:
            logger.warning(f"Redis SETEX failed for '{key}': {e}")
            self.pool = None

    async def delete(self, key: str):
        if not self.pool:
            await self._try_reconnect()
        if not self.pool:
            return
        try:
            await self.pool.delete(key)
        except Exception as e:
            logger.warning(f"Redis DELETE failed for '{key}': {e}")
            self.pool = None

    async def xadd(
        self,
        stream: str,
        payload: Dict[str, Any],
        max_retries: int = 3,
        raise_on_error: Optional[bool] = None,
    ):
        """
        Publish an entry to a Redis stream.
        
        Trading-critical streams (command_queue, risk_signal, execution_signal, strategy_signal)
        or calls with raise_on_error=True will retry on transient connection failures
        and raise PublishError if all retries fail.
        """
        is_critical = (raise_on_error is True) or (stream in TRADING_CRITICAL_STREAMS)
        attempts = max_retries if is_critical else 1
        last_exception = None

        for attempt in range(1, attempts + 1):
            if not self.pool:
                await self._try_reconnect()
            if self.pool:
                try:
                    body = {
                        k: json.dumps(v) if not isinstance(v, str) else v
                        for k, v in payload.items()
                    }
                    entry_id = await self.pool.xadd(stream, body)
                    if entry_id:
                        return entry_id
                except Exception as e:
                    last_exception = e
                    logger.warning(
                        f"Redis XADD attempt {attempt}/{attempts} failed for '{stream}': {e}"
                    )
                    self.pool = None
            else:
                last_exception = RuntimeError(f"Redis connection pool unavailable for stream '{stream}'")

            if attempt < attempts:
                await asyncio.sleep(0.1 * (2 ** (attempt - 1)))

        if is_critical:
            error_msg = (
                f"CRITICAL: Failed to publish trading message to stream '{stream}' "
                f"after {attempts} attempts. Last error: {last_exception}"
            )
            logger.error(error_msg)
            try:
                import sentry_sdk
                sentry_sdk.capture_message(error_msg, level="error")
            except Exception:
                pass
            raise PublishError(error_msg) from last_exception

        return None

    async def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: Dict[str, str],
        count: int = 10,
        block: int = 1000,
    ):
        if not self.pool:
            await self._try_reconnect()
        if not self.pool:
            return []
        try:
            return await self.pool.xreadgroup(
                group, consumer, streams, count=count, block=block
            )
        except Exception as e:
            logger.warning(f"Redis XREADGROUP failed for '{group}': {e}")
            return []

    async def xack(self, stream: str, group: str, *ids: str):
        if not self.pool:
            await self._try_reconnect()
        if not self.pool:
            return 0
        try:
            return await self.pool.xack(stream, group, *ids)
        except Exception as e:
            logger.warning(f"Redis XACK failed for '{stream}': {e}")
            return 0


redis_manager = RedisClient()
