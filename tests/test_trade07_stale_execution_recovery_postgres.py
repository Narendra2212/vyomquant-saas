"""TRADE-07 — a stuck execution must be recoverable, and recovery must not duplicate it.

WHAT THE FINDING WAS, AND WHERE THE RECORDED MECHANISM WAS WRONG
===============================================================
Recorded: "the 300-second stale cutoff releases reserved capacity but leaves
`execution_records` rows in EXECUTING with no age-based state transition."

The conclusion is right and the mechanism was narrower than stated. An age-based janitor
DOES exist and IS running in production -- `OrderWatchdog`, started unconditionally from
`main.py`, 30s poll, 60s stale threshold, with a deliberate UNKNOWN fail-safe that refuses
to assume FILLED. What it cannot do is see the rows in question: `_find_stale_orders` and
`_find_open_orders` both filter **`order_id IS NOT NULL`**, and `order_id` is written only
at the COMPLETED transition -- after `create_order` has already returned. So a process that
died during submission left a row the rescue mechanism was structurally blind to.

THE DEEPER PROBLEM: NOTHING COULD TELL THE TWO CRASHES APART
------------------------------------------------------------
On a stuck row, `order_id`, `submitted_at`, `exchange_id`, `last_exchange_sync` and
`result` were ALL NULL. `place_order` performs no database write at all. So "crashed
before `create_order`" and "crashed after `create_order` but before its response" were
indistinguishable -- and they demand opposite actions:

* failing a row whose order the venue actually holds orphans live exposure, and the next
  signal opens a duplicate;
* retrying a row whose order the venue holds places a second real order.

And because `check_idempotent_execution` returns `skip_already_running` for EXECUTING with
no age test, the row also blocked that exact (tenant, strategy, symbol, bucket, side, qty,
price) **forever**.

`submitted_at` was declared in the model and in the migration and **never written by any
code**. Writing it immediately before the venue call -- in its own committed transaction,
so it survives the crash it exists to detect -- splits one undecidable state into two
decidable ones. That is the fix this file measures.

HOW THIS FILE MEASURES
======================
* Rows are inserted DIRECTLY into real PostgreSQL in each of the four shapes a crash can
  leave, so the classifier is exercised against authoritative state rather than a mock.
* The venue is a recorder. `create_order` existing on it at all is a test failure in the
  recovery tests: **recovery must never place an order**, so the oracle is that no write
  method was ever called.
* Each "it recovered correctly" assertion reads the row back and checks the status, the
  order id and the recorded reason -- not just that something changed.
* Every refusal has a paired positive control, so "never resubmits" cannot be satisfied by
  a recovery path that does nothing at all.

WHAT IS NOT CLAIMED
===================
* No real exchange is contacted. `find_order_by_client_order_id` is exercised against a
  fake ccxt client that implements the three lookup strategies, including a venue that
  supports none of them.
* The `pending -> approved` and position-accounting consequences of a recovered fill are
  alerted on, not fixed. A fill the platform did not know about needs position and PnL
  reconciliation, which is a separate concern from establishing that the fill happened.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import pathlib
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.models.execution_record import (  # noqa: E402
    ExecutionRecordRepository, ExecutionStatus)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Explicit PostgreSQL requirement; no local-DSN-file fallback. See
# tests/test_money06_referral_obligations_postgres.py for the measured reason.
_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. "
        "A SKIP here is NOT a pass -- stale-execution recovery is unverified."
    ),
)

SYMBOL = "BTCUSDT"
PRICE = Decimal("1000")
SIZE = Decimal("1")


def _engine():
    # NullPool so every session is its own backend connection, which is what makes the
    # concurrent-worker test a real race rather than two cursors on one connection.
    return create_engine(_PG_URL, future=True, poolclass=NullPool)


@pytest.fixture
def db():
    engine = _engine()
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def tenant(db):
    """A tenant whose id satisfies `execution_records.tenant_id`'s FK chain."""
    tenant_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO auth.users (id, email) VALUES (CAST(:t AS uuid), :e) "
             "ON CONFLICT DO NOTHING"),
        {"t": tenant_id, "e": f"p9-trade07-{tenant_id[:8]}@example.invalid"},
    )
    db.execute(
        text("INSERT INTO public.profiles (id, email) VALUES (CAST(:t AS uuid), :e) "
             "ON CONFLICT DO NOTHING"),
        {"t": tenant_id, "e": f"p9-trade07-{tenant_id[:8]}@example.invalid"},
    )
    db.commit()

    yield tenant_id

    # Children first: execution_records FKs to profiles (tenant) and to orders.
    db.rollback()  # a test may have left the session in an aborted transaction
    db.execute(
        text("DELETE FROM public.execution_records WHERE tenant_id = CAST(:t AS uuid)"),
        {"t": tenant_id},
    )
    db.execute(
        text("DELETE FROM public.orders WHERE tenant_id = CAST(:t AS uuid)"),
        {"t": tenant_id},
    )
    db.execute(text("DELETE FROM public.profiles WHERE id = CAST(:t AS uuid)"),
               {"t": tenant_id})
    db.execute(text("DELETE FROM auth.users WHERE id = CAST(:t AS uuid)"),
               {"t": tenant_id})
    db.commit()


