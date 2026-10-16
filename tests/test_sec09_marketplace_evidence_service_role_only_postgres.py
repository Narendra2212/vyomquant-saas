"""tests/test_sec09_marketplace_evidence_service_role_only_postgres.py

SEC-09 (P2) -- the half of the proof that only a real PostgreSQL can give.

Migration 007 granted `authenticated` SELECT+INSERT on `marketplace_backtest_evidence`
and `marketplace_price_evaluations`, with an RLS policy admitting any row whose
`owner_id` matches `auth.uid()`. Read directly, every legitimate read AND write of these
two tables (backend/marketplace/submission_service.py, pricing_evaluator.py,
routers/library.py) already goes through a service-role client -- so the `authenticated`
grant let a marketplace listing's own owner write directly into their own supposedly
INDEPENDENT evidence/price-evaluation record via a raw PostgREST call with their own
access token, bypassing the real computation entirely. Migration 042 revokes exactly the
WRITE half of that grant.

This is REPRODUCED AND VERIFIED against a REAL PostgreSQL server, not asserted from
reading the SQL: this test builds a minimal, same-named mirror of the two tables with
migration 007's own grant/policy shape, proves the exploit succeeds under that starting
state, applies `migrations/042_marketplace_evidence_service_role_only_write.sql` VERBATIM
from disk, and proves the exploit now fails with `permission denied` while SELECT
(authenticated) and INSERT (service_role) are both unaffected.

EVERY TEST IN THIS FILE SKIPS unless a real PostgreSQL is reachable (same convention as
tests/test_tenant01_cross_tenant_matrix_postgres.py and
tests/test_database_isolation_level_postgres.py) and the `anon`/`authenticated`/
`service_role` roles exist on it -- the roles a real Supabase-provisioned Postgres (or
this repo's own CI `database-tests` job) always has.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql://postgres:postgres@127.0.0.1:5432/sec09_test"
)

if not DSN.startswith("postgres"):
    pytest.skip("SEC-09 requires PostgreSQL", allow_module_level=True)

MIGRATION_042 = (
    Path(__file__).resolve().parents[1]
    / "backend_app" / "migrations" / "042_marketplace_evidence_service_role_only_write.sql"
)

_OWNER_UID = "11111111-1111-1111-1111-111111111111"
_OTHER_UID = "22222222-2222-2222-2222-222222222222"


def _connect():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    return conn


def _roles_present(cur) -> bool:
    cur.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')")
    found = {r[0] for r in cur.fetchall()}
    return {"anon", "authenticated", "service_role"} <= found


@pytest.fixture(scope="module")
def db():
    try:
        conn = _connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not reachable at {DSN!r}: {exc}")
        return

    cur = conn.cursor()
    if not _roles_present(cur):
        conn.close()
        pytest.skip("anon/authenticated/service_role roles do not exist on this server")

    # A stand-in for Supabase's auth.uid(), which reads request.jwt.claim.sub in the real
    # project. Scoped to this test's own schema concerns only.
    cur.execute(
        "CREATE OR REPLACE FUNCTION public.auth_uid() RETURNS uuid "
        "LANGUAGE sql STABLE AS $$ SELECT current_setting('test.sec09_uid', true)::uuid $$"
    )

    _create_mirror_tables(cur)
    try:
        yield conn
    finally:
        cur.execute("DROP TABLE IF EXISTS public.marketplace_backtest_evidence CASCADE")
        cur.execute("DROP TABLE IF EXISTS public.marketplace_price_evaluations CASCADE")
        cur.execute("DROP FUNCTION IF EXISTS public.auth_uid()")
        conn.close()


def _create_mirror_tables(cur) -> None:
    """Migration 007's grant/policy shape for the two tables SEC-09 is about -- not its
    full column set, which this fix does not touch.
    """
    cur.execute("DROP TABLE IF EXISTS public.marketplace_backtest_evidence CASCADE")
    cur.execute("DROP TABLE IF EXISTS public.marketplace_price_evaluations CASCADE")
    # No CREATE EXTENSION pgcrypto: nothing below calls a pgcrypto function -- every
    # row id default is gen_random_uuid(), a core builtin since PG13. A stray call here
    # used to do nothing useful and broke against pgserver's PyPI wheel (no pgcrypto
    # shipped at all) -- same root cause and fix as test_paper03_write_service_role_
    # only_postgres.py's _create_mirror_tables.

    cur.execute(
        "CREATE TABLE public.marketplace_backtest_evidence ("
        " id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
        " owner_id uuid NOT NULL,"
        " win_rate_pct numeric,"
        " max_drawdown_pct numeric)"
    )
    cur.execute(
        "CREATE TABLE public.marketplace_price_evaluations ("
        " id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
        " owner_id uuid NOT NULL,"
        " price_minor integer)"
    )
    cur.execute("ALTER TABLE public.marketplace_backtest_evidence ENABLE ROW LEVEL SECURITY")
    cur.execute("ALTER TABLE public.marketplace_price_evaluations ENABLE ROW LEVEL SECURITY")

    cur.execute(
        "CREATE POLICY evidence_owner_access ON public.marketplace_backtest_evidence "
        "FOR ALL TO authenticated USING (auth_uid() = owner_id) WITH CHECK (auth_uid() = owner_id)"
    )
    cur.execute(
        "CREATE POLICY evidence_service_role ON public.marketplace_backtest_evidence "
        "FOR ALL TO service_role USING (true) WITH CHECK (true)"
    )
    cur.execute(
        "CREATE POLICY price_evaluations_owner_access ON public.marketplace_price_evaluations "
        "FOR ALL TO authenticated USING (auth_uid() = owner_id) WITH CHECK (auth_uid() = owner_id)"
    )
    cur.execute(
        "CREATE POLICY price_evaluations_service_role ON public.marketplace_price_evaluations "
        "FOR ALL TO service_role USING (true) WITH CHECK (true)"
    )

    for tbl in ("marketplace_backtest_evidence", "marketplace_price_evaluations"):
        cur.execute(f"REVOKE ALL ON public.{tbl} FROM anon")
        cur.execute(f"REVOKE ALL ON public.{tbl} FROM authenticated")
    # migration 007's actual pre-fix grants.
    cur.execute("GRANT SELECT, INSERT ON public.marketplace_backtest_evidence TO authenticated")
    cur.execute("GRANT SELECT, INSERT ON public.marketplace_price_evaluations TO authenticated")
    cur.execute("GRANT SELECT, INSERT ON public.marketplace_backtest_evidence TO service_role")
    cur.execute("GRANT SELECT, INSERT, UPDATE ON public.marketplace_price_evaluations TO service_role")


def _as_authenticated(cur, uid: str):
    cur.execute("SET ROLE authenticated")
    cur.execute("SELECT set_config('test.sec09_uid', %s, false)", (uid,))


def _as_service_role(cur):
    cur.execute("SET ROLE service_role")


class TestTheExploitIsReproducedBeforeTheFix:
    def test_an_owner_can_write_their_own_fabricated_evidence_and_price_before_the_fix(self, db):
        """Reproduction, not assertion: proves the starting state really is vulnerable,
        so the fix below is shown to change something real.
        """
        cur = db.cursor()
        try:
            _as_authenticated(cur, _OWNER_UID)
            cur.execute(
                "INSERT INTO public.marketplace_backtest_evidence (owner_id, win_rate_pct, max_drawdown_pct) "
                "VALUES (%s, 99.9, 0.01)",
                (_OWNER_UID,),
            )
            cur.execute(
                "INSERT INTO public.marketplace_price_evaluations (owner_id, price_minor) VALUES (%s, 999999900)",
                (_OWNER_UID,),
            )
        finally:
            cur.execute("RESET ROLE")

        cur.execute("SELECT count(*) FROM public.marketplace_backtest_evidence WHERE owner_id = %s", (_OWNER_UID,))
        assert cur.fetchone()[0] == 1, "SEC-09 reproduction failed: the pre-fix exploit did not even write a row"


class TestMigration042ClosesIt:
    @staticmethod
    @pytest.fixture(scope="class", autouse=True)
    def apply_migration(db):
        cur = db.cursor()
        cur.execute("TRUNCATE public.marketplace_backtest_evidence, public.marketplace_price_evaluations")
        cur.execute(MIGRATION_042.read_text(encoding="utf-8"))
        yield

    def test_authenticated_can_no_longer_insert_fabricated_evidence(self, db):
        cur = db.cursor()
        try:
            _as_authenticated(cur, _OWNER_UID)
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute(
                    "INSERT INTO public.marketplace_backtest_evidence (owner_id, win_rate_pct) VALUES (%s, 99.9)",
                    (_OWNER_UID,),
                )
        finally:
            db.rollback()
            cur.execute("RESET ROLE")

    def test_authenticated_can_no_longer_insert_a_fabricated_price_evaluation(self, db):
        cur = db.cursor()
        try:
            _as_authenticated(cur, _OWNER_UID)
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute(
                    "INSERT INTO public.marketplace_price_evaluations (owner_id, price_minor) VALUES (%s, 1)",
                    (_OWNER_UID,),
                )
        finally:
            db.rollback()
            cur.execute("RESET ROLE")

    def test_authenticated_can_still_read_both_tables(self, db):
        """NEGATIVE CONTROL: this fix narrows WRITE, not READ -- migration 007's SELECT
        grant, which this finding does not name, must survive untouched.
        """
        cur = db.cursor()
        try:
            _as_authenticated(cur, _OTHER_UID)
            cur.execute("SELECT count(*) FROM public.marketplace_backtest_evidence")
            cur.execute("SELECT count(*) FROM public.marketplace_price_evaluations")
        finally:
            cur.execute("RESET ROLE")

    def test_service_role_can_still_write_both_tables(self, db):
        """NEGATIVE CONTROL: the real application's own write path (always service-role,
        confirmed by reading submission_service.py/pricing_evaluator.py/library.py) must
        be completely unaffected.
        """
        cur = db.cursor()
        try:
            _as_service_role(cur)
            cur.execute(
                "INSERT INTO public.marketplace_backtest_evidence (owner_id, win_rate_pct) VALUES (%s, 55.0)",
                (_OTHER_UID,),
            )
            cur.execute(
                "INSERT INTO public.marketplace_price_evaluations (owner_id, price_minor) VALUES (%s, 5000)",
                (_OTHER_UID,),
            )
        finally:
            cur.execute("RESET ROLE")

        cur.execute("SELECT count(*) FROM public.marketplace_backtest_evidence WHERE owner_id = %s", (_OTHER_UID,))
        assert cur.fetchone()[0] == 1
