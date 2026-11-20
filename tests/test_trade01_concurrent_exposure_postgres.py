"""TRADE-01 — two distinct executions could both pass against the same exposure.

THE DEFECT, DEMONSTRATED BEFORE IT IS FIXED
-------------------------------------------
The only capital bound on the live order path was a read-then-compare in
`backend_app/core/execution_engine.py`::

    _equity   = Decimal(str(self.current_equity))      # PROCESS MEMORY
    _ceiling  = _equity * _max_leverage
    _notional = abs(size) * abs(price or 0)
    if _notional > _ceiling:  refuse

Nothing was reserved, decremented or locked, and there was no `FOR UPDATE`, advisory
lock or exposure table anywhere on the path. The only atomic predicate was
`claim_execution`, whose `UPDATE … WHERE status = 'PENDING'` guarantees that one worker
executes a GIVEN execution — it says nothing about two DIFFERENT executions.

And two orders are easy to make different:
`core/models/execution_record.generate_execution_id` folds quantity and price into the
canonical string, so two orders that differ in either get a different execution id AND a
different Redis lock key. They clear the identical ceiling independently and both
proceed. Two orders at 60% of the ceiling commit 120% of it.

:func:`test_the_per_order_ceiling_cannot_bound_a_pair` and
:func:`test_without_the_reservation_two_rows_both_reach_executing` demonstrate exactly
that, the second one against authoritative database state rather than arithmetic.

THE FIX
-------
`ExecutionRecordRepository.claim_execution_within_capacity` makes the claim and the
capacity decision ONE transaction, serialised per tenant by `pg_advisory_xact_lock`, with
in-flight notional summed from `execution_records` instead of read from memory. The
reservation IS the `executing` row — no new table, no second piece of state that can leak
if the process dies between writing it and claiming.

`execute_with_idempotency` calls it for live-bound orders; a declared paper or backtest
engine keeps the original claim.

WHAT THESE TESTS DO AND DO NOT PROVE
------------------------------------
Every concurrency assertion here uses TWO OR MORE SEPARATE POSTGRESQL CONNECTIONS
released together by a `threading.Barrier`, and then reads the outcome back out of the
database. None of it rests on a sequential unit test or an in-process lock — the brief is
explicit that such a result would not be a concurrency guarantee, and it would not be.

THE LIVE ORDER PATH IS NEVER ENABLED. The engine-level tests use an exchange stub that
raises on any call at all, so a regression that let an order through fails loudly instead
of reaching a venue. No credential, no venue connection, no real order.

LOCAL POSTGRESQL EVIDENCE. Production was never connected to in any phase of this audit.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Tuple

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason=(
        "TRADE-01 is a concurrency defect. The reservation is a PostgreSQL advisory lock "
        "and the proof is a genuine multi-connection race; SQLite has neither advisory "
        "locks nor concurrent writers. NOT a pass."
    ),
)

sa = pytest.importorskip("sqlalchemy")
psycopg2 = pytest.importorskip("psycopg2")
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from backend_app.core.models.execution_record import (  # noqa: E402
    ExecutionRecordRepository,
)

#: The account under test. Ceiling = equity x max_leverage = 100000 x 1 = 100000.
EQUITY = Decimal("100000")
PRICE = Decimal("1000")
#: 60% of the ceiling each: either order fits alone, the pair does not.
SIZE_60_PCT = Decimal("60")
#: 25% of the ceiling each: four fit, the fifth does not.
SIZE_25_PCT = Decimal("25")


# ══════════════════════════════════════════════════════════════════════════
#  FIXTURES — a real tenant, real rows, real separate connections
# ══════════════════════════════════════════════════════════════════════════


def _new_engine():
    """A fresh engine with NO pooling, so every session is a distinct backend process."""
    return create_engine(TEST_DATABASE_URL, poolclass=NullPool, future=True)


@pytest.fixture
def tenant():
    """A committed tenant. `execution_records.tenant_id` FKs to `profiles(id)`, which
    itself FKs to `auth.users(id)`, so both rows are required — measured, not assumed."""
    conn = psycopg2.connect(TEST_DATABASE_URL, connect_timeout=10)
    conn.autocommit = True
    tenant_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
            (tenant_id, f"trade01-{tenant_id[:8]}@example.invalid"),
        )
        cur.execute("INSERT INTO public.profiles (id) VALUES (%s)", (tenant_id,))
    try:
        yield tenant_id
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.execution_records WHERE tenant_id = %s", (tenant_id,)
            )
            cur.execute("DELETE FROM public.profiles WHERE id = %s", (tenant_id,))
            cur.execute("DELETE FROM auth.users WHERE id = %s", (tenant_id,))
        conn.close()


@pytest.fixture
def db():
    """A session for assertions, separate from any session under test."""
    engine = _new_engine()
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _insert_pending(
    session,
    tenant_id: str,
    execution_id: str,
    *,
    size: Decimal,
    price: Optional[Decimal] = PRICE,
    status: str = "PENDING",
    age_seconds: int = 0,
) -> str:
    """One `execution_records` row. `created_at`/`updated_at` are NOT NULL with no server
    default on this table, and `size`/`price` are `character varying` — both measured."""
    created = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    session.execute(
        text(
            "INSERT INTO public.execution_records "
            "(execution_id, tenant_id, strategy_id, symbol, side, size, price, status, "
            " created_at, updated_at) "
            "VALUES (:e, CAST(:t AS uuid), 'strat_trade01', 'BTCUSDT', 'buy', :size, "
            "        :price, CAST(:status AS executionstatus), :created, :created)"
        ),
        {
            "e": execution_id,
            "t": tenant_id,
            "size": str(size),
            "price": None if price is None else str(price),
            "status": status,
            "created": created,
        },
    )
    session.commit()
    return execution_id


def _statuses(session, tenant_id: str) -> dict:
    rows = session.execute(
        text(
            "SELECT execution_id, status::text FROM public.execution_records "
            " WHERE tenant_id = CAST(:t AS uuid) ORDER BY execution_id"
        ),
        {"t": tenant_id},
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _inflight_notional(session, tenant_id: str) -> Decimal:
    """What the database says is committed, by the same predicate the reservation uses."""
    value = session.execute(
        text(
            "SELECT COALESCE(SUM(ABS(size::numeric) * ABS(NULLIF(price,'')::numeric)), 0) "
            "  FROM public.execution_records "
            " WHERE tenant_id = CAST(:t AS uuid) "
            "   AND status = 'EXECUTING'"
        ),
        {"t": tenant_id},
    ).scalar()
    return Decimal(str(value))


def _unusable_inflight(session, tenant_id: str) -> int:
    """How many in-flight rows have no usable price, by the reservation's own predicate.

    TRADE-11. `_inflight_notional` above cannot express the finding, because the defect is
    precisely that such rows are INVISIBLE to that SUM: `size * NULL` is NULL and SUM
    skips it. Measuring them needs a second expression, which mirrors the
    `COUNT(*) FILTER (...)` the reservation now runs. `'0'` counts as unusable: a zero
    price is an unknown notional, not a zero one.
    """
    value = session.execute(
        text(
            "SELECT COUNT(*) FILTER ("
            "         WHERE price IS NULL OR btrim(price) = '' "
            "            OR NULLIF(btrim(price), '')::numeric = 0) "
            "  FROM public.execution_records "
            " WHERE tenant_id = CAST(:t AS uuid) "
            "   AND status = 'EXECUTING'"
        ),
        {"t": tenant_id},
    ).scalar()
    return int(value)


def _reserve(tenant_id: str, execution_id: str, size: Decimal, *, ceiling=None, stale=None):
    """Run one reservation on its OWN connection, and close it. Returns the 3-tuple."""
    engine = _new_engine()
    session = sessionmaker(bind=engine, future=True)()
    try:
        repo = ExecutionRecordRepository(session)
        return repo.claim_execution_within_capacity(
            execution_id,
            uuid.UUID(tenant_id),
            notional=size * PRICE,
            ceiling=EQUITY if ceiling is None else ceiling,
            stale_after_seconds=stale,
        )
    finally:
        session.close()
        engine.dispose()


# ══════════════════════════════════════════════════════════════════════════
#  1. THE DEFECT, DEMONSTRATED
# ══════════════════════════════════════════════════════════════════════════


def test_the_per_order_ceiling_cannot_bound_a_pair():
    """The arithmetic the per-order check performs, and what it cannot see.

    This is the whole of TRADE-01 in four lines: each order is under the ceiling, the
    pair is over it, and the check that runs per order has no way to know about the other
    one. Asserted against the engine's OWN bound-deriving helper, so it is the production
    ceiling and not a figure invented here.
    """
    from backend_app.core.execution_engine import ExecutionEngine

    engine = ExecutionEngine(portfolio_state={"total_equity": EQUITY})
    notional, ceiling, why = engine._capacity_bounds_for(size=SIZE_60_PCT, price=PRICE)

    assert ceiling is not None, why
    assert notional <= ceiling, "each order individually fits, which is the premise"
    assert notional * 2 > ceiling, (
        "the pair must exceed the ceiling or this demonstration is not demonstrating "
        "anything"
    )


def test_without_the_reservation_two_rows_both_reach_executing(tenant, db):
    """THE DEFECT AGAINST AUTHORITATIVE STATE — using the ORIGINAL `claim_execution`.

    `claim_execution` is still present and still correct for what it guarantees, so this
    is not a hypothetical reconstruction: it is the code path the live order path used.
    Two different execution ids, two separate connections, released together. Both claims
    succeed, and the database ends up holding 120% of the account's fundable notional in
    `EXECUTING` rows.

    This test PASSES. It documents the defect that `claim_execution_within_capacity`
    exists to close, and it is what makes the next section's result meaningful rather
    than tautological.
    """
    first = _insert_pending(db, tenant, "exec_t01_old_a", size=SIZE_60_PCT)
    second = _insert_pending(db, tenant, "exec_t01_old_b", size=SIZE_60_PCT)

    barrier = threading.Barrier(2)
    results: List[bool] = []
    errors: List[Exception] = []

    def worker(execution_id: str):
        engine = _new_engine()
        session = sessionmaker(bind=engine, future=True)()
        try:
            repo = ExecutionRecordRepository(session)
            barrier.wait(timeout=20)
            claimed, _ = repo.claim_execution(execution_id, uuid.UUID(tenant))
            results.append(claimed)
        except Exception as exc:  # noqa: BLE001 — recorded, then asserted on
            errors.append(exc)
        finally:
            session.close()
            engine.dispose()

    threads = [threading.Thread(target=worker, args=(e,)) for e in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)

    assert not errors, f"the demonstration itself failed: {errors}"
    assert results == [True, True] or sorted(results) == [True, True], (
        f"both claims were expected to succeed, which is the defect; got {results}"
    )

    statuses = _statuses(db, tenant)
    assert statuses == {first: "EXECUTING", second: "EXECUTING"}, statuses

    committed = _inflight_notional(db, tenant)
    assert committed > EQUITY, (
        f"the database holds {committed} of in-flight notional against a fundable "
        f"ceiling of {EQUITY}. `claim_execution` alone cannot bound two distinct "
        f"executions, which is TRADE-01."
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. THE RESERVATION — SUCCESS, REFUSAL, AND A REAL RACE
# ══════════════════════════════════════════════════════════════════════════


def test_a_single_order_within_capacity_is_claimed(tenant, db):
    """Successful reservation. Asserted first, so a fix that refuses everything is caught."""
    execution_id = _insert_pending(db, tenant, "exec_t01_ok", size=SIZE_60_PCT)

    claimed, record, refusal = _reserve(tenant, execution_id, SIZE_60_PCT)

    assert claimed is True, refusal
    assert refusal is None
    assert record is not None and record.execution_id == execution_id
    assert _statuses(db, tenant) == {execution_id: "EXECUTING"}


def test_an_order_that_would_breach_the_ceiling_is_refused(tenant, db):
    """Sequential, and deliberately so: this is the arithmetic, not the race.

    The race is :func:`test_two_concurrent_connections_cannot_both_reserve`. Both are
    needed — a reservation that serialises correctly but computes the wrong sum would
    pass the race test and fail this one.
    """
    first = _insert_pending(db, tenant, "exec_t01_cap_a", size=SIZE_60_PCT)
    second = _insert_pending(db, tenant, "exec_t01_cap_b", size=SIZE_60_PCT)

    claimed_a, _, _ = _reserve(tenant, first, SIZE_60_PCT)
    assert claimed_a is True

    claimed_b, record_b, refusal_b = _reserve(tenant, second, SIZE_60_PCT)

    assert claimed_b is False, "the second order was claimed despite breaching the ceiling"
    assert record_b is None
    assert "Capacity reservation refused" in (refusal_b or "")
    # The refusal names the figures, so an operator can see why.
    for figure in (str(SIZE_60_PCT * PRICE), str(EQUITY)):
        assert figure in refusal_b, f"the refusal does not name {figure}: {refusal_b}"

    statuses = _statuses(db, tenant)
    assert statuses[first] == "EXECUTING"
    assert statuses[second] == "PENDING", (
        "the refused order did not stay PENDING, so a refusal mutated state"
    )


def test_two_concurrent_connections_cannot_both_reserve(tenant, db):
    """THE TEST THIS FINDING IS ABOUT.

    Same setup as `test_without_the_reservation_two_rows_both_reach_executing` — two
    different execution ids, two separate PostgreSQL connections, released at the same
    instant by a barrier — but through the reservation. Exactly one may win, and the
    database must agree.
    """
    first = _insert_pending(db, tenant, "exec_t01_race_a", size=SIZE_60_PCT)
    second = _insert_pending(db, tenant, "exec_t01_race_b", size=SIZE_60_PCT)

    barrier = threading.Barrier(2)
    outcomes: List[Tuple[str, bool, Optional[str]]] = []
    errors: List[Exception] = []

    def worker(execution_id: str):
        engine = _new_engine()
        session = sessionmaker(bind=engine, future=True)()
        try:
            repo = ExecutionRecordRepository(session)
            barrier.wait(timeout=20)
            claimed, _, refusal = repo.claim_execution_within_capacity(
                execution_id,
                uuid.UUID(tenant),
                notional=SIZE_60_PCT * PRICE,
                ceiling=EQUITY,
            )
            outcomes.append((execution_id, claimed, refusal))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            session.close()
            engine.dispose()

    threads = [threading.Thread(target=worker, args=(e,)) for e in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"a worker raised: {errors}"
    assert len(outcomes) == 2, f"both workers must report an outcome: {outcomes}"

    winners = [e for e, claimed, _ in outcomes if claimed]
    losers = [(e, r) for e, claimed, r in outcomes if not claimed]

    assert len(winners) == 1, (
        f"{len(winners)} of 2 concurrent orders were admitted against one ceiling. "
        f"Outcomes: {outcomes}"
    )
    assert len(losers) == 1
    assert "Capacity reservation refused" in (losers[0][1] or ""), (
        f"the loser was rejected for the wrong reason: {losers[0][1]!r}"
    )

    # AUTHORITATIVE STATE, not the return values.
    statuses = _statuses(db, tenant)
    assert statuses[winners[0]] == "EXECUTING"
    assert statuses[losers[0][0]] == "PENDING"
    committed = _inflight_notional(db, tenant)
    assert committed <= EQUITY, (
        f"the database holds {committed} of in-flight notional against a ceiling of "
        f"{EQUITY}"
    )


def test_ten_concurrent_attempts_admit_only_what_fits(tenant, db):
    """Ten connections, four slots. Stronger than a two-way race: a lock that serialises
    but re-reads stale state would let a fifth through here."""
    ids = [
        _insert_pending(db, tenant, f"exec_t01_many_{i}", size=SIZE_25_PCT)
        for i in range(10)
    ]

    barrier = threading.Barrier(10)
    claimed_ids: List[str] = []
    lock = threading.Lock()
    errors: List[Exception] = []

    def worker(execution_id: str):
        engine = _new_engine()
        session = sessionmaker(bind=engine, future=True)()
        try:
            repo = ExecutionRecordRepository(session)
            barrier.wait(timeout=30)
            claimed, _, _ = repo.claim_execution_within_capacity(
                execution_id,
                uuid.UUID(tenant),
                notional=SIZE_25_PCT * PRICE,
                ceiling=EQUITY,
            )
            if claimed:
                with lock:
                    claimed_ids.append(execution_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            session.close()
            engine.dispose()

    threads = [threading.Thread(target=worker, args=(e,)) for e in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)

    assert not errors, f"workers raised: {errors}"

    # EXACTLY FOUR. Four orders of 25% fit a 100% ceiling and the fifth does not, so the
    # expected outcome is an exact number rather than a bound. An implementation that
    # serialised correctly but re-read stale state would admit more; one that counted
    # pending intents as exposure would admit none.
    statuses = _statuses(db, tenant)
    executing = [e for e, s in statuses.items() if s == "EXECUTING"]
    assert len(executing) == len(claimed_ids)
    assert len(executing) == 4, (
        f"{len(executing)} orders of 25% each were admitted against one ceiling "
        f"(expected exactly 4): {executing}"
    )
    assert _inflight_notional(db, tenant) <= EQUITY


def test_two_recorded_intents_do_not_block_each_other(tenant, db):
    """WHY ONLY `EXECUTING` IS COUNTED, asserted rather than left in a comment.

    The first version of the reservation summed `PENDING` rows too, on the reasoning that
    a pending order is an intent that has not been refused yet. That is wrong, and two of
    this file's own tests caught it: with two recorded intents of 60% each, EACH one sees
    the other's 60% and both are refused. The account can then never use capacity it
    genuinely has, and the deadlock does not clear until the stale cutoff expires.

    A `PENDING` row is recorded, not admitted — nothing is claimed and nothing is sent.
    Excluding it is safe because `PENDING -> EXECUTING` happens only in the claim, under
    the advisory lock, after the SUM: a second order must take the lock and be measured
    against the first order's `EXECUTING` row.
    """
    first = _insert_pending(db, tenant, "exec_t01_pend_a", size=SIZE_60_PCT)
    second = _insert_pending(db, tenant, "exec_t01_pend_b", size=SIZE_60_PCT)

    # Nothing is admitted yet, so nothing is held.
    assert _inflight_notional(db, tenant) == Decimal("0")

    claimed_first, _, refusal = _reserve(tenant, first, SIZE_60_PCT)
    assert claimed_first is True, (
        f"a recorded-but-unclaimed sibling order blocked this one: {refusal}"
    )

    # ...and now the sibling is correctly refused, because the first one IS admitted.
    claimed_second, _, refusal = _reserve(tenant, second, SIZE_60_PCT)
    assert claimed_second is False
    assert "Capacity reservation refused" in (refusal or "")
    assert _statuses(db, tenant) == {first: "EXECUTING", second: "PENDING"}


# ══════════════════════════════════════════════════════════════════════════
#  3. ROLLBACK, RETRY, STALE RECOVERY, IDEMPOTENCY
# ══════════════════════════════════════════════════════════════════════════


def test_a_refusal_leaks_no_capacity_and_a_later_order_still_fits(tenant, db):
    """A refusal must not consume the capacity it was refused for.

    The order that was too big is removed (the worker gave up), and a smaller one then
    succeeds — proving the refusal left nothing reserved behind it.
    """
    big = _insert_pending(db, tenant, "exec_t01_leak_big", size=Decimal("200"))
    claimed, _, refusal = _reserve(tenant, big, Decimal("200"))
    assert claimed is False and "Capacity reservation refused" in (refusal or "")

    db.execute(
        text("DELETE FROM public.execution_records WHERE execution_id = :e"), {"e": big}
    )
    db.commit()

    small = _insert_pending(db, tenant, "exec_t01_leak_small", size=SIZE_25_PCT)
    claimed, _, refusal = _reserve(tenant, small, SIZE_25_PCT)
    assert claimed is True, f"capacity leaked from the refused order: {refusal}"


def test_a_failed_execution_releases_its_capacity(tenant, db):
    """Rollback behaviour. An execution that ends FAILED must stop holding notional,
    or every failure would permanently shrink the account's usable capacity."""
    first = _insert_pending(db, tenant, "exec_t01_fail_a", size=SIZE_60_PCT)
    claimed, _, _ = _reserve(tenant, first, SIZE_60_PCT)
    assert claimed is True

    second = _insert_pending(db, tenant, "exec_t01_fail_b", size=SIZE_60_PCT)
    claimed, _, _ = _reserve(tenant, second, SIZE_60_PCT)
    assert claimed is False, "control: with the first still in flight there is no room"

    # The first worker's order failed.
    db.execute(
        text(
            "UPDATE public.execution_records SET status = 'FAILED' "
            " WHERE execution_id = :e"
        ),
        {"e": first},
    )
    db.commit()

    claimed, _, refusal = _reserve(tenant, second, SIZE_60_PCT)
    assert claimed is True, (
        f"a FAILED execution is still holding capacity, so repeated failures would "
        f"ratchet the account to zero: {refusal}"
    )


