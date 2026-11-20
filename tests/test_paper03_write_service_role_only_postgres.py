"""tests/test_paper03_write_service_role_only_postgres.py

PAPER-03 (P1) -- the half of the proof that only a real PostgreSQL can give.

Migration 009 granted `authenticated` SELECT+INSERT(+UPDATE on five of them) on eleven
`paper_*` tables, paired with a `*_owner_access` RLS policy admitting any statement whose
`user_id` matches `auth.uid()`. Read directly (`backend/paper_trading_service.py::
PaperTradingService._client`, the sole Persistence_Layer resolver every paper read and write
goes through), nothing in this platform builds a client from the caller's own JWT any more --
every statement goes through the service-role credential, the same move PAPER-04 already made
for the bot-driven session loop. The `authenticated` WRITE grant therefore let a trader's own
token write directly into their own paper-trading rows via a raw PostgREST call -- fabricating
a balance, a fill, a position, or an order's state -- bypassing every check
`PaperTradingService.place_order`/`cancel_order` perform. Migration 043 revokes exactly the
WRITE half of that grant.

This is REPRODUCED AND VERIFIED against a REAL PostgreSQL server, not asserted from reading the
SQL: this test builds a minimal, same-named mirror of the eleven tables with migration 009's
own grant/policy shape, proves the exploit succeeds under that starting state, applies
`migrations/043_paper_trading_service_role_only_write.sql` VERBATIM from disk, and proves the
exploit now fails with `permission denied` while SELECT (authenticated) and write (service_role)
are both unaffected.

EVERY TEST IN THIS FILE SKIPS unless a real PostgreSQL is reachable (same convention as
tests/test_sec09_marketplace_evidence_service_role_only_postgres.py and
tests/test_tenant01_cross_tenant_matrix_postgres.py) and the `anon`/`authenticated`/
`service_role` roles exist on it.
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
    pytest.skip("PAPER-03 requires PostgreSQL", allow_module_level=True)

MIGRATION_043 = (
    Path(__file__).resolve().parents[1]
    / "backend_app" / "migrations" / "043_paper_trading_service_role_only_write.sql"
)

_OWNER_UID = "33333333-3333-3333-3333-333333333333"
_OTHER_UID = "44444444-4444-4444-4444-444444444444"

# name -> whether migration 009 also granted UPDATE to authenticated (and so whether this
# migration must revoke it).
_RW_TABLES = ["paper_sessions", "paper_accounts", "paper_orders", "paper_positions", "paper_metrics"]
_INSERT_ONLY_TABLES = [
    "paper_fills", "paper_balance_events", "paper_trades",
    "paper_equity_snapshots", "paper_events", "paper_market_events",
]
_ALL_TABLES = _RW_TABLES + _INSERT_ONLY_TABLES

# WHY THIS FILE NOW MOVES THE REAL TABLES ASIDE INSTEAD OF DESTROYING THEM.
#
# This file needs its mirrors to be named `public.paper_*` exactly, because it applies
# `043_paper_trading_service_role_only_write.sql` VERBATIM from disk and that migration
# names `public.paper_sessions` and friends literally (it also builds
# `has_table_privilege('authenticated', 'public.' || tbl, ...)` strings in its own
# postflight). So the mirrors cannot simply be built in a scratch schema.
#
# What this file used to do was `DROP TABLE public.paper_* CASCADE` on the way in AND on
# the way out -- destroying whatever the database already had under those names and never
# putting it back. On a disposable replica that is invisible. In the `provisioning` CI
# lane it was not: that lane provisions the full schema once and then runs every
# PostgreSQL-gated file in ONE pytest invocation, and this file is listed immediately
# before `test_paper05_apply_fill_atomic_postgres.py`. So this file deleted the real
# `paper_*` tables, and PAPER-05's own module-scoped `_require_migration_048` fixture then
# probed `to_regclass('public.paper_accounts')`, got NULL, and skipped all 10 of its
# atomicity tests -- reporting "paper_* tables are absent" about a database the same job
# had just provisioned them into. That skip was recorded in the ledger as not root-caused.
# This is the cause.
#
# `ALTER TABLE ... SET SCHEMA` is the primitive used rather than `RENAME TO`: renaming
# within `public` would leave the original's indexes and constraints occupying the names
# (`paper_accounts_pkey` and siblings) that the mirror's own PRIMARY KEY then tries to
# create, which collides. Moving the table to another schema takes its indexes,
# constraints, policies and grants with it, freeing the names completely, and moving it
# back restores all of them along with every row.
_STASH_SCHEMA = "paper03_stash"


def _connect():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    return conn


def _roles_present(cur) -> bool:
    cur.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')")
    found = {r[0] for r in cur.fetchall()}
    return {"anon", "authenticated", "service_role"} <= found


def _exists(cur, qualified: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (qualified,))
    return bool(cur.fetchone()[0])


def _stash_real_tables(cur) -> list[str]:
    """Move any real ``public.paper_*`` table into the stash schema and report which.

    Self-healing on the way in: if the stash schema already holds tables, a previous run
    of this file died between its setup and its teardown, so those are the real tables and
    whatever is sitting in ``public`` under the same name is a leftover mirror. Restoring
    first makes this file safe to re-run after a crash instead of leaving the database
    permanently short of its real paper schema.
    """
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_STASH_SCHEMA}")
    for tbl in _ALL_TABLES:
        if _exists(cur, f"{_STASH_SCHEMA}.{tbl}"):
            cur.execute(f"DROP TABLE IF EXISTS public.{tbl} CASCADE")
            cur.execute(f"ALTER TABLE {_STASH_SCHEMA}.{tbl} SET SCHEMA public")

    stashed: list[str] = []
    for tbl in _ALL_TABLES:
        if not _exists(cur, f"public.{tbl}"):
            continue
        cur.execute(f"ALTER TABLE public.{tbl} SET SCHEMA {_STASH_SCHEMA}")
        stashed.append(tbl)
    return stashed


def _restore_real_tables(cur, stashed: list[str]) -> None:
    """Drop this file's mirrors and move the real tables back under their own names."""
    for tbl in stashed:
        cur.execute(f"DROP TABLE IF EXISTS public.{tbl} CASCADE")
        if _exists(cur, f"{_STASH_SCHEMA}.{tbl}"):
            cur.execute(f"ALTER TABLE {_STASH_SCHEMA}.{tbl} SET SCHEMA public")
    cur.execute(f"DROP SCHEMA IF EXISTS {_STASH_SCHEMA} CASCADE")


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

    cur.execute(
        "CREATE OR REPLACE FUNCTION public.auth_uid() RETURNS uuid "
        "LANGUAGE sql STABLE AS $$ SELECT current_setting('test.paper03_uid', true)::uuid $$"
    )

    stashed = _stash_real_tables(cur)
    _create_mirror_tables(cur)
    try:
        yield conn
    finally:
        for tbl in _ALL_TABLES:
            cur.execute(f"DROP TABLE IF EXISTS public.{tbl} CASCADE")
        cur.execute("DROP FUNCTION IF EXISTS public.auth_uid()")
        # Put back exactly what was borrowed, so a file that runs after this one in the
        # same pytest invocation still finds the schema the lane provisioned.
        _restore_real_tables(cur, stashed)
        conn.close()


