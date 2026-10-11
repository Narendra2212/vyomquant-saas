"""TENANT-02 — `fee_records` row level security, measured against real PostgreSQL.

WHAT WAS WRONG
--------------
`fee_records` carries per-customer trading fees keyed on `tenant_id`, and on the
provisioned database it had ROW LEVEL SECURITY DISABLED with `anon` and `authenticated`
each holding SELECT, INSERT, UPDATE and DELETE. Three independent tests in the Phase 7
PostgreSQL lane agreed, and they were precise rather than sweeping — 43 and 69 other
tables blocked correctly::

    test_cross_tenant_read_sweep_postgres   fee_records  anon saw 1 of 1 row(s)
    test_cross_tenant_read_sweep_postgres   fee_records  authenticated saw 1 of 1 row(s)
    test_production_security_probe_...      fee_records  (b_select=ALLOWED;
                                                          b_update=ALLOWED;
                                                          b_delete=ALLOWED)

Root cause was structural: the table existed **only** as a SQLAlchemy model
(`fee_engine.FeeRecordModel`) created by `Base.metadata.create_all()`, and zero `.sql`
files in the repository mentioned it — so the `ENABLE ROW LEVEL SECURITY` and
`CREATE POLICY` statements every migration-created table receives did not exist for it
anywhere, while `scripts/provision_database.py`'s
`ALTER DEFAULT PRIVILEGES ... GRANT ALL ON TABLES TO anon, authenticated, service_role`
handed it browser privileges at creation time without naming it.

WHAT THIS FILE ASSERTS, AND WHY IT IS BUILT THE WAY IT IS
---------------------------------------------------------
**Privileges and RLS are read from the PostgreSQL catalogues**, not inferred from an
application response. An application that returns no rows may be filtering correctly, or
may be failing for an unrelated reason, or may be reading a different table. `pg_class`,
`pg_policies` and `information_schema.role_table_grants` are the authority.

**The owner policy is exercised, not merely counted.** Migration 026 revokes every
privilege from both browser roles, because no code path in this repository reaches
`fee_records` other than through SQLAlchemy as the table owner. That is the right posture
and it makes the owner policy unobservable — with no privilege there is nothing for a
policy to filter. So the cross-tenant tests below **grant `SELECT` to `authenticated`
inside a transaction and roll it back**, which is the only way to prove the policy
predicate actually isolates tenants rather than just existing. A policy nobody has
executed is a claim.

**`service_role` holds `BYPASSRLS`** on this database (measured: `rolbypassrls=True`), so
row security does not apply to it at all and `fee_records_service_role` is belt-and-braces
rather than the thing that lets the backend work. That is stated here rather than left for
a reader to assume the policy is load-bearing.

RUNS ONLY AGAINST REAL POSTGRESQL. SQLite has no roles, no `SET ROLE`, no RLS and no
`pg_policies`, so there is nothing here it could measure. Skips — never silently passes.
"""

import os
import pathlib
import uuid
from datetime import datetime
from decimal import Decimal

import pytest

# ══════════════════════════════════════════════════════════════════════════
#  THE DATABASE, OR AN HONEST SKIP
# ══════════════════════════════════════════════════════════════════════════

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _resolve_dsn() -> str:
    """`AERORA_TEST_DATABASE_URL`, else `staging/pg_dsn.txt`, else empty."""
    dsn = (os.environ.get("AERORA_TEST_DATABASE_URL") or "").strip()
    if dsn:
        return dsn
    path = _REPO_ROOT / "staging" / "pg_dsn.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


DSN = _resolve_dsn()

if not DSN or not DSN.startswith("postgres"):
    pytest.skip(
        "TENANT-02 is about PostgreSQL row level security, roles and catalogues. "
        "AERORA_TEST_DATABASE_URL is unset or not PostgreSQL, and staging/pg_dsn.txt "
        "names no PostgreSQL server. NOT a pass.",
        allow_module_level=True,
    )

try:
    import psycopg2
    import psycopg2.extras
except Exception as exc:  # pragma: no cover
    pytest.skip(
        f"psycopg2 is not importable ({exc}), so this measurement cannot be taken. "
        f"NOT a pass.",
        allow_module_level=True,
    )

#: The claim `auth.uid()` reads. `scripts/provision_database.py` defines `auth.uid()` as a
#: COALESCE of `request.jwt.claims` (jsonb) with a object built from this GUC, which is why
#: every probe and test in this repository sets this one.
CLAIM_GUC = "request.jwt.claim.sub"

TABLE = "fee_records"
OWNER_POLICY = "fee_records_owner_access"
SERVICE_POLICY = "fee_records_service_role"


def _connect():
    conn = psycopg2.connect(DSN, connect_timeout=10)
    conn.autocommit = False
    return conn