def test_a_completed_execution_does_not_hold_capacity(tenant, db):
    """COMPLETED rows are excluded deliberately: a filled order's exposure belongs to
    equity and position accounting, which `current_equity` already reflects. Counting it
    here as well would double-count it and shrink capacity monotonically forever."""
    _insert_pending(db, tenant, "exec_t01_done", size=SIZE_60_PCT, status="COMPLETED")
    target = _insert_pending(db, tenant, "exec_t01_after_done", size=SIZE_60_PCT)

    claimed, _, refusal = _reserve(tenant, target, SIZE_60_PCT)
    assert claimed is True, f"a COMPLETED row is still holding capacity: {refusal}"


def test_a_stale_executing_row_stops_holding_capacity(tenant, db):
    """Stale reservation recovery. A worker that crashed mid-execution leaves an
    `EXECUTING` row that will never reach a terminal status. Without a cutoff its
    notional would be held against the account forever."""
    _insert_pending(
        db, tenant, "exec_t01_stale", size=SIZE_60_PCT, status="EXECUTING",
        age_seconds=3600,
    )
    target = _insert_pending(db, tenant, "exec_t01_after_stale", size=SIZE_60_PCT)

    claimed, _, refusal = _reserve(tenant, target, SIZE_60_PCT)
    assert claimed is True, f"a one-hour-old EXECUTING row still holds capacity: {refusal}"