def _create_mirror_tables(cur) -> None:
    """Migration 009's grant/policy shape for the eleven tables PAPER-03 is about -- not their
    full column set, which this fix does not touch.

    No `CREATE EXTENSION pgcrypto` here: nothing below calls a pgcrypto function --
    every row id default is `gen_random_uuid()`, a core builtin since PG13, not the
    extension. A stray `CREATE EXTENSION IF NOT EXISTS pgcrypto` used to sit here doing
    nothing useful, and broke outright against `pgserver`'s PyPI wheel (no pgcrypto
    shipped at all; `scripts/provision_database.py`'s own substrate step already
    documents this and falls back to a non-cryptographic shim for the one real caller,
    the referral-code trigger -- this fixture has no such caller to fall back for).
    """
    for tbl in _ALL_TABLES:
        cur.execute(f"DROP TABLE IF EXISTS public.{tbl} CASCADE")
        cur.execute(
            f"CREATE TABLE public.{tbl} ("
            " id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            " user_id uuid NOT NULL,"
            " payload text)"
        )
        cur.execute(f"ALTER TABLE public.{tbl} ENABLE ROW LEVEL SECURITY")
        cur.execute(
            f"CREATE POLICY {tbl}_owner_access ON public.{tbl} "
            # auth.uid() does not exist on a plain Postgres server; the test's own
            # auth_uid() stand-in plays its role, same technique as the SEC-09 test.
            "FOR ALL TO authenticated USING (public.auth_uid() = user_id) "
            "WITH CHECK (public.auth_uid() = user_id)"
        )
        cur.execute(
            f"CREATE POLICY {tbl}_service_role ON public.{tbl} "
            "FOR ALL TO service_role USING (true) WITH CHECK (true)"
        )
        cur.execute(f"REVOKE ALL ON public.{tbl} FROM anon")
        cur.execute(f"REVOKE ALL ON public.{tbl} FROM authenticated")
        cur.execute(f"REVOKE ALL ON public.{tbl} FROM service_role")

    # migration 009's actual pre-fix grants.
    for tbl in _RW_TABLES:
        cur.execute(f"GRANT SELECT, INSERT, UPDATE ON public.{tbl} TO authenticated")
        cur.execute(f"GRANT SELECT, INSERT, UPDATE ON public.{tbl} TO service_role")
    for tbl in _INSERT_ONLY_TABLES:
        cur.execute(f"GRANT SELECT, INSERT ON public.{tbl} TO authenticated")
        cur.execute(f"GRANT SELECT, INSERT ON public.{tbl} TO service_role")


