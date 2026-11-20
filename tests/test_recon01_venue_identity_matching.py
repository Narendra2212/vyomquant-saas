"""RECON-01 -- reconciliation must match orders by the id the VENUE assigned.

WHAT WAS FOUND
--------------
``reconcile_open_orders`` compared the two sides of the reconciliation by two different
keys. The exchange side was keyed by the venue's own order id (it has no other), while
every DB-side lookup used ``db_order.order_id`` alone:

    exchange_orders_by_id = {o['order_id']: o for o in exchange_orders}
    ...
    matching_db_order = self._find_db_order_by_exchange_id(db_orders, order_id)   # == order.order_id
    ...
    exchange_order = exchange_orders_by_id.get(db_order.order_id)

TRADE-12 split those two identifiers apart deliberately: ``execution_records.order_id``
carries ``FOREIGN KEY (order_id) REFERENCES orders(id)`` and holds THIS platform's order,
while ``venue_order_id`` holds the exchange's. The live execution engine therefore writes
the venue id to ``venue_order_id`` and leaves ``order_id`` NULL. RT-16 had already found
and fixed exactly this blind spot in ``OrderWatchdog``, which is why
``OrderWatchdog.venue_identifier`` exists -- and this module did not import it.

WHY THIS WAS WORSE THAN A MISSED CHECK
--------------------------------------
``_fetch_db_active_orders`` applies no identifier filter, so engine-created rows ARE in
``db_orders``. For each one, with the venue reporting that same order as open:

1. CASE 1 compared the venue's id against a NULL ``order_id``, found no match, and
   declared the tenant's own genuine open order a GHOST. ``_handle_ghost_order`` does not
   merely log -- it ``db.add``s and ``db.commit``s a SECOND ``execution_records`` row
   (``exec_ghost_<venue id>``, status EXECUTING, ``strategy_id="reconciled"``,
   ``price=None``) for an order already present under its real execution_id.
2. CASE 2, looking the same order up by NULL, missed it and routed it to
   ``_handle_missing_exchange_order`` as absent from the venue -- which then could not even
   ask ``fetch_order`` for an authoritative answer, because its guard was
   ``and db_order.order_id`` too.

So one real resting order produced a fabricated duplicate position row AND an
absent-from-venue verdict, on the same cycle. The duplicate is itself PENDING/EXECUTING
with ``order_id`` set, so it then also entered ``OrderWatchdog._find_open_orders``.

THIS WAS ARMED BY CLOSING THIS ENTRY'S OWN ENUMERATION BLOCKER
--------------------------------------------------------------
``enumerate_reconcilable_pairs`` WAS taught the TRADE-12 lesson -- it filters on
``venue_order_id`` first and says so in its docstring, citing ``venue_identifier`` by
name. The matching below it was not. Closing the enumeration blocker therefore widened the
population to precisely the rows that mismatch. Nothing exercised the ghost path, so no
test failed.

SCOPE OF THESE TESTS
--------------------
Mocks only: no venue is contacted and no order is placed. ``_fetch_exchange_orders`` is
replaced with a fixed list so that the identity comparison is the only variable, and
``_update_sync_timestamp`` is stubbed because it issues PostgreSQL's ``= ANY(:ids)``, which
SQLite cannot parse -- an unrelated dialect limitation that would otherwise mask the
property under test. These tests therefore claim only what they measure: which DB row the
reconciler pairs with which venue order, and what it writes as a result.
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

TENANT = uuid.uuid4()


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


def _row(session, *, venue_order_id=None, order_id=None, status=ExecutionStatus.EXECUTING,
         symbol="BTC/USDT", size="0.01", filled="0"):
    """An execution_records row. Defaults to the shape the LIVE ENGINE writes."""
    rec = ExecutionRecordModel(
        execution_id=f"exec-{uuid.uuid4().hex[:12]}",
        tenant_id=TENANT,
        strategy_id="strategy-under-test",  # NOT NULL on the model
        symbol=symbol,
        side="buy",
        size=size,
        filled_size=filled,
        status=status,
        exchange_id="binance",
        venue_order_id=venue_order_id,
        order_id=order_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(rec)
    session.commit()
    return rec


def _venue_order(order_id="V-1", *, symbol="BTC/USDT", size=0.01, filled=0.0,
                 status="open"):
    """One entry in the venue's open-orders list, in _fetch_exchange_orders' shape."""
    return {
        "order_id": order_id,
        "symbol": symbol,
        "side": "buy",
        "size": size,
        "filled_size": filled,
        "avg_price": None,
        "remaining_size": size - filled,
        "status": status,
    }


def _service(session, venue_orders):
    svc = ExchangeReconciliationService(db_session=session, exchange_client=object())

    async def _fake_fetch(tenant_id, exchange_id=None):
        return list(venue_orders)

    svc._fetch_exchange_orders = _fake_fetch
    svc._update_sync_timestamp = lambda ids: None  # PostgreSQL-only SQL; see module docstring
    return svc


def _reconcile(svc):
    return asyncio.run(svc.reconcile_open_orders(str(TENANT)))


def _ghost_rows(session):
    return (
        session.query(ExecutionRecordModel)
        .filter(ExecutionRecordModel.execution_id.like("exec_ghost_%"))
        .all()
    )


# ══════════════════════════════════════════════════════════════════════════════
#  1. The defect, stated as the property it violated
# ══════════════════════════════════════════════════════════════════════════════


def test_an_engine_created_order_open_at_the_venue_is_not_declared_a_ghost(db):
    """THE HEADLINE. order_id NULL + venue_order_id set is the normal live-engine row."""
    _row(db, venue_order_id="V-1", order_id=None)

    results = _reconcile(_service(db, [_venue_order("V-1")]))

    ghosts = [
        r for r in results
        if r.action == ReconciliationAction.REPORT_UNATTRIBUTED_ORDER
    ]
    assert ghosts == [], (
        "the tenant's own open order was classified as unattributable. The venue "
        "reported order V-1 and the DB row carries venue_order_id='V-1'; matching on "
        "order_id alone (NULL here) is what broke the pairing."
    )


def test_no_duplicate_execution_record_is_committed_for_a_genuine_order(db):
    """Originally: the ghost verdict was not advisory, it INSERTed and COMMITted a second
    row. That write is now gone entirely (RECON-01's policy answer), so this holds for two
    independent reasons -- the order matches, and nothing in this path writes a row at
    all. Kept because it is the property that mattered, and a future change that
    reintroduces a write for an unmatched order would still have to pass it."""
    _row(db, venue_order_id="V-1", order_id=None)
    before = db.query(ExecutionRecordModel).count()

    _reconcile(_service(db, [_venue_order("V-1")]))

    after = db.query(ExecutionRecordModel).count()
    assert after == before, (
        f"reconciliation committed {after - before} extra execution_records row(s) for an "
        f"order that was already present. Duplicated position state for a real order is a "
        f"fabrication, not a missed check."
    )
    assert _ghost_rows(db) == [], (
        "an exec_ghost_* row was written for the tenant's own genuine order"
    )


def test_the_order_is_not_simultaneously_reported_absent_from_the_venue(db):
    """CASE 2's mirror image of the same mismatch: looked up by NULL, so 'missing'."""
    _row(db, venue_order_id="V-1", order_id=None)

    results = _reconcile(_service(db, [_venue_order("V-1")]))

    for bad in (
        ReconciliationAction.MARK_FILLED,
        ReconciliationAction.MARK_CANCELLED,
    ):
        assert not [r for r in results if r.action == bad], (
            f"{bad} was decided for an order the venue reported as OPEN in the same cycle"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  2. The matcher itself
# ══════════════════════════════════════════════════════════════════════════════


def test_the_matcher_resolves_a_row_by_its_venue_order_id(db):
    row = _row(db, venue_order_id="V-1", order_id=None)
    svc = _service(db, [])

    assert svc._find_db_order_by_exchange_id([row], "V-1") is row


def test_a_legacy_row_carrying_only_order_id_still_matches(db):
    """Older recovery paths populate order_id. venue_identifier falls back to it."""
    row = _row(db, venue_order_id=None, order_id="LEGACY-9")
    svc = _service(db, [])

    assert svc._find_db_order_by_exchange_id([row], "LEGACY-9") is row


def test_the_matcher_refuses_to_pair_on_a_null_id(db):
    """Two rows that both lack an id are not 'the same order'."""
    row = _row(db, venue_order_id=None, order_id=None)
    svc = _service(db, [])

    assert svc._find_db_order_by_exchange_id([row], None) is None
    assert svc._find_db_order_by_exchange_id([row], "") is None


def test_venue_order_id_wins_when_a_row_carries_both(db):
    """TRADE-12: venue_order_id is the exchange's; order_id is this platform's."""
    row = _row(db, venue_order_id="V-1", order_id="PLATFORM-1")
    svc = _service(db, [])

    assert svc._find_db_order_by_exchange_id([row], "V-1") is row
    assert svc._find_db_order_by_exchange_id([row], "PLATFORM-1") is None


# ══════════════════════════════════════════════════════════════════════════════
#  3. A genuinely unknown venue order is STILL detected
# ══════════════════════════════════════════════════════════════════════════════


def test_a_genuinely_unattributable_venue_order_is_still_detected(db):
    """The fix must not disable detection -- only stop it firing on real rows.

    Nothing in the DB corresponds to V-UNKNOWN, which is what this path exists for
    (manual trading at the venue, or an order whose DB write was lost). Detection is the
    half that must survive; what changed is that it now REPORTS instead of writing.
    """
    _row(db, venue_order_id="V-1", order_id=None)

    results = _reconcile(_service(db, [_venue_order("V-1"), _venue_order("V-UNKNOWN")]))

    unattributed = [
        r for r in results
        if r.action == ReconciliationAction.REPORT_UNATTRIBUTED_ORDER
    ]
    assert len(unattributed) == 1, (
        f"expected exactly one unattributed order, got {[r.action for r in results]}"
    )
    assert unattributed[0].exchange_order_id == "V-UNKNOWN"
    # It has no row, so it has no execution id. A fabricated one is what the old
    # `exec_ghost_<venue id>` string was.
    assert unattributed[0].execution_id is None
    assert _ghost_rows(db) == [], "a row was written for an unattributable venue order"


# ══════════════════════════════════════════════════════════════════════════════
#  4. The authoritative venue query reaches the venue
# ══════════════════════════════════════════════════════════════════════════════


def test_the_authoritative_fetch_order_is_asked_about_the_venue_id(db):
    """BUG-FIX ORD-03's check was skipped entirely for engine rows.

    Its guard was ``and db_order.order_id``, so the row with the most at stake -- open at
    the venue, NULL platform id -- never got the definitive query, and its fate was decided
    from absence alone.
    """
    row = _row(db, venue_order_id="V-1", order_id=None)
    asked = []

    class _Venue:
        async def fetch_order(self, order_id, symbol=None):
            asked.append(order_id)
            return {"status": "open", "filled": 0, "average": 0}

    svc = ExchangeReconciliationService(db_session=db, exchange_client=_Venue())
    svc._update_sync_timestamp = lambda ids: None
    asyncio.run(svc._handle_missing_exchange_order(row))

    assert asked == ["V-1"], (
        f"the venue was asked about {asked!r}; gating on order_id meant it was asked "
        f"about nothing at all for engine-created rows"
    )


def test_the_result_reports_the_venue_id_not_the_platform_id(db):
    """``exchange_order_id`` is named for the exchange's id; it reported order_id."""
    row = _row(db, venue_order_id="V-1", order_id="PLATFORM-1")

    class _Venue:
        async def fetch_order(self, order_id, symbol=None):
            return {"status": "canceled", "filled": 0, "average": 0}

    svc = ExchangeReconciliationService(db_session=db, exchange_client=_Venue())
    svc._update_sync_timestamp = lambda ids: None
    result = asyncio.run(svc._handle_missing_exchange_order(row))

    assert result.exchange_order_id == "V-1", (
        f"reported {result.exchange_order_id!r}; PLATFORM-1 is this platform's own order "
        f"id and means nothing at the exchange"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  5. The module reuses the one definition of the rule
# ══════════════════════════════════════════════════════════════════════════════


def test_the_venue_id_rule_is_reused_and_not_copied():
    """One definition, so this module and OrderWatchdog cannot drift apart."""
    import inspect

    from backend_app.backend import exchange_reconciliation
    from backend_app.backend.order_watchdog import venue_identifier

    assert exchange_reconciliation.venue_identifier is venue_identifier, (
        "exchange_reconciliation must use OrderWatchdog's venue_identifier, not a copy"
    )

    source = inspect.getsource(exchange_reconciliation)
    assert "def venue_identifier" not in source, (
        "a second definition of the venue-id rule has appeared in this module"
    )


def test_the_factory_can_build_the_multi_tenant_safe_shape():
    """``vault=`` is the only multi-tenant-safe configuration, and the factory omitted it."""
    import inspect

    from backend_app.backend.exchange_reconciliation import \
        get_reconciliation_service

    params = inspect.signature(get_reconciliation_service).parameters
    assert "vault" in params, (
        "get_reconciliation_service cannot pass a vault, so the documented entry point "
        "can only build the single-shared-client shape RECON-01 names as the defect"
    )
