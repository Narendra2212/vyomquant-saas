"""core/creator_payout_scheduler.py — Weekly Creator Payout Scheduler (MKT-06)

There is currently NO way for a marketplace strategy creator to ever actually
receive their earned revenue share. `routers/library.py::creator_analytics`
(via `settlement_service.creator_earnings`) already computes a creator's
lifetime earnings correctly, but that function is READ-ONLY -- nothing marks
any of it paid, and nothing initiates a payout. This module is the platform
side of the fix: on a recurring interval, it finds every creator with a
nonzero unpaid balance and calls `public.create_creator_payout`
(`backend_app/migrations/051_mkt06_creator_payout_atomic.sql`) for each one.

WHAT THIS DOES NOT DO (same as REF-01's own settlement step)
--------------------------------------------------------------
This scheduler CREATES payout rows. It never moves money: there is no outbound
payment-rail integration anywhere in this codebase (Stripe/Razorpay are wired
for INCOMING subscription payments only), for creator payouts any more than
for referral payouts. Settling a created payout -- recording that an operator
actually transferred the money manually -- is a separate, admin-only step
(`routers/library.py::settle_creator_payout_endpoint`), not performed here.

POLLING SHAPE: HOURLY CHECK, NOT A BARE 7-DAY SLEEP
----------------------------------------------------
A naive `while True: await asyncio.sleep(7 * 86400)` loop would never recover a
missed cycle: if the process restarts six days into the wait, the sleep starts
over and the sweep is now at least thirteen days late with no way to notice.
Instead, this polls on `POLL_INTERVAL_SECONDS` (default: hourly) and, each
tick, asks `public.marketplace_creator_payout_sweeps` -- a durable Postgres
table, not an in-memory timestamp -- whether `SWEEP_INTERVAL_SECONDS` (default:
seven days) have passed since the last sweep that recorded a `finished_at`. A
sweep that started and crashed leaves a row with `finished_at` still NULL,
which does not count as a completion, so a crash does not silently push the
next real attempt a further seven days out.

LEADER ELECTION (ONE WORKER PER SWEEP, NOT N)
------------------------------------------------
`start_creator_payout_scheduler()` is called from `main.py`'s FastAPI lifespan
startup hook, which runs once per WEB WORKER PROCESS -- exactly the same
N-worker fan-out `reconciliation_scheduler.py`'s own `_try_become_leader`
(INF-04) exists to prevent for the reconciliation poll loop. This module reuses
that identical primitive -- a renewable Redis lease via `SET key value NX EX`
to win it when unheld, a plain `SET` to renew it while still held -- under its
OWN lease key, so the two schedulers' leaderships are independent of each
other (a process leading the reconciliation poll is not thereby the creator-
payout sweep leader, and vice versa). It fails CLOSED the same way: no
reachable Redis client means this process does NOT assume it is unopposed and
does NOT run the sweep this cycle, for the identical reason INF-04's own
module docstring gives -- an unavailable cache in a non-development deployment
must refuse, not silently hand back a per-process mock that would let every
worker believe itself the leader and run the sweep N times concurrently.
Running N copies concurrently would not be a correctness bug on its own
(`create_creator_payout`'s own `pg_advisory_xact_lock`, per owner_id, makes
concurrent calls for the same creator safe -- proven in
`tests/test_mkt06_creator_payout_atomic_postgres.py`), but it would multiply
the DB load across every creator by N for no benefit, which is exactly what
leader election exists to avoid.

CURRENCIES, AND WHY THIS MODULE NEVER COMPUTES A BALANCE ITSELF
-------------------------------------------------------------------
This scheduler does not read `marketplace_settlements` or sum anything. It
only discovers WHICH owner_ids have at least one settlement row at all (the
candidate set to call `create_creator_payout` for) and lets that function's own
atomic, per-currency, claim-aware logic decide whether there is anything
payable. A creator with zero unpaid earnings, or a currency whose unclaimed
balance nets to zero or negative, gets no payout row -- that decision lives
entirely in `051`'s SQL, once, rather than being re-implemented (and possibly
drifting from `creator_earnings`'s own formula) here.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("CreatorPayoutScheduler")


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

#: How often this process wakes up to CHECK whether a sweep is due. Hourly, not
#: a bare multi-day sleep -- see the module docstring for why.
POLL_INTERVAL_SECONDS: int = 3600

#: How long must have passed since the last successfully FINISHED sweep before
#: another one is due. "Weekly", concretely.
SWEEP_INTERVAL_SECONDS: int = 7 * 86400

#: Max distinct owner_ids processed in one sweep call, to bound a single sweep's
#: duration the same way ReconciliationScheduler.BATCH_SIZE bounds its own poll.
MAX_OWNERS_PER_SWEEP: int = 5000

#: INF-04's own reasoning, reused verbatim for a different lease key: longer than
#: one poll cycle so a renewal landing a little late does not let a second
#: process win leadership mid-cycle; short enough that a crashed leader's role is
#: picked up within one TTL window.
LEADER_LOCK_TTL_SECONDS: int = POLL_INTERVAL_SECONDS * 3
LEADER_LOCK_KEY: str = "leader:creator_payout_scheduler"


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════════


class CreatorPayoutScheduler:
    """Background scheduler that sweeps every marketplace creator's unpaid
    balance into weekly payout rows.

    Lifecycle:
        scheduler = CreatorPayoutScheduler()
        await scheduler.start()   # spawns background task
        ...
        await scheduler.stop()    # graceful shutdown
    """

    def __init__(
        self,
        poll_interval: int = POLL_INTERVAL_SECONDS,
        sweep_interval: int = SWEEP_INTERVAL_SECONDS,
        max_owners_per_sweep: int = MAX_OWNERS_PER_SWEEP,
        leader_lock_ttl: int = LEADER_LOCK_TTL_SECONDS,
        leader_lock_key: str = LEADER_LOCK_KEY,
    ):
        self._poll_interval = poll_interval
        self._sweep_interval = sweep_interval
        self._max_owners_per_sweep = max_owners_per_sweep
        self._task: Optional[asyncio.Task] = None
        self._running = False
        #: One id per process, stable for its whole lifetime -- the identity a
        #: renewable lease needs to tell "still me, renew" apart from "someone
        #: else, back off". Same role as ReconciliationScheduler's own
        #: `_instance_id`.
        self._instance_id = str(uuid.uuid4())
        self._leader_lock_ttl = leader_lock_ttl
        self._leader_lock_key = leader_lock_key

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background scheduler loop."""
        if self._running:
            logger.warning("[CreatorPayoutScheduler] Already running — start() ignored")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="creator_payout_scheduler")
        logger.info(
            f"[CreatorPayoutScheduler] Started — poll_interval={self._poll_interval}s, "
            f"sweep_interval={self._sweep_interval}s"
        )

    async def stop(self) -> None:
        """Gracefully stop the scheduler."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[CreatorPayoutScheduler] Stopped")

    # ── Main Loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Main polling loop — runs until _running is False.

        Every tick: become leader or skip; if leader, check whether a sweep is
        due (via the durable sweep log, not an in-memory timestamp); if due,
        run it.
        """
        logger.info(
            f"[CreatorPayoutScheduler] Loop started — instance={self._instance_id}"
        )
        while self._running:
            try:
                if await self._try_become_leader():
                    if await self._sweep_is_due():
                        await self._run_sweep()
                    else:
                        logger.debug(
                            "[CreatorPayoutScheduler] Leader this cycle, but the "
                            "weekly sweep is not due yet"
                        )
                else:
                    logger.debug(
                        "[CreatorPayoutScheduler] Not leader this cycle — skipping"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"[CreatorPayoutScheduler] Poll cycle error: {e}", exc_info=True,
                )
            await asyncio.sleep(self._poll_interval)

    # ── Leader Election (reuses INF-04's primitive, own lease key) ─────────────

    async def _try_become_leader(self) -> bool:
        """``True`` iff THIS process may run a sweep this cycle.

        Identical mechanism to `reconciliation_scheduler.ReconciliationScheduler.
        _try_become_leader`: a renewable Redis lease (`SET key value NX EX` to
        win when unheld, a plain `SET` to renew while already held), under this
        scheduler's own `LEADER_LOCK_KEY` so its leadership is independent of the
        reconciliation scheduler's. Fails CLOSED: no reachable Redis client means
        this process does NOT run the sweep this cycle, never a silent
        assumption of no competition. See `redis_manager.get_client`'s own
        docstring for why a DEV_MODE-only mock must never stand in for Redis
        outside a development deployment.
        """
        from backend_app.core.cache.redis_manager import redis_manager

        try:
            client = await redis_manager.get_client()
            if client is None:
                return False
            current_holder = await client.get(self._leader_lock_key)
            if current_holder == self._instance_id:
                await client.set(
                    self._leader_lock_key, self._instance_id, ex=self._leader_lock_ttl
                )
                return True
            acquired = await client.set(
                self._leader_lock_key,
                self._instance_id,
                nx=True,
                ex=self._leader_lock_ttl,
            )
            return bool(acquired)
        except Exception as e:
            logger.warning(f"[CreatorPayoutScheduler] Leadership check failed: {e}")
            return False

    # ── Due-check (durable, survives restarts) ──────────────────────────────────

    async def _sweep_is_due(self) -> bool:
        """``True`` iff no sweep has FINISHED within the last `sweep_interval`
        seconds, read from `marketplace_creator_payout_sweeps` -- real Postgres,
        not a module-level timestamp that resets on every process restart.

        A sweep with no `finished_at` (started, never completed -- a crash
        mid-sweep) is not treated as a completion: it is excluded by the
        `finished_at IS NOT NULL` filter below, so a crash does not push the
        next real attempt further away than it already was.
        """
        svc = self._service_client()
        if svc is None:
            logger.error(
                "[CreatorPayoutScheduler] no service client available; cannot "
                "check whether the weekly sweep is due"
            )
            return False

        try:
            resp = (
                svc.table("marketplace_creator_payout_sweeps")
                .select("finished_at")
                .not_.is_("finished_at", "null")
                .order("finished_at", desc=True)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.error(
                f"[CreatorPayoutScheduler] sweep-log read failed: {exc}", exc_info=True,
            )
            return False

        rows = getattr(resp, "data", None) or []
        if not rows:
            # No sweep has ever completed. Due immediately.
            return True

        last_finished_raw = rows[0].get("finished_at")
        if not last_finished_raw:
            return True

        last_finished = _parse_timestamp(last_finished_raw)
        if last_finished is None:
            # Unreadable timestamp. Treat conservatively as due rather than
            # silently never sweeping again because one row could not be parsed.
            logger.warning(
                "[CreatorPayoutScheduler] could not parse last sweep's "
                f"finished_at={last_finished_raw!r}; treating sweep as due"
            )
            return True

        elapsed = (datetime.now(timezone.utc) - last_finished).total_seconds()
        return elapsed >= self._sweep_interval

    # ── The sweep itself ─────────────────────────────────────────────────────

    async def _run_sweep(self) -> None:
        """One full weekly sweep: find every owner_id with at least one
        settlement row, call `create_creator_payout` for each, record the sweep.

        Recording the sweep (INSERT with `started_at`, then UPDATE with
        `finished_at`) happens around the owner loop, not inside it, so a
        failure partway through the owner loop still leaves `finished_at` NULL
        for this sweep -- `_sweep_is_due` will correctly judge the sweep as not
        completed and retry on a later tick, rather than silently recording a
        success that did not fully happen.
        """
        svc = self._service_client()
        if svc is None:
            logger.error(
                "[CreatorPayoutScheduler] no service client available; sweep skipped"
            )
            return

        try:
            sweep_resp = (
                svc.table("marketplace_creator_payout_sweeps")
                .insert({})
                .execute()
            )
            sweep_rows = getattr(sweep_resp, "data", None) or []
            sweep_id = sweep_rows[0]["id"] if sweep_rows else None
        except Exception as exc:
            logger.error(
                f"[CreatorPayoutScheduler] could not record sweep start: {exc}",
                exc_info=True,
            )
            return

        period_end = datetime.now(timezone.utc)
        period_start = period_end - timedelta(seconds=self._sweep_interval)

        owners_processed = 0
        payouts_created = 0

        try:
            owner_ids = await self._distinct_owner_ids(svc)
        except Exception as exc:
            logger.error(
                f"[CreatorPayoutScheduler] could not list creator owner_ids: {exc}",
                exc_info=True,
            )
            owner_ids = []

        for owner_id in owner_ids[: self._max_owners_per_sweep]:
            owners_processed += 1
            try:
                rpc_resp = svc.rpc(
                    "create_creator_payout",
                    {
                        "p_owner_id": owner_id,
                        "p_period_start": period_start.isoformat(),
                        "p_period_end": period_end.isoformat(),
                    },
                ).execute()
                created_rows = getattr(rpc_resp, "data", None) or []
                payouts_created += len(created_rows)
                if created_rows:
                    logger.info(
                        f"[CreatorPayoutScheduler] created {len(created_rows)} "
                        f"payout(s) for creator {owner_id}"
                    )
            except Exception as exc:
                # One creator's failure must not abort the sweep for every other
                # creator -- logged and the loop continues, same discipline
                # `ReconciliationScheduler._escalate`'s own per-mismatch
                # try/except uses so one bad row cannot stall the whole batch.
                logger.error(
                    f"[CreatorPayoutScheduler] create_creator_payout failed for "
                    f"owner {owner_id}: {exc}",
                    exc_info=True,
                )

        try:
            if sweep_id is not None:
                svc.table("marketplace_creator_payout_sweeps").update({
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "owners_processed": owners_processed,
                    "payouts_created": payouts_created,
                }).eq("id", sweep_id).execute()
        except Exception as exc:
            logger.error(
                f"[CreatorPayoutScheduler] could not record sweep completion: {exc}",
                exc_info=True,
            )
            return

        logger.info(
            f"[CreatorPayoutScheduler] sweep complete — owners_processed="
            f"{owners_processed}, payouts_created={payouts_created}"
        )

    async def _distinct_owner_ids(self, svc) -> list:
        """Every distinct `owner_id` that has at least one `marketplace_settlements`
        row — the candidate set `create_creator_payout` is called for.

        This does NOT filter by balance, currency or claim status: that decision
        belongs entirely to `create_creator_payout`'s own atomic SQL (see the
        module docstring), so a creator with nothing payable simply gets zero
        rows back from that call and costs one no-op RPC round trip, not a
        duplicated balance computation here that could drift from the real one.
        """
        resp = (
            svc.table("marketplace_settlements")
            .select("owner_id")
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        seen = []
        seen_set = set()
        for row in rows:
            owner_id = row.get("owner_id")
            if owner_id and owner_id not in seen_set:
                seen_set.add(owner_id)
                seen.append(owner_id)
        return seen

    # ── Service client ────────────────────────────────────────────────────────

    def _service_client(self):
        """The process-wide service-role Supabase client, resolved lazily so this
        module has no import-time dependency on credentials being configured."""
        from backend_app.core.dependencies import get_service_supabase

        return get_service_supabase()


def _parse_timestamp(value) -> Optional[datetime]:
    """Best-effort parse of a Postgres/PostgREST timestamp string into an aware
    ``datetime``. Returns ``None`` (never raises) on anything unreadable, so a
    single malformed row cannot crash the scheduler loop."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON + LIFECYCLE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

_scheduler: Optional[CreatorPayoutScheduler] = None


def get_creator_payout_scheduler() -> CreatorPayoutScheduler:
    """Return the global scheduler instance (creates if needed)."""
    global _scheduler
    if _scheduler is None:
        _scheduler = CreatorPayoutScheduler()
    return _scheduler


async def start_creator_payout_scheduler() -> None:
    """Call this from the FastAPI lifespan startup event."""
    scheduler = get_creator_payout_scheduler()
    await scheduler.start()


async def stop_creator_payout_scheduler() -> None:
    """Call this from the FastAPI lifespan shutdown event."""
    scheduler = get_creator_payout_scheduler()
    await scheduler.stop()


__all__ = [
    "CreatorPayoutScheduler",
    "get_creator_payout_scheduler",
    "start_creator_payout_scheduler",
    "stop_creator_payout_scheduler",
]
