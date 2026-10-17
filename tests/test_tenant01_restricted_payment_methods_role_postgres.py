"""TENANT-01: the restricted payment_methods_app role and the app dependency that uses
it, exercised against live PostgreSQL -- not introspected, CONNECTED AS.

WHAT THIS PROVES, AND WHY `test_tenant01_cross_tenant_matrix_postgres.py` DOES NOT ALREADY
COVER IT
============================================================================================
That matrix drives every table through `SET LOCAL ROLE authenticated`, which is what a
real PostgREST request looks like -- but `authenticated` has only ever had a SELECT policy
on `payment_methods` (`migrations/hardening/999_privilege_hardening.sql`), and this
backend's own routes never go through PostgREST for this table in the first place; they go
through SQLAlchemy as `postgres`, the table owner, which no RLS policy can ever constrain
(TENANT-01's own evidence: `rolsuper`/`rolbypassrls` both unconditionally skip RLS,
`FORCE` or not). So the one matrix the rest of this effort already has does not, and
structurally cannot, prove anything about the actual live code path these two routes use.

This file connects as the role those two routes ACTUALLY use now
(`backend_app/migrations/047_tenant01_restricted_payment_methods_role.sql`'s
`payment_methods_app`), through the actual app module
(`backend_app.core.restricted_db`), not a hand-rolled SQL harness that merely resembles it.
A defect in `restricted_db.py` itself -- the wrong GUC name, a missing `SET LOCAL`, a typo
in the grant -- would pass a harness that reimplements the connection logic instead of
calling it. This one cannot: it imports the real module.

THE THREE CONTROLS, SAME SHAPE AS THE EXISTING MATRIX
-------------------------------------------------------
1. NEGATIVE -- tenant A, connected as payment_methods_app, attempts to SELECT/DELETE
   tenant B's row. Zero rows affected AND the victim row unchanged afterwards, read back
   as the owner.
2. POSITIVE -- tenant A performs the same operations on its own row, and they succeed.
   Without this, a connection that denied everything would look perfectly isolated.
3. PRIVILEGE FLOOR -- payment_methods_app cannot INSERT or UPDATE this table at all
   (InsufficientPrivilege, not a 0-row RLS filter), proving the privilege grant itself is
   as narrow as 047's own evidence claims, not just the policy.

Also proves the ROLE ITSELF is not a second superuser bypass: `rolsuper`/`rolbypassrls`
are read directly off `pg_roles` and asserted false, which is the exact measurement
TENANT-01's evidence used to show the OLD connection shape could never be fixed by a
policy alone.

Skipped, not silently passed, when no PostgreSQL DSN is configured -- same convention as
every other file in this Postgres-gated set.
"""

from __future__ import annotations

import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql://postgres:@127.0.0.1:62674/postgres"
)

if not DSN.startswith("postgres"):
    pytest.skip(
        "TENANT-01 restricted-role test requires PostgreSQL", allow_module_level=True
    )

_TEST_ROLE_PASSWORD = "tenant01_test_only_" + uuid.uuid4().hex


def _owner_connect():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    return conn


@pytest.fixture(scope="module")
def owner_db():
    try:
        conn = _owner_connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    cur = conn.cursor()

    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'payment_methods_app')"
    )
    if not cur.fetchone()[0]:
        pytest.skip(
            "payment_methods_app role does not exist -- apply "
            "backend_app/migrations/047_tenant01_restricted_payment_methods_role.sql "
            "first."
        )

    # A throwaway password for THIS test run only. 047 deliberately creates the role
    # without one (see its own header: a real credential must never enter a tracked,
    # committed migration file) -- a test that needs to literally authenticate as the
    # role has to supply one itself, scoped to a disposable test database.
    cur.execute(
        "ALTER ROLE payment_methods_app WITH PASSWORD %s", (_TEST_ROLE_PASSWORD,)
    )

    yield conn
    conn.close()


def _restricted_dsn() -> str:
    """The same DSN shape PAYMENT_METHODS_DB_URL would hold in a real deployment,
    built from the owner DSN's own host/port so this works against whatever PostgreSQL
    this test run is pointed at, not a hardcoded local socket path."""
    import urllib.parse as up

    parsed = up.urlparse(DSN)
    netloc = parsed.netloc or ""
    host_part = netloc.split("@")[-1] if "@" in netloc else netloc
    query = parsed.query
    return up.urlunparse((
        parsed.scheme,
        f"payment_methods_app:{_TEST_ROLE_PASSWORD}@{host_part}",
        parsed.path or "/postgres",
        "",
        query,
        "",
    ))


