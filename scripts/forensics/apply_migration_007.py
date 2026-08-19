"""
scripts/forensics/apply_migration_007.py

Applies migrations/007_add_marketplace_pricing_columns.sql to the production
database referenced by the ECS DATABASE_URL secret.

SECURITY: the connection string is never printed or logged. Only redacted
metadata is emitted.

Usage:
  python scripts/forensics/apply_migration_007.py --check
  python scripts/forensics/apply_migration_007.py --apply
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import boto3
import psycopg2

SECRET_ID = "vyomquant/production/database_url"
REGION = "ap-southeast-1"
MIGRATION = Path("migrations/007_add_marketplace_pricing_columns.sql")

EXPECTED_COLUMNS = [
    "cover_image",
    "currency",
    "deployment_requirements",
    "evaluation_score",
    "price",
    "subscriber_count",
    "subscription_tier",
    "verification_status",
    "version_history",
]


def get_dsn():
    sm = boto3.client("secretsmanager", region_name=REGION)
    val = sm.get_secret_value(SecretId=SECRET_ID)["SecretString"].strip()
    if val.startswith("{"):
        data = json.loads(val)
        for k in ("DATABASE_URL", "database_url", "url", "dsn"):
            if k in data:
                return data[k]
        raise SystemExit("secret JSON has no recognised URL key")
    return val


def describe(dsn):
    u = urlparse(dsn)
    host = u.hostname or ""
    parts = host.split(".")
    safe_host = "***." + ".".join(parts[-3:]) if len(parts) > 3 else "***"
    dbname = (u.path or "").lstrip("/") or "(default)"
    sslmode = "require" if "sslmode=require" in dsn else "(unset)"
    print("  host      : " + safe_host)
    print("  port      : " + str(u.port))
    print("  database  : " + dbname)
    print("  sslmode   : " + sslmode)
    print("  user      : " + ("[REDACTED]" if u.username else "(none)"))
    print("  password  : " + ("[REDACTED]" if u.password else "(none)"))


def current_columns(cur):
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'library_strategies'"
    )
    return set(r[0] for r in cur.fetchall())


def show_state(label, cols):
    print(label)
    for c in EXPECTED_COLUMNS:
        print("    " + c.ljust(26) + ("PRESENT" if c in cols else "MISSING"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not (args.apply or args.check):
        print("specify --check or --apply")
        return 2

    print("[1] resolving secret (value never printed)")
    dsn = get_dsn()
    describe(dsn)

    print("[2] connecting")
    conn = psycopg2.connect(dsn, connect_timeout=20)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute("SELECT current_database(), version()")
        db, ver = cur.fetchone()
        print("  connected to : " + str(db))
        print("  server       : " + ver.split(",")[0])

        print("[3] table existence")
        cur.execute("SELECT to_regclass('public.library_strategies') IS NOT NULL")
        exists = cur.fetchone()[0]
        print("  public.library_strategies exists: " + str(exists))
        if not exists:
            print("  ABORT: table missing; migration 007 assumes it exists")
            conn.rollback()
            return 1

        before = current_columns(cur)
        show_state("[4] pre-state of expected columns", before)
        missing_before = [c for c in EXPECTED_COLUMNS if c not in before]
        print("  missing count: " + str(len(missing_before)))
        print("  missing: " + str(missing_before))

        cur.execute("SELECT count(*) FROM public.library_strategies")
        rows_before = cur.fetchone()[0]
        print("[5] baseline row count: " + str(rows_before))

        if args.check:
            conn.rollback()
            print("[check] read-only, nothing applied")
            return 0

        print("[6] applying migration 007")
        cur.execute(MIGRATION.read_text(encoding="utf-8"))
        conn.commit()
        print("  committed")

        after = current_columns(cur)
        show_state("[7] post-state verification", after)
        still_missing = [c for c in EXPECTED_COLUMNS if c not in after]

        cur.execute("SELECT count(*) FROM public.library_strategies")
        rows_after = cur.fetchone()[0]
        print("  rows before/after: " + str(rows_before) + " / " + str(rows_after))

        print("[8] replaying the exact failing production query")
        cur.execute(
            "SELECT id, name, price, currency, subscription_tier, "
            "cover_image, evaluation_score, subscriber_count "
            "FROM public.library_strategies WHERE is_active = TRUE LIMIT 1"
        )
        cur.fetchall()
        print("  SELECT including price succeeded - no 42703")

        if still_missing:
            print("  FAIL: still missing " + str(still_missing))
            return 1
        if rows_after != rows_before:
            print("  FAIL: row count changed")
            return 1
        print("[OK] migration 007 applied and verified")
        return 0
    except Exception as exc:
        conn.rollback()
        print("  ERROR: " + type(exc).__name__ + ": " + str(exc))
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
