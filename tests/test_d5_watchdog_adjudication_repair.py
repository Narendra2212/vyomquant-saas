"""Phase 11B-D5 — W-1 and W-2: the watchdog can adjudicate, and its decision persists.

WHAT WAS WRONG
==============
Two independent defects in `backend_app/backend/order_watchdog.py`, both found by the
Phase 11B-D4 readiness audit and both invisible to a 14,184-test passing run because
nothing in `tests/` referenced either symbol.

W-1  `_get_executor_for_order` is a plain `def` and all three call sites awaited it.
     `await` on a non-awaitable raises `TypeError`. At the adjudication call site that
     exception was caught by a `try/except Exception` which logged "could not build an
     executor" and left `executor = None` -- so EVERY ambiguous submission took the
     "cannot ask" branch and `find_order_by_client_order_id` was never reached for any
     row. The venue lookup TRADE-07 built, and which the DUP-02 report names as the
     adjudication owner, was unreachable.

W-2  `_persist_order_update` was called twice and had NO DEFINITION anywhere in the
     application. `OrderWatchdog` has no base class, so both calls raised
     `AttributeError` into `_check_stale_orders`' handler. The two branches that call it
     assign `order.status = UNKNOWN` and `order.exchange_status = <reason>` first --
     and assigning an ORM attribute is not a commit. Whether the fail-safe reached the
     database depended on some later unrelated commit elsewhere in the class.

W-2b A third defect in the same block, reached only once W-2 was fixed: the no-executor
     alert read `order.user_id`, and `ExecutionRecordModel` declares no such column.
     Fixing persistence would have turned a swallowed `AttributeError` into a raised one.

HOW THIS FILE MEASURES
======================
* The structural tests parse this module's own source with `ast` and assert the
  declaration and the call sites agree **in both directions**. That is the shape of
  assertion that would have caught W-1 originally, and it is what makes a future change
  to an async executor factory a test failure rather than a silent regression.
* The W-1 behaviour tests inject a stub executor and assert the venue lookup was
  REACHED, with a recording repository double standing in for the real one so the
  decision is observable without a dialect-specific `resolve_stale_execution`.
* The oracle for "recovery never resubmits" is negative: the stub executor's order-placing
  methods raise if touched, so a resubmission fails the test rather than passing it
  quietly.
* The W-2 tests commit through a real session and read the row back through a SECOND,
  independent session, because the defect was precisely that an in-memory attribute
  assignment looked like a write. One variant runs against real PostgreSQL when the lane
  is configured, so the native `executionstatus` enum is exercised too.

WHAT IS NOT CLAIMED
===================
No venue is contacted and no venue behaviour is verified. These tests prove that control
reaches the lookup and that the decision persists. They do NOT prove that an executor is
available in production -- it is not: `app_state.exchange_router` does not exist and
`get_exchange_client` has no definition anywhere (recorded as W-1b in the D5 report and
in the docstring of `_get_executor_for_order`). An executor must still be injected.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import pathlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import order_watchdog as wd  # noqa: E402
from backend_app.backend.exchange_executor import OrderStatusResult  # noqa: E402
from backend_app.backend.order_watchdog import (OrderWatchdog,  # noqa: E402
                                                WatchdogConfig)
from backend_app.core.models.execution_record import (  # noqa: E402
    Base, ExecutionRecordModel, ExecutionStatus)

_WATCHDOG_SOURCE = pathlib.Path(wd.__file__)

#: Deliberately contains hex LETTERS, and that is not cosmetic.
#:
#: `execution_records.tenant_id` is `postgresql.UUID(as_uuid=True)`, which SQLite does not
#: recognise as a type name -- so SQLite gives the column NUMERIC affinity. A UUID whose
#: 32-character hex form is all digits (e.g. 11111111-1111-...) is then stored as a float
#: and comes back as one, and SQLAlchemy's result processor fails with
#: `'float' object has no attribute 'replace'` when it tries to rebuild the UUID. Measured
#: while writing this file: every database test here failed for that reason and none of
#: them for a reason in the code under test. A letter-bearing hex form keeps TEXT affinity.
TENANT = uuid.UUID("d5a7c0de-0000-4000-8000-00000000d5a7")
SYMBOL = "BTCUSDT"

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. "
        "A SKIP here is NOT a pass -- durability against the native executionstatus "
        "enum is unverified."
    ),
)


# ══════════════════════════════════════════════════════════════════════════════════════
#  STRUCTURAL — the W-1 invariant, asserted in both directions
# ══════════════════════════════════════════════════════════════════════════════════════

def _awaited_self_methods() -> Dict[str, List[int]]:
    """Every `await self.<name>(...)` in the watchdog module, name -> line numbers."""
    tree = ast.parse(_WATCHDOG_SOURCE.read_text(encoding="utf-8"))
    found: Dict[str, List[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
        ):
            found.setdefault(func.attr, []).append(node.lineno)
    return found


def test_the_executor_lookup_is_synchronous_and_no_call_site_awaits_it():
    """W-1, the exact defect. Both halves are asserted, so neither can drift alone."""
    method = OrderWatchdog._get_executor_for_order
    is_async = inspect.iscoroutinefunction(method)
    awaited = _awaited_self_methods().get("_get_executor_for_order", [])

    assert not is_async, (
        "_get_executor_for_order became a coroutine function. That is allowed ONLY "
        "together with restoring `await` at all three call sites -- see its docstring "
        "(W-1b): if an executor factory is ever wired and is async, the declaration and "
        "the call sites must change in ONE commit."
    )
    assert awaited == [], (
        f"_get_executor_for_order is a plain `def` and is awaited at line(s) {awaited}. "
        f"`await` on a non-awaitable raises TypeError, which at "
        f"`_adjudicate_ambiguous_execution`'s call site is swallowed and leaves "
        f"executor=None -- the W-1 defect, where no ambiguous row ever reaches the "
        f"venue lookup."
    )


def test_the_persist_helper_exists_is_synchronous_and_is_not_awaited():
    """W-2. It had no definition at all; both call sites awaited a method that was absent."""
    assert hasattr(OrderWatchdog, "_persist_order_update"), (
        "_persist_order_update is undefined again. Both call sites in "
        "`_refresh_order_status` would raise AttributeError, and the UNKNOWN fail-safe "
        "they exist to record would not reach the database."
    )
    assert not inspect.iscoroutinefunction(OrderWatchdog._persist_order_update), (
        "_persist_order_update became a coroutine function; Session.commit() is "
        "blocking, so declaring it async describes work that does not happen."
    )
    awaited = _awaited_self_methods().get("_persist_order_update", [])
    assert awaited == [], f"_persist_order_update is awaited at line(s) {awaited}"


def test_every_awaited_self_method_in_the_module_is_actually_a_coroutine():
    """The general invariant. This is the test that generalises W-1 past its one instance.

    Any `await self.foo(...)` where `foo` is not a coroutine function is the same defect
    in a new place, and the surrounding `except Exception` handlers in this module make
    that failure quiet rather than loud.
    """
    offenders = []
    for name, lines in sorted(_awaited_self_methods().items()):
        target = getattr(OrderWatchdog, name, None)
        if target is None:
            offenders.append(f"{name} (UNDEFINED) awaited at {lines}")
        elif not inspect.iscoroutinefunction(target):
            offenders.append(f"{name} (not a coroutine) awaited at {lines}")
    assert offenders == [], (
        "awaited non-awaitables in order_watchdog.py: " + "; ".join(offenders)
    )


def test_the_no_executor_branch_reads_no_column_the_model_lacks():
    """W-2b. `order.user_id` was read in that branch and the model has no such column."""
    assert not hasattr(ExecutionRecordModel, "user_id"), (
        "ExecutionRecordModel gained a user_id column; this test's premise needs review"
    )
    source = _WATCHDOG_SOURCE.read_text(encoding="utf-8")
    offending = [
        (n, line.strip())
        for n, line in enumerate(source.splitlines(), start=1)
        if "order.user_id" in line and not line.strip().startswith("#")
    ]
    assert offending == [], (
        f"a non-comment line reads order.user_id, which ExecutionRecordModel does not "
        f"declare: {offending}"
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  DOUBLES
# ══════════════════════════════════════════════════════════════════════════════════════

class _RecordingRepo:
    """Stands in for `ExecutionRecordRepository` so the DECISION is observable.

    `resolve_stale_execution` is PostgreSQL-only raw SQL and is already covered against a
    real server by tests/test_trade07_stale_execution_recovery_postgres.py. What these
    tests need is the adjudicator's choice, not the UPDATE's dialect.
    """

    RECOVERY_FAILED_BEFORE_SUBMISSION = "failed_before_submission"
    RECOVERY_AMBIGUOUS_SUBMISSION = "ambiguous_submission"
    RECOVERY_SUBMITTED_WITH_ORDER_ID = "submitted_with_order_id"

    def __init__(self) -> None:
        self.resolutions: List[Dict[str, Any]] = []

    def resolve_stale_execution(self, execution_id, tenant_id, **kwargs):
        self.resolutions.append(
            {"execution_id": execution_id, "tenant_id": tenant_id, **kwargs}
        )
        return True


class _StubExecutor:
    """A venue double that can be looked up and CANNOT be ordered through.

    Every order-placing name raises. "Recovery never resubmits" is therefore enforced by
    the double rather than asserted after the fact, so a resubmission cannot pass quietly.
    """

    def __init__(self, result: OrderStatusResult) -> None:
        self._result = result
        self.lookup_calls: List[tuple] = []

    async def find_order_by_client_order_id(self, client_order_id, symbol):
        self.lookup_calls.append((client_order_id, symbol))
        return self._result

    async def create_order(self, *a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("recovery placed an order: create_order was called")

    async def place_order(self, *a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("recovery placed an order: place_order was called")

    async def cancel_order(self, *a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("recovery cancelled an order: cancel_order was called")


def _row(classification: str = "ambiguous_submission") -> Dict[str, Any]:
    return {
        "execution_id": "exec_d5aaaaaaaaaaaaaa",
        "tenant_id": TENANT,
        "symbol": SYMBOL,
        "side": "buy",
        "size": "1",
        "price": "1000",
        "exchange_id": "binance",
        "order_id": None,
        "submitted_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        "created_at": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        "age_seconds": 1800.0,
        "classification": classification,
    }


@pytest.fixture
def watchdog_with(monkeypatch):
    """Build a watchdog whose executor lookup returns *executor*, and record its alerts.

    The lookup is replaced with a SYNCHRONOUS callable deliberately: if a future change
    re-adds `await` at the call site, these behaviour tests fail too, not only the
    structural ones.
    """
    def _build(executor, db=None):
        w = OrderWatchdog(db, WatchdogConfig())
        w._get_executor_for_order = lambda order: executor  # sync, as the real one is
        alerts: List[str] = []

        async def _record_alert(*args, **kwargs):
            alerts.append(args[-1] if args else kwargs.get("reason", ""))

        w._trigger_recovery_alert = _record_alert
        w._trigger_alert = _record_alert
        return w, alerts

    return _build


# ══════════════════════════════════════════════════════════════════════════════════════
#  W-1 — adjudication reaches the venue lookup
# ══════════════════════════════════════════════════════════════════════════════════════

def test_adjudication_reaches_the_venue_lookup(watchdog_with):
    """The headline W-1 assertion: `find_order_by_client_order_id` is actually called.

    Before the fix this was unreachable for every row, because the awaited sync lookup
    raised TypeError into a handler that left `executor = None`.
    """
    executor = _StubExecutor(
        OrderStatusResult(
            success=True, status="closed", filled_size="1", remaining_size="0",
            avg_price="1000", venue_order_id="VENUE-1",
        )
    )
    w, _alerts = watchdog_with(executor)
    repo = _RecordingRepo()
    row = _row()

    asyncio.run(w._adjudicate_ambiguous_execution(repo, row))

    assert executor.lookup_calls == [(row["execution_id"], SYMBOL)], (
        "the venue lookup was not reached; adjudication fell through to the "
        "'cannot ask' branch, which is the W-1 defect"
    )


def test_a_confirmed_venue_order_is_completed_with_the_venue_id(watchdog_with):
    """Positive control. 'Never resubmits' must not be satisfied by doing nothing at all."""
    executor = _StubExecutor(
        OrderStatusResult(
            success=True, status="closed", filled_size="1", remaining_size="0",
            avg_price="1000", venue_order_id="VENUE-1",
        )
    )
    w, _ = watchdog_with(executor)
    repo = _RecordingRepo()

    asyncio.run(w._adjudicate_ambiguous_execution(repo, _row()))

    assert len(repo.resolutions) == 1
    decision = repo.resolutions[0]
    assert decision["status"] == ExecutionStatus.COMPLETED
    assert decision["order_id"] == "VENUE-1", (
        "a fill was recorded without the venue's own identifier for it"
    )


def test_a_definite_not_found_fails_the_row(watchdog_with):
    """A venue that answers and has no such order converts ambiguity into a safe failure."""
    executor = _StubExecutor(
        OrderStatusResult(
            success=True, status="not_found", filled_size="0", remaining_size="0",
            avg_price=None,
        )
    )
    w, _ = watchdog_with(executor)
    repo = _RecordingRepo()

    asyncio.run(w._adjudicate_ambiguous_execution(repo, _row()))

    assert repo.resolutions[0]["status"] == ExecutionStatus.FAILED


def test_an_inconclusive_lookup_leaves_the_row_unknown_and_alerts(watchdog_with):
    """A lookup that could not establish the outcome must not become a determinate one."""
    executor = _StubExecutor(
        OrderStatusResult(
            success=False, status="error", filled_size="0", remaining_size="0",
            avg_price=None, error_message="venue unreachable",
        )
    )
    w, alerts = watchdog_with(executor)
    repo = _RecordingRepo()

    asyncio.run(w._adjudicate_ambiguous_execution(repo, _row()))

    assert executor.lookup_calls, "the lookup was not even attempted"
    assert repo.resolutions[0]["status"] == ExecutionStatus.UNKNOWN, (
        "an inconclusive venue lookup was resolved to a determinate status"
    )
    assert alerts, "an unresolved ambiguous submission raised no alert"


def test_a_venue_that_answers_without_an_order_id_stays_unknown(watchdog_with):
    """Knowing 'something exists' is not enough to attribute a fill."""
    executor = _StubExecutor(
        OrderStatusResult(
            success=True, status="closed", filled_size="1", remaining_size="0",
            avg_price="1000", venue_order_id=None,
        )
    )
    w, alerts = watchdog_with(executor)
    repo = _RecordingRepo()

    asyncio.run(w._adjudicate_ambiguous_execution(repo, _row()))

    assert repo.resolutions[0]["status"] == ExecutionStatus.UNKNOWN
    assert alerts


def test_an_order_still_open_at_the_venue_stays_unknown_and_records_the_id(watchdog_with):
    """Live exposure the platform had lost track of: recorded, alerted, never cancelled."""
    executor = _StubExecutor(
        OrderStatusResult(
            success=True, status="open", filled_size="0", remaining_size="1",
            avg_price=None, venue_order_id="VENUE-OPEN",
        )
    )
    w, alerts = watchdog_with(executor)
    repo = _RecordingRepo()

    asyncio.run(w._adjudicate_ambiguous_execution(repo, _row()))

    decision = repo.resolutions[0]
    assert decision["status"] == ExecutionStatus.UNKNOWN
    assert decision["order_id"] == "VENUE-OPEN"
    assert alerts


def test_a_missing_executor_still_refuses_rather_than_resubmitting(watchdog_with):
    """The pre-fix behaviour, now reached only for its real reason.

    With no executor the row must stay UNKNOWN and must not be resubmitted. This is the
    branch W-1 made universal; it is still correct when it is genuinely true.
    """
    w, alerts = watchdog_with(None)
    repo = _RecordingRepo()

    asyncio.run(w._adjudicate_ambiguous_execution(repo, _row()))

    assert repo.resolutions[0]["status"] == ExecutionStatus.UNKNOWN
    assert alerts


def test_adjudication_never_resubmits_on_any_lookup_outcome(watchdog_with):
    """One sweep over every outcome, with the double enforcing the oracle.

    `_StubExecutor` raises from `create_order`, `place_order` and `cancel_order`, so a
    resubmission anywhere in this sweep is an AssertionError rather than a silent pass.
    """
    outcomes = [
        OrderStatusResult(True, "closed", "1", "0", "1000", None, "V1"),
        OrderStatusResult(True, "not_found", "0", "0", None),
        OrderStatusResult(True, "canceled", "0", "0", None, None, "V2"),
        OrderStatusResult(True, "open", "0", "1", None, None, "V3"),
        OrderStatusResult(False, "error", "0", "0", None, "unreachable"),
    ]
    for outcome in outcomes:
        executor = _StubExecutor(outcome)
        w, _ = watchdog_with(executor)
        repo = _RecordingRepo()

        asyncio.run(w._adjudicate_ambiguous_execution(repo, _row()))

        assert len(repo.resolutions) == 1, f"no decision for {outcome.status}"
        assert repo.resolutions[0]["status"] in {
            ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.UNKNOWN
        }


# ══════════════════════════════════════════════════════════════════════════════════════
#  W-2 — the decision is committed, and a failed commit is visible
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sqlite_db(tmp_path):
    """A FILE-backed SQLite database, so a second session is a second connection.

    In-memory would share one connection and could not distinguish a committed row from a
    dirty attribute on an identity map -- which is exactly the confusion W-2 was.
    """
    url = f"sqlite:///{tmp_path / 'd5.db'}"
    engine = create_engine(url, future=True, poolclass=NullPool)
    ExecutionRecordModel.__table__.create(bind=engine, checkfirst=True)
    maker = sessionmaker(bind=engine, future=True)
    session = maker()
    try:
        yield session, maker
    finally:
        session.close()
        engine.dispose()


def _seed(session, status=ExecutionStatus.EXECUTING, execution_id="exec_d5persist01"):
    row = ExecutionRecordModel(
        execution_id=execution_id,
        tenant_id=TENANT,
        strategy_id="strat-d5",
        symbol=SYMBOL,
        side="buy",
        size="1",
        price="1000",
        status=status,
        order_id=None,
        submitted_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
    )
    session.add(row)
    session.commit()
    return row


def test_persist_commits_and_a_fresh_session_can_read_the_result(sqlite_db):
    """The core W-2 assertion: assigning an attribute is not persisting it."""
    session, maker = sqlite_db
    row = _seed(session)

    row.status = ExecutionStatus.UNKNOWN
    row.exchange_status = "unknown: executor unavailable"

    w = OrderWatchdog(session, WatchdogConfig())
    assert w._persist_order_update(row) is True

    other = maker()
    try:
        reread = other.get(ExecutionRecordModel, "exec_d5persist01")
        assert reread.status == ExecutionStatus.UNKNOWN, (
            "the UNKNOWN fail-safe was not durable; a second session still sees the old "
            "status, which is the W-2 defect"
        )
        assert reread.exchange_status == "unknown: executor unavailable", (
            "the operator-facing reason was not persisted"
        )
    finally:
        other.close()


def test_a_failed_commit_returns_false_and_leaves_the_previous_status(sqlite_db, monkeypatch):
    """A persistence failure must be reported, and must not half-apply."""
    session, maker = sqlite_db
    row = _seed(session, status=ExecutionStatus.EXECUTING)

    row.status = ExecutionStatus.UNKNOWN
    row.exchange_status = "unknown: should not survive"

    def _boom():
        raise RuntimeError("commit refused")

    monkeypatch.setattr(session, "commit", _boom)

    w = OrderWatchdog(session, WatchdogConfig())
    assert w._persist_order_update(row) is False, (
        "a failed commit reported success; the caller would announce a recovery that "
        "did not happen"
    )

    other = maker()
    try:
        reread = other.get(ExecutionRecordModel, "exec_d5persist01")
        assert reread.status == ExecutionStatus.EXECUTING, (
            "a failed persist still changed the stored status"
        )
    finally:
        other.close()


def test_the_no_executor_branch_persists_unknown_and_its_reason(sqlite_db, monkeypatch):
    """End to end through `_refresh_order_status`, the branch that calls the helper."""
    session, maker = sqlite_db
    row = _seed(session)

    w = OrderWatchdog(session, WatchdogConfig())
    w._get_executor_for_order = lambda order: None
    alerts: List[str] = []

    async def _record(order, reason):
        alerts.append(reason)

    w._trigger_alert = _record

    asyncio.run(w._refresh_order_status(row))

    other = maker()
    try:
        reread = other.get(ExecutionRecordModel, "exec_d5persist01")
        assert reread.status == ExecutionStatus.UNKNOWN
        assert reread.exchange_status and "executor unavailable" in reread.exchange_status
    finally:
        other.close()

    assert alerts, "the no-executor fail-safe raised no alert"
    assert wd._UNPERSISTED_SUFFIX not in alerts[0], (
        "the alert warned of a failed persist on a run that committed successfully"
    )


def test_a_failed_persist_is_surfaced_in_the_alert_not_reported_as_recovery(
    sqlite_db, monkeypatch
):
    """The brief's hard requirement: the failure reaches the operator-facing path."""
    session, maker = sqlite_db
    row = _seed(session)

    w = OrderWatchdog(session, WatchdogConfig())
    w._get_executor_for_order = lambda order: None
    alerts: List[str] = []

    async def _record(order, reason):
        alerts.append(reason)

    w._trigger_alert = _record
    monkeypatch.setattr(
        session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit refused"))
    )

    asyncio.run(w._refresh_order_status(row))

    assert alerts, "no alert at all was raised"
    assert wd._UNPERSISTED_SUFFIX in alerts[0], (
        "the alert announced an UNKNOWN recovery whose database write had failed; the "
        "operator would act on a status the row does not carry"
    )


