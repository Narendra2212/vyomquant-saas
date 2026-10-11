"""Optional worker that consumes command_queue events and executes them.

WORK-01 / WORK-02 -- JOB DURABILITY
===================================
This worker is the Redis-driven path that starts, stops and kills trading bots. Before
this remediation its acknowledgement lived in a ``finally:`` block::

    try:
        ... dispatch the command ...
    except Exception as e:
        logger.exception("Worker failed: %s", e)
    finally:
        await redis_manager.xack(stream_name, "command_workers", msg_id)

so a command that *raised* was acknowledged exactly as though it had succeeded. An
XACKed entry leaves the consumer group's Pending Entries List and is never redelivered,
so the command was gone: a ``stop_bot`` that failed on a transient Supabase error was
silently dropped and the bot kept trading. Nothing recovered it --
``backend/reconciliation_worker.py`` does not reconcile the fleet against
``strategy_deployments``, so there was no second path that would notice.

The other half of the defect was the mirror image. Because nothing ever reclaimed the
PEL, an entry whose consumer died *before* the ACK was pending forever: not lost loudly,
just never done.

WHAT THIS MODULE NOW DOES, AND WHY IN THIS ORDER
------------------------------------------------
1. **A refusal is not a failure.** A command rejected on policy -- cancelled
   subscription, no declared mode, unknown action -- is a *terminal decision*. Retrying
   it reaches the same decision forever, so it is ACKed. Only an *exception* leaves the
   entry pending. Conflating the two is what made the original ``finally:`` look
   reasonable.

2. **A failure stays pending, and is reclaimed.** :meth:`_reclaim_abandoned` calls
   XAUTOCLAIM so an entry abandoned by a dead consumer -- or deliberately left by a
   failed attempt -- comes back. The idle threshold is a multiple of the poll interval,
   never less than :data:`MIN_RECLAIM_IDLE_SECONDS`, because a threshold inside one poll
   period does not detect abandonment, it manufactures it by stealing an entry from a
   consumer still working on it. That is the rule
   ``training_worker.MIN_STALE_TO_INTERVAL_RATIO`` already states for heartbeats.

3. **Retries are bounded, and exhaustion is durable.** Attempts are counted in Redis per
   entry id (``INCR``, atomic, so it survives a reclaim by a different consumer). Past
   :data:`MAX_DELIVERIES` the command is written to :data:`DEAD_LETTER_STREAM` with its
   original fields and the failure reason, and only then ACKed. A poison command
   therefore stops consuming the worker without vanishing -- the thing the previous code
   could not do, since it had no durable place to put a job it could not run.

WHAT IS *NOT* CLOSED BY THIS CHANGE
-----------------------------------
Reclaiming an entry across processes reintroduces the possibility of running a command
twice: ``FleetManager.start_bot`` refuses a duplicate via ``if bot_key in
self._active_fleet``, but ``_active_fleet`` is per-process in-memory, so two pods cannot
see each other's bots. ``uq_strategy_deployments_one_live`` constrains the *deployment*
row, not this worker's path. The window is narrow -- it needs a crash after the bot
starts but before the ACK, plus a reclaim onto a different pod -- and it is strictly
smaller than the unconditional loss it replaces, but it is real.

Closing it needs a shared ownership record for ``(user_id, symbol)``, which is an
architectural addition with its own lifecycle questions (renewal, release on stop,
expiry while the bot still runs) and is not something to bolt on inside a webhook-shaped
fix. Recorded as **WORK-03 (OPEN)** in staging/REMEDIATION_REPORT.md.

Regression: tests/test_work01_command_durability.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from backend_app.core.cache import redis_manager
from backend_app.core.state import app_state

logger = logging.getLogger("CommandWorker")

#: The stream this worker consumes, and the group it consumes it under. Named here
#: rather than inlined four times so the dead-letter stream and the reaper cannot drift
#: onto a different stream than the reader.
COMMAND_STREAM = "command_queue"
CONSUMER_GROUP = "command_workers"

#: Where a command that has exhausted its retries goes. A separate stream rather than a
#: key per entry, so the backlog is inspectable in order with XRANGE and can be replayed
#: once the cause is fixed.
DEAD_LETTER_STREAM = "command_queue:dead_letter"

#: How many times one command may be attempted before it is dead-lettered. Deliberately
#: small: these are operational commands whose value decays fast -- a ``start_bot`` that
#: has failed five times is not going to succeed on the sixth, and a stale one is worse
#: than none.
MAX_DELIVERIES = 5

#: TTL on the per-entry attempt counter. Long enough to outlive any realistic retry
#: sequence, short enough that the keyspace does not accumulate forever.
ATTEMPT_TTL_SECONDS = 24 * 60 * 60

#: Idle time before an entry is considered abandoned, as a multiple of the poll
#: interval, and the floor that multiple may not go under.
RECLAIM_IDLE_MULTIPLE = 10
MIN_RECLAIM_IDLE_SECONDS = 30.0

#: How many abandoned entries one pass will reclaim. Bounded so a large PEL cannot starve
#: the live ``>`` read behind it.
RECLAIM_BATCH = 10


class Outcome(str, Enum):
    """What happened to one command, and therefore whether to acknowledge it.

    The distinction the original ``finally:`` collapsed. ``REFUSED`` and ``DONE`` both
    acknowledge; only ``FAILED`` leaves the entry pending for another attempt.
    """

    #: Executed. Acknowledge.
    DONE = "done"
    #: Terminally rejected on policy. Acknowledge -- a retry reaches the same verdict.
    REFUSED = "refused"
    #: Transient error. Do NOT acknowledge; leave it in the PEL to be reclaimed.
    FAILED = "failed"


async def _ensure_group(stream: str, group: str):
    if not redis_manager.pool:
        return
    try:
        await redis_manager.pool.xgroup_create(stream, group, id="$", mkstream=True)
    except Exception:
        pass


def _default_consumer_name() -> str:
    """A consumer name unique to this process.

    WORK-02. The previous default was ``os.getenv("WORKER_NAME", "command_worker_1")``
    -- one shared string. A consumer group's PEL is keyed *by consumer name*, so every
    replica sharing a name shares one pending list: each pod could XACK an entry
    delivered to another, and XAUTOCLAIM could not tell a dead pod's backlog from a live
    pod's in-flight work. Scaling the worker past one replica quietly broke the delivery
    guarantee the consumer group exists to provide.

    ``WORKER_NAME`` is still honoured when set, because it is also the suffix of the
    ``worker:health:*`` key and an operator may want a stable name for a single-replica
    deployment -- but the host and pid are appended so two replicas that inherit the same
    value from a shared task definition still get distinct identities.
    """
    base = os.getenv("WORKER_NAME") or "command_worker"
    try:
        host = socket.gethostname() or "unknown-host"
    except Exception:
        host = "unknown-host"
    return f"{base}-{host}-{os.getpid()}"


from backend_app.core.worker_base import WorkerBase

class CommandWorker(WorkerBase):
    def __init__(self, worker_id: str | None = None):
        name = worker_id or _default_consumer_name()
        super().__init__(worker_name=name, poll_interval=1.0)
        self.worker_id = name
        #: Counters for health_details(), so the dead-letter backlog is observable rather
        #: than only discoverable by reading logs.
        self.commands_done = 0
        self.commands_refused = 0
        self.commands_failed = 0
        self.commands_dead_lettered = 0
        self.commands_reclaimed = 0

    async def start(self):
        await redis_manager.connect()
        await _ensure_group(COMMAND_STREAM, CONSUMER_GROUP)
        await super().start()

    # ---- durability helpers ------------------------------------------------

    @property
    def reclaim_idle_ms(self) -> int:
        """Idle threshold for XAUTOCLAIM, in milliseconds.

        Floored at :data:`MIN_RECLAIM_IDLE_SECONDS` so a small poll interval cannot turn
        the reaper into a thief that reclaims entries a healthy consumer is mid-way
        through.
        """
        seconds = max(
            self.poll_interval * RECLAIM_IDLE_MULTIPLE, MIN_RECLAIM_IDLE_SECONDS
        )
        return int(seconds * 1000)

    async def _attempt_number(self, msg_id: str) -> int:
        """Which attempt this is, counted durably and atomically.

        INCR rather than a local dict: the whole point is to survive the process, and a
        reclaimed entry is frequently counted by a *different* consumer than the one that
        first took it. Returns ``1`` when the counter cannot be read, which errs toward
        attempting the command rather than dead-lettering a job that may be fine.
        """
        key = f"command:attempts:{msg_id}"
        try:
            count = await redis_manager.incr(key)
            await redis_manager.expire(key, ATTEMPT_TTL_SECONDS)
            return int(count) if count else 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[%s] could not count attempts for %s (%s); treating as attempt 1",
                self.worker_id, msg_id, exc,
            )
            return 1

    async def _forget_attempts(self, msg_id: str) -> None:
        try:
            await redis_manager.delete(f"command:attempts:{msg_id}")
        except Exception:
            pass

    async def _dead_letter(
        self, msg_id: str, fields: Dict[str, Any], reason: str, attempts: int
    ) -> None:
        """Park a command that cannot be run, then let the caller acknowledge it.

        The original fields are copied verbatim so the entry can be replayed once the
        cause is fixed; the diagnosis travels beside them rather than only in a log line
        that will have rotated away by the time anyone looks.
        """
        entry = {
            str(k): ("" if v is None else str(v) if not isinstance(v, (str, bytes)) else v)
            for k, v in (fields or {}).items()
        }
        entry.update({
            "dead_letter_reason": reason,
            "dead_letter_attempts": str(attempts),
            "dead_letter_source_id": str(msg_id),
            "dead_letter_consumer": self.worker_id,
        })
        try:
            await redis_manager.xadd(DEAD_LETTER_STREAM, entry, raise_on_error=False)
            self.commands_dead_lettered += 1
            logger.error(
                "[%s] command %s DEAD-LETTERED after %d attempt(s): %s. Parked on %s; "
                "it was not executed and will not be retried.",
                self.worker_id, msg_id, attempts, reason, DEAD_LETTER_STREAM,
            )
        except Exception as exc:  # noqa: BLE001
            # If the dead-letter write fails we must NOT acknowledge, or the command is
            # lost for good -- which is the very defect being fixed. Re-raise so the
            # caller leaves it pending.
            logger.critical(
                "[%s] could not dead-letter command %s (%s); leaving it PENDING rather "
                "than dropping it.",
                self.worker_id, msg_id, exc,
            )
            raise

    async def _reclaim_abandoned(self) -> list:
        """Entries abandoned in this group's PEL, as ``[(stream, [(id, fields), ...])]``.

        Shaped like an XREADGROUP batch so the dispatch loop treats reclaimed and fresh
        entries identically -- there is no second code path that could drift.
        """
        try:
            result = await redis_manager.xautoclaim(
                COMMAND_STREAM,
                CONSUMER_GROUP,
                self.worker_id,
                self.reclaim_idle_ms,
                start_id="0-0",
                count=RECLAIM_BATCH,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] XAUTOCLAIM failed: %s", self.worker_id, exc)
            return []

        if not result:
            return []
        # redis-py returns (next_start_id, entries, deleted_ids); older servers omit the
        # third element.
        entries = result[1] if len(result) > 1 else []
        if not entries:
            return []
        self.commands_reclaimed += len(entries)
        logger.warning(
            "[%s] reclaimed %d abandoned command(s) idle > %dms from %s/%s",
            self.worker_id, len(entries), self.reclaim_idle_ms,
            COMMAND_STREAM, CONSUMER_GROUP,
        )
        return [(COMMAND_STREAM, entries)]

    # ---- the work ----------------------------------------------------------

    async def process_iteration(self):
        # Abandoned entries first: a stop_bot left pending by a crashed pod is more
        # urgent than whatever arrived since, and bounding the batch keeps the live read
        # behind it from starving.
        batches = await self._reclaim_abandoned()

        fresh = await redis_manager.xreadgroup(
            group=CONSUMER_GROUP, consumer=self.worker_id, streams={COMMAND_STREAM: ">"}
        )
        if fresh:
            batches = batches + list(fresh)

        if not batches:
            return

        for stream_name, messages in batches:
            for msg_id, fields in messages:
                attempts = await self._attempt_number(msg_id)

                if attempts > MAX_DELIVERIES:
                    try:
                        await self._dead_letter(
                            msg_id, fields,
                            f"exceeded MAX_DELIVERIES={MAX_DELIVERIES}", attempts,
                        )
                    except Exception:
                        continue  # left pending on purpose; see _dead_letter
                    await redis_manager.xack(stream_name, CONSUMER_GROUP, msg_id)
                    await self._forget_attempts(msg_id)
                    continue

                outcome, detail = await self._dispatch(msg_id, fields)

                if outcome is Outcome.FAILED:
                    # NOT acknowledged. This is the whole fix: the entry stays in the PEL
                    # so _reclaim_abandoned can bring it back, instead of being dropped
                    # by a `finally:` that could not tell failure from success.
                    self.commands_failed += 1
                    logger.error(
                        "[%s] command %s FAILED on attempt %d/%d (%s); left PENDING for "
                        "redelivery.",
                        self.worker_id, msg_id, attempts, MAX_DELIVERIES, detail,
                    )
                    continue

                if outcome is Outcome.REFUSED:
                    self.commands_refused += 1
                else:
                    self.commands_done += 1
                await redis_manager.xack(stream_name, CONSUMER_GROUP, msg_id)
                await self._forget_attempts(msg_id)

    async def _dispatch(
        self, msg_id: str, fields: Dict[str, Any]
    ) -> Tuple[Outcome, str]:
        """Execute one command and say what became of it.

        Returns an :class:`Outcome` instead of raising so the caller can make the
        acknowledge/leave-pending decision in one place. A malformed entry is REFUSED
        rather than FAILED: no number of retries will make unparseable JSON parse, and
        leaving it pending forever would block nothing but still grow the PEL.
        """
        try:
            action = fields.get("action")
            if isinstance(action, bytes):
                action = action.decode()

            payload_raw = fields.get("payload", "{}")
            if isinstance(payload_raw, bytes):
                payload_raw = payload_raw.decode()

            try:
                payload = json.loads(payload_raw)
            except (ValueError, TypeError) as exc:
                return Outcome.REFUSED, f"unparseable payload: {exc}"
            if not isinstance(payload, dict):
                return Outcome.REFUSED, f"payload is {type(payload).__name__}, not an object"

            if action == "start_bot":
                user_id = payload.get("user_id")
                # BUG-FIX WORKER-01 / UK-04: Verify subscription before starting bot.
                # Also block "unknown" — if subscription cache is unavailable we must
                # fail closed rather than allowing unverified users to trade.
                from backend_app.core.subscription_middleware import get_user_subscription
                sub = await get_user_subscription(user_id) if user_id else None
                sub_status = sub.get("status") if sub else "unknown"
                BLOCKED_STATUSES = ("cancelled", "expired", "suspended", "unknown")
                if sub_status in BLOCKED_STATUSES:
                    logger.warning(
                        f"Rejecting start_bot for user {user_id}: "
                        f"Subscription status is '{sub_status}' (blocked)"
                    )
                    # REFUSED, not FAILED: an entitlement verdict is terminal. Retrying
                    # a cancelled user's bot until the retry budget runs out would turn
                    # an enforcement decision into a dead-letter entry and would keep
                    # re-querying the subscription on every reclaim.
                    return Outcome.REFUSED, f"subscription status {sub_status!r} is blocked"
                if "paper_trading" not in payload:
                    # A start_bot command that does not state its mode is not
                    # actionable. Reading it with a default is exactly the defect
                    # that made every live deployment run as paper
                    # (`blueprint.get("paper_trading", True)` in master_executor),
                    # so this refuses instead of guessing — and refusing means no
                    # bot, which is the safe direction.
                    # Regression: tests/test_live_deployment_mode_propagation.py
                    logger.error(
                        "Rejecting start_bot for user %s: the command carries no "
                        "'paper_trading' mode. The publisher must state whether "
                        "this deployment was authorised to trade live; no bot was "
                        "started.",
                        user_id,
                    )
                    return Outcome.REFUSED, "command carries no 'paper_trading' mode"

                await app_state.fleet.start_bot(
                    payload["user_id"],
                    payload["symbol"],
                    payload["blueprint"],
                    paper_trading=bool(payload["paper_trading"]),
                )
                return Outcome.DONE, "start_bot dispatched"

            if action == "stop_bot":
                await app_state.fleet.stop_bot(
                    payload["user_id"], payload["symbol"]
                )
                return Outcome.DONE, "stop_bot dispatched"

            if action == "kill_all":
                await app_state.fleet.shutdown_all()
                return Outcome.DONE, "kill_all dispatched"

            # An action nobody handles will never be handled by retrying it.
            return Outcome.REFUSED, f"unknown action {action!r}"

        except KeyError as exc:
            # A required field is missing. Structural, so terminal.
            return Outcome.REFUSED, f"command is missing {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Worker failed: %s", exc)
            return Outcome.FAILED, f"{type(exc).__name__}: {exc}"

    # ---- observability -----------------------------------------------------

    async def health_details(self) -> Dict[str, Any]:
        """This worker's durability facts, for a health endpoint to read.

        ``dead_lettered`` and ``pending`` are the two numbers that make the fix above
        actionable: commands can now survive a failure, so somebody has to be able to see
        that they did. XPENDING is asked of Redis rather than counted locally because the
        PEL spans every replica.
        """
        pending: Optional[Any] = None
        try:
            pending = await redis_manager.xpending(COMMAND_STREAM, CONSUMER_GROUP)
        except Exception:
            pending = None

        return {
            "worker_name": self.worker_name,
            "consumer": self.worker_id,
            "running": self.running,
            "consecutive_failures": self._consecutive_failures,
            "stream": COMMAND_STREAM,
            "group": CONSUMER_GROUP,
            "dead_letter_stream": DEAD_LETTER_STREAM,
            "max_deliveries": MAX_DELIVERIES,
            "reclaim_idle_ms": self.reclaim_idle_ms,
            "commands_done": self.commands_done,
            "commands_refused": self.commands_refused,
            "commands_failed": self.commands_failed,
            "commands_dead_lettered": self.commands_dead_lettered,
            "commands_reclaimed": self.commands_reclaimed,
            "pending_summary": pending,
        }


async def run_worker():
    worker = CommandWorker()
    await worker.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await worker.stop()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
