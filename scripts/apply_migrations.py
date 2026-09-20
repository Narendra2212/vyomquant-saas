#!/usr/bin/env python
"""scripts/apply_migrations.py

Applies the ``006``-``014`` hand-applied migration set in
``backend_app/migrations/`` against a direct Postgres session, one file per
transaction, in dependency order.

This is deliberately the *only* thing this script does. It does not decide
whether a database is ready for a maintenance window, it does not rotate
credentials, and it does not touch ``migrations/`` (the older, unrelated
sequence) or any file outside ``backend_app/migrations/006`` through ``014``.

SAFETY MODEL
------------
  * ``--dry-run`` is the default. Nothing is executed against the database
    unless ``--apply`` is passed explicitly.
  * DSN comes from ``--dsn`` or ``MIGRATION_DATABASE_URL`` - never a
    hardcoded default. Absent either, refuses before any connection attempt.
  * Refuses a DSN on port 6543 (the Supabase pgbouncer transaction-mode
    pooler) for the same reason ``migration_preflight.py`` does: these files
    use multi-statement ``DO $$ ... $$`` blocks and explicit
    ``BEGIN``/``COMMIT``, which need a direct session on port 5432.
  * Runs ``migration_preflight``'s classification first and refuses to apply
    anything if any file is already PARTIALLY-APPLIED - applying more DDL on
    top of a half-run file is how a small drift becomes an unrecoverable one.
  * Refuses to apply ``010_signal_environment.sql`` unless
    ``--maintenance-window`` is passed. That file's ``ALTER TABLE ... ALTER
    COLUMN environment SET NOT NULL`` takes an ACCESS EXCLUSIVE lock on
    ``public.signals`` and scans the whole table to prove no row violates the
    constraint - on a large table that blocks every reader and writer for the
    scan's duration, so it does not belong in an unannounced run.
  * One file per transaction. ``BEGIN``/``COMMIT`` inside each ``.sql`` file
    are left exactly as written - this script never strips them to fit a
    pooler (a pooler is refused outright, see above) and never wraps a file
    in an extra transaction of its own; the file already opens and closes one.
  * Aborts the whole run on the first failure, naming the file and (where the
    driver provides it) the statement that failed.
  * Re-runs the preflight classification after every file, so drift is caught
    immediately rather than at the end of the run.
  * Every run - dry or real - writes a timestamped log under ``reports/``.
  * Never prints the DSN or any credential.

USAGE
-----
    # See what would run, without touching the database:
    python scripts/apply_migrations.py --dsn "postgresql://user:pass@host:5432/postgres"

    # Actually apply, outside 010's lock window:
    python scripts/apply_migrations.py --dsn ... --apply

    # Actually apply, including 010, inside an announced maintenance window:
    python scripts/apply_migrations.py --dsn ... --apply --maintenance-window

EXIT CODES
----------
    0   dry run completed, or apply run completed with every file applied
    2   refused before executing anything (no DSN, pooler port, partial
        state, 010 without --maintenance-window, connection failure)
    4   a migration file failed while applying (transaction rolled back
        automatically; earlier files in the run were already committed)
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path
from typing import List, Optional

import psycopg2

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.migration_preflight import (  # noqa: E402
    MIGRATION_FILES,
    PARTIALLY_APPLIED,
    RefusalError,
    classify_all,
    resolve_dsn,
    validate_dsn_port,
)

REPORTS_DIR = REPO_ROOT / "reports"

# The one file this run refuses without an explicit maintenance-window flag.
MAINTENANCE_WINDOW_REQUIRED_FILE = "010_signal_environment.sql"
MAINTENANCE_WINDOW_REASON = (
    "010_signal_environment.sql's ALTER TABLE public.signals ALTER COLUMN "
    "environment SET NOT NULL takes an ACCESS EXCLUSIVE lock on "
    "public.signals and scans the whole table to prove no row violates the "
    "constraint. On a table with production traffic that blocks every reader "
    "and writer for the scan's duration. Pass --maintenance-window to "
    "confirm this run is happening in an announced window."
)


class ApplyAbortedError(Exception):
    """Raised when a migration file fails; carries the file name and detail."""

    def __init__(self, filename: str, detail: str) -> None:
        super().__init__(f"{filename}: {detail}")
        self.filename = filename
        self.detail = detail


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Applies backend_app/migrations/006-014 in dependency order, one "
            "file per transaction. Dry-run by default."
        )
    )
    parser.add_argument("--dsn", default=None, help="Direct-session Postgres DSN (port 5432). Never printed.")
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually execute the migrations. Without this flag, nothing is run.",
    )
    parser.add_argument(
        "--maintenance-window",
        action="store_true",
        default=False,
        help=(
            "Required to apply 010_signal_environment.sql, whose SET NOT "
            "NULL takes an ACCESS EXCLUSIVE lock on public.signals."
        ),
    )
    return parser


def _log_path() -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPORTS_DIR / f"apply_migrations_{stamp}.log"


class RunLog:
    """Accumulates lines and writes them to a timestamped file under reports/.

    Never receives a DSN or credential - callers pass only file names,
    statuses and statement text.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lines: List[str] = []

    def write(self, line: str) -> None:
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        entry = f"[{stamp}] {line}"
        self._lines.append(entry)
        print(line)

    def flush(self) -> None:
        self.path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")


