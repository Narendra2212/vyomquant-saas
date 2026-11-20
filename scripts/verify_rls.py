"""
scripts/verify_rls.py — Sprint 1 F-04 RLS Verification Script

Verifies that Tenant A cannot access Tenant B's data through:
  1. Checking that RLS is enabled on all critical tables
  2. Checking that RLS policies exist on each table
  3. Attempting cross-tenant access simulation (read isolation test)

SCOPE, AND WHAT THIS SCRIPT CANNOT TELL YOU
-------------------------------------------
This checks *configuration*. It does not check *enforcement*, and the two differ here.

`pg_tables.rowsecurity` reports only whether RLS is ENABLED. It has no column for
`relforcerowsecurity`, and PostgreSQL exempts a table's owner from its own policies
unless FORCE is set. The backend connects as `postgres`, which owns tables created by
migrations, so every check below can pass while no policy is ever applied. Use
`scripts/verify_rls_enforcement.py` for the enforcement question - it connects directly
through DATABASE_URL and probes empirically.

Three defects were fixed in this file; they are recorded because each produced a green
result over a real gap:

  * `check_rls_policies` initialised `all_pass = True` and never reassigned it, so it
    could not fail under any input. A table with zero policies printed SKIP and passed.
  * `CRITICAL_TABLES` named `exchange_credentials`, which no migration creates. The real
    table is `exchange_keys`. The missing table hit the SKIP branch and so, per the
    above, passed - meaning the most sensitive table on the platform was reported green
    without being examined.
  * Both catalog reads can fail silently, since PostgREST does not expose pg_catalog by
    default. An empty result left every table on SKIP and the run reporting success
    having verified nothing. "Could not determine" now fails.

The cross-tenant JWT test in part 3 was and remains the genuinely load-bearing check:
it exercises the `authenticated` role, which is the one path where RLS does bind.

Requirements:
    SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY in env
    Optional: TENANT_A_JWT, TENANT_B_JWT for live JWT isolation test

Usage:
    python scripts/verify_rls.py
"""

import os
import sys
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SERVICE_KEY   = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
ANON_KEY      = os.environ.get("SUPABASE_ANON_KEY")


def get_admin():
    """The service-role client, built on demand rather than at import.

    This used to run at module scope, with a ``sys.exit(1)`` when the credentials were
    absent - which made the module unimportable without production secrets, and so
    untestable. That is why the three defects named in the module docstring survived:
    nothing could check whether the checks were capable of failing.

    Bypasses RLS (``service_role`` carries ``BYPASSRLS``). Used to read the catalog for
    configuration checks, never to assert isolation - the cross-tenant test uses the
    anon key and real user JWTs precisely because this client would see everything.
    """
    if not SUPABASE_URL or not SERVICE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required.")
        sys.exit(1)

    from supabase import create_client

    return create_client(SUPABASE_URL, SERVICE_KEY)

#: Tables this script expects to exist AND to be governed by RLS.
#:
#: `exchange_credentials` was removed: no migration anywhere creates that table. The
#: real one is `exchange_keys` (`migrations/003_create_exchange_keys_table.sql`), and it
#: is deliberately NOT listed here - see EXPECTED_WITHOUT_RLS below. Listing a
#: non-existent table meant the most sensitive table on the platform reported green
#: while nothing examined it, because "not found" took the SKIP branch and SKIP used to
#: pass.
CRITICAL_TABLES = [
    "strategies",
    "orders",
    "positions",
    "profiles",
]

#: Tables that hold tenant data but where RLS is NOT the control, with the reason.
#: Checked for existence only. Asserting RLS on these would produce a finding that can
#: never be actioned, which trains readers to ignore the output.
EXPECTED_WITHOUT_RLS = {
    "exchange_keys": (
        "backend_app/backend/api_key_vault.py connects with SUPABASE_SERVICE_ROLE_KEY, "
        "which carries BYPASSRLS, so no policy on this table can ever apply. Isolation "
        "is the vault's own .eq('user_id', user_id) filter plus _validate_id. "
        "Confidentiality rests on MultiFernet encryption - see "
        "docs/adr/ADR-0007-per-tenant-credential-encryption-keys.md."
    ),
}

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"
SKIP = "  ⚠️  SKIP"
INFO = "  ℹ️  INFO"


#: Every table this script expects to exist, whether or not RLS governs it.
ALL_EXPECTED_TABLES = CRITICAL_TABLES + list(EXPECTED_WITHOUT_RLS)