def _insert_executing(
    session,
    tenant_id: str,
    execution_id: str,
    *,
    age_seconds: int,
    submitted: bool,
    order_id: Optional[str] = None,
    price: Optional[Decimal] = PRICE,
):
    """One `EXECUTING` row in a specified crash shape.

    `created_at`/`updated_at` are NOT NULL with no server default on this table, and
    `size`/`price` are `character varying` -- both measured.
    """
    created = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    session.execute(
        text(
            "INSERT INTO public.execution_records "
            "(execution_id, tenant_id, strategy_id, symbol, side, size, price, status, "
            " order_id, submitted_at, created_at, updated_at) "
            "VALUES (:e, CAST(:t AS uuid), 'strat_trade07', :sym, 'buy', :size, :price, "
            "        CAST('EXECUTING' AS executionstatus), :oid, :sub, :created, :created)"
        ),
        {
            "e": execution_id,
            "t": tenant_id,
            "sym": SYMBOL,
            "size": str(SIZE),
            "price": None if price is None else str(price),
            "oid": order_id,
            "sub": created if submitted else None,
            "created": created,
        },
    )
    session.commit()
    return execution_id


def _row(session, execution_id: str) -> dict:
    r = session.execute(
        text(
            "SELECT status::text AS status, order_id, venue_order_id, submitted_at, "
            "       exchange_status, last_exchange_sync "
            "  FROM public.execution_records WHERE execution_id = :e"
        ),
        {"e": execution_id},
    ).fetchone()
    assert r is not None, f"{execution_id} vanished"
    return {
        "status": r.status,
        "order_id": r.order_id,
        "venue_order_id": r.venue_order_id,
        "submitted_at": r.submitted_at,
        "exchange_status": r.exchange_status,
        "last_exchange_sync": r.last_exchange_sync,
    }


# ══════════════════════════════════════════════════════════════════════════
#  1. THE EVIDENCE SEAM
# ══════════════════════════════════════════════════════════════════════════


def test_submitted_at_is_written_by_the_product_now():
    """The column existed and nothing wrote it. That was the whole finding.

    AST-scoped to `mark_submitted`, so a mention of the column name in a comment or a
    docstring elsewhere cannot satisfy this. Three Phase 8 verifier checks were defeated
    by exactly that kind of substring match.
    """
    import backend_app.core.models.execution_record as mod

    tree = ast.parse(inspect.getsource(mod))
    writers = [
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "submitted_at" in (ast.get_source_segment(inspect.getsource(mod), n) or "")
    ]
    assert "mark_submitted" in writers, (
        "nothing writes submitted_at, so a crash during submission is still "
        "indistinguishable from a crash before it. That is TRADE-07."
    )


def test_the_engine_records_submission_before_calling_the_venue():
    """Placement is the entire value: after `place_order` it would prove nothing.

    Asserted on line order inside `_execute_trade_internal`'s own AST range.
    """
    import backend_app.core.execution_engine as mod

    source = inspect.getsource(mod)
    tree = ast.parse(source)
    target = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_execute_trade_internal"
    )

    mark_lines, place_lines = [], []
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "mark_submitted":
            mark_lines.append(node.lineno)
        elif name == "place_order":
            place_lines.append(node.lineno)

    assert mark_lines, "_execute_trade_internal does not record submission evidence"
    assert place_lines, "_execute_trade_internal no longer calls place_order"
    assert min(mark_lines) < min(place_lines), (
        f"mark_submitted is called at {mark_lines} but place_order at {place_lines}. "
        f"Evidence recorded after the venue call cannot survive a crash during it."
    )


