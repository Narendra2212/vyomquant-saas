#!/usr/bin/env python3
"""Standing production check: browser roles hold least privilege on schema `public`.

WHY THIS EXISTS
---------------
SEC-07 (P0) was recorded VERIFIED_FIXED while it was live-exploitable in production. The
fix -- migration 033 -- was written, tested, and never applied to the production database.
Its tests pass in CI because CI provisions the schema from repository source. Production is
a different database, and nothing compared the two.

Measured on 2026-10-22 against production, before any of this existed:

    anon           DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE
    authenticated  DELETE,INSERT,REFERENCES,SELECT,TRIGGER,TRUNCATE,UPDATE

...on `public.profiles` and 59 other tables. RLS was enabled, but its "Update own profile"
policy constrains WHICH ROW, not WHICH COLUMNS -- so any signed-in user could set their own
subscription_tier, max_api_slots, balance, is_frozen and billing_currency through the REST
API with their own JWT.

The defect this guards is NOT "migration 033 is missing". It is "nobody was checking
production". A green CI lane says nothing about the deployed database.

ROOT CAUSE, and why a per-table REVOKE is not enough on its own
---------------------------------------------------------------
`pg_default_acl` carried, for schema public, from two grantors (postgres and
supabase_admin):

    anon=arwdDxtm   authenticated=arwdDxtm        (arwdDxtm is every privilege)

That is Supabase's stock default. It means every table created in `public` is
automatically granted full write to both browser roles -- so SEC-07 (profiles) and SEC-19
(10 platform tables) were not 11 separate mistakes, they were 11 instances of one rule,
and the next migration adding a table would have reopened the hole. INVARIANT 5 below
pins the default ACL itself.

A NOTE ON TRUNCATE
------------------
PostgreSQL has no TRUNCATE row-security policy command, so RLS cannot filter it. Measured
in production (inside a rolled-back transaction): `SET ROLE anon; TRUNCATE
public.exchange_keys` emptied 2 rows of user exchange credentials. `alembic_version`,
`risk_settings`, `referral_profiles`, `notification_settings` and `ticket_comments` were
destructible the same way. It was not reachable through PostgREST -- there is no TRUNCATE
verb, and none of the anon-executable functions in `public` contain one -- so it was a
latent hole, not an exploited one. "No route happens to exist" is not a control, hence
INVARIANT 2.

USAGE
-----
    python scripts/check_production_privileges.py              # read-only, exit 1 on drift
    python scripts/check_production_privileges.py --json       # machine-readable
    python scripts/check_production_privileges.py --verbose    # also print the clean state

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
#: Privileges no API client ever needs. TRUNCATE is unfilterable by RLS; REFERENCES and
#: TRIGGER are schema-modification rights.
NEVER_PRIVS = ("TRUNCATE", "REFERENCES", "TRIGGER")

# ─────────────────────────────────────────────────────────────────────────────────
# THE PATH-(A) ALLOWLIST.
#
# This backend reaches PostgREST through two clients that map to two different database
# roles, and the distinction is the whole basis of this check:
#
#   (A) anon key + the end user's JWT  -> role `authenticated`
#       core/dependencies.py: create_request_supabase:148, create_request_supabase_async:242,
#       and the `get_request_supabase` dependency at :389 that wraps the async one. Any
#       router parameter declared `Depends(get_request_supabase)` is one of these.
#   (B) service-role key                -> role `service_role`, bypasses RLS
#       get_service_supabase(), billing.py::_background_sb:203, library.py::_get_service_client:147,
#       referral.py::_get_service_client:78, api_key_vault.py:89.
#
# Revoking `authenticated` writes on a path-(A) table takes a production feature OFFLINE.
# Each entry below records the privileges that role genuinely needs and the code that
# needs them. Anything NOT listed here must have no browser-role write at all.
#
# Keep this in sync when a router changes which client it uses. `scripts/` has no way to
# derive it automatically: the routers wrap the factories in local helpers (`_sb`,
# `_get_supabase`, `_client`), so a grep for the factory names misses most call sites.
# ─────────────────────────────────────────────────────────────────────────────────
PATH_A_ALLOWLIST: dict[str, dict] = {
    "profiles": {
        "authenticated": {"UPDATE"},
        "why": "routers/user.py:93 update_profile; core/pricing_service.py:324 currency "
               "preference. UPDATE is permitted ONLY because the column-guard trigger "
               "constrains it -- see INVARIANT 4.",
        "requires_trigger": "trg_guard_profiles_platform_columns",
    },
    "strategies": {
        "authenticated": {"INSERT", "UPDATE", "DELETE"},
        "why": "routers/strategies.py:1275 create, :1578 rename, :1891 update, :3502 clone, "
               ":3907/:3949 pause/resume; backend/strategy_service.py x16. DELETE is "
               "required by backend/strategy_service.py:2502 (strategy deletion, reached "
               "from the UI via endpoints.strategies.delete).",
    },
    "strategy_versions": {
        "authenticated": {"INSERT", "UPDATE"},
        "why": "backend/strategy_service.py:1288 create_version and 7 more. Sole writer of "
               "this table anywhere; a strategy without a current version is not runnable.",
    },
    "strategy_deployments": {
        "authenticated": {"INSERT", "UPDATE"},
        "why": "backend/strategy_service.py x10; signal_service.py:5558/:6269; "
               "strategy_lifecycle.py:1317; routers/strategy_operations.py:2291",
    },
    "signals": {
        "authenticated": {"INSERT", "UPDATE"},
        "why": "backend/signal_service.py:288/:2533 INSERT, :356/:423/:482/:3465 UPDATE",
    },
    "risk_settings": {
        "authenticated": {"INSERT", "UPDATE"},
        "why": "routers/risk.py:227 upsert, reached from PUT /api/risk/settings",
    },
    "notifications": {
        "authenticated": {"INSERT", "UPDATE", "DELETE"},
        "why": "routers/notifications.py:180 INSERT, :343/:378 UPDATE, :406/:442 DELETE",
    },
    "notification_settings": {
        "authenticated": {"INSERT", "UPDATE"},
        "why": "routers/user.py:151 upsert",
    },
    "support_tickets": {
        "authenticated": {"INSERT", "UPDATE"},
        "why": "routers/support.py:459 INSERT; :671/:770/:917 UPDATE",
    },
    "ticket_comments": {
        "authenticated": {"INSERT"},
        "why": "routers/support.py:670/:916 INSERT",
    },
    "exchange_keys": {
        "authenticated": {"DELETE"},
        "why": "routers/exchange.py:756 delete_connection. Key material is written by "
               "backend/api_key_vault.py:170 as service_role, so INSERT/UPDATE are "
               "part of the invariant-3 advisory backlog, not a need here.",
    },
    "strategy_backtests": {
        "authenticated": {"INSERT", "UPDATE", "DELETE"},
        "why": "backend/backtest_service.py:776 DELETE, via _get_supabase:336 which is "
               "create_request_supabase_async. MISSED by two static-analysis passes "
               "before a grep for the factory itself found it -- see WHY INVARIANT 3 "
               "IS ADVISORY.",
    },
    "training_jobs": {
        "authenticated": {"INSERT", "UPDATE"},
        "why": "backend/training_status.py:573 resolves a path-(A) client "
               "(S.create_request_supabase_async). Same late discovery as "
               "strategy_backtests.",
    },
    "training_governance_decisions": {
        "authenticated": {"INSERT", "UPDATE", "DELETE"},
        "why": "written alongside training_jobs from backend/training_status.py's "
               "path-(A) client.",
    },
    "model_versions": {
        "authenticated": {"INSERT"},
        "why": "backend/model_versioning.py:1963 resolves a path-(A) client.",
    },
    "referral_codes": {
        "authenticated": {"INSERT"},
        "why": "routers/referral.py:259/:269 via _ensure_referral_code_exists",
    },
    "copilot_sessions": {
        "authenticated": {"INSERT", "DELETE"},
        "why": "routers/copilot.py:139 INSERT, :329 DELETE. NOTE: those call sites pass the "
               "whole `user` dict where create_request_supabase_async expects a token "
               "string, producing a malformed Authorization header -- so these writes are "
               "probably already failing. Grant retained pending that being fixed or the "
               "feature being removed; revoking on a guess is the costlier error.",
    },
    "copilot_messages": {
        "authenticated": {"INSERT"},
        "why": "routers/copilot.py:165/:210 INSERT. Same caveat as copilot_sessions.",
    },
    "strategy_research_reports": {
        "authenticated": {"INSERT"},
        "why": "routers/strategy_operations.py:2026. NOTE: built at :2001 from an un-awaited "
               "async call with access_token=None, so it is very likely already "
               "non-functional. Grant retained for the same reason as copilot.",
    },
    "waitlist": {
        "authenticated": {"UPDATE", "DELETE"},
        "why": "The ONLY table the browser writes directly with the anon key: "
               "src/lib/waitlistApi.js:109 update, :124 delete (admin UI, RLS-gated on "
               "auth.jwt() app_metadata role). Its INSERT path is broken in production -- "
               "anon has no grant at all and no INSERT policy exists. See DEPLOY-05.",
    },
}

#: Tables where `anon` legitimately writes. `download_analytics` INSERT happens on the
#: public download page before sign-in (src/lib/downloadAnalyticsApi.js:11). The table does
#: not exist in production yet (DEPLOY-05); when it is created it needs anon INSERT only.
ANON_WRITE_ALLOWLIST: dict[str, set[str]] = {
    "download_analytics": {"INSERT"},
}

# ─────────────────────────────────────────────────────────────────────────────────
# WHY INVARIANT 3 IS ADVISORY AND NOT BLOCKING.
#
# `authenticated` still holds writes on roughly 45 tables that no path-(A) code appears to
# touch. Removing them is correct least-privilege work. It is NOT done, and this check does
# not fail on it, for a measured reason:
#
# Deriving the path-(A) inventory from source proved unreliable three times in a row. The
# routers wrap the two client factories in local helpers (`_sb`, `_get_supabase`, `_client`),
# so the call sites do not grep as factory names. Two separate full passes over the backend
# produced a list that was missing `strategies` DELETE (backend/strategy_service.py:2502 --
# strategy deletion), `strategy_backtests` (backend/backtest_service.py:776),
# `training_jobs` and `training_governance_decisions` (backend/training_status.py:573) and
# `model_versions` (backend/model_versioning.py:1963). Each omission, had it driven a
# REVOKE, would have taken a working production feature offline.
#
# So the asymmetry is deliberate: a MISSING privilege that path-(A) code needs is a broken
# feature and fails this check; an EXTRA privilege is a defence-in-depth gap and is
# reported as a backlog. The extra grants are not an open door -- every one of these tables
# has RLS enabled with per-user policies, and `anon` now holds no write at all, so reaching
# them requires a valid signed-in JWT and confines the caller to their own rows.
#
# To drain the backlog: revoke ONE table, run the feature's integration test, keep it.
# Then pass --strict here so the drained table can never regress. Do not sweep.
# ─────────────────────────────────────────────────────────────────────────────────

#: Platform-owned tables the backend must keep writing as service_role.
SERVICE_ROLE_MUST_WRITE = (
    "profiles", "orders", "execution_records", "fee_records",
    "referral_wallets", "referral_commissions", "billing_invoices", "subscriptions",
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


def main() -> int:  # noqa: C901 - a checklist; splitting it would hide the invariants
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--verbose", action="store_true", help="also print the clean state")
    parser.add_argument(
        "--strict", action="store_true",
        help="also fail on the least-privilege backlog (extra `authenticated` write "
             "grants beyond known path-(A) needs). Turn this on once the backlog is "
             "drained table by table; see WHY INVARIANT 3 IS ADVISORY, below.",
    )
    args = parser.parse_args()

    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is required", file=sys.stderr)
        return 2

    conn = psycopg2.connect(read_dsn(), connect_timeout=30)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1
    """)
    tables = [r[0] for r in cur.fetchall()]

    cur.execute("""
        SELECT table_name, grantee, privilege_type
          FROM information_schema.role_table_grants
         WHERE table_schema = 'public'
           AND grantee IN ('anon', 'authenticated', 'service_role')
    """)
    grants: dict[tuple[str, str], set[str]] = {}
    for t, g, p in cur.fetchall():
        grants.setdefault((t, g), set()).add(p)

    findings: list[dict] = []
    #: Real gaps that this role cannot close. Reported, never silently dropped, but they
    #: do not fail the check -- a permanently red check is a check nobody reads.
    unfixable: list[str] = []

    def flag(invariant, detail):
        findings.append({"invariant": invariant, "detail": detail})

    # INVARIANT 1 -- anon holds no write anywhere in public, bar the documented exception.
    for t in tables:
        held = grants.get((t, "anon"), set()) & set(WRITE_PRIVS)
        allowed = ANON_WRITE_ALLOWLIST.get(t, set())
        for priv in sorted(held - allowed):
            flag(1, f"anon holds {priv} on public.{t}")

    # INVARIANT 2 -- no browser role holds TRUNCATE / REFERENCES / TRIGGER. No exceptions.
    for t in tables:
        for role in BROWSER_ROLES:
            for priv in sorted(grants.get((t, role), set()) & set(NEVER_PRIVS)):
                flag(2, f"{role} holds {priv} on public.{t} "
                        f"({'RLS cannot filter TRUNCATE' if priv == 'TRUNCATE' else 'schema-modification right'})")

    # INVARIANT 3 -- authenticated writes only where path-(A) code needs them.
    # Asymmetric on purpose; see WHY INVARIANT 3 IS ADVISORY.
    backlog: list[str] = []
    for t in tables:
        held = grants.get((t, "authenticated"), set()) & set(WRITE_PRIVS)
        entry = PATH_A_ALLOWLIST.get(t)
        allowed = set(entry.get("authenticated", set())) if entry else set()

        # A privilege path-(A) code REQUIRES but does not have => a broken feature. Blocks.
        for priv in sorted(allowed - held):
            flag(3, f"authenticated LACKS {priv} on public.{t}, which path-(A) code "
                    f"requires -- feature broken. {entry.get('why', '')}")

        # A privilege beyond what the code needs => least-privilege backlog. Advisory,
        # unless --strict.
        for priv in sorted(held - allowed):
            msg = f"authenticated holds {priv} on public.{t} beyond any known path-(A) need"
            if args.strict:
                flag(3, msg)
            else:
                backlog.append(msg)

    # INVARIANT 4 -- profiles UPDATE is conditional on the column-guard trigger.
    for t, entry in PATH_A_ALLOWLIST.items():
        trig = entry.get("requires_trigger")
        if not trig or t not in tables:
            continue
        cur.execute("""
            SELECT tgenabled FROM pg_trigger
             WHERE tgrelid = %s::regclass AND tgname = %s AND NOT tgisinternal
        """, (f"public.{t}", trig))
        row = cur.fetchone()
        has_write = bool(grants.get((t, "authenticated"), set()) & set(WRITE_PRIVS))
        if row is None:
            if has_write:
                flag(4, f"authenticated can write public.{t} but the column-guard trigger "
                        f"{trig} is ABSENT -- this is SEC-07 reopened")
        elif row[0] == "D":
            flag(4, f"column-guard trigger {trig} on public.{t} is DISABLED")
        elif args.verbose:
            print(f"   trigger {trig} on public.{t}: enabled ({row[0]})")

    # INVARIANT 5 -- the default ACL must not re-grant browser writes on new tables.
    cur.execute("""
        SELECT pg_get_userbyid(d.defaclrole), d.defaclacl::text
          FROM pg_default_acl d JOIN pg_namespace n ON n.oid = d.defaclnamespace
         WHERE n.nspname = 'public' AND d.defaclobjtype = 'r'
    """)
    for grantor, acl in cur.fetchall():
        for entry_txt in (acl or "").strip("{}").split(","):
            if "=" not in entry_txt:
                continue
            role = entry_txt.split("=")[0].strip('"')
            if role not in BROWSER_ROLES:
                continue
            privs = entry_txt.split("=")[1].split("/")[0]
            # a=INSERT w=UPDATE d=DELETE D=TRUNCATE
            if set("awdD") & set(privs):
                detail = (f"default privileges for grantor {grantor} grant {role} "
                          f"'{privs}' on NEW tables in public -- every table created "
                          f"from now on reopens the hole")
                if grantor == "supabase_admin":
                    # Cannot be fixed from this role, so it must not permanently redden
                    # the check -- that is how SEC-07 came to be ignored. Verified by
                    # dry-run: `ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin` is
                    # refused because the postgres role is not a member of it
                    # (supabase_admin is a superuser). It needs the Supabase dashboard or
                    # support. Scope is Supabase-managed objects; application tables are
                    # created by migrations running as postgres, which IS pinned above.
                    unfixable.append(
                        f"grantor supabase_admin still grants {role} '{privs}' on new "
                        f"tables in public -- needs Supabase dashboard/support, not "
                        f"reachable from the postgres role"
                    )
                else:
                    flag(5, detail)

    # INVARIANT 6 -- service_role keeps what the backend needs.
    for t in SERVICE_ROLE_MUST_WRITE:
        if t not in tables:
            continue
        for priv in ("SELECT", "INSERT", "UPDATE"):
            if priv not in grants.get((t, "service_role"), set()):
                flag(6, f"service_role LACKS {priv} on public.{t} -- backend write path "
                        f"broken")

    conn.close()

    if args.json:
        print(json.dumps({
            "tables_checked": len(tables),
            "violations": findings,
            "least_privilege_backlog": backlog,
            "not_fixable_from_this_role": unfixable,
            "ok": not findings,
        }, indent=2))
        return 1 if findings else 0

    print(f"checked {len(tables)} table(s) in schema public")
    if not findings:
        print("\nOK: the blocking invariants hold.")
        print("    anon            no write privilege anywhere in public")
        print("    neither role    holds TRUNCATE, REFERENCES or TRIGGER")
        print("    profiles        UPDATE is column-scoped by the guard trigger")
        print("    new tables      inherit SELECT only (default ACL, grantor postgres)")
        print("    path-(A) code   retains every write privilege it needs")
        print("    service_role    retains the backend write paths")
        if backlog:
            print(f"\nADVISORY -- least-privilege backlog: {len(backlog)} extra "
                  f"`authenticated` write grant(s) on "
                  f"{len({b.split('public.')[1].split(' ')[0] for b in backlog})} "
                  f"table(s) beyond any known path-(A) need.")
            print("    Not a blocking failure: those tables all have RLS with per-user "
                  "policies,\n    and anon can no longer write at all, so reaching them "
                  "needs a valid\n    signed-in JWT and confines the caller to their own "
                  "rows.")
            print("    Drain one table at a time, each with its feature test; then "
                  "run with --strict.")
            if args.verbose:
                for b in backlog:
                    print(f"       {b}")
            else:
                print("    Re-run with --verbose to list them.")
        if unfixable:
            print(f"\nNOT FIXABLE FROM THIS ROLE -- {len(unfixable)} item(s):")
            for u in unfixable:
                print(f"    {u}")
        return 0

    by_inv: dict[int, list[str]] = {}
    for f in findings:
        by_inv.setdefault(f["invariant"], []).append(f["detail"])
    titles = {
        1: "anon must hold no write privilege in schema public",
        2: "no browser role may hold TRUNCATE, REFERENCES or TRIGGER",
        3: "authenticated may write only path-(A) tables",
        4: "profiles UPDATE requires the column-guard trigger",
        5: "default privileges must not grant browser writes on new tables",
        6: "service_role must retain the backend's write paths",
    }
    print(f"\n{len(findings)} VIOLATION(S):")
    for inv in sorted(by_inv):
        print(f"\n  INVARIANT {inv} -- {titles[inv]}")
        for d in by_inv[inv]:
            print(f"     {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
