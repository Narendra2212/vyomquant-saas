"""
backend_app/workers/marketplace_expiry_worker.py - hosts the Subscription expiry sweep.

Spec: marketplace-subscriptions-paper-trading task 20.1. ``design.md`` ->
"``marketplace/expiry_sweep.py`` and its worker". Requirements 11.8, 11.15, 26.2, 26.5.

Exposes
-------
MarketplaceExpiryWorker     the ``WorkerBase`` subclass that calls the sweep on a timer
run_worker()                the module's ``__main__`` entry point
WORKER_NAME                 ``'marketplace_expiry_worker'`` - the health key's suffix

WHAT THIS WORKER IS, AND EMPHATICALLY IS NOT
--------------------------------------------
It is a **timer**. Its whole job is to call
``backend_app.backend.marketplace.expiry_sweep.sweep`` every
:data:`~backend_app.backend.marketplace.expiry_sweep.SWEEP_INTERVAL_SECONDS` seconds and to let
a failure be visible.

It is **not** part of any access decision. ``entitlement_resolver.resolve`` compares ``now``
with each Subscription's ``period_expiry`` on every call, so if this process is dead, throttled
or has never been started, access still ends at the expiry instant (Requirement 11.7, property
P-11). What lags while this worker is down is the stored ``status`` label and the
session-stopping action of Requirement 11.15 - housekeeping, not authorisation. The sweep
module's docstring is the long version of this, and it is worth reading before changing either
file.

WHY 30 SECONDS
--------------
Requirement 11.8 permits an interval "no greater than 60 seconds". The interval comes from
``expiry_sweep.SWEEP_INTERVAL_SECONDS`` (30) rather than being written again here, so there is
one literal to change and one literal for the test to assert. At 30 seconds the 60-second bound
of Requirements 11.8 and 11.15 still holds when a single pass is skipped by backpressure or
runs long.

WHY ``requires_subscription_check`` STAYS FALSE
----------------------------------------------
``WorkerBase.requires_subscription_check`` exists for workers that execute trading operations
*on behalf of a specific user* (BUG-FIX MC-02), and it gates ``process_iteration`` on that
user's subscription being live. This worker is not user-scoped - it operates on every
Subscription at once - and gating it on a subscription check would be circular: the thing that
records a subscription as lapsed would refuse to run because subscriptions are lapsed. So the
flag stays at its default ``False``, and this paragraph is the reason, so the next reader does
not "fix" it.

HOW A FAILURE BECOMES VISIBLE (Requirement 26.5)
------------------------------------------------
Two independent mechanisms, and neither of them is a swallow:

  * :meth:`process_iteration` lets :class:`~...expiry_sweep.ExpirySweepFailed` propagate.
    ``WorkerBase._run_loop`` catches it, logs it with a traceback, increments
    ``_consecutive_failures``, and after three consecutive failures the heartbeat publishes
    ``status = "unhealthy"`` to ``worker:health:marketplace_expiry_worker``. A liveness probe
    watching that key sees the real state.
  * ``expiry_sweep.last_run_at`` advances only on a fully successful pass, so a probe comparing
    it against the interval sees the sweep fall behind even if the process itself is up. That
    is the ``marketplace.expiry_sweep.last_run_at`` health-check input ``design.md`` names, and
    :meth:`health_details` is what an endpoint reads it through.

The one exception that is *not* a sweep failure is a missing Persistence_Layer credential, which
is a deployment condition rather than a sweep condition. It is raised from
:meth:`process_iteration` too, for the same reason: a worker that quietly ran forever without a
database would report healthy while expiring nothing at all.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from backend_app.backend.marketplace import expiry_sweep
from backend_app.core.worker_base import WorkerBase

logger = logging.getLogger("MarketplaceExpiryWorker")

#: The worker's name, and therefore the suffix of its Redis health key
#: (``worker:health:marketplace_expiry_worker``).
WORKER_NAME = "marketplace_expiry_worker"

__all__ = ["WORKER_NAME", "MarketplaceExpiryWorker", "run_worker"]


class PersistenceUnavailable(RuntimeError):
    """No service-role Persistence_Layer handle could be built, so no sweep can run.

    A *defined* outcome rather than a silent no-op: a worker that looped happily with no
    database would report healthy while never expiring a single Subscription, and the only
    evidence would be an absence. Raised from :meth:`MarketplaceExpiryWorker.process_iteration`
    so ``WorkerBase``'s failure counting and the unhealthy heartbeat pick it up.
    """


class MarketplaceExpiryWorker(WorkerBase):
    """Calls ``expiry_sweep.sweep`` every 30 seconds. That is the whole worker.

    Follows the shape ``backend_app/workers/command_worker.py`` establishes: subclass
    ``WorkerBase``, implement ``process_iteration``, let ``start``/``stop``, the heartbeat, the
    backpressure hook and the failure counting come from the base class rather than being
    re-implemented here.
    """

    #: Not user-scoped - see the module docstring for why this must stay ``False``.
    requires_subscription_check: bool = False

    def __init__(
        self,
        *,
        supabase: Any = None,
        worker_name: Optional[str] = None,
        poll_interval: Optional[float] = None,
    ) -> None:
        """
        Args:
            supabase: an injected Persistence_Layer handle. Passed in by a test or by a caller
                that already holds a service-role client; when omitted, one is built lazily from
                ``SUPABASE_URL`` / ``SUPABASE_SERVICE_ROLE_KEY`` on the first iteration.
            worker_name: overrides :data:`WORKER_NAME`, e.g. to run a second instance under its
                own health key.
            poll_interval: overrides the sweep interval. Defaults to
                ``expiry_sweep.SWEEP_INTERVAL_SECONDS`` (30) - the one place the interval is
                written (Requirement 11.8).
        """
        super().__init__(
            worker_name=worker_name or os.getenv("WORKER_NAME") or WORKER_NAME,
            poll_interval=(
                expiry_sweep.SWEEP_INTERVAL_SECONDS
                if poll_interval is None
                else float(poll_interval)
            ),
        )
        self._supabase = supabase
        #: The last outcome, for the health endpoint and for a test that wants to look.
        self.last_outcome: Optional[expiry_sweep.SweepOutcome] = None

    # ---- the Persistence_Layer handle -------------------------------------

    def _resolve_client(self) -> Any:
        """The service-role client, built once and cached.

        Built here rather than imported from ``routers/library.py``: importing a router into a
        worker would pull FastAPI, every dependency in that module and its route table into a
        background process that serves no HTTP.

        Raises:
            PersistenceUnavailable: the credentials are absent or the client could not be
                constructed. Never ``None`` - see :class:`PersistenceUnavailable`.
        """
        if self._supabase is not None:
            return self._supabase

        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise PersistenceUnavailable(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set for the "
                "marketplace expiry sweep to run"
            )
        try:
            from supabase import create_client
        except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome
            raise PersistenceUnavailable(
                f"the supabase client library is not importable: {exc}"
            ) from exc
        try:
            self._supabase = create_client(url, key)
        except Exception as exc:  # noqa: BLE001 - re-raised as the defined outcome
            raise PersistenceUnavailable(
                f"a service-role client could not be created: {exc}"
            ) from exc
        return self._supabase

    # ---- the one iteration ------------------------------------------------

    async def process_iteration(self) -> None:
        """One sweep pass.

        Nothing is caught here. ``ExpirySweepFailed`` and :class:`PersistenceUnavailable` both
        propagate to ``WorkerBase._run_loop``, which logs with a traceback and drives the
        unhealthy heartbeat after three consecutive failures. Catching either would be the
        swallow Requirement 26.5 forbids, and would leave subscriptions unexpired with the
        worker still reporting green.
        """
        outcome = await expiry_sweep.sweep(supabase=self._resolve_client())
        self.last_outcome = outcome
        if outcome.expired_count:
            logger.info(
                "[%s] expired %d subscription(s) in %d ms; stopped %d deployment(s) and "
                "%d paper session(s)",
                self.worker_name,
                outcome.expired_count,
                outcome.duration_ms,
                outcome.deployments_stopped,
                outcome.paper_sessions_stopped,
            )

    # ---- the health-check input -------------------------------------------

    def health_details(self) -> Dict[str, Any]:
        """The sweep's metrics plus this worker's own liveness facts, as one mapping.

        ``marketplace.expiry_sweep.last_run_at`` is the health-check input of ``design.md`` ->
        "Observability"; ``interval_seconds`` travels with it because "stale" is only meaningful
        relative to the interval, and a probe that had to guess the interval would either alarm
        early or never.
        """
        details: Dict[str, Any] = dict(expiry_sweep.metrics_snapshot())
        details.update(
            {
                "worker_name": self.worker_name,
                "running": self.running,
                "consecutive_failures": self._consecutive_failures,
                "interval_seconds": self.poll_interval,
            }
        )
        return details


async def run_worker() -> None:
    """Start the worker and keep the process alive until it is cancelled.

    The same shape ``command_worker.run_worker`` uses, so both workers are hosted identically.
    """
    worker = MarketplaceExpiryWorker()
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
