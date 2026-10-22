"""AUD-01: every signal lifecycle write (create / risk decision / order / execution)
now also appends an immutable row to signal_audit_log.

`signals` is mutated in place by design -- update_risk_decision/update_order/
update_execution overwrite the same row as a signal progresses (update_execution's own
docstring: "a REPEAT call is safe by construction: it overwrites... rather than
appending"). That is the right behaviour for CURRENT state, but it means a later edit to
`signals` leaves no trace of what the row used to say. 046_signal_audit_log.sql added a
separate, append-only table (same shape as 036/order_audit_log and 040/trade_history:
a BEFORE UPDATE/DELETE/TRUNCATE trigger refuses every role, RLS enabled with anon/
authenticated revoked) for exactly that history, and SignalService._record_signal_audit
is the one place that appends to it.

Unit tests below use a fake PostgREST double (this file's own, scoped to `signals` and
`signal_audit_log`) to prove every writer calls it correctly and that a failure to record
the audit row never breaks the write it is recording. The real-Postgres test proves the
migration's own append-only/grant guarantees against the actual schema, skipped when no
PostgreSQL DSN is configured -- same convention as
tests/test_tenant01_cross_tenant_matrix_postgres.py.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, List, Optional

import pytest

from backend_app.backend.signal_service import SignalService


def run(coro):
    return asyncio.run(coro)


USER_ID = "11111111-1111-1111-1111-111111111111"


class _Result:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


class _FakeQuery:
    def __init__(self, client, table, verb, payload=None):
        self.client = client
        self.table = table
        self.verb = verb
        self.payload = payload
        self.filters: List[Any] = []

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def execute(self):
        return self.client._execute(self)


class FakeSupabase:
    """A minimal PostgREST stand-in scoped to `signals` and `signal_audit_log`."""

    def __init__(self, *, raise_on_audit_insert: bool = False):
        self.signals_row: Dict[str, Any] = {}
        self.raise_on_audit_insert = raise_on_audit_insert
        self.calls: List[Dict[str, Any]] = []

    def table(self, name):
        self._table = name
        return self

    def insert(self, payload):
        return _FakeQuery(self, self._table, "insert", payload)

    def update(self, payload):
        return _FakeQuery(self, self._table, "update", payload)

    def _execute(self, query):
        self.calls.append({"table": query.table, "verb": query.verb, "payload": query.payload})

        if query.table == "signal_audit_log" and query.verb == "insert":
            if self.raise_on_audit_insert:
                raise Exception("signal_audit_log insert failed (simulated)")
            return _Result(data=[dict(query.payload)])

        if query.table == "signals":
            if query.verb == "insert":
                self.signals_row = dict(query.payload)
                return _Result(data=[dict(self.signals_row)])
            if query.verb == "update":
                self.signals_row.update(query.payload)
                return _Result(data=[dict(self.signals_row)])

        return _Result(data=[])

    def audit_inserts(self) -> List[Dict[str, Any]]:
        return [c["payload"] for c in self.calls if c["table"] == "signal_audit_log" and c["verb"] == "insert"]


def _service(fake_sb: FakeSupabase) -> SignalService:
    service = SignalService()

    async def _get_supabase(_user):
        return fake_sb

    service._get_supabase = _get_supabase  # type: ignore[method-assign]
    return service


def _user(uid: str = USER_ID) -> dict:
    return {"id": uid, "access_token": "t"}


class TestEachWriterAppendsExactlyOneAuditRow:
    def test_create_signal_appends_a_created_row(self):
        fake = FakeSupabase()
        service = _service(fake)

        result = run(service.create_signal(
            _user(), "strat-1", "v1", "dep-1", "binance", "BTC/USDT", "1h", "w-1",
            "BUY", {}, {},
        ))

        inserts = fake.audit_inserts()
        assert len(inserts) == 1
        assert inserts[0]["event_type"] == "created"
        assert inserts[0]["signal_id"] == result["id"]
        assert inserts[0]["user_id"] == USER_ID
        assert inserts[0]["strategy_id"] == "strat-1"

    def test_update_risk_decision_appends_a_risk_decision_row(self):
        fake = FakeSupabase()
        service = _service(fake)
        created = run(service.create_signal(
            _user(), "strat-1", "v1", "dep-1", "binance", "BTC/USDT", "1h", "w-1",
            "BUY", {}, {},
        ))

        run(service.update_risk_decision(_user(), created["id"], True, "within limits"))

        inserts = fake.audit_inserts()
        assert [i["event_type"] for i in inserts] == ["created", "risk_decision"]
        assert inserts[1]["signal_id"] == created["id"]
        assert inserts[1]["payload"]["risk_passed"] is True

    def test_update_order_appends_an_order_row(self):
        fake = FakeSupabase()
        service = _service(fake)
        created = run(service.create_signal(
            _user(), "strat-1", "v1", "dep-1", "binance", "BTC/USDT", "1h", "w-1",
            "BUY", {}, {},
        ))

        run(service.update_order(
            _user(), created["id"], "ord-1", "ex-ord-1", "FILLED",
            1.0, 1.0, 0.0, 61000.0, 0.1, 0.0, 50.0,
        ))

        inserts = fake.audit_inserts()
        assert [i["event_type"] for i in inserts] == ["created", "order"]
        assert inserts[1]["payload"]["order_status"] == "FILLED"

    def test_update_execution_appends_an_execution_row(self):
        fake = FakeSupabase()
        service = _service(fake)
        created = run(service.create_signal(
            _user(), "strat-1", "v1", "dep-1", "binance", "BTC/USDT", "1h", "w-1",
            "BUY", {}, {},
        ))

        run(service.update_execution(_user(), created["id"], "trade-1", 42.0, 42.0))

        inserts = fake.audit_inserts()
        assert [i["event_type"] for i in inserts] == ["created", "execution"]
        assert inserts[1]["payload"]["pnl"] == 42.0


class TestAuditFailureNeverBreaksTheRealWrite:
    def test_a_failed_audit_insert_still_returns_the_signal(self):
        fake = FakeSupabase(raise_on_audit_insert=True)
        service = _service(fake)

        result = run(service.create_signal(
            _user(), "strat-1", "v1", "dep-1", "binance", "BTC/USDT", "1h", "w-1",
            "BUY", {}, {},
        ))

        assert result["id"]
        assert fake.signals_row["id"] == result["id"]

    def test_a_failed_audit_insert_on_update_still_returns_the_updated_signal(self):
        fake = FakeSupabase()
        service = _service(fake)
        created = run(service.create_signal(
            _user(), "strat-1", "v1", "dep-1", "binance", "BTC/USDT", "1h", "w-1",
            "BUY", {}, {},
        ))
        fake.raise_on_audit_insert = True

        result = run(service.update_risk_decision(_user(), created["id"], True, "ok"))

        assert result["risk_passed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Real PostgreSQL: the migration's own append-only/grant guarantees.
# ─────────────────────────────────────────────────────────────────────────────

DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
)

#: Gates ONLY the real-PostgreSQL class below, per-test.
#:
#: This was a module-level ``pytest.skip(..., allow_module_level=True)`` preceded by a
#: module-level ``pytest.importorskip("psycopg2")``. Both abort collection of the WHOLE
#: file, which also discarded the six fake-PostgREST unit tests ABOVE them -- the ones
#: this module's own docstring describes as proving "every writer calls it correctly and
#: that a failure to record the audit row never breaks the write it is recording". Those
#: six need no database at all, yet they ran in NO lane: the two PostgreSQL lanes in
#: 01-pr-check.yml do not list this file, and the SQLite lane skipped the module before
#: collecting them, so the skip was never visible as a gap.
#:
#: Same per-test convention as
#: tests/test_production_security_probe_rolls_back_postgres.py's own ``_needs_pg``,
#: which is why that file's six source-inspection tests do run in the SQLite lane.
_needs_pg = pytest.mark.skipif(
    not (DSN or "").startswith("postgres"),
    reason="AUD-01 migration checks require PostgreSQL",
)


@pytest.fixture(scope="module")
def db():
    # Imported here, not at module scope, for the same reason the gate above is a
    # marker and not a module-level skip: a missing psycopg2 must not delete this
    # file's database-independent tests.
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        conn = psycopg2.connect(DSN)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    conn.autocommit = True
    yield conn
    conn.close()


@_needs_pg
class TestSignalAuditLogMigration:
    def test_a_row_can_be_inserted_but_never_updated_or_deleted(self, db):
        cur = db.cursor()
        cur.execute(
            "INSERT INTO public.signal_audit_log (signal_id, event_type, payload) "
            "VALUES (%s, 'created', '{}'::jsonb) RETURNING audit_id",
            (str(uuid.uuid4()),),
        )
        audit_id = cur.fetchone()[0]

        with pytest.raises(Exception, match="append-only"):
            cur.execute(
                "UPDATE public.signal_audit_log SET event_type = 'order' WHERE audit_id = %s",
                (audit_id,),
            )

        cur2 = db.cursor()
        with pytest.raises(Exception, match="append-only"):
            cur2.execute(
                "DELETE FROM public.signal_audit_log WHERE audit_id = %s", (audit_id,)
            )

    def test_anon_and_authenticated_hold_no_privileges_at_all(self, db):
        cur = db.cursor()
        cur.execute(
            "SELECT grantee, privilege_type FROM information_schema.table_privileges "
            "WHERE table_schema='public' AND table_name='signal_audit_log' "
            "AND grantee IN ('anon','authenticated')"
        )
        assert cur.fetchall() == []

    def test_event_type_is_constrained_to_the_known_lifecycle_stages(self, db):
        cur = db.cursor()
        with pytest.raises(Exception, match="violates check constraint"):
            cur.execute(
                "INSERT INTO public.signal_audit_log (signal_id, event_type, payload) "
                "VALUES (%s, 'not_a_real_stage', '{}'::jsonb)",
                (str(uuid.uuid4()),),
            )
