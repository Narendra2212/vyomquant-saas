"""WORK-03: the cross-process single-owner claim for a logical bot.

THE INVARIANT
=============
    At most one active execution owner may exist for a logical bot (user_id, symbol) at
    any instant, including when requests are handled by different processes or pods.

`FleetManager._active_fleet` is a per-process dict, so it cannot provide this. Two pods
do not see each other's bots. The WORK-02 XAUTOCLAIM reaper made that reachable rather
than theoretical: a reclaimed `start_bot` command is delivered to a *different* consumer
than the one that first took it.

WHY POSTGRESQL AND NOT A REDIS LOCK
-----------------------------------
`core/cache/redis_manager.py` is deliberately fail-soft -- every method swallows its
exception and returns a falsey default. A Redis outage would therefore make `SET NX`
answer "not acquired" for everybody (an availability failure dressed as a correctness
one), and a future refactor that returned a truthy default on error would make it answer
"acquired" for everybody. **An availability dependency must not be able to flip a
correctness invariant in the unsafe direction.** PostgreSQL is already a hard dependency
of the deployment path and a primary key cannot fail open.

THE MECHANISM
-------------
One statement does the whole claim::

    INSERT INTO bot_ownership_claims (...) VALUES (...)
    ON CONFLICT (user_id, symbol) DO UPDATE SET owner_token = EXCLUDED.owner_token, ...
     WHERE bot_ownership_claims.heartbeat_at < now() - <stale>
    RETURNING owner_token

PostgreSQL takes the row lock for the ON CONFLICT path and re-evaluates the WHERE after
acquiring it, so of two concurrent claimants exactly one matches. The loser gets **zero
rows** -- a deterministic no-op, not an exception.

* fresh row -> INSERT wins, caller owns it
* live row  -> the WHERE is false, zero rows, caller stands down
* stale row -> the WHERE is true, ownership transfers

NO PERMANENT LOCK. A worker that dies holding a claim stops heartbeating and the claim
becomes reclaimable after :data:`STALE_AFTER_SECONDS`. There is no state in which a dead
pod strands a bot forever -- the failure mode a plain `ON CONFLICT DO NOTHING` lock would
have had.

OWNERSHIP IS A TOKEN. `owner_token` is regenerated on every successful claim and
release/heartbeat are scoped to it, so a worker that was reaped cannot release or
heartbeat its successor's claim when it wakes up.

Regression: tests/test_work03_bot_ownership_claims_postgres.py
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger("BotOwnership")

#: How long a claim may go without a heartbeat before another worker may take it over.
#:
#: Must be a comfortable multiple of the heartbeat interval. A threshold close to the
#: interval does not detect worker loss, it manufactures it -- the rule
#: `training_worker.MIN_STALE_TO_INTERVAL_RATIO` already states for heartbeats and the
#: WORK-02 reaper already applies to the PEL.
STALE_AFTER_SECONDS = 90

#: How often an owner should refresh its claim.
HEARTBEAT_INTERVAL_SECONDS = 15

#: Enforced floor on the ratio, so a misconfiguration cannot turn the liveness detector
#: into a bot killer that reaps healthy claims mid-heartbeat.
MIN_STALE_TO_INTERVAL_RATIO = 3


class ClaimOutcome(str):
    """Why a claim attempt ended the way it did. A plain str subclass so it logs cleanly."""


#: The caller now owns the bot and must release it when the bot stops.
CLAIMED = ClaimOutcome("claimed")
#: Another live process owns it. Deterministic no-op -- NOT an error.
HELD_BY_ANOTHER = ClaimOutcome("held_by_another")
#: The previous owner's claim had gone stale and was taken over.
RECLAIMED_STALE = ClaimOutcome("reclaimed_stale")
#: The claim store could not be reached. FAILS CLOSED -- see `acquire`.
STORE_UNAVAILABLE = ClaimOutcome("store_unavailable")


@dataclass(frozen=True)
class BotClaim:
    """A held claim. `owner_token` is the capability; without it you own nothing."""

    user_id: str
    symbol: str
    owner_token: str
    worker_name: str
    outcome: ClaimOutcome

    @property
    def granted(self) -> bool:
        return self.outcome in (CLAIMED, RECLAIMED_STALE)


def worker_identity() -> str:
    """A name unique to this process, for diagnosing who holds what.

    Same construction as the command worker's consumer name, and for the same reason: a
    shared name makes "which pod owns this bot?" unanswerable.
    """
    base = os.getenv("WORKER_NAME") or "fleet"
    try:
        host = socket.gethostname() or "unknown-host"
    except Exception:
        host = "unknown-host"
    return f"{base}-{host}-{os.getpid()}"


def effective_stale_seconds() -> int:
    """The stale threshold, floored at :data:`MIN_STALE_TO_INTERVAL_RATIO` x interval."""
    floor = MIN_STALE_TO_INTERVAL_RATIO * HEARTBEAT_INTERVAL_SECONDS
    configured = STALE_AFTER_SECONDS
    if configured < floor:
        logger.warning(
            "WORK-03 stale threshold %ss is below %sx the heartbeat interval; raising to "
            "%ss so a healthy claim is not reaped.",
            configured, MIN_STALE_TO_INTERVAL_RATIO, floor,
        )
        return floor
    return configured


# ── the three operations ────────────────────────────────────────────────────


_ACQUIRE_SQL = """
INSERT INTO public.bot_ownership_claims
       (user_id, symbol, owner_token, worker_name, state, claimed_at, heartbeat_at)
VALUES (%(user_id)s, %(symbol)s, %(token)s, %(worker)s, 'STARTING', now(), now())
ON CONFLICT (user_id, symbol) DO UPDATE
   SET owner_token  = EXCLUDED.owner_token,
       worker_name  = EXCLUDED.worker_name,
       state        = 'STARTING',
       claimed_at   = now(),
       heartbeat_at = now(),
       released_at  = NULL
 WHERE public.bot_ownership_claims.heartbeat_at
       < now() - make_interval(secs => %(stale)s)
RETURNING owner_token, (xmax = 0) AS was_inserted
"""
# `xmax = 0` is how PostgreSQL distinguishes the INSERT arm of an upsert from the
# DO UPDATE arm: a freshly inserted tuple has no updating transaction, so xmax is 0,
# while a row the conflict path updated carries this transaction's id.
#
# The first version returned `claimed_at IS NOT NULL`, which is ALWAYS true -- the INSERT
# sets it -- so every claim reported itself as a stale takeover. Harmless to the
# invariant (both outcomes are `granted`) but it would have made the logs say a healthy
# first start had reaped a dead pod, which is exactly the kind of false signal that
# wastes an incident response.

_RELEASE_SQL = """
DELETE FROM public.bot_ownership_claims
 WHERE user_id = %(user_id)s AND symbol = %(symbol)s AND owner_token = %(token)s
RETURNING owner_token
"""

_HEARTBEAT_SQL = """
UPDATE public.bot_ownership_claims
   SET heartbeat_at = now(), state = 'ACTIVE'
 WHERE user_id = %(user_id)s AND symbol = %(symbol)s AND owner_token = %(token)s
RETURNING owner_token
"""


def acquire(cursor: Any, user_id: str, symbol: str,
            worker_name: Optional[str] = None) -> BotClaim:
    """Attempt to become the single owner of ``(user_id, symbol)``.

    ``cursor`` is a DB-API cursor the caller owns, so this composes with the caller's
    transaction rather than opening its own connection -- which is what lets the
    concurrency test drive two genuinely separate connections.

    **FAILS CLOSED.** A store error returns :data:`STORE_UNAVAILABLE` with
    ``granted == False``. If we cannot establish that nobody else owns this bot, we must
    not start it: the cost of a false refusal is a bot that does not start, and the cost
    of a false grant is two bots trading the same pair on one account.
    """
    worker = worker_name or worker_identity()
    token = str(uuid.uuid4())
    params = {
        "user_id": str(user_id),
        "symbol": str(symbol),
        "token": token,
        "worker": worker,
        "stale": effective_stale_seconds(),
    }

    try:
        cursor.execute(_ACQUIRE_SQL, params)
        row = cursor.fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "WORK-03: could not reach the bot-claim store for %s/%s (%s). Refusing to "
            "start, because an unestablished claim is indistinguishable from somebody "
            "else holding it.",
            user_id, symbol, exc,
        )
        return BotClaim(str(user_id), str(symbol), "", worker, STORE_UNAVAILABLE)

    if row is None:
        # The ON CONFLICT predicate did not match: a LIVE claim exists. Zero rows is the
        # deterministic "stand down", and it is not an error.
        logger.info(
            "WORK-03: %s/%s is already owned by a live worker; this process is standing "
            "down.", user_id, symbol,
        )
        return BotClaim(str(user_id), str(symbol), "", worker, HELD_BY_ANOTHER)

    granted_token = str(row[0])
    was_inserted = bool(row[1]) if len(row) > 1 else True
    outcome = CLAIMED if was_inserted else RECLAIMED_STALE
    if outcome is RECLAIMED_STALE:
        logger.warning(
            "WORK-03: took over a STALE claim on %s/%s (no heartbeat for >%ss). The "
            "previous owner cannot release this claim, because release is scoped to the "
            "owner token.",
            user_id, symbol, effective_stale_seconds(),
        )
    return BotClaim(str(user_id), str(symbol), granted_token, worker, outcome)


def release(cursor: Any, claim: BotClaim) -> bool:
    """Give up a claim. Scoped to the owner token, so a reaped worker cannot release its
    successor's claim. Returns whether a row was actually removed."""
    if not claim.owner_token:
        return False
    try:
        cursor.execute(_RELEASE_SQL, {
            "user_id": claim.user_id, "symbol": claim.symbol,
            "token": claim.owner_token,
        })
        return cursor.fetchone() is not None
    except Exception as exc:  # noqa: BLE001
        # A failed release is not fatal: the claim goes stale on its own and becomes
        # reclaimable. That is the whole point of the heartbeat.
        logger.warning(
            "WORK-03: could not release the claim on %s/%s (%s). It will become "
            "reclaimable after the stale threshold.",
            claim.user_id, claim.symbol, exc,
        )
        return False


