#!/usr/bin/env python3
"""Which objects declared by repository migrations are absent from the production database?

WHY THIS EXISTS
---------------
DEPLOY-04 recorded that "migrations 019 through 046 were closed out against production".
That claim was unverifiable, and measurement showed it was not true: migration
``017_plan_entitlements.sql`` has never been applied -- all four of its objects are absent
-- while migration 037's columns (``profiles.payment_provider``,
``profiles.next_billing_date``) ARE present. **The deployed schema is not any prefix of the
repository's migration sequence.** Migrations were applied out of order and out of band.

Nothing could catch that, because there is no migration ledger. ``public.recorded_migrations``
does not exist and is declared nowhere in the repository; ``public.alembic_version`` holds a
single revision pointer; ``supabase_migrations.schema_migrations`` holds ONE row against 50+
migration files. SEC-07 sat at VERIFIED_FIXED for the same reason -- migration 033 was
written, tested in CI against a schema CI provisions from source, and never applied to the
database that matters.

So this script does not read a ledger. It asks production directly whether each declared
object is there, which is the only question whose answer cannot be stale.

WHAT IT CHECKS
--------------
A hand-maintained inventory of objects that repository migrations declare, below. It is
deliberately NOT a SQL parser: parsing 50+ migration files to extract their declared objects
is a project, and a half-working parser that silently misses a declaration would restore
exactly the false confidence this replaces. Entries are added as they are verified, and an
absent object is reported with the migration that should have created it.

USAGE
-----
    python scripts/check_production_schema_drift.py            # exit 1 if anything is absent
    python scripts/check_production_schema_drift.py --json

Reads the DSN from AWS Secrets Manager (``vyomquant/production/database_url``) and NEVER
prints it. Catalog SELECTs only -- this script has no code path that writes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

SECRET_ID = "vyomquant/production/database_url"
REGION = "ap-southeast-1"

#: (kind, identifier, declaring-migration, note). kind is column|table|constraint.
#: `column` identifiers are "table.column"; `constraint` identifiers are "table/conname".
DECLARED_OBJECTS = [
    # ── backend_app/migrations/017_plan_entitlements.sql ───────────────────────
    # This heading said "NEVER APPLIED" until 2026-10-24, when the probe reported 4/4
    # declared objects present. It has since been applied. The heading is corrected rather
    # than deleted because the original finding -- that the deployed schema is NOT a prefix
    # of the migration sequence, 037 applied while 017 was not -- is the reason this
    # inventory exists, and a reader needs to know that gap was real. What must not survive
    # is the standing claim that it is still open: an inventory that asserts a state nobody
    # re-measured is the defect it was written to replace.
    ("column", "profiles.plan_limit_overrides", "017_plan_entitlements.sql",
     "Contracted capacity of the custom Enterprise tier, read by "
     "SubscriptionEngine.get_effective_quotas. Its absence is SILENT: "
     "core/subscription_dependencies.py::_PROFILE_COLUMN_SETS retries the read with "
     "fewer columns when PostgREST rejects an unknown one, so an Enterprise override "
     "simply never applies and nothing errors."),
    ("column", "profiles.billing_interval", "017_plan_entitlements.sql",
     "Billing interval; Stripe's was hardcoded 'month' before it."),
    ("table", "plan_usage_ledger", "017_plan_entitlements.sql",
     "Created with indexes and RLS by 017 section 3."),
    ("constraint", "profiles/chk_profiles_plan_limit_overrides_object",
     "017_plan_entitlements.sql",
     "Rejects a non-object in plan_limit_overrides, which the reader would otherwise "
     "silently ignore."),

    # ── backend_app/migrations/054_paper04_abandoned_session_state.sql ─────────
    # PAPER-04's terminal state. Inventoried because it shipped in PR #92 and WAS NEVER
    # APPLIED, and nothing noticed for the most instructive reason available: every
    # mechanism that depended on it fails in a way the code swallows.
    #
    #   * touch_session_heartbeat's UPDATE -> UndefinedColumn on last_heartbeat_at, caught
    #     by a try/except that logs at DEBUG, so the liveness record silently never
    #     existed.
    #   * list_stale_running_sessions' SELECT names the same column -> UndefinedColumn, so
    #     reconcile_orphaned_sessions logged one warning and changed nothing.
    #   * chk_paper_session_state still admitted four states, so the RUNNING -> ABANDONED
    #     transition the whole finding is about would have been rejected with 23514 had the
    #     read ever returned a row to transition.
    #
    # Measured against production 2026-10-24, before applying: all three refused. So the
    # half of PAPER-04 the ledger recorded as DELIVERED was decorative. That is what this
    # inventory is for -- an object declared in a merged migration is not an object that
    # exists.
    ("column", "paper_sessions.last_heartbeat_at",
     "054_paper04_abandoned_session_state.sql",
     "The liveness mark the session loop writes at the top of every iteration and "
     "reconciliation reads the staleness of. Its absence is SILENT: "
     "paper_repository.touch_session_heartbeat catches the UndefinedColumn and logs at "
     "debug, so a session never declares liveness and reconciliation cannot read it."),
    ("column", "paper_sessions.abandoned_reason",
     "054_paper04_abandoned_session_state.sql",
     "Why a session ended up ABANDONED, so an operator can tell 'no heartbeat since "
     "14:02' from 'the host was redeployed'. 054's own COMMENT says it is set by the "
     "reconciliation pass."),
    ("constraint", "paper_sessions/chk_paper_session_state",
     "054_paper04_abandoned_session_state.sql",
     "Recreated by 054 to admit the FIFTH state, ABANDONED. Present either way -- 009 "
     "declares the four-state version -- so this entry is satisfied by the constraint "
     "existing and NOT by it admitting ABANDONED; the five-state shape is asserted "
     "separately in tests/test_paper04_abandoned_sessions_are_reconciled.py."),
    # ── backend_app/migrations/055_deploy05_migration_ledger.sql ──────────────
    # DEPLOY-05's own ledger, inventoried like anything else so the thing that records
    # evidence is itself subject to the same evidence check.
    ("table", "recorded_migrations", "055_deploy05_migration_ledger.sql",
     "The migration ledger DEPLOY-04 claimed to write to and that was declared nowhere. "
     "One row per migration whose application has been ESTABLISHED, with how. Absence of "
     "a row means nobody checked -- never that the migration is unapplied. NOT alembic: "
     "`alembic_version`'s single pointer asserts the applied set is a PREFIX of the "
     "revision sequence, which production disproves (037 applied, 017 was not)."),
    ("constraint", "recorded_migrations/chk_recorded_migrations_verification",
     "055_deploy05_migration_ledger.sql",
     "Restricts `verification` to EVIDENCE_PROBED or APPLIED_BY_TOOL, so 'we ran it' and "
     "'we confirmed it' can never be recorded as the same claim -- the conflation that "
     "made DEPLOY-04's close-out unverifiable."),
    ("constraint", "recorded_migrations/chk_recorded_migrations_evidence_not_blank",
     "055_deploy05_migration_ledger.sql",
     "Refuses a row whose evidence is empty, so the ledger cannot fill with assertions "
     "that cite nothing."),

    # ── migrations/referral_system_redesign.sql -- NEVER APPLIED ──────────────
    ("column", "profiles.referred_by_user_id", "referral_system_redesign.sql",
     "routers/auth.py:233 writes this with a service-role client on referral signup, so "
     "that write fails today. The relationship is still recorded in "
     "referral_relationships, so this column is a denormalised copy."),
    ("column", "profiles.referral_code", "referral_system_redesign.sql",
     "Declared alongside referred_by_user_id."),
    ("constraint", "profiles/fk_profiles_referred_by", "referral_system_redesign.sql",
     "REFERENCES profiles(id) ON DELETE SET NULL."),
]

#: Objects a migration declares that production is RIGHT not to have. Reported as
#: informational so nobody 'fixes' them, with the reason attached.
DELIBERATELY_ABSENT = [
    ("column", "profiles.role",
     "Admin authority comes from the JWT (`app_metadata.role`) -- what routers/admin.py, "
     "the waitlist RLS policies and the frontend's AdminGuard all read. "
     "routers/user.py::get_profile already treats a profiles-side role as optional with "
     "the token as the source of truth."),
    ("column", "profiles.is_admin",
     "ZERO references anywhere in backend_app. Adding it would add a column nothing reads."),
    ("column", "profiles.api_calls_limit",
     "ZERO references anywhere in backend_app."),
    ("table", "download_analytics",
     "RESOLVED by DEPLOY-05: the build-or-remove decision was taken as REMOVE, and the "
     "feature is gone. It had no declaration in any .sql, no writer "
     "(downloadAnalyticsApi.track() was called from nowhere), and the admin dashboard's "
     "getStats() read was the only live caller -- which THREW a missing-relation error at "
     "every admin who opened that tab rather than showing an empty panel. "
     "algo22-terminal/src/lib/downloadAnalyticsApi.js is deleted and the Downloads tab is "
     "removed. Kept listed here so a future reader who finds the name in git history sees "
     "why production is right not to have the table."),
]


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
    cur = conn.cursor()

    def present(kind: str, ident: str) -> bool:
        if kind == "column":
            table, _, column = ident.partition(".")
            cur.execute("""
                SELECT 1 FROM information_schema.columns
                 WHERE table_schema='public' AND table_name=%s AND column_name=%s
            """, (table, column))
            return cur.fetchone() is not None
        if kind == "table":
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{ident}",))
            return bool(cur.fetchone()[0])
        if kind == "constraint":
            table, _, conname = ident.partition("/")
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
            if not cur.fetchone()[0]:
                return False
            cur.execute("""
                SELECT 1 FROM pg_constraint
                 WHERE conrelid = %s::regclass AND conname = %s
            """, (f"public.{table}", conname))
            return cur.fetchone() is not None
        raise ValueError(f"unknown kind {kind!r}")

    absent, found = [], []
    for kind, ident, migration, note in DECLARED_OBJECTS:
        (found if present(kind, ident) else absent).append(
            {"kind": kind, "object": ident, "migration": migration, "note": note}
        )

    unexpected = []
    for kind, ident, note in DELIBERATELY_ABSENT:
        if present(kind, ident):
            unexpected.append({"kind": kind, "object": ident, "note": note})

    # Whatever ledger-ish state does exist, reported so the picture is not implied.
    ledgers = {}
    cur.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute("SELECT version_num FROM public.alembic_version")
        ledgers["public.alembic_version"] = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT to_regclass('supabase_migrations.schema_migrations') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute("SELECT count(*) FROM supabase_migrations.schema_migrations")
        ledgers["supabase_migrations.schema_migrations"] = f"{cur.fetchone()[0]} row(s)"
    cur.execute("SELECT to_regclass('public.recorded_migrations') IS NOT NULL")
    ledgers["public.recorded_migrations"] = (
        "present" if cur.fetchone()[0] else "ABSENT (and declared nowhere in the repository)"
    )

    conn.close()

    if args.json:
        print(json.dumps({
            "absent": absent, "present": [f["object"] for f in found],
            "deliberately_absent_but_found": unexpected,
            "migration_ledger_state": ledgers,
            "ok": not absent and not unexpected,
        }, indent=2))
        return 1 if (absent or unexpected) else 0

    print(f"checked {len(DECLARED_OBJECTS)} declared object(s)")
    print("\nmigration ledger state:")
    for k, v in ledgers.items():
        print(f"   {k:44} {v}")

    if not absent:
        print("\nOK: every declared object in the inventory is present in production.")
    else:
        by_mig: dict[str, list[dict]] = {}
        for item in absent:
            by_mig.setdefault(item["migration"], []).append(item)
        print(f"\n{len(absent)} DECLARED OBJECT(S) ABSENT FROM PRODUCTION:")
        for mig in sorted(by_mig):
            items = by_mig[mig]
            total = sum(1 for o in DECLARED_OBJECTS if o[2] == mig)
            whole = " -- THE WHOLE MIGRATION APPEARS NEVER TO HAVE RUN" \
                if len(items) == total else ""
            print(f"\n  {mig}  ({len(items)} of {total} object(s) absent){whole}")
            for item in items:
                print(f"     {item['kind']:10} {item['object']}")
                print(f"        {item['note']}")

    if unexpected:
        print(f"\n{len(unexpected)} object(s) recorded as DELIBERATELY absent are present. "
              f"Either someone added them, or the reasoning below is now stale:")
        for item in unexpected:
            print(f"   {item['kind']:10} {item['object']}")
            print(f"      {item['note']}")

    return 1 if (absent or unexpected) else 0


if __name__ == "__main__":
    sys.exit(main())
