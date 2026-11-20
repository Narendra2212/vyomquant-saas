"""The database refuses an invalid tenant reference, rather than trusting the application.

WHAT THIS IS FOR. Before alembic revision ``a1b2c3d4e5f6``, tenant ownership on the
execution and financial tables had no referential integrity at all. ``tenant_id`` was
``varchar(36)`` on ``orders``, ``positions``, ``fills`` and ``dag_tasks`` while
``profiles.id`` -- the identity it is supposed to reference -- is ``uuid``, and PostgreSQL
has no equality operator between the two. So five of the seven constraints declared in
``add_foreign_keys_20260817.py`` could not be created, and a row was free to name a
profile that had never existed or had already been deleted.

The brief for this work said not to settle for application validation, and that is the
distinction these tests draw: every assertion here is about what the DATABASE refuses,
with no application code in the path. An ORM-level check can be bypassed by raw SQL, by a
background worker, by a future endpoint, or by a bug; a foreign key cannot.

Gated on AERORA_TEST_DATABASE_URL. Foreign keys of this shape are a PostgreSQL fact and
the SQLite lane renders ``tenant_id`` as CHAR(32) with no constraint.
"""

from __future__ import annotations

import os
import uuid

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_DATABASE_URL.startswith("postgresql"),
    reason="AERORA_TEST_DATABASE_URL is unset or not PostgreSQL.",
)

#: The seven constraints that must exist. Five of them could not be created before
#: normalisation; all seven are asserted so a regression in either revision is caught.
EXPECTED_CONSTRAINTS = {
    "fk_fills_tenant_id": "fills",
    "fk_orders_tenant_id": "orders",
    "fk_positions_tenant_id": "positions",
    "fk_dag_tasks_tenant_id": "dag_tasks",
    "fk_execution_records_task_id": "execution_records",
    "fk_execution_records_order_id": "execution_records",
    "fk_execution_records_tenant_id": "execution_records",
}

#: Tables with a simple (id, tenant_id, data) shape, so an insert needs nothing else.
#:
#: `positions` USED TO BE HERE AND IS NOT ANY MORE. Alembic `b2c3d4e5f6a7` reshaped it
#: onto `core/position_model.PositionModel`, so it has a `position_id` primary key and
#: eight further NOT NULL columns. Its `fk_positions_tenant_id` is covered by the
#: dedicated tests at the bottom of this file rather than by the generic blob loop --
#: keeping it in the loop would have meant weakening the loop to the lowest common
#: shape, which is how a generic test stops testing anything.
BLOB_TABLES = ("orders", "fills")

#: A minimal valid `positions` row. Every column here is NOT NULL with no default.
_POSITION_INSERT = (
    "INSERT INTO public.positions "
    "  (position_id, tenant_id, strategy_id, symbol, side, size, avg_entry_price, "
    "   unrealized_pnl, realized_pnl, status) "
    "VALUES (:p, :t, 'fk-probe', 'BTC/USDT', 'LONG', '1', '1', '0.0', '0.0', 'OPEN')"
)

#: ``dag_tasks`` needs every NOT NULL column that has no default. Spelled once because
#: three tests use it, and taken from information_schema rather than from the ORM -- the
#: point of these tests is what the database enforces, so the insert should be the one
#: the database actually accepts.
_DAG_TASK_INSERT = (
    "INSERT INTO public.dag_tasks "
    "  (task_id, tenant_id, status, priority, dag_config, progress, "
    "   retry_count, max_retries, created_at) "
    "VALUES (:k, :t, 'PENDING', 5, '{}', 0, 0, 3, now())"
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def tenant(engine):
    """A real profile row, removed afterwards."""
    tid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:i, :e) "
                 "ON CONFLICT (id) DO NOTHING"),
            {"i": tid, "e": f"{tid[:8]}@example.test"},
        )
        conn.execute(text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": tid})
    yield tid
    with engine.begin() as conn:
        for t in (*BLOB_TABLES, "positions", "dag_tasks"):
            conn.execute(text(f"DELETE FROM public.{t} WHERE tenant_id = :i"), {"i": tid})
        conn.execute(text("DELETE FROM public.profiles WHERE id = :i"), {"i": tid})
        conn.execute(text("DELETE FROM auth.users WHERE id = :i"), {"i": tid})


# ── the constraints exist and the columns are the right type ─────────────────


@pytest.mark.parametrize("name,table", sorted(EXPECTED_CONSTRAINTS.items()))
def test_the_foreign_key_exists(engine, name, table):
    with engine.begin() as conn:
        found = conn.execute(
            text(
                "SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE c.conname = :n AND t.relname = :t AND c.contype = 'f'"
            ),
            {"n": name, "t": table},
        ).scalar()
    assert found == 1, (
        f"{name} is missing from {table}; tenant ownership there has no referential "
        f"integrity"
    )


@pytest.mark.parametrize(
    "table",
    ["orders", "positions", "fills", "dag_tasks", "execution_records",
     "idempotency_keys", "transaction_records", "transaction_checkpoints",
     "transaction_rollbacks", "reconciliation_mismatches"],
)
def test_tenant_id_is_a_uuid_column(engine, table):
    """The type is the precondition for the constraint, so it is asserted separately.

    A future migration that recreated one of these tables as ``varchar`` would drop the
    foreign key with it, and the constraint test above would then be the only failure --
    this one names the cause.
    """
    with engine.begin() as conn:
        dtype = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t AND column_name='tenant_id'"
            ),
            {"t": table},
        ).scalar()
    assert dtype == "uuid", f"{table}.tenant_id is {dtype!r}, not uuid"


