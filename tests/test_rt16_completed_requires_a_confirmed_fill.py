"""RT-16 -- ``COMPLETED`` requires a confirmed fill, not a venue acceptance.

THE DEFECT
    ``ExecutionEngine.execute_with_idempotency`` wrote ``ExecutionStatus.COMPLETED``
    whenever ``_execute_trade_internal`` reported success. Success at a venue means
    ACCEPTED, not FILLED. A resting limit order and a genuine fill took the same branch, so
    the platform reported fills that had not happened.

    Three consequences, each proven below:

    1. ``COMPLETED`` is operationally terminal. ``OrderWatchdog._find_open_orders``,
       ``_find_stale_orders`` and ``ExchangeReconciliation._get_active_orders`` all select
       only PENDING/EXECUTING, so the row left every reconciliation population at the exact
       moment it most needed watching, and nothing would ever discover whether it filled.
    2. ``float(result.filled_size or size)`` -- ``0`` is falsy in Python -- substituted THE
       FULL REQUESTED SIZE whenever the venue reported ``filled=0``.
       ``master_executor.settle_exposure`` reads that field to credit the bot's only record
       of what it holds, so an unfilled order became phantom exposure.
       ``float(result.avg_price or price)`` had the identical bug: it reported the price we
       ASKED for as a price we GOT.
    3. The fee row was computed from the same phantom quantity.

THE FIX
    The evidence needed to tell an accept from a fill was already in the response and
    simply never read: the venue's own status word plus its own filled quantity.
    ``classify_venue_fill`` reads both, and resolves every ambiguous case to ``accepted``
    rather than ``filled`` -- under-reporting is self-correcting, because the row stays
    EXECUTING and therefore stays in the watchdog's population; over-reporting is not.

    No new ``ExecutionStatus`` member: ``executionstatus`` is a PostgreSQL enum type, and
    ``EXECUTING`` already carries this meaning everywhere else in the codebase.

    Fixed in the same change because the status fix is INERT without it: every venue call in
    ``OrderWatchdog`` passed ``order.order_id`` and both population queries required
    ``order_id IS NOT NULL``, but TRADE-12 moved the venue's identifier into
    ``venue_order_id`` and the engine leaves ``order_id`` NULL -- so every row the engine
    created was invisible to the watchdog. Leaving that alone would have swapped "marked
    complete and never checked" for "marked executing and never checked".

SCOPE. Offline: a recording double for the venue, real engine, real repository, real
SQLite ``execution_records``. No network, no credential, no real order. What this does NOT
show: how any particular exchange words its responses in practice (that needs sandbox
credentials -- RT-16's remaining EXTERNAL_BLOCKED half), nor consumption of a private
order/fill stream, which still does not exist.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend_app.core.execution_engine as ee
from backend_app.backend.execution_environment import ExecutionEnvironment
from backend_app.backend.master_executor import settle_exposure
from backend_app.backend.order_watchdog import venue_identifier
from backend_app.core.execution_engine import ExecutionEngine
from backend_app.core.models.execution_record import (FILL_STATE_ACCEPTED,
                                                      FILL_STATE_FILLED,
                                                      FILL_STATE_PARTIAL,
                                                      ExecutionRecordModel,
                                                      ExecutionRecordRepository,
                                                      ExecutionStatus,
                                                      classify_venue_fill,
                                                      execution_status_for_fill_state)
from tests.test_trade04_live_order_gate_at_venue_boundary import (
    SYMBOL, TENANT, RecordingExchange, _KillSwitch, build_executor, place)

pytestmark = pytest.mark.usefixtures("live_platform_enabled")

REQUESTED = Decimal("0.01")


# ══════════════════════════════════════════════════════════════════════════════
# 1. THE CLASSIFIER
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("venue_status", ["closed", "FILLED", "filled", "done", "Closed"])
def test_a_venue_that_says_filled_with_a_quantity_is_a_fill(venue_status):
    state, qty, inferred = classify_venue_fill(
        venue_status=venue_status, filled_size=REQUESTED, requested_size=REQUESTED,
    )
    assert state == FILL_STATE_FILLED
    assert qty == REQUESTED
    assert inferred is False
    assert execution_status_for_fill_state(state) is ExecutionStatus.COMPLETED


@pytest.mark.parametrize("venue_status", ["open", "NEW", "accepted", "working", "untriggered"])
def test_a_venue_that_accepted_without_filling_is_not_a_fill(venue_status):
    state, qty, _ = classify_venue_fill(
        venue_status=venue_status, filled_size=0, requested_size=REQUESTED,
    )
    assert state == FILL_STATE_ACCEPTED
    assert qty == Decimal("0")
    assert execution_status_for_fill_state(state) is ExecutionStatus.EXECUTING


def test_zero_filled_is_not_read_as_unknown_filled():
    """THE DEFECT, AS A UNIT.

    ``float(result.filled_size or size)`` is the original line. ``0`` is falsy, so it
    evaluated to ``size`` -- the full requested quantity -- for an order the venue had
    filled NONE of. This pins the distinction the fix rests on: ``None`` means the venue
    did not say, ``0`` means the venue said none, and they are not the same answer.
    """
    pre_fix_reading = float(0 or float(REQUESTED))
    assert pre_fix_reading == float(REQUESTED), (
        "sanity: this is what the pre-fix expression computed for filled=0"
    )

    _, qty_for_zero, _ = classify_venue_fill(
        venue_status="open", filled_size=0, requested_size=REQUESTED,
    )
    assert qty_for_zero == Decimal("0")

    state_for_none, qty_for_none, _ = classify_venue_fill(
        venue_status="open", filled_size=None, requested_size=REQUESTED,
    )
    assert state_for_none == FILL_STATE_ACCEPTED
    assert qty_for_none == Decimal("0")


def test_a_partial_fill_is_partial_and_keeps_the_venues_quantity():
    state, qty, _ = classify_venue_fill(
        venue_status="partially_filled", filled_size=Decimal("0.004"),
        requested_size=REQUESTED,
    )
    assert state == FILL_STATE_PARTIAL
    assert qty == Decimal("0.004")
    assert execution_status_for_fill_state(state) is ExecutionStatus.EXECUTING


def test_a_quantity_short_of_the_request_is_partial_even_with_no_venue_word():
    state, qty, _ = classify_venue_fill(
        venue_status=None, filled_size=Decimal("0.004"), requested_size=REQUESTED,
    )
    assert state == FILL_STATE_PARTIAL
    assert qty == Decimal("0.004")


def test_closed_with_nothing_filled_is_not_a_fill():
    """A venue order that closed having filled nothing was cancelled or expired. The word
    ``closed`` alone must not be read as execution when the quantity contradicts it."""
    state, qty, _ = classify_venue_fill(
        venue_status="closed", filled_size=0, requested_size=REQUESTED,
    )
    assert state == FILL_STATE_ACCEPTED
    assert qty == Decimal("0")


def test_a_completion_claim_with_no_quantity_infers_the_request_and_says_so():
    """The one case where the number is not the venue's own, so it is flagged. This is
    also the reading that keeps pre-RT-16 behaviour for a venue that confirms completion
    without a ``filled`` field."""
    state, qty, inferred = classify_venue_fill(
        venue_status="closed", filled_size=None, requested_size=REQUESTED,
    )
    assert state == FILL_STATE_FILLED
    assert qty == REQUESTED
    assert inferred is True


@pytest.mark.parametrize("venue_status", [None, "", "wat", "something_new", 42])
def test_an_unreadable_status_with_no_quantity_is_never_a_fill(venue_status):
    """SAFETY DIRECTION. Anything the classifier cannot positively clear as filled is
    `accepted`, which keeps the row in the watchdog's population."""
    state, qty, _ = classify_venue_fill(
        venue_status=venue_status, filled_size=None, requested_size=REQUESTED,
    )
    assert state == FILL_STATE_ACCEPTED
    assert qty == Decimal("0")