def heartbeat(cursor: Any, claim: BotClaim) -> bool:
    """Refresh a claim. ``False`` means ownership was LOST and the caller must stop.

    Scoped to the owner token, so a worker that was declared stale and had its claim
    taken over learns about it here and stands down rather than continuing to trade
    alongside the new owner.
    """
    if not claim.owner_token:
        return False
    try:
        cursor.execute(_HEARTBEAT_SQL, {
            "user_id": claim.user_id, "symbol": claim.symbol,
            "token": claim.owner_token,
        })
        return cursor.fetchone() is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "WORK-03: heartbeat failed for %s/%s (%s).",
            claim.user_id, claim.symbol, exc,
        )
        return False


__all__ = [
    "STALE_AFTER_SECONDS",
    "HEARTBEAT_INTERVAL_SECONDS",
    "MIN_STALE_TO_INTERVAL_RATIO",
    "CLAIMED",
    "HELD_BY_ANOTHER",
    "RECLAIMED_STALE",
    "STORE_UNAVAILABLE",
    "BotClaim",
    "worker_identity",
    "effective_stale_seconds",
    "acquire",
    "release",
    "heartbeat",
]


# ── the wiring: a self-contained claim against the real claim store ────────
#
# `FleetManager` holds no database handle, so these manage a short-lived connection.
# Kept separate from `acquire`/`release`/`heartbeat` above, which take a cursor, so the
# concurrency test can drive two genuinely independent connections.


def _claim_store_dsn() -> Optional[str]:
    """The PostgreSQL DSN for the claim store, or ``None`` if there isn't one.

    ``None`` is returned for a SQLite or empty ``DATABASE_URL`` rather than raising,
    because "no PostgreSQL configured" is a CONFIGURATION state and the caller's response
    to it depends on the environment -- see :func:`claim_bot`.
    """
    for var in ("AERORA_TEST_DATABASE_URL", "DATABASE_URL"):
        dsn = (os.environ.get(var) or "").strip()
        if dsn.startswith("postgres"):
            return dsn
    return None