def test_a_fresh_executing_row_does_hold_capacity(tenant, db):
    """NEGATIVE CONTROL for stale recovery.

    Without this, the stale cutoff could be ignoring every `EXECUTING` row — which would
    make the test above pass while the reservation counted nothing at all.
    """
    _insert_pending(
        db, tenant, "exec_t01_fresh", size=SIZE_60_PCT, status="EXECUTING", age_seconds=1,
    )
    target = _insert_pending(db, tenant, "exec_t01_after_fresh", size=SIZE_60_PCT)

    claimed, _, refusal = _reserve(tenant, target, SIZE_60_PCT)
    assert claimed is False, (
        "a one-second-old EXECUTING order is in flight and its notional must be held; "
        "the stale cutoff is swallowing live exposure"
    )
    assert "Capacity reservation refused" in (refusal or "")


def test_reclaiming_the_same_execution_is_refused_not_duplicated(tenant, db):
    """Idempotency. A retry of the SAME execution must not claim twice.

    The reservation excludes the row being claimed from its own SUM — otherwise an order
    would be measured against itself — so this check matters: the `WHERE status =
    'PENDING'` predicate is what stops the second claim, and it has to still be there.
    """
    execution_id = _insert_pending(db, tenant, "exec_t01_idem", size=SIZE_25_PCT)

    first_claim, record, _ = _reserve(tenant, execution_id, SIZE_25_PCT)
    assert first_claim is True and record is not None

    second_claim, record_2, refusal = _reserve(tenant, execution_id, SIZE_25_PCT)
    assert second_claim is False, "the same execution was claimed twice"
    assert record_2 is None
    assert "Another worker acquired the execution claim first" in (refusal or ""), refusal

    assert _statuses(db, tenant) == {execution_id: "EXECUTING"}


def test_concurrent_retries_of_one_execution_claim_once(tenant, db):
    """The same thing under a real race: five connections, one execution id."""
    execution_id = _insert_pending(db, tenant, "exec_t01_idem_race", size=SIZE_25_PCT)

    barrier = threading.Barrier(5)
    wins: List[bool] = []
    lock = threading.Lock()

    def worker():
        engine = _new_engine()
        session = sessionmaker(bind=engine, future=True)()
        try:
            repo = ExecutionRecordRepository(session)
            barrier.wait(timeout=30)
            claimed, _, _ = repo.claim_execution_within_capacity(
                execution_id,
                uuid.UUID(tenant),
                notional=SIZE_25_PCT * PRICE,
                ceiling=EQUITY,
            )
            with lock:
                wins.append(claimed)
        finally:
            session.close()
            engine.dispose()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)

    assert wins.count(True) == 1, f"the same execution was claimed {wins.count(True)} times"


def test_the_advisory_lock_is_released_when_the_call_returns(tenant, db):
    """Transaction-scoped, so a crashed worker cannot hold an account's capacity shut.

    `pg_advisory_xact_lock` releases at COMMIT or ROLLBACK. Asserted from `pg_locks`
    rather than from the documentation.
    """
    execution_id = _insert_pending(db, tenant, "exec_t01_lock", size=SIZE_25_PCT)
    claimed, _, _ = _reserve(tenant, execution_id, SIZE_25_PCT)
    assert claimed is True

    held = db.execute(
        text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
    ).scalar()
    assert held == 0, (
        f"{held} advisory lock(s) are still held after the reservation returned. A worker "
        f"that died mid-execution would block the account indefinitely."
    )