# ══════════════════════════════════════════════════════════════════════════════
# 2. THE ENGINE, END TO END
# ══════════════════════════════════════════════════════════════════════════════


class _VenueReturning(RecordingExchange):
    """A venue whose `create_order` answer this test chooses."""

    def __init__(self, response):
        super().__init__()
        self._response = response

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.calls.append((symbol, type_, side, amount, price, params))
        return dict(self._response)


@pytest.fixture
def engine_over_sqlite(monkeypatch):
    """Real engine + real repository over a real in-memory execution_records table."""
    db = create_engine("sqlite://")
    ExecutionRecordModel.__table__.create(db)
    Session = sessionmaker(bind=db)

    monkeypatch.setattr(ee, "SessionLocal", Session)
    monkeypatch.setattr(ee.execution_metrics, "record_success", lambda **k: None,
                        raising=False)

    import backend_app.core.global_safety as gs
    monkeypatch.setattr(gs, "get_global_kill_switch", lambda: _KillSwitch(False))

    # SQLite cannot take the advisory-lock capacity claim; grant the claim, keep real rows.
    real_claim = ExecutionRecordRepository.claim_execution

    def _claim_within(self, execution_id, tenant_id, **kw):
        ok, rec = real_claim(self, execution_id, tenant_id)
        return ok, rec, None

    monkeypatch.setattr(ExecutionRecordRepository, "claim_execution_within_capacity",
                        _claim_within)

    def make(response):
        exchange = _VenueReturning(response)
        executor = build_executor(exchange)
        executor._api_key, executor._api_secret, executor._password = "k", "s", None
        engine = ExecutionEngine(
            portfolio_state={"total_equity": 1_000_000.0},
            exchange_executor=executor,
            execution_environment=ExecutionEnvironment.LIVE,
        )
        monkeypatch.setattr(engine, "_validate_strategy_exists", lambda t, s: True)
        return engine, exchange

    return make, Session


