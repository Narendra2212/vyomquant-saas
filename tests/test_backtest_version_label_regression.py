"""
tests/test_backtest_version_label_regression.py

THE BUG THIS FILE DETECTS
------------------------
``public.strategy_backtests.version`` had TWO divergent declarations, and production took
the wrong one:

  * definition A - ``backend_app/migrations/001_strategy_architecture.sql`` line 125 -
    ``version VARCHAR(20) NOT NULL``, in the same file whose
    ``strategy_versions.version`` carries the comment ``e.g., "v1.0", "v1.1", "v2.0"``.
  * definition B - ``migrations/006_reconcile_production_database.sql`` line 449 -
    ``version INTEGER DEFAULT 1``.

Both are ``CREATE TABLE IF NOT EXISTS``, so whichever ran first in a given database decided
the shape and the other silently did nothing. **Production carried B's integer.**

``BacktestService.create_backtest`` writes a version LABEL into that column - the string
``routers/strategy_operations.py`` computes as
``version_label = str(version_row.get("version") or "v1.0")`` off
``strategy_versions.version``, itself ``VARCHAR(20)``. PostgreSQL therefore refused **every**
backtest insert::

    {'code': '22P02', 'message': 'invalid input syntax for type integer: "v1.0"'}

which CloudWatch recorded as ``ERROR:StrategyOperationsRouter:Error executing backtest for
strategy f83975d7-... : {'code': '22P02', ...}``.

WHY THE FIX IS THE COLUMN AND NOT THE VALUE
-------------------------------------------
Every other version column in this schema is text - ``strategies.current_version``
(``VARCHAR(20) DEFAULT 'v1.0'``), ``strategy_versions.version``,
``strategy_deployments.version``, ``signals.strategy_version`` - so the integer was the
outlier. And ``"v1.0"`` has no integer spelling: parsing the numeric part would map
``"v1.0"``, ``"v1.1"`` and ``"v1.9"`` onto the single value ``1``, reinstating exactly the
defect the removed hardcoded ``1`` in ``create_backtest`` caused, where every backtest was
recorded against "version 1". ``backend_app/migrations/016_strategy_backtests_version_label.sql``
therefore converts the column to ``VARCHAR(20)`` and drops B's ``DEFAULT 1``.

WHAT EACH HALF OF THIS FILE ASSERTS
-----------------------------------
``TestTheDivergenceIsReal``
    The premise, parsed out of the two ``CREATE TABLE`` statements rather than remembered:
    A says character, B says integer with a default of 1. Without this, the rest of the file
    could pass while asserting against a problem that no longer exists.

``TestMigration016ReconcilesTheColumn``
    Static verification of migration 016 - it is in the checkout, it converts the column to
    a 20-character character type with an explicit ``USING version::text`` cast, it drops the
    default **before** the type change (a surviving ``DEFAULT 1`` makes the conversion abort
    with "default for column ... cannot be cast automatically"), both mutating statements are
    guarded so the file is re-runnable, and it changes nothing else on the table. **These
    tests fail before the migration exists and pass after it lands**, which is what makes
    deleting it a red suite rather than a quiet regression.

``TestTheLabelReachesTheRow`` / ``test_every_label_the_router_can_produce_is_stored_verbatim``
    The Python half: ``create_backtest`` passes the label through **unchanged** - no
    ``int()``, no ordinal, no truncation - and the *integer-shaped* column is what refuses
    it. The second test proves the same over the whole label space the router can produce.

NO POSTGRESQL, AND NOTHING MOCKED THAT IS UNDER TEST
----------------------------------------------------
CI runs no PostgreSQL, so the double here is the PostgREST client - the one thing this
environment has no instance of - exactly as
``tests/test_backtest_evidence_columns_regression.py`` does it. The real ``BacktestService``
is used, and the double enforces the *column type* taken from whichever declaration it is
asked to stand in for, so "the integer column is what rejects the label" is a claim about
the committed schema and not about a fake.

The migration half reads only **executable** SQL: comments and string literals are blanked
before anything is asserted, so 016's own prose (which quotes ``INTEGER DEFAULT 1``,
``22P02`` and every keyword this file forbids) cannot satisfy or trip a single assertion.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.backtest_service import BacktestService

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Definition A - the ``strategy_backtests`` of 001, line 118 onwards.
DEFINITION_A_SQL = REPO_ROOT / "backend_app" / "migrations" / "001_strategy_architecture.sql"
#: Definition B - the ``strategy_backtests`` of the production reconciliation, line 445.
DEFINITION_B_SQL = REPO_ROOT / "migrations" / "006_reconcile_production_database.sql"
#: The reconciliation this file is the contract for.
MIGRATION_016 = (
    REPO_ROOT / "backend_app" / "migrations" / "016_strategy_backtests_version_label.sql"
)

TABLE = "strategy_backtests"
COLUMN = "version"

USER_ID = "11111111-1111-4111-8111-111111111111"
STRATEGY_ID = "22222222-2222-4222-8222-222222222222"
VERSION_ID = "33333333-3333-4333-8333-333333333333"
USER = {"id": USER_ID, "access_token": "token-for-the-rls-scoped-client"}

#: The label the router produces for a strategy whose version row names none
#: (``strategy_operations.py`` line 1462's own fallback).
LABEL = "v1.0"


# ---------------------------------------------------------------------------
# SQL reading - executable code only
# ---------------------------------------------------------------------------

def _blank_comments(sql: str) -> str:
    """Replace every ``--`` comment with spaces, preserving line and column offsets.

    Offsets are preserved because two assertions below compare the POSITION of one
    statement against another (the drop-default-before-type-change ordering).
    """
    out = []
    for line in sql.splitlines(keepends=True):
        code, sep, _comment = line.partition("--")
        if sep:
            stripped = line[len(line.rstrip("\r\n")) :]
            out.append(code + " " * (len(line) - len(code) - len(stripped)) + stripped)
        else:
            out.append(line)
    return "".join(out)


def _blank_literals(sql: str) -> str:
    """Replace the contents of every single-quoted literal with spaces.

    Without this, 016's ``RAISE EXCEPTION`` and ``RAISE NOTICE`` messages - which quote
    ``INSERT``, ``UPDATE``, ``DELETE``, ``TRUNCATE``, ``ENABLE ROW LEVEL SECURITY`` and
    ``INTEGER DEFAULT 1`` while explaining that the file contains none of them - would
    fail the "no destructive statement" scan. Doubled ``''`` escapes are consumed as
    part of the literal. Offsets are preserved.
    """
    out = []
    index = 0
    inside = False
    length = len(sql)
    while index < length:
        char = sql[index]
        if inside:
            if char == "'":
                if index + 1 < length and sql[index + 1] == "'":
                    out.append("  ")
                    index += 2
                    continue
                inside = False
                out.append("'")
            else:
                out.append("\n" if char == "\n" else " ")
            index += 1
            continue
        if char == "'":
            inside = True
        out.append(char)
        index += 1
    return "".join(out)


def _executable(path: Path) -> str:
    """The migration's executable SQL, lower-cased, with comments and literals blanked."""
    assert path.is_file(), f"missing migration: {path}"
    return _blank_literals(_blank_comments(path.read_text(encoding="utf-8", errors="replace"))).lower()