@pytest.fixture(scope="module")
def db():
    """One owner connection for the module, rolled back between tests.

    Connects as whatever the DSN names — `postgres` on this substrate, which is also the
    table owner. That matters: the owner is exempt from its own policies unless `FORCE` is
    set, and 026 deliberately uses `ENABLE`, so this connection sees every row. Tests that
    need a browser identity adopt one with `SET LOCAL ROLE` inside a transaction.
    """
    try:
        conn = _connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"the configured PostgreSQL is not reachable, so this measurement could not "
            f"be taken: {type(exc).__name__}: {str(exc).strip()[:160]}. NOT a pass.",
            allow_module_level=True,
        )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=%s",
            (TABLE,),
        )
        if not cur.fetchone()[0]:
            conn.close()
            pytest.skip(
                "public.fee_records is not present on this database, so migration 026 "
                "has not been applied here. NOT a pass.",
                allow_module_level=True,
            )
        for role in ("anon", "authenticated"):
            cur.execute("SELECT count(*) FROM pg_roles WHERE rolname=%s", (role,))
            if not cur.fetchone()[0]:
                conn.close()
                pytest.skip(
                    f"role {role!r} does not exist on this database, so the browser-role "
                    f"denial cannot be measured. Run provision_database.py "
                    f"--create-substrate. NOT a pass.",
                    allow_module_level=True,
                )
    conn.commit()
    yield conn
    conn.rollback()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
#  SYNTHETIC TENANTS AND ROWS
# ══════════════════════════════════════════════════════════════════════════

def _fee_row(tenant_id: str, *, marker: str) -> dict:
    """A complete `fee_records` row.

    Every NOT NULL column is supplied explicitly, including `created_at`, which has **no
    server default** — SQLAlchemy provides it from `default=datetime.utcnow` and a raw
    INSERT must too. `execution_id` and `position_id` are left NULL on purpose: both carry
    foreign keys to Alembic-owned tables, and this file's subject is row security rather
    than referential integrity.
    """
    return {
        "fee_id": f"fee_{marker}_{uuid.uuid4().hex[:12]}",
        "tenant_id": tenant_id,
        "execution_id": None,
        "order_id": f"order_{marker}",
        "position_id": None,
        "symbol": "BTC/USDT",
        "side": "buy",
        "fee_type": "TAKER",
        "fee_amount": "0.05000000",
        "fee_asset": "USDT",
        "fee_pct": "0.00050000",
        "trade_volume": "100.00000000",
        "gross_pnl": None,
        "net_pnl": None,
        "status": "DEDUCTED",
        "exchange_id": "binance",
        "created_at": datetime.utcnow(),
    }


_INSERT = """
INSERT INTO public.fee_records
    (fee_id, tenant_id, execution_id, order_id, position_id, symbol, side, fee_type,
     fee_amount, fee_asset, fee_pct, trade_volume, gross_pnl, net_pnl, status,
     exchange_id, created_at)
VALUES
    (%(fee_id)s, %(tenant_id)s, %(execution_id)s, %(order_id)s, %(position_id)s,
     %(symbol)s, %(side)s, %(fee_type)s, %(fee_amount)s, %(fee_asset)s, %(fee_pct)s,
     %(trade_volume)s, %(gross_pnl)s, %(net_pnl)s, %(status)s, %(exchange_id)s,
     %(created_at)s)
"""


@pytest.fixture
def tenants(db):
    """Two synthetic tenants, each with one fee row, removed afterwards.

    Committed rather than held in an open transaction, because the browser-role tests below
    run in their own transactions and must be able to see — or fail to see — rows that
    really exist. Torn down by `fee_id`, so a failure cannot leave the table dirty for the
    cross-tenant read sweep that runs later in the same suite.
    """
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
    row_a = _fee_row(a, marker="tenant_a")
    row_b = _fee_row(b, marker="tenant_b")

    with db.cursor() as cur:
        cur.execute(_INSERT, row_a)
        cur.execute(_INSERT, row_b)
    db.commit()

    try:
        yield {"a": a, "b": b, "row_a": row_a, "row_b": row_b}
    finally:
        with db.cursor() as cur:
            cur.execute(
                "DELETE FROM public.fee_records WHERE fee_id = ANY(%s)",
                ([row_a["fee_id"], row_b["fee_id"]],),
            )
        db.commit()