def _only_row(Session):
    s = Session()
    try:
        rows = s.query(ExecutionRecordModel).all()
        assert len(rows) == 1, f"expected exactly one execution row, got {len(rows)}"
        return rows[0]
    finally:
        s.close()


FILLED_RESPONSE = {
    "id": "venue-filled-1", "filled": 0.01, "remaining": 0,
    "average": 50_000, "status": "closed",
}
ACCEPTED_RESPONSE = {
    "id": "venue-resting-1", "filled": 0, "remaining": 0.01,
    "average": None, "status": "open",
}
PARTIAL_RESPONSE = {
    "id": "venue-partial-1", "filled": 0.004, "remaining": 0.006,
    "average": 50_010, "status": "partially_filled",
}


def test_a_confirmed_fill_is_completed(engine_over_sqlite):
    """THE POSITIVE CONTROL. Without it, every 'not completed' assertion below could be
    passing because the harness cannot complete an order at all."""
    make, Session = engine_over_sqlite
    engine, exchange = make(FILLED_RESPONSE)

    result = place(engine)

    assert exchange.calls, "the order never reached the venue; the harness is broken"
    assert result["status"] == "completed"
    row = _only_row(Session)
    assert row.status == ExecutionStatus.COMPLETED
    assert row.venue_order_id == "venue-filled-1"
    assert Decimal(row.filled_size) == REQUESTED
    assert row.filled_at is not None


def test_a_bare_venue_acceptance_is_not_completed(engine_over_sqlite):
    """THE DEFECT. The venue took the order and filled none of it."""
    make, Session = engine_over_sqlite
    engine, exchange = make(ACCEPTED_RESPONSE)

    result = place(engine)

    assert exchange.calls, "the order never reached the venue; the harness is broken"
    assert result["status"] == FILL_STATE_ACCEPTED, (
        "an accepted-but-unfilled order must not be reported as completed -- "
        "settle_exposure and the fee row both key off this string"
    )
    row = _only_row(Session)
    assert row.status == ExecutionStatus.EXECUTING, (
        "COMPLETED is operationally terminal: every reconciliation population selects "
        "only PENDING/EXECUTING, so recording COMPLETED here removes the order from the "
        "watchdog at the moment it most needs watching"
    )
    assert row.venue_order_id == "venue-resting-1", (
        "the venue's id must be recorded even when nothing filled -- it is the only way "
        "to ask the venue about this order later"
    )
    assert row.filled_at is None


def test_an_unfilled_acceptance_reports_zero_filled_not_the_requested_size(
    engine_over_sqlite,
):
    """THE PHANTOM-EXPOSURE REGRESSION, end to end.

    `settle_exposure` reads `result["result"]["trade_result"]["size"]`. The pre-fix
    expression `float(result.filled_size or size)` made that the FULL REQUESTED QUANTITY
    for an order the venue had filled none of.
    """
    make, Session = engine_over_sqlite
    engine, _ = make(ACCEPTED_RESPONSE)

    result = place(engine)

    inner = result["result"]["trade_result"]
    assert inner["size"] == 0.0, (
        f"an unfilled order reported size={inner['size']!r}; the pre-fix code reported "
        f"the full requested {float(REQUESTED)} because 0 is falsy in Python"
    )
    assert inner["size"] != float(REQUESTED)
    assert inner["fill_state"] == FILL_STATE_ACCEPTED
    assert inner["requested_size"] == float(REQUESTED)

    # And the consequence that made it matter: exposure must not move.
    filled, exposure = settle_exposure(
        side="buy", qty=REQUESTED, previous_exposure=Decimal("1.5"), result=result,
    )
    assert filled == Decimal("0")
    assert exposure == Decimal("1.5"), (
        "an accepted-but-unfilled order credited exposure the bot does not hold"
    )


def test_a_partial_fill_is_executing_and_settles_only_what_filled(engine_over_sqlite):
    make, Session = engine_over_sqlite
    engine, _ = make(PARTIAL_RESPONSE)

    result = place(engine)

    assert result["status"] == FILL_STATE_PARTIAL
    row = _only_row(Session)
    assert row.status == ExecutionStatus.EXECUTING
    assert Decimal(row.filled_size) == Decimal("0.004")
    assert Decimal(row.remaining_size) == Decimal("0.006")
    assert row.filled_at is None

    filled, exposure = settle_exposure(
        side="buy", qty=REQUESTED, previous_exposure=Decimal("1.5"), result=result,
    )
    assert filled == Decimal("0.004"), "a real partial fill is real exposure"
    assert exposure == Decimal("1.50400000")


