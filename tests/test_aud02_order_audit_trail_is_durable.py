"""AUD-02: the order audit trail must be durably stored and readable.

Unit lane: fake service client (no network). Real-Postgres lane (AERORA_TEST_DATABASE_URL)
applies the actual migration's constraints: append-only, browser roles locked out,
idempotent on audit_id, and a record produced by OrderAuditRecord.to_db_row() really fits.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend_app.core.audit_trail import (AuditEventType, OrderAuditLogger,
                                          OrderAuditRecord)


def _record(**kw):
    base = dict(
        audit_id="aud-1", timestamp=datetime(2026, 1, 2, 3, 4, 5), event_type=AuditEventType.ORDER_CONFIRMED,
        user_id="u1", strategy_id="s1", execution_id="ex-1", signal={"a": 1}, decision_reason="r",
        symbol="BTC/USDT", side="buy", size=0.5, price=100.0, order_type="limit", exchange_id="binance",
        exchange_response={"id": "x"}, client_order_id="c1", exchange_order_id="o1", status="filled",
        filled_amount=0.5, remaining_amount=0.0, average_price=100.0, fee=0.1, error_message=None,
        metadata={"m": 1},
    )
    base.update(kw)
    return OrderAuditRecord(**base)


def test_from_dict_round_trips_both_shapes():
    r = _record()
    assert OrderAuditRecord.from_dict(json.loads(json.dumps(r.to_dict()))) == r  # Redis shape
    row = r.to_db_row()
    row["timestamp"] = None  # durable shape uses occurred_at
    back = OrderAuditRecord.from_dict(row)
    assert back.audit_id == "aud-1" and back.signal == {"a": 1} and back.size == 0.5


def test_bug049_user_id_column_is_uuid_and_a_non_uuid_actor_is_kept_in_metadata():
    """hardening/998 requires every user_id column to be uuid; no event may be dropped."""
    system = _record(user_id="system:reconciler").to_db_row()
    assert system["user_id"] is None and system["metadata"]["raw_user_id"] == "system:reconciler"
    assert OrderAuditRecord.from_dict({**system, "timestamp": None}).user_id == "system:reconciler"

    uid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    real = _record(user_id=uid.upper()).to_db_row()
    assert real["user_id"] == uid and "raw_user_id" not in (real["metadata"] or {})


def test_migration_036_declares_user_id_as_uuid():
    import re
    sql = open(os.path.join(os.path.dirname(__file__), "..", "backend_app", "migrations",
                            "036_order_audit_log_durable.sql"), encoding="utf-8").read()
    assert re.search(r"^\s*user_id\s+uuid\s*,", sql, re.M)


class _Table:
    def __init__(self, store):
        self.store, self._q = store, {}
    def upsert(self, row, on_conflict=None, ignore_duplicates=False):
        self.store.setdefault("rows", {}).setdefault(row["audit_id"], row)
        return self
    def select(self, *_): return self
    def eq(self, col, val): self._q[col] = val; return self
    def order(self, *_): return self
    def execute(self):
        rows = [r for r in self.store.get("rows", {}).values()
                if all(r.get(k) == v for k, v in self._q.items())]
        return SimpleNamespace(data=rows)


def _client(store):
    c = MagicMock()
    c.table.side_effect = lambda name: (_ for _ in ()).throw(AssertionError(name)) if name != "order_audit_log" else _Table(store)
    return c


def test_store_to_db_actually_writes_and_is_idempotent():
    store = {}
    lg = OrderAuditLogger()
    with patch("backend_app.core.dependencies.get_service_supabase", return_value=_client(store)):
        assert asyncio.run(lg._store_to_db(_record())) is True
        assert asyncio.run(lg._store_to_db(_record())) is True
    assert list(store["rows"]) == ["aud-1"]


def test_get_audit_trail_reads_back_from_durable_store_when_redis_is_empty():
    store = {}
    lg = OrderAuditLogger()
    with patch("backend_app.core.dependencies.get_service_supabase", return_value=_client(store)), \
         patch("backend_app.core.audit_trail.redis_manager") as rm:
        async def _none(*_): return None
        rm.get = _none
        asyncio.run(lg._store_to_db(_record(audit_id="a2", timestamp=datetime(2026, 1, 2, 3, 4, 6),
                                            event_type=AuditEventType.FILL_RECEIVED)))
        asyncio.run(lg._store_to_db(_record()))
        out = asyncio.run(lg.get_audit_trail("ex-1"))
    assert [r.audit_id for r in out] == ["aud-1", "a2"]  # chronological, durable only


def test_durable_write_failure_does_not_raise_but_is_reported():
    lg = OrderAuditLogger()
    boom = MagicMock(); boom.table.side_effect = RuntimeError("db down")
    with patch("backend_app.core.dependencies.get_service_supabase", return_value=boom):
        assert asyncio.run(lg._store_to_db(_record())) is False


def test_no_service_client_is_reported_not_silent(caplog):
    lg = OrderAuditLogger()
    with patch("backend_app.core.dependencies.get_service_supabase", return_value=None), \
         caplog.at_level("ERROR"):
        assert asyncio.run(lg._store_to_db(_record())) is False
    assert "DURABLE WRITE UNAVAILABLE" in caplog.text


# ── real PostgreSQL lane ────────────────────────────────────────────────────
TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
pg = pytest.mark.skipif(not TEST_DATABASE_URL.startswith("postgres"), reason="needs provisioned PostgreSQL")


@pytest.fixture()
def conn():
    sa = pytest.importorskip("sqlalchemy")
    eng = sa.create_engine(TEST_DATABASE_URL, future=True)
    c = eng.connect(); t = c.begin()
    try:
        yield c
    finally:
        t.rollback(); c.close()


def _insert(conn, rec):
    from sqlalchemy import text
    row = rec.to_db_row()
    cols = ", ".join(row)
    binds = ", ".join(f":{k}" if k not in ("signal", "exchange_response", "metadata") else f"CAST(:{k} AS jsonb)" for k in row)
    params = {k: (json.dumps(v) if k in ("signal", "exchange_response", "metadata") and v is not None else v)
              for k, v in row.items()}
    conn.execute(text(f"INSERT INTO public.order_audit_log ({cols}) VALUES ({binds}) ON CONFLICT (audit_id) DO NOTHING"), params)


@pg
def test_pg_row_fits_and_insert_is_idempotent(conn):
    from sqlalchemy import text
    _insert(conn, _record(audit_id="pg-1")); _insert(conn, _record(audit_id="pg-1"))
    n = conn.execute(text("SELECT count(*) FROM public.order_audit_log WHERE audit_id='pg-1'")).scalar()
    assert n == 1


@pg
@pytest.mark.parametrize("sql", [
    "UPDATE public.order_audit_log SET status='x' WHERE audit_id='pg-2'",
    "DELETE FROM public.order_audit_log WHERE audit_id='pg-2'",
    "TRUNCATE public.order_audit_log",
])
def test_pg_table_is_append_only_even_for_the_owner(conn, sql):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    _insert(conn, _record(audit_id="pg-2"))
    sp = conn.begin_nested()
    with pytest.raises(DBAPIError) as ei:
        conn.execute(text(sql))
    assert "append-only" in str(ei.value.orig)
    sp.rollback()


@pg
@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("sql", [
    "SELECT * FROM public.order_audit_log",
    "INSERT INTO public.order_audit_log (audit_id, occurred_at, event_type, user_id) VALUES ('b', now(), 'x', '00000000-0000-0000-0000-000000000001')",
])
def test_pg_browser_roles_have_no_access(conn, role, sql):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    sp = conn.begin_nested()
    conn.execute(text(f"SET LOCAL ROLE {role}"))
    with pytest.raises(DBAPIError) as ei:
        conn.execute(text(sql))
    assert "permission denied" in str(ei.value.orig).lower()
    sp.rollback()