def _header_prose(path: Path) -> str:
    """Only the leading ``--`` comment block of a migration, lower-cased."""
    assert path.is_file(), f"missing migration: {path}"
    lines = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("--"):
            lines.append(line)
        elif line.strip() == "":
            continue
        else:
            break
    return "\n".join(lines).lower()


def _declared_column_type(path: Path, table: str, column: str) -> str:
    """The type text a ``CREATE TABLE ... <table>`` gives ``<column>``.

    Returns the remainder of the column's declaration line - type, default and any
    inline constraint - so a caller can assert on all three.
    """
    sql = _blank_comments(path.read_text(encoding="utf-8", errors="replace"))
    pattern = (
        r"create\s+table[^;]*?(?:public\.)?" + re.escape(table) + r"\s*\((.*?)\n\s*\)\s*;"
    )
    match = re.search(pattern, sql, re.S | re.I)
    assert match, f"no CREATE TABLE {table} parsed out of {path.name}"
    for line in match.group(1).splitlines():
        line = line.strip().rstrip(",")
        declaration = re.match(r"([a-z0-9_]+)\s+(.+)$", line, re.I)
        if declaration and declaration.group(1).lower() == column:
            return declaration.group(2).strip().lower()
    raise AssertionError(f"{table}.{column} is not declared in {path.name}")