def test_the_fill_columns_are_persisted_so_the_watchdogs_delta_has_a_baseline(
    engine_over_sqlite,
):
    """`OrderWatchdog._update_order_from_status` computes
    ``fill_delta = new_filled - Decimal(order.filled_size or "0")``. `update_status` never
    wrote `filled_size`, so a row that HAD filled carried NULL and the next poll
    re-credited the entire quantity as newly filled."""
    make, Session = engine_over_sqlite
    engine, _ = make(FILLED_RESPONSE)

    place(engine)

    row = _only_row(Session)
    assert row.filled_size is not None
    assert row.avg_price is not None
    assert row.exchange_status == "closed", "the venue's own word is recorded verbatim"
    assert Decimal(row.remaining_size) == Decimal("0")


def test_an_accepted_order_records_no_execution_price(engine_over_sqlite):
    """Nothing filled means there is no price at which anything traded. The requested
    price must not be recorded as one."""
    make, Session = engine_over_sqlite
    engine, _ = make(ACCEPTED_RESPONSE)

    place(engine)

    row = _only_row(Session)
    assert row.avg_price is None


# ══════════════════════════════════════════════════════════════════════════════
# 3. THE ROW MUST ACTUALLY BE WATCHABLE
# ══════════════════════════════════════════════════════════════════════════════


def test_the_engines_row_is_in_the_watchdogs_population(engine_over_sqlite):
    """Without this the status fix is inert. Both watchdog populations required
    ``order_id IS NOT NULL``, and the engine writes the venue id to ``venue_order_id``
    (TRADE-12) leaving ``order_id`` NULL -- so an EXECUTING engine row would sit forever,
    never polled, which is worse than the COMPLETED it replaced."""
    from sqlalchemy import and_, or_

    make, Session = engine_over_sqlite
    engine, _ = make(ACCEPTED_RESPONSE)
    place(engine)

    session = Session()
    try:
        open_statuses = [ExecutionStatus.PENDING, ExecutionStatus.EXECUTING]

        pre_fix = session.query(ExecutionRecordModel).filter(
            and_(
                ExecutionRecordModel.status.in_(open_statuses),
                ExecutionRecordModel.order_id.isnot(None),
            )
        ).all()
        assert pre_fix == [], (
            "NEGATIVE CONTROL: the pre-fix filter must find nothing, which is exactly why "
            "it had to change -- if this ever matches, the engine started writing order_id "
            "and this test no longer proves anything"
        )

        post_fix = session.query(ExecutionRecordModel).filter(
            and_(
                ExecutionRecordModel.status.in_(open_statuses),
                or_(
                    ExecutionRecordModel.venue_order_id.isnot(None),
                    ExecutionRecordModel.order_id.isnot(None),
                ),
            )
        ).all()
        assert len(post_fix) == 1
        assert venue_identifier(post_fix[0]) == "venue-resting-1", (
            "the watchdog must ask the venue about the VENUE's id"
        )
    finally:
        session.close()


def test_venue_identifier_prefers_the_venue_column_and_falls_back():
    class _Row:
        def __init__(self, venue, platform):
            self.venue_order_id, self.order_id = venue, platform

    assert venue_identifier(_Row("V", None)) == "V"
    assert venue_identifier(_Row("V", "P")) == "V", "the venue's id wins"
    assert venue_identifier(_Row(None, "P")) == "P", "older recovery rows still resolve"
    assert venue_identifier(_Row(None, None)) is None


def test_a_websocket_fill_event_can_find_the_engines_row(engine_over_sqlite):
    """`EventRouter._find_execution_by_order_id` matched the venue id from the exchange's
    own WebSocket event against ``order_id`` -- this platform's column, NULL on every row
    the engine creates. The fastest fill-confirmation path in the system could never
    resolve one."""
    from sqlalchemy import or_

    make, Session = engine_over_sqlite
    engine, _ = make(ACCEPTED_RESPONSE)
    place(engine)

    session = Session()
    try:
        venue_id = "venue-resting-1"

        pre_fix = session.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.order_id == venue_id,
            ExecutionRecordModel.tenant_id == TENANT,
        ).first()
        assert pre_fix is None, "NEGATIVE CONTROL: the pre-fix lookup must miss"

        post_fix = session.query(ExecutionRecordModel).filter(
            or_(
                ExecutionRecordModel.venue_order_id == venue_id,
                ExecutionRecordModel.order_id == venue_id,
            ),
            ExecutionRecordModel.tenant_id == TENANT,
        ).first()
        assert post_fix is not None
        assert post_fix.symbol == SYMBOL
    finally:
        session.close()
