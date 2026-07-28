"""Core cache module with shared Redis connection pool."""

from .redis_manager import redis_manager, PublishError, TRADING_CRITICAL_STREAMS, SharedRedisManager as RedisClient

__all__ = ["redis_manager", "RedisClient", "PublishError", "TRADING_CRITICAL_STREAMS"]
