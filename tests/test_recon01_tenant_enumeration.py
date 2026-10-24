"""RECON-01 -- the tenant-enumeration source, and why it is derived from open orders.

THE BLOCKER THIS CLOSES
    RECON-01's remaining_action: "NO TENANT-ENUMERATION SOURCE IS WIRED. Nothing calls
    reconcile_open_orders for any real tenant, so the service is still invoked nowhere in
    production." ``start_reconciliation_loop`` takes a single ``tenant_id``, and there was
    no answer to "which tenant?".

THE DECISION, AND THE MEASUREMENT BEHIND IT
    The finding left the shape open: "whether one leader instance reconciles every tenant
    in sequence (simple, but one slow venue delays everyone) or whether the lease is held
    per tenant/exchange pair (parallel, but N leases and N executors to manage)".

    Measured against production before choosing: ``execution_records`` holds 198 rows --
    108 COMPLETED, 90 FAILED, and ZERO PENDING or EXECUTING -- across 40 distinct tenants,
    against 182 profiles. So:

    * Enumerating TENANTS would resolve a per-tenant executor for 40+ tenants, each
      needing a vault decryption and a venue session, to reconcile nothing.
    * Enumerating OPEN ORDERS returns the empty set for one indexed query, and scales with
      open exposure rather than with signups.

    One lease per cycle, not per pair: N is zero today, production runs a single ECS
    service with no workers at all (INF-03), so there is nothing to parallelise across.

WHAT THIS FILE DOES NOT CHANGE, DELIBERATELY
    The service is still not started anywhere. A 5-second background loop inside the web
    process is exactly the defect PAPER-04 records, and hosting it there would repeat that
    mistake knowingly. It belongs in a worker; INF-03 records that production has none.
    ``test_the_service_is_still_not_started_anywhere`` in
    tests/test_recon01_exchange_reconciliation_leader_election.py and
    ``test_the_service_is_not_registered_in_the_application_lifespan`` in
    tests/test_p10d_exchange_reconciliation_fails_safely.py both still pin that, and are
    deliberately left passing. (That first path was cited here as
    tests/test_recon01_leader_election.py -- a file which does not exist -- and is
    corrected above to the real filename.)
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend_app.backend.exchange_reconciliation import (
    ExchangeReconciliationService, ReconciliationAction)
from backend_app.core.models.execution_record import (ExecutionRecordModel,
                                                      ExecutionStatus)

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    ExecutionRecordModel.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _row(session, *, tenant, status, exchange="binance", venue_order_id="V-1",
         order_id=None, symbol="BTC/USDT"):
    rec = ExecutionRecordModel(
        execution_id=f"exec-{uuid.uuid4().hex[:12]}",
        tenant_id=tenant,
        strategy_id="strategy-under-test",  # NOT NULL on the model
        symbol=symbol,
        side="buy",
        size="0.01",
        status=status,
        exchange_id=exchange,
        venue_order_id=venue_order_id,
        order_id=order_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(rec)
    session.commit()
    return rec


def _service(session):
    return ExchangeReconciliationService(db_session=session, exchange_client=object())


# ── what the enumeration returns ────────────────────────────────────────────


def test_no_open_orders_enumerates_nothing(db):
    """THE PRODUCTION STATE TODAY, and the reason this is safe to run. Completed and
    failed orders are not reconcilable work."""
    _row(db, tenant=TENANT_A, status=ExecutionStatus.COMPLETED)
    _row(db, tenant=TENANT_A, status=ExecutionStatus.FAILED)

    assert _service(db).enumerate_reconcilable_pairs() == []


def test_an_open_order_enumerates_its_tenant_and_exchange(db):
    _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING, exchange="binance")

    assert _service(db).enumerate_reconcilable_pairs() == [(str(TENANT_A), "binance")]


@pytest.mark.parametrize("status", [ExecutionStatus.PENDING, ExecutionStatus.EXECUTING])
def test_both_open_statuses_are_enumerated(db, status):
    """The predicate matches `_fetch_db_active_orders`, which selects PENDING and
    EXECUTING -- the same population OrderWatchdog polls."""
    _row(db, tenant=TENANT_A, status=status)

    assert _service(db).enumerate_reconcilable_pairs() == [(str(TENANT_A), "binance")]


def test_a_row_with_no_venue_identifier_is_not_enumerated(db):
    """An order the venue was never told about, or whose id was never recorded, cannot be
    asked about -- so it is not reconcilable work."""
    _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING,
         venue_order_id=None, order_id=None)

    assert _service(db).enumerate_reconcilable_pairs() == []


def test_a_row_carrying_only_the_legacy_order_id_is_still_enumerated(db):
    """TRADE-12 split the two identifiers apart; the older recovery paths still populate
    `order_id`. Those rows must not be dropped."""
    _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING,
         venue_order_id=None, order_id="LEGACY-1")

    assert _service(db).enumerate_reconcilable_pairs() == [(str(TENANT_A), "binance")]


def test_a_venue_order_id_alone_is_enough(db):
    """THE REGRESSION THAT MATTERS. The live engine writes the venue's id to
    `venue_order_id` and leaves `order_id` NULL (TRADE-12), so an enumeration filtering on
    `order_id` alone would miss every row the engine created -- the same blind spot RT-16
    found in the watchdog's own population queries."""
    _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING,
         venue_order_id="VENUE-ONLY", order_id=None)

    assert _service(db).enumerate_reconcilable_pairs() == [(str(TENANT_A), "binance")]


