"""
core/reconciliation_scheduler.py — Background Reconciliation Scheduler

STEP 9 — RECONCILIATION SCHEDULER (MANDATORY)

Runs as a background asyncio task.  Every POLL_INTERVAL_SECONDS it:

  1. Fetches all NEW reconciliation mismatches from the DB.
  2. For each mismatch:
     a. CRITICAL severity  → triggers kill switch + sends CRITICAL alert.
     b. HIGH severity       → sends HIGH alert (no auto kill switch).
     c. MEDIUM severity     → logs + sends MEDIUM alert.
  3. Marks mismatches as ESCALATED in DB (idempotent — already-escalated
     rows are skipped).
  4. After MAX_ESCALATION_ROUNDS without resolution, emits a re-escalation
     alert to prevent alert fatigue from burying the issue.

INTEGRATION:
  Call `start_reconciliation_scheduler()` from the FastAPI lifespan startup hook.
  Call `stop_reconciliation_scheduler()` on shutdown.

EXPECTED RESULT:
  ✔ No mismatch is ever silently ignored.
  ✔ CRITICAL mismatches immediately halt all execution via kill switch.
  ✔ Full audit trail — every escalation is recorded in the DB.

INF-04 — LEADER ELECTION (one worker per poll cycle, not every worker)
------------------------------------------------------------------------
`start_reconciliation_scheduler()` is called from `main.py`'s FastAPI lifespan startup
hook, which runs once per WEB WORKER PROCESS — every uvicorn/gunicorn worker therefore
built its own `ReconciliationScheduler` and ran this loop independently, polling the same
`reconciliation_mismatches` table every `POLL_INTERVAL_SECONDS` from N processes at once.
Each process would escalate the same NEW mismatch and attempt to trigger the SAME kill
switch redundantly on every cycle — the kill switch and the "already-escalated" check are
each idempotent on their own, so this did not multiply a mismatch's real-world effect, but
it did multiply the DB/alerting load by N for no benefit, which is exactly INF-04's finding.

`_try_become_leader` makes exactly one process "the" scheduler at a time via a renewable
Redis lease (`SET key value NX EX` to win it when unheld, a plain `SET` to renew it while
still held) — the same primitive `master_executor`'s own execution lock and
`IdempotentExchangeSubmission.claim_submission` already use, and it fails CLOSED the same
way theirs do: `redis_manager.get_client()`'s own docstring explains why an unavailable
cache in a non-development deployment must refuse rather than hand back a per-process mock
that would make every worker win its own lock and silently reintroduce the N-way fan-out
this fix exists to remove. A worker that is not leader this cycle skips `_poll_and_escalate`
entirely rather than running it a second time for nothing.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger("ReconciliationScheduler")


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

POLL_INTERVAL_SECONDS: int = 30          # How often to poll for new mismatches
BATCH_SIZE: int = 50                     # Max mismatches processed per poll
MAX_RE_ESCALATION_ROUNDS: int = 3        # Re-escalate after N escalation cycles
RE_ESCALATION_INTERVAL_SECONDS: int = 300  # 5 minutes between re-escalations

#: INF-04. Longer than one poll cycle so a renewal that lands a little late (a slow DB
#: round trip, a GC pause) does not let a second process win leadership mid-cycle; short
#: enough that a crashed leader's role is picked up by another process within one TTL
#: window rather than leaving the mismatches table unpolled indefinitely.
LEADER_LOCK_TTL_SECONDS: int = POLL_INTERVAL_SECONDS * 3
LEADER_LOCK_KEY: str = "leader:reconciliation_scheduler"


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════════

class ReconciliationScheduler:
    """
    Background scheduler that detects, escalates, and tracks reconciliation
    mismatches across all tenants.

    Lifecycle:
        scheduler = ReconciliationScheduler()
        await scheduler.start()   # spawns background task
        ...
        await scheduler.stop()    # graceful shutdown
    """

    def __init__(
        self,
        poll_interval: int = POLL_INTERVAL_SECONDS,
        batch_size: int = BATCH_SIZE,
        leader_lock_ttl: int = LEADER_LOCK_TTL_SECONDS,
        leader_lock_key: str = LEADER_LOCK_KEY,
    ):
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._task: Optional[asyncio.Task] = None
        self._running = False
        #: One id per process: the module-level singleton means one instance per worker,
        #: so this is stable for the process's whole lifetime and distinct from every
        #: other worker's - exactly the identity a renewable lease needs to tell "still
        #: me, renew" apart from "someone else, back off".
        self._instance_id = str(uuid.uuid4())
        self._leader_lock_ttl = leader_lock_ttl
        self._leader_lock_key = leader_lock_key

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background scheduler loop."""
        if self._running:
            logger.warning("[ReconciliationScheduler] Already running — start() ignored")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="reconciliation_scheduler")
        logger.info(
            f"[ReconciliationScheduler] Started — "
            f"poll_interval={self._poll_interval}s, batch={self._batch_size}"
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
        logger.info("[ReconciliationScheduler] Stopped")

    # ── Main Loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Main polling loop — runs until _running is False."""
        logger.info(
            f"[ReconciliationScheduler] Loop started — instance={self._instance_id}"
        )
        while self._running:
            try:
                if await self._try_become_leader():
                    await self._poll_and_escalate()
                else:
                    logger.debug(
                        "[ReconciliationScheduler] Not leader this cycle — skipping poll"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"[ReconciliationScheduler] Poll cycle error: {e}",
                    exc_info=True,
                )
            await asyncio.sleep(self._poll_interval)

    # ── Leader Election (INF-04) ────────────────────────────────────────────────

    async def _try_become_leader(self) -> bool:
        """``True`` iff THIS process may run ``_poll_and_escalate`` this cycle.

        A renewable lease, not a one-shot lock: a process already holding it re-sets the
        SAME key with its OWN value to extend the TTL (plain ``SET``, no ``NX``, so it
        always succeeds for the current holder) rather than letting the lease lapse and
        re-racing for it every cycle - the module docstring's "INF-04 — LEADER ELECTION"
        section explains why a one-shot-only lock would be wrong here (every process
        would lose and regain leadership every single cycle, which defeats the point).
        A process that does NOT hold it attempts ``SET key value NX EX`` once; winning
        that call is what makes it the new leader.

        Fails CLOSED: no reachable Redis client answers ``False`` (never-leader) rather
        than letting every process assume it has no competition and poll independently -
        the exact silent per-process fan-out this method exists to prevent. See
        ``redis_manager.get_client``'s own docstring for why a DEV_MODE-only mock must
        never stand in for Redis outside a development deployment.
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
            logger.warning(f"[ReconciliationScheduler] Leadership check failed: {e}")
            return False

    # ── Poll & Escalate ───────────────────────────────────────────────────────

    async def _poll_and_escalate(self) -> None:
        """
        Single poll cycle:
          1. Fetch NEW mismatches from DB.
          2. Escalate each one according to severity.
          3. Re-escalate long-standing ESCALATED mismatches.
        """
        from backend_app.core.database import get_db_context
        from backend_app.core.models.reconciliation import \
            ReconciliationMismatchRepository

        with get_db_context() as db:
            repo = ReconciliationMismatchRepository(db)

            # ── Step A: Handle NEW mismatches ──────────────────────────────
            new_mismatches = repo.list_new(limit=self._batch_size)

            if new_mismatches:
                logger.warning(
                    f"[ReconciliationScheduler] Found {len(new_mismatches)} NEW mismatch(es)"
                )

            for mismatch in new_mismatches:
                await self._escalate(mismatch, repo)

            # ── Step B: Re-escalate stale ESCALATED mismatches ─────────────
            escalated_mismatches = repo.list_escalated(limit=self._batch_size)

            for mismatch in escalated_mismatches:
                if self._needs_re_escalation(mismatch):
                    await self._re_escalate(mismatch, repo)

    # ── Escalation Logic ──────────────────────────────────────────────────────

    async def _escalate(self, mismatch, repo) -> None:
        """
        Escalate a single NEW mismatch.

        CRITICAL → kill switch + alert
        HIGH     → alert only
        MEDIUM   → alert only
        """
        from backend_app.core.alerting_system import (AlertSeverity,
                                                      get_alerting_system)
        from backend_app.core.global_safety import get_global_kill_switch
        from backend_app.core.models.reconciliation import MismatchSeverity

        mismatch_id = mismatch.mismatch_id
        severity = mismatch.severity
        kill_switch_triggered = False

        logger.warning(
            f"[ReconciliationScheduler] Escalating mismatch {mismatch_id} | "
            f"execution={mismatch.execution_id} | field={mismatch.field} | "
            f"severity={severity}"
        )

        # ── CRITICAL: halt execution ───────────────────────────────────────
        if severity == MismatchSeverity.CRITICAL:
            try:
                kill_switch = get_global_kill_switch()
                await kill_switch.trigger_on_reconciliation_mismatch(
                    order_id=mismatch.execution_id,
                    local_state=mismatch.raw_local_state or {
                        "field": mismatch.field,
                        "value": mismatch.local_value,
                    },
                    exchange_state=mismatch.raw_exchange_state or {
                        "field": mismatch.field,
                        "value": mismatch.exchange_value,
                    },
                )
                kill_switch_triggered = True
                logger.critical(
                    f"[ReconciliationScheduler] KILL SWITCH TRIGGERED for mismatch {mismatch_id}"
                )
            except Exception as e:
                logger.error(
                    f"[ReconciliationScheduler] Failed to trigger kill switch: {e}",
                    exc_info=True,
                )

        # ── Alert all severities ───────────────────────────────────────────
        try:
            alerting = get_alerting_system()
            alert_severity_map = {
                MismatchSeverity.CRITICAL: AlertSeverity.CRITICAL,
                MismatchSeverity.HIGH:     AlertSeverity.HIGH,
                MismatchSeverity.MEDIUM:   AlertSeverity.MEDIUM,
            }
            alert_severity_map.get(severity, AlertSeverity.HIGH)

            await alerting.alert_reconciliation_mismatch(
                tenant_id=str(mismatch.tenant_id),
                execution_id=mismatch.execution_id,
                local_state=mismatch.raw_local_state or {"value": mismatch.local_value},
                exchange_state=mismatch.raw_exchange_state or {"value": mismatch.exchange_value},
                details={
                    "mismatch_id": mismatch_id,
                    "field": mismatch.field,
                    "symbol": mismatch.symbol,
                    "side": mismatch.side,
                    "order_id": mismatch.order_id,
                    "severity": severity.value if hasattr(severity, "value") else severity,
                    "kill_switch_triggered": kill_switch_triggered,
                    "escalation_round": 1,
                },
            )
        except Exception as e:
            logger.error(
                f"[ReconciliationScheduler] Failed to send alert for {mismatch_id}: {e}",
                exc_info=True,
            )

        # ── Persist escalation ────────────────────────────────────────────
        try:
            repo.mark_escalated(mismatch_id, kill_switch_triggered=kill_switch_triggered)
        except Exception as e:
            logger.error(
                f"[ReconciliationScheduler] Failed to mark escalated in DB: {e}",
                exc_info=True,
            )

    def _needs_re_escalation(self, mismatch) -> bool:
        """
        Returns True if a still-ESCALATED mismatch has been quiet long enough
        to warrant a re-escalation alert (prevents alert fatigue from burying
        persistent issues).
        """
        if mismatch.escalation_count >= MAX_RE_ESCALATION_ROUNDS:
            return False  # Stop re-escalating after max rounds to prevent spam

        if mismatch.last_escalated_at is None:
            return True

        seconds_since = (datetime.utcnow() - mismatch.last_escalated_at).total_seconds()
        return seconds_since >= RE_ESCALATION_INTERVAL_SECONDS

    async def _re_escalate(self, mismatch, repo) -> None:
        """Send a follow-up alert for a mismatch that remains unresolved."""
        from backend_app.core.alerting_system import (AlertSeverity, AlertType,
                                                      get_alerting_system)

        mismatch_id = mismatch.mismatch_id
        round_num = (mismatch.escalation_count or 0) + 1

        logger.warning(
            f"[ReconciliationScheduler] Re-escalating mismatch {mismatch_id} "
            f"(round {round_num})"
        )

        try:
            alerting = get_alerting_system()
            await alerting.send_alert(
                alert_type=AlertType.RECONCILIATION_MISMATCH,
                severity=AlertSeverity.CRITICAL,
                title=f"⚠️ Unresolved Mismatch (Round {round_num}): {mismatch.symbol}",
                message=(
                    f"Reconciliation mismatch for execution {mismatch.execution_id} "
                    f"remains unresolved after {mismatch.escalation_count} escalation(s). "
                    f"Field: {mismatch.field} | "
                    f"Local: {mismatch.local_value} | "
                    f"Exchange: {mismatch.exchange_value}"
                ),
                source="reconciliation_scheduler",
                tenant_id=str(mismatch.tenant_id),
                execution_id=mismatch.execution_id,
                details={
                    "mismatch_id": mismatch_id,
                    "escalation_round": round_num,
                    "first_detected_at": mismatch.detected_at.isoformat() if mismatch.detected_at else None,
                    "kill_switch_triggered": mismatch.kill_switch_triggered,
                },
                force=True,  # Never deduplicate re-escalations
            )
        except Exception as e:
            logger.error(
                f"[ReconciliationScheduler] Re-escalation alert failed: {e}",
                exc_info=True,
            )

        try:
            repo.mark_escalated(mismatch_id, kill_switch_triggered=mismatch.kill_switch_triggered)
        except Exception as e:
            logger.error(
                f"[ReconciliationScheduler] Failed to update escalation count: {e}",
                exc_info=True,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON + LIFECYCLE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

_scheduler: Optional[ReconciliationScheduler] = None


def get_reconciliation_scheduler() -> ReconciliationScheduler:
    """Return the global scheduler instance (creates if needed)."""
    global _scheduler
    if _scheduler is None:
        _scheduler = ReconciliationScheduler()
    return _scheduler


async def start_reconciliation_scheduler() -> None:
    """Call this from the FastAPI lifespan startup event."""
    scheduler = get_reconciliation_scheduler()
    await scheduler.start()


async def stop_reconciliation_scheduler() -> None:
    """Call this from the FastAPI lifespan shutdown event."""
    scheduler = get_reconciliation_scheduler()
    await scheduler.stop()


__all__ = [
    "ReconciliationScheduler",
    "get_reconciliation_scheduler",
    "start_reconciliation_scheduler",
    "stop_reconciliation_scheduler",
]