def _type_conversion(code: str):
    """The ``ALTER COLUMN version TYPE ...`` statement in ``code``, as a match object."""
    return re.search(
        r"alter\s+table\s+(?:public\.)?"
        + re.escape(TABLE)
        + r"\s+alter\s+column\s+"
        + re.escape(COLUMN)
        + r"\s+type\s+([^;]+);",
        code,
        re.S,
    )


def _drop_default(code: str):
    """The ``ALTER COLUMN version DROP DEFAULT`` statement in ``code``, as a match."""
    return re.search(
        r"alter\s+table\s+(?:public\.)?"
        + re.escape(TABLE)
        + r"\s+alter\s+column\s+"
        + re.escape(COLUMN)
        + r"\s+drop\s+default\s*;",
        code,
        re.S,
    )


def _do_blocks(code: str):
    """Every ``DO $$ ... END $$;`` body in ``code``, as (start, end, body) triples."""
    return [
        (m.start(), m.end(), m.group(0))
        for m in re.finditer(r"do\s+\$\$.*?end\s+\$\$\s*;", code, re.S)
    ]


def _enclosing_do_block(code: str, position: int):
    for start, end, body in _do_blocks(code):
        if start <= position <= end:
            return body
    return None


# ---------------------------------------------------------------------------
# The PostgREST double - it enforces the COLUMN TYPE, which is the whole point
# ---------------------------------------------------------------------------

#: ``strategy_backtests.version`` as migrations/006_reconcile_production_database.sql
#: declares it, and as production carried it.
INTEGER_COLUMN = "integer"
#: ``strategy_backtests.version`` as 001 declares it and as migration 016 restores it.
VARCHAR_COLUMN = "varchar(20)"


class _Result:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    def __init__(self, parent, table):
        self._parent = parent
        self._table = table
        self._payload = None

    def insert(self, payload, *a, **kw):
        self._payload = payload
        return self

    def execute(self):
        self._parent.check(self._table, self._payload)
        row = dict(self._payload)
        self._parent.inserts.append((self._table, dict(row)))
        self._parent.rows.setdefault(self._table, []).append(row)
        return _Result([row])


class _Supabase:
    """A PostgREST client whose ``strategy_backtests.version`` has a declared TYPE.

    ``version_type=INTEGER_COLUMN`` reproduces production before migration 016: a value
    PostgreSQL cannot read as an integer is refused with ``22P02``, the error CloudWatch
    recorded. ``version_type=VARCHAR_COLUMN`` is 001's shape, which 016 restores: any
    string of at most 20 characters is accepted, and anything longer is ``22001``.

    Only ``version`` is policed. Every other key passes through untouched, because this
    file makes no claim about the rest of the row - that is
    ``tests/test_backtest_evidence_columns_regression.py``'s subject.
    """

    def __init__(self, version_type=VARCHAR_COLUMN):
        self.version_type = version_type
        self.rows = {}
        self.inserts = []

    def check(self, table, payload):
        if table != TABLE or COLUMN not in (payload or {}):
            return
        value = payload[COLUMN]
        if self.version_type == INTEGER_COLUMN:
            # PostgreSQL's integer input function, as PostgREST surfaces its refusal.
            if isinstance(value, bool) or not (
                isinstance(value, int)
                or (isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()))
            ):
                raise RuntimeError(
                    "{'code': '22P02', 'message': 'invalid input syntax for type "
                    'integer: "' + str(value) + "\"'}"
                )
            return
        if not isinstance(value, str):
            raise RuntimeError(
                f"{{'code': '42804', 'message': 'column {COLUMN} is of type character "
                f"varying but expression is of type {type(value).__name__}'}}"
            )
        if len(value) > 20:
            raise RuntimeError(
                "{'code': '22001', 'message': 'value too long for type "
                "character varying(20)'}"
            )

    def table(self, name):
        return _Query(self, name)

    def inserted(self, table=TABLE):
        (payload,) = [p for name, p in self.inserts if name == table]
        return payload


def _service(sb) -> BacktestService:
    """The real service, wired to the fake client at its one seam."""
    service = BacktestService()
    service._get_supabase = lambda user: sb  # noqa: SLF001 - the seam under test
    return service


async def _create(sb, version=LABEL):
    return await _service(sb).create_backtest(
        user=USER,
        strategy_id=STRATEGY_ID,
        version=version,
        version_id=VERSION_ID,
        blueprint={"schema_version": "2.0", "metadata": {"dag_hash": "abc123"}},
        dataset="BTC/USDT",
        start_date="2024-01-01",
        end_date="2024-04-01",
        initial_capital=10_000.0,
        commission=0.001,
        slippage=0.0005,
    )