def test_pairs_are_distinct_not_one_per_order(db):
    """Three open orders for one tenant at one venue are ONE unit of work, because
    reconcile_open_orders fetches that tenant's whole open set in a single call."""
    for _ in range(3):
        _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING)

    assert _service(db).enumerate_reconcilable_pairs() == [(str(TENANT_A), "binance")]


def test_one_tenant_at_two_venues_is_two_pairs(db):
    """Why pairs rather than tenants: reconcile_open_orders resolves ONE executor per
    (tenant, exchange), so a tenant holding orders at two venues is two units of work."""
    _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING, exchange="binance")
    _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING, exchange="kraken")

    pairs = _service(db).enumerate_reconcilable_pairs()
    assert sorted(pairs) == sorted([(str(TENANT_A), "binance"), (str(TENANT_A), "kraken")])


def test_two_tenants_are_two_pairs_and_are_not_merged(db):
    _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING)
    _row(db, tenant=TENANT_B, status=ExecutionStatus.EXECUTING)

    pairs = _service(db).enumerate_reconcilable_pairs()
    assert sorted(pairs) == sorted([(str(TENANT_A), "binance"), (str(TENANT_B), "binance")])


def test_enumeration_does_not_scale_with_closed_history(db):
    """40 tenants have execution records in production and none has an open one. The
    enumeration must cost nothing for the closed ones."""
    for _ in range(25):
        _row(db, tenant=uuid.uuid4(), status=ExecutionStatus.COMPLETED)
    _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING)

    assert _service(db).enumerate_reconcilable_pairs() == [(str(TENANT_A), "binance")]


# ── the multi-tenant loop ───────────────────────────────────────────────────


class _Recorder(ExchangeReconciliationService):
    """Records which pairs a cycle reconciled, and stops the loop after one cycle."""

    def __init__(self, session, *, leader=True, fail_for=None):
        super().__init__(db_session=session, exchange_client=object())
        self.reconciled: list[tuple[str, str | None]] = []
        self._leader = leader
        self._fail_for = fail_for or set()

    async def _try_become_leader(self):
        return self._leader

    async def reconcile_open_orders(self, tenant_id, exchange_id=None):
        self.reconciled.append((tenant_id, exchange_id))
        if tenant_id in self._fail_for:
            raise RuntimeError(f"venue unreachable for {tenant_id}")
        self.stop_reconciliation_loop()
        return []


def _run_one_cycle(service):
    async def _go():
        await asyncio.wait_for(
            service.start_reconciliation_loop_all_tenants(interval_seconds=0),
            timeout=5,
        )
    asyncio.run(_go())


def test_the_loop_reconciles_every_enumerated_pair(db):
    _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING)
    _row(db, tenant=TENANT_B, status=ExecutionStatus.EXECUTING)
    service = _Recorder(db)

    _run_one_cycle(service)

    assert {t for t, _ in service.reconciled} == {str(TENANT_A), str(TENANT_B)}