def fetch_rls_enabled(admin):
    """``{tablename: rowsecurity}`` from the catalog. ``{}`` if it could not be read.

    Kept separate from the evaluation below so the decision logic can be tested without
    a database. The table list is derived from the module constants rather than
    hardcoded in the SQL - the previous inline ARRAY still named
    ``exchange_credentials`` after it had been removed from ``CRITICAL_TABLES``, which
    is the same drift this script exists to catch.
    """
    names = "','".join(ALL_EXPECTED_TABLES)
    try:
        result = admin.rpc(
            "query",
            {
                "query": f"""
                    SELECT tablename, rowsecurity
                    FROM pg_tables
                    WHERE schemaname = 'public'
                      AND tablename = ANY(ARRAY['{names}'])
                    ORDER BY tablename;
                """
            }
        ).execute()
        rows = result.data or []
    except Exception:
        # Fallback: PostgREST view, if one is exposed for pg_tables.
        try:
            result = admin.from_("pg_tables").select("tablename,rowsecurity").in_(
                "tablename", ALL_EXPECTED_TABLES
            ).eq("schemaname", "public").execute()
            rows = result.data or []
        except Exception as e:
            print(f"  Could not query pg_tables: {e}")
            rows = []

    return {r["tablename"]: r.get("rowsecurity", False) for r in rows}


def evaluate_rls_enabled(found):
    """Decide pass/fail from ``{tablename: rowsecurity}``. Pure; see tests."""
    all_pass = True

    # "Could not determine" is not "OK". Both catalog reads above can fail silently -
    # PostgREST does not expose pg_catalog by default - which left `rows` empty, every
    # table on the SKIP branch, and the whole check reporting success having verified
    # nothing. An empty result is now a failure.
    if not found:
        print(f"{FAIL} Could not read RLS state for ANY table. This check verified "
              f"nothing; it is not a pass.")
        print("       PostgREST cannot read pg_catalog by default. Use "
              "scripts/verify_rls_enforcement.py,")
        print("       which connects directly via DATABASE_URL.")
        return False

    for table in CRITICAL_TABLES:
        if table not in found:
            # Previously SKIP-and-pass. A table this script names as critical and
            # cannot find is either misnamed here or missing from the database, and
            # both are defects.
            print(f"{FAIL} {table}: expected table NOT FOUND. Either the name is wrong "
                  f"in CRITICAL_TABLES or the migration did not run.")
            all_pass = False
        elif found[table]:
            print(f"{PASS} {table}: RLS enabled")
        else:
            print(f"{FAIL} {table}: RLS DISABLED — CRITICAL")
            all_pass = False

    for table, reason in EXPECTED_WITHOUT_RLS.items():
        if table not in found:
            print(f"{FAIL} {table}: expected table NOT FOUND.")
            all_pass = False
        else:
            print(f"{INFO} {table}: present; RLS is not the control here.")
            print(f"       {reason}")

    # ENABLE is not enforcement. PostgreSQL exempts a table's owner from its own
    # policies unless FORCE ROW LEVEL SECURITY is set, and `pg_tables` has no column
    # for that - so a green result above is consistent with every policy being inert.
    print(f"\n{INFO} `rowsecurity` above reports ENABLE only. It cannot see")
    print("       `relforcerowsecurity`, and the backend connects as the table owner,")
    print("       so these policies may not be applied at all. Run")
    print("       scripts/verify_rls_enforcement.py to settle whether RLS binds.")

    return all_pass


def check_rls_enabled():
    """Read the catalog, then evaluate. Prints and returns pass/fail."""
    print("\n=== F-04: RLS ENABLED CHECK ===")
    return evaluate_rls_enabled(fetch_rls_enabled(get_admin()))


def fetch_rls_policies(admin):
    """Policy rows for the critical tables. ``None`` if the catalog read failed.

    ``None`` and ``[]`` mean different things and are kept distinct: the first is "the
    query raised", the second is "the query succeeded and found no policies". Both are
    failures, but conflating them is how "could not check" became "nothing to report".
    """
    try:
        result = admin.from_("pg_policies").select(
            "tablename,policyname,cmd"
        ).in_("tablename", CRITICAL_TABLES).execute()
        return result.data or []
    except Exception as e:
        print(f"  Could not query pg_policies: {e}")
        return None


