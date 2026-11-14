"""
tests/test_pos_txn01_position_failure_keeps_the_order_update.py

POS-TXN-01 / POS-SCHEMA-01: a failed position update poisoned the watchdog's session.

Found in production when the first live testnet fills arrived. ``public.positions`` was
still the blob shape (alembic ``b2c3d4e5f6a7`` had never run there), so the order
watchdog's ``_update_position`` failed with ``column positions.position_id does not
exist``. The exception was logged and swallowed with the transaction still aborted, so
the caller's ``self.db.commit()`` raised ``PendingRollbackError``: the order's own
COMPLETED status was never saved, it stayed stale, and every cycle repeated the failure
(about 2,000 Sentry events).

Asserted:
1. a position failure rolls back only its SAVEPOINT, never the order's update;
2. if the position path already committed, a later failure takes a plain rollback;
3. success releases the savepoint;
4. against real PostgreSQL (gated): the order's status survives a failed position query
   and the following commit does not raise;
5. migration 067 reshapes a blob-shaped table to the canonical columns, is a no-op on
   the canonical shape, and refuses blob rows it would have to guess about (gated).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend_app.backend.order_watchdog import OrderWatchdog

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "backend_app" / "migrations" / "067_pos_schema01_positions_canonical_shape.sql"
TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
needs_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason="AERORA_TEST_DATABASE_URL is unset or not PostgreSQL",
)


class _Savepoint:
    def __init__(self):
        self.is_active = True
        self.committed = self.rolled_back = False

    def commit(self):
        self.committed, self.is_active = True, False

    def rollback(self):
        self.rolled_back, self.is_active = True, False


class _Session:
    def __init__(self):
        self.savepoint = _Savepoint()
        self.rollbacks = 0

    def begin_nested(self):
        return self.savepoint

    def rollback(self):
        self.rollbacks += 1


def _order():
    return SimpleNamespace(execution_id="e1", filled_size="0.0001", avg_price="67149.89")


def _engine(update):
    return SimpleNamespace(update_position_from_fill=update)


def _run(session, update):
    watchdog = OrderWatchdog.__new__(OrderWatchdog)
    watchdog.db = session
    with patch("backend_app.backend.order_watchdog.get_position_engine",
               lambda _db: _engine(update)):
        asyncio.run(watchdog._update_position(_order(), fill_delta="0.0001"))


def test_a_failed_position_update_rolls_back_only_its_savepoint():
    session = _Session()

    async def boom(**_kw):
        raise RuntimeError("column positions.position_id does not exist")

    _run(session, boom)
    assert session.savepoint.rolled_back
    assert session.rollbacks == 0, "a full rollback would discard the order's own update"


def test_a_failure_after_the_position_path_committed_takes_a_plain_rollback():
    session = _Session()

    async def commits_then_fails(**_kw):
        session.savepoint.is_active = False  # Session.commit() ended the savepoint
        raise RuntimeError("trade history write failed")

    _run(session, commits_then_fails)
    assert not session.savepoint.rolled_back
    assert session.rollbacks == 1


def test_a_successful_update_releases_the_savepoint():
    session = _Session()

    async def ok(**_kw):
        return SimpleNamespace(position_id="pos_e1")

    _run(session, ok)
    assert session.savepoint.committed
    assert session.rollbacks == 0


@needs_postgres
def test_the_order_status_survives_a_failed_position_query_on_postgres():
    from sqlalchemy import Column, String, create_engine, text
    from sqlalchemy.orm import declarative_base, sessionmaker

    table = f"pos_txn01_{uuid.uuid4().hex[:8]}"
    base = declarative_base()

    class Order(base):
        __tablename__ = table
        id = Column(String, primary_key=True)
        status = Column(String)

    engine = create_engine(TEST_DATABASE_URL, future=True)
    base.metadata.create_all(engine)
    try:
        with sessionmaker(engine)() as db:
            db.add(Order(id="e1", status="EXECUTING"))
            db.commit()

            order = db.get(Order, "e1")
            order.status = "COMPLETED"

            async def broken_schema(**_kw):
                db.execute(text("SELECT position_id FROM pg_class"))  # UndefinedColumn

            watchdog = OrderWatchdog.__new__(OrderWatchdog)
            watchdog.db = db
            with patch("backend_app.backend.order_watchdog.get_position_engine",
                       lambda _db: _engine(broken_schema)):
                asyncio.run(watchdog._update_position(_order(), fill_delta="0.0001"))

            db.commit()  # raised PendingRollbackError before POS-TXN-01

        with engine.connect() as conn:
            assert conn.execute(
                text(f"SELECT status FROM {table} WHERE id = 'e1'")  # nosec B608 - test-generated identifier
            ).scalar() == "COMPLETED"
    finally:
        base.metadata.drop_all(engine)
        engine.dispose()


# ── migration 067 ─────────────────────────────────────────────────────────────────────


def test_migration_067_is_guarded_and_refuses_to_guess():
    sql = MIGRATION.read_text()
    assert "column_name = 'position_id'" in sql and "RETURN;" in sql
    assert "RAISE EXCEPTION" in sql and "data IS NOT NULL" in sql
    assert "PRIMARY KEY (position_id)" in sql


def _blob_schema(conn, schema):
    from sqlalchemy import text

    conn.execute(text(f"CREATE SCHEMA {schema}"))
    conn.execute(text(f"SET search_path TO {schema}, public"))
    conn.execute(text(
        "CREATE TABLE positions (id varchar(36) PRIMARY KEY, tenant_id uuid, data json, "
        "created_at timestamp, updated_at timestamp)"
    ))


def _apply_067(conn, schema):
    # The migration addresses `public.positions`; run it against a scratch schema.
    sql = MIGRATION.read_text().replace("public.positions", f"{schema}.positions")
    sql = sql.replace("table_schema = 'public'", f"table_schema = '{schema}'")
    sql = sql.replace("n.nspname = 'public'", f"n.nspname = '{schema}'")
    # Raw DBAPI cursor with no parameters: the file's `%I` / `%` RAISE placeholders
    # must reach PL/pgSQL untouched rather than be read as psycopg2 paramstyle.
    with conn.connection.cursor() as cur:
        cur.execute(sql)


@needs_postgres
def test_migration_067_reshapes_the_blob_table_and_is_idempotent():
    from sqlalchemy import create_engine, text

    schema = f"pos067_{uuid.uuid4().hex[:8]}"
    engine = create_engine(TEST_DATABASE_URL, future=True)
    try:
        with engine.begin() as conn:
            _blob_schema(conn, schema)
            _apply_067(conn, schema)
            _apply_067(conn, schema)  # second run is a no-op
            cols = {
                r[0]: (r[1], r[2])
                for r in conn.execute(text(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'positions'"
                ), {"s": schema})
            }
            pk = conn.execute(text(
                "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
                "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = (:s || '.positions')::regclass AND i.indisprimary"
            ), {"s": schema}).scalars().all()
        assert pk == ["position_id"]
        for name in ("strategy_id", "symbol", "size", "avg_entry_price", "unrealized_pnl",
                     "realized_pnl", "side", "status", "tenant_id", "created_at", "updated_at"):
            assert cols[name][1] == "NO", name
        assert cols["side"][0] == "USER-DEFINED"
        assert cols["created_at"][0] == "timestamp with time zone"
        assert cols["id"][1] == "YES"
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        engine.dispose()


@needs_postgres
def test_migration_067_refuses_blob_rows():
    psycopg2 = pytest.importorskip("psycopg2")
    from sqlalchemy import create_engine, text

    schema = f"pos067_{uuid.uuid4().hex[:8]}"
    engine = create_engine(TEST_DATABASE_URL, future=True)
    try:
        with engine.begin() as conn:
            _blob_schema(conn, schema)
            conn.execute(text(
                "INSERT INTO positions (id, tenant_id, data) "
                "VALUES ('p1', gen_random_uuid(), '{\"symbol\": \"BTC/USDT\"}')"
            ))
        with pytest.raises(psycopg2.Error, match="refusing to guess"):
            with engine.begin() as conn:
                conn.execute(text(f"SET search_path TO {schema}, public"))
                _apply_067(conn, schema)
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        engine.dispose()


# ── the loops recover a session a failed cycle left aborted ───────────────────────────


@pytest.mark.parametrize("loop, step", [
    ("_monitoring_loop", "_check_stale_orders"),
    ("_reconciliation_loop", "_reconcile_open_orders"),
])
def test_a_failed_cycle_rolls_the_shared_session_back(loop, step):
    """Before POS-TXN-01 the loop logged and slept, and the next cycle's first query
    raised PendingRollbackError forever (~3,000 events in production)."""
    from unittest.mock import AsyncMock

    session = _Session()
    watchdog = OrderWatchdog.__new__(OrderWatchdog)
    watchdog.db = session
    watchdog._running = True
    watchdog.config = SimpleNamespace(check_interval_seconds=0)
    watchdog._try_become_leader = AsyncMock(return_value=True)
    if loop == "_monitoring_loop":  # it spawns the other loop as a task
        watchdog._reconciliation_loop = AsyncMock()

    async def fail_then_stop():
        watchdog._running = False
        raise RuntimeError("current transaction is aborted")

    setattr(watchdog, step, fail_then_stop)
    with patch("backend_app.backend.order_watchdog.asyncio.sleep", AsyncMock()):
        asyncio.run(getattr(watchdog, loop)())
    assert session.rollbacks == 1
