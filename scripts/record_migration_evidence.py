"""DEPLOY-05: backfill public.recorded_migrations from EVIDENCE, by probing.

WHAT THIS IS FOR
----------------
DEPLOY-05's remaining action: "backfill from EVIDENCE by probing for each migration's
declared objects, which scripts/check_production_schema_drift.py now does. Its inventory
is hand-maintained by design: a half-working SQL parser that silently missed a
declaration would restore exactly the false confidence this replaces. Extend that
inventory as migrations are confirmed, and it becomes the ledger."

So this does not parse SQL and does not read the migrations directory. It groups
``check_production_schema_drift.DECLARED_OBJECTS`` by declaring migration, probes each
object in the live database, and writes one row per migration whose objects are ALL
present.

WHAT IT REFUSES TO DO, AND WHY EACH REFUSAL MATTERS
---------------------------------------------------
* It never records a migration from the FILE LIST. The deployed schema is not a prefix of
  the migration sequence -- DEPLOY-05 records 037 applied while 017 was not -- so
  "the files up to N exist, therefore N is applied" is exactly the inference that made
  DEPLOY-04's close-out claim unverifiable.
* It never records a migration whose objects are only PARTIALLY present. A half-applied
  migration is the most dangerous state to mark as applied, so it is reported and skipped.
* It never writes a row with no evidence. The table's own CHECK constraints enforce that
  too, but failing here gives a readable reason instead of a 23514.
* It writes ``EVIDENCE_PROBED``, never ``APPLIED_BY_TOOL``. This script did not apply
  anything; it looked. Only a deploy process that ran a file may claim the stronger value.

READ-ONLY BY DEFAULT. ``--apply`` is required to write. ``--dsn`` overrides the secret.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.check_production_schema_drift import (  # noqa: E402
    DECLARED_OBJECTS, REGION, SECRET_ID)

LEDGER_TABLE = "public.recorded_migrations"
VERIFICATION_PROBED = "EVIDENCE_PROBED"


def read_dsn() -> str:
    override = os.environ.get("PROD_DSN")
    if override:
        return override
    out = subprocess.run(
        ["aws", "secretsmanager", "get-secret-value", "--secret-id", SECRET_ID,
         "--region", REGION, "--query", "SecretString", "--output", "text"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise SystemExit(f"could not read {SECRET_ID}: {out.stderr.strip()}")
    return out.stdout.strip()


def probe(cur, kind: str, identifier: str) -> bool:
    """Is this declared object present? One query per object, by kind."""
    if kind == "table":
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{identifier}",))
    elif kind == "column":
        table, _, column = identifier.partition(".")
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name=%s)",
            (table, column),
        )
    elif kind == "constraint":
        table, _, conname = identifier.partition("/")
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE t.relname=%s AND c.conname=%s)",
            (table, conname),
        )
    else:
        raise SystemExit(f"unknown declared-object kind {kind!r}")
    return bool(cur.fetchone()[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write the rows. Without it, this only reports.")
    ap.add_argument("--recorded-by", default="",
                    help="operator identity stored on each row (required with --apply)")
    ap.add_argument("--dsn", default="")
    args = ap.parse_args()

    if args.apply and not args.recorded_by.strip():
        raise SystemExit(
            "--recorded-by is required with --apply: a ledger row whose provenance is "
            "blank is the kind of record DEPLOY-05 exists to stop"
        )

    import psycopg2

    dsn = args.dsn or read_dsn()
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute(f"SELECT to_regclass('{LEDGER_TABLE}') IS NOT NULL")
    if not cur.fetchone()[0]:
        print(f"{LEDGER_TABLE} does not exist. Apply "
              f"backend_app/migrations/055_deploy05_migration_ledger.sql first.")
        return 2

    by_migration = collections.defaultdict(list)
    for kind, identifier, migration, _note in DECLARED_OBJECTS:
        by_migration[migration].append((kind, identifier))

    print("=" * 78)
    print("DEPLOY-05: probing each migration's declared objects")
    print("=" * 78)
    print(f"inventory covers {len(by_migration)} migration(s), "
          f"{len(DECLARED_OBJECTS)} declared object(s)\n")

    recordable, partial = [], []
    for migration, objects in sorted(by_migration.items()):
        present, missing = [], []
        for kind, identifier in objects:
            (present if probe(cur, kind, identifier) else missing).append(
                f"{kind}:{identifier}"
            )
        status = "COMPLETE" if not missing else "PARTIAL"
        print(f"  {migration}")
        print(f"      {status}: {len(present)}/{len(objects)} declared object(s) present")
        for item in missing:
            print(f"      MISSING {item}")
        if missing:
            partial.append((migration, present, missing))
        else:
            recordable.append((migration, present))

    print()
    if partial:
        print("NOT RECORDABLE -- partially applied, which is the most dangerous state to")
        print("mark as applied. Reported and skipped:")
        for migration, present, missing in partial:
            print(f"   {migration}: {len(present)} present, {len(missing)} missing")
        print()

    if not args.apply:
        print(f"DRY RUN. {len(recordable)} migration(s) would be recorded as "
              f"{VERIFICATION_PROBED}.")
        print("Re-run with --apply --recorded-by '<you>' to write them.")
        cur.close()
        conn.close()
        return 0

    written = 0
    for migration, present in recordable:
        evidence = (
            f"Probed {len(present)} declared object(s) in this database and found all "
            f"present: {', '.join(sorted(present))}. Objects come from "
            f"scripts/check_production_schema_drift.py::DECLARED_OBJECTS, which is "
            f"hand-maintained; this row therefore attests ONLY to those objects, not to "
            f"everything the file declares."
        )
        cur.execute(
            f"INSERT INTO {LEDGER_TABLE} "
            "(filename, verification, evidence, objects_verified, recorded_by) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (filename) DO UPDATE SET "
            "  verification = EXCLUDED.verification, "
            "  evidence = EXCLUDED.evidence, "
            "  objects_verified = EXCLUDED.objects_verified, "
            "  recorded_by = EXCLUDED.recorded_by, "
            "  recorded_at = NOW()",
            (migration, VERIFICATION_PROBED, evidence, len(present),
             args.recorded_by.strip()),
        )
        written += 1
        print(f"   recorded {migration} ({len(present)} object(s))")

    conn.commit()

    cur.execute(f"SELECT count(*) FROM {LEDGER_TABLE}")
    total = cur.fetchone()[0]
    print(f"\n{written} row(s) written. {LEDGER_TABLE} now holds {total} row(s).")
    print("A migration with NO row means nobody has checked it -- not that it is "
          "unapplied.")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