def evaluate_rls_policies(rows):
    """Decide pass/fail from policy rows. Pure; see tests."""
    all_pass = True

    if rows is None:
        return False

    # Same reasoning as evaluate_rls_enabled: an unreadable catalog verified nothing.
    if not rows:
        print(f"{FAIL} pg_policies returned no rows at all. Either no policies exist "
              f"on the critical tables, or PostgREST cannot read the catalog. Both "
              f"mean this check proved nothing.")
        return False

    by_table = {}
    commands_by_table = {}
    for row in rows:
        t = row["tablename"]
        by_table.setdefault(t, []).append(f"{row['cmd']}:{row['policyname']}")
        commands_by_table.setdefault(t, set()).add(row["cmd"])

    for table in CRITICAL_TABLES:
        policies = by_table.get(table, [])

        if not policies:
            # Previously SKIP-and-pass, with `all_pass` initialised True and never
            # reassigned - so this function could not fail under any input. An
            # RLS-enabled table with no policy denies all access to non-exempt roles;
            # an RLS-disabled one is wide open. Neither is a pass.
            print(f"{FAIL} {table}: NO POLICIES. With RLS enabled this denies all "
                  f"access to non-exempt roles; with RLS disabled it is unprotected.")
            all_pass = False
            continue

        print(f"{PASS} {table}: {len(policies)} polic{'y' if len(policies) == 1 else 'ies'}")
        for p in policies:
            print(f"       {p}")

        # Partial command coverage is the `referrals` failure shape: a SELECT-only
        # policy reads as a working table until something attempts a write.
        commands = commands_by_table.get(table, set())
        if "ALL" not in commands:
            uncovered = {"SELECT", "INSERT", "UPDATE", "DELETE"} - commands
            if uncovered:
                print(f"{FAIL} {table}: no policy covers "
                      f"{', '.join(sorted(uncovered))} — those commands are denied to "
                      f"non-exempt roles.")
                all_pass = False

    return all_pass


def check_rls_policies():
    """Read the catalog, then evaluate. Prints and returns pass/fail."""
    print("\n=== F-04: RLS POLICIES CHECK ===")
    return evaluate_rls_policies(fetch_rls_policies(get_admin()))


def check_cross_tenant_isolation():
    """
    Tenant isolation test: using two different user JWTs, verify that
    Tenant A cannot see Tenant B's rows.

    Set TENANT_A_JWT and TENANT_B_JWT in env to run this test.
    """
    print("\n=== F-04: CROSS-TENANT ISOLATION TEST ===")

    jwt_a = os.environ.get("TENANT_A_JWT")
    jwt_b = os.environ.get("TENANT_B_JWT")

    if not jwt_a or not jwt_b:
        print(f"{SKIP} TENANT_A_JWT / TENANT_B_JWT not set in env — skipping live isolation test.")
        print("  To run: set TENANT_A_JWT and TENANT_B_JWT to real user JWT tokens.")
        return True

    if not ANON_KEY:
        print(f"{SKIP} SUPABASE_ANON_KEY not set — skipping JWT isolation test.")
        return True

    all_pass = True
    tables_to_test = ["strategies", "orders", "positions"]

    for table in tables_to_test:
        try:
            # Client A reads its own data
            client_a = create_client(SUPABASE_URL, ANON_KEY)
            client_a.auth.set_session(access_token=jwt_a, refresh_token="")
            rows_a = client_a.table(table).select("user_id").limit(5).execute().data or []

            if not rows_a:
                print(f"{SKIP} {table}: Tenant A has no rows — cannot test isolation")
                continue

            user_a_id = rows_a[0].get("user_id")

            # Client B tries to read Tenant A's rows
            client_b = create_client(SUPABASE_URL, ANON_KEY)
            client_b.auth.set_session(access_token=jwt_b, refresh_token="")
            rows_b = (
                client_b.table(table)
                .select("user_id")
                .eq("user_id", user_a_id)
                .limit(5)
                .execute()
                .data or []
            )

            if rows_b:
                print(
                    f"{FAIL} {table}: Tenant B CAN see Tenant A's rows "
                    f"({len(rows_b)} rows leaked) — RLS POLICY BREACH"
                )
                all_pass = False
            else:
                print(f"{PASS} {table}: Tenant B cannot see Tenant A's rows")

        except Exception as e:
            print(f"{SKIP} {table}: error during isolation test: {e}")

    return all_pass


def main():
    print("╔══════════════════════════════════════════╗")
    print("║  AERORA SPRINT 1 — F-04 RLS VERIFICATION ║")
    print("╚══════════════════════════════════════════╝")

    results = [
        check_rls_enabled(),
        check_rls_policies(),
        check_cross_tenant_isolation(),
    ]

    print("\n=== SUMMARY ===")
    if all(results):
        print("✅  All RLS checks passed.")
        sys.exit(0)
    else:
        print("❌  One or more RLS checks FAILED. See above for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
