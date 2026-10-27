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


# ─────────────────────────────────────────────────────────────────────────────
# AUD-01, the half 046 deferred: the hash chain (061).
#
# 046's own header says why it stopped short -- "NOT ATTEMPTED HERE: hash chaining...
# designing a new tamper-evidence primitive from scratch... is a platform-wide design
# decision" -- and distinguishes tamper RESISTANCE (its triggers) from tamper EVIDENCE.
# 061 adds the evidence half, and this is where it is held to it.
#
# WHY EVERY TEST BELOW WORKS WITH THE APPEND-ONLY TRIGGERS DISABLED.
#
# That is not a convenience to make the fixtures easier. It IS the threat model. 046's
# trigger already refuses UPDATE and DELETE to every role including the owner, so a test
# that only tries the refused operation measures 046, which the class above already does.
# The chain exists for the cases that never pass through the trigger at all: an owner who
# runs ALTER TABLE ... DISABLE TRIGGER, a restore from a doctored backup, a logical
# replication stream replayed with rows dropped. Disabling the trigger is how a test puts
# itself in that position.
# ─────────────────────────────────────────────────────────────────────────────

#: 046's CHECK constraint: event_type IN ('created','risk_decision','order','execution').
#: Using anything else makes an INSERT fail on the constraint, which can make a test pass
#: for the wrong reason -- the statement was refused before any trigger ran.
_LIFECYCLE = ("created", "risk_decision", "order", "execution")


def _empty_the_audit_log(conn):
    """Remove every row, going around the append-only trigger.

    Only ever called against a test database. The production posture is the opposite --
    061's own preflight REFUSES to install the chain over a non-empty table, precisely so
    that nobody reaches for this.
    """
    cur = conn.cursor()
    cur.execute("ALTER TABLE public.signal_audit_log DISABLE TRIGGER USER")
    try:
        cur.execute("DELETE FROM public.signal_audit_log")
    finally:
        cur.execute("ALTER TABLE public.signal_audit_log ENABLE TRIGGER USER")


def _behind_the_triggers(conn, *statements: str):
    """Run statements the append-only trigger would refuse: the attacker's position."""
    cur = conn.cursor()
    cur.execute("ALTER TABLE public.signal_audit_log DISABLE TRIGGER USER")
    try:
        for stmt in statements:
            cur.execute(stmt)
    finally:
        cur.execute("ALTER TABLE public.signal_audit_log ENABLE TRIGGER USER")


def _seed_chain(conn, n=4):
    """Write an n-row chain through the normal (trigger-assigned) path."""
    signal_id = str(uuid.uuid4())
    cur = conn.cursor()
    for i in range(n):
        cur.execute(
            "INSERT INTO public.signal_audit_log "
            "(signal_id, event_type, user_id, strategy_id, payload) "
            "VALUES (%s, %s, %s, %s, %s::jsonb)",
            (signal_id, _LIFECYCLE[i % len(_LIFECYCLE)], str(uuid.uuid4()),
             # A separator inside the data on purpose: see
             # test_a_separator_inside_strategy_id_cannot_collide_two_rows.
             f"strat|with|pipes-{i}", '{"i": %d}' % i),
        )
    return signal_id


def _breaks(conn):
    cur = conn.cursor()
    cur.execute("SELECT seq, problem FROM public.verify_signal_audit_chain()")
    return cur.fetchall()


def _head(conn):
    cur = conn.cursor()
    cur.execute("SELECT row_count, max_seq, head_hash FROM public.signal_audit_chain_head()")
    return cur.fetchone()


