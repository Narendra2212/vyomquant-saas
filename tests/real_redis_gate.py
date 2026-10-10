"""The one place that decides whether a real-Redis test may run. D5'.

WHY THIS MODULE EXISTS
----------------------
Three test files want a real Redis, and before this they disagreed about how to ask for one:

* ``tests/test_webhook_duplicate_delivery_real_redis.py`` read ``AERORA_TEST_REDIS_URL`` only
  and gated on REACHABILITY (``client.ping()``) -- the correct shape.
* ``tests/test_quota01_exchange_slot_is_atomic.py`` and
  ``tests/test_quota01_metered_reservation_is_hard.py`` read
  ``AERORA_TEST_REDIS_URL or REDIS_URL`` and gated on the STRING only.

The second form has two defects, and finding D5' is the first of them:

1. THE ``REDIS_URL`` FALLBACK IS DEAD, AND MISLEADING WHILE IT LOOKS ALIVE.
   ``tests/conftest.py`` executes ``os.environ["REDIS_URL"] = ""`` unconditionally at import,
   before any test module loads, so the fallback can never contribute a value under pytest.
   That is what made the CI Redis service -- healthy, mapped, and exported as ``REDIS_URL`` --
   unable to switch these tests on: the gate read ``''`` and skipped. Measured in
   ``staging/p10k_gate_predicate.txt``.

   It is also a latent hazard in the other direction. ``REDIS_URL`` is the variable
   PRODUCTION sets. A context that imports these modules without ``tests/conftest.py``
   having run first would read a production endpoint out of it. So the fallback is removed
   rather than fixed: the opt-in is one deliberate name, exactly as
   ``AERORA_TEST_DATABASE_URL`` is for PostgreSQL.

2. "CONFIGURED BUT UNREACHABLE" WAS INDISTINGUISHABLE FROM "NOT ASKED FOR".
   A string-shape gate admits a URL that no server answers, and the test then dies somewhere
   inside its own body -- or, worse, a reachability gate of the webhook file's shape reports
   it as a SKIP, which reads as "nobody wanted this" rather than "the service you configured
   is broken". Both hide a broken CI service behind a green run.

   So the three states are kept apart:

   =====================  ==============================================================
   opt-in UNSET             SKIP. The default, and the only legitimate skip.
   opt-in SET, no server    FAIL. You asked for a real Redis; there isn't one.
   opt-in SET, reachable    RUN.
   =====================  ==============================================================

PRODUCTION SAFETY
-----------------
A non-loopback host is REFUSED unless ``AERORA_TEST_REDIS_ALLOW_REMOTE=1`` is also set. CI's
service container answers on ``localhost``, so the common path needs no exception; the escape
hatch exists for a docker-compose-style service name and has to be asked for by name.

NO URL, HOST OR CREDENTIAL IS EVER PUT IN A MESSAGE THIS MODULE PRODUCES. A skip reason or a
failure reason naming the endpoint would print it into CI logs, which are world-readable on
public repositories. Reasons name the VARIABLE, never its value.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

#: The single deliberate opt-in. ``REDIS_URL`` is NOT consulted -- see the module docstring.
OPT_IN_VAR = "AERORA_TEST_REDIS_URL"

#: Permits a non-loopback endpoint. Off by default so a test run cannot wander onto a shared
#: or managed instance because someone exported a convenient URL.
ALLOW_REMOTE_VAR = "AERORA_TEST_REDIS_ALLOW_REMOTE"

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

#: Seconds to wait for the PING that decides reachability. Short: this runs at import time for
#: every collection of a gated module, including collections that will skip.
_PING_TIMEOUT_SECONDS = 2


def redis_url() -> str:
    """The configured test endpoint, or ``''``. Never logged by this module."""
    return (os.environ.get(OPT_IN_VAR) or "").strip()


def _remote_allowed() -> bool:
    return (os.environ.get(ALLOW_REMOTE_VAR) or "").strip() == "1"


def _classify(url: str) -> tuple[str, str]:
    """``(state, reason)`` where state is ``"skip"``, ``"fail"`` or ``"run"``.

    Reasons are safe to print: they name variables and conditions, never values.
    """
    if not url:
        return (
            "skip",
            f"{OPT_IN_VAR} is not set. Real-Redis coverage is opt-in: the default suite runs "
            f"on the in-process substrate, and the logic tests beside these cover it. Set "
            f"{OPT_IN_VAR} to a DEDICATED test instance or database index to enable it. "
            f"A skip here is NOT a pass.",
        )

    if not url.startswith(("redis://", "rediss://")):
        return (
            "fail",
            f"{OPT_IN_VAR} is set but is not a redis:// or rediss:// URL. It was asked for, so "
            f"this is a misconfiguration rather than an absence, and it fails instead of "
            f"skipping. (The value is deliberately not echoed.)",
        )

    host = (urlsplit(url).hostname or "").lower()
    if host not in _LOOPBACK_HOSTS and not _remote_allowed():
        return (
            "fail",
            f"{OPT_IN_VAR} names a non-loopback host, and {ALLOW_REMOTE_VAR}=1 was not set. "
            f"Refusing to connect: a real-Redis test must not be able to reach a shared or "
            f"managed instance by accident. Point it at a local instance, or set "
            f"{ALLOW_REMOTE_VAR}=1 to state that the target is a dedicated test service. "
            f"(The host is deliberately not echoed.)",
        )

    try:
        import redis as _redis
    except ImportError:
        return (
            "fail",
            f"{OPT_IN_VAR} is set but the `redis` client library is not importable, so the "
            f"endpoint cannot be used. Install the test requirements.",
        )

    try:
        client = _redis.Redis.from_url(
            url,
            socket_connect_timeout=_PING_TIMEOUT_SECONDS,
            socket_timeout=_PING_TIMEOUT_SECONDS,
        )
        try:
            if not client.ping():
                raise RuntimeError("PING returned a falsey value")
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        return (
            "fail",
            f"{OPT_IN_VAR} is set but the server did not answer PING "
            f"({type(exc).__name__}). A configured endpoint that cannot be reached is a "
            f"BROKEN TEST SERVICE, not an absent one, so this fails rather than skipping -- "
            f"otherwise a dead Redis in CI would look like a green run. (The endpoint is "
            f"deliberately not echoed.)",
        )

    return "run", ""


#: Evaluated once per session, at import of the first gated module. The PING is the only
#: network operation this module performs, and it sends no data beyond PING.
GATE_STATE, GATE_REASON = _classify(redis_url())

#: ``True`` only when a reachable, permitted endpoint is configured.
REAL_REDIS_AVAILABLE = GATE_STATE == "run"

#: Apply to a test that needs a real Redis. It skips ONLY for the legitimate opt-out; a
#: configured-but-broken endpoint is handled by :func:`require_real_redis`.
real_redis_required = pytest.mark.skipif(
    GATE_STATE == "skip",
    reason=GATE_REASON or "real Redis not requested",
)


def require_real_redis() -> str:
    """Assert a usable endpoint and return it. Call as the first line of a gated test.

    Paired with :data:`real_redis_required`: the marker handles "not asked for" (skip), and
    this handles "asked for and broken" (fail). Both are needed because a pytest marker cannot
    express a failure.
    """
    if GATE_STATE == "fail":
        pytest.fail(GATE_REASON)
    if GATE_STATE != "run":
        pytest.skip(GATE_REASON)
    return redis_url()


def gate_summary() -> str:
    """One safe line for a report or a log. Contains no endpoint, host or credential."""
    return (
        f"real-Redis gate: state={GATE_STATE} "
        f"({OPT_IN_VAR}={'set' if redis_url() else 'unset'}, "
        f"remote_allowed={_remote_allowed()})"
    )
