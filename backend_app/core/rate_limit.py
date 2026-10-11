"""The shared rate-limit store, chosen once and fail-closed.

API-03 -- A TRADING MODE WAS BEING READ AS A DEPLOYMENT ENVIRONMENT
-------------------------------------------------------------------
This module used to decide its storage like this::

    env_raw = os.getenv("ENV") or get_vyomquant_mode(default="safe")
    DEV_TEST_ENVS = {"testing", "test", "development", "dev", "local",
                     "safe", "paper", "ambiguous"}

``VYOMQUANT_MODE`` is a **trading mode** -- ``safe`` / ``paper`` / ``live``, documented
in ``core/deployment_config.py`` -- and not a deployment environment. Listing ``safe``
and ``paper`` as dev environments meant the documented production setting
``VYOMQUANT_MODE=paper`` selected in-memory storage whenever ``ENV`` was unset. Two
consequences, both silent:

1. With ``REDIS_URL`` absent, every pod got its own counters, so the effective limit was
   the configured one times the replica count. Announced at ``info``.
2. With ``REDIS_URL`` present but Redis unreachable, the ``env in DEV_TEST_ENVS``
   fallback caught it and degraded to memory instead of failing closed -- the same
   outcome, reached from a genuine outage.

The ``"ambiguous"`` entry was dead code on top of that: ``get_vyomquant_mode`` always
returns a non-empty string (it defaults to ``"safe"``), so ``env_raw`` was never falsey
and the ``env = "ambiguous"`` branch could not execute. The ``elif`` below it,
commented *"Production or ambiguous environment missing REDIS_URL"*, could therefore
never see an ambiguous environment -- the allowlist had already claimed it.

This is the same defect class ``core/cache/redis_manager.py`` was fixed for earlier,
where the old check compared the trading mode against ``"production"``, a value it can
never hold. The judgement now lives in one place for both:
:func:`backend_app.core.runtime_env.local_state_is_acceptable`.

THE RULE NOW
------------
Process-local storage requires an explicit local/test environment. Otherwise a reachable
Redis is mandatory, and its absence raises at import rather than degrading quietly. The
decision is computed into a frozen :class:`RateLimitConfig` so it can be asserted on
directly instead of being inferred from which branch logged what.

Regression: tests/test_api03_rate_limit_storage_is_fail_closed.py
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Optional

from slowapi import Limiter
from slowapi.util import get_remote_address

from backend_app.core.runtime_env import (
    deployment_environment,
    local_state_is_acceptable,
)

logger = logging.getLogger("RateLimit")

MEMORY_URI = "memory://"


@dataclass(frozen=True)
class RateLimitConfig:
    """The resolved storage decision, and the reasoning that produced it.

    Frozen and returned rather than applied as a side effect, so a test can assert the
    decision for a given environment without importing the module under that environment
    -- which, for import-time configuration, is otherwise untestable.
    """

    #: The deployment environment as read. ``""`` when unset. Never a trading mode.
    env: str
    #: ``REDIS_URL`` as configured, or ``""``.
    redis_url: str
    #: Whether this environment may substitute process-local counters.
    local_allowed: bool
    #: Whether a reachable Redis was confirmed by probe.
    redis_reachable: bool
    #: What the limiter will actually use.
    storage_uri: str
    #: Why, in one line, for the log and for a failing test's message.
    reason: str

    @property
    def is_shared(self) -> bool:
        """True when the store is shared across processes, i.e. the limit is real."""
        return self.storage_uri != MEMORY_URI


def _probe_redis(url: str) -> None:
    """Raise if ``url`` is not a reachable Redis. Kept separate so tests can inject."""
    import redis

    client = redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    client.ping()


def resolve_rate_limit_config(
    *,
    redis_url: Optional[str] = None,
    probe: Optional[Callable[[str], None]] = None,
) -> RateLimitConfig:
    """Decide where rate-limit counters live, failing closed.

    Order of reasoning, which is also the order of precedence:

    1. A reachable ``REDIS_URL`` is always correct, in any environment.
    2. No ``REDIS_URL``: process-local storage, but only if this environment explicitly
       permits it. Otherwise raise.
    3. ``REDIS_URL`` set but unreachable: process-local storage for a local/test
       environment, otherwise raise. A deployed pod that cannot reach its limiter store
       has lost the control; continuing without it is the failure, not the recovery.

    Raises:
        RuntimeError: when no shared store is available and this environment is not
            permitted to run without one.
    """
    env = deployment_environment()
    local_allowed = local_state_is_acceptable()
    url = (os.getenv("REDIS_URL", "") if redis_url is None else redis_url).strip()
    probe_fn = probe or _probe_redis

    def _refuse(detail: str) -> RuntimeError:
        return RuntimeError(
            "FATAL: rate limiting has no shared store. "
            f"{detail} "
            f"(ENV={env or '<unset>'}, REDIS_URL={'set' if url else 'empty'}). "
            "In-memory rate limiting is per-process: with more than one replica the "
            "effective limit becomes the configured limit times the replica count, so "
            "it is refused outside an explicitly local environment. Set REDIS_URL to a "
            "reachable Redis, or declare the environment with DEV_MODE=true / "
            "ENV=development."
        )

    if url:
        try:
            probe_fn(url)
        except Exception as exc:  # noqa: BLE001
            if not local_allowed:
                raise _refuse(f"REDIS_URL is set but unreachable ({exc}).") from exc
            return RateLimitConfig(
                env=env, redis_url=url, local_allowed=True, redis_reachable=False,
                storage_uri=MEMORY_URI,
                reason=f"local environment {env!r}; REDIS_URL unreachable ({exc})",
            )
        return RateLimitConfig(
            env=env, redis_url=url, local_allowed=local_allowed, redis_reachable=True,
            storage_uri=url, reason="REDIS_URL reachable; counters are shared",
        )

    if not local_allowed:
        raise _refuse("REDIS_URL is missing or empty.")

    return RateLimitConfig(
        env=env, redis_url="", local_allowed=True, redis_reachable=False,
        storage_uri=MEMORY_URI,
        reason=f"explicitly local environment {env or '<DEV_MODE>'!r}; no REDIS_URL",
    )


#: The decision this process is running under. Exported so a health endpoint or a test
#: can read what was chosen rather than guess from log output.
config = resolve_rate_limit_config()

if config.is_shared:
    logger.info("[RateLimit] Redis-backed rate limiter armed (%s).", config.reason)
else:
    logger.warning(
        "[RateLimit] Using PER-PROCESS in-memory rate limiting: %s. Limits are not "
        "shared between replicas.", config.reason,
    )

limiter = Limiter(key_func=get_remote_address, storage_uri=config.storage_uri)

# Kept for backward compatibility with anything that imported these names. `env` is now
# strictly the deployment environment; it is no longer ever a trading mode.
env = config.env
REDIS_URL = config.redis_url
