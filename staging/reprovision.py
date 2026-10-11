"""Reset the working staging database and provision it from source.

Drops and recreates `public` and `auth`, then runs the provisioner. Used after a
migration edit so the verification database reflects the current repository state rather
than an accumulation of hand-applied changes.

Short-lived by design: long-running `execute_pwsh` commands have had their process tree
killed on timeout in this environment, which takes the PostgreSQL server with it.
"""

import pathlib
import subprocess
import sys

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

ROOT = pathlib.Path(__file__).resolve().parent.parent
DSN = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()

if "6543" in DSN or not ("127.0.0.1" in DSN or "localhost" in DSN):
    sys.exit(f"refusing to reset a non-local database: {DSN!r}")

conn = psycopg2.connect(DSN)
conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
with conn.cursor() as cur:
    cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
    cur.execute("DROP SCHEMA IF EXISTS auth CASCADE")
    cur.execute("CREATE SCHEMA public")
conn.close()
print("public and auth dropped and recreated")

proc = subprocess.run(
    [
        sys.executable, str(ROOT / "scripts" / "provision_database.py"),
        "--dsn", DSN, "--apply", "--create-substrate",
    ],
    cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
)
log = ROOT / "staging" / "reprovision.log"
log.write_text(proc.stdout + proc.stderr, encoding="utf-8")
failures = [l for l in (proc.stdout + proc.stderr).splitlines() if "FAILED" in l]
for line in failures[:10]:
    print(line.strip()[:170].encode("ascii", "replace").decode("ascii"))
print(f"provision exit={proc.returncode}  log={log}")
sys.exit(proc.returncode)
