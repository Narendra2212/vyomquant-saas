#!/usr/bin/env python3
"""Reconcile duplicate live strategy names so STRAT-01's unique index can be built.

WHY THIS IS A SCRIPT AND NOT A MIGRATION
----------------------------------------
`021_strategy_deployment_live_uniqueness.sql` states the rule this repository
follows: "a unique index that cannot be built is not a migration, and
reconciling live duplicate deployments is an operational decision, not a
schema one."  Archiving rows of customer data is an operational act.  It needs
a dry run, a named keeper per group, a human reading the report, and a printed
inverse for every row touched.  A migration file gives you none of those.

WHAT IT DOES
------------
For every (user_id, lower(name)) group among live strategies that holds more
than one row, it picks exactly one KEEPER and sets `archived_at = now()` on
the others.  `archived_at` is this schema's soft delete, already used by
`idx_strategies_archived_at` and by 005a_strategy_archive.sql, so the rows
remain fully readable and the action is reversible with a single UPDATE.
Nothing is deleted.  No other column is touched.

KEEPER SELECTION, IN ORDER
--------------------------
1. The row with the most dependent rows across the six tables that reference
   public.strategies.  A strategy carrying deployments and signals is in use;
   archiving it would orphan live execution history.
2. On a tie, the earliest `created_at` — the original, not the retry.
3. On a further tie, the lowest id, purely so runs are deterministic.

HARD REFUSALS
-------------
* Any row selected for archiving that has ANY dependent row aborts the whole
  operation.  The expectation is that duplicates from a double submit are
  inert; if one is not, this script is the wrong tool and a human should look.
* A non-local DSN requires --i-understand-this-is-remote.
* Port 6543 (the Supabase transaction pooler) is refused: this needs a session.
* --apply is mandatory.  The default is a report.

USAGE
-----
    python scripts/reconcile_duplicate_strategy_names.py --dsn "$DSN"
    python scripts/reconcile_duplicate_strategy_names.py --dsn "$DSN" --apply

Afterwards apply backend_app/migrations/068_strat01_strategy_name_uniqueness.sql,
whose own preflight refuses if any collision remains.
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

try:
    import psycopg2
except ImportError:  # pragma: no cover
    sys.stderr.write("psycopg2 is required: pip install psycopg2-binary\n")
    raise SystemExit(1)


# The six tables that reference public.strategies, measured from
# information_schema against production on 2026-11-16.
REFERENCING = (
    ("strategy_versions", "strategy_id"),
    ("strategy_deployments", "strategy_id"),
    ("signals", "strategy_id"),
    ("model_versions", "strategy_id"),
    ("training_jobs", "strategy_id"),
    ("training_governance_decisions", "strategy_id"),
)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def validate_dsn(dsn: str, allow_remote: bool) -> None:
    u = urlparse(dsn)
    if u.port == 6543:
        raise SystemExit(
            "REFUSED: port 6543 is the Supabase TRANSACTION pooler. This script "
            "needs a session (it runs a multi-statement transaction). Use the "
            "session pooler on 5432, or a direct connection."
        )
    host = (u.hostname or "").lower()
    if host not in LOCAL_HOSTS and not allow_remote:
        raise SystemExit(
            f"REFUSED: {host!r} is not local. Re-run with "
            "--i-understand-this-is-remote if you really mean to reconcile "
            "customer data on a remote database."
        )


def dependents(cur, sid) -> dict[str, int]:
    out: dict[str, int] = {}
    for table, col in REFERENCING:
        cur.execute(
            "select count(*) from information_schema.tables "
            "where table_schema='public' and table_name=%s",
            (table,),
        )
        if not cur.fetchone()[0]:
            continue  # table absent in this deployment; schema lineages differ
        cur.execute(
            f'select count(*) from public."{table}" where "{col}" = %s::uuid',
            (str(sid),),
        )
        n = cur.fetchone()[0]
        if n:
            out[table] = n
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("PROVISION_DATABASE_URL")
                    or os.environ.get("DATABASE_URL"),
                    help="PostgreSQL DSN. Defaults to PROVISION_DATABASE_URL "
                         "then DATABASE_URL.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually archive. Without this, reports only.")
    ap.add_argument("--i-understand-this-is-remote", action="store_true",
                    dest="allow_remote",
                    help="Permit a non-local DSN.")
    args = ap.parse_args()

    if not args.dsn:
        raise SystemExit("No DSN. Pass --dsn or set PROVISION_DATABASE_URL.")
    validate_dsn(args.dsn, args.allow_remote)

    conn = psycopg2.connect(args.dsn, connect_timeout=25)
    conn.autocommit = False
    try:
        cur = conn.cursor()

        cur.execute("select count(*) from public.strategies")
        total = cur.fetchone()[0]
        cur.execute("select count(*) from public.strategies "
                    "where archived_at is null")
        live = cur.fetchone()[0]

        print("=" * 78)
        print("STRAT-01 DUPLICATE STRATEGY NAME RECONCILIATION")
        print("=" * 78)
        print(f"  mode            : {'APPLY' if args.apply else 'DRY RUN (report only)'}")
        print(f"  strategies      : {total} total, {live} live")

        cur.execute("""
            select user_id, lower(name) as lname, count(*) as n
              from public.strategies
             where archived_at is null
             group by user_id, lower(name)
            having count(*) > 1
             order by count(*) desc, 1, 2
        """)
        groups = cur.fetchall()
        print(f"  colliding groups: {len(groups)}")

        if not groups:
            print("\n  Nothing to do. Migration 068 can be applied.")
            conn.rollback()
            return 0

        to_archive: list[tuple] = []
        blocked: list[tuple] = []

        for user_id, lname, n in groups:
            cur.execute("""
                select id, name, status, current_version, created_at
                  from public.strategies
                 where archived_at is null and user_id = %s and lower(name) = %s
                 order by created_at, id
            """, (user_id, lname))
            rows = cur.fetchall()

            enriched = []
            for sid, name, status, ver, created in rows:
                deps = dependents(cur, sid)
                enriched.append({
                    "id": sid, "name": name, "status": status,
                    "version": ver, "created": created,
                    "deps": deps, "dep_total": sum(deps.values()),
                })

            # Keeper: most dependents, then earliest, then lowest id.
            keeper = sorted(
                enriched,
                key=lambda r: (-r["dep_total"], r["created"], str(r["id"])),
            )[0]

            print()
            print("-" * 78)
            print(f"  owner {str(user_id)[:8]}  name(len={len(lname)})  {n} live rows")
            print("-" * 78)
            for r in enriched:
                mark = "KEEP   " if r["id"] == keeper["id"] else "ARCHIVE"
                dep = ", ".join(f"{k}={v}" for k, v in r["deps"].items()) or "none"
                print(f"    {mark} id={str(r['id'])[:8]} created={r['created']} "
                      f"status={r['status']} ver={r['version']}")
                print(f"            dependents: {dep}")
                if r["id"] != keeper["id"]:
                    if r["dep_total"]:
                        blocked.append((user_id, r))
                    else:
                        to_archive.append((user_id, r))

            why = ("has the most dependent rows" if keeper["dep_total"]
                   else "is the earliest of an inert set")
            print(f"    keeper rationale: {str(keeper['id'])[:8]} {why}")

        print()
        print("=" * 78)
        print("PLAN")
        print("=" * 78)
        print(f"  rows to archive : {len(to_archive)}")
        print(f"  rows blocked    : {len(blocked)}")

        if blocked:
            print()
            print("  REFUSING. These rows are duplicates but carry dependent data,")
            print("  so archiving them would orphan live history. A human must")
            print("  decide what to do with each:")
            for uid, r in blocked:
                dep = ", ".join(f"{k}={v}" for k, v in r["deps"].items())
                print(f"    owner {str(uid)[:8]} id={r['id']} -> {dep}")
            conn.rollback()
            return 3

        if not args.apply:
            print()
            print("  DRY RUN. Re-run with --apply to archive the rows above.")
            print("  Every archive is reversible with the printed inverse.")
            conn.rollback()
            return 0

        print()
        print("=" * 78)
        print("APPLYING")
        print("=" * 78)
        ids = [str(r["id"]) for _, r in to_archive]
        cur.execute("""
            update public.strategies
               set archived_at = now()
             where id = any(%s::uuid[]) and archived_at is null
            returning id, archived_at
        """, (ids,))
        done = cur.fetchall()
        for sid, at in done:
            print(f"  archived {sid} at {at}")
            print(f"     inverse: UPDATE public.strategies SET archived_at = NULL "
                  f"WHERE id = '{sid}';")

        if len(done) != len(to_archive):
            conn.rollback()
            raise SystemExit(
                f"REFUSED: expected to archive {len(to_archive)} rows but "
                f"matched {len(done)}. Rolled back; nothing changed."
            )

        # Prove the collision is gone before committing.
        cur.execute("""
            select count(*) from (
                select 1 from public.strategies
                 where archived_at is null
                 group by user_id, lower(name) having count(*) > 1
            ) t
        """)
        remaining = cur.fetchone()[0]
        if remaining:
            conn.rollback()
            raise SystemExit(
                f"REFUSED: {remaining} collision group(s) still remain after the "
                "planned archive. Rolled back; nothing changed."
            )

        conn.commit()
        print()
        print(f"  COMMITTED. {len(done)} row(s) archived, 0 collisions remain.")
        print("  Next: apply backend_app/migrations/"
              "068_strat01_strategy_name_uniqueness.sql")
        return 0

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