def _as_role(cur, role: str, claim: str | None) -> None:
    """Adopt the identity PostgREST would present.

    `set_config(..., true)` and `SET LOCAL ROLE` are both TRANSACTION-scoped, so the
    caller's transaction is the whole lifetime of the identity and nothing leaks into the
    next test through the connection. The claim is a bound parameter rather than
    interpolated, because `SET` takes no bind parameters — which is exactly why
    `core/tenant_db_context.py` uses `set_config` too.
    """
    cur.execute("SELECT set_config(%s, %s, true)", (CLAIM_GUC, claim or ""))
    cur.execute(f"SET LOCAL ROLE {role}")


# ══════════════════════════════════════════════════════════════════════════
#  1. THE CATALOGUES — what PostgreSQL itself says
# ══════════════════════════════════════════════════════════════════════════

def test_row_level_security_is_enabled_and_not_forced(db):
    """ENABLE, deliberately not FORCE.

    `fee_records` is owned by `postgres` and every DSN in this repository connects as
    `postgres`. An owner is exempt from its own policies unless FORCE is set, so ENABLE
    closes the PostgREST surface and leaves all six `FeeEngine` methods working. FORCE
    would break them. Both halves are asserted, because enabling the wrong one is a silent
    outage and enabling neither is the original defect.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT c.relrowsecurity, c.relforcerowsecurity, c.relowner::regrole::text "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=%s",
            (TABLE,),
        )
        enabled, forced, owner = cur.fetchone()
    db.rollback()

    assert enabled is True, (
        "row level security is DISABLED on public.fee_records — this is the TENANT-02 "
        "defect, and migration 026 has not taken effect"
    )
    assert forced is False, (
        "FORCE ROW LEVEL SECURITY is set. The table owner is the account the application "
        "connects as, so FORCE would make every FeeEngine read and write subject to the "
        "policies and break fee recording. 026 uses ENABLE for exactly this reason."
    )
    assert owner == "postgres", (
        f"fee_records is owned by {owner!r}, not postgres. The ENABLE-not-FORCE reasoning "
        f"above depends on the application connecting as the owner; if that is no longer "
        f"true, the posture needs re-deciding rather than re-asserting."
    )


def test_both_policies_exist_with_the_intended_shape(db):
    """The owner policy and the service-role policy, with their predicates read back.

    Asserting the predicate text, not just the policy name: a policy called
    `fee_records_owner_access` whose `USING` clause is `true` would filter nothing while
    looking protective in a catalogue listing. That is the `library_ratings` shape
    `migrations/hardening/999_privilege_hardening.sql`'s third backstop exists to catch.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT policyname, cmd, roles::text, qual, with_check FROM pg_policies "
            "WHERE schemaname='public' AND tablename=%s ORDER BY policyname",
            (TABLE,),
        )
        policies = {r[0]: r for r in cur.fetchall()}
    db.rollback()

    assert set(policies) == {OWNER_POLICY, SERVICE_POLICY}, (
        f"expected exactly the two policies 026 declares, found {sorted(policies)}"
    )

    _, owner_cmd, owner_roles, owner_qual, owner_check = policies[OWNER_POLICY]
    assert owner_cmd == "SELECT", (
        f"the owner policy is {owner_cmd}, not SELECT. A fee record is written by the "
        f"platform from an exchange fill; a customer reads their own fees and never "
        f"authors one, so a write policy would describe a client surface that does not "
        f"exist."
    )
    assert "authenticated" in owner_roles
    assert owner_qual is not None and owner_qual.strip().lower() != "true", (
        f"the owner policy's USING predicate is {owner_qual!r}. A constant-true predicate "
        f"filters nothing and is worse than no policy, because the table reports as "
        f"secured."
    )
    assert "tenant_id" in owner_qual and "uid" in owner_qual, (
        f"the owner policy does not compare auth.uid() to tenant_id: {owner_qual!r}"
    )
    assert owner_check is None, "a SELECT policy cannot carry WITH CHECK"

    _, svc_cmd, svc_roles, svc_qual, svc_check = policies[SERVICE_POLICY]
    assert svc_cmd == "ALL"
    assert "service_role" in svc_roles
    assert (svc_qual or "").strip().lower() == "true"
    assert (svc_check or "").strip().lower() == "true", (
        "the service_role policy omits WITH CHECK, so a reader cannot tell whether the "
        "omission means 'any new row is allowed' deliberately or by accident"
    )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_no_browser_role_holds_any_privilege(db, role):
    """THE DEFECT, read straight from `information_schema`.

    Before 026 each of these roles held DELETE, INSERT, SELECT and UPDATE. Both are now
    revoked entirely, because measurement established that no code path in this repository
    reaches `fee_records` other than through SQLAlchemy as the table owner — so the
    privileges were never load-bearing.

    Checked as a privilege rather than as a query result on purpose: with no privilege, a
    `SELECT` raises `permission denied` and with RLS alone it would return zero rows. Those
    are different postures and only the catalogue distinguishes them.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) "
            "FROM information_schema.role_table_grants "
            "WHERE table_schema='public' AND table_name=%s AND grantee=%s",
            (TABLE, role),
        )
        privileges = cur.fetchone()[0]
    db.rollback()

    assert privileges is None, (
        f"{role} still holds {privileges} on public.fee_records. The substrate's "
        f"ALTER DEFAULT PRIVILEGES grants these at table-creation time, and only a "
        f"statement naming the table can retract them — which is what 026 section 5 does."
    )


def test_service_role_keeps_what_the_backend_needs(db):
    """The backend must still be able to record a fee.

    Stated explicitly rather than inherited, so a future blanket revoke cannot silently
    break fee recording and have it discovered in production.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema='public' AND table_name=%s AND grantee='service_role'",
            (TABLE,),
        )
        privileges = {r[0] for r in cur.fetchall()}
    db.rollback()

    assert {"SELECT", "INSERT", "UPDATE"} <= privileges, (
        f"service_role holds only {sorted(privileges)} on fee_records; fee recording "
        f"needs SELECT, INSERT and UPDATE"
    )


