"""tests/test_db03_privilege_hardening_detects_untracked_tables_postgres.py

DB-03 (P2) -- the half of the proof that only a real PostgreSQL can give.

``scripts/post_migration_validation.py``'s RLS check used to walk a hardcoded
``required_tables`` list -- the same list section 1 checks for existence -- so a brand
new customer table, never added to that list, was invisible to it no matter what its
RLS state was. ``fetch_privileged_tables_without_rls`` replaces "is this table on my
list" with "does the catalog say anon/authenticated can touch this table at all" --
which is the question that actually matters, and the one a hardcoded list can never
keep in sync with every future migration.

This is REPRODUCED AND VERIFIED against a REAL PostgreSQL server: creates a table named
something that is deliberately NOT in ``post_migration_validation.required_tables``,
grants ``authenticated`` SELECT on it (Supabase's default privilege shape for a new
table) without enabling RLS, and proves the new privilege-driven check finds it anyway
-- which the old name-list check structurally could not have, and does not need a
exploit reproduced against it to prove: the defect was that nothing looked at the
table in the first place.

EVERY TEST IN THIS FILE SKIPS unless a real PostgreSQL is reachable (same convention as
tests/test_sec09_marketplace_evidence_service_role_only_postgres.py) and the
``anon``/``authenticated``/``service_role`` roles exist on it.
"""
from __future__ import annotations

import importlib.util
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
    pytest.skip("DB-03 requires PostgreSQL", allow_module_level=True)

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "post_migration_validation.py"

_UNTRACKED_TABLE = "db03_brand_new_customer_table_not_on_any_list"


def _load_module():
    spec = importlib.util.spec_from_file_location("post_migration_validation_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _connect():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    return conn


def _roles_present(cur) -> bool:
    cur.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')")
    found = {r[0] for r in cur.fetchall()}
    return {"anon", "authenticated", "service_role"} <= found


@pytest.fixture
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

    cur.execute(f"DROP TABLE IF EXISTS public.{_UNTRACKED_TABLE} CASCADE")
    cur.execute(f"CREATE TABLE public.{_UNTRACKED_TABLE} (id serial PRIMARY KEY, secret text)")
    try:
        yield conn
    finally:
        cur.execute(f"DROP TABLE IF EXISTS public.{_UNTRACKED_TABLE} CASCADE")
        conn.close()


def test_the_untracked_table_really_is_absent_from_the_old_fixed_list():
    """Sanity check on the reproduction itself: if this table were already on
    ``required_tables``, the rest of this file would be proving nothing.
    """
    mod = _load_module()
    import inspect

    source = inspect.getsource(mod.validate_production_db)
    assert _UNTRACKED_TABLE not in source


def test_a_newly_privileged_table_with_no_rls_is_caught(db):
    """Reproduction: grant authenticated a privilege, enable nothing, and prove the new
    catalog-driven check finds exactly this table -- one that was never on any list.
    """
    mod = _load_module()
    cur = db.cursor()
    cur.execute(f"GRANT SELECT ON public.{_UNTRACKED_TABLE} TO authenticated")

    rows = mod.fetch_privileged_tables_without_rls(cur)
    names = {name for name, _rls in rows}
    assert _UNTRACKED_TABLE in names, (
        "the privilege-driven check must see this table purely because authenticated "
        "holds a grant on it, with no table-name list involved"
    )

    passed = mod.evaluate_privilege_hardening(rows)
    assert passed is False


def test_enabling_rls_on_that_same_table_makes_it_pass(db):
    """NEGATIVE CONTROL: the check is not "fail every table" -- once RLS is actually
    enabled, the same privileged, previously-untracked table must stop being flagged.

    Asserts on THIS table's own row, not on `evaluate_privilege_hardening`'s whole-
    catalog verdict: a real database can carry OTHER tables this fixture does not
    control that are unprotected for reasons unrelated to this reproduction (this
    session's own real-Postgres run found exactly that -- plans/plan_features/
    plan_limits/plan_prices/plan_migration_mapping, a pre-existing, separately-tracked
    finding -- see DB-03-FOLLOWUP). Calling the all-or-nothing verdict here would make
    this negative control depend on every OTHER table in the database being clean,
    which is not what it is testing.
    """
    mod = _load_module()
    cur = db.cursor()
    cur.execute(f"GRANT SELECT ON public.{_UNTRACKED_TABLE} TO authenticated")
    cur.execute(f"ALTER TABLE public.{_UNTRACKED_TABLE} ENABLE ROW LEVEL SECURITY")

    rows = mod.fetch_privileged_tables_without_rls(cur)
    by_name = dict(rows)
    assert by_name[_UNTRACKED_TABLE] is True


def test_a_table_with_no_anon_or_authenticated_grant_is_not_flagged_even_without_rls(db):
    """NEGATIVE CONTROL: an internal/service-only table with RLS disabled is a normal,
    intentional configuration (service_role bypasses RLS regardless) -- this check must
    not flag tables anon/authenticated were never granted anything on.

    REVOKE is explicit and not optional here: on this Supabase-shaped substrate, a bare
    `CREATE TABLE` with no GRANT statement at all still ends up privileged -- `ALTER
    DEFAULT PRIVILEGES ... IN SCHEMA public` (the exact mechanism `fetch_privileged_
    tables_without_rls`'s own docstring names as "Supabase's default privilege shape")
    grants anon/authenticated the full DML set automatically. Verified directly this
    session: a freshly created scratch table, with no GRANT ever issued against it,
    already held anon INSERT/SELECT/UPDATE/DELETE. So "no GRANT in this test's body"
    does not mean "no privilege" against a real Supabase-shaped database -- achieving
    that state needs an explicit REVOKE, which is the fix here, not a behavioural
    assumption that happened to hold against a plainer Postgres instance.
    """
    mod = _load_module()
    cur = db.cursor()
    cur.execute(f"REVOKE ALL ON public.{_UNTRACKED_TABLE} FROM anon, authenticated")

    rows = mod.fetch_privileged_tables_without_rls(cur)
    names = {name for name, _rls in rows}
    assert _UNTRACKED_TABLE not in names
