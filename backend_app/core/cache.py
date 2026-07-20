"""Redis connection manager and lightweight event bus helpers."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import redis.asyncio as redis

logger = logging.getLogger("RedisCache")


class RedisClient:
    def __init__(self):
        self.pool: Optional[redis.Redis] = None
        self._redis_url: str = ""

    async def connect(self):
        self._redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        try:
            self.pool = redis.from_url(self._redis_url, decode_responses=True)
            await self.pool.ping()
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
        try:
            await self.connect()
        except Exception:
            self.pool = None

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

    async def xadd(self, stream: str, payload: Dict[str, Any]):
        if not self.pool:
            await self._try_reconnect()
        if not self.pool:
            return None
        try:
            body = {
                k: json.dumps(v) if not isinstance(v, str) else v
                for k, v in payload.items()
            }
            return await self.pool.xadd(stream, body)
        except Exception as e:
            logger.warning(f"Redis XADD failed for '{stream}': {e}")
            self.pool = None
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
