#!/usr/bin/env python
"""scripts/migration_preflight.py

Read-only checker for the ``006``-``014`` hand-applied migration set in
``backend_app/migrations/``.

WHY THIS EXISTS
----------------
The three migration scripts already in this repo are unusable:

  * ``run_schema_migration_prod.py`` hardcodes a live production Supabase
    Postgres password and targets a migration file that no longer exists.
  * ``post_migration_validation.py`` called ``os.getenv`` without importing
    ``os``.
  * ``check_migrations_vs_live.py`` reads a ``migrations/`` path that does not
    match this repo's layout (the marketplace/paper set lives in
    ``backend_app/migrations/``).

This script replaces the "am I safe to touch this database" half of that
workflow. It makes **no write** of any kind: every query it issues is a
``SELECT`` against ``information_schema`` / ``pg_catalog`` views, run over a
connection this script itself puts into ``readonly`` mode.

WHAT IT DOES
------------
For each of migrations ``006`` through ``014`` (in
``backend_app/migrations/``), it parses the file's *own* declared schema
objects - tables, columns, constraints, indexes, RLS flags and policy scopes -
using the **same parser** ``tests/test_marketplace_paper_schema_contract.py``
already uses to build its in-memory schema model (imported, not
reimplemented: ``build_schema_model``'s constituent functions,
``_parse_create_table``, ``_parse_add_columns``, ``_parse_add_constraints``,
``_parse_indexes``, ``_parse_rls``, ``_parse_policies``). It then probes the
live database's catalogues for each of those objects and reports, per file:

  * ``APPLIED``               - every declared object is present.
  * ``NOT-APPLIED``           - none of the declared objects are present.
  * ``PARTIALLY-APPLIED``     - some but not all are present. This is the
                                 dangerous state (a half-run file) and is
                                 printed with a visually distinct marker; its
                                 presence makes this script exit non-zero.
  * ``NO-CHECKABLE-OBJECTS``  - the file declares no table/column/constraint/
                                 index this parser can see (``012`` is a
                                 pure ``INSERT`` seed row, ``014`` is a pure
                                 ``CREATE OR REPLACE FUNCTION``). Reported
                                 plainly rather than guessed at.

REFUSAL CONDITIONS
-------------------
  * No DSN. The DSN is read from ``--dsn`` or the ``MIGRATION_DATABASE_URL``
    environment variable - never a hardcoded default (the exact defect this
    script exists to not repeat). Absent either, it refuses: non-zero exit,
    no traceback, no network attempt.
  * Port ``6543``. That is the Supabase pgbouncer pooler in *transaction*
    mode. These migrations use multi-statement ``DO $$ ... $$`` blocks and
    explicit ``BEGIN``/``COMMIT`` around DDL, which need a direct session on
    port ``5432`` - a transaction-mode pooler cannot hold session-local state
    (``set_config``, prepared statements) across statements in the same way.
    Refused before any connection is attempted.

It never prints the DSN or any credential, in any code path, on success or on
refusal.

USAGE
-----
    python scripts/migration_preflight.py --dsn "postgresql://user:pass@host:5432/postgres"
    MIGRATION_DATABASE_URL=postgresql://... python scripts/migration_preflight.py

EXIT CODES
----------
    0   all checkable files are APPLIED, NOT-APPLIED or NO-CHECKABLE-OBJECTS
    2   refused before connecting (no DSN, pooler port, unparseable DSN,
        connection failure)
    3   at least one migration file is PARTIALLY-APPLIED
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

import psycopg2
import psycopg2.extensions

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Reuse the schema-contract test's parser rather than writing a second one.
from tests.test_marketplace_paper_schema_contract import (  # noqa: E402
    SchemaModel,
    _parse_add_columns,
    _parse_add_constraints,
    _parse_create_table,
    _parse_indexes,
    _parse_policies,
    _parse_rls,
    _strip_comments_only,
    _strip_sql_comments_and_strings,
)

BACKEND_MIGRATIONS_DIR = REPO_ROOT / "backend_app" / "migrations"

# The fixed dependency order this checker (and the apply script) work over.
MIGRATION_IDS: List[str] = [
    "006", "007", "008", "009", "010", "011", "012", "013", "014",
]

POOLER_TRANSACTION_MODE_PORT = "6543"
DIRECT_SESSION_PORT = "5432"


class RefusalError(Exception):
    """Raised for every condition this script refuses to proceed past.

    Caught exactly once, at the top of ``run()``/``main()``, and turned into a
    clean non-zero exit with a message - never an uncaught traceback.
    """


def _migration_path(migration_id: str) -> Path:
    matches = sorted(BACKEND_MIGRATIONS_DIR.glob(f"{migration_id}_*.sql"))
    if not matches:
        raise FileNotFoundError(
            f"no migration file found for id {migration_id!r} in "
            f"{BACKEND_MIGRATIONS_DIR}"
        )
    return matches[0]


MIGRATION_FILES: List[Path] = [_migration_path(mid) for mid in MIGRATION_IDS]


# ============================================================================
# DSN handling - never a hardcoded default, never printed
# ============================================================================


def resolve_dsn(cli_dsn: Optional[str], env: Optional[dict] = None) -> str:
    """The DSN from ``--dsn`` or ``MIGRATION_DATABASE_URL``. Refuses if absent.

    ``env`` defaults to ``os.environ`` and exists only so a test can pass a
    controlled mapping without mutating real process environment.
    """
    if cli_dsn:
        return cli_dsn
    import os

    source = env if env is not None else os.environ
    dsn = source.get("MIGRATION_DATABASE_URL")
    if not dsn:
        raise RefusalError(
            "No database DSN provided. Pass --dsn or set the "
            "MIGRATION_DATABASE_URL environment variable. This script never "
            "falls back to a hardcoded connection string - refusing rather "
            "than guessing."
        )
    return dsn


def validate_dsn_port(dsn: str) -> None:
    """Refuses a DSN targeting the Supabase pgbouncer pooler's port 6543."""
    try:
        parsed = psycopg2.extensions.parse_dsn(dsn)
    except Exception as exc:  # psycopg2.ProgrammingError on a malformed DSN
        raise RefusalError(f"DSN could not be parsed: {exc}") from None

    port = parsed.get("port")
    if port == POOLER_TRANSACTION_MODE_PORT:
        raise RefusalError(
            f"DSN targets port {POOLER_TRANSACTION_MODE_PORT}, the Supabase "
            "pgbouncer pooler in transaction mode. These migrations use "
            "multi-statement DO $$ ... $$ blocks and explicit BEGIN/COMMIT "
            "around DDL, which require a direct session on port "
            f"{DIRECT_SESSION_PORT} - a transaction-mode pooler cannot hold "
            "session-local state across statements. Point --dsn / "
            "MIGRATION_DATABASE_URL at the direct connection "
            f"(port {DIRECT_SESSION_PORT}) instead."
        )


