"""RT-16 — the host for the private venue fill stream.

WHAT THIS CLOSES
----------------
RT-16 recorded three links that each existed and none of which had a caller::

    1. ExchangeWebSocketListener._listen_for_orders   (watch_orders -> _handle_order_update)
    2. EventListener._on_exchange_event              (enqueue -> _event_processing_loop)
    3. EventRouter.route_event                       (-> the execution record)

Its own entry is emphatic that **starting any one of them alone delivers nothing**:
``_listen_for_orders`` would build events and hand them to a listener with no router, and
``EventListener`` refuses an event when ``_event_router is None`` -- deliberately, and it
un-records the event id so a venue redelivery is not later suppressed as a duplicate. So
this module starts all three, in dependency order, or none of them.

WHY A SEPARATE PROCESS AND NOT THE WEB APP
------------------------------------------
``gunicorn`` runs ``--workers ${WORKERS:-2}``. Every worker process runs the FastAPI
lifespan hook independently, so a stream started there would be opened once per worker:
two authenticated private sockets per (tenant, venue), two copies of every fill event, and
two writers racing on one execution row. PAPER-04 records what happens to a loop that
lives in the web process. The links therefore live here, in one process, under a leader
lease -- and ``tests/test_rt16_private_fill_stream_posture.py`` still asserts that
``main.py`` starts none of them.

THE ORDER IS LOAD-BEARING
-------------------------
``EventRouter`` first, then ``EventListener.start(router)``, then the feeder. Reversed, the
window between the feeder starting and the router arriving is a window in which every fill
is refused and logged as an error -- which is safe, because the guard exists, but it means
losing the first fills of every deployment for no reason.

WHAT IT DOES NOT DO
-------------------
It does not adjudicate. ``OrderEventHandler`` records the venue's figures as EVIDENCE and
leaves the terminal transition and the position credit to ``OrderWatchdog``, because
``core/models/execution_record.py`` states that "OrderWatchdog is the only owner of
adjudication, and introducing a second one would be worse than the defect". This worker
makes a fill arrive in seconds instead of on the watchdog's next poll; it does not decide
what the fill means.

It also does not enable live trading. RT-15's platform gate is untouched, and a venue that
hands back a fill for an order this platform never placed resolves to no execution row and
is logged and dropped.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from backend_app.core.leader_lease import try_acquire_or_renew
from backend_app.core.worker_base import WorkerBase

logger = logging.getLogger(__name__)

#: The Redis health-key suffix (``worker:health:{name}``) and the ECS worker key.
WORKER_NAME = "exchange_fill_worker"

#: The lease key. MUST be distinct from every other subsystem's -- `leader_lease`'s own
#: docstring warns that two loops sharing one key make each one's leader suppress the
#: other, which looks exactly like "the loop is running" while half the work never happens.
LEADER_LOCK_KEY = "rt16:exchange_fill_stream:leader"

#: How often the live (tenant, venue) set is re-read. There is no change feed on
#: ``strategy_deployments`` -- ``rows_in`` is a snapshot -- so picking up a new deployment
#: and retiring a stopped one both depend on this poll.
POLL_INTERVAL_SECONDS = float(os.getenv("FILL_STREAM_POLL_SECONDS", "30"))

#: Lease duration. MUST exceed the poll interval, or the holder keeps losing a lease it is
#: still actively using -- `leader_lease` documents that as an argument contract.
LEASE_TTL_SECONDS = int(os.getenv("FILL_STREAM_LEASE_TTL_SECONDS", "90"))

#: A pair is (tenant_id, exchange_id).
Pair = Tuple[str, str]


class _PerEventRouter:
    """Satisfies :class:`EventListener`'s ``route_event`` duck type, one session per event.

    WHY NOT HAND ``EventRouter`` A SESSION AND KEEP IT. ``EventRouter.__init__`` takes a
    synchronous SQLAlchemy ``Session``, stores it as ``self.db``, and gives that same
    instance to all three of its handlers, which ``commit()`` on it. ``EventListener``
    holds ONE router for its lifetime, so the obvious wiring would pin a single session
    open for the life of this process -- days -- accumulating identity-map state and
    holding a pooled connection across every idle period between fills.

    A fresh session per event costs one checkout from a pool that already has
    ``pool_pre_ping=True`` and ``pool_recycle=3600``, and it means a poisoned transaction
    cannot outlive the event that caused it.

    WHAT IS GIVEN UP, and why it does not matter: ``EventRouter`` keeps two pieces of
    per-instance state. ``_last_processed_event_id`` feeds ``_is_duplicate_event``, which
    compares the router's OWN monotonic counter -- it enforces ordering within one
    instance and is not venue-level deduplication, which lives in
    ``EventListener._processed_event_ids`` and does persist. ``_state_version`` only
    stamps the frontend push payload. Neither carries meaning across events.
    """

    def __init__(self, session_factory=None, router_factory=None):
        self._session_factory = session_factory
        self._router_factory = router_factory
        self.routed = 0
        self.failed = 0

    def _new_session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from backend_app.core.database_pool import db_pool
        return db_pool.get_session()

    def _new_router(self, session):
        if self._router_factory is not None:
            return self._router_factory(session)
        from backend_app.backend.event_router import get_event_router
        return get_event_router(session)

    async def route_event(self, event: Any) -> None:
        session = None
        try:
            session = self._new_session()
            router = self._new_router(session)
            await router.route_event(event)
            self.routed += 1
        except Exception:  # noqa: BLE001
            # Logged and swallowed, matching EventListener's own drain loop: one bad event
            # must not kill the stream for every other tenant. The event is lost rather
            # than retried, which is acceptable only because the watchdog still
            # reconciles from the venue -- this stream is an accelerator, not the system
            # of record.
            self.failed += 1
            logger.error(
                "RT-16: routing a venue event failed; it is dropped and the watchdog "
                "remains responsible for this execution.", exc_info=True,
            )
            if session is not None:
                try:
                    session.rollback()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:  # noqa: BLE001
                    logger.warning("RT-16: failed to close an event session.",
                                   exc_info=True)


class ExchangeFillWorker(WorkerBase):
    """Starts RT-16's three links, under a leader lease, and keeps the pair set current."""

    def __init__(
        self,
        *,
        worker_name: Optional[str] = None,
        poll_interval: Optional[float] = None,
        state_store: Optional[Any] = None,
        listener: Optional[Any] = None,
        event_bus: Optional[Any] = None,
        router: Optional[Any] = None,
    ):
        super().__init__(
            worker_name=worker_name or os.getenv("WORKER_NAME") or WORKER_NAME,
            poll_interval=poll_interval if poll_interval is not None else POLL_INTERVAL_SECONDS,
        )
        #: Stable for this process's lifetime, which is what a renewable lease needs to
        #: tell "still me, renew" apart from "someone else, back off".
        self._instance_id = str(uuid.uuid4())

        self._state_store = state_store
        self._injected_listener = listener
        self._injected_event_bus = event_bus
        self._injected_router = router

        self._listener: Optional[Any] = None
        self._event_bus: Optional[Any] = None
        self._router: Optional[Any] = None
        self._chain_started = False

        #: The pairs this process is currently streaming.
        self._streaming: Set[Pair] = set()
        self._is_leader = False

    # ── the chain ──────────────────────────────────────────────────────────────────

    async def _ensure_chain_started(self) -> None:
        """Start all three links, in dependency order. Idempotent."""
        if self._chain_started:
            return

        # 1. THE ROUTER FIRST. Nothing downstream of it can accept an event until it
        #    exists, and EventListener refuses events while its router is None.
        self._router = self._injected_router or _PerEventRouter()

        # 2. THE DRAIN SECOND. `start(router)` is the only thing that sets
        #    `_event_router` AND starts `_event_processing_loop`; without it the queue is
        #    never read and `_on_exchange_event` refuses everything by design.
        if self._injected_event_bus is not None:
            self._event_bus = self._injected_event_bus
        else:
            from backend_app.backend.event_listener import get_event_listener
            self._event_bus = get_event_listener()
        await self._event_bus.start(self._router)

        # 3. THE FEEDER LAST, and only its `_running` flag here -- the per-pair streams
        #    are opened by `_reconcile_pairs`. `start()` must still be awaited, because
        #    both `_listen_for_orders` and `_fallback_monitor` gate their loops on that
        #    flag and would otherwise fall straight through and complete silently.
        if self._injected_listener is not None:
            self._listener = self._injected_listener
        else:
            from backend_app.backend.exchange_websocket_listener import \
                get_exchange_websocket_listener
            self._listener = get_exchange_websocket_listener()
        await self._listener.start()

        self._chain_started = True
        logger.info(
            "RT-16: fill stream chain started -- router, event drain, then feeder. "
            "instance=%s", self._instance_id,
        )

    async def _stop_chain(self) -> None:
        """Tear the chain down in reverse. Never raises."""
        for pair in sorted(self._streaming):
            await self._stop_pair(pair)
        self._streaming.clear()

        if self._listener is not None:
            try:
                await self._listener.stop()
            except Exception:  # noqa: BLE001
                logger.warning("RT-16: feeder stop failed.", exc_info=True)
        if self._event_bus is not None:
            try:
                await self._event_bus.stop()
            except Exception:  # noqa: BLE001
                logger.warning("RT-16: event drain stop failed.", exc_info=True)

        self._chain_started = False
        logger.info("RT-16: fill stream chain stopped.")

    # ── pair discovery ─────────────────────────────────────────────────────────────

    async def _live_pairs(self) -> Set[Pair]:
        """The distinct (tenant, venue) pairs with a LIVE deployment right now.

        Reads the same table and the same states ``fleet_manager.resume_deployments`` and
        ``deployment_runtime.resume_running_deployments`` already use, through the same
        repository, so "which deployments are live" has one answer in this codebase rather
        than two.

        A row is only included when it names BOTH a tenant and a venue. A deployment with
        no ``exchange_id`` cannot be streamed -- there is no venue to connect to -- and
        guessing one would open a socket to the wrong exchange with a real credential.
        """
        store = self._state_store
        if store is None:
            from backend_app.backend.deployment_state import DeploymentStateStore
            store = self._state_store = DeploymentStateStore()

        from backend_app.backend.deployment_state import LIVE_STATES

        try:
            rows: Iterable[Dict[str, Any]] = await store.rows_in(LIVE_STATES)
        except Exception:  # noqa: BLE001
            # Fails CLOSED by changing nothing: returning an empty set here would tear
            # down every working stream on one bad read, so the caller is told to leave
            # the current set alone instead.
            logger.warning(
                "RT-16: could not read live deployments; the streaming set is left "
                "unchanged for this cycle.", exc_info=True,
            )
            raise

        pairs: Set[Pair] = set()
        for row in rows or []:
            if not self._is_live_mode(row):
                continue
            tenant = row.get("user_id")
            venue = row.get("exchange_id")
            if not tenant or not venue:
                continue
            pairs.add((str(tenant), str(venue).strip().lower()))
        return pairs

    @staticmethod
    def _is_live_mode(row: Dict[str, Any]) -> bool:
        """Is this deployment trading for real money?

        PAPER DEPLOYMENTS MUST NOT OPEN A VENUE SOCKET. A paper session fills against this
        platform's own simulator and has no venue orders to hear about, so streaming for
        one would spend an authenticated connection and a rate-limit budget to receive
        nothing -- and would require a credential the user may never have stored.

        ``mode`` is the binding column (004e); ``environment`` is the fallback
        ``deployment_runtime`` itself uses when 004e has not been applied. Absent both, the
        row is treated as NOT live: a deployment whose mode cannot be established is the
        one case where guessing wrong opens a real socket.
        """
        mode = row.get("mode")
        if mode is not None:
            return str(mode).strip().lower() == "live"
        environment = row.get("environment")
        if environment is not None:
            return str(environment).strip().lower() == "live"
        return False

    # ── per-pair lifecycle ─────────────────────────────────────────────────────────

    async def _start_pair(self, pair: Pair) -> None:
        tenant_id, exchange_id = pair
        try:
            await self._listener.start_listening(tenant_id, exchange_id)
            self._streaming.add(pair)
            logger.info("RT-16: streaming fills for %s on %s.", tenant_id, exchange_id)
        except Exception:  # noqa: BLE001
            logger.error(
                "RT-16: could not start the fill stream for %s on %s; it will be retried "
                "next cycle.", tenant_id, exchange_id, exc_info=True,
            )

    async def _stop_pair(self, pair: Pair) -> None:
        tenant_id, exchange_id = pair
        try:
            await self._listener.stop_listening(tenant_id, exchange_id)
            logger.info("RT-16: stopped streaming fills for %s on %s.",
                        tenant_id, exchange_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "RT-16: failed to stop the fill stream for %s on %s.",
                tenant_id, exchange_id, exc_info=True,
            )

    async def _reconcile_pairs(self) -> None:
        """Bring the streaming set in line with the live set."""
        try:
            wanted = await self._live_pairs()
        except Exception:  # noqa: BLE001
            return  # already logged; leave the current streams running

        for pair in sorted(wanted - self._streaming):
            await self._start_pair(pair)

        for pair in sorted(self._streaming - wanted):
            await self._stop_pair(pair)
            self._streaming.discard(pair)

    # ── the loop ───────────────────────────────────────────────────────────────────

    async def process_iteration(self) -> None:
        """One cycle: confirm leadership, ensure the chain, reconcile the pair set."""
        leader = await try_acquire_or_renew(
            lock_key=LEADER_LOCK_KEY,
            instance_id=self._instance_id,
            ttl_seconds=LEASE_TTL_SECONDS,
            subsystem="RT-16 fill stream",
        )

        if not leader:
            # LOSING THE LEASE MUST CLOSE THE SOCKETS. A process that keeps streaming
            # after another has taken over gives every fill two writers -- exactly the
            # fan-out the lease exists to prevent. `try_acquire_or_renew` also answers
            # False when Redis is unreachable, so this is the path taken when leadership
            # simply cannot be established, and standing down is the safe reading of
            # "unknown".
            if self._is_leader or self._chain_started:
                logger.warning(
                    "RT-16: no longer the leader (or leadership is unverifiable); "
                    "stopping every stream this process holds."
                )
                await self._stop_chain()
            self._is_leader = False
            return

        if not self._is_leader:
            logger.info("RT-16: holding the fill-stream lease (instance=%s).",
                        self._instance_id)
        self._is_leader = True

        await self._ensure_chain_started()
        await self._reconcile_pairs()

    async def cleanup(self) -> None:
        await self._stop_chain()

    # ── observability ──────────────────────────────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        """What this process currently holds. For logs and tests, not a health contract."""
        return {
            "worker": self.worker_name,
            "instance_id": self._instance_id,
            "is_leader": self._is_leader,
            "chain_started": self._chain_started,
            "streaming_pairs": sorted(self._streaming),
        }


async def run_worker() -> None:
    """Start the worker and keep the process alive until it is cancelled.

    The same shape ``command_worker.run_worker`` and
    ``marketplace_expiry_worker.run_worker`` use, so every worker is hosted identically.
    """
    worker = ExchangeFillWorker()
    await worker.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await worker.stop()


# INF-07. This block is the entry point, and its ABSENCE is a recorded production
# incident: `backend_app.backend.dag_worker` was declared in terraform with
# `python -m backend_app.backend.dag_worker`, had no `__main__`, and so imported, built a
# module-level global, reached the end of the file and exited 0 -- which ECS cannot tell
# apart from a job that completed, so it restarted the task forever while the service
# reported a task running. `tests/test_inf07_declared_workers_are_runnable.py` parses
# every declared worker module with `ast` and fails if this block is missing.
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass
