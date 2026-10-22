"""DUP-01: the confirmation-lost give-up path, against a REAL PostgreSQL server.

WHY THIS FILE EXISTS
--------------------
DUP-01 is recorded in the ledger as FIXED_UNVERIFIED, and its own remaining_action says
why, in as many words:

    "The PostgreSQL-only raw SQL this session added (classify_unresolved_submissions,
    give_up_on_unresolved_submission, and resolve_stale_execution's new from_status
    parameter) was verified to fail safely on SQLite -- returns [] / False, never raises
    out of the watchdog loop ... but was NOT executed against a real PostgreSQL server:
    no DATABASE_URL / AERORA_TEST_DATABASE_URL is configured in this environment."

So the only assertions that existed were about the *refusal* path on SQLite. The actual
behaviour -- which rows the classifier selects, and whether the give-up UPDATE's `WHERE`
clause really closes the race it is documented to close -- had never run anywhere.

That matters more than usual for this particular code, because both guarantees are
expressed *only* in SQL and cannot be checked any other way:

* ``classify_unresolved_submissions`` uses ``make_interval(secs => :threshold)``, which is
  PostgreSQL-specific. On every other dialect the method returns ``[]`` by design, so a
  SQLite test can prove it is fail-safe but can never prove it selects correctly.
* ``give_up_on_unresolved_submission`` closes a concurrent-worker race "by the WHERE
  clause itself, not by locking" (its own docstring). A single-statement conditional
  UPDATE either matches or does not; nothing in Python observes it. The only way to show
  it holds is to run two real sessions against a real server.

WHAT A SKIP HERE MEANS
----------------------
Not a pass. Without ``AERORA_TEST_DATABASE_URL`` naming a postgres DSN these tests skip
and DUP-01's give-up path stays unverified -- the same convention every other
``*_postgres.py`` file in this directory uses, and the reason
tests/test_every_postgres_gated_file_runs_in_a_postgres_lane.py exists to make sure this
file is actually named in a PostgreSQL lane in 01-pr-check.yml.

WHAT THIS FILE DOES NOT COVER
-----------------------------
The half of DUP-01 that needs a venue. coinbase and kraken are declared UNSUPPORTED in
``exchange_executor.SANDBOX_MECHANISMS`` ("no sandbox exists"), so venue-side duplicate
protection for those two cannot be certified end to end by anything, here or elsewhere.
These tests cover the database side of the give-up design only, which is precisely the
part that was written and never exercised.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from backend_app.core.models.execution_record import (
    CONFIRMATION_LOST_AFTER_SECONDS,
    RECOVERY_CONFIRMATION_LOST,
    ExecutionRecordRepository,
    is_venue_confirmation_lost,
)

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. A SKIP "
        "here is NOT a pass -- DUP-01's confirmation-lost give-up stays unverified, and "
        "its classifier returns [] on every non-PostgreSQL dialect by design, so no "
        "SQLite run can stand in for this one."
    ),
)

pytestmark = pg_only

SYMBOL = "BTCUSDT"
PRICE = Decimal("1000")
SIZE = Decimal("1")


def _engine():
    # NullPool so each session is its own backend connection -- what makes the
    # concurrent-give-up test a real race rather than two cursors sharing one.
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
    """A tenant id that satisfies ``execution_records.tenant_id``'s FK chain."""
    tenant_id = str(uuid.uuid4())
    email = f"dup01-{tenant_id[:8]}@example.invalid"
    db.execute(
        text("INSERT INTO auth.users (id, email) VALUES (CAST(:t AS uuid), :e) "
             "ON CONFLICT DO NOTHING"),
        {"t": tenant_id, "e": email},
    )
    db.execute(
        text("INSERT INTO public.profiles (id, email) VALUES (CAST(:t AS uuid), :e) "
             "ON CONFLICT DO NOTHING"),
        {"t": tenant_id, "e": email},
    )
    db.commit()

    yield tenant_id

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


def _insert(
    session,
    tenant_id: str,
    execution_id: str,
    *,
    status: str = "UNKNOWN",
    age_seconds: int,
    submitted: bool = True,
    order_id: Optional[str] = None,
    symbol: str = SYMBOL,
    exchange_status: Optional[str] = None,
):
    """One ``execution_records`` row in a precise shape.

    ``created_at``/``updated_at`` are NOT NULL with no server default, and ``size``/
    ``price`` are ``character varying`` on this table -- both measured by
    test_trade07_stale_execution_recovery_postgres.py, whose insert helper this mirrors
    so the two files cannot disagree about the schema.
    """
    created = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    session.execute(
        text(
            "INSERT INTO public.execution_records "
            "(execution_id, tenant_id, strategy_id, symbol, side, size, price, status, "
            " order_id, submitted_at, exchange_status, created_at, updated_at) "
            "VALUES (:e, CAST(:t AS uuid), 'strat_dup01', :sym, 'buy', :size, :price, "
            f"        CAST('{status}' AS executionstatus), :oid, :sub, :xs, "
            "        :created, :created)"
        ),
        {
            "e": execution_id,
            "t": tenant_id,
            "sym": symbol,
            "size": str(SIZE),
            "price": str(PRICE),
            "oid": order_id,
            "sub": created if submitted else None,
            "xs": exchange_status,
            "created": created,
        },
    )
    session.commit()
    return execution_id


def _row(session, execution_id: str) -> dict:
    r = session.execute(
        text(
            "SELECT status::text AS status, order_id, exchange_status, "
            "       last_exchange_sync, submitted_at "
            "  FROM public.execution_records WHERE execution_id = :e"
        ),
        {"e": execution_id},
    ).fetchone()
    assert r is not None, f"{execution_id} vanished"
    return {
        "status": r.status,
        "order_id": r.order_id,
        "exchange_status": r.exchange_status,
        "last_exchange_sync": r.last_exchange_sync,
        "submitted_at": r.submitted_at,
    }


def _ids(classified) -> set:
    return {c["execution_id"] for c in classified}


OLD = CONFIRMATION_LOST_AFTER_SECONDS + 600
FRESH = 60


# ══════════════════════════════════════════════════════════════════════════════
#  1. classify_unresolved_submissions -- which rows it selects, for real
# ══════════════════════════════════════════════════════════════════════════════


def test_an_old_unknown_row_with_no_venue_order_id_is_classified(tenant, db):
    """The population the give-up exists to act on."""
    repo = ExecutionRecordRepository(db)
    eid = _insert(db, tenant, f"dup01-old-{uuid.uuid4().hex[:8]}", age_seconds=OLD)

    classified = repo.classify_unresolved_submissions(uuid.UUID(tenant))

    assert eid in _ids(classified), (
        "an UNKNOWN row with no order_id, older than the threshold, is exactly what "
        "this classifier is for"
    )
    entry = next(c for c in classified if c["execution_id"] == eid)
    assert entry["classification"] == repo.RECOVERY_AMBIGUOUS_SUBMISSION
    assert entry["age_seconds"] >= CONFIRMATION_LOST_AFTER_SECONDS, (
        f"age_seconds={entry['age_seconds']} should reflect real wall-clock age; it is "
        "computed by EXTRACT(EPOCH FROM (now() - submitted_at)) in SQL and has never "
        "been checked against a real server before"
    )


def test_a_fresh_unknown_row_is_not_classified(tenant, db):
    """Giving the venue more time is still the honest default inside the window."""
    repo = ExecutionRecordRepository(db)
    eid = _insert(db, tenant, f"dup01-fresh-{uuid.uuid4().hex[:8]}", age_seconds=FRESH)

    assert eid not in _ids(repo.classify_unresolved_submissions(uuid.UUID(tenant)))


def test_a_row_holding_a_venue_order_id_is_never_classified_however_old(tenant, db):
    """POSITIVE evidence of a real order must never be downgraded.

    The classifier's own docstring calls this out as a deliberate exclusion: an
    ``order_id`` means the venue holds an order (filled, or still resting).
    """
    repo = ExecutionRecordRepository(db)
    eid = _insert(
        db, tenant, f"dup01-hasid-{uuid.uuid4().hex[:8]}",
        age_seconds=OLD * 10, order_id="platform-order-1",
    )

    assert eid not in _ids(repo.classify_unresolved_submissions(uuid.UUID(tenant))), (
        "a row with a platform order id is positive evidence of a real order and is "
        "excluded regardless of age"
    )


@pytest.mark.parametrize("status", ["FAILED", "COMPLETED", "EXECUTING", "PENDING"])
def test_only_unknown_rows_are_classified(tenant, db, status):
    """In particular a row already given up on is terminal and is not re-offered."""
    repo = ExecutionRecordRepository(db)
    eid = _insert(
        db, tenant, f"dup01-{status.lower()}-{uuid.uuid4().hex[:8]}",
        status=status, age_seconds=OLD,
    )

    assert eid not in _ids(repo.classify_unresolved_submissions(uuid.UUID(tenant)))


def test_a_row_that_was_never_submitted_is_not_classified(tenant, db):
    """``submitted_at IS NULL`` is a pre-submission crash, a different population.

    That one belongs to TRADE-07's ``classify_stale_executions``; conflating them would
    let a row that never reached the venue be labelled confirmation-lost.
    """
    repo = ExecutionRecordRepository(db)
    eid = _insert(
        db, tenant, f"dup01-nosub-{uuid.uuid4().hex[:8]}",
        age_seconds=OLD, submitted=False,
    )

    assert eid not in _ids(repo.classify_unresolved_submissions(uuid.UUID(tenant)))


def test_the_threshold_is_honoured_as_given(tenant, db):
    """An explicit, short threshold selects a row the default would leave alone."""
    repo = ExecutionRecordRepository(db)
    eid = _insert(db, tenant, f"dup01-thresh-{uuid.uuid4().hex[:8]}", age_seconds=FRESH)

    assert eid not in _ids(repo.classify_unresolved_submissions(uuid.UUID(tenant)))
    assert eid in _ids(
        repo.classify_unresolved_submissions(
            uuid.UUID(tenant), confirmation_lost_after_seconds=1
        )
    ), "make_interval(secs => :threshold) must use the supplied threshold"


def test_the_tenant_filter_excludes_another_tenants_row(db):
    """Tenant scoping, in the query rather than in the caller."""
    repo = ExecutionRecordRepository(db)
    ids = []
    try:
        for _ in range(2):
            tid = str(uuid.uuid4())
            email = f"dup01-x-{tid[:8]}@example.invalid"
            db.execute(
                text("INSERT INTO auth.users (id, email) VALUES (CAST(:t AS uuid), :e) "
                     "ON CONFLICT DO NOTHING"), {"t": tid, "e": email})
            db.execute(
                text("INSERT INTO public.profiles (id, email) "
                     "VALUES (CAST(:t AS uuid), :e) ON CONFLICT DO NOTHING"),
                {"t": tid, "e": email})
            db.commit()
            eid = _insert(db, tid, f"dup01-t-{uuid.uuid4().hex[:8]}", age_seconds=OLD)
            ids.append((tid, eid))

        (tid_a, eid_a), (tid_b, eid_b) = ids
        got_a = _ids(repo.classify_unresolved_submissions(uuid.UUID(tid_a)))
        assert eid_a in got_a
        assert eid_b not in got_a, "tenant A's query returned tenant B's row"
    finally:
        db.rollback()
        for tid, _ in ids:
            db.execute(
                text("DELETE FROM public.execution_records "
                     "WHERE tenant_id = CAST(:t AS uuid)"), {"t": tid})
            db.execute(text("DELETE FROM public.profiles WHERE id = CAST(:t AS uuid)"),
                       {"t": tid})
            db.execute(text("DELETE FROM auth.users WHERE id = CAST(:t AS uuid)"),
                       {"t": tid})
        db.commit()


def test_results_are_oldest_first_and_respect_the_limit(tenant, db):
    """``ORDER BY submitted_at ASC`` + ``LIMIT`` -- the oldest silence is addressed first."""
    repo = ExecutionRecordRepository(db)
    oldest = _insert(db, tenant, f"dup01-a-{uuid.uuid4().hex[:8]}", age_seconds=OLD + 7200)
    middle = _insert(db, tenant, f"dup01-b-{uuid.uuid4().hex[:8]}", age_seconds=OLD + 3600)
    _insert(db, tenant, f"dup01-c-{uuid.uuid4().hex[:8]}", age_seconds=OLD + 60)

    everything = repo.classify_unresolved_submissions(uuid.UUID(tenant))
    assert [c["execution_id"] for c in everything][:2] == [oldest, middle]

    limited = repo.classify_unresolved_submissions(uuid.UUID(tenant), limit=1)
    assert [c["execution_id"] for c in limited] == [oldest]


# ══════════════════════════════════════════════════════════════════════════════
#  2. give_up_on_unresolved_submission -- the single terminal transition
# ══════════════════════════════════════════════════════════════════════════════


def test_the_give_up_writes_failed_with_the_confirmation_lost_marker(tenant, db):
    repo = ExecutionRecordRepository(db)
    eid = _insert(db, tenant, f"dup01-give-{uuid.uuid4().hex[:8]}", age_seconds=OLD)

    assert repo.give_up_on_unresolved_submission(
        eid, uuid.UUID(tenant), detail="venue lookup failed 3x"
    ) is True

    row = _row(db, eid)
    assert row["status"].upper() == "FAILED"
    assert RECOVERY_CONFIRMATION_LOST in (row["exchange_status"] or "")
    assert "venue lookup failed 3x" in row["exchange_status"]
    assert row["last_exchange_sync"] is not None, (
        "last_exchange_sync records when the platform stopped trying"
    )


def test_the_give_up_is_visible_from_an_independent_session(tenant, db):
    """It commits, so another worker sees the terminal state rather than re-adjudicating."""
    repo = ExecutionRecordRepository(db)
    eid = _insert(db, tenant, f"dup01-commit-{uuid.uuid4().hex[:8]}", age_seconds=OLD)

    assert repo.give_up_on_unresolved_submission(
        eid, uuid.UUID(tenant), detail="no executor available"
    ) is True

    engine = _engine()
    other = sessionmaker(bind=engine, future=True)()
    try:
        row = _row(other, eid)
        assert row["status"].upper() == "FAILED"
        assert RECOVERY_CONFIRMATION_LOST in (row["exchange_status"] or "")
    finally:
        other.close()
        engine.dispose()


def test_a_row_that_acquired_a_venue_order_id_is_refused_not_downgraded(tenant, db):
    """The documented race, exercised for real.

    "a row that meanwhile acquired a venue order id ... must never be downgraded to
    FAILED by a worker that is still looking at the old, order-id-less snapshot of it.
    That race is closed by the WHERE clause itself, not by locking."
    """
    repo = ExecutionRecordRepository(db)
    eid = _insert(
        db, tenant, f"dup01-raced-{uuid.uuid4().hex[:8]}",
        age_seconds=OLD, order_id="arrived-while-we-looked",
    )

    assert repo.give_up_on_unresolved_submission(
        eid, uuid.UUID(tenant), detail="stale snapshot"
    ) is False

    row = _row(db, eid)
    assert row["status"].upper() == "UNKNOWN", "the row must be left exactly as it was"
    assert row["exchange_status"] is None, "nothing may be written on the refusal path"


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "EXECUTING"])
def test_a_row_no_longer_unknown_is_refused(tenant, db, status):
    repo = ExecutionRecordRepository(db)
    eid = _insert(
        db, tenant, f"dup01-not-unknown-{uuid.uuid4().hex[:8]}",
        status=status, age_seconds=OLD,
    )

    assert repo.give_up_on_unresolved_submission(
        eid, uuid.UUID(tenant), detail="should not apply"
    ) is False
    assert _row(db, eid)["status"].upper() == status


def test_another_tenant_cannot_give_up_on_this_tenants_row(tenant, db):
    """``tenant_id`` is in the WHERE clause, so the UPDATE matches nothing cross-tenant."""
    repo = ExecutionRecordRepository(db)
    eid = _insert(db, tenant, f"dup01-xt-{uuid.uuid4().hex[:8]}", age_seconds=OLD)

    assert repo.give_up_on_unresolved_submission(
        eid, uuid.uuid4(), detail="wrong tenant"
    ) is False
    assert _row(db, eid)["status"].upper() == "UNKNOWN"