def refuse_if_partially_applied(cur, log: RunLog) -> None:
    statuses = classify_all(cur, MIGRATION_FILES)
    partial = [s for s in statuses if s.status == PARTIALLY_APPLIED]
    if partial:
        names = ", ".join(s.filename for s in partial)
        raise RefusalError(
            f"preflight found {len(partial)} PARTIALLY-APPLIED file(s) "
            f"({names}). Resolve the drift by hand before applying anything "
            "further."
        )
    log.write("Preflight: no partially-applied migration files found.")


def apply_one_file(conn, path: Path, log: RunLog) -> None:
    """Applies a single migration file inside its own transaction.

    The file's own BEGIN/COMMIT run as part of its SQL text - psycopg2 issues
    it verbatim over one cursor.execute call, and the driver-level
    transaction wrapping ``conn`` provides the abort-on-failure guarantee
    (``conn.rollback()``) if the file's own COMMIT is never reached.
    """
    sql_text = path.read_text(encoding="utf-8", errors="replace")
    log.write(f"Applying {path.name} ...")
    try:
        with conn.cursor() as cur:
            cur.execute(sql_text)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - re-raised as a named abort
        conn.rollback()
        detail = str(exc).strip()
        raise ApplyAbortedError(path.name, detail) from exc
    log.write(f"Applied {path.name}.")


def run(argv: Optional[List[str]] = None, connector=None) -> int:
    connector = connector or psycopg2.connect
    args = build_arg_parser().parse_args(argv)
    log = RunLog(_log_path())
    log.write(f"apply_migrations.py starting; apply={args.apply} maintenance_window={args.maintenance_window}")

    # Refuse to even build the plan without --apply if the only purpose of a
    # dry run were to prove the flag gating works; the plan is still printed
    # either way so a dry run is useful.
    files_to_run = MIGRATION_FILES

    if any(p.name == MAINTENANCE_WINDOW_REQUIRED_FILE for p in files_to_run) and not args.maintenance_window:
        # A dry run is allowed to show the plan even without the flag; only
        # an --apply run is refused outright, since a dry run performs no
        # lock and is safe to preview.
        if args.apply:
            log.write(f"REFUSED: {MAINTENANCE_WINDOW_REASON}")
            log.flush()
            return 2
        log.write(
            "NOTE: --apply run would refuse 010_signal_environment.sql "
            "without --maintenance-window: " + MAINTENANCE_WINDOW_REASON
        )

    try:
        dsn = resolve_dsn(args.dsn)
        validate_dsn_port(dsn)
    except RefusalError as exc:
        log.write(f"REFUSED: {exc}")
        log.flush()
        return 2

    if not args.apply:
        log.write("DRY RUN (default). The following files would be applied, in order:")
        for path in files_to_run:
            log.write(f"  - {path.name}")
        log.write("No connection was made and no statement was issued. Pass --apply to execute.")
        log.flush()
        return 0

    try:
        conn = connector(dsn)
    except Exception as exc:  # pragma: no cover - real network/auth failures
        log.write(f"REFUSED: could not connect to the database: {exc}")
        log.flush()
        return 2

    try:
        try:
            with conn.cursor() as cur:
                refuse_if_partially_applied(cur, log)
        except RefusalError as exc:
            log.write(f"REFUSED: {exc}")
            log.flush()
            return 2

        for path in files_to_run:
            try:
                apply_one_file(conn, path, log)
            except ApplyAbortedError as exc:
                log.write(f"ABORTED at {exc.filename}: {exc.detail}")
                log.write("Run stopped. Files already committed before this one remain applied.")
                log.flush()
                return 4

            with conn.cursor() as cur:
                statuses = classify_all(cur, MIGRATION_FILES)
            for status in statuses:
                if status.status == PARTIALLY_APPLIED:
                    log.write(
                        f"WARNING: {status.filename} reports PARTIALLY-APPLIED "
                        f"immediately after applying {path.name}."
                    )
        log.write("All files applied successfully.")
    finally:
        conn.close()

    log.flush()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
