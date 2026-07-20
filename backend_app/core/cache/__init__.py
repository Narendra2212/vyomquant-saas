"""Core cache module with shared Redis connection pool."""

from .redis_manager import redis_manager

__all__ = ["redis_manager"]
