"""
Exchange Reconciliation Service

STEP 3.4 — ORDER CONSISTENCY + EXCHANGE RECONCILIATION

Detects divergence between this platform's record of open orders and the venue's.

Reconciliation Loop:
┌─────────────────────────────────────────────────────────────────┐
│  Every 5-10 seconds                                              │
│                                                                  │
│  1. Fetch open orders from exchange                               │
│  2. Fetch open orders from DB                                    │
│  3. Compare and reconcile:                                        │
│                                                                  │
│     Exchange has order, DB missing:                               │
│     → REPORT it. No row is created. See below.                    │
│                                                                  │
│     DB has order, exchange missing:                             │
│     → Mark FILLED or CANCELLED (reconciliation)                   │
│                                                                  │
│     Both have order but mismatch:                                 │
│     → Update filled_size, avg_price, status                       │
│                                                                  │
│  4. Log all discrepancies                                         │
└─────────────────────────────────────────────────────────────────┘

THE FIRST CASE REPORTS AND DOES NOT WRITE, WHICH IS RECON-01'S ANSWER
---------------------------------------------------------------------
This module used to INSERT an ``execution_records`` row for a venue order it could not
attribute -- ``strategy_id="reconciled"``, ``price=None``, ``status=EXECUTING``, no risk
check -- and RECON-01 held that open as the one owner's policy decision left in it. The
answer is NO: ``execution_records`` is the strategy-execution ledger, a row in it means
"this platform executed this", and that is the one claim the platform is not entitled to
make about an order it cannot explain. A reconciler detects divergence between two systems
of record; it does not resolve it by editing one of them to agree with the other.

The exposure is real either way, so the order is reported at ``error`` with every field
the venue gave. ``_report_unattributed_order`` carries the full reasoning.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from backend_app.backend.order_watchdog import venue_identifier
from backend_app.core.models.execution_record import (ExecutionRecordModel,
                                                      ExecutionStatus)
from backend_app.core.order_state_machine import (OrderStateMachine,
                                                  get_order_state_machine,
                                                  transition_to_cancelled,
                                                  transition_to_filled,
                                                  transition_to_partial_fill)

logger = logging.getLogger(__name__)

#: RECON-01. The Redis key naming this subsystem's leader lease.
#:
#: DISTINCT from ``core.reconciliation_scheduler.LEADER_LOCK_KEY`` on purpose. These are
#: two different subsystems doing different work (this one talks to the venue; that one
#: escalates rows already recorded as mismatched), and sharing one key would make
#: whichever process won suppress the OTHER loop entirely -- which presents as "the loop
#: is running" while half the work silently never happens.
RECONCILIATION_LEADER_LOCK_KEY = "vyomquant:leader:exchange_reconciliation"

#: Lease duration. MUST exceed ``start_reconciliation_loop``'s own ``interval_seconds``
#: (default 5), or the holder would keep losing a lease it is still actively using and
#: leadership would churn every cycle. 60s gives the default interval twelve cycles of
#: headroom while still bounding how long a crashed leader's hold blocks a successor --
#: the lease expires on its own, so a dead worker cannot deadlock the subsystem.
RECONCILIATION_LEADER_LOCK_TTL_SECONDS = 60


class ReconciliationAction(Enum):
    """Actions from reconciliation process.

    ``INSERT_GHOST_ORDER`` WAS HERE AND IS GONE. RECON-01's one open question was whether
    the platform should create an ``execution_records`` row for a venue order it cannot
    attribute to any strategy. The answer is NO, and the member is removed rather than
    left unused so nothing can emit it again: an enum value that names a forbidden write
    is an invitation. :data:`REPORT_UNATTRIBUTED_ORDER` replaces it and is report-only.
    """

    #: A venue order with no ``execution_records`` row. REPORTED, never written.
    REPORT_UNATTRIBUTED_ORDER = "report_unattributed_order"
    MARK_FILLED = "mark_filled"
    MARK_CANCELLED = "mark_cancelled"
    UPDATE_FILL_DATA = "update_fill_data"
    NO_ACTION = "no_action"


@dataclass
class ReconciliationResult:
    """Result of a reconciliation operation.

    ``execution_id`` is OPTIONAL, and ``None`` is the meaningful case: a
    :data:`ReconciliationAction.REPORT_UNATTRIBUTED_ORDER` result describes a venue order
    that has NO execution record, so there is no execution id to give. Returning a
    fabricated one -- the ``exec_ghost_<venue id>`` string this module used to mint -- is
    how a reader ends up believing a row exists. Consumers must handle ``None``;
    ``_update_sync_timestamp`` filters it out rather than touching a row by a made-up key.
    """

    execution_id: Optional[str]
    action: ReconciliationAction
    old_state: Optional[str]
    new_state: Optional[str]
    exchange_order_id: Optional[str]
    filled_size: Optional[float]
    avg_price: Optional[float]
    discrepancy: Optional[str] = None


class ExchangeReconciliationService:
    """
    Service for reconciling orders between system and exchange.
    
    Runs continuously in background to ensure consistency.
    
    Usage:
        service = ExchangeReconciliationService(db_session, exchange_client)
        
        # Start reconciliation loop
        await service.start_reconciliation_loop(
            tenant_id="tenant-123",
            interval_seconds=5
        )
        
        # Or run single reconciliation
        results = await service.reconcile_open_orders(tenant_id="tenant-123")
    """
    
    def __init__(
        self,
        db_session: Session,
        exchange_client: Optional[Any] = None,
        state_machine: Optional[OrderStateMachine] = None,
        vault: Optional[Any] = None,
    ):
        """
        Initialize reconciliation service.

        Args:
            db_session: SQLAlchemy session for DB operations
            exchange_client: A single, already-connected exchange client shared across
                every call. Correct only when this instance is used for exactly one
                tenant and one exchange account -- RECON-01's own named defect is that
                nothing in this service enforced that, so a caller could pass a
                tenant_id the client does not actually belong to and reconcile one
                tenant's orders against another's exchange account. Kept for backward
                compatibility with existing callers/tests that already pass one.
            state_machine: Order state machine instance
            vault: An ``APIKeyVault``-shaped object (``load_decrypted_keys(user_id,
                exchange_id)``). When provided, ``_fetch_exchange_orders`` resolves a
                FRESH, per-(tenant_id, exchange_id) executor for each call via the same
                ``order_control._executor_for`` helper RT-16's OrderWatchdog fix and
                RT-21's manual cancel both already use, instead of trusting
                ``exchange_client`` to belong to whichever tenant_id is passed in.
                Takes precedence over ``exchange_client`` when set.
        """
        self.db = db_session
        self.exchange = exchange_client
        self.vault = vault
        self.state_machine = state_machine or get_order_state_machine()
        self._running = False
        self._reconciliation_task: Optional[asyncio.Task] = None

        #: RECON-01. One id per instance, stable for its whole lifetime and distinct from
        #: every other worker process's -- exactly the identity a renewable lease needs to
        #: tell "still me, renew" apart from "someone else, back off".
        self._instance_id = str(uuid.uuid4())

        logger.info("ExchangeReconciliationService initialized")

    # ── Leader election (RECON-01) ──────────────────────────────────────────────

    async def _try_become_leader(self) -> bool:
        """``True`` iff THIS process may run a reconciliation cycle right now.

        RECON-01's first named blocker: ``gunicorn`` runs ``--workers ${WORKERS:-2}`` and
        nothing stopped N worker processes from each running
        ``start_reconciliation_loop`` against the SAME rows and the same venue
        concurrently -- every one of them cancelling, re-querying and transitioning
        orders the others were already working on.

        Delegates to the shared primitive in ``core.leader_lease``, which is the same
        renewable-lease implementation INF-04 proved for ``ReconciliationScheduler``;
        this finding's own ledger entry named that one as "the template to reuse, not yet
        applied here". It is reused rather than copied, so the two cannot drift.

        Its own key, deliberately: sharing one lock with ``ReconciliationScheduler``
        would make each subsystem's leader silently suppress the other entirely, which
        looks exactly like "the loop is running" while half the work never happens.

        Fails CLOSED -- no reachable Redis means no leadership and no cycle.
        """
        from backend_app.core.leader_lease import try_acquire_or_renew

        return await try_acquire_or_renew(
            lock_key=RECONCILIATION_LEADER_LOCK_KEY,
            instance_id=self._instance_id,
            ttl_seconds=RECONCILIATION_LEADER_LOCK_TTL_SECONDS,
            subsystem="ExchangeReconciliationService",
        )
    
    async def reconcile_open_orders(
        self,
        tenant_id: str,
        exchange_id: Optional[str] = None
    ) -> List[ReconciliationResult]:
        """
        STEP 3.4: Reconcile open orders between system and exchange.
        
        1. Fetch open orders from exchange
        2. Fetch open orders from DB
        3. Compare and resolve discrepancies
        
        Args:
            tenant_id: Tenant UUID
            exchange_id: Optional exchange identifier for filtering
            
        Returns:
            List of reconciliation results
        """
        logger.info(f"Starting reconciliation for tenant {tenant_id}")
        results: List[ReconciliationResult] = []
        
        try:
            # 1. Fetch orders from exchange
            exchange_orders = await self._fetch_exchange_orders(tenant_id, exchange_id)
            logger.debug(f"Fetched {len(exchange_orders)} orders from exchange")
            
            # 2. Fetch active orders from DB
            db_orders = self._fetch_db_active_orders(tenant_id, exchange_id)
            logger.debug(f"Fetched {len(db_orders)} active orders from DB")
            
            # Build lookup maps.
            #
            # RECON-01 / RT-16 / TRADE-12: both sides of this comparison must be keyed by
            # the id the VENUE assigned, via `venue_identifier`. They were not. The
            # exchange side is keyed by the venue's own id (correct, it has no other),
            # while every DB-side lookup below used `db_order.order_id` alone -- and
            # TRADE-12 moved the venue's id into `venue_order_id`, leaving `order_id` NULL
            # on every row the live engine creates. `_fetch_db_active_orders` applies no
            # identifier filter, so those rows ARE in `db_orders`.
            #
            # The consequence was not a missed check but a FABRICATED one, in both
            # directions at once, for the same real order:
            #   * CASE 1 compared the venue's id against a NULL `order_id`, found no
            #     match, and so classified the tenant's own genuine open order as a ghost
            #     -- `_handle_ghost_order` then INSERTs and COMMITs a second
            #     `execution_records` row (`exec_ghost_<venue id>`, status EXECUTING,
            #     `strategy_id="reconciled"`, `price=None`) duplicating a row that was
            #     already there under its real execution_id;
            #   * CASE 2 looked the same order up by NULL, missed it, and routed it to
            #     `_handle_missing_exchange_order` as absent from the venue.
            # Closing this entry's enumeration blocker is what ARMED that: enumeration was
            # taught to accept `venue_order_id` rows (see `enumerate_reconcilable_pairs`)
            # while the matching below was not, so the fix widened the population to
            # exactly the rows that mismatch. Nothing tested the ghost path.
            exchange_orders_by_id = {o['order_id']: o for o in exchange_orders}
            
            # 3. Compare and reconcile
            
            # CASE 1: the venue has an order with no execution_records row → REPORT it.
            # It used to be INSERTed. RECON-01's open question was whether the platform
            # should create a row for an order it cannot attribute to a strategy; the
            # answer is no, and `_report_unattributed_order` carries the reasoning.
            for order_id, exchange_order in exchange_orders_by_id.items():
                matching_db_order = self._find_db_order_by_exchange_id(db_orders, order_id)
                
                if not matching_db_order:
                    result = await self._report_unattributed_order(
                        tenant_id, exchange_order
                    )
                    results.append(result)
            
            # CASE 2 & 3: DB has order, check against exchange
            for db_order in db_orders:
                exchange_order = exchange_orders_by_id.get(venue_identifier(db_order))
                
                if not exchange_order:
                    # Exchange doesn't have order → FILLED or CANCELLED
                    result = await self._handle_missing_exchange_order(db_order)
                    results.append(result)
                else:
                    # Both have order → Check for fill data mismatch
                    result = await self._handle_fill_mismatch(db_order, exchange_order)
                    if result.action != ReconciliationAction.NO_ACTION:
                        results.append(result)
            
            # Update last sync timestamp for all reconciled orders.
            # `None` is filtered: a REPORT_UNATTRIBUTED_ORDER result has no execution id
            # because it has no row, and stamping "last synced" on a key that names
            # nothing would be a write derived from an absence.
            self._update_sync_timestamp(
                [r.execution_id for r in results if r.execution_id]
            )
            
            logger.info(
                f"Reconciliation complete: {len(results)} actions, "
                f"{len([r for r in results if r.action != ReconciliationAction.NO_ACTION])} discrepancies"
            )
            
        except Exception as e:
            logger.error(f"Reconciliation failed: {e}", exc_info=True)
            raise
        
        return results
    
    async def _fetch_exchange_orders(
        self,
        tenant_id: str,
        exchange_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch open orders from exchange API.
        
        Returns list of orders with:
        - order_id: Exchange order ID
        - symbol: Trading symbol
        - side: buy/sell
        - size: Total order size
        - filled_size: Amount filled
        - remaining_size: Amount remaining
        - avg_price: Average fill price
        - status: Exchange status
        """
        # Phase 10 / WS-D. This used to `return []`, which is the SAME defect BUG-FIX
        # UK-01 (below) fixed for the exception path and left standing here.
        #
        # An empty list does not mean "no exchange client". It means "the venue reports
        # no open orders", and `reconcile_open_orders` acts on that: every PENDING or
        # EXECUTING row in the database is then treated as absent from the exchange and
        # handed to `_handle_missing_exchange_order`, which resolves it. So a service
        # constructed without a client -- which is exactly what
        # `get_reconciliation_service(db)` produces, since `exchange_client` defaults to
        # None -- would resolve EVERY active order on its first pass, having observed
        # nothing at all.
        #
        # "I cannot see the exchange" and "the exchange is empty" must not be the same
        # value, so this refuses instead. Regression:
        # tests/test_p10d_exchange_reconciliation_fails_safely.py
        #
        # RECON-01: when a vault is configured, this resolves a FRESH executor scoped
        # to THIS tenant_id and THIS exchange_id on every call, rather than trusting
        # self.exchange (set once at construction) to belong to whichever tenant_id a
        # caller happens to pass in -- the defect this finding named ("architected for
        # exactly one tenant_id and one exchange_client per instance"). Same helper
        # RT-16's OrderWatchdog fix and RT-21's manual cancel already use.
        if self.vault is not None:
            if not exchange_id:
                raise RuntimeError(
                    "cannot resolve a per-tenant exchange executor without an "
                    "exchange_id: reconcile_open_orders was called with vault "
                    "configured but exchange_id=None."
                )
            from backend_app.backend.order_control import _executor_for
            exchange = await _executor_for(str(tenant_id), str(exchange_id), self.vault)
        elif not self.exchange:
            raise RuntimeError(
                "cannot reconcile: no exchange client is configured on this "
                "ExchangeReconciliationService. Refusing to report zero open orders, "
                "which would be read as every active order having left the venue."
            )
        else:
            exchange = self.exchange

        try:
            # This would call the actual exchange API
            # Format depends on exchange (ccxt, custom, etc.)
            orders = await exchange.fetch_open_orders()

            # Normalize to common format
            normalized = []
            for order in orders:
                normalized.append({
                    'order_id': order.get('id'),
                    'symbol': order.get('symbol', '').upper(),
                    'side': order.get('side', '').lower(),
                    'size': float(order.get('amount', 0)),
                    'filled_size': float(order.get('filled', 0)),
                    'remaining_size': float(order.get('remaining', 0)),
                    'avg_price': float(order.get('average', 0) or order.get('price', 0)),
                    'status': order.get('status', 'open'),
                    'raw': order  # Keep raw data for reference
                })
            
            return normalized
            
        except Exception as e:
            # BUG-FIX UK-01: NEVER return [] on exchange API error.
            # An empty list causes reconcile_open_orders() to treat every DB-active
            # order as absent from the exchange and cancel all of them.
            # Transient errors (timeout, 429, 500) must propagate so the caller aborts.
            logger.error(f"Failed to fetch exchange orders: {e}")
            raise
    
    def _fetch_db_active_orders(
        self,
        tenant_id: str,
        exchange_id: Optional[str] = None
    ) -> List[ExecutionRecordModel]:
        """Fetch active orders from DB (not in terminal state)."""
        query = self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.tenant_id == UUID(tenant_id),
            ExecutionRecordModel.status.in_([
                ExecutionStatus.PENDING.value,
                ExecutionStatus.EXECUTING.value
            ])
        )
        
        if exchange_id:
            query = query.filter(ExecutionRecordModel.exchange_id == exchange_id)
        
        return query.all()
    
    def _find_db_order_by_exchange_id(
        self,
        db_orders: List[ExecutionRecordModel],
        exchange_order_id: str
    ) -> Optional[ExecutionRecordModel]:
        """Find the DB order the venue knows by ``exchange_order_id``.

        Matched via ``venue_identifier`` (``venue_order_id``, then ``order_id``) rather
        than ``order_id`` alone. This is the deciding comparison in CASE 1: a row that
        fails to match here is declared a ghost and a duplicate ``execution_records`` row
        is inserted for it. Keying on ``order_id`` alone therefore did not merely miss
        engine-created rows, it manufactured a duplicate for each one -- see the reasoning
        recorded at the lookup-map construction in ``reconcile_open_orders``.
        """
        if not exchange_order_id:
            # Never match on a null id: an exchange order with no id must not be paired
            # with whichever DB row also happens to have none.
            return None
        for order in db_orders:
            if venue_identifier(order) == exchange_order_id:
                return order
        return None
    
    async def _report_unattributed_order(
        self,
        tenant_id: str,
        exchange_order: Dict[str, Any]
    ) -> ReconciliationResult:
        """CASE 1: the venue has an order this platform has no record of. REPORT it.

        RECON-01'S ONE OPEN QUESTION, ANSWERED: NO
        ------------------------------------------
        The question was whether the platform should create an ``execution_records`` row
        for a venue order it cannot attribute to any strategy. It must not, and this
        function used to do exactly that -- ``db.add()`` + ``db.commit()`` of a row with
        ``strategy_id="reconciled"``, ``price=None``, ``status=EXECUTING``, and no risk
        check -- on a live-money path.

        Every field of that row was a false statement:

        * ``strategy_id="reconciled"`` names no strategy. Anything that groups exposure or
          performance by strategy gained a phantom one, and nothing could ever answer
          "which strategy authorised this".
        * ``price=None`` on an ``EXECUTING`` record leaves every downstream P&L and risk
          computation without the one input it needs.
        * ``status=EXECUTING`` asserts that this platform is executing the order. It is
          not; the order arrived from somewhere this platform cannot see.
        * No risk check ran, so the row contributed to exposure without ever having been
          authorised against a limit -- the one thing the risk engine exists to prevent.

        And ``execution_records`` is the strategy-execution ledger: a row in it means
        "this platform executed this". For an order the platform cannot explain, that is
        the one claim it is not entitled to make. A reconciler's job is to detect
        divergence between two systems of record, not to resolve it by editing one of them
        to agree with the other.

        WHAT "NO" MUST NOT MEAN
        -----------------------
        It must not mean ignoring it. An unattributable open order at the venue is REAL
        exposure on a real account -- plausibly a manual trade in the exchange UI, or this
        platform's own order whose database write failed after the venue accepted it. The
        second case is dangerous and the two are indistinguishable from here. So this
        reports at ``error`` with every field the venue gave, which is the honest answer:
        the platform has seen something it cannot account for, and says so, without
        inventing a record of having done it.

        Writes NOTHING: no row, and no state-machine entry either. The old code also
        injected ``OrderState.SUBMITTED`` under the fabricated ``exec_ghost_<id>`` key,
        which left the in-memory machine holding a state for an execution that did not
        exist even after the row was rolled back.
        """
        venue_order_id = exchange_order.get('order_id')
        logger.error(
            "UNATTRIBUTED VENUE ORDER (RECON-01): tenant=%s venue_order_id=%s "
            "symbol=%s side=%s size=%s filled=%s avg_price=%s venue_status=%s. "
            "The venue reports this order and this platform holds no execution_records "
            "row for it. NO ROW HAS BEEN CREATED: the platform does not invent an "
            "execution it cannot attribute to a strategy or check against a risk limit. "
            "This is REAL exposure on a real account and needs a human -- either a manual "
            "trade placed outside the platform, or a platform order whose database write "
            "failed after the venue accepted it, and the two are not distinguishable from "
            "here.",
            tenant_id, venue_order_id, exchange_order.get('symbol'),
            exchange_order.get('side'), exchange_order.get('size'),
            exchange_order.get('filled_size'), exchange_order.get('avg_price'),
            exchange_order.get('status'),
        )

        return ReconciliationResult(
            # No execution exists, so there is no execution id. See the dataclass note.
            execution_id=None,
            action=ReconciliationAction.REPORT_UNATTRIBUTED_ORDER,
            old_state=None,
            new_state=None,
            exchange_order_id=venue_order_id,
            filled_size=exchange_order.get('filled_size'),
            avg_price=exchange_order.get('avg_price'),
            discrepancy=(
                "venue holds an open order with no execution_records row; reported, not "
                "written, because the platform cannot attribute it to a strategy"
            ),
        )
    
    async def _handle_missing_exchange_order(
        self,
        db_order: ExecutionRecordModel
    ) -> ReconciliationResult:
        """
        CASE 2: DB has order, missing from open_orders → Query specific order or reconcile.
        
        Order no longer exists in open_orders list. Could be:
        - FILLED (instant market fill or completed limit)
        - CANCELLED (user or system cancelled)
        - EXPIRED/REJECTED
        """
        # BUG-FIX ORD-03: Query authoritative order status from exchange first if supported.
        # RECON-01 / RT-16 / TRADE-12: ask about the id the VENUE assigned. Gating on
        # `db_order.order_id` skipped this authoritative check for every engine-created row
        # (`order_id` NULL, venue id in `venue_order_id`) and so fell through to deciding
        # the order's fate from its absence alone -- the precise situation the rest of this
        # function refuses to act on. With the venue id resolved, the row that most needs a
        # definitive answer is the one that now gets asked.
        venue_id = venue_identifier(db_order)
        if self.exchange and hasattr(self.exchange, "fetch_order") and venue_id:
            try:
                raw_order = await self.exchange.fetch_order(venue_id, db_order.symbol)
                if isinstance(raw_order, dict):
                    raw_status = str(raw_order.get("status", "")).lower()
                    filled_val = float(raw_order.get("filled", 0) or 0)
                    avg_p = float(raw_order.get("average", 0) or raw_order.get("price", 0) or 0)
                    
                    if raw_status in ["closed", "filled"] or filled_val >= float(db_order.size or 0) * 0.99999:
                        transition_to_filled(
                            execution_id=db_order.execution_id,
                            filled_size=filled_val,
                            avg_price=avg_p,
                            exchange_order_id=venue_id,
                            reason="Reconciled via fetch_order: Order confirmed filled on exchange"
                        )
                        db_order.status = ExecutionStatus.COMPLETED
                        db_order.filled_size = str(filled_val)
                        db_order.avg_price = str(avg_p)
                        db_order.filled_at = datetime.utcnow()
                        self.db.commit()
                        
                        return ReconciliationResult(
                            execution_id=db_order.execution_id,
                            action=ReconciliationAction.MARK_FILLED,
                            old_state=db_order.status,
                            new_state="filled",
                            exchange_order_id=venue_id,
                            filled_size=filled_val,
                            avg_price=avg_p,
                            discrepancy="Order closed on exchange (verified via fetch_order)"
                        )
                    elif raw_status in ["canceled", "cancelled", "expired"]:
                        transition_to_cancelled(
                            execution_id=db_order.execution_id,
                            cancelled_by="exchange",
                            reason="Reconciled via fetch_order: Order cancelled on exchange"
                        )
                        db_order.status = ExecutionStatus.FAILED
                        self.db.commit()
                        
                        return ReconciliationResult(
                            execution_id=db_order.execution_id,
                            action=ReconciliationAction.MARK_CANCELLED,
                            old_state=db_order.status,
                            new_state="cancelled",
                            exchange_order_id=venue_id,
                            filled_size=filled_val,
                            avg_price=avg_p if avg_p > 0 else None,
                            discrepancy="Order cancelled on exchange (verified via fetch_order)"
                        )
                    else:
                        # Unrecognized or missing status in response -> preserve state for retry
                        logger.warning(f"Unrecognized status '{raw_status}' from fetch_order for {db_order.execution_id}; preserving state")
                        return ReconciliationResult(
                            execution_id=db_order.execution_id,
                            action=ReconciliationAction.NO_ACTION,
                            old_state=db_order.status,
                            new_state=db_order.status,
                            exchange_order_id=venue_id,
                            filled_size=float(db_order.filled_size or 0),
                            avg_price=float(db_order.avg_price or 0) if db_order.avg_price else None,
                            discrepancy=f"Unrecognized status '{raw_status}' from fetch_order; state preserved for retry"
                        )
                else:
                    # fetch_order returned non-dict (e.g. None) -> preserve state for retry
                    logger.warning(f"fetch_order returned non-dict response for {db_order.execution_id}; preserving state")
                    return ReconciliationResult(
                        execution_id=db_order.execution_id,
                        action=ReconciliationAction.NO_ACTION,
                        old_state=db_order.status,
                        new_state=db_order.status,
                        exchange_order_id=venue_id,
                        filled_size=float(db_order.filled_size or 0),
                        avg_price=float(db_order.avg_price or 0) if db_order.avg_price else None,
                        discrepancy="fetch_order returned non-dict response; state preserved for retry"
                    )
            except Exception as e:
                # BUG-FIX REC-01: Network timeout or transient exchange error must NOT destroy local state
                logger.warning(f"Failed to fetch individual order status for {db_order.execution_id} due to transient error: {e}")
                return ReconciliationResult(
                    execution_id=db_order.execution_id,
                    action=ReconciliationAction.NO_ACTION,
                    old_state=db_order.status,
                    new_state=db_order.status,
                    exchange_order_id=venue_id,
                    filled_size=float(db_order.filled_size or 0),
                    avg_price=float(db_order.avg_price or 0) if db_order.avg_price else None,
                    discrepancy=f"Exchange status uncertain due to transient error ({e}); state preserved for next reconciliation cycle"
                )

        # ═══════════════════════════════════════════════════════════════════
        # NO `fetch_order` MEANS NO EVIDENCE, SO NO OUTCOME IS WRITTEN
        # ═══════════════════════════════════════════════════════════════════
        # Phase 10 / WS-D. This fallback used to decide the order's fate from its
        # ABSENCE from the open-orders list alone:
        #
        #     filled_size > 0  ->  transition_to_filled(),  status = COMPLETED
        #     otherwise        ->  transition_to_cancelled(), status = FAILED
        #
        # commented "Assume FILLED (conservative - better than leaving hanging)". It is
        # not conservative. COMPLETED asserts that a trade settled at a particular size
        # and price, and the only figures available here are the ones the database
        # already held BEFORE the order left the list -- so a partially filled order that
        # went on to fill completely is recorded at its stale partial size, and an order
        # still live on the venue under a different query is recorded as FAILED. Both are
        # terminal states with no route back, and they feed PnL.
        #
        # It also contradicts the two fixes directly above it in this same function:
        # BUG-FIX ORD-03 asks the venue before resolving, and BUG-FIX REC-01 preserves
        # state whenever the venue's answer is uncertain. An absent `fetch_order` is the
        # MOST uncertain case of all -- the venue was never asked and cannot be.
        #
        # So the row is left exactly as it is and reported as a discrepancy. A stuck
        # EXECUTING row is visible, queryable and recoverable: `OrderWatchdog`'s
        # `classify_stale_executions` / `recover_unsubmitted_executions` (TRADE-07) exist
        # for precisely this population. A fabricated COMPLETED is none of those things.
        # Regression: tests/test_p10d_exchange_reconciliation_fails_safely.py
        filled_size = float(db_order.filled_size or 0)

        logger.warning(
            "RECONCILIATION WITHHELD: %s is absent from the exchange's open orders, but "
            "this exchange client exposes no fetch_order, so its outcome cannot be "
            "confirmed. State preserved (status=%s, filled_size=%s). The order is left "
            "for the stale-order recovery path rather than resolved on an assumption.",
            db_order.execution_id, db_order.status, filled_size,
        )

        return ReconciliationResult(
            execution_id=db_order.execution_id,
            action=ReconciliationAction.NO_ACTION,
            old_state=db_order.status,
            new_state=db_order.status,
            exchange_order_id=venue_id,
            filled_size=filled_size,
            avg_price=float(db_order.avg_price) if db_order.avg_price else None,
            discrepancy=(
                "Order absent from exchange open orders and no fetch_order available to "
                "confirm its outcome; state preserved for stale-order recovery"
            ),
        )
    
    async def _handle_fill_mismatch(
        self,
        db_order: ExecutionRecordModel,
        exchange_order: Dict[str, Any]
    ) -> ReconciliationResult:
        """
        CASE 3: Both have order → Update fill data if mismatch.

        RECON-01 / RT-16 / TRADE-12: `exchange_order_id` reports the id the VENUE
        assigned, resolved via `venue_identifier`. It read `db_order.order_id`, which is
        this platform's own order id (it carries the FK to `orders(id)`) and is NULL on
        every row the live engine wrote -- so the field named for the exchange's id
        reported either the wrong system's id or nothing at all.
        """
        venue_id = venue_identifier(db_order)
        db_filled = float(db_order.filled_size or 0)
        exchange_filled = exchange_order['filled_size']
        
        # Check for mismatch
        if abs(db_filled - exchange_filled) > 1e-9:  # Allow for float precision
            # Update DB with exchange data (exchange is source of truth)
            db_order.filled_size = str(exchange_filled)
            db_order.remaining_size = str(exchange_order['remaining_size'])
            db_order.avg_price = str(exchange_order['avg_price']) if exchange_order['avg_price'] else None
            db_order.exchange_status = exchange_order['status']
            
            # Check if fully filled
            total_size = float(db_order.size or 0)
            if exchange_filled >= total_size * 0.999999:  # Allow for rounding
                # Transition to FILLED
                transition_to_filled(
                    execution_id=db_order.execution_id,
                    filled_size=exchange_filled,
                    avg_price=exchange_order['avg_price'],
                    exchange_order_id=venue_id or "unknown",
                    reason="Reconciled: Order fully filled on exchange"
                )
                db_order.status = ExecutionStatus.COMPLETED
                db_order.filled_at = datetime.utcnow()
            else:
                # Transition to PARTIALLY_FILLED
                transition_to_partial_fill(
                    execution_id=db_order.execution_id,
                    filled_size=exchange_filled,
                    remaining_size=exchange_order['remaining_size'],
                    avg_price=exchange_order['avg_price'],
                    exchange_order_id=venue_id or "unknown",
                    reason="Reconciled: Updated fill data from exchange"
                )
            
            self.db.commit()
            
            return ReconciliationResult(
                execution_id=db_order.execution_id,
                action=ReconciliationAction.UPDATE_FILL_DATA,
                old_state=f"filled={db_filled}",
                new_state=f"filled={exchange_filled}",
                exchange_order_id=venue_id,
                filled_size=exchange_filled,
                avg_price=exchange_order['avg_price'],
                discrepancy=f"Fill mismatch: DB={db_filled}, Exchange={exchange_filled}"
            )
        
        # No mismatch
        return ReconciliationResult(
            execution_id=db_order.execution_id,
            action=ReconciliationAction.NO_ACTION,
            old_state=None,
            new_state=None,
            exchange_order_id=venue_id,
            filled_size=exchange_filled,
            avg_price=exchange_order['avg_price']
        )
    
    def _update_sync_timestamp(self, execution_ids: List[Optional[str]]):
        """Update last_exchange_sync timestamp for reconciled orders.

        Defensive against ``None`` as well as the caller filtering it: a
        ``REPORT_UNATTRIBUTED_ORDER`` result carries no execution id, and
        ``execution_id = ANY(ARRAY[NULL])`` would match nothing while still committing a
        transaction for no reason.
        """
        execution_ids = [eid for eid in execution_ids if eid]
        if not execution_ids:
            return
        
        self.db.execute(
            text("""
                UPDATE execution_records
                SET last_exchange_sync = CURRENT_TIMESTAMP
                WHERE execution_id = ANY(:execution_ids)
            """),
            {"execution_ids": execution_ids}
        )
        self.db.commit()
    
    async def start_reconciliation_loop(
        self,
        tenant_id: str,
        interval_seconds: int = 5,
        exchange_id: Optional[str] = None
    ):
        """
        STEP 3.5: Start continuous reconciliation loop.
        
        Runs every `interval_seconds` to keep system in sync with exchange.
        
        Args:
            tenant_id: Tenant to reconcile
            interval_seconds: Seconds between reconciliation runs (default: 5)
            exchange_id: Optional exchange filter
        """
        self._running = True
        logger.info(
            f"Starting reconciliation loop for tenant {tenant_id} "
            f"(interval={interval_seconds}s)"
        )
        
        while self._running:
            try:
                start_time = datetime.utcnow()

                # RECON-01. Exactly one process per deployment may reconcile. Without
                # this, every gunicorn worker ran this loop against the same rows and the
                # same venue account concurrently -- each cancelling, re-querying and
                # transitioning orders the others were already working on. Checked EVERY
                # cycle, not once at startup: the lease is renewable and a leader that
                # dies must be able to lose it, which only works if non-leaders keep
                # asking.
                if not await self._try_become_leader():
                    logger.debug(
                        "Not the reconciliation leader this cycle - skipping (tenant=%s)",
                        tenant_id,
                    )
                    await asyncio.sleep(interval_seconds)
                    continue

                # Run reconciliation
                results = await self.reconcile_open_orders(tenant_id, exchange_id)
                
                # Log summary
                discrepancies = [r for r in results if r.action != ReconciliationAction.NO_ACTION]
                if discrepancies:
                    logger.warning(
                        f"Reconciliation found {len(discrepancies)} discrepancies: "
                        f"{[r.action.value for r in discrepancies]}"
                    )
                
                # Calculate sleep time to maintain interval
                elapsed = (datetime.utcnow() - start_time).total_seconds()
                sleep_time = max(0, interval_seconds - elapsed)
                
                await asyncio.sleep(sleep_time)
                
            except Exception as e:
                logger.error(f"Reconciliation loop error: {e}", exc_info=True)
                await asyncio.sleep(interval_seconds)  # Continue after error

    # ── Tenant enumeration (RECON-01) ───────────────────────────────────────────

    def enumerate_reconcilable_pairs(self) -> List[Tuple[str, Optional[str]]]:
        """Every ``(tenant_id, exchange_id)`` with an order that could be reconciled.

        RECON-01's second blocker was "NO TENANT-ENUMERATION SOURCE IS WIRED. Nothing
        calls reconcile_open_orders for any real tenant". This is that source.

        IT IS DERIVED FROM THE WORK, NOT FROM A TENANT LIST, and that choice is the whole
        design. Measured against production before deciding: ``execution_records`` holds
        198 rows -- 108 COMPLETED, 90 FAILED, and **zero** PENDING or EXECUTING -- across
        40 distinct tenants, against 182 profiles. So enumerating tenants and reconciling
        each would resolve a per-tenant executor for 40+ tenants, each requiring a vault
        decryption and a venue session, to reconcile nothing at all. Enumerating the OPEN
        ORDERS instead returns the empty set, costs one indexed query, and scales with
        actual open exposure rather than with signups.

        The predicate is deliberately the same one ``_fetch_db_active_orders`` uses, plus
        the requirement that a venue identifier exists: an order the venue was never told
        about, or whose id was never recorded, cannot be asked about. ``venue_order_id``
        is checked first and ``order_id`` second for the reason recorded in
        ``OrderWatchdog.venue_identifier`` -- TRADE-12 moved the exchange's id into
        ``venue_order_id`` and the live engine leaves ``order_id`` NULL, so a filter on
        ``order_id`` alone misses every row the engine created.

        Returns pairs, not tenants, because ``reconcile_open_orders`` resolves one
        executor per ``(tenant, exchange)`` and a tenant may hold orders at more than one
        venue. ``exchange_id`` may be ``None`` on a row that never recorded one; that pair
        is still returned so the caller can see it rather than silently dropping an open
        order, and ``_fetch_exchange_orders`` already refuses a ``None`` exchange with a
        clear error when a vault is configured.
        """
        rows = (
            self.db.query(
                ExecutionRecordModel.tenant_id,
                ExecutionRecordModel.exchange_id,
            )
            .filter(
                ExecutionRecordModel.status.in_([
                    ExecutionStatus.PENDING,
                    ExecutionStatus.EXECUTING,
                ]),
                or_(
                    ExecutionRecordModel.venue_order_id.isnot(None),
                    ExecutionRecordModel.order_id.isnot(None),
                ),
            )
            .distinct()
            .all()
        )
        return [(str(tenant_id), exchange_id) for tenant_id, exchange_id in rows]

    async def start_reconciliation_loop_all_tenants(
        self,
        interval_seconds: int = 5,
    ):
        """Reconcile every tenant that currently has an open order, once per cycle.

        RECON-01. ``start_reconciliation_loop`` above takes a single ``tenant_id``, which
        is why nothing could call it in production: there was no answer to "which tenant?".
        This resolves that by asking the database each cycle.

        ONE LEASE FOR THE WHOLE CYCLE, NOT ONE PER TENANT. The finding left this open as a
        decision between "one leader reconciles every tenant in sequence (simple, but one
        slow venue delays everyone)" and "the lease is held per tenant/exchange pair
        (parallel, but N leases and N executors to manage)". Taken deliberately, with the
        measurement above: N is zero today and will be small at launch, production runs a
        SINGLE ECS service with no worker services at all (INF-03), and there is therefore
        nothing to parallelise across. N leases for N=0 is complexity bought on
        speculation. The head-of-line concern is real but becomes real only with several
        tenants holding orders at a slow venue simultaneously, at which point the pair
        shape is a contained change -- the enumeration already returns pairs.

        FAILURE IS PER TENANT, NOT PER CYCLE. One tenant's venue being unreachable, or its
        credentials being unloadable, must not stop the others being reconciled; each pair
        is attempted independently and its failure logged. A cycle that loses every tenant
        to the same error still sleeps and retries rather than exiting the loop.

        NOT REGISTERED IN THE APPLICATION LIFESPAN, and that is not an oversight -- see
        the module note at :func:`start_reconciliation_loop_all_tenants` callers. A 5-second
        background loop inside the web process is precisely the defect PAPER-04 records
        ("paper session loops run in the web process ... a worker restart/redeploy ends
        every in-flight loop"). Hosting this in the API process would repeat that mistake
        knowingly. It belongs in a worker service, and INF-03 records that production has
        none: exactly one ECS service exists. The enumeration is the blocker this change
        closes; hosting is INF-03's.
        """
        self._running = True
        logger.info(
            "Starting multi-tenant reconciliation loop (interval=%ss)", interval_seconds
        )

        while self._running:
            try:
                start_time = datetime.utcnow()

                # Leadership first, and before the enumerating query: a non-leader should
                # cost nothing, not one query per cycle per process.
                if not await self._try_become_leader():
                    logger.debug(
                        "Not the reconciliation leader this cycle - skipping"
                    )
                    await asyncio.sleep(interval_seconds)
                    continue

                pairs = self.enumerate_reconcilable_pairs()
                if not pairs:
                    # The ordinary state while live trading is off. Debug, not info: this
                    # fires every interval_seconds and would otherwise be the loudest line
                    # in the log while saying nothing happened.
                    logger.debug(
                        "No tenant holds a reconcilable open order this cycle"
                    )
                else:
                    logger.info(
                        "Reconciling %d (tenant, exchange) pair(s)", len(pairs)
                    )

                total_discrepancies = 0
                for tenant_id, exchange_id in pairs:
                    try:
                        results = await self.reconcile_open_orders(
                            tenant_id, exchange_id
                        )
                    except Exception as exc:  # noqa: BLE001 - isolate one tenant's failure
                        logger.error(
                            "Reconciliation failed for tenant=%s exchange=%s: %s. Other "
                            "tenants in this cycle are unaffected.",
                            tenant_id, exchange_id, exc, exc_info=True,
                        )
                        continue

                    discrepancies = [
                        r for r in results
                        if r.action != ReconciliationAction.NO_ACTION
                    ]
                    if discrepancies:
                        total_discrepancies += len(discrepancies)
                        logger.warning(
                            "Reconciliation found %d discrepancy(ies) for tenant=%s "
                            "exchange=%s: %s",
                            len(discrepancies), tenant_id, exchange_id,
                            [r.action.value for r in discrepancies],
                        )

                if total_discrepancies:
                    logger.warning(
                        "Cycle complete: %d discrepancy(ies) across %d pair(s)",
                        total_discrepancies, len(pairs),
                    )

                elapsed = (datetime.utcnow() - start_time).total_seconds()
                await asyncio.sleep(max(0, interval_seconds - elapsed))

            except Exception as e:  # noqa: BLE001 - the loop must outlive a bad cycle
                logger.error(
                    f"Multi-tenant reconciliation loop error: {e}", exc_info=True
                )
                await asyncio.sleep(interval_seconds)

    def stop_reconciliation_loop(self):
        """Stop the reconciliation loop."""
        self._running = False
        logger.info("Reconciliation loop stopping...")
        
        if self._reconciliation_task and not self._reconciliation_task.done():
            self._reconciliation_task.cancel()
    
    async def check_order_exists_on_exchange(
        self,
        order_id: str,
        tenant_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        STEP 3.7: Check if order exists on exchange before retry.
        
        This prevents duplicate submissions when retrying failed orders.
        
        Args:
            order_id: Exchange order ID
            tenant_id: Tenant UUID
            
        Returns:
            Order data if exists, None if not found
        """
        if not self.exchange:
            logger.warning("No exchange client, cannot check order existence")
            return None
        
        try:
            # Try to fetch order from exchange
            order = await self.exchange.fetch_order(order_id)
            
            if order:
                logger.info(f"Order {order_id} exists on exchange")
                return {
                    'order_id': order.get('id'),
                    'status': order.get('status'),
                    'filled': float(order.get('filled', 0)),
                    'remaining': float(order.get('remaining', 0)),
                    'average': float(order.get('average', 0))
                }
            
            return None
            
        except Exception as e:
            # Order not found or error
            logger.debug(f"Order {order_id} not found on exchange: {e}")
            return None


# Global service instance
_reconciliation_service: Optional[ExchangeReconciliationService] = None


def get_reconciliation_service(
    db_session: Session,
    exchange_client: Optional[Any] = None,
    vault: Optional[Any] = None,
) -> ExchangeReconciliationService:
    """Get or create global reconciliation service.

    RECON-01: this factory could not express the only multi-tenant-safe configuration of
    the thing it constructs. ``__init__`` grew a ``vault`` parameter precisely so that
    ``_fetch_exchange_orders`` resolves a per-(tenant, exchange) executor rather than
    trusting one shared ``exchange_client``, but the factory did not forward it -- so the
    single documented entry point could only ever build the unsafe shape, and any caller
    wanting the safe one had to bypass it and call the constructor directly.
    """
    global _reconciliation_service
    if _reconciliation_service is None:
        _reconciliation_service = ExchangeReconciliationService(
            db_session, exchange_client, vault=vault
        )
    return _reconciliation_service