# ============================================================================
# Per-file expected-object extraction (parser reused from the schema contract
# test - not reimplemented here)
# ============================================================================


@dataclasses.dataclass(frozen=True)
class FileExpectations:
    migration_id: str
    path: Path
    columns: Set[Tuple[str, str]]
    constraints: Set[str]
    indexes: Set[str]
    rls_tables: Set[str]
    policies: Set[Tuple[str, str]]  # (table, scope)

    @property
    def total_units(self) -> int:
        return (
            len(self.columns)
            + len(self.constraints)
            + len(self.indexes)
            + len(self.rls_tables)
            + len(self.policies)
        )


def expectations_for_file(path: Path) -> FileExpectations:
    """Parse one migration file's own declared schema objects.

    Deliberately scoped to *this file's* statements, not the accumulated
    model every other file contributes to - a preflight check needs to know
    what *this* file, specifically, is supposed to have created.
    """
    model = SchemaModel()
    text = path.read_text(encoding="utf-8", errors="replace")
    clean = _strip_sql_comments_and_strings(text)
    _parse_create_table(model, clean)
    _parse_add_columns(model, clean)
    _parse_add_constraints(model, clean)
    _parse_indexes(model, clean)
    _parse_rls(model, clean)
    # Policy parsing needs string literals (table names, roles) intact.
    _parse_policies(model, _strip_comments_only(text))

    columns = {
        (table, column) for table, cols in model.columns.items() for column in cols
    }
    policies = {
        (table, scope)
        for table, scopes in model.policy_scopes.items()
        for scope in scopes
    }
    migration_id = path.name.split("_", 1)[0]
    return FileExpectations(
        migration_id=migration_id,
        path=path,
        columns=columns,
        constraints=set(model.constraints),
        indexes=set(model.indexes),
        rls_tables=set(model.rls_enabled),
        policies=policies,
    )