def test_the_retries_exhausted_branch_also_persists(sqlite_db, monkeypatch):
    """The second `_persist_order_update` call site, driven through its own branch."""
    session, maker = sqlite_db
    row = _seed(session)

    class _AlwaysFails:
        async def get_order_status(self, exchange_order_id, symbol):
            return OrderStatusResult(
                success=False, status="error", filled_size="0", remaining_size="0",
                avg_price=None, error_message="venue down",
            )

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(wd.asyncio, "sleep", _no_sleep)

    w = OrderWatchdog(session, WatchdogConfig())
    w._get_executor_for_order = lambda order: _AlwaysFails()

    async def _record(order, reason):
        return None

    w._trigger_alert = _record

    asyncio.run(w._refresh_order_status(row))

    other = maker()
    try:
        reread = other.get(ExecutionRecordModel, "exec_d5persist01")
        assert reread.status == ExecutionStatus.UNKNOWN
        assert reread.exchange_status and "after retries" in reread.exchange_status
    finally:
        other.close()


# ══════════════════════════════════════════════════════════════════════════════════════
#  W-2 against the real engine — the native `executionstatus` enum
# ══════════════════════════════════════════════════════════════════════════════════════

@pg_only
def test_persist_is_durable_on_postgresql():
    """SQLite stores the enum as text; PostgreSQL has a native type with UPPERCASE labels.

    The lowercase/uppercase divergence between `ExecutionStatus`' values and the
    `executionstatus` labels is a documented defect family in this codebase (TRADE-06), so
    the durability proof is repeated on the engine that actually has the type.
    """
    engine = create_engine(_PG_URL, future=True, poolclass=NullPool)
    maker = sessionmaker(bind=engine, future=True)
    session = maker()
    execution_id = f"exec_d5pg{uuid.uuid4().hex[:12]}"
    # `execution_records.tenant_id` FKs through `public.profiles` to `auth.users`; the
    # same two inserts tests/test_trade07_stale_execution_recovery_postgres.py uses.
    tenant = str(uuid.uuid4())
    email = f"d5-w2-{tenant[:8]}@example.invalid"
    try:
        session.execute(
            text("INSERT INTO auth.users (id, email) VALUES (CAST(:t AS uuid), :e) "
                 "ON CONFLICT DO NOTHING"),
            {"t": tenant, "e": email},
        )
        session.execute(
            text("INSERT INTO public.profiles (id, email) VALUES (CAST(:t AS uuid), :e) "
                 "ON CONFLICT DO NOTHING"),
            {"t": tenant, "e": email},
        )
        session.execute(
            text(
                "INSERT INTO public.execution_records "
                "(execution_id, tenant_id, strategy_id, symbol, side, size, price, "
                " status, submitted_at, created_at, updated_at) "
                "VALUES (:e, CAST(:t AS uuid), 'strat-d5', :s, 'buy', '1', '1000', "
                " CAST('EXECUTING' AS executionstatus), now(), now(), now())"
            ),
            {"e": execution_id, "t": tenant, "s": SYMBOL},
        )
        session.commit()

        row = session.get(ExecutionRecordModel, execution_id)
        row.status = ExecutionStatus.UNKNOWN
        row.exchange_status = "unknown: pg durability probe"

        w = OrderWatchdog(session, WatchdogConfig())
        assert w._persist_order_update(row) is True

        other = maker()
        try:
            stored = other.execute(
                text(
                    "SELECT status::text AS s, exchange_status "
                    "FROM execution_records WHERE execution_id = :e"
                ),
                {"e": execution_id},
            ).fetchone()
            assert stored is not None, "the row vanished"
            assert stored.s.upper() == "UNKNOWN", (
                f"PostgreSQL stored status={stored.s!r}, not UNKNOWN"
            )
            assert stored.exchange_status == "unknown: pg durability probe"
        finally:
            other.close()
    finally:
        try:
            session.rollback()
            # Children first: execution_records FKs to profiles, profiles to auth.users.
            session.execute(
                text("DELETE FROM public.execution_records WHERE execution_id = :e"),
                {"e": execution_id},
            )
            session.execute(
                text("DELETE FROM public.profiles WHERE id = CAST(:t AS uuid)"),
                {"t": tenant},
            )
            session.execute(
                text("DELETE FROM auth.users WHERE id = CAST(:t AS uuid)"),
                {"t": tenant},
            )
            session.commit()
        except Exception:  # pragma: no cover - cleanup must not mask a failure
            session.rollback()
        session.close()
        engine.dispose()
