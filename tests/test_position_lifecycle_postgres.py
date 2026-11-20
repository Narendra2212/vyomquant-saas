"""A position can be created, updated and closed through the ORM, against real PostgreSQL.

WHY THIS EXISTS. Three declarations of ``public.positions`` competed, and the database had
the one almost nothing used -- a blob shape ``(id, tenant_id, data json, created_at,
updated_at)`` from alembic ``d97ffff9c3bb``. The rich shape in ``core/position_model.py``
is what the application is actually built on: ``position_engine``, ``pnl_engine``,
``fee_engine``, ``metrics_system``, ``event_router`` and -- load-bearing for this launch
assessment -- ``core/consistency_checker.py``, which is reconciliation. Every query
through it failed:

    ProgrammingError: column positions.position_id does not exist

Alembic revision ``b2c3d4e5f6a7`` reshapes the table onto the rich declaration. This file
is the proof that the reshape is sufficient, not merely present: a schema that merely has
the right column names can still be unusable, and the only way to know is to run the
lifecycle the application runs.

WHAT IT DELIBERATELY DOES NOT DO. It does not assert PnL arithmetic. That is
``pnl_engine``'s contract and has its own tests; this is about whether the persistence
layer works at all. Mixing the two would mean a schema failure and a maths failure
produce the same red.

Gated on AERORA_TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import uuid

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_DATABASE_URL.startswith("postgresql"),
    reason=(
        "AERORA_TEST_DATABASE_URL is unset or not PostgreSQL. The enum types and the "
        "reshaped table are PostgreSQL objects."
    ),
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def workspace(engine):
    """A tenant and a session together, torn down in the one order that cannot deadlock.

    WHY THIS IS ONE FIXTURE AND NOT TWO. It was two -- `tenant` and `session` -- and the
    teardown order deadlocked the test run until the outer timeout killed the server.
    `PositionRepository.create` commits, so the rows survive the session; the `tenant`
    teardown then issued `DELETE FROM positions WHERE tenant_id = ...` while the session
    fixture had not yet been torn down and still held an open transaction with row locks
    on exactly those rows. The DELETE waited for a lock the same test process was holding
    and never released, which is a deadlock pytest cannot see: no error, no failure, just
    a run that stops.
    
    Combining them makes the order explicit and unarguable: close the session first,
    which releases every lock it holds, and only then delete. A `lock_timeout` is set on
    the cleanup as well, so if anything else ever holds one of these rows the cleanup
    fails loudly in two seconds instead of hanging.
    """
    tid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:i, :e)"),
            {"i": tid, "e": f"{tid[:8]}@positions.test"},
        )
        conn.execute(text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": tid})

    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    sess = factory()
    try:
        yield tid, sess
    finally:
        # Order is the whole point: locks go first.
        try:
            sess.rollback()
        finally:
            sess.close()

        with engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            conn.execute(
                text("DELETE FROM public.positions WHERE tenant_id = :i"), {"i": tid}
            )
            conn.execute(text("DELETE FROM public.profiles WHERE id = :i"), {"i": tid})
            conn.execute(text("DELETE FROM auth.users WHERE id = :i"), {"i": tid})


# ── the schema actually matches the model ────────────────────────────────────


def test_every_model_column_exists_on_the_table(engine):
    """Checked before the lifecycle, so a missing column reports as a missing column
    rather than as a confusing failure three steps into a write."""
    from backend_app.core.position_model import PositionModel

    with engine.begin() as conn:
        actual = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='positions'"
                )
            ).scalars().all()
        )
    declared = {c.name for c in PositionModel.__table__.columns}
    missing = sorted(declared - actual)
    assert not missing, (
        f"core/position_model.py declares {len(declared)} columns and the table is "
        f"missing {len(missing)}: {missing}"
    )


def test_the_primary_key_is_position_id(engine):
    with engine.begin() as conn:
        cols = conn.execute(
            text(
                "SELECT array_to_string(ARRAY(SELECT a.attname FROM pg_attribute a "
                "  WHERE a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
                "  ORDER BY a.attname), ',') "
                "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname='public' AND t.relname='positions' AND c.contype='p'"
            )
        ).scalar()
    assert cols == "position_id", f"positions primary key is ({cols}), expected position_id"


def test_tenant_id_is_not_nullable(engine):
    """A position with a null tenant is invisible to every RLS policy on the table and
    belongs to no account, so the column has to refuse NULL."""
    with engine.begin() as conn:
        nullable = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='positions' "
                "  AND column_name='tenant_id'"
            )
        ).scalar()
    assert nullable == "NO"


# ── the lifecycle, through the repository the application uses ───────────────


def test_a_position_can_be_created_read_updated_and_closed(workspace):
    """The whole path, through `PositionRepository` rather than raw SQL.

    Using the repository is the point: it is what `position_engine` calls, so this
    exercises the same query construction, the same enum handling and the same
    `Decimal`-as-string convention the application relies on.
    """
    from backend_app.core.position_model import (
        PositionCreate,
        PositionSide,
        PositionStatus,
        PositionUpdate,
        get_position_repository,
    )

    tenant, session = workspace
    repo = get_position_repository(session)
    position_id = f"pos_{uuid.uuid4().hex[:16]}"

    # CREATE
    created = repo.create(
        PositionCreate(
            position_id=position_id,
            tenant_id=uuid.UUID(tenant),
            strategy_id="strat-lifecycle",
            exchange_id="binance",
            symbol="BTC/USDT",
            side=PositionSide.LONG,
            size="0.10000000",
            avg_entry_price="50000.00000000",
        )
    )
    assert created.position_id == position_id
    assert created.status == PositionStatus.OPEN

    # READ by id
    fetched = repo.get_by_id(position_id, uuid.UUID(tenant))
    assert fetched is not None, "the position could not be read back"
    assert fetched.symbol == "BTC/USDT"
    assert fetched.side == PositionSide.LONG

    # READ by the open-position lookup `position_engine` uses to decide
    # update-or-create
    found_open = repo.find_open_position(
        tenant_id=uuid.UUID(tenant),
        strategy_id="strat-lifecycle",
        symbol="BTC/USDT",
        side=PositionSide.LONG,
    )
    assert found_open is not None, (
        "find_open_position did not locate the position; position_engine would create a "
        "duplicate instead of updating this one"
    )
    assert found_open.position_id == position_id

    # UPDATE
    updated = repo.update(
        position_id,
        uuid.UUID(tenant),
        PositionUpdate(size="0.20000000", avg_entry_price="51000.00000000",
                       unrealized_pnl="100.0"),
    )
    assert updated is not None
    assert updated.size == "0.20000000"
    assert updated.unrealized_pnl == "100.0"

    # LIST
    open_positions = repo.list_open_positions(uuid.UUID(tenant))
    assert [p.position_id for p in open_positions] == [position_id]

    # CLOSE
    closed = repo.close_position(
        position_id, uuid.UUID(tenant), final_realized_pnl="250.0"
    )
    assert closed is not None
    assert closed.status == PositionStatus.CLOSED
    assert closed.realized_pnl == "250.0"
    assert closed.closed_at is not None, "closed_at was not set"

    # A closed position no longer appears as open.
    assert repo.list_open_positions(uuid.UUID(tenant)) == []


def test_a_position_is_scoped_to_its_tenant_on_read(workspace):
    """`get_by_id` takes a tenant and must not return another tenant's row.

    This is the application-level half of the isolation story for `positions`. The
    database-level half is in `tests/test_tenant_isolation_rls_postgres.py`; both are
    needed because the SQLAlchemy path connects as the table owner and is therefore
    exempt from the policies.
    """
    from backend_app.core.position_model import (
        PositionCreate,
        PositionSide,
        get_position_repository,
    )

    tenant, session = workspace
    repo = get_position_repository(session)
    position_id = f"pos_{uuid.uuid4().hex[:16]}"
    repo.create(
        PositionCreate(
            position_id=position_id,
            tenant_id=uuid.UUID(tenant),
            strategy_id="strat-scope",
            exchange_id="binance",
            symbol="ETH/USDT",
            side=PositionSide.SHORT,
            size="1.0",
            avg_entry_price="3000.0",
        )
    )

    other_tenant = uuid.uuid4()
    assert repo.get_by_id(position_id, other_tenant) is None, (
        "get_by_id returned a position to a tenant that does not own it"
    )


def test_the_reconciliation_query_runs(workspace):
    """`core/consistency_checker.py` is what made this a launch concern.

    It queries `PositionModel` filtered by `tenant_id` AND `exchange_id` to build the
    local position map it compares against the venue. While the table had the blob shape
    that query raised `UndefinedColumn`, so reconciliation could not run at all. This is
    the same filter, executed.
    """
    from backend_app.core.position_model import (
        PositionCreate,
        PositionModel,
        PositionSide,
        get_position_repository,
    )

    tenant, session = workspace
    repo = get_position_repository(session)
    repo.create(
        PositionCreate(
            position_id=f"pos_{uuid.uuid4().hex[:16]}",
            tenant_id=uuid.UUID(tenant),
            strategy_id="strat-recon",
            exchange_id="binance",
            symbol="SOL/USDT",
            side=PositionSide.LONG,
            size="10.0",
            avg_entry_price="150.0",
        )
    )

    rows = (
        session.query(PositionModel)
        .filter(
            PositionModel.tenant_id == uuid.UUID(tenant),
            PositionModel.exchange_id == "binance",
        )
        .all()
    )
    assert rows, "the reconciliation query returned nothing for a tenant that has a position"
    assert all(r.exchange_id == "binance" for r in rows)