# ---------------------------------------------------------------------------
# 1. The premise: the two declarations really do disagree
# ---------------------------------------------------------------------------

class TestTheDivergenceIsReal:
    """Parsed out of the SQL, so this file cannot pass by asserting against a memory."""

    def test_001_declares_the_version_column_as_characters(self):
        declaration = _declared_column_type(DEFINITION_A_SQL, TABLE, COLUMN)
        assert "varchar(20)" in declaration or "character varying(20)" in declaration, (
            f"001_strategy_architecture.sql no longer declares {TABLE}.{COLUMN} as a "
            f"20-character string (found {declaration!r}). Migration 016 reconciles the "
            f"production integer against THIS declaration, so if 001 changed, 016's "
            f"target type has to be revisited with it."
        )

    def test_the_production_reconciliation_declares_it_as_an_integer_with_a_default(self):
        declaration = _declared_column_type(DEFINITION_B_SQL, TABLE, COLUMN)
        assert declaration.startswith("integer"), (
            f"migrations/006_reconcile_production_database.sql no longer declares "
            f"{TABLE}.{COLUMN} as an integer (found {declaration!r}). That integer IS the "
            f"bug 016 repairs; if this file was edited instead, the historical record of "
            f"how production got its shape is gone and 016 needs re-reading."
        )
        assert "default 1" in declaration, (
            f"the integer declaration no longer carries DEFAULT 1 (found {declaration!r}). "
            f"016 drops that default BEFORE converting the type, because a surviving "
            f"default cannot be cast from integer to character varying."
        )

    def test_every_other_version_column_in_001_is_a_character_column(self):
        """The integer was the outlier - the reason the TYPE is what 016 changes."""
        sql = _blank_comments(DEFINITION_A_SQL.read_text(encoding="utf-8", errors="replace"))
        integers = re.findall(r"^\s*(\w*version\w*)\s+integer", sql, re.I | re.M)
        assert not integers, (
            f"001 now declares version column(s) {integers} as integers; the premise that "
            f"every label column in this schema is text no longer holds"
        )
        assert re.search(
            r"current_version\s+varchar\(20\)\s+default\s+'v1\.0'", sql, re.I
        ), "strategies.current_version is no longer VARCHAR(20) DEFAULT 'v1.0'"


# ---------------------------------------------------------------------------
# 2. The migration - these fail before 016 exists and pass after it lands
# ---------------------------------------------------------------------------