# ============================================================================
# Live-database probes - every one a read-only SELECT
# ============================================================================


def _probe_columns(cur, pairs: Set[Tuple[str, str]]) -> Set[Tuple[str, str]]:
    if not pairs:
        return set()
    tables = sorted({table for table, _ in pairs})
    cur.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = ANY(%s)",
        (tables,),
    )
    actual = {(row[0], row[1]) for row in cur.fetchall()}
    return pairs & actual


def _probe_constraints(cur, names: Set[str]) -> Set[str]:
    if not names:
        return set()
    cur.execute(
        "SELECT conname FROM pg_constraint WHERE conname = ANY(%s)",
        (sorted(names),),
    )
    actual = {row[0] for row in cur.fetchall()}
    return names & actual


def _probe_indexes(cur, names: Set[str]) -> Set[str]:
    if not names:
        return set()
    cur.execute(
        "SELECT indexname FROM pg_indexes "
        "WHERE schemaname = 'public' AND indexname = ANY(%s)",
        (sorted(names),),
    )
    actual = {row[0] for row in cur.fetchall()}
    return names & actual


def _probe_rls(cur, tables: Set[str]) -> Set[str]:
    if not tables:
        return set()
    cur.execute(
        "SELECT tablename FROM pg_tables "
        "WHERE schemaname = 'public' AND rowsecurity = TRUE "
        "AND tablename = ANY(%s)",
        (sorted(tables),),
    )
    actual = {row[0] for row in cur.fetchall()}
    return tables & actual


def _probe_policies(cur, pairs: Set[Tuple[str, str]]) -> Set[Tuple[str, str]]:
    if not pairs:
        return set()
    tables = sorted({table for table, _ in pairs})
    cur.execute(
        "SELECT tablename, roles FROM pg_policies "
        "WHERE schemaname = 'public' AND tablename = ANY(%s)",
        (tables,),
    )
    found: Set[Tuple[str, str]] = set()
    for tablename, roles in cur.fetchall():
        role_set = set(roles or [])
        for table, scope in pairs:
            if table == tablename and scope in role_set:
                found.add((table, scope))
    return found & pairs


# ============================================================================
# Classification
# ============================================================================


APPLIED = "APPLIED"
NOT_APPLIED = "NOT-APPLIED"
PARTIALLY_APPLIED = "PARTIALLY-APPLIED"
NO_CHECKABLE_OBJECTS = "NO-CHECKABLE-OBJECTS"


@dataclasses.dataclass(frozen=True)
class FileStatus:
    migration_id: str
    filename: str
    total_units: int
    applied_units: int
    missing: Tuple[str, ...]
    status: str