@_needs_pg
class TestSignalAuditLogHashChain:
    @pytest.fixture(autouse=True)
    def _isolated_chain(self, db):
        """Every test gets the whole table to itself, and leaves it empty and intact.

        The properties under test are whole-chain ones -- "verification reports nothing",
        "the first row is the only genesis" -- so they cannot be scoped to one signal_id
        the way a per-row assertion could. Leaving the table empty afterwards also keeps
        this class from breaking the class above, which inserts a row and asserts on it.
        """
        _empty_the_audit_log(db)
        yield
        _empty_the_audit_log(db)

    # ── structure ────────────────────────────────────────────────────────────
    def test_the_chain_columns_and_trigger_are_installed(self, db):
        cur = db.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='signal_audit_log' "
            "AND column_name IN ('seq','prev_hash','row_hash') ORDER BY column_name"
        )
        assert [r[0] for r in cur.fetchall()] == ["prev_hash", "row_hash", "seq"]

        cur.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid='public.signal_audit_log'::regclass "
            "AND NOT tgisinternal ORDER BY tgname"
        )
        names = [r[0] for r in cur.fetchall()]
        assert "trg_signal_audit_log_chain" in names, (
            "the chain trigger is absent, so inserts would store NULL hashes and "
            f"verification would be decorative; triggers present: {names}"
        )
        # 061 must not have traded 046's prevention for its own detection.
        assert "trg_signal_audit_log_no_update_delete" in names
        assert "trg_signal_audit_log_no_truncate" in names

    def test_seq_is_not_an_identity_column(self, db):
        """The regression guard for the concurrency bug 061's header documents.

        `seq bigint GENERATED ALWAYS AS IDENTITY` looks like the better design and is the
        reason the first draft forked the chain: a column default is evaluated while the
        tuple is formed, which is BEFORE the BEFORE-INSERT trigger and therefore OUTSIDE
        the advisory lock. The seq a session gets then has no relation to the order in
        which sessions acquire the lock, so `ORDER BY seq DESC LIMIT 1` stops returning
        the actual head. Measured: the session that acquired the lock 4th got seq=161 and
        the 5th got seq=158, so the 6th read seq=161's hash again and two rows claimed one
        predecessor.
        """
        cur = db.cursor()
        cur.execute(
            "SELECT is_identity, is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='signal_audit_log' "
            "AND column_name='seq'"
        )
        is_identity, is_nullable = cur.fetchone()
        assert is_identity == "NO", (
            "seq is an identity column again. nextval() then runs outside the advisory "
            "lock and concurrent inserts fork the chain; the trigger must assign seq "
            "itself, in the same read that finds the predecessor."
        )
        assert is_nullable == "NO"

    def test_a_client_supplied_seq_is_overwritten_by_the_trigger(self, db):
        """seq is no longer protected by GENERATED ALWAYS, so the trigger must do it."""
        _seed_chain(db, 3)
        cur = db.cursor()
        cur.execute("SELECT max(seq) FROM public.signal_audit_log")
        before = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO public.signal_audit_log (signal_id, event_type, payload, seq) "
            "VALUES (%s, 'created', '{}'::jsonb, %s) RETURNING seq",
            (str(uuid.uuid4()), 999_999),
        )
        assert cur.fetchone()[0] == before + 1, (
            "a client chose its own position in the chain"
        )
        assert _breaks(db) == []

    def test_a_fork_is_refused_by_a_constraint_not_merely_detected(self, db):
        """uq_signal_audit_log_prev_hash: each row precedes at most one other row.

        The backstop for the bug above. With it, a second row claiming an already-claimed
        predecessor cannot be stored at all -- which matters because a stored fork makes
        verification report tampering that never happened.
        """
        cur = db.cursor()
        cur.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='public.signal_audit_log'::regclass "
            "AND conname='uq_signal_audit_log_prev_hash'"
        )
        row = cur.fetchone()
        assert row is not None, "the anti-fork constraint is missing"
        assert "NULLS NOT DISTINCT" in row[0], (
            "a plain UNIQUE treats every NULL as distinct, so a second genesis row would "
            f"be accepted and the chain could be restarted mid-history: {row[0]}"
        )

        # And it actually bites: forge a duplicate predecessor behind the triggers.
        _seed_chain(db, 3)
        cur.execute("SELECT prev_hash FROM public.signal_audit_log "
                    "WHERE prev_hash IS NOT NULL ORDER BY seq LIMIT 1")
        claimed = cur.fetchone()[0]
        with pytest.raises(Exception, match="uq_signal_audit_log_prev_hash"):
            _behind_the_triggers(
                db,
                "UPDATE public.signal_audit_log SET prev_hash = "
                f"'{claimed}' WHERE seq = (SELECT max(seq) FROM public.signal_audit_log)",
            )
        db.rollback()

    def test_only_one_genesis_row_can_exist(self, db):
        _seed_chain(db, 3)
        with pytest.raises(Exception, match="uq_signal_audit_log_prev_hash"):
            _behind_the_triggers(
                db,
                "UPDATE public.signal_audit_log SET prev_hash = NULL "
                "WHERE seq = (SELECT max(seq) FROM public.signal_audit_log)",
            )
        db.rollback()

    # ── the chain does what it claims ────────────────────────────────────────
    def test_an_intact_chain_reports_no_breaks(self, db):
        _seed_chain(db, 5)
        assert _breaks(db) == []

        cur = db.cursor()
        cur.execute(
            "SELECT count(*) FROM public.signal_audit_log WHERE prev_hash IS NULL"
        )
        assert cur.fetchone()[0] == 1, "exactly one genesis row is expected"

        cur.execute(
            "SELECT count(*) FROM public.signal_audit_log "
            "WHERE row_hash IS NULL OR length(row_hash) <> 64"
        )
        assert cur.fetchone()[0] == 0, "every row must carry a full sha256 hex digest"

    def test_the_client_cannot_supply_its_own_hashes(self, db):
        signal_id = _seed_chain(db, 2)
        cur = db.cursor()
        cur.execute(
            "INSERT INTO public.signal_audit_log "
            "(signal_id, event_type, payload, prev_hash, row_hash) "
            "VALUES (%s, 'created', '{}'::jsonb, %s, %s) RETURNING prev_hash, row_hash",
            (signal_id, "deadbeef", "f" * 64),
        )
        prev, row = cur.fetchone()
        assert prev != "deadbeef", (
            "the insert trigger let a client set prev_hash, so a forged row could be "
            "stitched into the chain consistently"
        )
        assert row != "f" * 64, "the insert trigger let a client set row_hash"
        assert _breaks(db) == []

    def test_a_row_deleted_behind_the_append_only_trigger_is_detected(self, db):
        _seed_chain(db, 5)
        cur = db.cursor()
        cur.execute(
            "SELECT seq FROM public.signal_audit_log ORDER BY seq OFFSET 2 LIMIT 1"
        )
        victim = cur.fetchone()[0]
        _behind_the_triggers(
            db, f"DELETE FROM public.signal_audit_log WHERE seq = {victim}"
        )

        found = _breaks(db)
        assert found, (
            "a row was removed with the append-only trigger disabled and verification "
            "reported nothing. This is the single case the chain exists for: the trigger "
            "cannot stop an owner who turns it off, so the chain has to leave evidence."
        )
        assert any("removed, altered or reordered" in problem for _, problem in found)

    def test_a_row_altered_behind_the_append_only_trigger_is_detected(self, db):
        _seed_chain(db, 5)
        _behind_the_triggers(
            db,
            "UPDATE public.signal_audit_log SET payload = '{\"tampered\": true}'::jsonb "
            "WHERE seq = (SELECT min(seq) + 2 FROM public.signal_audit_log)",
        )
        found = _breaks(db)
        assert any("this row was altered" in problem for _, problem in found), (
            f"an edited row was not reported as altered; verification said: {found}"
        )

    def test_deleting_the_head_of_the_chain_is_detected(self, db):
        """The first surviving row then carries a prev_hash with nothing before it."""
        _seed_chain(db, 5)
        _behind_the_triggers(
            db,
            "DELETE FROM public.signal_audit_log "
            "WHERE seq <= (SELECT min(seq) + 1 FROM public.signal_audit_log)",
        )
        found = _breaks(db)
        assert any("rows before it were removed" in problem for _, problem in found), (
            f"head truncation was not reported; verification said: {found}"
        )

    def test_a_reordered_chain_is_detected(self, db):
        """Swapping two rows' positions, with the append-only triggers disabled.

        Since seq is in the digest preimage, a rewritten seq shows up directly as "this
        row was altered" as well as indirectly as a broken link.
        """
        _seed_chain(db, 5)
        cur = db.cursor()
        cur.execute("SELECT min(seq), min(seq) + 1 FROM public.signal_audit_log")
        first, second = cur.fetchone()
        _behind_the_triggers(
            db,
            f"UPDATE public.signal_audit_log SET seq = -1 WHERE seq = {first}",
            f"UPDATE public.signal_audit_log SET seq = {first} WHERE seq = {second}",
            f"UPDATE public.signal_audit_log SET seq = {second} WHERE seq = -1",
        )
        found = _breaks(db)
        assert found, "two rows were swapped and verification reported nothing"
        assert any("altered" in problem for _, problem in found), (
            f"seq is in the preimage, so a swap should read as an alteration: {found}"
        )

    # ── the limits 061 documents, asserted so they stay documented ───────────
    def test_tail_truncation_is_invisible_to_verify_but_visible_to_the_head_anchor(self, db):
        """061's header states this limit. An untested limit is a guess, so it is tested.

        It also pins the reason signal_audit_chain_head() exists: if this ever starts
        failing because verification DID report the truncation, 061's header is wrong and
        the anchoring advice in it is unnecessary.
        """
        _seed_chain(db, 5)
        anchored = _head(db)
        assert anchored[0] == 5

        _behind_the_triggers(
            db,
            "DELETE FROM public.signal_audit_log "
            "WHERE seq >= (SELECT max(seq) - 1 FROM public.signal_audit_log)",
        )

        assert _breaks(db) == [], (
            "verify_signal_audit_chain() reported a break for tail truncation. That is "
            "BETTER than documented -- but 061's header says it cannot, and tells "
            "operators to anchor the head externally because of it. Update the header."
        )
        now = _head(db)
        assert now[0] == 3 and now != anchored, (
            "the head anchor did not move after the newest rows were deleted, which "
            "would leave tail truncation undetectable by any means"
        )

    def test_an_emptied_table_is_a_vacuously_intact_chain(self, db):
        _seed_chain(db, 3)
        assert _head(db)[0] == 3
        _empty_the_audit_log(db)
        assert _breaks(db) == []
        assert _head(db) == (0, None, None), (
            "chain_head() must report an empty table honestly -- it is the only signal "
            "that every row was deleted, since the chain itself cannot tell"
        )

    # ── the digest ───────────────────────────────────────────────────────────
    def test_the_digest_is_bound_to_the_rows_position_in_the_chain(self, db):
        """Identical contents, different predecessor: the digests must differ.

        Otherwise a row could be lifted out of one point in the chain and dropped in at
        another, and both positions would verify.
        """
        cur = db.cursor()
        args = (
            7, str(uuid.uuid4()), str(uuid.uuid4()), "execution", str(uuid.uuid4()),
            "strat|x", "2026-03-14T15:09:26.535897+05:30", '{"a": 1}',
        )
        cur.execute(
            "SELECT public.signal_audit_row_digest(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb), "
            "       public.signal_audit_row_digest(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb), "
            "       public.signal_audit_row_digest(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            ("aaaa", *args, "bbbb", *args, "aaaa", *args),
        )
        under_a, under_b, under_a_again = cur.fetchone()
        assert under_a != under_b, "prev_hash is not actually part of the preimage"
        assert under_a == under_a_again, "the digest is not reproducible"
        assert len(under_a) == 64

        # And the position is in the preimage too, which is what makes a rewritten seq
        # read as an alteration rather than only as a broken link.
        rest = args[1:]
        cur.execute(
            "SELECT public.signal_audit_row_digest(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb), "
            "       public.signal_audit_row_digest(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            ("aaaa", 7, *rest, "aaaa", 8, *rest),
        )
        at_7, at_8 = cur.fetchone()
        assert at_7 != at_8, "seq is not part of the preimage"

    def test_a_separator_inside_strategy_id_cannot_collide_two_rows(self, db):
        """Why the preimage is canonical jsonb and not `a || '|' || b`.

        strategy_id is free text. Under a naive concatenation these two materially
        different rows render identically, so one could be substituted for the other.
        """
        cur = db.cursor()
        common = (str(uuid.uuid4()), str(uuid.uuid4()))
        uid = str(uuid.uuid4())
        ts = "2026-03-14T15:09:26.535897+05:30"
        cur.execute(
            "SELECT public.signal_audit_row_digest(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb), "
            "       public.signal_audit_row_digest(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            ("p", 1, *common, "created", uid, "x|execution", ts, "{}",
             "p", 1, *common, "created|x", uid, "execution", ts, "{}"),
        )
        split_one, split_two = cur.fetchone()
        assert split_one != split_two, (
            "two different (event_type, strategy_id) splits produced the same digest, so "
            "content containing the field separator can impersonate another row"
        )

    def test_the_digest_does_not_move_with_session_timezone_or_datestyle(self, db):
        """Two connections must agree about the same row, or verification cries wolf.

        recorded_at is formatted with to_char, which PostgreSQL marks STABLE rather than
        IMMUTABLE because its output CAN depend on DateStyle and lc_time. 061's format
        string is numeric-only so it does not -- this is what holds that true.
        """
        cur = db.cursor()
        # `SET TimeZone` validates the name against the server's timezone database, and
        # the `pgserver` build used for local provisioning ships none at all -- TimeZone is
        # stuck on the GMT fallback and pg_timezone_names raises UndefinedFile. CI runs
        # postgres:17-alpine, which has it, so this skip never fires there. Narrow on
        # purpose: it is the ONE axis that needs tz data, and the DateStyle half below is
        # the half that to_char could plausibly have been sensitive to anyway.
        try:
            cur.execute("SELECT 1 FROM pg_timezone_names LIMIT 1")
            cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            pytest.skip(f"server has no timezone database, so TimeZone cannot be set: {exc}")
        args = (
            "prev", 3, str(uuid.uuid4()), str(uuid.uuid4()), "execution",
            str(uuid.uuid4()), "strat|x", "2026-03-14T15:09:26.535897+05:30", '{"a": 1}',
        )
        digests = set()
        for tz, datestyle in (
            ("UTC", "ISO, MDY"),
            ("Asia/Kolkata", "German, DMY"),
            ("America/New_York", "SQL, DMY"),
            ("Pacific/Kiritimati", "Postgres, YMD"),
        ):
            cur.execute(f"SET TimeZone = '{tz}'")
            cur.execute(f"SET DateStyle = '{datestyle}'")
            cur.execute(
                "SELECT public.signal_audit_row_digest(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                args,
            )
            digests.add(cur.fetchone()[0])
        cur.execute("RESET TimeZone")
        cur.execute("RESET DateStyle")

        assert len(digests) == 1, (
            "the digest depends on session settings, so two connections disagree about "
            f"the same row and verification reports breaks that are not tampering: {digests}"
        )

    def test_the_declared_volatility_matches_what_the_body_can_promise(self, db):
        """STABLE, not IMMUTABLE -- and not VOLATILE either.

        PostgreSQL does not check a declared volatility against the body, so IMMUTABLE
        here would be a claim the planner believes and acts on (constant-folding,
        eligibility for index expressions). to_char(timestamptz,text),
        convert_to(text,name) and jsonb_build_object(VARIADIC "any") are all STABLE, so
        STABLE is the strongest honest declaration.
        """
        cur = db.cursor()
        cur.execute(
            "SELECT p.proname, CASE p.provolatile WHEN 'i' THEN 'IMMUTABLE' "
            "       WHEN 's' THEN 'STABLE' ELSE 'VOLATILE' END "
            "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            " WHERE n.nspname = 'public' "
            "   AND p.proname IN ('signal_audit_row_digest', 'verify_signal_audit_chain', "
            "                     'signal_audit_chain_head') "
            " ORDER BY 1"
        )
        assert cur.fetchall() == [
            ("signal_audit_chain_head", "STABLE"),
            ("signal_audit_row_digest", "STABLE"),
            ("verify_signal_audit_chain", "STABLE"),
        ]

    def test_the_insert_trigger_function_is_volatile(self, db):
        """VOLATILE is a CORRECTNESS condition here, not a style choice.

        The trigger reads the chain head after waiting on the advisory lock. In READ
        COMMITTED a statement's snapshot is taken when the statement starts, so a session
        that waited could otherwise read a pre-wait snapshot and miss the row the previous
        holder just committed -- forking the chain. Measured on a real server: a VOLATILE
        plpgsql function takes a fresh snapshot per command and sees the committed head;
        the same probe marked STABLE saw zero rows.
        """
        cur = db.cursor()
        cur.execute(
            "SELECT CASE p.provolatile WHEN 'i' THEN 'IMMUTABLE' "
            "       WHEN 's' THEN 'STABLE' ELSE 'VOLATILE' END "
            "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            " WHERE n.nspname='public' AND p.proname='signal_audit_log_chain'"
        )
        assert cur.fetchone() == ("VOLATILE",), (
            "signal_audit_log_chain() must stay VOLATILE: a STABLE or IMMUTABLE marking "
            "makes its head read use the calling statement's snapshot, which cannot see "
            "the row committed by the session that held the lock before it"
        )

    def test_there_is_exactly_one_digest_function(self, db):
        """Two overloads would let the writer and the checker use different rules.

        CREATE OR REPLACE with a changed signature creates an overload rather than
        replacing, so the 8-argument draft could have survived beside the 9-argument one.
        """
        cur = db.cursor()
        cur.execute(
            "SELECT oidvectortypes(p.proargtypes) "
            "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            " WHERE n.nspname='public' AND p.proname='signal_audit_row_digest'"
        )
        signatures = [r[0] for r in cur.fetchall()]
        assert len(signatures) == 1, (
            f"expected exactly one signal_audit_row_digest, found: {signatures}"
        )
        assert signatures[0] == (
            "text, bigint, uuid, uuid, text, uuid, text, timestamp with time zone, jsonb"
        ), signatures[0]

    # ── concurrency: the reason for the advisory lock ────────────────────────
    def test_concurrent_inserts_do_not_fork_the_chain(self, db):
        """Without the advisory lock, two inserts read the same head and both claim it.

        That produces two rows with one predecessor -- a break verification would report
        as tampering when nothing tampered. This is the property the serialisation buys,
        and the only way to measure it is with genuinely concurrent connections.
        """
        import threading

        psycopg2 = pytest.importorskip("psycopg2")
        _seed_chain(db, 1)

        writers = 8
        barrier = threading.Barrier(writers)
        errors: list[str] = []

        def insert_one(i):
            try:
                conn = psycopg2.connect(DSN)
                conn.autocommit = True
                try:
                    cur = conn.cursor()
                    barrier.wait(timeout=30)
                    cur.execute(
                        "INSERT INTO public.signal_audit_log "
                        "(signal_id, event_type, payload) "
                        "VALUES (%s, 'order', %s::jsonb)",
                        (str(uuid.uuid4()), '{"w": %d}' % i),
                    )
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"worker {i}: {exc}")

        threads = [threading.Thread(target=insert_one, args=(i,)) for i in range(writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not errors, f"concurrent inserts failed: {errors}"

        cur = db.cursor()
        cur.execute("SELECT count(*) FROM public.signal_audit_log")
        assert cur.fetchone()[0] == writers + 1

        # One predecessor each, one genesis, no forks.
        cur.execute(
            "SELECT count(*) FROM (SELECT prev_hash FROM public.signal_audit_log "
            " WHERE prev_hash IS NOT NULL GROUP BY prev_hash HAVING count(*) > 1) f"
        )
        assert cur.fetchone()[0] == 0, (
            "two rows claim the same predecessor: the chain forked under concurrency, "
            "which is what the advisory lock in signal_audit_log_chain() prevents"
        )
        assert _breaks(db) == [], (
            f"{writers} concurrent inserts produced a chain that does not verify"
        )

    # ── grants ───────────────────────────────────────────────────────────────
    def test_the_browser_roles_cannot_execute_the_chain_functions(self, db):
        """Verification reads every row's digest -- a whole-table read RLS would mediate."""
        cur = db.cursor()
        for fn in ("verify_signal_audit_chain()",
                   "signal_audit_chain_head()",
                   "signal_audit_row_digest"
                   "(text,bigint,uuid,uuid,text,uuid,text,timestamptz,jsonb)"):
            for role in ("anon", "authenticated"):
                cur.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                    (role, f"public.{fn}"),
                )
                assert cur.fetchone()[0] is False, (
                    f"{role} can execute {fn}, which exposes the whole audit log's "
                    "digests to a browser role"
                )
        cur.execute(
            "SELECT has_function_privilege('service_role', "
            "       'public.verify_signal_audit_chain()', 'EXECUTE')"
        )
        assert cur.fetchone()[0] is True, (
            "service_role cannot verify the chain, so nothing in the backend can"
        )