class TestMigration016ReconcilesTheColumn:
    def test_migration_016_is_in_this_checkout(self):
        """Its absence is the bug, not an environment this file has nothing to say about."""
        assert MIGRATION_016.is_file(), (
            f"{MIGRATION_016.relative_to(REPO_ROOT)} is not in this checkout. It IS the fix: "
            f"without it a database carrying migrations/006_reconcile_production_database."
            f"sql's shape keeps {TABLE}.{COLUMN} as an INTEGER and refuses every backtest "
            f"insert with 22P02 invalid input syntax for type integer: \"v1.0\". Its absence "
            f"is a REVERT - do not turn this into a skip."
        )

    def test_it_converts_the_version_column_to_a_20_character_string(self):
        conversion = _type_conversion(_executable(MIGRATION_016))
        assert conversion, (
            f"016 contains no ALTER TABLE {TABLE} ALTER COLUMN {COLUMN} TYPE statement in "
            f"its executable SQL. Changing the column's type is the entire fix; a file that "
            f"only comments on the problem leaves production refusing every insert."
        )
        target = conversion.group(1)
        assert re.match(r"(varchar|character\s+varying)\s*\(\s*20\s*\)", target.strip()), (
            f"016 converts {TABLE}.{COLUMN} to {target.strip()!r}, not to a 20-character "
            f"character type. 001 declares VARCHAR(20) and strategy_versions.version - the "
            f"column the label is copied from - is VARCHAR(20); a different target type "
            f"makes this a third opinion rather than a reconciliation."
        )
        assert "integer" not in target, (
            f"016's conversion target still mentions integer: {target.strip()!r}"
        )

    def test_the_conversion_casts_explicitly_so_rows_cannot_block_it(self):
        """``USING version::text`` - safe even if rows appear before an operator applies it.

        Without a USING clause PostgreSQL refuses an integer -> varchar conversion outright
        (42804 cannot_coerce), so the cast is not decoration.
        """
        conversion = _type_conversion(_executable(MIGRATION_016))
        assert conversion, "no type conversion in 016 (see the previous test)"
        target = " ".join(conversion.group(1).split())
        assert re.search(r"using\s+" + re.escape(COLUMN) + r"\s*::\s*text", target), (
            f"016's conversion has no explicit USING {COLUMN}::text cast: {target!r}. A bare "
            f"ALTER ... TYPE fails on an integer column, and an implicit cast would be a "
            f"guess about data this file may not have seen."
        )

    def test_it_drops_the_default_before_it_changes_the_type(self):
        """Ordering is mechanical, not stylistic.

        PostgreSQL re-coerces a surviving DEFAULT into the column's new type, and there is
        no assignment cast from integer to character varying, so an ALTER ... TYPE issued
        while ``DEFAULT 1`` is attached aborts with "default for column "version" cannot be
        cast automatically to type character varying" - on precisely the databases 016
        exists to repair.
        """
        code = _executable(MIGRATION_016)
        dropped = _drop_default(code)
        conversion = _type_conversion(code)
        assert dropped, (
            f"016 never drops the DEFAULT from {TABLE}.{COLUMN}. Carried through the cast, "
            f"DEFAULT 1 becomes DEFAULT '1' - a label naming no version, recorded on any "
            f"row whose writer omits the field."
        )
        assert conversion, "no type conversion in 016 (see the earlier test)"
        assert dropped.start() < conversion.start(), (
            "016 changes the column's type BEFORE dropping DEFAULT 1. That ordering makes "
            "the migration abort with \"default for column \"version\" cannot be cast "
            "automatically to type character varying\" on every database that has 006's "
            "shape - the only databases that need it."
        )

    def test_both_mutating_statements_are_guarded_so_the_file_is_re_runnable(self):
        """Migrations here are applied BY HAND with no record of what has run.

        Neither ``DROP DEFAULT`` nor ``ALTER ... TYPE`` is idempotent on its own: the type
        change would rewrite an already-correct table a second time. Each therefore sits in
        a ``DO`` block that reads ``information_schema.columns`` first.
        """
        code = _executable(MIGRATION_016)
        for name, match in (("DROP DEFAULT", _drop_default(code)),
                            ("ALTER ... TYPE", _type_conversion(code))):
            assert match, f"016 has no {name} statement"
            block = _enclosing_do_block(code, match.start())
            assert block is not None, (
                f"016's {name} is not inside a DO block, so it runs unconditionally and a "
                f"re-run either raises or rewrites the table again"
            )
            assert "information_schema.columns" in block, (
                f"016's {name} is in a DO block that never reads information_schema."
                f"columns, so it cannot know whether it has already been applied"
            )

    def test_it_changes_nothing_but_that_one_column(self):
        """This file is the first here to ALTER a column type; it must be nothing more."""
        code = _executable(MIGRATION_016)
        forbidden = (
            "drop table",
            "drop column",
            "add column",
            "truncate",
            "insert into",
            "delete from",
            "update public.",
            "create policy",
            "alter policy",
            "drop policy",
            "enable row level security",
            "disable row level security",
            "create index",
            "drop index",
            "create trigger",
            "drop trigger",
            "create or replace function",
            "grant ",
            "revoke ",
            "rename",
        )
        offenders = sorted(statement for statement in forbidden if statement in code)
        assert not offenders, (
            f"016's executable SQL contains {offenders}. It converts one column's type and "
            f"drops one default; policies, triggers, indexes, rows and every other column "
            f"are somebody else's business and are asserted unchanged by its own postflight."
        )

    def test_it_is_one_transaction(self):
        code = _executable(MIGRATION_016)
        assert "begin;" in code and "commit;" in code, (
            "016 is not wrapped in a transaction. A session must never see the default "
            "dropped without the type changed, or either without the comment."
        )
        assert code.index("begin;") < code.index("commit;")
        assert "rollback;" not in code

    def test_the_header_records_which_declaration_it_reconciles_and_what_the_app_writes(self):
        """The next reader must not have to rediscover which of the two shapes won."""
        header = _header_prose(MIGRATION_016)
        assert "001_strategy_architecture.sql" in header
        assert "006_reconcile_production_database.sql" in header
        assert "varchar(20)" in header
        assert "integer" in header
        assert "22p02" in header, (
            "016's header does not name the production error (22P02) it repairs"
        )
        assert "v1.0" in header, (
            "016's header does not state that the application writes a label like \"v1.0\", "
            "which is the whole reason the column cannot be an integer"
        )