def test_service_role_bypasses_rls_so_its_policy_is_belt_and_braces(db):
    """An honest note, asserted so it cannot quietly stop being true.

    `service_role` has `BYPASSRLS` on this substrate, so row security does not apply to it
    and `fee_records_service_role` is NOT what lets the backend work. If a future hardening
    pass removes `BYPASSRLS`, the policy becomes load-bearing — and this test is where a
    reader finds out which of the two situations they are in.
    """
    with db.cursor() as cur:
        cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname='service_role'")
        bypass = cur.fetchone()[0]
    db.rollback()

    assert bypass is True, (
        "service_role no longer has BYPASSRLS. That is not necessarily wrong, but it "
        "makes fee_records_service_role the thing the backend depends on, so the policy's "
        "WITH CHECK (true) now matters and this file's reasoning needs revisiting."
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. ANONYMOUS ACCESS IS DENIED — SELECT, INSERT, UPDATE, DELETE
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "statement,label",
    [
        ("SELECT count(*) FROM public.fee_records", "SELECT"),
        (
            "INSERT INTO public.fee_records (fee_id, tenant_id, order_id, symbol, side, "
            "fee_type, fee_amount, fee_asset, fee_pct, trade_volume, status, "
            "exchange_id, created_at) VALUES ('anon_forged', "
            "'11111111-1111-4111-8111-111111111111', 'o', 'BTC/USDT', 'buy', 'TAKER', "
            "'1', 'USDT', '0.001', '1', 'DEDUCTED', 'binance', now())",
            "INSERT",
        ),
        ("UPDATE public.fee_records SET fee_amount = '999999'", "UPDATE"),
        ("DELETE FROM public.fee_records", "DELETE"),
    ],
)
def test_anonymous_access_is_refused(db, tenants, statement, label):
    """The `anon` key ships in the browser, so this is the public internet.

    All four verbs, because the original grant was all four. A refusal is a raised
    `psycopg2.Error` (`permission denied for table fee_records`) now that the privilege is
    gone; before 026 the SELECT returned the row and the three writes succeeded.

    Each statement runs in its own savepoint so one refusal does not abort the next.
    """
    with db.cursor() as cur:
        cur.execute("SAVEPOINT anon_probe")
        _as_role(cur, "anon", None)
        refused = None
        try:
            cur.execute(statement)
        except psycopg2.Error as exc:
            refused = exc
        cur.execute("ROLLBACK TO SAVEPOINT anon_probe")
        cur.execute("RESET ROLE")
    db.rollback()

    assert refused is not None, (
        f"anon was permitted to {label} on public.fee_records. This is the TENANT-02 "
        f"exposure: the anon key is public, so this is readable or writable by anyone."
    )
    assert "permission denied" in str(refused).lower(), (
        f"anon's {label} was refused, but not by a privilege check: {refused}"
    )


def test_an_anonymous_write_leaves_every_row_byte_identical(db, tenants):
    """The refusals above say the statement failed. This says nothing changed.

    A refusal that still mutated a row would be the worst outcome, and asserting the
    exception alone cannot rule it out.
    """
    def snapshot():
        with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM public.fee_records WHERE fee_id = ANY(%s) ORDER BY fee_id",
                ([tenants["row_a"]["fee_id"], tenants["row_b"]["fee_id"]],),
            )
            return [dict(r) for r in cur.fetchall()]

    before = snapshot()
    assert len(before) == 2, "the fixture's two rows are not both present"

    with db.cursor() as cur:
        cur.execute("SAVEPOINT anon_write")
        _as_role(cur, "anon", None)
        for statement in (
            "UPDATE public.fee_records SET fee_amount = '999999'",
            "DELETE FROM public.fee_records",
        ):
            try:
                cur.execute(statement)
            except psycopg2.Error:
                cur.execute("ROLLBACK TO SAVEPOINT anon_write")
        cur.execute("RESET ROLE")
    db.rollback()

    assert snapshot() == before, "an anonymous write changed a row"