def test_a_refused_reservation_also_releases_the_lock(tenant, db):
    """The refusal path rolls back, and rollback must release the lock too.

    The blocker is inserted as `EXECUTING`, not `PENDING`: only admitted exposure is
    counted, so a pending sibling would not cause a refusal and this test would be
    measuring the success path instead. (It did, on the first run.)
    """
    _insert_pending(
        db, tenant, "exec_t01_lock_r_a", size=SIZE_60_PCT, status="EXECUTING"
    )
    target = _insert_pending(db, tenant, "exec_t01_lock_r_b", size=SIZE_60_PCT)

    claimed, _, refusal = _reserve(tenant, target, SIZE_60_PCT)
    assert claimed is False, "the blocker did not block, so no refusal was exercised"
    assert "Capacity reservation refused" in (refusal or "")

    held = db.execute(
        text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
    ).scalar()
    assert held == 0, f"{held} advisory lock(s) survived a refused reservation"


def test_one_tenant_cannot_block_another(tenant, db):
    """The lock is keyed on the tenant, so a busy account must not serialise everyone.

    Also an isolation assertion: tenant A's in-flight notional must not count against
    tenant B's ceiling.
    """
    other = str(uuid.uuid4())
    conn = psycopg2.connect(TEST_DATABASE_URL, connect_timeout=10)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
                (other, f"trade01-other-{other[:8]}@example.invalid"),
            )
            cur.execute("INSERT INTO public.profiles (id) VALUES (%s)", (other,))

        # Tenant A fills its account.
        a_exec = _insert_pending(db, tenant, "exec_t01_iso_a", size=SIZE_60_PCT)
        claimed, _, _ = _reserve(tenant, a_exec, SIZE_60_PCT)
        assert claimed is True

        # Tenant B's identical order must still fit.
        b_exec = _insert_pending(db, other, "exec_t01_iso_b", size=SIZE_60_PCT)
        claimed, _, refusal = _reserve(other, b_exec, SIZE_60_PCT)
        assert claimed is True, (
            f"another tenant's exposure counted against this account's ceiling: {refusal}"
        )
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.execution_records WHERE tenant_id = %s", (other,)
            )
            cur.execute("DELETE FROM public.profiles WHERE id = %s", (other,))
            cur.execute("DELETE FROM auth.users WHERE id = %s", (other,))
        conn.close()


