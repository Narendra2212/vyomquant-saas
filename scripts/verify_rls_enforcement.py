"""
scripts/verify_rls_enforcement.py - does RLS actually *bind* on the SQLAlchemy path?

`scripts/verify_rls.py` already answers a different question: is RLS configured?
It reads `pg_tables.rowsecurity` and counts rows in `pg_policies`, both of which
report *configuration*. Neither tells you whether the policies are ever applied to
the role the application connects as, and `pg_tables` does not expose
`relforcerowsecurity` at all.

That gap matters because PostgreSQL exempts a table's owner from its own policies
unless FORCE ROW LEVEL SECURITY is set. `alembic/versions/implement_rls_policies_20260817.py`
issues only `ENABLE ROW LEVEL SECURITY`. If the role in DATABASE_URL owns those tables
- on Supabase, `postgres` does - then every policy in that migration is inert on the
SQLAlchemy path, and a configuration-level check still reports green.

A second reason this cannot be answered from source: every statement in that migration
is wrapped in a try/except that only prints the failure, so the migration reports
success even when an ENABLE or a CREATE POLICY did not apply. The live state is
knowable only by asking the database.

This script connects exactly as the application does - same DATABASE_URL, same role -
and reports:

  1. The connected role, and whether it holds BYPASSRLS or SUPERUSER.
  2. Per table: owner, ENABLE state, FORCE state, and whether the connected role is
     the owner (and therefore exempt when FORCE is off).
  3. Per table: policy inventory broken down by command, flagging RLS-enabled tables
     with no policy (deny-all for non-exempt roles) and tables whose policies cover
     only some commands.
  4. The `tenant_id` column type per table, against the policy predicate's expected type.
  5. An empirical enforcement probe (read-only).

The probe is the decisive check. The policies compare `tenant_id` against
`current_setting('app.current_tenant_id', true)`. With no tenant context set that
setting is NULL, so the comparison yields NULL - never true - and an enforced policy
therefore matches zero rows. So a non-zero count with no tenant context set is proof
that the policy is not being applied to this role.

READ-ONLY. Issues only SELECT and a transaction-scoped SET LOCAL, inside a transaction
that is always rolled back. Writes nothing.

Usage:
    python scripts/verify_rls_enforcement.py

Requires DATABASE_URL in .env - the same direct-Postgres URL the backend uses. The
Supabase anon/service keys are not used here; PostgREST cannot see pg_class.relowner
or relforcerowsecurity, which is the whole point of connecting directly.

Exit codes:
    0 - RLS is enforced on every tenant table for the connected role
    1 - at least one tenant table is unenforced, or a policy gap was found
    2 - could not connect or could not determine state
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(dotenv_path=_REPO_ROOT / ".env")

# Running this as `python scripts/verify_rls_enforcement.py` puts scripts/ on sys.path,
# not the repo root, so `import backend_app` would fail and resolve_database_url() would
# silently fall back to the raw env var - skipping the app's Supavisor rewrite and
# auditing a different connection than the one the backend actually uses.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from sqlalchemy import create_engine, text
except ImportError:
    print("ERROR: SQLAlchemy is required. pip install sqlalchemy psycopg2-binary")
    sys.exit(2)


# ── Table inventory ────────────────────────────────────────────────────────────
#
# Split by which migration enables RLS, because the three groups fail differently.

# alembic/versions/implement_rls_policies_20260817.py - 9 tables, each with one
# FOR ALL policy keyed on app.current_tenant_id. These are the tenant-scoped
# execution-path tables reached via SQLAlchemy.
TENANT_SCOPED_TABLES = [
    "orders",
    "positions",
    "fills",
    "dag_tasks",
    "execution_records",
    "idempotency_keys",
    "transaction_records",
    "transaction_checkpoints",
    "reconciliation_mismatches",
]

# alembic/versions/e88f9911b5a2_consolidate_full_schema.py enables RLS on these,
# but defines policies for only dag_tasks and execution_records. The remaining five
# are policied in the parallel raw-SQL migration system (backend_app/migrations/*.sql
# and archived_migrations/), not in the Alembic chain. An environment that ran only
# Alembic has five deny-all tables for any non-exempt role.
SQL_MIGRATION_POLICIED_TABLES = [
    "copilot_sessions",
    "copilot_messages",
    "waitlist",
    "library_strategies",
    "library_ratings",
]

# alembic/versions/6f1b3d9c8a7e_add_referrals.py enables RLS and defines exactly one
# policy: referrals_referrer_select, FOR SELECT TO authenticated. No INSERT/UPDATE/
# DELETE policy exists, so writes are denied to any role that is not exempt.
PARTIAL_POLICY_TABLES = ["referrals"]

# Supabase-managed application tables the existing verify_rls.py covers. Included so
# this script's picture is not narrower than the one it supplements.
SUPABASE_APP_TABLES = [
    "strategies",
    "profiles",
    "exchange_credentials",
    "library_subscriptions",
    "strategy_deployments",
    "paper_sessions",
]

ALL_TABLES = (
    TENANT_SCOPED_TABLES
    + SQL_MIGRATION_POLICIED_TABLES
    + PARTIAL_POLICY_TABLES
    + SUPABASE_APP_TABLES
)

# The policy predicate casts tenant_id::text only on the two uuid columns
# (execution_records, reconciliation_mismatches). The other seven compare a
# varchar(36) column to a text setting, which is a valid comparison and needs no cast.
# Divergence from this map means a policy predicate may raise at evaluation time -
# an error that stays invisible for as long as the owner bypass holds.
EXPECTED_TENANT_ID_TYPE = {
    "orders": "character varying",
    "positions": "character varying",
    "fills": "character varying",
    "dag_tasks": "character varying",
    "idempotency_keys": "character varying",
    "transaction_records": "character varying",
    "transaction_checkpoints": "character varying",
    "execution_records": "uuid",
    "reconciliation_mismatches": "uuid",
}

PASS = "  [PASS]"
FAIL = "  [FAIL]"
WARN = "  [WARN]"
INFO = "  [INFO]"
SKIP = "  [SKIP]"


def resolve_database_url():
    """The same URL the backend connects with.

    Imports the application's own resolution rather than re-deriving it, so this
    script cannot drift from the thing it is auditing - including the Supavisor
    rewrite that moves the port to 6543 and injects the project ref into the
    username. Importing the module is cheap: DatabasePool.__new__ does no I/O and
    the engine is built only by initialize(), which is never called here.
    """
    try:
        from backend_app.core.database_pool import DATABASE_URL

        return DATABASE_URL, "backend_app.core.database_pool"
    except Exception as exc:
        url = os.environ.get("DATABASE_URL")
        if not url:
            print(f"ERROR: could not import the app's DATABASE_URL ({exc}) and "
                  f"DATABASE_URL is not set in the environment.")
            sys.exit(2)
        return url, "environment (app import failed: %s)" % exc


def redact(url):
    """The URL with its password removed, for printing."""
    if "@" not in url:
        return url
    scheme_sep = url.find("://")
    if scheme_sep == -1:
        return url
    scheme = url[: scheme_sep + 3]
    rest = url[scheme_sep + 3:]
    creds, _, host = rest.partition("@")
    user, sep, _pw = creds.partition(":")
    return f"{scheme}{user}{':***' if sep else ''}@{host}"


def check_role(conn):
    """The connected role and whether it is exempt from RLS by role attribute.

    BYPASSRLS and SUPERUSER are role-level exemptions. FORCE ROW LEVEL SECURITY does
    NOT override them - FORCE removes only the *owner's* implicit exemption. A role
    holding BYPASSRLS sees every row on every table no matter how the tables are
    configured, which is why Supabase's service_role is unaffected by any of this.
    """
    print("\n=== 1. CONNECTED ROLE ===")
    row = conn.execute(
        text(
            """
            SELECT current_user        AS current_role_name,
                   session_user        AS session_role_name,
                   r.rolsuper          AS is_superuser,
                   r.rolbypassrls      AS has_bypassrls
            FROM pg_roles r
            WHERE r.rolname = current_user
            """
        )
    ).mappings().first()

    if row is None:
        print(f"{FAIL} could not resolve current_user in pg_roles.")
        return None

    print(f"{INFO} current_user  : {row['current_role_name']}")
    print(f"{INFO} session_user  : {row['session_role_name']}")

    exempt = False
    if row["is_superuser"]:
        print(f"{FAIL} role is SUPERUSER - RLS is never applied, FORCE cannot change that.")
        exempt = True
    if row["has_bypassrls"]:
        print(f"{FAIL} role holds BYPASSRLS - RLS is never applied, FORCE cannot change that.")
        exempt = True
    if not exempt:
        print(f"{PASS} role holds neither SUPERUSER nor BYPASSRLS.")

    return {"name": row["current_role_name"], "role_exempt": exempt}


def check_table_state(conn, role):
    """Owner, ENABLE, FORCE, and owner-exemption per table."""
    print("\n=== 2. PER-TABLE RLS STATE ===")
    rows = conn.execute(
        text(
            """
            SELECT c.relname                      AS table_name,
                   pg_get_userbyid(c.relowner)    AS table_owner,
                   c.relrowsecurity               AS rls_enabled,
                   c.relforcerowsecurity          AS rls_forced
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind = 'r'
              AND c.relname = ANY(:tables)
            ORDER BY c.relname
            """
        ),
        {"tables": ALL_TABLES},
    ).mappings().all()

    state = {r["table_name"]: dict(r) for r in rows}

    missing = [t for t in ALL_TABLES if t not in state]
    if missing:
        print(f"{SKIP} not present in this database: {', '.join(sorted(missing))}")

    print(f"\n  {'table':<32} {'owner':<16} {'ENABLE':<8} {'FORCE':<8} effective")
    print(f"  {'-' * 32} {'-' * 16} {'-' * 8} {'-' * 8} {'-' * 28}")

    for name in ALL_TABLES:
        if name not in state:
            continue
        s = state[name]
        owner_exempt = s["table_owner"] == role["name"] and not s["rls_forced"]
        s["owner_exempt"] = owner_exempt

        if role["role_exempt"]:
            effective = "NOT ENFORCED (role attr)"
        elif not s["rls_enabled"]:
            effective = "NOT ENFORCED (disabled)"
        elif owner_exempt:
            effective = "NOT ENFORCED (owner)"
        else:
            effective = "enforced"
        s["effective"] = effective

        print(
            f"  {name:<32} {s['table_owner']:<16} "
            f"{'yes' if s['rls_enabled'] else 'NO':<8} "
            f"{'yes' if s['rls_forced'] else 'NO':<8} {effective}"
        )

    unenforced = [n for n, s in state.items() if s["effective"] != "enforced"]
    if unenforced:
        print(f"\n{FAIL} {len(unenforced)} of {len(state)} tables do not enforce RLS "
              f"for role '{role['name']}'.")
    else:
        print(f"\n{PASS} every present table enforces RLS for role '{role['name']}'.")

    return state


def check_policies(conn, state):
    """Policy inventory, with the two gap shapes called out separately.

    An RLS-enabled table with no policy denies all access to non-exempt roles - it
    fails closed, which is safe but breaks the feature. A table with policies for
    only some commands (referrals: SELECT only) denies the uncovered commands, which
    looks like a working table until a write is attempted.
    """
    print("\n=== 3. POLICY COVERAGE ===")
    rows = conn.execute(
        text(
            """
            SELECT tablename, policyname, cmd, roles
            FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename = ANY(:tables)
            ORDER BY tablename, policyname
            """
        ),
        {"tables": ALL_TABLES},
    ).mappings().all()

    by_table = {}
    for r in rows:
        by_table.setdefault(r["tablename"], []).append(r)

    problems = []

    for name in ALL_TABLES:
        if name not in state:
            continue
        policies = by_table.get(name, [])
        enabled = state[name]["rls_enabled"]

        if not policies:
            if enabled:
                print(f"{FAIL} {name}: RLS enabled, ZERO policies - "
                      f"denies all access to any non-exempt role.")
                problems.append(f"{name}: RLS enabled with no policy")
            else:
                print(f"{WARN} {name}: no RLS, no policies - unprotected.")
                problems.append(f"{name}: no RLS and no policy")
            continue

        cmds = {p["cmd"] for p in policies}
        covers_all = "ALL" in cmds
        detail = ", ".join(sorted(f"{p['cmd']}:{p['policyname']}" for p in policies))

        if covers_all:
            print(f"{PASS} {name}: {detail}")
        else:
            uncovered = {"SELECT", "INSERT", "UPDATE", "DELETE"} - cmds
            if uncovered:
                print(f"{FAIL} {name}: {detail}")
                print(f"         no policy for {', '.join(sorted(uncovered))} - "
                      f"those commands are denied to non-exempt roles.")
                problems.append(
                    f"{name}: no policy for {', '.join(sorted(uncovered))}"
                )
            else:
                print(f"{PASS} {name}: {detail}")

    return problems


def check_column_types(conn, state):
    """tenant_id type vs. what the policy predicate assumes."""
    print("\n=== 4. tenant_id COLUMN TYPES ===")
    rows = conn.execute(
        text(
            """
            SELECT table_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND column_name  = 'tenant_id'
              AND table_name   = ANY(:tables)
            ORDER BY table_name
            """
        ),
        {"tables": TENANT_SCOPED_TABLES},
    ).mappings().all()

    actual = {r["table_name"]: r["data_type"] for r in rows}
    problems = []

    for name, expected in sorted(EXPECTED_TENANT_ID_TYPE.items()):
        if name not in state:
            continue
        got = actual.get(name)
        if got is None:
            print(f"{FAIL} {name}: no tenant_id column, but a tenant policy references it.")
            problems.append(f"{name}: tenant_id column missing")
        elif got != expected:
            print(f"{FAIL} {name}: tenant_id is '{got}', policy predicate assumes "
                  f"'{expected}'.")
            problems.append(f"{name}: tenant_id is {got}, expected {expected}")
        else:
            print(f"{PASS} {name}: tenant_id is '{got}'.")

    if problems:
        print(f"\n{WARN} A type mismatch makes the policy predicate raise when it is "
              f"evaluated. While an exemption is in force the predicate is never "
              f"evaluated, so the error stays hidden until RLS starts binding.")

    # positions is the known model/migration divergence: position_model.py declares
    # PG_UUID(as_uuid=True) while d97ffff9c3bb_execution_schema_rebuild.py creates
    # String(36). Whichever is live, the two disagree.
    if actual.get("positions"):
        print(f"{INFO} positions.tenant_id is '{actual['positions']}' on disk. Note "
              f"core/position_model.py declares PG_UUID(as_uuid=True) while "
              f"d97ffff9c3bb_execution_schema_rebuild.py creates String(36) - the ORM "
              f"model and the migration disagree regardless of which one is live.")

    return problems


def probe_enforcement(conn, state, role):
    """Empirical check: with no tenant context, an enforced policy yields zero rows.

    The policies compare tenant_id against current_setting('app.current_tenant_id',
    true). Unset, that returns NULL; the comparison yields NULL rather than true; an
    enforced policy therefore matches nothing. So any non-zero count here proves the
    policy is not being applied to this role.

    A zero count is not proof of the opposite - the table may simply be empty - so
    that case is reported as inconclusive rather than as a pass.

    Read-only: COUNT(*) plus a transaction-scoped SET LOCAL, always rolled back.
    """
    print("\n=== 5. EMPIRICAL ENFORCEMENT PROBE ===")
    print(f"{INFO} Counting rows with app.current_tenant_id unset. An enforced policy "
          f"must return 0.\n")

    unenforced, inconclusive, enforced = [], [], []

    for name in TENANT_SCOPED_TABLES:
        if name not in state:
            continue
        if not state[name]["rls_enabled"]:
            print(f"{FAIL} {name}: RLS not enabled - every row is visible by "
                  f"configuration, no probe needed.")
            unenforced.append(name)
            continue

        trans = conn.begin()
        try:
            conn.execute(text("RESET app.current_tenant_id"))
            count = conn.execute(
                text(f'SELECT count(*) FROM public."{name}"')  # noqa: S608 - name is
                # drawn from this module's hardcoded inventory, never from input.
            ).scalar()

            if count and count > 0:
                print(f"{FAIL} {name}: {count} rows visible with no tenant context - "
                      f"policy is NOT being applied to role '{role['name']}'.")
                unenforced.append(name)
            else:
                print(f"{SKIP} {name}: 0 rows visible. Enforced, or the table is "
                      f"empty - inconclusive.")
                inconclusive.append(name)
        except Exception as exc:
            msg = str(exc).strip().splitlines()[0]
            print(f"{WARN} {name}: probe raised - {msg}")
            print(f"         An 'operator does not exist' error here means the policy "
                  f"predicate's type assumption is wrong (see section 4).")
            inconclusive.append(name)
        finally:
            trans.rollback()

    return unenforced, inconclusive, enforced


def main():
    print("=" * 78)
    print("  RLS ENFORCEMENT VERIFICATION - does RLS bind on the SQLAlchemy path?")
    print("=" * 78)

    url, source = resolve_database_url()
    print(f"\n{INFO} DATABASE_URL from: {source}")
    print(f"{INFO} target: {redact(url)}")

    if url.startswith("sqlite"):
        print(f"\n{FAIL} DATABASE_URL points at SQLite, which has no RLS. Point this "
              f"at the Postgres instance you want to audit.")
        sys.exit(2)

    try:
        engine = create_engine(url, pool_pre_ping=True)
        conn = engine.connect()
    except Exception as exc:
        print(f"\n{FAIL} could not connect: {exc}")
        sys.exit(2)

    try:
        role = check_role(conn)
        if role is None:
            sys.exit(2)

        state = check_table_state(conn, role)
        if not state:
            print(f"\n{FAIL} none of the expected tables exist in this database.")
            sys.exit(2)

        policy_problems = check_policies(conn, state)
        type_problems = check_column_types(conn, state)
        unenforced, inconclusive, _ = probe_enforcement(conn, state, role)
    finally:
        conn.close()
        engine.dispose()

    print("\n" + "=" * 78)
    print("  SUMMARY")
    print("=" * 78)

    failed = False

    if role["role_exempt"]:
        print(f"\n{FAIL} Role '{role['name']}' is exempt from RLS by role attribute "
              f"(SUPERUSER or BYPASSRLS).")
        print("       No table-level setting changes this. FORCE ROW LEVEL SECURITY "
              "removes only the\n       owner's implicit exemption, not a role "
              "attribute. Isolation on this connection\n       rests entirely on "
              "application-level WHERE clauses.")
        failed = True

    owner_exempt = [n for n, s in state.items() if s.get("owner_exempt")]
    if owner_exempt:
        print(f"\n{FAIL} {len(owner_exempt)} tables are owned by the connected role "
              f"with FORCE off, so the\n       owner exemption applies and their "
              f"policies are inert:")
        for n in sorted(owner_exempt):
            print(f"         - {n}")
        failed = True

    if unenforced:
        print(f"\n{FAIL} The probe confirmed {len(unenforced)} tables return rows with "
              f"no tenant context set,\n       which means no policy is filtering "
              f"them:")
        for n in sorted(unenforced):
            print(f"         - {n}")
        failed = True

    if inconclusive:
        print(f"\n{WARN} {len(inconclusive)} tables returned 0 rows - enforced, or "
              f"empty. Seed one row per\n       tenant and re-run to make these "
              f"conclusive:")
        print(f"         {', '.join(sorted(inconclusive))}")

    if policy_problems:
        print(f"\n{FAIL} {len(policy_problems)} policy coverage gaps:")
        for p in policy_problems:
            print(f"         - {p}")
        failed = True

    if type_problems:
        print(f"\n{FAIL} {len(type_problems)} tenant_id type mismatches:")
        for p in type_problems:
            print(f"         - {p}")
        failed = True

    if failed:
        print("\n  Do not apply FORCE ROW LEVEL SECURITY while any of the above holds.")
        print("  With tenant context unset on request paths, forcing RLS turns reads "
              "into silently\n  empty result sets on the execution-path tables rather "
              "than into errors. Establish\n  tenant context propagation first, then "
              "force one table at a time.")
        sys.exit(1)

    print(f"\n{PASS} RLS is enforced for role '{role['name']}' on every table checked.")
    sys.exit(0)


if __name__ == "__main__":
    main()