# ══════════════════════════════════════════════════════════════════════════
#  3. THE OWNER POLICY, EXERCISED RATHER THAN COUNTED
# ══════════════════════════════════════════════════════════════════════════
#
# 026 revokes every privilege from `authenticated`, so the owner policy has nothing to
# filter and cannot be observed. Granting SELECT inside a transaction and rolling it back
# is the only way to prove the predicate isolates tenants. Without this, "the policy
# exists" is the strongest claim available — and a policy nobody has executed is a claim.

def test_the_owner_policy_shows_a_tenant_exactly_their_own_rows(db, tenants):
    """Positive control AND cross-tenant denial, in the same transaction.

    Tenant A sees exactly one row — theirs. Tenant B sees exactly one row — theirs. Neither
    sees the other's. Both halves matter: a policy that showed nobody anything would pass a
    denial-only test.
    """
    with db.cursor() as cur:
        cur.execute("SAVEPOINT grant_probe")
        cur.execute("GRANT SELECT ON public.fee_records TO authenticated")

        _as_role(cur, "authenticated", tenants["a"])
        cur.execute("SELECT auth.uid()::text")
        assert cur.fetchone()[0] == tenants["a"], (
            "auth.uid() did not resolve to the claim this test installed, so whatever the "
            "policy filtered on, it was not this tenant"
        )
        cur.execute("SELECT fee_id, tenant_id::text FROM public.fee_records")
        seen_a = cur.fetchall()
        cur.execute("RESET ROLE")

        _as_role(cur, "authenticated", tenants["b"])
        cur.execute("SELECT fee_id, tenant_id::text FROM public.fee_records")
        seen_b = cur.fetchall()
        cur.execute("RESET ROLE")

        cur.execute("ROLLBACK TO SAVEPOINT grant_probe")
    db.rollback()

    assert [r[0] for r in seen_a] == [tenants["row_a"]["fee_id"]], (
        f"tenant A saw {seen_a} instead of exactly their own one row"
    )
    assert [r[0] for r in seen_b] == [tenants["row_b"]["fee_id"]], (
        f"tenant B saw {seen_b} instead of exactly their own one row"
    )
    assert all(r[1] == tenants["a"] for r in seen_a)
    assert all(r[1] == tenants["b"] for r in seen_b)


