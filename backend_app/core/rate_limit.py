import os

from slowapi import Limiter
from slowapi.util import get_remote_address

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Fallback to in-memory if REDIS is not available during dev, else use RedisStorage
try:
    # slowapi doesn't have native RedisStorage but it can use Limits' storage uri
    limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)
except Exception:
    limiter = Limiter(key_func=get_remote_address)