def test_a_priceless_in_flight_row_now_refuses_rather_than_vanishing(tenant, db):
    """TRADE-11, CLOSED. A row whose notional cannot be computed refuses the next order.

    HISTORY OF THIS TEST, BECAUSE IT USED TO ASSERT THE OPPOSITE
    -----------------------------------------------------------
    This was `test_a_market_order_with_no_price_is_documented_not_silently_zero`, and it
    pinned the blind spot as expected behaviour: a priceless `EXECUTING` row contributes
    `Decimal("0")` to committed exposure, and a subsequent 60% order is therefore
    admitted. Its failure message said closing it "must update TRADING_SAFETY_AUDIT.md's
    note about the market-order blind spot" -- which Phase 9 did.

    WHY THE OLD BEHAVIOUR WAS WRONG, NOT MERELY INCOMPLETE. `size * NULL` is NULL and SUM
    skips NULL, so the row disappeared from the aggregate entirely. Under-reporting
    committed exposure is the one direction a capital check must never fail in: it admits
    MORE risk than the ceiling allows, silently, and the gap widens with every such row.
    Two independent producers were measured -- a live market order (price '0' or NULL) and
    `exchange_reconciliation`'s ghost-order insert, which sets `price=None` explicitly.

    The row is still invisible to the SUM, and that is unavoidable arithmetic. What
    changed is that the reservation now COUNTS such rows and refuses while any exist,
    because the account's true exposure is unknowable. Both halves are asserted below, so
    the SUM's blindness is recorded as the reason the count exists rather than as a
    property anyone relies on.
    """
    _insert_pending(db, tenant, "exec_t01_mkt", size=Decimal("1000"), price=None,
                    status="EXECUTING")

    # Still true, and still the reason the count is needed: the SUM cannot see it.
    assert _inflight_notional(db, tenant) == Decimal("0")
    # ...and the reservation does.
    assert _unusable_inflight(db, tenant) == 1

    target = _insert_pending(db, tenant, "exec_t01_mkt_after", size=SIZE_60_PCT)
    claimed, _, refusal = _reserve(tenant, target, SIZE_60_PCT)
    assert claimed is False, (
        "an order was admitted while an in-flight execution had no usable price, so the "
        "account's committed exposure was unknown. That is TRADE-11 reopened."
    )
    assert "cannot be computed" in (refusal or ""), refusal