def test_the_grant_used_by_the_test_above_does_not_survive_it(db):
    """The hypothetical GRANT must not leak into the database.

    This file proves the policy by temporarily granting SELECT. If that grant escaped, the
    test would have re-opened the exposure it exists to verify is closed — so the rollback
    is asserted rather than trusted.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE table_schema='public' AND table_name=%s AND grantee='authenticated'",
            (TABLE,),
        )
        count = cur.fetchone()[0]
    db.rollback()
    assert count == 0, (
        "`authenticated` holds a privilege on fee_records after the policy test. The "
        "in-transaction GRANT leaked, which would re-open TENANT-02."
    )


def test_a_tenant_with_no_claim_sees_nothing(db, tenants):
    """An `authenticated` session with no `sub` claim.

    `auth.uid()` returns NULL, so `auth.uid() = tenant_id` is NULL — not true — for every
    row. NULL is not true, so the row is filtered; this asserts PostgreSQL's three-valued
    logic lands on the safe side rather than assuming it.
    """
    with db.cursor() as cur:
        cur.execute("SAVEPOINT noclaim")
        cur.execute("GRANT SELECT ON public.fee_records TO authenticated")
        _as_role(cur, "authenticated", None)
        cur.execute("SELECT count(*) FROM public.fee_records")
        count = cur.fetchone()[0]
        cur.execute("RESET ROLE")
        cur.execute("ROLLBACK TO SAVEPOINT noclaim")
    db.rollback()

    assert count == 0, (
        f"an authenticated session with no sub claim saw {count} fee row(s)"
    )


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_a_tenant_cannot_write_another_tenants_row_even_if_granted(db, tenants, verb):
    """Cross-tenant WRITE denial, with the write privilege deliberately granted.

    The owner policy is `FOR SELECT`, so even with UPDATE or DELETE granted there is no
    policy permitting a write — and under RLS an operation with no permissive policy
    affects zero rows. That is the posture 026 intends: a customer reads their fees and
    never authors or removes one.
    """
    target = tenants["row_a"]["fee_id"]

    with db.cursor() as cur:
        cur.execute("SAVEPOINT write_probe")
        cur.execute(f"GRANT SELECT, {verb} ON public.fee_records TO authenticated")
        _as_role(cur, "authenticated", tenants["b"])

        if verb == "UPDATE":
            cur.execute(
                "UPDATE public.fee_records SET fee_amount='999999' WHERE fee_id=%s",
                (target,),
            )
        else:
            cur.execute("DELETE FROM public.fee_records WHERE fee_id=%s", (target,))
        affected = cur.rowcount

        cur.execute("RESET ROLE")
        cur.execute("ROLLBACK TO SAVEPOINT write_probe")
    db.rollback()

    assert affected == 0, (
        f"tenant B's {verb} affected {affected} of tenant A's row(s). With no write policy "
        f"on this table the statement must match nothing."
    )


def test_a_tenant_cannot_write_even_their_own_row(db, tenants):
    """Not a cross-tenant test — a surface-area test.

    The owner policy is SELECT-only, so a customer cannot rewrite their own fee history
    either. That is intended: a fee is a record of what the venue charged, not a figure the
    customer authors. Asserted so that widening the policy to `FOR ALL` is a deliberate
    decision somebody has to make this test fail to take.
    """
    with db.cursor() as cur:
        cur.execute("SAVEPOINT own_write")
        cur.execute("GRANT SELECT, UPDATE ON public.fee_records TO authenticated")
        _as_role(cur, "authenticated", tenants["a"])
        cur.execute(
            "UPDATE public.fee_records SET fee_amount='1' WHERE fee_id=%s",
            (tenants["row_a"]["fee_id"],),
        )
        affected = cur.rowcount
        cur.execute("RESET ROLE")
        cur.execute("ROLLBACK TO SAVEPOINT own_write")
    db.rollback()

    assert affected == 0, (
        "a customer rewrote their own fee record. The owner policy is FOR SELECT; if that "
        "has been widened, say why."
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. THE INTENDED BACKEND PATH STILL WORKS
# ══════════════════════════════════════════════════════════════════════════

def test_the_table_owner_can_still_read_and_write(db, tenants):
    """ENABLE-not-FORCE, demonstrated rather than argued.

    `FeeEngine` connects as the table owner. If 026 had used FORCE, this is the test that
    would fail — and it would fail as "fee recording is broken" in production rather than
    here.
    """
    row = _fee_row(tenants["a"], marker="owner_path")
    with db.cursor() as cur:
        cur.execute(_INSERT, row)
        cur.execute(
            "SELECT count(*) FROM public.fee_records WHERE tenant_id = %s",
            (tenants["a"],),
        )
        own = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM public.fee_records WHERE fee_id = %s",
                    (row["fee_id"],))
        wrote = cur.fetchone()[0]
    db.rollback()

    assert wrote == 1, "the table owner could not insert a fee record"
    assert own >= 2, (
        f"the table owner saw {own} rows for tenant A; with ENABLE (not FORCE) the owner "
        f"is exempt from the policy and must see the fixture row plus the one just written"
    )


def test_fee_engine_reads_only_the_requested_tenant(db, tenants):
    """The application's own isolation, independent of RLS.

    `FeeEngine.get_fee_summary` filters on `tenant_id` in the query. That is a SECOND,
    independent boundary: the owner connection is exempt from the policy, so for the
    backend path the application's `WHERE` clause is the only thing separating tenants.
    Worth asserting precisely because RLS does not cover it.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend_app.backend.fee_engine import FeeEngine

    engine = create_engine(DSN)
    session = sessionmaker(bind=engine)()
    try:
        summary_a = FeeEngine(session).get_fee_summary(uuid.UUID(tenants["a"]))
        summary_b = FeeEngine(session).get_fee_summary(uuid.UUID(tenants["b"]))
    finally:
        session.close()
        engine.dispose()

    assert summary_a["count"] == 1, (
        f"get_fee_summary returned {summary_a['count']} rows for tenant A, not 1"
    )
    assert summary_b["count"] == 1
    # Each tenant's single row has the same shape, so the figures must match each other
    # and the seeded values — which is what proves neither summary absorbed the other's row.
    #
    # Compared as Decimal, against the seed. `get_fee_summary` returns `str(Decimal(...))`,
    # and Decimal addition PRESERVES the operand's scale: a seeded "0.05000000" comes back
    # as "0.05000000", not "0.05". That scale is an artefact of how the fixture wrote the
    # column, not a contract — the money amount is the contract. Deriving the expectation
    # from `_fee_row` rather than retyping a literal also means a change to the seed cannot
    # leave this assertion passing against a figure nobody inserted.
    expected_fee = Decimal(_fee_row(tenants["a"], marker="expected")["fee_amount"])
    expected_volume = Decimal(_fee_row(tenants["a"], marker="expected")["trade_volume"])
    assert Decimal(summary_a["total_fees"]) == Decimal(summary_b["total_fees"]) == expected_fee, (
        f"tenant A totalled {summary_a['total_fees']} and tenant B {summary_b['total_fees']}; "
        f"each holds exactly one row of {expected_fee}, so a different figure means one "
        f"summary absorbed the other tenant's row"
    )
    assert Decimal(summary_a["total_volume"]) == expected_volume