# ---------------------------------------------------------------------------
# 3. The Python half: the label goes through unchanged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestTheLabelReachesTheRow:
    async def test_the_label_is_persisted_verbatim_and_is_still_a_string(self):
        sb = _Supabase(VARCHAR_COLUMN)
        await _create(sb, LABEL)
        stored = sb.inserted()[COLUMN]
        assert stored == LABEL, (
            f"create_backtest persisted {stored!r} instead of the caller's label {LABEL!r}"
        )
        assert isinstance(stored, str), (
            f"create_backtest coerced the label to {type(stored).__name__}. The value was "
            f"always right - the column's type was wrong, and coercing here would record a "
            f"version nothing ran against."
        )
        assert stored != 1 and stored != "1", (
            "the hardcoded 1 is back: every backtest would again be recorded against "
            '"version 1"'
        )

    async def test_two_labels_that_differ_only_after_the_dot_stay_distinct(self):
        """The failure any integer coercion produces, stated as a test.

        ``int("v1.0".lstrip("v").split(".")[0])`` is 1 for "v1.0", "v1.1" and "v1.9" alike,
        so three different immutable versions would be recorded as one.
        """
        first, second = _Supabase(VARCHAR_COLUMN), _Supabase(VARCHAR_COLUMN)
        await _create(first, "v1.0")
        await _create(second, "v1.9")
        assert first.inserted()[COLUMN] == "v1.0"
        assert second.inserted()[COLUMN] == "v1.9"
        assert first.inserted()[COLUMN] != second.inserted()[COLUMN]

    async def test_an_integer_version_column_is_what_refuses_the_insert(self):
        """Production before 016, reproduced: the write is rejected, not degraded.

        This is the condition CloudWatch logged. It is asserted here so that the fix cannot
        be mistaken for a value change: the same call, with the same label, succeeds against
        the reconciled column in the test above and fails against the integer column here.
        """
        sb = _Supabase(INTEGER_COLUMN)
        with pytest.raises(RuntimeError) as exc:
            await _create(sb, LABEL)
        message = str(exc.value)
        assert "22P02" in message, message
        assert "invalid input syntax for type integer" in message, message
        assert LABEL in message, message
        assert sb.inserts == [], "a refused insert must leave no row behind"

    async def test_the_reconciled_column_accepts_the_router_s_own_fallback_label(self):
        """``str(version_row.get("version") or "v1.0")`` - strategy_operations.py line 1462.

        Four characters against a 20-character column, so the label the router falls back to
        can always be stored. A label that did NOT fit would be a different bug, and the
        double raises 22001 for it rather than passing it silently.
        """
        sb = _Supabase(VARCHAR_COLUMN)
        row = await _create(sb, "v1.0")
        assert row[COLUMN] == "v1.0"
        assert len("v1.0") <= 20


@settings(max_examples=75, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    major=st.integers(min_value=1, max_value=999),
    minor=st.integers(min_value=0, max_value=999),
)
def test_every_label_the_router_can_produce_is_stored_verbatim(major, minor):
    """PROPERTY. For every version label the system can produce:

    1. ``create_backtest`` persists it **unchanged** - never an ordinal, never truncated;
    2. it fits the reconciled ``VARCHAR(20)`` column, so 016's target type is wide enough
       for the whole label space and not merely for the ``"v1.0"`` in the bug report;
    3. the integer column of ``migrations/006_reconcile_production_database.sql`` refuses
       it with ``22P02`` - so the failure was a property of the column, not of one value.

    The generator is constrained to the real input space: ``strategy_versions.version`` is
    written as ``v{major}.{minor}`` (001 line 25: 'e.g., "v1.0", "v1.1", "v2.0"'), and the
    router either copies that value or falls back to ``"v1.0"``. Generating arbitrary text
    would test PostgreSQL's varchar length rule rather than this system's labels.
    """
    label = f"v{major}.{minor}"

    accepted = _Supabase(VARCHAR_COLUMN)
    row = asyncio.run(_create(accepted, label))
    assert accepted.inserted()[COLUMN] == label
    assert row[COLUMN] == label
    assert len(label) <= 20

    refused = _Supabase(INTEGER_COLUMN)
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(_create(refused, label))
    assert "22P02" in str(exc.value)