def _as_authenticated(cur, uid: str):
    cur.execute("SET ROLE authenticated")
    cur.execute("SELECT set_config('test.paper03_uid', %s, false)", (uid,))


def _as_service_role(cur):
    cur.execute("SET ROLE service_role")


class TestTheExploitIsReproducedBeforeTheFix:
    @pytest.mark.parametrize("table", _ALL_TABLES)
    def test_an_owner_can_insert_a_fabricated_row_before_the_fix(self, db, table):
        """Reproduction, not assertion: proves the starting state really is vulnerable for
        EVERY one of the eleven tables, so the fix below is shown to change something real.
        """
        cur = db.cursor()
        try:
            _as_authenticated(cur, _OWNER_UID)
            cur.execute(
                f"INSERT INTO public.{table} (user_id, payload) VALUES (%s, 'forged')",
                (_OWNER_UID,),
            )
        finally:
            cur.execute("RESET ROLE")

        cur.execute(f"SELECT count(*) FROM public.{table} WHERE user_id = %s", (_OWNER_UID,))
        assert cur.fetchone()[0] >= 1, f"PAPER-03 reproduction failed: the pre-fix exploit did not write a row into {table}"

    @pytest.mark.parametrize("table", _RW_TABLES)
    def test_an_owner_can_also_update_their_own_forged_row_before_the_fix(self, db, table):
        """The five accounts/orders/sessions/positions/metrics tables additionally grant
        UPDATE -- so a trader could rewrite an already-settled balance or order in place, not
        merely insert a new fabricated one.
        """
        cur = db.cursor()
        try:
            _as_authenticated(cur, _OWNER_UID)
            cur.execute(
                f"UPDATE public.{table} SET payload = 'rewritten' WHERE user_id = %s",
                (_OWNER_UID,),
            )
        finally:
            cur.execute("RESET ROLE")


