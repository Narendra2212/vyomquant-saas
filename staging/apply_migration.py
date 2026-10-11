"""Apply ONE migration file to the local verification database, with the same bookkeeping
`scripts/provision_database.py` uses.

WHY NOT JUST RUN provision_database.py. It would re-run `alembic upgrade head`, the
substrate and the hardening file, which takes many minutes and re-grants the Supabase
defaults. For verifying a single new numbered file that is unnecessary. This reproduces
`_apply_sql`'s exact semantics for one file:

  * SHA-256 the contents and record them in `public.schema_migrations`;
  * skip if already applied with the same checksum;
  * REFUSE if applied previously with a different checksum;
  * run the file in its own transaction with its own BEGIN/COMMIT left intact;
  * abort on the first failure, naming the driver's error.

REFUSES A NON-LOCAL DSN, for the same reason provision_database.py does: this executes DDL.

Usage:  python staging/apply_migration.py backend_app/migrations/026_...sql [step]

`step` is the label recorded in `schema_migrations.step` and defaults to the phase this
script was last used in. Pass it explicitly so a later phase's migrations are
attributable: `python staging/apply_migration.py .../028_....sql phase9`.
"""

import hashlib
import os
import pathlib
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2

TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename     text        PRIMARY KEY,
    sha256       text        NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now(),
    step         text        NOT NULL
);
"""


def refuse(message: str) -> None:
    print(f"REFUSED: {message}")
    raise SystemExit(2)


def main() -> int:
    if len(sys.argv) < 2:
        refuse("no migration path given")

    relative = sys.argv[1].replace("\\", "/")
    path = ROOT / relative
    if not path.is_file():
        refuse(f"{relative} does not exist")

    step = sys.argv[2] if len(sys.argv) > 2 else "phase8"

    dsn = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
    parsed = urllib.parse.urlparse(dsn)
    if (parsed.hostname or "") not in ("127.0.0.1", "localhost", "::1", ""):
        refuse("DSN host is not local; this script applies DDL and will not target a "
               "remote database")

    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # provision_database.py records `f"{path.parent.name}/{path.name}"`.
    name = f"{path.parent.name}/{path.name}"

    print(f"FILE={name}")
    print(f"SHA256={digest}")
    print(f"BYTES={len(text)}")
    print(f"STEP={step}")

    conn = psycopg2.connect(dsn, connect_timeout=10)
    conn.autocommit = False

    with conn.cursor() as cur:
        cur.execute(TRACKING_DDL)
        conn.commit()

        cur.execute(
            "SELECT sha256 FROM public.schema_migrations WHERE filename = %s", (name,)
        )
        row = cur.fetchone()
        if row:
            if row[0] != digest:
                print("REFUSED: applied previously with a DIFFERENT checksum. A migration "
                      "file changed after being applied; resolve by hand.")
                return 4
            print("STATUS=skipped (already applied, checksum matches)")
            conn.close()
            return 0

    with conn.cursor() as cur:
        try:
            cur.execute(text)
            # Surface the file's own RAISE NOTICE output, which is where its postflight
            # reports what it verified.
            for notice in conn.notices:
                print("  " + notice.rstrip())
            cur.execute(
                "INSERT INTO public.schema_migrations (filename, sha256, step) "
                "VALUES (%s, %s, %s) ON CONFLICT (filename) DO NOTHING",
                (name, digest, step),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"FAILED: {type(exc).__name__}: {exc}")
            for notice in conn.notices:
                print("  " + notice.rstrip())
            conn.close()
            return 4

    print("STATUS=applied")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