def claim_bot(user_id: str, symbol: str) -> BotClaim:
    """Claim ``(user_id, symbol)`` against the real store, deciding the no-store case.

    THE POLICY, AND WHY IT REUSES THE API-03 DETECTOR
    ------------------------------------------------
    When no PostgreSQL claim store is configured there are two defensible answers, and
    which one is right depends on exactly the question
    :func:`backend_app.core.runtime_env.local_state_is_acceptable` already answers: *is
    there a second process to coordinate with?*

    * **A declared local/test environment** -- one process, no pods. Process-local state
      (``FleetManager._active_fleet``) genuinely is sufficient there, so the claim is
      skipped and a warning is logged. Without this, a developer with the default SQLite
      ``DATABASE_URL`` could not start a paper bot at all, and the whole test suite would
      fail closed.
    * **Anything else**, including an unset environment -- assume pods. No claim store
      means we cannot establish single ownership, so we refuse. An unconfigured
      deployment is protected rather than exposed.

    That is the same judgement, the same function and the same fail-closed default as the
    rate limiter's, so the two cannot drift apart.
    """
    from backend_app.core.runtime_env import local_state_is_acceptable

    dsn = _claim_store_dsn()
    worker = worker_identity()

    if dsn is None:
        if local_state_is_acceptable():
            logger.warning(
                "WORK-03: no PostgreSQL claim store is configured and this is a declared "
                "local/test environment, so single-bot ownership falls back to the "
                "in-process fleet registry. That is NOT safe with more than one process.",
            )
            # Granted, with a token, so release() is symmetric and the caller needs no
            # special case. The local `_active_fleet` check remains the real control here.
            return BotClaim(str(user_id), str(symbol), f"local-{uuid.uuid4()}", worker,
                            CLAIMED)
        logger.error(
            "WORK-03: no PostgreSQL claim store is configured, so single-bot ownership "
            "cannot be established. Refusing to start %s/%s. Set DATABASE_URL to the "
            "PostgreSQL instance holding bot_ownership_claims, or declare a local "
            "environment with DEV_MODE=true / ENV=development.",
            user_id, symbol,
        )
        return BotClaim(str(user_id), str(symbol), "", worker, STORE_UNAVAILABLE)

    try:
        import psycopg2
    except Exception as exc:  # noqa: BLE001
        logger.error("WORK-03: psycopg2 unavailable (%s); refusing to start.", exc)
        return BotClaim(str(user_id), str(symbol), "", worker, STORE_UNAVAILABLE)

    conn = None
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        return acquire(conn.cursor(), user_id, symbol, worker)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "WORK-03: could not reach the claim store (%s); refusing to start %s/%s.",
            exc, user_id, symbol,
        )
        return BotClaim(str(user_id), str(symbol), "", worker, STORE_UNAVAILABLE)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def release_bot(claim: Optional[BotClaim]) -> bool:
    """Release a claim taken by :func:`claim_bot`. Never raises.

    A local-environment claim (``owner_token`` prefixed ``local-``) has no row to delete,
    so this is a no-op for it. A failed release is not escalated: the claim goes stale and
    becomes reclaimable, which is what the heartbeat exists for.
    """
    if claim is None or not claim.owner_token:
        return False
    if claim.owner_token.startswith("local-"):
        return True

    dsn = _claim_store_dsn()
    if dsn is None:
        return False

    try:
        import psycopg2
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        try:
            return release(conn.cursor(), claim)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "WORK-03: could not release the claim on %s/%s (%s); it will become "
            "reclaimable after the stale threshold.",
            claim.user_id, claim.symbol, exc,
        )
        return False


class ClaimStoreUnavailable(RuntimeError):
    """The claim store could not be reached.

    DISTINCT from "ownership was lost", and the distinction is load-bearing. The
    heartbeat loop must react to those two outcomes in opposite ways:

    * ownership lost -> another worker owns this bot now; stop trading immediately.
    * store unreachable -> we do not know; a transient blip must NOT kill a live bot.

    :func:`heartbeat` above returns ``False`` for both, which is correct for a caller
    that owns the cursor and can see the exception itself. :func:`heartbeat_bot` manages
    its own connection, so it raises instead of collapsing the two into one ``False``.
    """


def heartbeat_bot(claim: Optional[BotClaim]) -> bool:
    """Refresh a claim taken by :func:`claim_bot`.

    :returns: ``True`` while the claim is still ours, ``False`` once ownership has been
        taken over by another worker (the scoped UPDATE matched zero rows).
    :raises ClaimStoreUnavailable: the store could not be reached at all.
    """
    if claim is None or not claim.owner_token:
        return False
    if claim.owner_token.startswith("local-"):
        # A declared local environment has no row to refresh; `_active_fleet` is the
        # control there, and it cannot go stale.
        return True

    dsn = _claim_store_dsn()
    if dsn is None:
        raise ClaimStoreUnavailable("no PostgreSQL claim store is configured")

    try:
        import psycopg2
        conn = psycopg2.connect(dsn)
    except Exception as exc:  # noqa: BLE001
        raise ClaimStoreUnavailable(f"could not connect: {exc}") from exc

    try:
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute(_HEARTBEAT_SQL, {
            "user_id": claim.user_id, "symbol": claim.symbol,
            "token": claim.owner_token,
        })
        # A zero-row UPDATE does not raise, so reaching here means the statement ran and
        # the row count is a real answer about ownership.
        return cursor.fetchone() is not None
    except Exception as exc:  # noqa: BLE001
        raise ClaimStoreUnavailable(f"heartbeat failed: {exc}") from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass


__all__ += ["claim_bot", "release_bot", "heartbeat_bot", "ClaimStoreUnavailable"]