class TestMigration043ClosesIt:
    @staticmethod
    @pytest.fixture(scope="class", autouse=True)
    def apply_migration(db):
        cur = db.cursor()
        for tbl in _ALL_TABLES:
            cur.execute(f"TRUNCATE public.{tbl}")
        cur.execute(MIGRATION_043.read_text(encoding="utf-8"))
        yield

    @pytest.mark.parametrize("table", _ALL_TABLES)
    def test_authenticated_can_no_longer_insert_a_fabricated_row(self, db, table):
        cur = db.cursor()
        try:
            _as_authenticated(cur, _OWNER_UID)
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute(
                    f"INSERT INTO public.{table} (user_id, payload) VALUES (%s, 'forged')",
                    (_OWNER_UID,),
                )
        finally:
            db.rollback()
            cur.execute("RESET ROLE")

    @pytest.mark.parametrize("table", _RW_TABLES)
    def test_authenticated_can_no_longer_update_its_own_row(self, db, table):
        cur = db.cursor()
        try:
            _as_service_role(cur)
            cur.execute(
                f"INSERT INTO public.{table} (user_id, payload) VALUES (%s, 'genuine')",
                (_OWNER_UID,),
            )
        finally:
            cur.execute("RESET ROLE")

        cur = db.cursor()
        try:
            _as_authenticated(cur, _OWNER_UID)
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute(
                    f"UPDATE public.{table} SET payload = 'rewritten' WHERE user_id = %s",
                    (_OWNER_UID,),
                )
        finally:
            db.rollback()
            cur.execute("RESET ROLE")

    @pytest.mark.parametrize("table", _ALL_TABLES)
    def test_authenticated_can_still_read_every_table(self, db, table):
        """NEGATIVE CONTROL: this fix narrows WRITE, not READ -- migration 009's SELECT grant,
        which PAPER-03's own remediation note says to keep, must survive untouched.
        """
        cur = db.cursor()
        try:
            _as_authenticated(cur, _OTHER_UID)
            cur.execute(f"SELECT count(*) FROM public.{table}")
        finally:
            cur.execute("RESET ROLE")

    @pytest.mark.parametrize("table", _ALL_TABLES)
    def test_service_role_can_still_insert_into_every_table(self, db, table):
        """NEGATIVE CONTROL: the real application's own write path (always service-role,
        confirmed by reading PaperTradingService._client) must be completely unaffected.
        """
        cur = db.cursor()
        try:
            _as_service_role(cur)
            cur.execute(
                f"INSERT INTO public.{table} (user_id, payload) VALUES (%s, 'from the service') "
                "RETURNING id",
                (_OTHER_UID,),
            )
            row_id = cur.fetchone()[0]
        finally:
            cur.execute("RESET ROLE")

        cur.execute(f"SELECT payload FROM public.{table} WHERE id = %s", (row_id,))
        assert cur.fetchone()[0] == "from the service"

    @pytest.mark.parametrize("table", _RW_TABLES)
    def test_service_role_can_still_update_the_five_rw_tables(self, db, table):
        """NEGATIVE CONTROL, RW tables only: migration 009 grants service_role UPDATE on
        sessions/accounts/orders/positions/metrics specifically -- the other six are
        append-only logs service_role never had UPDATE on either, in the real migration.
        """
        cur = db.cursor()
        try:
            _as_service_role(cur)
            cur.execute(
                f"INSERT INTO public.{table} (user_id, payload) VALUES (%s, 'from the service') "
                "RETURNING id",
                (_OTHER_UID,),
            )
            row_id = cur.fetchone()[0]
            cur.execute(f"UPDATE public.{table} SET payload = 'updated' WHERE id = %s", (row_id,))
        finally:
            cur.execute("RESET ROLE")

        cur.execute(f"SELECT payload FROM public.{table} WHERE id = %s", (row_id,))
        assert cur.fetchone()[0] == "updated"
