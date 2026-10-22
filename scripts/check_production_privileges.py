#!/usr/bin/env python3
"""Standing production check: browser roles must hold no WRITE on platform-owned tables.

WHY THIS EXISTS
---------------
SEC-07 (P0) was recorded VERIFIED_FIXED while it was live-exploitable in production. The
fix -- migration 033 -- was written, tested, and never applied to the production database.
Its tests pass in CI because CI provisions the schema from repository source. Production is
a different database, and nothing compared the two.

Measured on 2026-10-22 against production, before this script existed:

    anon           DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
    authenticated  DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE

RLS was enabled but its "Update own profile" policy constrains WHICH ROW, not WHICH
COLUMNS -- so any signed-in user could set their own subscription_tier, max_api_slots,
balance, is_frozen, available_discounts and billing_currency through the REST API with
their own JWT. Exposure assessment found no exploitation (every profile was still `free`
and every payment table empty -- the platform had not launched), but the grant was open.

The defect this script guards is therefore NOT "migration 033 is missing". It is "nobody
was checking production". A green CI lane says nothing about the deployed database.

WHAT IT CHECKS
--------------
For each platform-owned table, that neither `anon` nor `authenticated` holds any write
privilege, while `service_role` retains what the backend needs. SELECT is expected to
remain for the browser roles -- RLS scopes it to the caller's own row, and removing it
would break the UI.

USAGE
-----
    python scripts/check_production_privileges.py              # read-only, exit 1 on drift
    python scripts/check_production_privileges.py --json        # machine-readable

Reads the DSN from AWS Secrets Manager (``vyomquant/production/database_url``) and NEVER
prints it. Requires ``secretsmanager:GetSecretValue``. Catalog SELECTs only -- this script
has no code path that writes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

SECRET_ID = "vyomquant/production/database_url"
REGION = "ap-southeast-1"

BROWSER_ROLES = ("anon", "authenticated")
WRITE_PRIVS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")

#: Tables whose columns are set by the platform, never by the account holder. A browser
#: role holding any write privilege on one of these is a privilege-escalation path.
PLATFORM_TABLES = (
    "profiles",
    "fee_records",
    "execution_records",
    "orders",
    "referral_wallets",
    "referral_commissions",
    "referral_payouts",
    "billing_invoices",
    "payment_methods",
    "subscriptions",
)


def read_dsn() -> str:
    out = subprocess.run(
        ["aws", "secretsmanager", "get-secret-value", "--secret-id", SECRET_ID,
         "--region", REGION, "--query", "SecretString", "--output", "text"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        print(f"could not read {SECRET_ID}: {out.stderr.strip()[:200]}", file=sys.stderr)
        sys.exit(2)
    raw = out.stdout.strip()
    if raw.startswith("{"):
        blob = json.loads(raw)
        for key in ("DATABASE_URL", "database_url", "url", "dsn"):
            if key in blob:
                return blob[key]
        return next(iter(blob.values()))
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is required", file=sys.stderr)
        return 2

    conn = psycopg2.connect(read_dsn(), connect_timeout=30)
    conn.autocommit = True

    findings: list[dict] = []
    checked: list[str] = []
    missing: list[str] = []

    with conn.cursor() as cur:
        for table in PLATFORM_TABLES:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
            if not cur.fetchone()[0]:
                missing.append(table)
                continue
            checked.append(table)
            for role in BROWSER_ROLES:
                for priv in WRITE_PRIVS:
                    cur.execute(
                        "SELECT has_table_privilege(%s, %s, %s)",
                        (role, f"public.{table}", priv),
                    )
                    if cur.fetchone()[0]:
                        findings.append(
                            {"table": table, "role": role, "privilege": priv}
                        )
        # The backend must still be able to work.
        backend_broken = []
        for table in checked:
            for priv in ("SELECT", "INSERT", "UPDATE"):
                cur.execute(
                    "SELECT has_table_privilege('service_role', %s, %s)",
                    (f"public.{table}", priv),
                )
                if not cur.fetchone()[0]:
                    backend_broken.append(f"service_role lacks {priv} on {table}")
    conn.close()

    if args.json:
        print(json.dumps({
            "checked": checked, "absent": missing,
            "violations": findings, "backend_broken": backend_broken,
        }, indent=2))
    else:
        print(f"checked {len(checked)} platform table(s); {len(missing)} absent "
              f"({', '.join(missing) or 'none'})")
        if findings:
            print(f"\nVIOLATIONS -- {len(findings)} browser-role write privilege(s):")
            for f in findings:
                print(f"   {f['role']} holds {f['privilege']} on public.{f['table']}")
        else:
            print("\nOK: no browser role holds a write privilege on any checked table.")
        for problem in backend_broken:
            print(f"BACKEND RISK: {problem}")

    if findings or backend_broken:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