# ── what the database refuses ────────────────────────────────────────────────


@pytest.mark.parametrize("table", BLOB_TABLES)
def test_a_valid_tenant_reference_is_accepted(engine, tenant, table):
    """The control has to let real writes through, or it is just an outage."""
    row_id = f"ok-{uuid.uuid4().hex[:10]}"
    with engine.begin() as conn:
        conn.execute(
            text(f"INSERT INTO public.{table} (id, tenant_id) VALUES (:i, :t)"),
            {"i": row_id, "t": tenant},
        )
        stored = conn.execute(
            text(f"SELECT tenant_id FROM public.{table} WHERE id = :i"), {"i": row_id}
        ).scalar()
    assert str(stored) == tenant


@pytest.mark.parametrize("table", BLOB_TABLES)
def test_an_unknown_tenant_reference_is_refused(engine, table):
    """The case that was silently accepted before the foreign keys existed."""
    ghost = str(uuid.uuid4())
    row_id = f"ghost-{uuid.uuid4().hex[:10]}"
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(Exception) as excinfo:
                conn.execute(
                    text(f"INSERT INTO public.{table} (id, tenant_id) VALUES (:i, :t)"),
                    {"i": row_id, "t": ghost},
                )
            assert "foreign key" in str(excinfo.value).lower(), (
                f"{table}: the insert failed, but not on a foreign key: {excinfo.value}"
            )
        finally:
            trans.rollback()


def test_a_valid_tenant_is_accepted_on_positions(engine, tenant):
    """``positions`` separately, since it no longer shares the blob shape."""
    pid = f"pos-ok-{uuid.uuid4().hex[:10]}"
    with engine.begin() as conn:
        conn.execute(text(_POSITION_INSERT), {"p": pid, "t": tenant})
        stored = conn.execute(
            text("SELECT tenant_id FROM public.positions WHERE position_id = :p"),
            {"p": pid},
        ).scalar()
    assert str(stored) == tenant


def test_an_unknown_tenant_is_refused_on_positions(engine):
    """``fk_positions_tenant_id`` -- one of the five that could not exist before
    ``a1b2c3d4e5f6`` normalised the column type."""
    ghost = str(uuid.uuid4())
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(Exception) as excinfo:
                conn.execute(
                    text(_POSITION_INSERT),
                    {"p": f"pos-ghost-{uuid.uuid4().hex[:8]}", "t": ghost},
                )
            assert "foreign key" in str(excinfo.value).lower(), str(excinfo.value)
        finally:
            trans.rollback()