def test_exactly_one_of_two_concurrent_give_ups_wins(tenant, db):
    """Two real sessions, one row. The conditional UPDATE must admit exactly one."""
    eid = _insert(db, tenant, f"dup01-race-{uuid.uuid4().hex[:8]}", age_seconds=OLD)

    results: list[bool] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker(tag: str):
        engine = _engine()
        session = sessionmaker(bind=engine, future=True)()
        try:
            repo = ExecutionRecordRepository(session)
            barrier.wait(timeout=30)
            results.append(
                repo.give_up_on_unresolved_submission(
                    eid, uuid.UUID(tenant), detail=f"worker {tag}"
                )
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            session.close()
            engine.dispose()

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"a worker raised instead of returning a verdict: {errors}"
    assert sorted(results) == [False, True], (
        f"exactly one give-up must win; got {results}. Both winning would mean the "
        "status guard in the WHERE clause is not actually serialising the transition."
    )
    assert _row(db, eid)["status"].upper() == "FAILED"


def test_the_detail_is_truncated_rather_than_overflowing_the_column(tenant, db):
    repo = ExecutionRecordRepository(db)
    eid = _insert(db, tenant, f"dup01-long-{uuid.uuid4().hex[:8]}", age_seconds=OLD)

    assert repo.give_up_on_unresolved_submission(
        eid, uuid.UUID(tenant), detail="x" * 5000
    ) is True

    stored = _row(db, eid)["exchange_status"]
    assert len(stored) <= 500, f"exchange_status was stored at {len(stored)} chars"
    assert stored.startswith(RECOVERY_CONFIRMATION_LOST)


# ══════════════════════════════════════════════════════════════════════════════
#  3. The new terminal state must not be safer to retry against than UNKNOWN was
# ══════════════════════════════════════════════════════════════════════════════


def test_the_symbol_stays_frozen_after_the_give_up(tenant, db):
    """RT-17's guard must still match, or the give-up would unfreeze the symbol.

    ``give_up_on_unresolved_submission``'s own docstring: "the one thing that must never
    happen is for this new, more legible label to accidentally be SAFER to retry against
    than plain UNKNOWN was." This is that assertion, against real rows.
    """
    repo = ExecutionRecordRepository(db)
    eid = _insert(db, tenant, f"dup01-frozen-{uuid.uuid4().hex[:8]}", age_seconds=OLD)

    assert repo.has_unresolved_submission(uuid.UUID(tenant), SYMBOL) is True

    assert repo.give_up_on_unresolved_submission(
        eid, uuid.UUID(tenant), detail="exhausted"
    ) is True

    assert repo.has_unresolved_submission(uuid.UUID(tenant), SYMBOL) is True, (
        "the symbol became retryable the moment the row was given up on -- the give-up "
        "renames the frozen state, it does not clear it"
    )


def test_an_ordinary_failure_does_not_freeze_the_symbol(tenant, db):
    """Negative control: without the marker, FAILED must NOT match.

    Otherwise the previous test would pass for the wrong reason -- any FAILED row
    freezing the symbol rather than specifically a confirmation-lost one.
    """
    repo = ExecutionRecordRepository(db)
    _insert(
        db, tenant, f"dup01-plainfail-{uuid.uuid4().hex[:8]}",
        status="FAILED", age_seconds=OLD, exchange_status="insufficient balance",
    )

    assert repo.has_unresolved_submission(uuid.UUID(tenant), SYMBOL) is False


def test_the_stored_row_is_recognised_as_confirmation_lost(tenant, db):
    """``is_venue_confirmation_lost`` must identify what the UPDATE actually wrote."""
    repo = ExecutionRecordRepository(db)
    eid = _insert(db, tenant, f"dup01-recog-{uuid.uuid4().hex[:8]}", age_seconds=OLD)
    repo.give_up_on_unresolved_submission(eid, uuid.UUID(tenant), detail="gave up")

    record = repo.get_by_id(eid, uuid.UUID(tenant))
    assert record is not None
    assert is_venue_confirmation_lost(record) is True

    plain = _insert(
        db, tenant, f"dup01-plain-{uuid.uuid4().hex[:8]}",
        status="FAILED", age_seconds=OLD, exchange_status="rejected by venue",
    )
    assert is_venue_confirmation_lost(
        repo.get_by_id(plain, uuid.UUID(tenant))
    ) is False


def test_a_given_up_row_is_not_reclassified_on_the_next_sweep(tenant, db):
    """End to end: classify -> give up -> classify again returns nothing for that row.

    This is the loop OrderWatchdog.recheck_unresolved_submissions runs, and the property
    that stops a terminal row being adjudicated forever.
    """
    repo = ExecutionRecordRepository(db)
    eid = _insert(db, tenant, f"dup01-loop-{uuid.uuid4().hex[:8]}", age_seconds=OLD)

    assert eid in _ids(repo.classify_unresolved_submissions(uuid.UUID(tenant)))
    assert repo.give_up_on_unresolved_submission(
        eid, uuid.UUID(tenant), detail="first and only give-up"
    ) is True
    assert eid not in _ids(repo.classify_unresolved_submissions(uuid.UUID(tenant)))

    # And a second give-up on the same row is refused rather than rewriting the detail.
    assert repo.give_up_on_unresolved_submission(
        eid, uuid.UUID(tenant), detail="second attempt"
    ) is False
    assert "first and only give-up" in _row(db, eid)["exchange_status"]