@pytest.mark.parametrize("bad_price", [None, "", "0"])
def test_every_unusable_price_form_refuses(tenant, db, bad_price):
    """NULL, empty and zero are all unusable, and for the same reason.

    `'0'` is the one worth stating: it does NOT null the SUM -- it contributes a real
    zero -- so it was the quietest of the three. A zero price is an unknown notional, not
    a zero-notional position.
    """
    _insert_pending(db, tenant, f"exec_t01_bp_{bad_price or 'none'}",
                    size=Decimal("1000"), price=bad_price, status="EXECUTING")
    target = _insert_pending(db, tenant, f"exec_t01_bp_after_{bad_price or 'none'}",
                             size=SIZE_25_PCT)

    claimed, _, refusal = _reserve(tenant, target, SIZE_25_PCT)
    assert claimed is False, f"price={bad_price!r} was treated as a usable notional"
    assert "cannot be computed" in (refusal or "")


def test_a_priced_in_flight_row_still_admits_what_fits(tenant, db):
    """NEGATIVE CONTROL for the two tests above.

    Without it, "refuses when a price is unusable" could mean "refuses whenever anything
    is in flight", which would be a liveness defect rather than a fix.
    """
    _insert_pending(db, tenant, "exec_t01_priced", size=SIZE_25_PCT, status="EXECUTING")
    assert _unusable_inflight(db, tenant) == 0

    target = _insert_pending(db, tenant, "exec_t01_priced_after", size=SIZE_25_PCT)
    claimed, _, refusal = _reserve(tenant, target, SIZE_25_PCT)
    assert claimed is True, f"a properly priced in-flight row blocked a fitting order: {refusal}"


# ══════════════════════════════════════════════════════════════════════════
#  4. THE ENGINE USES IT, AND THE LIVE PATH STAYS SHUT
# ══════════════════════════════════════════════════════════════════════════


class RefusingExchange:
    """Any venue call is a test failure. The live order path must never be enabled here."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def __getattr__(self, name: str):
        def _boom(*_a, **_k):
            self.calls.append(name)
            raise AssertionError(f"a test reached the venue: exchange.{name}")

        return _boom


def test_claim_execution_uses_its_postgresql_branch_and_does_not_fall_back(tenant, db):
    """TRADE-06, found by building the reservation on top of `claim_execution`'s predicate.

    `claim_execution` has a PostgreSQL `UPDATE … RETURNING` fast path and a
    dialect-agnostic ORM fallback. The fast path's predicate was
    `(status = 'PENDING' OR status = 'pending')` and its SET clause was
    `status = 'executing'` — and `execution_records.status` is the enum
    `executionstatus`, whose labels are UPPERCASE only. PostgreSQL cannot parse either
    lowercase literal as a value of that type, so the statement raised
    `invalid input value for enum executionstatus: "pending"` on EVERY call, the `except`
    caught it, and the ORM path silently did the work.

    The result was still correct, because the fallback is a single guarded UPDATE. But the
    atomic path the audit recorded as "the only atomic predicate on the live order path"
    had never executed, and every claim logged a warning.

    This asserts the fast path is now taken, by watching for the fallback's own warning.
    It is the reason the reservation deliberately has NO fallback: with one, this defect
    would have been masked there too.
    """
    import logging

    execution_id = _insert_pending(db, tenant, "exec_t01_branch", size=SIZE_25_PCT)

    messages: List[str] = []

    class _Catch(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    handler = _Catch()
    log = logging.getLogger("ExecutionRecord")
    log.addHandler(handler)
    previous_level = log.level
    log.setLevel(logging.DEBUG)
    try:
        engine = _new_engine()
        session = sessionmaker(bind=engine, future=True)()
        try:
            claimed, record = ExecutionRecordRepository(session).claim_execution(
                execution_id, uuid.UUID(tenant)
            )
        finally:
            session.close()
            engine.dispose()
    finally:
        log.removeHandler(handler)
        log.setLevel(previous_level)

    assert claimed is True, "the claim failed outright"
    assert record is not None

    fell_back = [m for m in messages if "falling back" in m]
    assert fell_back == [], (
        "claim_execution's PostgreSQL branch still raises and falls back to the ORM "
        "path:\n  " + "\n  ".join(m[:300] for m in fell_back)
    )
    assert _statuses(db, tenant) == {execution_id: "EXECUTING"}


def test_the_engine_derives_the_same_ceiling_as_the_per_order_check():
    """The cross-order bound and the per-order bound must agree on the ceiling.

    `_capacity_bounds_for` exists so the figure is computed in one place. If it drifted
    from `_execute_trade_internal`'s `_equity * _max_leverage`, one of the two gates would
    be enforcing a limit the other does not.
    """
    from backend_app.core.execution_engine import ExecutionEngine

    engine = ExecutionEngine(portfolio_state={"total_equity": EQUITY})
    notional, ceiling, _ = engine._capacity_bounds_for(size=SIZE_25_PCT, price=PRICE)

    expected_leverage = Decimal(str(getattr(engine, "max_leverage", 1) or 1))
    assert ceiling == Decimal(str(engine.current_equity)) * expected_leverage
    assert notional == SIZE_25_PCT * PRICE


@pytest.mark.parametrize("bad_equity", [None, "", "not-a-number", 0, Decimal("-1")])
def test_an_unreadable_equity_refuses_rather_than_reserving(bad_equity):
    """Fails closed, matching the per-order check's documented policy."""
    from backend_app.core.execution_engine import ExecutionEngine

    engine = ExecutionEngine(portfolio_state={"total_equity": EQUITY})
    engine.current_equity = bad_equity

    _, ceiling, why = engine._capacity_bounds_for(size=SIZE_25_PCT, price=PRICE)
    assert ceiling is None, f"equity {bad_equity!r} produced a usable ceiling"
    assert why, "the refusal carries no explanation"


