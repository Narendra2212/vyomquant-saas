"""The one answer to "may this process substitute local state for shared state?".

WHY THIS MODULE EXISTS
----------------------
Two separate controls in this codebase needed the same judgement and answered it
differently:

* ``core/cache/redis_manager.py`` decides whether an in-process dict may stand in for
  Redis. Getting that wrong hands out a distributed lock that reports success separately
  in every pod.
* ``core/rate_limit.py`` decides whether an in-process counter may stand in for a shared
  rate-limit store. Getting that wrong gives every pod its own quota, so the effective
  limit is the configured one multiplied by the replica count.

Both are the same question -- *is there a second process to coordinate with?* -- and both
had got it wrong in the same way: by consulting ``VYOMQUANT_MODE``, which is a **trading
mode** (``safe`` / ``paper`` / ``live``, documented in ``core/deployment_config.py``) and
not a deployment environment. ``redis_manager`` was fixed first and carries the
post-mortem at :func:`~backend_app.core.cache.redis_manager.SharedRedisManager._get_active_cache`:
its old check compared the trading mode against ``"production"``, a value the variable
can never hold, so it always permitted the mock. ``rate_limit`` had the mirror image --
it listed ``safe`` and ``paper`` as *dev environments*, so the documented production
setting ``VYOMQUANT_MODE=paper`` selected in-memory storage.

Rather than fix the second one the same way and leave two definitions to drift, the
judgement lives here once.

WHAT IT READS, AND WHAT IT REFUSES TO READ
------------------------------------------
``DEV_MODE``, ``ENV`` and ``ENVIRONMENT`` only. Never a trading mode. A trading mode says
what the system is allowed to *do with money*; it says nothing about how many copies of
the process are running.

FAILS CLOSED. An unset ``ENV`` returns ``False``: an unconfigured deployment must not be
handed a fake lock or a per-pod quota. That is stricter than it needs to be on a
developer machine, and the fix for a developer machine is to say so -- ``DEV_MODE=true``
or ``ENV=development`` -- not to make silence mean "local".

A DECLARED ENVIRONMENT BEATS ``DEV_MODE``
-----------------------------------------
The first version of this module read::

    if os.environ.get("DEV_MODE", "false") in _TRUTHY:
        return True
    return deployment_environment() in LOCAL_STATE_ENVS

``DEV_MODE`` short-circuited, so it was never compared against ``ENV`` at all. That made
``ENV=production`` plus a stray ``DEV_MODE=true`` resolve to *local*, and the rate
limiter then logged "explicitly local environment 'production'" and quietly used a
per-process counter -- in production, behind N replicas, with only a warning. One
leftover environment variable disabled the control this module exists to enforce, which
is a worse failure than the two drifting definitions it was written to prevent.

Found by ``tests/test_rate_limiter_fails_closed_in_production.py``, which sets both
variables. The API-03 tests did not catch it because they clear ``DEV_MODE`` before
asserting, so they never exercised the conflict.

The rule now: if ``ENV`` names an environment that is **not** on the local allow-list,
that declaration wins and this returns ``False`` whatever ``DEV_MODE`` says. ``DEV_MODE``
can only grant local state where the environment is silent or already local -- which is
the only situation it was ever meant for. An ignored ``DEV_MODE`` is logged at CRITICAL,
because it means somebody believes this process is a developer machine and it is not.

Read per call rather than cached at import, because the test suite sets these with
``monkeypatch.setenv`` and a cached value would make the decision depend on import order.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("RuntimeEnv")

__all__ = [
    "LOCAL_STATE_ENVS",
    "deployment_environment",
    "dev_mode_requested",
    "local_fallbacks_are_acceptable",
    "local_state_is_acceptable",
]

#: The ``ENV`` values in which substituting process-local state for shared state is
#: acceptable. Acceptable means "there is no second process to coordinate with" -- a
#: developer machine or the test suite. Deliberately does NOT contain any trading mode.
LOCAL_STATE_ENVS = frozenset({"development", "dev", "local", "test", "testing"})

_TRUTHY = ("true", "1", "yes", "on")


def deployment_environment() -> str:
    """The deployment environment as configured, lowercased, or ``""`` if unset.

    ``ENV`` first, then ``ENVIRONMENT``. Returns the empty string rather than guessing a
    default, so callers can tell "unset" from "set to something I do not recognise" --
    the two deserve the same (closed) decision but not the same log line.
    """
    raw = os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or ""
    return raw.strip().lower()


def dev_mode_requested() -> bool:
    """Whether ``DEV_MODE`` is set to a truthy value. Requested, not necessarily granted."""
    return os.environ.get("DEV_MODE", "false").strip().lower() in _TRUTHY


def local_state_is_acceptable() -> bool:
    """Whether process-local state may stand in for shared state.

    ``True`` only where the environment is silent or declared local **and** either
    ``DEV_MODE`` is set or ``ENV`` is on :data:`LOCAL_STATE_ENVS`. Everything else --
    an unset environment with no ``DEV_MODE``, any unrecognised environment, and every
    trading mode -- is ``False``.

    A declared non-local ``ENV`` **overrides** ``DEV_MODE``. See the module docstring:
    letting a leftover ``DEV_MODE=true`` turn ``ENV=production`` into a local environment
    was a production fail-open, and it is the one case this function most needs to get
    right.
    """
    env = deployment_environment()
    dev_mode = dev_mode_requested()

    if env and env not in LOCAL_STATE_ENVS:
        # An explicitly declared non-local environment. Nothing may override it.
        if dev_mode:
            logger.critical(
                "DEV_MODE=true is being IGNORED because ENV=%r is a declared non-local "
                "environment. Process-local state is refused. If this really is a "
                "developer machine, set ENV to one of %s; if it is not, remove DEV_MODE "
                "from the environment -- it should never be set in a deployment.",
                env, sorted(LOCAL_STATE_ENVS),
            )
        return False

    if dev_mode:
        return True

    # env is either empty (fail closed) or on the allow-list.
    return env in LOCAL_STATE_ENVS


def local_fallbacks_are_acceptable() -> bool:
    """Whether a missing or broken dependency may degrade instead of refusing.

    THE SAME JUDGEMENT AS :func:`local_state_is_acceptable`, under the name that reads
    correctly at the other kind of call site. It delegates rather than re-deriving, so the
    two uses cannot drift -- which is the whole reason this module exists.

    Two questions, one answer:

    * *May an in-process dict stand in for Redis?* -- :func:`local_state_is_acceptable`.
      About **shared state**: getting it wrong hands out a fake distributed lock.
    * *May an unconstructable service-role client be returned as ``None``?* -- this one.
      About **configuration**: getting it wrong turns "this deployment has no credentials"
      into an ``AttributeError`` on the first ``.table(...)`` call, reported to the caller
      as a generic 500 whose message names the wrong cause.

    Both reduce to *is this process a developer machine or the test suite?*, and both must
    answer ``False`` for a declared production environment no matter what ``DEV_MODE``
    says. ``routers/library.py`` and ``routers/referral.py`` asked the question as a bare
    ``os.getenv("DEV_MODE", "false") == "true"``, which never consulted ``ENV`` at all --
    the RUNTIME-01 shape, measured again in ``staging/probe_lib01_service_client.py``:
    with ``ENV=production`` and ``DEV_MODE=true``, ``_build_service_client()`` -- whose
    docstring says it "raises 500 if env is not configured" -- returned ``None``.

    Regression: tests/test_environment_precedence_is_centralised.py
    """
    return local_state_is_acceptable()