def test_a_non_leader_reconciles_nothing(db):
    """The lease is checked BEFORE the enumerating query, so a non-leader costs nothing
    per cycle -- not one query per process per interval."""
    _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING)
    service = _Recorder(db, leader=False)
    service.stop_reconciliation_loop()  # one pass, then exit

    async def _go():
        service._running = True
        task = asyncio.ensure_future(
            service.start_reconciliation_loop_all_tenants(interval_seconds=0)
        )
        await asyncio.sleep(0.05)
        service.stop_reconciliation_loop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(_go())

    assert service.reconciled == []


def test_one_tenants_failure_does_not_stop_the_others(db):
    """FAILURE IS PER TENANT. One unreachable venue or one unloadable credential must not
    cost every other tenant its reconciliation in that cycle."""
    _row(db, tenant=TENANT_A, status=ExecutionStatus.EXECUTING)
    _row(db, tenant=TENANT_B, status=ExecutionStatus.EXECUTING)
    service = _Recorder(db, fail_for={str(TENANT_A), str(TENANT_B)} - {str(TENANT_B)})

    _run_one_cycle(service)

    assert {t for t, _ in service.reconciled} == {str(TENANT_A), str(TENANT_B)}, (
        "both pairs must be attempted even though the first raised"
    )


def test_an_empty_cycle_is_a_no_op_and_does_not_raise(db):
    """The production state today: nothing open, so a cycle reconciles nobody. It must not
    error, and it must not attempt a venue call."""
    _row(db, tenant=TENANT_A, status=ExecutionStatus.COMPLETED)
    service = _Recorder(db)

    async def _go():
        service._running = True
        task = asyncio.ensure_future(
            service.start_reconciliation_loop_all_tenants(interval_seconds=0)
        )
        await asyncio.sleep(0.05)
        service.stop_reconciliation_loop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(_go())

    assert service.reconciled == []


# ── the posture this change deliberately does NOT flip ──────────────────────


def test_the_service_is_still_not_started_in_the_web_process():
    """RECON-01's enumeration blocker is closed; HOSTING is not, and must not be faked by
    starting a 5-second loop in the API process.

    That is precisely PAPER-04's recorded defect -- "paper session loops run in the web
    process ... a worker restart/redeploy still ends every in-flight loop with no attempt
    to resume it". Repeating it knowingly for reconciliation would be worse than leaving
    the loop unhosted, because reconciliation exists to notice lost fills and a loop that
    silently dies on deploy would be a reconciliation service that reports healthy while
    reconciling nothing. ``/health/services`` would not even show the death: it is a boot
    snapshot written once during the lifespan and never refreshed (OBS-01), so a loop that
    died an hour ago still reports ACTIVE.

    INF-03 records that production has exactly ONE ECS service and no worker services of
    any kind, so there is nowhere correct to host this yet. Measured this session, the
    worker route is additionally blocked by PERMISSIONS and not merely unbuilt: the deploy
    identity ``user/github-actions`` is DENIED ``s3:CreateBucket``, the terraform state
    bucket ``vyomquant-terraform-state`` returns 404, and ``events:ListRules`` is denied
    too -- so neither a terraform-managed worker service nor an EventBridge scheduled task
    can be created with the credentials available.

    BUT HOSTING IS NO LONGER THE DECIDING REASON, and this test does not rest on it.
    Two reasons survive regardless of where the loop would run:

    1. RESOLVED, and no longer one of them. This used to read: "``_handle_ghost_order``
       INSERTs and COMMITs a new ``execution_records`` row ... manufacturing position
       state for unexplained live orders is an owner's policy call." The call has been
       made -- NO -- and the code now refuses rather than the ledger merely saying so.
       See tests/test_recon01_unattributed_order_policy.py.
    2. ``backend_app/core/models/execution_record.py`` records that "`OrderWatchdog` is the
       only owner of adjudication, and introducing a second one would be worse than the
       defect" -- and OrderWatchdog is already started and already reconciles these same
       rows against the venue every 5-10s.

    This test fails the moment someone wires it into the lifespan, which forces that
    decision to be made explicitly.
    """
    import inspect

    import backend_app.main as main_module

    source = inspect.getsource(main_module)
    for marker in (
        "start_reconciliation_loop_all_tenants",
        "ExchangeReconciliationService",
    ):
        assert marker not in source, (
            f"{marker} now appears in backend_app/main.py. If reconciliation is being "
            f"hosted, host it in a worker service (INF-03), not the web process -- see "
            f"PAPER-04. If that decision has been taken deliberately, update this test "
            f"and record the reasoning in RECON-01."
        )