def test_an_unparseable_size_refuses_rather_than_counting_as_zero():
    """A size that cannot be parsed must not become zero notional and pass."""
    from backend_app.core.execution_engine import ExecutionEngine

    engine = ExecutionEngine(portfolio_state={"total_equity": EQUITY})
    _, ceiling, why = engine._capacity_bounds_for(size="twelve", price=PRICE)
    assert ceiling is None and "usable number" in why


@pytest.mark.parametrize(
    "bad_price",
    [None, "", "   ", 0, 0.0, Decimal("0"), Decimal("-1"), -5, "abc",
     Decimal("NaN"), Decimal("Infinity")],
)
def test_an_unusable_price_refuses_rather_than_counting_as_zero(bad_price):
    """TRADE-11 on the INCOMING order, the mirror of the aggregate half.

    The expression this replaces was `abs(Decimal(str(price or 0)))`, and `price or 0` is
    the finding: `None`, `""`, `0`, `0.0` and `Decimal("0")` are all falsey, so every one
    produced `notional = 0` -- a reservation that consumes no capacity and therefore
    ALWAYS fits, whatever else is in flight. The pre-existing refusal fired only for a
    price that was non-numeric AND non-falsey (`"abc"`), i.e. never for an input that
    actually occurs.

    `Decimal("NaN")` and `Decimal("Infinity")` are included because both PARSE. NaN is
    the subtle one: every comparison against it is False, so `notional > ceiling` would
    be False and a NaN-priced order would pass the ceiling gate rather than fail it --
    the comparison silently inverts instead of erroring.

    Note there is no parametrised price test in the Phase 8 suite; the equivalent existed
    only for equity and size. That asymmetry is why this blind spot survived.
    """
    from backend_app.core.execution_engine import ExecutionEngine

    engine = ExecutionEngine(portfolio_state={"total_equity": EQUITY})
    notional, ceiling, why = engine._capacity_bounds_for(size=SIZE_25_PCT, price=bad_price)

    assert ceiling is None, (
        f"price {bad_price!r} produced a usable ceiling with notional {notional}, so an "
        f"order with no computable exposure would be admitted"
    )
    assert why, "the refusal carries no explanation"


def test_a_usable_price_still_produces_a_notional():
    """NEGATIVE CONTROL: the validation above does not refuse everything."""
    from backend_app.core.execution_engine import ExecutionEngine

    engine = ExecutionEngine(portfolio_state={"total_equity": EQUITY})
    notional, ceiling, why = engine._capacity_bounds_for(size=SIZE_25_PCT, price=PRICE)
    assert ceiling is not None, why
    assert notional == SIZE_25_PCT * PRICE


def test_the_non_postgres_policy_is_fail_closed_when_deployed(monkeypatch):
    """SQLite offers no advisory lock, so there is no serialisation point.

    The judgement reuses `core.runtime_env.local_state_is_acceptable` — the same detector
    `core/bot_ownership.claim_bot` uses for a missing claim store, so the two cannot
    drift. Deployed: refuse. Declared local: warn and fall back, because otherwise the
    whole test suite and every developer's SQLite setup would fail closed.
    """
    import backend_app.core.runtime_env as runtime_env

    class _FakeBind:
        class dialect:
            name = "sqlite"

    class _FakeSession:
        bind = _FakeBind()

        def rollback(self):
            pass

    repo = ExecutionRecordRepository(_FakeSession())

    monkeypatch.setattr(runtime_env, "local_state_is_acceptable", lambda: False)
    claimed, record, refusal = repo.claim_execution_within_capacity(
        "exec_sqlite", uuid.uuid4(), notional=Decimal("1"), ceiling=Decimal("100")
    )
    assert claimed is False and record is None
    assert "sqlite" in (refusal or "").lower()
    assert "Capacity reservation unavailable" in refusal


def test_the_non_postgres_policy_falls_back_in_a_declared_local_environment(monkeypatch):
    """The other half of the same policy, so the fail-closed branch is attributable."""
    import backend_app.core.runtime_env as runtime_env

    class _FakeBind:
        class dialect:
            name = "sqlite"

    calls: List[tuple] = []

    class _FakeSession:
        bind = _FakeBind()

        def rollback(self):
            pass

    repo = ExecutionRecordRepository(_FakeSession())
    monkeypatch.setattr(runtime_env, "local_state_is_acceptable", lambda: True)
    monkeypatch.setattr(
        repo, "claim_execution", lambda e, t: (calls.append((e, t)), (True, None))[1]
    )

    claimed, _, refusal = repo.claim_execution_within_capacity(
        "exec_sqlite_local", uuid.uuid4(), notional=Decimal("1"), ceiling=Decimal("100")
    )
    assert claimed is True and refusal is None
    assert len(calls) == 1, "the local fallback did not delegate to claim_execution"