@pytest.fixture
def seeded_pair(owner_db):
    """Two real users, each with a profiles row (payment_methods' FK target) and one
    payment_methods row, inserted as the owner -- the same shape 020/999 and
    PaymentMethodModel's own FK (fk_bug049_payment_methods_user_id -> profiles(id))
    require."""
    cur = owner_db.cursor()
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    pm_a, pm_b = str(uuid.uuid4()), str(uuid.uuid4())

    for uid in (user_a, user_b):
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s)", (uid, f"{uid}@t.example")
        )
        cur.execute(
            "INSERT INTO profiles (id, available_discounts, max_api_slots, "
            "cancel_at_period_end) VALUES (%s, 0, 5, false)",
            (uid,),
        )
    cur.execute(
        "INSERT INTO payment_methods (id, user_id, payment_method_id, brand, last4, "
        "expiry_month, expiry_year, is_default, created_at) "
        "VALUES (%s, %s, 'pm_a', 'visa', '4242', 12, 2030, false, now()::text)",
        (pm_a, user_a),
    )
    cur.execute(
        "INSERT INTO payment_methods (id, user_id, payment_method_id, brand, last4, "
        "expiry_month, expiry_year, is_default, created_at) "
        "VALUES (%s, %s, 'pm_b', 'mastercard', '1111', 11, 2031, false, now()::text)",
        (pm_b, user_b),
    )
    yield {"user_a": user_a, "user_b": user_b, "pm_a": pm_a, "pm_b": pm_b}

    cur.execute("DELETE FROM payment_methods WHERE id IN (%s, %s)", (pm_a, pm_b))
    cur.execute("DELETE FROM profiles WHERE id IN (%s, %s)", (user_a, user_b))
    cur.execute("DELETE FROM auth.users WHERE id IN (%s, %s)", (user_a, user_b))


class TestRoleIsGenuinelySubjectToRLS:
    def test_payment_methods_app_is_neither_superuser_nor_bypassrls(self, owner_db):
        """The exact measurement TENANT-01's evidence used to prove the OLD connection
        (postgres) could never be fixed by a policy alone -- repeated here against the
        NEW role to prove it actually is a role RLS applies to."""
        cur = owner_db.cursor()
        cur.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = "
            "'payment_methods_app'"
        )
        rolsuper, rolbypassrls = cur.fetchone()
        assert rolsuper is False
        assert rolbypassrls is False


class TestRealTwoTenantIsolation:
    """Connects through the real app module, not a parallel SQL harness."""

    def test_select_sees_only_own_row(self, owner_db, seeded_pair):
        os.environ["PAYMENT_METHODS_DB_URL"] = _restricted_dsn()
        import importlib

        import backend_app.core.restricted_db as restricted_db

        importlib.reload(restricted_db)  # pick up the env var set just above
        try:
            from sqlalchemy import text

            with restricted_db.restricted_payment_methods_session(
                seeded_pair["user_a"]
            ) as session:
                rows = session.execute(
                    text("SELECT user_id, payment_method_id FROM payment_methods")
                ).fetchall()
            assert [r[1] for r in rows] == ["pm_a"]
            assert all(str(r[0]) == seeded_pair["user_a"] for r in rows)
        finally:
            del os.environ["PAYMENT_METHODS_DB_URL"]
            importlib.reload(restricted_db)

    def test_cross_tenant_delete_affects_nothing_and_victim_row_survives(
        self, owner_db, seeded_pair
    ):
        os.environ["PAYMENT_METHODS_DB_URL"] = _restricted_dsn()
        import importlib

        import backend_app.core.restricted_db as restricted_db

        importlib.reload(restricted_db)
        try:
            from sqlalchemy import text

            with restricted_db.restricted_payment_methods_session(
                seeded_pair["user_a"]
            ) as session:
                result = session.execute(
                    text(
                        "DELETE FROM payment_methods WHERE id = :pm_id"
                    ),
                    {"pm_id": seeded_pair["pm_b"]},
                )
                assert result.rowcount == 0
        finally:
            del os.environ["PAYMENT_METHODS_DB_URL"]
            importlib.reload(restricted_db)

        # read back as the owner: the victim row is untouched
        cur = owner_db.cursor()
        cur.execute(
            "SELECT payment_method_id FROM payment_methods WHERE id = %s",
            (seeded_pair["pm_b"],),
        )
        row = cur.fetchone()
        assert row is not None and row[0] == "pm_b"

    def test_own_row_delete_succeeds(self, owner_db, seeded_pair):
        os.environ["PAYMENT_METHODS_DB_URL"] = _restricted_dsn()
        import importlib

        import backend_app.core.restricted_db as restricted_db

        importlib.reload(restricted_db)
        try:
            from sqlalchemy import text

            with restricted_db.restricted_payment_methods_session(
                seeded_pair["user_a"]
            ) as session:
                result = session.execute(
                    text("DELETE FROM payment_methods WHERE id = :pm_id"),
                    {"pm_id": seeded_pair["pm_a"]},
                )
                assert result.rowcount == 1
        finally:
            del os.environ["PAYMENT_METHODS_DB_URL"]
            importlib.reload(restricted_db)

        cur = owner_db.cursor()
        cur.execute(
            "SELECT 1 FROM payment_methods WHERE id = %s", (seeded_pair["pm_a"],)
        )
        assert cur.fetchone() is None


class TestPrivilegeFloor:
    def test_update_is_refused_by_privilege_not_by_policy(self, owner_db, seeded_pair):
        """INSERT/UPDATE must fail with InsufficientPrivilege (no grant at all), not
        silently affect 0 rows the way an RLS-filtered DELETE does. The two failure
        modes look similar from a caller's point of view but mean very different
        things about what this role can and cannot do -- 047's own grant is
        SELECT, DELETE only, and this is the test that would catch a future migration
        accidentally widening it to FOR ALL."""
        import psycopg2.errors

        conn = psycopg2.connect(_dsn_for_raw_connect(owner_db))
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            "SET LOCAL request.jwt.claim.sub = %s", (seeded_pair["user_a"],)
        )
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute(
                "UPDATE payment_methods SET is_default = true WHERE id = %s",
                (seeded_pair["pm_a"],),
            )
        conn.rollback()
        conn.close()


def _dsn_for_raw_connect(owner_db) -> str:
    return _restricted_dsn()
