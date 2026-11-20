#!/usr/bin/env python3
"""Apply one migration file to the production database, with a trial mode that rolls back.

WHY THIS EXISTS
---------------
DEPLOY-05. Migrations in this repository have been applied to production out of order and
out of band: measurement showed `017_plan_entitlements.sql` had never run (all four of its
objects absent) while `037`'s columns ARE present. There is no migration ledger -- nothing
declares `recorded_migrations` anywhere, `public.alembic_version` holds a single revision
pointer, and `supabase_migrations.schema_migrations` holds one row against 50+ files. So
"which migrations are applied" has only ever been answerable by probing for their objects.

Applying one by hand is where that goes wrong. The lesson taken from migration 033 -- which
was written, tested in CI, and never applied, while SEC-07 sat recorded as fixed -- is that
the dangerous step is not writing the SQL, it is applying it to a schema that may not match
what the file assumes. A migration that fails half-way through DDL is the worst outcome.

WHAT THIS GIVES THAT `psql -f` DOES NOT
---------------------------------------
``--trial`` executes the ENTIRE migration, including its own postcondition blocks, and then
ROLLS BACK. A trial that reaches "postconditions passed" and rolls back proves the file runs
against the real schema with the real role's real permissions, before anything is committed.
That is how `017` was confirmed safe: its `plan_usage_ledger.user_id REFERENCES
auth.users(id)` needed REFERENCES on a Supabase-managed table, which was not obviously
granted to this role.

It also enforces one invariant the migration files cannot enforce for themselves: no browser
role may gain a write privilege anywhere in `public`. SEC-20 established that Supabase's
stock default ACL granted every privilege on every NEW table to `anon` and `authenticated`,
which is how SEC-07 and SEC-19 happened. That default is now revoked, but a migration can
still `GRANT` explicitly, so every apply re-checks the whole schema and refuses to commit if
the surface grew.

USAGE
-----
    python scripts/apply_migration_to_production.py --migration backend_app/migrations/XXX.sql --trial
    python scripts/apply_migration_to_production.py --migration backend_app/migrations/XXX.sql --apply

Reads the DSN from AWS Secrets Manager and NEVER prints it. The migration must be
idempotent and should carry its own postconditions; this tool verifies the privilege
invariant and surfaces NOTICEs, it does not substitute for the file proving its own outcome.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from urllib.parse import unquote, urlparse

SECRET_ID = "vyomquant/production/database_url"
REGION = "ap-southeast-1"

#: IPv4-reachable route to the same database. :5432 is the SESSION pooler (dedicated
#: backend, DDL-capable); :6543 is the TRANSACTION pooler and cannot hold the
#: multi-statement transaction that --trial rolls back. Do not change this to 6543.
POOLER_HOST = "aws-1-ap-southeast-1.pooler.supabase.com"
POOLER_DDL_PORT = 5432

BROWSER_ROLES = ("anon", "authenticated")
WRITE_PRIVS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")


def _extract_dsn(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("{"):
        blob = json.loads(raw)
        for key in ("DATABASE_URL", "database_url", "url", "dsn"):
            if key in blob:
                return blob[key]
        return next(iter(blob.values()))
    return raw


def read_dsn() -> str:
    """The production DSN, from Secrets Manager, via boto3 or the AWS CLI.

    boto3 is tried FIRST and the CLI is the fallback, which is the opposite of what this
    function originally did. The CLI is not installed on every operator host -- it was
    removed from this one after a broken extract on PATH hung every invocation -- and a
    tool that refuses to read its own credential is a tool nobody can use in the incident
    it exists for. boto3 is already a hard dependency of this repository's AWS code.

    Never returns the DSN to a log, a shell, or an argv: the caller hands it straight to
    psycopg2.connect.
    """
    try:
        import boto3  # noqa: PLC0415 - optional at import time, required only here
    except ImportError:
        boto3 = None

    if boto3 is not None:
        try:
            client = boto3.session.Session(region_name=REGION).client("secretsmanager")
            return _extract_dsn(client.get_secret_value(SecretId=SECRET_ID)["SecretString"])
        except Exception as exc:  # noqa: BLE001 - fall through to the CLI and report both
            boto3_error = f"{type(exc).__name__}: {str(exc)[:160]}"
    else:
        boto3_error = "boto3 is not installed"

    try:
        out = subprocess.run(
            ["aws", "secretsmanager", "get-secret-value", "--secret-id", SECRET_ID,
             "--region", REGION, "--query", "SecretString", "--output", "text"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        print(f"could not read {SECRET_ID}.\n"
              f"   boto3: {boto3_error}\n"
              f"   aws CLI: not on PATH", file=sys.stderr)
        sys.exit(2)
    if out.returncode != 0:
        print(f"could not read {SECRET_ID}.\n"
              f"   boto3: {boto3_error}\n"
              f"   aws CLI: {out.stderr.strip()[:160]}", file=sys.stderr)
        sys.exit(2)
    return _extract_dsn(out.stdout)


def connect(dsn: str, psycopg2):
    """Connect to production, falling back to the session pooler if the direct host is
    unroutable.

    ``db.<project>.supabase.co`` is **IPv6-only**. From an operator host with no IPv6
    route it is unreachable no matter how correct the credential is, and the failure looks
    like a timeout rather than a network-family problem, which is how an afternoon gets
    lost. The session pooler resolves on IPv4 and is DDL-capable on **:5432** -- unlike the
    transaction pooler on :6543, which cannot run multi-statement transactions and would
    therefore defeat --trial's rollback.

    The direct endpoint is still tried first, so on a host with IPv6 nothing changes.
    """
    try:
        return psycopg2.connect(dsn, connect_timeout=30), "direct"
    except psycopg2.OperationalError as exc:
        text = str(exc)
        unroutable = any(s in text for s in (
            "Network is unreachable", "could not translate host name",
            "timeout expired", "Cannot assign requested address",
            "No route to host", "connection timed out",
        ))
        if not unroutable:
            raise

        parsed = urlparse(dsn)
        user = unquote(parsed.username or "")
        host = parsed.hostname or ""
        if "." not in user and host.startswith("db."):
            # the pooler requires a tenant-qualified user: postgres.<project-ref>
            user = f"{user}.{host.split('.')[1]}"
        print(f"   direct host {host} is unroutable from here "
              f"({text.strip().splitlines()[0][:80]});\n"
              f"   retrying via the DDL-capable session pooler on :5432")
        return psycopg2.connect(
            host=POOLER_HOST, port=POOLER_DDL_PORT,
            dbname=(parsed.path or "/postgres").lstrip("/") or "postgres",
            user=user, password=unquote(parsed.password or ""),
            sslmode="require", connect_timeout=30,
        ), "session pooler"


def migration_body(path: pathlib.Path) -> str:
    """The migration with its own transaction-control statements removed.

    Only a bare ``BEGIN;`` / ``COMMIT;`` alone on a line is stripped, so the ``BEGIN`` that
    opens a ``DO $$ ... END $$`` block -- PL/pgSQL, not transaction control -- is untouched.
    This tool owns the transaction boundary so that ``--trial`` can roll the whole thing
    back; without stripping, the file's own COMMIT would defeat that.
    """
    text = path.read_text(encoding="utf-8")
    body = re.sub(r"(?mi)^[ \t]*(BEGIN|COMMIT)[ \t]*;[ \t]*$", "", text)
    for survivor in ("BEGIN", "COMMIT"):
        if re.search(rf"(?mi)^[ \t]*{survivor}[ \t]*;[ \t]*$", body):
            print(f"refusing: a bare {survivor}; survived the strip, so --trial could not "
                  f"roll back", file=sys.stderr)
            sys.exit(2)
    return body


def browser_write_surface(cur) -> set[tuple[str, str, str]]:
    cur.execute("""
        SELECT table_name, grantee, privilege_type
          FROM information_schema.role_table_grants
         WHERE table_schema = 'public'
           AND grantee IN ('anon', 'authenticated')
           AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')
    """)
    return {(t, g, p) for t, g, p in cur.fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--migration", required=True, help="path to the .sql file")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--trial", action="store_true",
                      help="execute the whole migration, then ROLL BACK")
    mode.add_argument("--apply", action="store_true",
                      help="execute, verify the privilege invariant, then COMMIT")
    args = parser.parse_args()

    path = pathlib.Path(args.migration)
    if not path.exists():
        print(f"{path} not found (run from the repository root)", file=sys.stderr)
        return 2

    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is required", file=sys.stderr)
        return 2

    body = migration_body(path)
    conn, route = connect(read_dsn(), psycopg2)
    conn.autocommit = False
    cur = conn.cursor()

    label = "TRIAL (will roll back)" if args.trial else "APPLY (commits only if verified)"
    print(f"{label}: {path}  [{len(body.splitlines())} lines]  via {route}\n")

    before = browser_write_surface(cur)

    try:
        cur.execute(body)
    except Exception as exc:  # noqa: BLE001 - classifying the failure is the point
        conn.rollback()
        print(f"MIGRATION FAILED and was rolled back. Nothing changed.\n"
              f"   {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    for notice in conn.notices:
        print("   " + notice.strip())

    after = browser_write_surface(cur)
    gained = sorted(after - before)
    if gained:
        conn.rollback()
        print(f"\nROLLED BACK: the migration granted {len(gained)} new browser-role write "
              f"privilege(s). SEC-20/SEC-21 closed that surface; a migration must not "
              f"reopen it.", file=sys.stderr)
        for table, role, priv in gained:
            print(f"   {role} gained {priv} on public.{table}", file=sys.stderr)
        return 1
    # Report the DELTA, not just the absence of growth. The invariant this tool enforces
    # is one-directional -- it refuses growth -- so a migration whose whole purpose is to
    # REVOKE used to print "surface unchanged" and read like a no-op. 074 revoked three
    # grants and said "55 before and after". A shrink is the success signal for that class
    # of migration and has to be visible.
    removed = sorted(before - after)
    if removed:
        print(f"\n   browser-role write surface SHRANK: {len(before)} -> {len(after)} "
              f"grant(s), {len(removed)} revoked")
        for table, role, priv in removed:
            print(f"      {role} lost {priv} on public.{table}")
    else:
        print(f"\n   browser-role write surface unchanged "
              f"({len(before)} grant(s) before and after)")

    print()
    if args.trial:
        conn.rollback()
        print("TRIAL PASSED and was rolled back. Nothing was changed.")
        print("The file runs cleanly against the real schema with the real role, including "
              "its own postconditions. Re-run with --apply to commit.")
        conn.close()
        return 0

    conn.commit()
    print("COMMITTED.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
