"""
Order Watchdog

STEP 7.1 — PRODUCTION HARDENING

Monitors pending orders and fetches status from exchange.
Prevents orders from being stuck in pending state.

Watchdog Logic:
┌─────────────────────────────────────────────────────────────────┐
│  Every X seconds:                                                  │
│                                                                  │
│  1. Query execution_records for:                                  │
│     status IN (SUBMITTED, PENDING, PARTIALLY_FILLED)              │
│     AND last_exchange_sync < now() - threshold                    │
│                                                                  │
│  2. For each stale order:                                        │
│     ├─ Fetch order status from exchange                          │
│     ├─ Update execution record                                   │
│     ├─ If filled → update position                               │
│     └─ Log discrepancy                                           │
│                                                                  │
│  3. Alert if orders stuck > alert_threshold                       │
└─────────────────────────────────────────────────────────────────┘
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from backend_app.backend.exchange_executor import OrderStatusResult
from backend_app.backend.position_engine import get_position_engine
from backend_app.core.models.execution_record import (ExecutionRecordModel,
                                                      ExecutionStatus)

# STEP 1: Connection layer imports for executor and credential management

logger = logging.getLogger(__name__)

#: W-2. Appended to an alert when the state change that alert describes could NOT be
#: committed. Without it an operator reads "this order is now UNKNOWN" from an alert whose
#: database write failed, and the row is still in its previous status -- the alert would be
#: reporting a recovery that did not happen.
_UNPERSISTED_SUFFIX = (
    " WARNING: this status change could NOT be committed to the database, so the "
    "execution row still carries its previous status. The row has not been recovered."
)


@dataclass
class WatchdogConfig:
    """Configuration for order watchdog."""
    check_interval_seconds: int = 30          # Check every 30s
    stale_threshold_seconds: int = 60         # Orders older than 60s considered stale
    alert_threshold_seconds: int = 300      # Alert if stuck > 5 minutes
    max_retries: int = 3                     # Max fetch retries


# ══════════════════════════════════════════════════════════════════════════
#  THE POSITION LOCK STATEMENT, RENDERED FOR THE BOUND DIALECT
#
#  Both call sites below are labelled "FIN-CRITICAL-002 FIX: Update position with row-level
#  locking", and both ran this, inline and twice:
#
#      SELECT * FROM positions WHERE tenant_id = :tenant_id AND symbol = :symbol FOR UPDATE
#
#  `positions` HAS NO `symbol` COLUMN. `alembic/versions/d97ffff9c3bb_execution_schema_
#  rebuild.py` declares it as exactly five columns -- `id`, `tenant_id`, `data` (JSON),
#  `created_at`, `updated_at` -- and the symbol lives inside the `data` payload. PostgreSQL,
#  which is what production runs, answers `UndefinedColumn: column "symbol" does not exist`.
#
#  Both call sites then swallow it:
#
#      except Exception as lock_error:
#          logger.warning(...)
#          # Continue with update even if lock fails (non-blocking fallback)
#
#  So the row-level lock that fix is named after was never once acquired, and the code went on
#  to mutate the position without it. `main.py:383` starts this watchdog unconditionally at
#  startup, so that is the shipped behaviour, not a dormant branch.
#
#  Nothing caught it for two compounding reasons. `01-pr-check.yml`'s `database-tests` job --
#  the only lane with a real PostgreSQL -- had never executed, because `black --check` failed
#  ahead of it and every test job declared `needs: validate-code`. And the two tests that DO
#  assert this statement, in `tests/test_position_delta_race_condition_fix.py`, are verbatim
#  mirrors of these lines, so they only ever reported it where a real server exists. The first
#  run of that job reported both.
#
#  Same defect family as the `json_extract` portability fix, and fixed with the same renderer:
#  one definition, chosen by the SESSION'S dialect rather than by a module-level assumption.
#  Regression: tests/test_positions_table_column_agreement.py
# ══════════════════════════════════════════════════════════════════════════

def _position_lock_sql(dialect: str) -> str:
    """`SELECT ... FOR UPDATE` locking one tenant's position for one symbol.

    ``dialect`` picks the JSON access spelling: PostgreSQL gets ``data ->> 'symbol'``,
    everything else gets ``json_extract(data, '$.symbol')``. Both compare against the bound
    ``:symbol`` parameter.

    ``FOR UPDATE`` is retained and is the point of the statement. SQLite cannot parse it, which
    is why the SQLite-side regression strips it to check the predicate and the real lock is
    exercised against PostgreSQL in the ``database-tests`` job.
    """
    from backend_app.backend.transactional_execution_manager import json_text

    symbol_sql = json_text("data", "symbol", dialect)
    return (
        "SELECT * FROM positions "  # nosec: B608
        f"WHERE tenant_id = :tenant_id AND {symbol_sql} = :symbol "  # nosec: B608
        "FOR UPDATE"
    )


def _watchdog_session_dialect(db: Any) -> str:
    """The bound dialect's name, or ``"sqlite"`` when it cannot be determined.

    Thin re-export of the accessor `transactional_execution_manager` already uses, kept behind
    a local import so this module does not gain a module-level dependency on it.
    """
    from backend_app.backend.transactional_execution_manager import _session_dialect

    return _session_dialect(db)


class OrderWatchdog:
    """
    STEP 7.1: Order watchdog system.
    
    Monitors pending orders and ensures they don't get stuck.
    Fetches status from exchange for stale orders.
    
    Usage:
        watchdog = OrderWatchdog(db_session, config)
        await watchdog.start()
    """
    
    def __init__(
        self,
        db_session: Session,
        config: Optional[WatchdogConfig] = None
    ):
        self.db = db_session
        self.config = config or WatchdogConfig()
        self._running = False
        self._watchdog_task: Optional[asyncio.Task] = None
        
        # Metrics
        self.checks_run = 0
        self.orders_fixed = 0
        self.alerts_triggered = 0
    
    async def start(self):
        """Start watchdog monitoring loop."""
        self._running = True
        self._watchdog_task = asyncio.create_task(self._monitoring_loop())
        logger.info(
            f"OrderWatchdog started | "
            f"interval={self.config.check_interval_seconds}s | "
            f"stale_threshold={self.config.stale_threshold_seconds}s"
        )
    
    async def stop(self):
        """Stop watchdog."""
        self._running = False
        
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        
        logger.info("OrderWatchdog stopped")
    
    async def _monitoring_loop(self):
        """
        Main monitoring loop.
        
        STEP 9: Runs two tasks:
        1. Stale order check (existing)
        2. Order fill reconciliation loop (every 5-10 seconds)
        """
        # STEP 9: Start reconciliation loop as a separate background task
        reconciliation_task = asyncio.create_task(self._reconciliation_loop())
        
        while self._running:
            try:
                await self._check_stale_orders()

                # TRADE-07. The OTHER stale population: EXECUTING rows with NO venue
                # order id, which `_check_stale_orders` filters out by design (it
                # reconciles by venue order id, and there isn't one). Before this call
                # nothing adjudicated them, so a crash during submission left a row
                # EXECUTING forever -- and because `check_idempotent_execution` returns
                # `skip_already_running` for EXECUTING with no age test, it also blocked
                # that strategy/symbol/bucket permanently.
                #
                # Driven from the SAME loop rather than a new background task, so there is
                # one schedule to reason about and no second lifecycle to start, stop and
                # cancel. The cost is bounded: `classify_stale_executions` is one indexed
                # query that returns nothing in the normal case, and it only considers
                # rows older than `RECOVERY_STALE_AFTER_SECONDS` (900s) -- far longer than
                # this loop's 30s interval, so the work is rare rather than per-tick.
                await self.recover_unsubmitted_executions()

                self.checks_run += 1
                
                # Wait before next check
                await asyncio.sleep(self.config.check_interval_seconds)
                
            except asyncio.CancelledError:
                reconciliation_task.cancel()
                try:
                    await reconciliation_task
                except asyncio.CancelledError:
                    pass
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}", exc_info=True)
                await asyncio.sleep(5)  # Short wait on error
    
    async def _reconciliation_loop(self):
        """
        STEP 9: Order Fill Reconciliation Loop
        
        Background task that runs every 5-10 seconds to:
        - Fetch status for all open orders
        - Update database with latest status
        - Ensure no fills are lost
        
        EXPECTED RESULT: ✔ No lost fills
        """
        logger.info("STEP 9: Order fill reconciliation loop started (interval: 5-10s)")
        
        while self._running:
            try:
                await self._reconcile_open_orders()
                
                # Wait 5-10 seconds before next reconciliation
                # Randomize slightly to prevent thundering herd
                wait_time = 5 + (hash(asyncio.get_event_loop().time()) % 5)
                await asyncio.sleep(wait_time)
                
            except asyncio.CancelledError:
                logger.info("STEP 9: Reconciliation loop cancelled")
                break
            except Exception as e:
                logger.error(f"STEP 9: Reconciliation loop error: {e}", exc_info=True)
                await asyncio.sleep(5)  # Wait before retry
    
    async def _reconcile_open_orders(self):
        """
        STEP 9: Fetch status for all open orders and update database.
        
        This ensures no fills are lost by:
        1. Querying all open orders (SUBMITTED, PENDING, PARTIALLY_FILLED)
        2. Fetching status from exchange
        3. Updating database with any changes
        4. Triggering alerts for any discrepancies
        """
        try:
            # Find all open orders (not just stale ones)
            open_orders = self._find_open_orders()
            
            if not open_orders:
                return
            
            logger.info(f"STEP 9: Reconciling {len(open_orders)} open orders")
            
            reconciled_count = 0
            discrepancy_count = 0
            
            for order in open_orders:
                try:
                    # RT-16: a real, per-tenant executor; see the invariant recorded at
                    # _get_executor_for_order's definition.
                    executor = await self._get_executor_for_order(order)

                    if not executor:
                        logger.warning(
                            f"STEP 9: No executor for order {order.execution_id}, "
                            f"skipping reconciliation"
                        )
                        continue
                    
                    # Fetch latest status from exchange
                    result = await executor.get_order_status(
                        exchange_order_id=order.order_id,
                        symbol=order.symbol
                    )
                    
                    if not result.success:
                        logger.warning(
                            f"STEP 9: Failed to fetch status for order {order.execution_id}: "
                            f"{result.error_message}"
                        )
                        continue
                    
                    # Check for discrepancies
                    if self._has_order_changed(order, result):
                        order_status_val = order.status.value if hasattr(order.status, 'value') else str(order.status)
                        res_status_val = result.status.value if hasattr(result.status, 'value') else str(result.status)
                        logger.info(
                            f"STEP 9: Order {order.execution_id} discrepancy detected: "
                            f"DB status={order_status_val}, "
                            f"Exchange status={res_status_val}, "
                            f"DB filled={order.filled_size}, "
                            f"Exchange filled={result.filled_size}"
                        )
                        
                        # Update order from exchange status
                        await self._update_order_from_status(order, result)

                        # Trigger alert for fill
                        res_filled_dec = Decimal(result.filled_size or "0")
                        order_filled_dec = Decimal(order.filled_size or "0")
                        if res_filled_dec > order_filled_dec:
                            fill_amount = res_filled_dec - order_filled_dec
                            await self._trigger_alert(
                                order,
                                f"STEP 9: Order fill detected via reconciliation. "
                                f"Filled {fill_amount} @ {result.avg_price}"
                            )

                        # RT-16: this is the discrepancy ReconciliationScheduler exists to
                        # escalate, and until now nothing ever wrote one for it to find.
                        self._record_reconciliation_mismatch(
                            order, order_status_val, res_status_val,
                            order_filled_dec, res_filled_dec,
                        )

                        discrepancy_count += 1
                    
                    reconciled_count += 1
                    
                except Exception as e:
                    logger.error(
                        f"STEP 9: Error reconciling order {order.execution_id}: {e}"
                    )
                    continue
            
            if reconciled_count > 0:
                logger.info(
                    f"STEP 9: Reconciliation complete. "
                    f"Checked: {reconciled_count}, Discrepancies: {discrepancy_count}"
                )
                
        except Exception as e:
            logger.error(f"STEP 9: Reconciliation batch error: {e}", exc_info=True)

    def _record_reconciliation_mismatch(
        self,
        order: ExecutionRecordModel,
        local_status: str,
        exchange_status: str,
        local_filled: Decimal,
        exchange_filled: Decimal,
    ) -> None:
        """RT-16: feed ``ReconciliationScheduler`` the discrepancy it exists to escalate.

        Until this call existed, ``core/models/reconciliation.py``'s
        ``ReconciliationMismatchRepository.record_mismatch`` had no caller anywhere in the
        application -- the scheduler was polling a table nothing ever wrote to. This is the
        write side, placed at the one spot ``_reconcile_open_orders`` already proved a
        genuine local/venue divergence (``_has_order_changed`` was true), not a guess at a
        different one.

        Severity MEDIUM ("status or fill-price divergence" -- this file's own taxonomy;
        see ``core/models/reconciliation.MismatchSeverity``), never CRITICAL: CRITICAL is
        the scheduler's own trigger for a global kill switch, and this function has no
        basis to claim this is a SIZE or SYMBOL mismatch (CRITICAL's definition) -- only
        that the venue's status or fill figure moved since the DB was last told. A failure
        to record never blocks or alters reconciliation itself; this is additive visibility,
        not a new control.
        """
        try:
            from backend_app.core.models.reconciliation import (
                MismatchSeverity, ReconciliationMismatchCreate,
                ReconciliationMismatchRepository)

            field = "filled_size" if local_filled != exchange_filled else "status"
            repo = ReconciliationMismatchRepository(self.db)
            repo.record_mismatch(ReconciliationMismatchCreate(
                tenant_id=order.tenant_id,
                execution_id=order.execution_id,
                order_id=order.order_id,
                symbol=order.symbol,
                side=getattr(order, "side", None) or "unknown",
                field=field,
                local_value=f"status={local_status},filled={local_filled}",
                exchange_value=f"status={exchange_status},filled={exchange_filled}",
                severity=MismatchSeverity.MEDIUM,
                raw_local_state={"status": local_status, "filled_size": str(local_filled)},
                raw_exchange_state={"status": exchange_status, "filled_size": str(exchange_filled)},
            ))
        except Exception as exc:  # noqa: BLE001 - recording the mismatch must never break reconciliation
            logger.error(
                "STEP 9: could not record reconciliation mismatch for %s: %s",
                order.execution_id, exc,
            )

    def _find_open_orders(self) -> List[ExecutionRecordModel]:
        """
        STEP 9: Find all open orders that need reconciliation.
        
        Includes:
        - SUBMITTED
        - PENDING  
        - PARTIALLY_FILLED
        - EXECUTING
        """
        open_statuses = [
            ExecutionStatus.PENDING,
            ExecutionStatus.EXECUTING
        ]
        
        orders = self.db.query(ExecutionRecordModel).filter(
            and_(
                ExecutionRecordModel.status.in_(open_statuses),
                ExecutionRecordModel.order_id.isnot(None)  # Must have exchange order ID
            )
        ).all()
        
        return orders
    
    def _has_order_changed(self, order: ExecutionRecordModel, result) -> bool:
        """
        STEP 9: Check if order status has changed on exchange.
        
        Returns True if there are discrepancies between local state and exchange.
        """
        # Check status change
        order_status_str = order.status.value if hasattr(order.status, 'value') else str(order.status)
        result_status_str = result.status.value if hasattr(result.status, 'value') else str(result.status)
        if result_status_str.lower() != order_status_str.lower():
            return True
        
        # Check fill amount change
        if str(result.filled_size) != str(order.filled_size):
            return True
        
        # Check remaining amount change
        if str(result.remaining_size) != str(order.remaining_size):
            return True
        
        return False
    
    async def _check_stale_orders(self):
        """Check for and fix stale orders."""
        stale_orders = self._find_stale_orders()
        
        if not stale_orders:
            return
        
        logger.info(f"Watchdog found {len(stale_orders)} stale orders")
        
        for order in stale_orders:
            try:
                await self._refresh_order_status(order)
            except Exception as e:
                logger.error(
                    f"Failed to refresh order {order.execution_id}: {e}"
                )

    async def recover_unsubmitted_executions(self):
        """Adjudicate EXECUTING rows that have NO venue order id.

        TRADE-07. THE POPULATION THIS WATCHDOG COULD NOT SEE.
        ----------------------------------------------------
        `_find_stale_orders` and `_find_open_orders` both filter
        `order_id IS NOT NULL`, and that filter is CORRECT for what they do -- they
        reconcile by venue order id, and `get_order_status` has nothing else to look an
        order up by. But `order_id` is written only at the COMPLETED transition
        (`execution_engine.py`, after `create_order` has already returned), so a row
        stranded by a crash during submission has `order_id IS NULL` and was invisible to
        the one component that exists to rescue it. It then stayed EXECUTING forever, and
        `check_idempotent_execution` returns `skip_already_running` for EXECUTING with no
        age test -- so it also blocked that (tenant, strategy, symbol, bucket, side, qty,
        price) permanently.

        FOUR CASES, THREE OF WHICH ARE ACTED ON:

        ``failed_before_submission`` -> FAILED.
            `submitted_at IS NULL`, and `mark_submitted` commits BEFORE `create_order`,
            so no venue order can exist. Safe.

        ``ambiguous_submission`` -> ask the venue, then decide.
            `submitted_at` set, `order_id` NULL: the order was sent and the response was
            lost. Resolved by `find_order_by_client_order_id`, using the deterministic
            `execution_id` the venue received as `clientOrderId`.
              * venue has it, with a real id  -> COMPLETED with that id
              * venue definitively does not   -> FAILED
              * venue could not be asked      -> UNKNOWN + alert, left unresolved
            **It is never resubmitted.** Nothing in this method places an order.

        ``submitted_with_order_id`` -> left alone.
            Already `_find_stale_orders`' population. Acting here too would mean two
            paths moving one row.

        WHY IT NEVER RESUBMITS, STATED PLAINLY. The ambiguous case is ambiguous about
        whether real money is already committed. Resubmitting would double a position;
        failing would orphan a live resting order and let the next signal duplicate it.
        UNKNOWN is the only honest third option, and it is a real enum label the watchdog
        already uses for exactly this purpose ("DO NOT ASSUME FILLED").
        """
        from backend_app.core.models.execution_record import (
            ExecutionRecordRepository, ExecutionStatus)

        repo = ExecutionRecordRepository(self.db)
        try:
            candidates = repo.classify_stale_executions()
        except Exception as exc:  # noqa: BLE001
            logger.error("TRADE-07: could not classify stale executions: %s", exc)
            return

        actionable = [
            c for c in candidates
            if c["classification"] != repo.RECOVERY_SUBMITTED_WITH_ORDER_ID
        ]
        if not actionable:
            return

        logger.warning(
            "TRADE-07: %d stale execution(s) with no venue order id require "
            "adjudication.", len(actionable),
        )

        for row in actionable:
            try:
                if row["classification"] == repo.RECOVERY_FAILED_BEFORE_SUBMISSION:
                    repo.resolve_stale_execution(
                        row["execution_id"], row["tenant_id"],
                        status=ExecutionStatus.FAILED,
                        classification=row["classification"],
                        detail=(
                            f"no submission evidence after {row['age_seconds']:.0f}s; "
                            f"the order never reached a venue"
                        ),
                    )
                    continue

                # Ambiguous: the venue may hold this order.
                await self._adjudicate_ambiguous_execution(repo, row)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "TRADE-07: adjudication of %s failed (%s); the row stays EXECUTING "
                    "and will be re-examined.", row["execution_id"], exc,
                )

    async def _adjudicate_ambiguous_execution(self, repo, row: dict):
        """Resolve one `ambiguous_submission` row against the venue. Never resubmits."""
        from backend_app.core.models.execution_record import ExecutionStatus

        execution_id = row["execution_id"]

        class _Shim:
            """`_get_executor_for_order` expects a record-like object."""
            pass

        shim = _Shim()
        for attr in ("execution_id", "tenant_id", "symbol", "exchange_id", "order_id"):
            setattr(shim, attr, row.get(attr))
        shim.user_id = row.get("tenant_id")

        executor = None
        try:
            # RT-16: a real, per-tenant executor built from this tenant's stored keys.
            # Regression: tests/test_d5_watchdog_adjudication_repair.py,
            # tests/test_rt16_watchdog_executor_is_real.py
            executor = await self._get_executor_for_order(shim)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "TRADE-07: could not build an executor to adjudicate %s: %s",
                execution_id, exc,
            )

        if executor is None or not hasattr(executor, "find_order_by_client_order_id"):
            # Cannot ask. UNKNOWN, not FAILED: credentials being unavailable is not
            # evidence that the order does not exist.
            repo.resolve_stale_execution(
                execution_id, row["tenant_id"],
                status=ExecutionStatus.UNKNOWN,
                classification=row["classification"],
                detail=(
                    "submitted but unacknowledged, and no venue connection was available "
                    "to establish whether the order exists"
                ),
            )
            await self._trigger_recovery_alert(
                row,
                "Order was SUBMITTED to the venue and never acknowledged, and the venue "
                "could not be queried. It may be resting or filled. Manual "
                "reconciliation required; the execution has been left UNKNOWN and was "
                "NOT resubmitted.",
            )
            return

        result = await executor.find_order_by_client_order_id(
            execution_id, row.get("symbol") or ""
        )

        if not result.success:
            repo.resolve_stale_execution(
                execution_id, row["tenant_id"],
                status=ExecutionStatus.UNKNOWN,
                classification=row["classification"],
                detail=f"venue lookup failed: {result.error_message}",
            )
            await self._trigger_recovery_alert(
                row,
                f"Order was SUBMITTED and never acknowledged, and the venue lookup "
                f"failed ({result.error_message}). Left UNKNOWN; NOT resubmitted.",
            )
            return

        if result.status == "not_found":
            # A definite negative from the venue. The submission did not take effect.
            repo.resolve_stale_execution(
                execution_id, row["tenant_id"],
                status=ExecutionStatus.FAILED,
                classification=row["classification"],
                detail=(
                    "the venue was queried by clientOrderId and has no such order, so "
                    "the submission did not take effect"
                ),
            )
            return

        venue_order_id = result.venue_order_id
        if not venue_order_id:
            # The venue knows something but gave us no id. Not enough to record a fill.
            repo.resolve_stale_execution(
                execution_id, row["tenant_id"],
                status=ExecutionStatus.UNKNOWN,
                classification=row["classification"],
                detail=(
                    f"the venue reported status {result.status!r} but returned no order "
                    f"id, so a fill cannot be attributed"
                ),
            )
            await self._trigger_recovery_alert(
                row,
                f"Venue reports {result.status!r} for this clientOrderId but returned no "
                f"order id. Left UNKNOWN; NOT resubmitted.",
            )
            return

        if result.status in ("closed", "filled", "completed"):
            repo.resolve_stale_execution(
                execution_id, row["tenant_id"],
                status=ExecutionStatus.COMPLETED,
                classification=row["classification"],
                detail=f"recovered from the venue by clientOrderId ({result.status})",
                order_id=venue_order_id,
            )
            logger.critical(
                "TRADE-07: execution %s was FILLED at the venue and the platform did not "
                "know. Recovered as order %s. Position accounting for this fill must be "
                "reconciled.", execution_id, venue_order_id,
            )
            await self._trigger_recovery_alert(
                row,
                f"Order was filled at the venue ({venue_order_id}) while the platform "
                f"had no record of it. The execution is now COMPLETED; position and PnL "
                f"accounting for this fill require reconciliation.",
            )
            return

        if result.status in ("canceled", "cancelled", "expired", "rejected"):
            repo.resolve_stale_execution(
                execution_id, row["tenant_id"],
                status=ExecutionStatus.FAILED,
                classification=row["classification"],
                detail=f"the venue reports the order as {result.status}",
                order_id=venue_order_id,
            )
            return

        # Still open at the venue. This is live exposure the platform had lost track of.
        # The order id is now recorded, which moves the row into `_find_stale_orders`'
        # population so the ordinary reconciliation loop takes it from here.
        repo.resolve_stale_execution(
            execution_id, row["tenant_id"],
            status=ExecutionStatus.UNKNOWN,
            classification=row["classification"],
            detail=(
                f"the order is {result.status!r} at the venue; its id has been recorded "
                f"so the ordinary reconciliation path can follow it"
            ),
            order_id=venue_order_id,
        )
        await self._trigger_recovery_alert(
            row,
            f"Order is still {result.status!r} at the venue as {venue_order_id} and the "
            f"platform had no record of it. This is live exposure. Its id is now "
            f"recorded; it was NOT resubmitted and NOT cancelled.",
        )

    async def _trigger_recovery_alert(self, row: dict, reason: str):
        """Alert for an adjudicated stale execution.

        Separate from `_trigger_alert`, which takes an ORM record; this path works from
        the classification dict and must not require one.
        """
        self.alerts_triggered += 1
        logger.critical(
            "TRADE-07 RECOVERY ALERT: execution %s | symbol=%s | age=%.0fs | %s",
            row.get("execution_id"), row.get("symbol"),
            row.get("age_seconds") or 0, reason,
        )
        try:
            from backend_app.backend.alert_system import get_alert_system

            alert_system = get_alert_system()
            await alert_system.send_critical_alert(
                title="Stale Execution Requires Reconciliation",
                message=f"Execution {row.get('execution_id')}: {reason}",
                metadata={
                    "execution_id": row.get("execution_id"),
                    "symbol": row.get("symbol"),
                    "side": row.get("side"),
                    "size": row.get("size"),
                    "classification": row.get("classification"),
                    "submitted_at": (
                        row["submitted_at"].isoformat()
                        if row.get("submitted_at") else None
                    ),
                    "age_seconds": row.get("age_seconds"),
                },
            )
        except Exception:  # noqa: BLE001 - alerting must never decide execution state
            logger.warning("TRADE-07: recovery alert dispatch failed", exc_info=True)
    
    def _find_stale_orders(self) -> List[ExecutionRecordModel]:
        """
        Find orders that need status refresh.
        
        Criteria:
        - Status is SUBMITTED, PENDING, or PARTIALLY_FILLED
        - Last exchange sync is older than threshold
        """
        threshold = datetime.utcnow() - timedelta(
            seconds=self.config.stale_threshold_seconds
        )
        
        stale_statuses = [
            ExecutionStatus.PENDING,
            ExecutionStatus.EXECUTING
        ]
        
        orders = self.db.query(ExecutionRecordModel).filter(
            and_(
                ExecutionRecordModel.status.in_(stale_statuses),
                or_(
                    ExecutionRecordModel.last_exchange_sync < threshold,
                    ExecutionRecordModel.last_exchange_sync.is_(None)
                ),
                ExecutionRecordModel.order_id.isnot(None)  # Must have exchange order ID
            )
        ).all()
        
        return orders
    
    async def _refresh_order_status(self, order: ExecutionRecordModel):
        """Fetch and update order status from exchange."""
        logger.info(
            f"Refreshing order: {order.execution_id} | "
            f"exchange_order={order.order_id} | "
            f"last_sync={order.last_exchange_sync}"
        )
        
        # STEP 1: Get exchange executor for this order. RT-16: a real, per-tenant one.
        executor = await self._get_executor_for_order(order)
        
        # STEP 3: FAIL-SAFE - If no executor (credentials unavailable), mark as UNKNOWN
        if not executor:
            logger.error(
                f"STEP 3: FAIL-SAFE - No executor available for order: {order.execution_id}. "
                f"Credentials unavailable or exchange connection failed."
            )
            
            # Mark order as UNKNOWN - DO NOT ASSUME FILLED
            order.status = ExecutionStatus.UNKNOWN
            # `exchange_status`, not `exchange_status_message`. The latter is NOT a column
            # on ExecutionRecordModel -- the model declares `exchange_status` -- so this
            # assignment created a transient attribute on the instance and was silently
            # discarded at commit. Measured during the TRADE-07 work: the UNKNOWN status
            # persisted, but the reason an operator needs in order to act on it never did.
            order.exchange_status = "unknown: executor unavailable (credential/connection failure)"
            
            # Persist the UNKNOWN status.
            #
            # W-2. `_persist_order_update` had NO DEFINITION anywhere in the application,
            # and both call sites awaited it, so each raised AttributeError into
            # `_check_stale_orders`' handler. The two assignments above dirtied the ORM
            # instance on this watchdog's long-lived session and whether they ever
            # reached the database depended on an unrelated later commit.
            #
            # W-2b. The alert below read `order.user_id`, and `ExecutionRecordModel`
            # declares no such column -- so once persistence was fixed this branch would
            # have raised AttributeError inside the f-string instead. `tenant_id` is the
            # column that exists, and it is the same substitution
            # `_adjudicate_ambiguous_execution` already makes on its shim.
            persisted = self._persist_order_update(order)
            
            # TRIGGER ALERT
            await self._trigger_alert(
                order,
                f"CRITICAL: Cannot reconcile order {order.execution_id} - no executor available. "
                f"Verify exchange credentials for tenant {order.tenant_id}, exchange {order.exchange_id}."
                + ("" if persisted else _UNPERSISTED_SUFFIX)
            )
            return
        
        # Fetch status from exchange
        for attempt in range(self.config.max_retries):
            try:
                result: OrderStatusResult = await executor.get_order_status(
                    exchange_order_id=order.order_id,
                    symbol=order.symbol
                )
                
                if result.success:
                    await self._update_order_from_status(order, result)
                    self.orders_fixed += 1
                    return
                else:
                    logger.warning(
                        f"Status fetch failed (attempt {attempt+1}): "
                        f"{result.error_message}"
                    )
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    
            except Exception as e:
                logger.error(f"Error fetching status (attempt {attempt+1}): {e}")
                await asyncio.sleep(2 ** attempt)
        
        # All retries failed
        logger.error(
            f"STEP 3: FAIL-SAFE - All {self.config.max_retries} retry attempts exhausted. "
            f"Marking order as UNKNOWN: {order.execution_id}"
        )
        
        # Mark order as UNKNOWN - DO NOT ASSUME FILLED
        order.status = ExecutionStatus.UNKNOWN
        # Same column correction as the no-executor branch above: the model declares
        # `exchange_status`, so the `_message` variant never persisted.
        order.exchange_status = "unknown: exchange communication failed after retries"
        
        # Persist the UNKNOWN status to database. W-2, as above.
        persisted = self._persist_order_update(order)
        
        # STEP 3: TRIGGER ALERT - Critical failure requires human attention
        await self._trigger_alert(
            order,
            f"CRITICAL: Order status UNKNOWN after {self.config.max_retries} failed reconciliation attempts. "
            f"Order ID: {order.execution_id}. Manual intervention required."
            + ("" if persisted else _UNPERSISTED_SUFFIX)
        )
        
        # Check if order is stuck for too long (additional alert)
        time_since_creation = datetime.utcnow() - order.created_at
        if time_since_creation.total_seconds() > self.config.alert_threshold_seconds:
            await self._trigger_alert(
                order,
                f"URGENT: Order stuck for > {self.config.alert_threshold_seconds // 60} minutes: {order.execution_id}"
            )
    
    async def _update_order_from_status(
        self,
        order: ExecutionRecordModel,
        status: OrderStatusResult
    ):
        """Update order record from exchange status with idempotent position updates."""
        # Map exchange status to our status
        exchange_status = status.status.lower()
        
        # FIN-CRITICAL-002 FIX: Add row-level locking for position delta calculation
        # Calculate incremental fill delta BEFORE database commit with proper locking
        # This prevents race conditions where concurrent reconciliation workers could
        # both calculate non-zero deltas and apply them twice, causing position drift
        from decimal import Decimal
        from sqlalchemy import text
        
        # Lock the position row for this tenant/symbol combination to prevent concurrent updates
        position_lock_key = f"position_update:{order.tenant_id}:{order.symbol}"
        
        prev_filled_dec = Decimal(order.filled_size or "0")
        new_filled_dec = Decimal(status.filled_size or "0")
        fill_delta = max(Decimal("0"), new_filled_dec - prev_filled_dec)

        if exchange_status in ["filled", "closed", "completed"]:
            order.status = ExecutionStatus.COMPLETED
            order.filled_size = status.filled_size
            order.remaining_size = "0"
            order.avg_price = status.avg_price or order.avg_price
            
            logger.info(
                f"Order marked FILLED by watchdog: {order.execution_id} | "
                f"filled={status.filled_size} (delta={fill_delta})"
            )
            
            # FIN-CRITICAL-002 FIX: Update position with row-level locking
            if fill_delta > 0:
                # Acquire row-level lock on position before update
                try:
                    # Lock position row for this tenant/symbol to prevent concurrent updates.
                    # Rendered for the session's dialect -- see _position_lock_sql: the inline
                    # statement this replaces referenced a `symbol` column `positions` does
                    # not have, so PostgreSQL rejected it and the lock was never taken.
                    self.db.execute(
                        text(_position_lock_sql(_watchdog_session_dialect(self.db))),
                        {"tenant_id": order.tenant_id, "symbol": order.symbol}
                    )
                    logger.info(f"Position lock acquired for {order.tenant_id}:{order.symbol}")
                except Exception as lock_error:
                    logger.warning(f"Position lock failed for {order.tenant_id}:{order.symbol}: {lock_error}")
                    # Continue with update even if lock fails (non-blocking fallback).
                    # A failed statement leaves the transaction needing a rollback on
                    # PostgreSQL, so the session is reset before the unlocked update proceeds;
                    # without this the `_update_position` call and the `self.db.commit()`
                    # below both inherit the aborted transaction.
                    try:
                        self.db.rollback()
                    except Exception:  # pragma: no cover - rollback of a dead session
                        logger.warning("Position lock rollback failed", exc_info=True)
                
                await self._update_position(order, fill_delta=fill_delta)
            
        elif exchange_status in ["partially_filled", "partial"]:
            order.status = ExecutionStatus.EXECUTING
            order.filled_size = status.filled_size
            order.remaining_size = status.remaining_size
            order.avg_price = status.avg_price or order.avg_price
            
            logger.info(
                f"Order partial fill updated: {order.execution_id} | "
                f"filled={status.filled_size} (delta={fill_delta})"
            )
            
            # FIN-CRITICAL-002 FIX: Update position with row-level locking
            if fill_delta > 0:
                # Acquire row-level lock on position before update
                try:
                    # Same statement and same reasoning as the `filled` branch above.
                    self.db.execute(
                        text(_position_lock_sql(_watchdog_session_dialect(self.db))),
                        {"tenant_id": order.tenant_id, "symbol": order.symbol}
                    )
                    logger.info(f"Position lock acquired for {order.tenant_id}:{order.symbol}")
                except Exception as lock_error:
                    logger.warning(f"Position lock failed for {order.tenant_id}:{order.symbol}: {lock_error}")
                    # Continue with update even if lock fails (non-blocking fallback).
                    # Rollback first -- see the `filled` branch.
                    try:
                        self.db.rollback()
                    except Exception:  # pragma: no cover - rollback of a dead session
                        logger.warning("Position lock rollback failed", exc_info=True)
                
                await self._update_position(order, fill_delta=fill_delta)
            
        elif exchange_status in ["canceled", "cancelled", "expired"]:
            order.status = ExecutionStatus.FAILED
            
            logger.warning(
                f"Order marked CANCELLED by watchdog: {order.execution_id}"
            )
            
        elif exchange_status in ["rejected", "error"]:
            order.status = ExecutionStatus.FAILED
            
            logger.error(
                f"Order marked REJECTED by watchdog: {order.execution_id}"
            )
        
        # Update sync timestamp
        order.last_exchange_sync = datetime.utcnow()
        
        self.db.commit()
    
    def _persist_order_update(self, order: ExecutionRecordModel) -> bool:
        """Commit status/reason changes already assigned to *order*. Returns success.

        W-2. THIS METHOD DID NOT EXIST.
        -------------------------------
        `_refresh_order_status` called it twice -- on the "no executor" branch and on the
        "all retries exhausted" branch -- and a repository-wide search for
        `def _persist_order_update` returned ZERO definitions. `OrderWatchdog` has no base
        class, so both calls raised `AttributeError`, which `_check_stale_orders` swallows
        at its `except Exception` and logs as "Failed to refresh order".

        What that cost: the two branches assign `order.status = UNKNOWN` and
        `order.exchange_status = <reason>` immediately before calling this. Assigning an
        ORM attribute is NOT a commit -- it dirties the instance on this watchdog's
        long-lived session, and whether it ever reached the database depended on some
        later unrelated `self.db.commit()` elsewhere in the class. The UNKNOWN fail-safe
        the surrounding comments describe as "DO NOT ASSUME FILLED" was therefore not
        durable, and the operator-facing reason was the part most likely to be lost.

        NO NEW ABSTRACTION. `_update_order_from_status` in this same class already
        persists by calling `self.db.commit()` on the session the watchdog was
        constructed with, and `ExecutionRecordRepository` is not used by that path. This
        method does exactly what that one does, so there is one persistence convention in
        this class rather than two.

        SYNCHRONOUS, and the call sites do not await it. `Session.commit()` is a blocking
        call; declaring this `async` would have described work that does not exist and
        would have re-created the shape of W-1 in a second place.

        FAILURE IS RETURNED, NOT SWALLOWED. A failed commit leaves the transaction needing
        a rollback on PostgreSQL -- without one, every later statement on this session
        inherits the aborted transaction, which is how one failure becomes a dead
        watchdog. The rollback is taken, the failure is logged `critical`, and `False` is
        returned so the caller can say so in its alert (`_UNPERSISTED_SUFFIX`) instead of
        announcing a recovery that did not happen. The row is left in its previous status,
        which is the safe direction: it stays in the stale population and is re-examined.

        Regression: tests/test_d5_watchdog_adjudication_repair.py
        """
        execution_id = getattr(order, "execution_id", "unknown")
        try:
            self.db.commit()
            return True
        except Exception as exc:  # noqa: BLE001 - the caller must learn this failed
            try:
                self.db.rollback()
            except Exception:  # pragma: no cover - rollback of an already-dead session
                logger.warning(
                    "W-2: rollback after a failed persist also failed for %s",
                    execution_id, exc_info=True,
                )
            logger.critical(
                "W-2: could not persist the status change for execution %s (%s: %s). "
                "The row keeps its previous status and has NOT been recovered; it "
                "remains in the stale population and will be re-examined.",
                execution_id, exc.__class__.__name__, exc,
            )
            return False

    async def _update_position(self, order: ExecutionRecordModel, fill_delta: Optional[Any] = None):
        """Update position from order fill (idempotent with delta tracking)."""
        try:
            position_engine = get_position_engine(self.db)
            
            size_to_apply = str(fill_delta) if fill_delta is not None else order.filled_size
            if not size_to_apply or Decimal(size_to_apply) <= 0:
                return
                
            position = await position_engine.update_position_from_fill(
                execution_record=order,
                fill_size=size_to_apply,
                fill_price=order.avg_price
            )
            
            if position:
                logger.info(
                    f"Position updated by watchdog: {position.position_id} | delta={size_to_apply}"
                )
                
        except Exception as e:
            logger.error(f"Failed to update position from watchdog: {e}")
    
    async def _trigger_alert(self, order: ExecutionRecordModel, reason: str):
        """Trigger alert for stuck order."""
        self.alerts_triggered += 1
        
        logger.critical(
            f"WATCHDOG ALERT: Order {order.execution_id} | "
            f"symbol={order.symbol} | "
            f"reason={reason} | "
            f"created={order.created_at}"
        )
        
        # STEP 7.8: Send to alert system
        try:
            from backend_app.backend.alert_system import get_alert_system
            alert_system = get_alert_system()
            
            await alert_system.send_critical_alert(
                title="Order Stuck Alert",
                message=f"Order {order.execution_id} ({order.symbol}) is stuck: {reason}",
                metadata={
                    "execution_id": order.execution_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "size": order.size,
                    "created_at": order.created_at.isoformat(),
                    "reason": reason
                }
            )
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
    
    async def _get_executor_for_order(
        self,
        order: ExecutionRecordModel
    ) -> Optional[Any]:
        """Get a real exchange executor for this order's tenant, via the account vault.

        RT-16 / W-1b. THIS METHOD IS NOW ASYNC; EVERY CALL SITE MUST `await` IT.
        --------------------------------------------------------------------------
        Previously neither dependency this reached for existed (`app_state.exchange_router`
        was never set anywhere in the application; `vault.get_exchange_client` was never a
        method `APIKeyVault` defines) -- see W-1's own history, and git blame on this
        docstring for the full account of why it was deliberately left synchronous and
        returning ``None`` until an executor could legitimately be built.

        That became possible once RT-21's `order_control._executor_for` existed: a real,
        per-tenant executor built from the SAME decrypted keys the manual cancel path uses
        (`APIKeyVault.load_decrypted_keys` -> `get_exchange_executor` -> `.connect()`).
        Reused here rather than re-implemented, so there is exactly one place that builds an
        executor from a tenant's stored keys, not two that could drift.

        Still returns ``None`` -- not an exception -- for every unreachable case the callers
        already treat as "cannot ask" (no stored keys for this tenant+exchange, vault
        unavailable, connect failure), which is the existing fail-safe behaviour at all
        three call sites: no executor means UNKNOWN, with an alert, never a resubmission.

        Regression: tests/test_d5_watchdog_adjudication_repair.py,
        tests/test_rt16_watchdog_executor_is_real.py
        """
        try:
            exchange_id = getattr(order, "exchange_id", None) or "binance"
            tenant_id = getattr(order, "tenant_id", None)
            if not tenant_id:
                return None
            from backend_app.core.state import app_state
            vault = getattr(app_state, "vault", None)
            if vault is None:
                return None
            from backend_app.backend.order_control import _executor_for
            return await _executor_for(str(tenant_id), str(exchange_id), vault)
        except Exception as e:
            logger.warning(f"Could not route executor for order {getattr(order, 'execution_id', 'unknown')}: {e}")
            return None
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get watchdog metrics."""
        return {
            "checks_run": self.checks_run,
            "orders_fixed": self.orders_fixed,
            "alerts_triggered": self.alerts_triggered,
            "running": self._running,
            "config": {
                "check_interval": self.config.check_interval_seconds,
                "stale_threshold": self.config.stale_threshold_seconds,
                "alert_threshold": self.config.alert_threshold_seconds
            }
        }


# Global instance
_watchdog: Optional[OrderWatchdog] = None


def get_order_watchdog(
    db_session: Session,
    config: Optional[WatchdogConfig] = None
) -> OrderWatchdog:
    """Get or create global watchdog."""
    global _watchdog
    if _watchdog is None:
        _watchdog = OrderWatchdog(db_session, config)
    return _watchdog
