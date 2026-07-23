import os
from slowapi import Limiter
from slowapi.util import get_remote_address

REDIS_URL = os.getenv("REDIS_URL")
env = os.getenv("ENV", os.getenv("AERORA_MODE", "development")).lower()

# Fallback to memory if REDIS_URL not set or in testing/dev mode
if not REDIS_URL or env in ("testing", "test", "development", "dev"):
    limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
else:
    try:
        limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)
    except Exception:
        limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