def test_deleting_a_profile_cascades_positions(engine):
    """``ON DELETE CASCADE`` on positions, checked on the canonical shape."""
    tid = str(uuid.uuid4())
    pid = f"pos-casc-{uuid.uuid4().hex[:10]}"
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:i, :e)"),
            {"i": tid, "e": f"{tid[:8]}@example.test"},
        )
        conn.execute(text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": tid})
        conn.execute(text(_POSITION_INSERT), {"p": pid, "t": tid})

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM public.profiles WHERE id = :i"), {"i": tid})
        remaining = conn.execute(
            text("SELECT count(*) FROM public.positions WHERE position_id = :p"),
            {"p": pid},
        ).scalar()
        conn.execute(text("DELETE FROM auth.users WHERE id = :i"), {"i": tid})
    assert remaining == 0, f"position {pid} survived the deletion of its profile"


def test_an_unknown_tenant_is_refused_on_dag_tasks(engine):
    """``dag_tasks`` separately, because it has required columns the blob tables do not
    and is on a live request path (``routers/dag_tasks.py``)."""
    ghost = str(uuid.uuid4())
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(Exception) as excinfo:
                conn.execute(text(_DAG_TASK_INSERT), {"k": str(uuid.uuid4()), "t": ghost})
            assert "foreign key" in str(excinfo.value).lower(), str(excinfo.value)
        finally:
            trans.rollback()


@pytest.mark.parametrize("table", BLOB_TABLES)
def test_deleting_a_profile_cascades_rather_than_orphaning(engine, table):
    """``ON DELETE CASCADE`` was in the original declarations and is preserved.

    The alternative outcomes are both worse: a refused delete would make account closure
    fail, and an orphaned row would be execution data belonging to nobody -- which is
    exactly the state the missing constraints allowed.
    """
    tid = str(uuid.uuid4())
    row_id = f"casc-{uuid.uuid4().hex[:10]}"
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:i, :e)"),
            {"i": tid, "e": f"{tid[:8]}@example.test"},
        )
        conn.execute(text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": tid})
        conn.execute(
            text(f"INSERT INTO public.{table} (id, tenant_id) VALUES (:r, :t)"),
            {"r": row_id, "t": tid},
        )

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM public.profiles WHERE id = :i"), {"i": tid})
        remaining = conn.execute(
            text(f"SELECT count(*) FROM public.{table} WHERE id = :r"), {"r": row_id}
        ).scalar()
        conn.execute(text("DELETE FROM auth.users WHERE id = :i"), {"i": tid})

    assert remaining == 0, (
        f"{table}: {row_id} survived the deletion of its profile and is now orphaned "
        f"execution data"
    )


def test_a_malformed_tenant_id_is_refused_by_the_type(engine):
    """Not every bad value is a missing reference; some are not identifiers at all.

    While the column was ``varchar(36)`` a tenant_id of 'not-a-uuid' -- or an empty
    string -- stored happily. The uuid type refuses it before any constraint is consulted,
    which is the cheaper of the two rejections and catches a different class of bug.
    """
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(Exception) as excinfo:
                conn.execute(
                    text("INSERT INTO public.orders (id, tenant_id) VALUES (:i, :t)"),
                    {"i": "bad-1", "t": "not-a-uuid"},
                )
            message = str(excinfo.value).lower()
            assert "uuid" in message or "invalid input" in message, str(excinfo.value)
        finally:
            trans.rollback()


def test_execution_records_can_reference_a_dag_task(engine, tenant):
    """The seventh constraint, which was blocked by uuid-against-varchar(36) on a PRIMARY
    KEY rather than by the tenant split -- and is what links an execution back to the task
    that scheduled it."""
    task_id = str(uuid.uuid4())
    exec_id = f"exec-{uuid.uuid4().hex[:10]}"
    with engine.begin() as conn:
        conn.execute(text(_DAG_TASK_INSERT), {"k": task_id, "t": tenant})
        conn.execute(
            text(
                "INSERT INTO public.execution_records "
                "  (execution_id, tenant_id, task_id, strategy_id, symbol, side, size, "
                "   status, created_at, updated_at) "
                "VALUES (:e, :t, :k, 'strat-1', 'BTC/USDT', 'buy', '1', 'PENDING', "
                "        now(), now())"
            ),
            {"e": exec_id, "t": tenant, "k": task_id},
        )
        linked = conn.execute(
            text("SELECT task_id FROM public.execution_records WHERE execution_id = :e"),
            {"e": exec_id},
        ).scalar()
    assert str(linked) == task_id

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM public.execution_records WHERE execution_id = :e"),
            {"e": exec_id},
        )
        conn.execute(text("DELETE FROM public.dag_tasks WHERE task_id = :k"), {"k": task_id})


def test_an_unknown_task_reference_is_refused(engine, tenant):
    ghost_task = str(uuid.uuid4())
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(Exception) as excinfo:
                conn.execute(
                    text(
                        "INSERT INTO public.execution_records "
                        "  (execution_id, tenant_id, task_id, strategy_id, symbol, side, "
                        "   size, status, created_at, updated_at) "
                        "VALUES (:e, :t, :k, 's', 'BTC/USDT', 'buy', '1', 'PENDING', "
                        "        now(), now())"
                    ),
                    {"e": f"x-{uuid.uuid4().hex[:8]}", "t": tenant, "k": ghost_task},
                )
            assert "foreign key" in str(excinfo.value).lower(), str(excinfo.value)
        finally:
            trans.rollback()
