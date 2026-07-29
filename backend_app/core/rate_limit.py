import os
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend_app.core.safety_config import get_vyomquant_mode

logger = logging.getLogger("RateLimit")

REDIS_URL = os.getenv("REDIS_URL", "").strip()
env_raw = os.getenv("ENV") or get_vyomquant_mode(default=None)

# Explicit list of local/test environments allowed to use in-memory rate limiting
DEV_TEST_ENVS = {"testing", "test", "development", "dev", "local"}

if env_raw:
    env = env_raw.lower()
else:
    # Ambiguous environment: neither ENV nor VYOMQUANT_MODE is set
    env = "ambiguous"

if env in DEV_TEST_ENVS and not REDIS_URL:
    logger.info(f"[RateLimit] Explicit dev/test environment ('{env}'). Using in-memory storage.")
    limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
elif not REDIS_URL:
    # Production or ambiguous environment missing REDIS_URL
    msg = (
        f"FATAL: REDIS_URL environment variable is missing or empty in '{env}' environment. "
        "In-memory rate-limiting fallback is prohibited in production/non-development environments."
    )
    logger.critical(f"[RateLimit] {msg}")
    raise RuntimeError(msg)
else:
    # REDIS_URL is provided — attempt Redis connection
    try:
        limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)
        logger.info(f"[RateLimit] Redis-backed rate limiter armed successfully ({env}).")
    except Exception as e:
        if env in DEV_TEST_ENVS:
            logger.warning(f"[RateLimit] Redis connection failed in '{env}' environment ({e}). Falling back to memory.")
            limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
        else:
            msg = f"FATAL: Failed to initialize Redis rate limit storage in '{env}' environment ({e})."
            logger.critical(f"[RateLimit] {msg}")
            raise RuntimeError(msg) from e