# ══════════════════════════════════════════════════════════════════════════
#  5. MIGRATION REPLAY
# ══════════════════════════════════════════════════════════════════════════

MIGRATION = _REPO_ROOT / "backend_app" / "migrations" / "026_fee_records_row_level_security.sql"

def _executable_sql(sql: str) -> str:
    """`sql` with comments removed and string literals blanked, upper-cased for scanning.

    A deny-list scan must read statements the server would execute, not prose about them:
    026's header explains that the file never deletes or truncates, and that sentence must
    not itself register as a `TRUNCATE`.

    SINGLE PASS, AND WHY IT HAS TO BE. The obvious implementation — three independent
    regexes for `--`, `/* */` and `'…'` — is wrong on this very file, measured rather than
    supposed: 026 contains five string literals whose text includes `--`, for instance::

        'unattributable fee row is visible only to service_role rather than to a wrong owner '
        '-- fail-closed, and reported as FEE-01.'

    PostgreSQL parses the literal first, so that `--` is data. A comment regex applied
    independently deletes to end-of-line, tears the literal open, and every subsequent
    quote pairs off by one — which silently blanked the file's real DDL and made the
    assertions below read an empty string. Consuming a literal whole, the moment it opens,
    is what keeps the two cases apart. Mirrors
    `tests/test_marketplace_paper_schema_contract.py::_strip_sql_comments_and_strings`.

    ONE DELIBERATE DIFFERENCE from that function: dollar-quoted `DO $$ … $$` bodies are
    kept. Blanking them is right for its purpose, but here it would mean a destructive
    statement placed inside one of 026's guarded `DO` blocks escaped the scan. Quoting
    inside a body behaves normally, so `RAISE EXCEPTION '… DROP TABLE …'` is still blanked.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if ch == "-" and nxt == "-":  # line comment
            end = sql.find("\n", i)
            if end == -1:
                break
            i = end
            continue

        if ch == "/" and nxt == "*":  # block comment
            end = sql.find("*/", i + 2)
            i = (end + 2) if end != -1 else n
            out.append(" ")
            continue

        if ch == "'":  # string literal, honouring the doubled '' escape
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(" '' ")
            continue

        out.append(ch)
        i += 1
    return "".join(out).upper()


def _security_snapshot(cur) -> tuple:
    """Everything 026 is responsible for, in a comparable form."""
    cur.execute(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname='public' AND c.relname=%s",
        (TABLE,),
    )
    rls = cur.fetchone()
    cur.execute(
        "SELECT policyname, cmd, roles::text, qual, with_check FROM pg_policies "
        "WHERE schemaname='public' AND tablename=%s ORDER BY policyname",
        (TABLE,),
    )
    policies = tuple(cur.fetchall())
    cur.execute(
        "SELECT grantee, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema='public' AND table_name=%s ORDER BY grantee, privilege_type",
        (TABLE,),
    )
    grants = tuple(cur.fetchall())
    cur.execute(
        "SELECT column_name, udt_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (TABLE,),
    )
    columns = tuple(cur.fetchall())
    return (rls, policies, grants, columns)


def test_the_migration_file_exists_and_is_additive_only():
    """026 is additive-only, and its row-security posture is the intended one.

    `tests/test_marketplace_paper_schema_contract.py::test_no_destructive_statements` globs
    every `*.sql` in both migration directories, so it already covers 026's DROP/TRUNCATE
    deny-list. What it does not cover, and what this adds, is the policy-specific posture:
    no `DROP POLICY` (which would open a policy-less window inside the transaction — 009
    records choosing `pg_policies` guards over it), `ENABLE` row security present, and
    `FORCE` absent. The hard-coded 006-010 set is `test_marketplace_paper_migrations.py`
    and the P-58 idempotency property; neither sees 026, which is why
    `test_re_applying_the_migration_changes_nothing` below exists.
    """
    assert MIGRATION.is_file(), f"{MIGRATION} is missing"
    body = _executable_sql(MIGRATION.read_text(encoding="utf-8"))

    for forbidden in ("DROP TABLE", "DROP COLUMN", "TRUNCATE", "DELETE FROM", "RENAME"):
        assert forbidden not in body, (
            f"026 executes {forbidden!r}. A migration that removes or renames is not "
            f"replay-safe and is not additive."
        )
    assert "DROP POLICY" not in body, (
        "026 uses DROP POLICY. 009 records why that is avoided: it opens a window in which "
        "the table has RLS enabled and no policy."
    )
    assert "ENABLE ROW LEVEL SECURITY" in body
    assert "FORCE ROW LEVEL SECURITY" not in body, (
        "026 would FORCE row security, which applies the policies to the table owner — "
        "the account the application connects as."
    )
    # Two negative controls on the stripper itself, because every assertion above is a
    # `not in` and would pass vacuously against an empty string.
    #
    # (a) It did not eat the DDL.
    for required in ("CREATE POLICY", "ALTER TABLE", "REVOKE", "GRANT"):
        assert required in body, (
            f"the comment/literal stripper removed 026's {required}, so the deny-list "
            f"above scanned prose rather than SQL"
        )
    # (b) It is load-bearing: the raw file DOES contain the forbidden words, in its header
    # and its COMMENT bodies. If this ever stops being true the stripper is untested, and
    # a future edit could break it without any test noticing.
    raw_upper = MIGRATION.read_text(encoding="utf-8").upper()
    assert "TRUNCATE" in raw_upper, (
        "026 no longer mentions TRUNCATE anywhere, so this test no longer demonstrates "
        "that the comment stripper is what makes the deny-list pass"
    )


def test_re_applying_the_migration_changes_nothing(db):
    """Requirement 24.8/24.9's property, for this file, against a real server.

    P-58 asserts idempotency over a parsed model of five other files. This applies 026
    itself, to a database where it is already applied, and compares the catalogues before
    and after. Rolled back, so the test leaves the database as it found it.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    # The file carries its own BEGIN/COMMIT. Strip them so it runs inside this test's
    # transaction and can be rolled back; the statements themselves are unchanged.
    body = sql.replace("BEGIN;", "", 1)
    index = body.rfind("COMMIT;")
    assert index != -1, "026 no longer ends with COMMIT; the replay harness assumed it did"
    body = body[:index]

    with db.cursor() as cur:
        before = _security_snapshot(cur)
        cur.execute("SAVEPOINT replay")
        cur.execute(body)
        after = _security_snapshot(cur)
        cur.execute("ROLLBACK TO SAVEPOINT replay")
    db.rollback()

    assert after == before, (
        "re-applying 026 on top of an already-applied schema changed the security posture. "
        "Re-application must be a no-op (Requirement 24.8)."
    )


