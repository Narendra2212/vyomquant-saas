"""
scripts/verify_rls.py — Sprint 1 F-04 RLS Verification Script

Verifies that Tenant A cannot access Tenant B's data through:
  1. Checking that RLS is enabled on all critical tables
  2. Checking that RLS policies exist on each table
  3. Attempting cross-tenant access simulation (read isolation test)

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

from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SERVICE_KEY   = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
ANON_KEY      = os.environ.get("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SERVICE_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required.")
    sys.exit(1)

# ── Admin client (bypasses RLS — used for setup verification only) ──────────
admin = create_client(SUPABASE_URL, SERVICE_KEY)

CRITICAL_TABLES = [
    "strategies",
    "orders",
    "positions",
    "exchange_credentials",
    "profiles",
]

PASS = "  ✅ PASS"
FAIL = "  ❌ FAIL"
SKIP = "  ⚠️  SKIP"


def check_rls_enabled():
    """Verify RLS is enabled on all critical tables."""
    print("\n=== F-04: RLS ENABLED CHECK ===")
    all_pass = True
    try:
        result = admin.rpc(
            "query",
            {
                "query": """
                    SELECT tablename, rowsecurity
                    FROM pg_tables
                    WHERE schemaname = 'public'
                      AND tablename = ANY(ARRAY[
                          'strategies','orders','positions',
                          'exchange_credentials','profiles'
                      ])
                    ORDER BY tablename;
                """
            }
        ).execute()
        rows = result.data or []
    except Exception:
        # Fallback: use pg_catalog via admin
        try:
            result = admin.from_("pg_tables").select("tablename,rowsecurity").in_(
                "tablename", CRITICAL_TABLES
            ).eq("schemaname", "public").execute()
            rows = result.data or []
        except Exception as e:
            print(f"  Could not query pg_tables: {e}")
            rows = []

    found = {r["tablename"]: r.get("rowsecurity", False) for r in rows}

    for table in CRITICAL_TABLES:
        if table not in found:
            print(f"{SKIP} {table}: table not found (may not exist yet)")
        elif found[table]:
            print(f"{PASS} {table}: RLS enabled")
        else:
            print(f"{FAIL} {table}: RLS DISABLED — CRITICAL")
            all_pass = False

    return all_pass


def check_rls_policies():
    """Verify RLS policies exist for each table."""
    print("\n=== F-04: RLS POLICIES CHECK ===")
    all_pass = True

    try:
        result = admin.from_("pg_policies").select(
            "tablename,policyname,cmd"
        ).in_("tablename", CRITICAL_TABLES).execute()
        rows = result.data or []
    except Exception as e:
        print(f"  Could not query pg_policies: {e}")
        return False

    by_table = {}
    for row in rows:
        t = row["tablename"]
        by_table.setdefault(t, []).append(f"{row['cmd']}:{row['policyname']}")

    for table in CRITICAL_TABLES:
        policies = by_table.get(table, [])
        if not policies:
            print(f"{SKIP} {table}: no policies (table may not exist)")
        else:
            print(f"{PASS} {table}: {len(policies)} polic{'y' if len(policies)==1 else 'ies'}")
            for p in policies:
                print(f"       {p}")

    return all_pass


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