def classify_file(cur, expectations: FileExpectations) -> FileStatus:
    applied_columns = _probe_columns(cur, expectations.columns)
    applied_constraints = _probe_constraints(cur, expectations.constraints)
    applied_indexes = _probe_indexes(cur, expectations.indexes)
    applied_rls = _probe_rls(cur, expectations.rls_tables)
    applied_policies = _probe_policies(cur, expectations.policies)

    total = expectations.total_units
    applied = (
        len(applied_columns)
        + len(applied_constraints)
        + len(applied_indexes)
        + len(applied_rls)
        + len(applied_policies)
    )

    missing: List[str] = []
    for table, column in sorted(expectations.columns - applied_columns):
        missing.append(f"column {table}.{column}")
    for name in sorted(expectations.constraints - applied_constraints):
        missing.append(f"constraint {name}")
    for name in sorted(expectations.indexes - applied_indexes):
        missing.append(f"index {name}")
    for table in sorted(expectations.rls_tables - applied_rls):
        missing.append(f"RLS enabled on {table}")
    for table, scope in sorted(expectations.policies - applied_policies):
        missing.append(f"{scope} policy on {table}")

    if total == 0:
        status = NO_CHECKABLE_OBJECTS
    elif applied == 0:
        status = NOT_APPLIED
    elif applied == total:
        status = APPLIED
    else:
        status = PARTIALLY_APPLIED

    return FileStatus(
        migration_id=expectations.migration_id,
        filename=expectations.path.name,
        total_units=total,
        applied_units=applied,
        missing=tuple(missing),
        status=status,
    )


def classify_all(cur, files: Iterable[Path] = MIGRATION_FILES) -> List[FileStatus]:
    return [classify_file(cur, expectations_for_file(path)) for path in files]


# ============================================================================
# Reporting - never prints the DSN or a credential
# ============================================================================

_STATUS_MARKERS = {
    APPLIED: "[OK]              ",
    NOT_APPLIED: "[NOT-APPLIED]     ",
    PARTIALLY_APPLIED: "[!! PARTIAL !!]   ",
    NO_CHECKABLE_OBJECTS: "[NO-CHECKABLE-OBJECTS]",
}


def format_report(statuses: List[FileStatus]) -> str:
    lines = [
        "Migration preflight report (read-only; no DSN or credentials shown)",
        "=" * 78,
    ]
    for status in statuses:
        marker = _STATUS_MARKERS[status.status]
        lines.append(
            f"{status.migration_id}  {marker}  {status.filename}  "
            f"({status.applied_units}/{status.total_units} declared objects present)"
        )
        if status.status == PARTIALLY_APPLIED:
            for item in status.missing:
                lines.append(f"        missing: {item}")
    lines.append("=" * 78)
    partial = [s for s in statuses if s.status == PARTIALLY_APPLIED]
    if partial:
        names = ", ".join(s.filename for s in partial)
        lines.append(
            f"REFUSAL: {len(partial)} migration file(s) are PARTIALLY applied "
            f"({names}). Resolve the drift by hand before applying further "
            "migrations or trusting this schema."
        )
    return "\n".join(lines)


# ============================================================================
# CLI
# ============================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only preflight check for backend_app/migrations/006-014. "
            "Never connects to the pgbouncer pooler port and never applies "
            "anything."
        )
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help=(
            "Postgres DSN for a direct session (port 5432). Falls back to "
            "the MIGRATION_DATABASE_URL environment variable. Never printed."
        ),
    )
    return parser


def run(argv: Optional[List[str]] = None, connector=None) -> int:
    """The whole checker, with the DB connector injectable for testing.

    ``connector`` defaults to ``psycopg2.connect``. A test passes a recording
    double instead so refusal paths can be asserted to make no network
    attempt at all.
    """
    connector = connector or psycopg2.connect
    args = build_arg_parser().parse_args(argv)

    try:
        dsn = resolve_dsn(args.dsn)
        validate_dsn_port(dsn)
    except RefusalError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    try:
        conn = connector(dsn)
    except Exception as exc:  # pragma: no cover - real network/auth failures
        print(f"REFUSED: could not connect to the database: {exc}", file=sys.stderr)
        return 2

    try:
        try:
            conn.set_session(readonly=True, autocommit=True)
        except AttributeError:
            # A test double may not implement set_session; that is fine, it
            # never issues a write regardless.
            pass

        cursor_cm = conn.cursor()
        with cursor_cm as cur:
            statuses = classify_all(cur)
    finally:
        conn.close()

    print(format_report(statuses))

    if any(status.status == PARTIALLY_APPLIED for status in statuses):
        return 3
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