# ══════════════════════════════════════════════════════════════════════════
#  6. THE BACKSTOP THAT SHOULD HAVE CAUGHT THIS NOW DOES
# ══════════════════════════════════════════════════════════════════════════

def test_the_privilege_hardening_backstop_no_longer_names_fee_records(db):
    """`999_privilege_hardening.sql` section 4, run as a query.

    That backstop already listed `tenant_id` among its per-customer columns and RAISEs an
    EXCEPTION for exactly this shape. It missed `fee_records` because it enumerates
    `pg_class` during provisioning step 4 and the table did not exist yet — the application
    created it later, at startup. Declaring the table in 026 is what puts it in front of
    the backstop.

    Run here verbatim rather than asserting the migration's own postflight, because this is
    the check that protects every FUTURE table and it is worth knowing it is satisfied.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT string_agg(c.relname, ', ' ORDER BY c.relname)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind = 'r'
              AND NOT c.relrowsecurity
              AND EXISTS (
                  SELECT 1 FROM information_schema.columns col
                  WHERE col.table_schema = 'public'
                    AND col.table_name = c.relname
                    AND col.column_name IN (
                        'user_id', 'tenant_id', 'profile_id', 'owner_id', 'author_id',
                        'customer_id', 'subscriber_id', 'referrer_id', 'created_by',
                        'creator_id', 'seller_id', 'account_id'
                    )
              )
              AND EXISTS (
                  SELECT 1 FROM information_schema.role_table_grants g
                  WHERE g.table_schema = 'public'
                    AND g.table_name = c.relname
                    AND g.grantee = 'anon'
                    AND g.privilege_type = 'SELECT'
              )
            """
        )
        offending = cur.fetchone()[0]
    db.rollback()

    named = [t.strip() for t in (offending or "").split(",") if t.strip()]
    assert TABLE not in named, (
        f"fee_records is still named by the privilege-hardening backstop: {offending}"
    )
    # Reported rather than asserted empty: another table appearing here is a finding of its
    # own, and failing this test on it would attribute it to TENANT-02.
    if named:
        print(
            "NOTE: the backstop names other tables, which is a separate finding: "
            + ", ".join(named)
        )