@pg_only
def test_marking_submission_is_idempotent_and_keeps_the_first_instant(tenant, db):
    """A retry must not refresh the timestamp.

    If it did, an old submission would look recent and the recovery threshold would never
    adjudicate it -- the row would be permanently young.
    """
    execution_id = _insert_executing(
        db, tenant, "exec_t07_idem", age_seconds=1000, submitted=False
    )
    repo = ExecutionRecordRepository(db)

    assert repo.mark_submitted(execution_id, uuid.UUID(tenant), exchange_id="binance")
    first = _row(db, execution_id)["submitted_at"]
    assert first is not None

    assert repo.mark_submitted(execution_id, uuid.UUID(tenant), exchange_id="binance")
    assert _row(db, execution_id)["submitted_at"] == first, (
        "a second mark_submitted refreshed the timestamp, so a stale submission would "
        "look fresh and never be adjudicated"
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. CLASSIFICATION — THE FOUR CRASH SHAPES
# ══════════════════════════════════════════════════════════════════════════


@pg_only
def test_a_crash_before_submission_is_classified_as_such(tenant, db):
    execution_id = _insert_executing(
        db, tenant, "exec_t07_pre", age_seconds=2000, submitted=False
    )
    repo = ExecutionRecordRepository(db)
    rows = {
        r["execution_id"]: r
        for r in repo.classify_stale_executions(uuid.UUID(tenant))
    }
    assert rows[execution_id]["classification"] == repo.RECOVERY_FAILED_BEFORE_SUBMISSION


@pg_only
def test_a_crash_after_submission_is_classified_ambiguous(tenant, db):
    """The case that was undecidable before `submitted_at` was written."""
    execution_id = _insert_executing(
        db, tenant, "exec_t07_amb", age_seconds=2000, submitted=True
    )
    repo = ExecutionRecordRepository(db)
    rows = {
        r["execution_id"]: r
        for r in repo.classify_stale_executions(uuid.UUID(tenant))
    }
    assert rows[execution_id]["classification"] == repo.RECOVERY_AMBIGUOUS_SUBMISSION


@pg_only
def test_a_row_with_a_platform_order_id_is_left_to_the_existing_watchdog(tenant, db):
    """Two recovery paths must not both move one row.

    NOTE THE ORDER ID USED HERE. `execution_records.order_id` carries
    `FOREIGN KEY (order_id) REFERENCES orders(id)`, so an arbitrary string is refused --
    the first version of this test used `"VENUE-123"` and got a `ForeignKeyViolation`,
    which is how TRADE-12 was discovered. The fixture therefore creates a real
    `public.orders` row, which is what that column legitimately holds; the EXCHANGE's
    identifier lives in `venue_order_id` and has no foreign key.
    """
    platform_order_id = f"ord_p9_{uuid.uuid4().hex[:10]}"
    db.execute(
        text("INSERT INTO public.orders (id, tenant_id, created_at, updated_at) "
             "VALUES (:o, CAST(:t AS uuid), now(), now())"),
        {"o": platform_order_id, "t": tenant},
    )
    db.commit()

    execution_id = _insert_executing(
        db, tenant, "exec_t07_hasid", age_seconds=2000, submitted=True,
        order_id=platform_order_id,
    )
    repo = ExecutionRecordRepository(db)
    rows = {
        r["execution_id"]: r
        for r in repo.classify_stale_executions(uuid.UUID(tenant))
    }
    assert rows[execution_id]["classification"] == repo.RECOVERY_SUBMITTED_WITH_ORDER_ID


@pg_only
def test_a_fresh_execution_is_not_classified_at_all(tenant, db):
    """NEGATIVE CONTROL. A live submission in progress must not be adjudicated.

    Without this, "recovery works" could mean "recovery reaps everything", which would
    fail orders that are merely mid-flight.
    """
    execution_id = _insert_executing(
        db, tenant, "exec_t07_fresh", age_seconds=5, submitted=True
    )
    repo = ExecutionRecordRepository(db)
    ids = [r["execution_id"] for r in repo.classify_stale_executions(uuid.UUID(tenant))]
    assert execution_id not in ids, (
        "a 5-second-old execution was adjudicated as stale; a live submission would be "
        "declared dead while still in progress"
    )


@pg_only
def test_the_recovery_threshold_is_not_shorter_than_the_capacity_cutoff():
    """Ordering between the two thresholds is a safety property.

    Capacity release (300s) is a reversible accounting decision. Status adjudication is
    close to irreversible. Adjudicating first would mean a row could be declared dead
    while still being counted as live exposure.
    """
    assert (
        ExecutionRecordRepository.RECOVERY_STALE_AFTER_SECONDS
        >= ExecutionRecordRepository.CAPACITY_STALE_AFTER_SECONDS
    ), (
        f"recovery adjudicates at "
        f"{ExecutionRecordRepository.RECOVERY_STALE_AFTER_SECONDS}s but capacity is "
        f"released at {ExecutionRecordRepository.CAPACITY_STALE_AFTER_SECONDS}s"
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. RESOLUTION — WHAT RECOVERY IS ALLOWED TO WRITE
# ══════════════════════════════════════════════════════════════════════════


@pg_only
def test_the_venue_order_id_column_exists_and_is_unconstrained():
    """TRADE-12, discovered while building this recovery and not in the Phase 9 brief.

    `execution_records.order_id` carries
    `FOREIGN KEY (order_id) REFERENCES orders(id)` -- correctly, for an order THIS
    platform created. But the only value the application ever wrote there was the
    EXCHANGE's identifier, straight from `create_order`'s response, which has no reason
    to exist in `public.orders`. Measured:

        VENUE_NUMERIC_ID   = REJECTED  ForeignKeyViolation
        VENUE_STRING_ID    = REJECTED  ForeignKeyViolation
        EXISTING_ORDERS_ID = ACCEPTED
        EXECUTION_RECORDS_WITH_ORDER_ID = 0   (of 24 orders rows)

    So the COMPLETED write would raise 23503 on a real fill; and because `update_status`
    commits with no `try` at that call site, the aborted transaction then also broke the
    enclosing handler's FAILED write, leaving the row EXECUTING with `order_id IS NULL`
    -- the very state this file is about, with a venue order actually live.

    The venue id now has its own column with NO foreign key, and `order_id` keeps its own.
    """
    conn_engine = _engine()
    session = sessionmaker(bind=conn_engine, future=True)()
    try:
        col = session.execute(
            text(
                "SELECT data_type, is_nullable FROM information_schema.columns "
                " WHERE table_schema='public' AND table_name='execution_records' "
                "   AND column_name='venue_order_id'"
            )
        ).fetchone()
        assert col is not None, "execution_records.venue_order_id does not exist"
        assert col.data_type == "character varying"
        assert col.is_nullable == "YES", (
            "the venue id is unknown between submission and acknowledgement -- which is "
            "exactly the TRADE-07 window -- so the column must be nullable"
        )

        fks = session.execute(
            text(
                # `definition`, not `def` -- the latter is a Python keyword and
                # `row.def` is a SyntaxError.
                "SELECT conname, pg_get_constraintdef(oid) AS definition "
                "  FROM pg_constraint "
                " WHERE conrelid = to_regclass('public.execution_records') "
                "   AND contype = 'f'"
            )
        ).fetchall()
        on_venue = [f.conname for f in fks if "venue_order_id" in f.definition]
        assert on_venue == [], (
            f"venue_order_id acquired a foreign key ({on_venue}). The exchange assigns "
            f"this id, so no local table can be its parent -- that is TRADE-12 recreated."
        )
        assert any(f.conname == "fk_execution_records_order_id" for f in fks), (
            "fk_execution_records_order_id was dropped. The separate column exists so "
            "that constraint can be KEPT; dropping it would fix the symptom and preserve "
            "the modelling error."
        )
    finally:
        session.close()
        conn_engine.dispose()


@pg_only
def test_a_venue_order_id_is_now_recordable(tenant, db):
    """The write that used to be refused with 23503."""
    execution_id = _insert_executing(
        db, tenant, "exec_t07_venueid", age_seconds=2000, submitted=True
    )
    repo = ExecutionRecordRepository(db)
    assert repo.resolve_stale_execution(
        execution_id, uuid.UUID(tenant),
        status=ExecutionStatus.COMPLETED,
        classification=repo.RECOVERY_AMBIGUOUS_SUBMISSION,
        detail="recovered from the venue",
        order_id="1234567890",       # a venue-shaped id, which order_id would refuse
    )
    row = _row(db, execution_id)
    assert row["status"] == "COMPLETED"
    assert row["venue_order_id"] == "1234567890"
    assert row["order_id"] is None, (
        "the venue's identifier was written into order_id, which is foreign-keyed to "
        "public.orders. That is TRADE-12."
    )


def test_the_success_path_records_the_venue_id_in_the_unconstrained_column():
    """The pre-existing COMPLETED write, corrected.

    Asserted structurally because reaching it behaviourally requires a live fill, which
    this engagement must never produce.
    """
    import backend_app.core.execution_engine as mod

    source = inspect.getsource(mod)
    tree = ast.parse(source)

    completions = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) != "update_status":
            continue
        kwargs = {k.arg for k in node.keywords}
        seg = ast.get_source_segment(source, node) or ""
        if "COMPLETED" in seg:
            completions.append((node.lineno, kwargs))

    assert completions, "no COMPLETED update_status call found in execution_engine"
    for lineno, kwargs in completions:
        assert "venue_order_id" in kwargs, (
            f"the COMPLETED write at line {lineno} does not pass venue_order_id"
        )
        assert "order_id" not in kwargs, (
            f"the COMPLETED write at line {lineno} still passes the exchange's "
            f"identifier as order_id, which is foreign-keyed to public.orders and will "
            f"be refused with 23503 on a real fill (TRADE-12)"
        )


@pg_only
def test_a_pre_submission_crash_can_be_failed(tenant, db):
    execution_id = _insert_executing(
        db, tenant, "exec_t07_fail", age_seconds=2000, submitted=False
    )
    repo = ExecutionRecordRepository(db)
    assert repo.resolve_stale_execution(
        execution_id, uuid.UUID(tenant),
        status=ExecutionStatus.FAILED,
        classification=repo.RECOVERY_FAILED_BEFORE_SUBMISSION,
        detail="no submission evidence",
    )
    row = _row(db, execution_id)
    assert row["status"] == "FAILED"
    assert "failed_before_submission" in (row["exchange_status"] or ""), (
        "the reason was not persisted, so an operator cannot tell why the row was failed"
    )
    assert row["last_exchange_sync"] is not None


@pg_only
def test_recovery_cannot_record_a_fill_without_a_venue_order_id(tenant, db):
    """A fill is only recordable with the venue's own identifier for it.

    Without this guard, COMPLETED would be assertable from the ABSENCE of information --
    which is how a phantom fill enters the ledger and the position accounting.
    """
    execution_id = _insert_executing(
        db, tenant, "exec_t07_nofill", age_seconds=2000, submitted=True
    )
    repo = ExecutionRecordRepository(db)
    assert repo.resolve_stale_execution(
        execution_id, uuid.UUID(tenant),
        status=ExecutionStatus.COMPLETED,
        classification=repo.RECOVERY_AMBIGUOUS_SUBMISSION,
        detail="claiming a fill with no order id",
    ) is False
    assert _row(db, execution_id)["status"] == "EXECUTING", "the row was completed anyway"


@pg_only
@pytest.mark.parametrize("forbidden", [ExecutionStatus.PENDING, ExecutionStatus.EXECUTING])
def test_recovery_cannot_assign_a_non_terminal_status(tenant, db, forbidden):
    """The set of states recovery may assign is a safety property.

    PENDING in particular would be a resubmission by another name: it returns the row to
    the claimable pool, and the next signal would send a second order for an execution
    that may already be live at the venue.
    """
    execution_id = _insert_executing(
        db, tenant, f"exec_t07_bad_{forbidden.name}", age_seconds=2000, submitted=True
    )
    repo = ExecutionRecordRepository(db)
    assert repo.resolve_stale_execution(
        execution_id, uuid.UUID(tenant),
        status=forbidden,
        classification=repo.RECOVERY_AMBIGUOUS_SUBMISSION,
        detail="should be refused",
    ) is False
    assert _row(db, execution_id)["status"] == "EXECUTING"


@pg_only
def test_resolution_only_moves_a_row_that_is_still_executing(tenant, db):
    """A row a live worker has meanwhile completed must be left alone."""
    execution_id = _insert_executing(
        db, tenant, "exec_t07_raced", age_seconds=2000, submitted=False
    )
    db.execute(
        text("UPDATE public.execution_records SET status = 'COMPLETED' "
             " WHERE execution_id = :e"),
        {"e": execution_id},
    )
    db.commit()

    repo = ExecutionRecordRepository(db)
    assert repo.resolve_stale_execution(
        execution_id, uuid.UUID(tenant),
        status=ExecutionStatus.FAILED,
        classification=repo.RECOVERY_FAILED_BEFORE_SUBMISSION,
        detail="too late",
    ) is False
    assert _row(db, execution_id)["status"] == "COMPLETED", (
        "recovery overwrote a completed execution"
    )


@pg_only
def test_two_concurrent_recovery_workers_resolve_a_row_once(tenant, db):
    """The transition is one statement guarded on the current status, so a second worker
    finds nothing to do rather than writing over the first."""
    execution_id = _insert_executing(
        db, tenant, "exec_t07_race", age_seconds=2000, submitted=False
    )
    barrier = threading.Barrier(2)
    outcomes: List[bool] = []
    lock = threading.Lock()

    def worker():
        engine = _engine()
        session = sessionmaker(bind=engine, future=True)()
        try:
            repo = ExecutionRecordRepository(session)
            barrier.wait(timeout=30)
            got = repo.resolve_stale_execution(
                execution_id, uuid.UUID(tenant),
                status=ExecutionStatus.FAILED,
                classification=repo.RECOVERY_FAILED_BEFORE_SUBMISSION,
                detail="concurrent",
            )
            with lock:
                outcomes.append(got)
        finally:
            session.close()
            engine.dispose()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(outcomes) == 2
    assert sum(1 for o in outcomes if o) == 1, (
        f"both recovery workers claimed to resolve the row: {outcomes}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. VENUE RECONCILIATION BY clientOrderId
# ══════════════════════════════════════════════════════════════════════════


class _Recorder:
    """A fake ccxt client that records every call and performs no write.

    `create_order` exists ONLY so that a recovery path calling it is a test failure
    rather than an AttributeError. The oracle for "recovery never resubmits" is
    `writes == []`.
    """

    def __init__(self, *, open_orders=None, closed_orders=None,
                 direct: Optional[dict] = None, reachable: bool = True):
        self.open_orders = open_orders or []
        self.closed_orders = closed_orders or []
        self.direct = direct
        self.reachable = reachable
        self.writes: List[tuple] = []
        self.reads: List[str] = []

    async def create_order(self, *a, **k):
        self.writes.append(("create_order", a, k))
        raise AssertionError("recovery placed an order at the venue")

    async def cancel_order(self, *a, **k):
        self.writes.append(("cancel_order", a, k))
        raise AssertionError("recovery cancelled an order at the venue")

    async def fetch_order(self, order_id, symbol=None, params=None):
        self.reads.append("fetch_order")
        if not self.reachable:
            raise RuntimeError("venue unreachable")
        if self.direct is None:
            raise RuntimeError("order not found")
        return self.direct

    async def fetch_open_orders(self, symbol=None):
        self.reads.append("fetch_open_orders")
        if not self.reachable:
            raise RuntimeError("venue unreachable")
        return self.open_orders

    async def fetch_closed_orders(self, symbol=None):
        self.reads.append("fetch_closed_orders")
        if not self.reachable:
            raise RuntimeError("venue unreachable")
        return self.closed_orders


class _Normalizer:
    def format_symbol_for_ccxt(self, symbol):
        return str(symbol)


class _RateLimiter:
    async def acquire(self, _op):
        return None


def _executor(recorder: _Recorder):
    from backend_app.backend.exchange_executor import CCXTExchangeExecutor

    ex = object.__new__(CCXTExchangeExecutor)
    ex._exchange = recorder
    ex._connected = True
    ex.exchange_id = "binance"
    ex.sandbox = False
    ex.normalizer = _Normalizer()
    ex.rate_limiter = _RateLimiter()
    ex._api_key = "k"
    ex._api_secret = "s"
    ex._password = None
    return ex


def _run(coro):
    return asyncio.run(coro)


def test_a_direct_client_order_id_lookup_is_used_when_the_venue_supports_it():
    rec = _Recorder(direct={
        "id": "VENUE-9", "clientOrderId": "exec_abc", "status": "closed",
        "filled": 1, "remaining": 0, "average": 1000,
    })
    result = _run(_executor(rec).find_order_by_client_order_id("exec_abc", SYMBOL))

    assert result.success is True
    assert result.status == "closed"
    assert result.venue_order_id == "VENUE-9", "the venue order id was not returned"
    assert rec.writes == [], "a lookup wrote to the venue"


def test_a_resting_order_is_found_by_scanning_open_orders():
    """The most dangerous case to get wrong: live exposure the platform lost track of."""
    rec = _Recorder(
        direct=None,
        open_orders=[{
            "id": "VENUE-10", "clientOrderId": "exec_abc", "status": "open",
            "filled": 0, "remaining": 1, "average": None,
        }],
    )
    result = _run(_executor(rec).find_order_by_client_order_id("exec_abc", SYMBOL))

    assert result.success is True
    assert result.status == "open"
    assert result.venue_order_id == "VENUE-10"
    assert "fetch_open_orders" in rec.reads
    assert rec.writes == []


def test_a_filled_order_is_found_by_scanning_closed_orders():
    rec = _Recorder(
        direct=None,
        closed_orders=[{
            "id": "VENUE-11", "info": {"clientOrderId": "exec_abc"}, "status": "closed",
            "filled": 1, "remaining": 0, "average": 1001,
        }],
    )
    result = _run(_executor(rec).find_order_by_client_order_id("exec_abc", SYMBOL))
    assert result.success is True and result.venue_order_id == "VENUE-11"


def test_a_definite_absence_is_reported_as_success_not_as_an_error():
    """"The venue has no such order" is a USEFUL answer, not a failure.

    It converts an ambiguous row into a safely failable one, so it must be
    distinguishable from "we could not ask" -- which must not.
    """
    rec = _Recorder(direct=None, open_orders=[], closed_orders=[])
    result = _run(_executor(rec).find_order_by_client_order_id("exec_abc", SYMBOL))

    assert result.success is True
    assert result.status == "not_found"


def test_an_unreachable_venue_is_an_error_not_an_absence():
    """The distinction the whole recovery decision rests on.

    If an unreachable venue reported "not found", recovery would FAIL a row whose order
    may be live -- orphaning real exposure. This is KILL-01's shape in a different file.
    """
    rec = _Recorder(reachable=False)
    result = _run(_executor(rec).find_order_by_client_order_id("exec_abc", SYMBOL))

    assert result.success is False
    assert result.status == "error"
    assert "unknown" in (result.error_message or "").lower()


def test_a_non_matching_client_order_id_is_not_reported_as_a_match():
    """A scan must compare the id, not merely find any order on the symbol."""
    rec = _Recorder(
        direct=None,
        open_orders=[{"id": "VENUE-12", "clientOrderId": "someone_elses", "status": "open"}],
    )
    result = _run(_executor(rec).find_order_by_client_order_id("exec_abc", SYMBOL))
    assert result.status == "not_found", (
        "an unrelated order on the same symbol was reported as this execution's"
    )


# ══════════════════════════════════════════════════════════════════════════
#  5. THE RECOVERY NEVER RESUBMITS — ASSERTED STRUCTURALLY
# ══════════════════════════════════════════════════════════════════════════


def test_no_recovery_code_path_places_an_order():
    """The strongest form of "never resubmits": there is no such call to reach.

    AST-scoped to the three recovery methods. A behavioural test can only prove that the
    paths it exercised did not resubmit; this proves no path can, including ones no test
    covers.
    """
    import backend_app.backend.order_watchdog as mod

    source = inspect.getsource(mod)
    tree = ast.parse(source)
    recovery_methods = {
        "recover_unsubmitted_executions",
        "_adjudicate_ambiguous_execution",
        "_trigger_recovery_alert",
    }

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in recovery_methods:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            name = getattr(inner.func, "id", None) or getattr(inner.func, "attr", None)
            if name in {"place_order", "create_order", "place_order_with_idempotency",
                        "execute_with_idempotency", "_execute_trade_internal"}:
                offenders.append(f"{node.name}:{inner.lineno} calls {name}")

    assert offenders == [], (
        "a recovery path can place an order:\n  " + "\n  ".join(offenders)
        + "\nAn ambiguous execution may already be live at the venue; resubmitting it "
          "doubles the position."
    )


def test_the_recovery_is_wired_into_the_loop_that_actually_runs():
    """A recovery nobody calls is the KILL-03 defect in a new place.

    `OrderWatchdog` is started unconditionally from `main.py`, so being driven from its
    monitoring loop is what makes this reachable in production.
    """
    import backend_app.backend.order_watchdog as mod

    source = inspect.getsource(mod)
    tree = ast.parse(source)
    loop = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "_monitoring_loop"
    )
    called = {
        getattr(c.func, "id", None) or getattr(c.func, "attr", None)
        for c in ast.walk(loop) if isinstance(c, ast.Call)
    }
    assert "recover_unsubmitted_executions" in called, (
        "the stale-execution recovery is never called from the monitoring loop, so it "
        "guards nothing in production"
    )

    main_source = (_REPO_ROOT / "backend_app" / "main.py").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "OrderWatchdog" in main_source, (
        "main.py no longer starts the OrderWatchdog, so the recovery loop does not run"
    )


def test_the_existing_watchdog_filter_is_unchanged():
    """The `order_id IS NOT NULL` filter is CORRECT for what it does, and is kept.

    The gap was never that the filter was wrong -- `get_order_status` can only look an
    order up by venue id -- it was that no path handled the rows the filter excludes.
    Removing it would make the watchdog call `get_order_status(exchange_order_id=None)`
    for every stuck row.
    """
    import backend_app.backend.order_watchdog as mod

    source = inspect.getsource(mod)
    assert source.count("order_id.isnot(None)") >= 2, (
        "the watchdog's venue-order-id filter was removed; it would now try to reconcile "
        "rows that have no venue order id to reconcile against"
    )


def test_the_watchdog_only_assigns_columns_that_exist():
    """`exchange_status_message` is not a column on ExecutionRecordModel.

    Two watchdog branches assigned it, which created a transient instance attribute and
    was silently discarded at commit -- so the UNKNOWN status persisted but the reason an
    operator needs never did. Measured during this work and corrected.

    AST-SCOPED TO ASSIGNMENTS. The first version of this test asserted the string was
    absent from the module source and failed on the CORRECTIVE COMMENT that explains the
    fix -- the eighth time substring matching has produced a false positive across these
    two phases. What is actually forbidden is an `order.<attr> = ...` assignment to a
    name the model does not declare, so that is what is checked, and the prose may name
    the old attribute freely.
    """
    import backend_app.backend.order_watchdog as mod
    from backend_app.core.models.execution_record import ExecutionRecordModel

    tree = ast.parse(inspect.getsource(mod))
    declared = set(ExecutionRecordModel.__table__.columns.keys())

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            # Only assignments onto a variable called `order`, which is how the watchdog
            # names the ORM record throughout.
            if getattr(target.value, "id", None) != "order":
                continue
            if target.attr not in declared:
                offenders.append(f"line {node.lineno}: order.{target.attr}")

    assert offenders == [], (
        "the watchdog assigns attributes that are not columns on ExecutionRecordModel, "
        "so the values are silently discarded at commit:\n  " + "\n  ".join(offenders)
        + f"\nDeclared columns: {sorted(declared)}"
    )
    assert "exchange_status" in declared
    assert "venue_order_id" in declared, (
        "TRADE-12's venue_order_id column is missing from the model, so the exchange's "
        "identifier has nowhere to go that is not foreign-keyed to public.orders"
    )
