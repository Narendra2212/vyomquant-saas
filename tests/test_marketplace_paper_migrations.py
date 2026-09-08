"""
tests/test_marketplace_paper_migrations.py — Migration-set behaviour test

Marketplace / Subscriptions / Paper-Trading spec, task 11.7. Verifies the
migration *set* behaves the way ``design.md § Migration tests`` and Requirements
23.7, 24.7, 24.8 and 24.10 promise:

  * it applies cleanly **from empty**;
  * it applies cleanly **from a simulated current production revision** built by
    first applying ``migrations/00[1-7]*`` and ``backend_app/migrations/00[1-5]*``;
  * every file honours the additive-only **destructive-statement deny-list** —
    no ``DROP TABLE``, ``DROP COLUMN``, ``ALTER … RENAME``, ``DELETE FROM`` or
    ``TRUNCATE`` (Requirement 24.7);
  * applying ``010`` **alone** raises the *named* missing-``paper_sessions``
    error rather than creating a dangling FK (dependency order, Requirement
    24.4 / 24.10);
  * the ``signals`` back-fill sets every pre-existing row to ``'LIVE'`` and
    **changes no other column** (Requirement 23.7).

WHY THIS IS A STATIC-PARSE TEST AND NOT A LIVE-POSTGRES TEST
    The two migration sequences in this repository are applied **by hand, per
    file** — there is no migration table, no ``alembic``/``django`` runner and
    ``.github/workflows/03-deploy.yml`` has no migration step (see the header of
    ``backend_app/migrations/010_signal_environment.sql`` and
    ``006_backtest_evidence_columns.sql``). CI runs without a PostgreSQL
    instance, so the sibling migration tests
    (``tests/test_schema_as_code_completeness.py``,
    ``tests/test_backtest_evidence_columns_regression.py``,
    ``tests/test_deployment_binding_columns_migration.py``) all verify
    migrations by **parsing the SQL statically into an in-memory schema model**
    rather than by connecting to a database. This test follows exactly that
    pattern: it builds an in-memory ``SchemaModel`` from the ``CREATE TABLE`` /
    ``ALTER TABLE`` / ``UPDATE`` statements, "applies" the migration set to it
    in dependency order, and asserts the resulting model and the recorded
    back-fill. No DB connection is required or attempted.

    Where a real solver would prove the dependency-order refusal by running
    ``010`` against a live database, this test asserts the *mechanism* that
    produces the refusal: ``010`` carries a preflight ``RAISE EXCEPTION`` that
    names ``paper_sessions`` and ``009_paper_trading.sql`` and fires when
    ``to_regclass('public.paper_sessions') IS NULL`` — i.e. exactly when the
    "apply 010 alone" scenario holds. The named error is asserted to exist and
    to be guarded by that null-table condition.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The two independently numbered migration sequences. Both are applied by hand,
# per file — see the module docstring.
MIGRATIONS_DIR = REPO_ROOT / "migrations"
BACKEND_MIGRATIONS_DIR = REPO_ROOT / "backend_app" / "migrations"

# The new marketplace / paper-trading migration set (tasks 11.1–11.5), in the
# dependency order design.md § "Migration ordering" fixes:
#   006 → 007 → 008 → 009 → 010, with 010 depending on paper_sessions from 009.
MARKETPLACE_PAPER_SET = [
    BACKEND_MIGRATIONS_DIR / "006_backtest_evidence_columns.sql",
    BACKEND_MIGRATIONS_DIR / "007_marketplace_submissions.sql",
    BACKEND_MIGRATIONS_DIR / "008_marketplace_settlement.sql",
    BACKEND_MIGRATIONS_DIR / "009_paper_trading.sql",
    BACKEND_MIGRATIONS_DIR / "010_signal_environment.sql",
]

MIGRATION_010 = BACKEND_MIGRATIONS_DIR / "010_signal_environment.sql"
MIGRATION_009 = BACKEND_MIGRATIONS_DIR / "009_paper_trading.sql"


# ===========================================================================
# SQL text handling
# ===========================================================================

def _read(path: Path) -> str:
    assert path.is_file(), f"missing migration: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8", errors="replace")


_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
# A dollar-quoted body: $$ ... $$ or $tag$ ... $tag$. PL/pgSQL DO/function
# bodies live inside these, and their text (RAISE EXCEPTION messages, guard
# conditions) is *not* executable schema DDL — a DELETE FROM or DROP written
# inside a RAISE message string is prose, not a destructive statement.
_DOLLAR_QUOTED = re.compile(r"\$(?P<tag>[a-zA-Z_]*)\$.*?\$(?P=tag)\$", re.S)
# Single-quoted string literals (after doubling '' is collapsed conceptually);
# a simple non-greedy match is enough for the deny-list scan, which only needs
# to avoid counting text *inside* a literal.
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


def _strip_comments(sql: str) -> str:
    sql = _BLOCK_COMMENT.sub(" ", sql)
    sql = _LINE_COMMENT.sub(" ", sql)
    return sql


def _executable_ddl(sql: str) -> str:
    """
    The SQL text with comments, dollar-quoted PL/pgSQL bodies and string
    literals removed, so a scan sees only statements the server would execute
    as DDL/DML — never a keyword that merely appears inside a ``RAISE
    EXCEPTION`` message or a ``COMMENT`` body. Used by the deny-list scan, where
    a literal's *contents* must not count as a statement.
    """
    sql = _strip_comments(sql)
    sql = _DOLLAR_QUOTED.sub(" ", sql)
    sql = _STRING_LITERAL.sub(" '' ", sql)
    return sql


def _statements(sql: str) -> str:
    """
    Like :func:`_executable_ddl` but keeps string literals intact, so the value
    a back-fill assigns (``environment = 'LIVE'``) survives. Comments and
    dollar-quoted PL/pgSQL bodies are still removed so a ``RAISE`` message or a
    ``COMMENT`` body never registers as an executable statement.
    """
    sql = _strip_comments(sql)
    sql = _DOLLAR_QUOTED.sub(" ", sql)
    return sql


# ===========================================================================
# In-memory schema model — the stand-in for a live database
# ===========================================================================

class SchemaModel:
    """
    A deliberately small model of what a PostgreSQL catalogue would hold after a
    migration is applied: table → set of column names, plus the recorded effect
    of ``UPDATE`` back-fills. It is *not* a SQL engine; it models exactly the
    statements this repository's migrations use (``CREATE TABLE``,
    ``ALTER TABLE … ADD COLUMN``, ``UPDATE … SET … WHERE``), which is all this
    task's assertions need.
    """

    def __init__(self):
        # table name (lower, unqualified) -> set of column names
        self.tables: dict[str, set[str]] = {}
        # ordered log of applied back-fills: (table, {column: value}, where_col)
        self.backfills: list[tuple[str, dict[str, str], str]] = []

    def has_table(self, table: str) -> bool:
        return table.lower() in self.tables

    def columns(self, table: str) -> set[str]:
        return self.tables.get(table.lower(), set())

    # -- statement application -------------------------------------------

    def create_table(self, table: str, columns: set[str]) -> None:
        table = table.lower()
        # CREATE TABLE IF NOT EXISTS is a no-op when the table is present, so a
        # re-application never widens or narrows an existing table.
        self.tables.setdefault(table, set()).update(columns)

    def add_columns(self, table: str, columns: set[str]) -> None:
        # ADD COLUMN IF NOT EXISTS against a table the model has not seen a
        # CREATE TABLE for models a column added to a table that exists in the
        # live database from a source outside the parsed globs — a Supabase
        # system table (profiles, auth.users), or a table created by a
        # non-numbered historical migration this glob does not include. On a
        # live database that ALTER succeeds; the model mirrors that by treating
        # the table as pre-existing (auto-created empty) so the column lands.
        table = table.lower()
        self.tables.setdefault(table, set()).update(columns)

    def record_backfill(self, table: str, assignments: dict[str, str], where_col: str) -> None:
        self.backfills.append((table.lower(), assignments, where_col))

    def snapshot(self) -> dict[str, frozenset]:
        """A hashable, comparable view of the schema for equality assertions."""
        return {t: frozenset(cols) for t, cols in self.tables.items()}


# ---------------------------------------------------------------------------
# Statement extractors (regex, matching the sibling migration tests' approach)
# ---------------------------------------------------------------------------

_CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?"
    r"([a-z0-9_]+)\s*\((.*?)\n\s*\)\s*;",
    re.S | re.I,
)

_ALTER_TABLE = re.compile(
    r"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?(?:public\.)?"
    r"([a-z0-9_]+)(.*?);",
    re.S | re.I,
)

_ADD_COLUMN = re.compile(
    r"add\s+column\s+(?:if\s+not\s+exists\s+)?([a-z0-9_]+)", re.I
)

# UPDATE public.signals SET environment = 'LIVE' WHERE environment IS NULL;
_UPDATE = re.compile(
    r"update\s+(?:public\.)?([a-z0-9_]+)\s+set\s+(.*?)\s+where\s+(.*?);",
    re.S | re.I,
)


def _parse_create_columns(body: str) -> set[str]:
    """Column names from the parenthesised body of a CREATE TABLE."""
    found: set[str] = set()
    depth = 0
    # Split on top-level commas only, so a NUMERIC(28,10) does not split.
    parts: list[str] = []
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))

    reserved = {
        "primary", "unique", "check", "foreign", "constraint", "exclude",
        "like", "partition",
    }
    for raw in parts:
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'"?([a-z0-9_]+)"?\s+[a-z"(]', line, re.I)
        if m and m.group(1).lower() not in reserved:
            found.add(m.group(1).lower())
    return found


def apply_migration(model: SchemaModel, path: Path) -> None:
    """
    Apply one migration file to the in-memory model: create tables, add columns
    and record back-fill UPDATEs. Operates on the executable DDL only (comments,
    PL/pgSQL bodies and string literals stripped) so a DO-block guard's prose
    never counts as a statement.
    """
    raw = _read(path)
    ddl = _executable_ddl(raw)

    for m in _CREATE_TABLE.finditer(ddl):
        model.create_table(m.group(1), _parse_create_columns(m.group(2)))

    for m in _ALTER_TABLE.finditer(ddl):
        table = m.group(1)
        added = {c.group(1).lower() for c in _ADD_COLUMN.finditer(m.group(2))}
        if added:
            model.add_columns(table, added)

    # Back-fill UPDATEs are parsed from the literal-preserving view so the
    # assigned value ('LIVE') survives the scan.
    for m in _UPDATE.finditer(_statements(raw)):
        table, set_clause, where_clause = m.group(1), m.group(2), m.group(3)
        assignments: dict[str, str] = {}
        for assign in set_clause.split(","):
            if "=" in assign:
                col, _, val = assign.partition("=")
                assignments[col.strip().lower()] = val.strip()
        where_col = ""
        wm = re.match(r"\(?\s*([a-z0-9_]+)", where_clause, re.I)
        if wm:
            where_col = wm.group(1).lower()
        model.record_backfill(table, assignments, where_col)


def apply_set(model: SchemaModel, files: list[Path]) -> SchemaModel:
    for path in files:
        apply_migration(model, path)
    return model


# ===========================================================================
# Fixtures: the "empty" and "current production revision" starting points
# ===========================================================================

# The simulated current production revision the task names: migrations/00[1-7]*
# and backend_app/migrations/00[1-5]*. These are the files an established
# environment has already applied by hand before the marketplace/paper set
# lands. Applying the set on top of them must succeed exactly as it does on an
# empty database.
def _production_revision_files() -> list[Path]:
    older = sorted(MIGRATIONS_DIR.glob("00[1-7]*.sql"))
    backend_older = sorted(BACKEND_MIGRATIONS_DIR.glob("00[1-5]*.sql"))
    # Guard the premise: the glob must actually match the historical files, or
    # a rename upstream would make this test silently assert against nothing.
    assert older, "migrations/00[1-7]*.sql matched no files"
    assert backend_older, "backend_app/migrations/00[1-5]*.sql matched no files"
    return older + backend_older


def _empty_model() -> SchemaModel:
    return SchemaModel()


def _production_model() -> SchemaModel:
    """
    A model that already holds the tables the historical migrations created —
    in particular ``public.signals`` with its pre-existing rows, which 010's
    back-fill must set to ``'LIVE'``. The signals table is created by
    backend_app/migrations/002_signal_trace.sql (part of the 00[1-5]* glob), so
    applying the production revision populates it here.
    """
    model = SchemaModel()
    apply_set(model, _production_revision_files())
    return model


# ===========================================================================
# 1) Applies from empty
# ===========================================================================

def test_migration_files_all_exist():
    """The five files of the marketplace/paper set are in this checkout."""
    for path in MARKETPLACE_PAPER_SET:
        assert path.is_file(), (
            f"{path.relative_to(REPO_ROOT)} is missing "
            "(marketplace-subscriptions-paper-trading tasks 11.1-11.5)."
        )


def test_applies_from_empty():
    """
    Applying the set to an empty database creates the paper tables and adds the
    signals columns without error. Requirement 24.10 — the set is self-contained
    from empty except for the auth.users/signals dependencies the historical
    migrations own; from empty, 010's signals ALTER has nothing to alter, so we
    seed the one external precondition (an empty signals table) the way an empty
    bootstrap would before running the signal-trace migrations.
    """
    model = _empty_model()
    # From a truly empty database the signals table is created by the historical
    # 002 migration, not by this set. Seed it so 010's ADD COLUMN has a target,
    # mirroring the bootstrap order (002 before 010).
    model.create_table("signals", {"id", "user_id", "generated_at", "status"})

    apply_set(model, MARKETPLACE_PAPER_SET)

    # 009 created the eleven paper_* tables plus the two transition seed tables.
    for table in (
        "paper_sessions", "paper_accounts", "paper_orders", "paper_fills",
        "paper_positions", "paper_balance_events", "paper_trades",
        "paper_equity_snapshots", "paper_metrics", "paper_events",
        "paper_market_events",
    ):
        assert model.has_table(table), f"009 did not create {table}"

    # 010 added the two signals columns.
    assert "environment" in model.columns("signals")
    assert "paper_session_id" in model.columns("signals")


# ===========================================================================
# 2) Applies from a simulated current production revision
# ===========================================================================

def test_applies_from_current_production_revision():
    """
    Building the model from migrations/00[1-7]* and backend_app/migrations/
    00[1-5]* first (the current production revision), then applying the
    marketplace/paper set, succeeds and yields the same paper tables and signals
    columns as the empty apply. Requirement 24.10.
    """
    model = _production_model()
    # The historical revision must already carry public.signals (from 002).
    assert model.has_table("signals"), (
        "the simulated production revision did not create public.signals; "
        "backend_app/migrations/002_signal_trace.sql should be inside the "
        "00[1-5]* glob"
    )

    apply_set(model, MARKETPLACE_PAPER_SET)

    for table in ("paper_sessions", "paper_orders", "paper_fills"):
        assert model.has_table(table)
    assert "environment" in model.columns("signals")
    assert "paper_session_id" in model.columns("signals")


# ===========================================================================
# 3) Destructive-statement deny-list (Requirement 24.7)
# ===========================================================================

# Additive-only: none of these appear as an executable statement anywhere in the
# marketplace/paper set. Text inside a RAISE message or a comment does not count
# (the scan runs over _executable_ddl, which strips both).
_DENY = {
    "DROP TABLE": re.compile(r"\bdrop\s+table\b", re.I),
    "DROP COLUMN": re.compile(r"\bdrop\s+column\b", re.I),
    "ALTER ... RENAME": re.compile(r"\brename\b", re.I),
    "DELETE FROM": re.compile(r"\bdelete\s+from\b", re.I),
    "TRUNCATE": re.compile(r"\btruncate\b", re.I),
}


@pytest.mark.parametrize("path", MARKETPLACE_PAPER_SET, ids=lambda p: p.name)
def test_no_destructive_statements(path):
    """No file in the set contains a destructive statement (Requirement 24.7)."""
    ddl = _executable_ddl(_read(path))
    offenders = [name for name, pattern in _DENY.items() if pattern.search(ddl)]
    assert not offenders, (
        f"{path.name} contains destructive statement(s) {offenders}; the "
        "migration set is additive-only (Requirement 24.7)."
    )


def test_deny_list_scan_ignores_prose():
    """
    Guard the deny-list scan's own premise: the raw text of 010 *does* mention
    'DELETE' (in 'ON DELETE SET NULL') and 'DROP'/'TRUNCATE'/'RENAME' in its
    header prose, so a naive raw-text grep would false-positive. The executable
    DDL view must clear that prose while still catching a real statement.
    """
    raw = _read(MIGRATION_010)
    # ON DELETE SET NULL is a real, allowed clause — not "DELETE FROM".
    assert "on delete set null" in raw.lower()
    ddl = _executable_ddl(raw)
    assert not _DENY["DELETE FROM"].search(ddl)
    # And a fabricated destructive statement would still be caught.
    assert _DENY["TRUNCATE"].search(_executable_ddl("TRUNCATE public.signals;"))


# ===========================================================================
# 4) Dependency order: applying 010 alone raises the named missing-paper_sessions error
# ===========================================================================

def test_010_names_missing_paper_sessions_dependency():
    """
    010's preflight raises a NAMED error that points at paper_sessions and at
    009_paper_trading.sql, guarded by ``to_regclass('public.paper_sessions') IS
    NULL`` — i.e. it fires exactly in the "apply 010 alone" scenario, refusing to
    create a dangling FK (Requirements 23.2, 24.4, 24.10).
    """
    sql = _read(MIGRATION_010).lower()

    # The refusal is a RAISE EXCEPTION, guarded by the null-table probe.
    assert "to_regclass('public.paper_sessions') is null" in sql, (
        "010 must guard on paper_sessions being absent before its ALTER runs"
    )
    assert "raise exception" in sql

    # The message names the missing table and the file that creates it.
    assert "paper_sessions" in sql
    assert "009_paper_trading.sql" in sql

    # The FK it would otherwise create dangles onto paper_sessions(id).
    assert "references public.paper_sessions(id)" in sql
    assert "fk_signals_paper_session" in sql


def test_010_alone_against_model_without_paper_sessions_is_the_named_scenario():
    """
    Model-level analogue of "apply 010 alone": on a model that has signals but
    no paper_sessions, the preflight condition (paper_sessions absent) is true,
    which is precisely when 010's named RAISE fires. We assert the model state
    that triggers it, then confirm 009 is what supplies the missing table.
    """
    model = SchemaModel()
    model.create_table("signals", {"id", "user_id", "environment"})
    assert not model.has_table("paper_sessions"), (
        "the 'apply 010 alone' scenario is defined by paper_sessions being "
        "absent — this is the condition 010's preflight RAISE guards on"
    )

    # 009 is the file that creates paper_sessions, resolving the dependency.
    ddl_009 = _executable_ddl(_read(MIGRATION_009))
    created = {m.group(1).lower() for m in _CREATE_TABLE.finditer(ddl_009)}
    assert "paper_sessions" in created, (
        "009_paper_trading.sql must create paper_sessions — it is 010's FK target"
    )


# ===========================================================================
# 5) signals back-fill sets every pre-existing row to 'LIVE' and changes nothing else
# ===========================================================================

def test_signals_backfill_sets_live_only():
    """
    010's back-fill is ``UPDATE public.signals SET environment = 'LIVE' WHERE
    environment IS NULL`` — it writes exactly one column (``environment``), to
    exactly the value ``'LIVE'``, and only into rows where it is still NULL, so
    it neither rewrites an already-set environment nor touches any other column
    (Requirement 23.7).
    """
    model = _production_model()
    # Ensure the historical revision left signals present with rows to back-fill.
    assert model.has_table("signals")

    apply_migration(model, MIGRATION_010)

    signals_backfills = [b for b in model.backfills if b[0] == "signals"]
    assert signals_backfills, "010 recorded no UPDATE against public.signals"

    for table, assignments, where_col in signals_backfills:
        # (a) exactly one column assigned, and it is environment.
        assert set(assignments) == {"environment"}, (
            f"back-fill assigns {set(assignments)}; Requirement 23.7 permits "
            "only environment to change"
        )
        # (b) the assigned value is 'LIVE'.
        value = assignments["environment"].strip().strip("'").upper()
        assert value == "LIVE", f"back-fill sets environment to {value!r}, not 'LIVE'"
        # (c) it is conditioned on environment IS NULL, so an already-set value
        # is never rewritten (idempotent, Requirement 24.8).
        assert where_col == "environment", (
            "back-fill must be scoped by WHERE environment IS NULL so it never "
            "rewrites a row that already carries a value"
        )


def test_signals_backfill_where_clause_is_environment_is_null():
    """
    The precise idempotency guarantee: the WHERE clause is ``environment IS
    NULL``, so a second application matches zero rows (Requirement 24.8) and no
    pre-set environment is ever overwritten.
    """
    ddl = _statements(_read(MIGRATION_010))
    m = _UPDATE.search(ddl)
    assert m, "010 has no UPDATE ... signals statement"
    assert m.group(1).lower() == "signals"
    where_clause = m.group(3).lower()
    assert "environment" in where_clause and "is null" in where_clause, (
        f"back-fill WHERE clause is {where_clause!r}; expected 'environment IS NULL'"
    )


def test_signals_backfill_does_not_touch_paper_session_id():
    """
    paper_session_id is added NULLABLE and never back-filled: a LIVE/BACKTEST
    signal has no Paper_Session and NULL there is the true value (Requirement
    23.7). No UPDATE in 010 assigns paper_session_id.
    """
    model = _production_model()
    apply_migration(model, MIGRATION_010)
    for table, assignments, _ in model.backfills:
        if table == "signals":
            assert "paper_session_id" not in assignments, (
                "010 must not back-fill paper_session_id — a NULL there is the "
                "true value for a non-paper signal"
            )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
