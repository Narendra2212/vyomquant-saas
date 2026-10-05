"""`order_watchdog` locks `positions` on a column that does not exist.

WHAT WAS MEASURED, AND WHERE IT CAME FROM
------------------------------------------
Unblocking the PR gate let `01-pr-check.yml`'s `database-tests` job run for the first time.
It reported three failures, two of them in `tests/test_position_delta_race_condition_fix.py`::

    psycopg2.errors.UndefinedColumn: column "symbol" does not exist
    [SQL: SELECT * FROM positions WHERE tenant_id = %(tenant_id)s AND symbol = %(symbol)s
          FOR UPDATE]

That test is a VERBATIM MIRROR of production code. `backend_app/backend/order_watchdog.py`
ran the identical statement at two call sites, in the `filled` branch and the
`partially_filled` branch of `_sync_order_status`. So the test was not broken; it was
correctly reporting that the production statement is invalid.

The schema settles it. `alembic/versions/d97ffff9c3bb_execution_schema_rebuild.py` declares
`positions` as exactly five columns::

    id, tenant_id, data (JSON), created_at, updated_at

There is no `symbol` column and never was. The payload lives inside `data`, which is the same
fact that made four statements in `transactional_execution_manager.py` use `json_extract` and
required the `json_text` dialect renderer.

WHY THIS MATTERED MORE THAN A FAILING TEST
-------------------------------------------
Both call sites are labelled "FIN-CRITICAL-002 FIX: Update position with row-level locking",
and both wrap the statement in::

    except Exception as lock_error:
        logger.warning(...)
        # Continue with update even if lock fails (non-blocking fallback)

So against PostgreSQL -- which is what production runs, and `main.py:383` starts this watchdog
unconditionally at startup -- the lock raised, was swallowed as a warning, and the code
proceeded to mutate the position WITHOUT the lock. The row-level locking that fix is named
after has never once been acquired in production.

WHAT THIS FILE DOES AND DOES NOT ESTABLISH
-------------------------------------------
It establishes that the column expression now resolves against a table with the real five
columns, and that no string literal under `backend_app` treats `symbol` as a `positions`
column any more. It does NOT establish that `FOR UPDATE` locks anything -- SQLite has no
`FOR UPDATE`, and `SELECT ... FOR UPDATE` matching zero rows succeeds while locking nothing
even on PostgreSQL. `test_position_delta_race_condition_fix.py` in the `database-tests` job is
what exercises the real lock, and its own docstring is already honest about that limit.

NOT FIXED BY RELAXING THE TESTS, DELIBERATELY. The two CI failures could have been made to
pass by deleting the `symbol` predicate from the test. That would have left the production
statement raising on every fill the watchdog detects.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest
from sqlalchemy import create_engine, text

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND_APP = REPO_ROOT / "backend_app"
MIGRATION = (
    BACKEND_APP / "alembic" / "versions" / "d97ffff9c3bb_execution_schema_rebuild.py"
)

#: Tables whose payload lives in a single JSON `data` column, so a bare column reference to
#: anything inside that payload is a defect. All three are declared in MIGRATION.
JSON_PAYLOAD_TABLES = ("positions", "orders", "fills")

#: Payload field names that have been written as bare SQL columns in this repository. Each is
#: a real past defect: `symbol` in order_watchdog (this file), `symbol`/`quantity` in
#: transactional_execution_manager (the json_extract portability fix).
PAYLOAD_FIELDS_MISTAKEN_FOR_COLUMNS = ("symbol", "quantity")


# ---------------------------------------------------------------------------
# The premise: what the table actually has
# ---------------------------------------------------------------------------

def _declared_columns(table: str) -> set[str]:
    """Column names from `op.create_table('<table>', sa.Column('x', ...), ...)`, via AST.

    Parsed rather than pattern-matched so a column added later is picked up automatically and
    a column mentioned in a comment is not picked up at all.
    """
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "create_table" or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == table):
            continue
        columns = set()
        for arg in node.args[1:]:
            if (
                isinstance(arg, ast.Call)
                and getattr(arg.func, "attr", None) == "Column"
                and arg.args
                and isinstance(arg.args[0], ast.Constant)
            ):
                columns.add(arg.args[0].value)
        return columns
    pytest.fail(f"{MIGRATION.name} declares no table named {table!r}")


def test_the_positions_table_is_five_columns_and_none_of_them_is_symbol() -> None:
    """The fact the whole file rests on, pinned so it cannot quietly change."""
    columns = _declared_columns("positions")

    assert columns == {"id", "tenant_id", "data", "created_at", "updated_at"}, (
        f"positions is declared with {sorted(columns)}. This file, and the fix in "
        "order_watchdog.py, assume the payload lives in `data`. If a real `symbol` column "
        "has since been added, the dialect renderer is no longer needed there."
    )
    assert "symbol" not in columns
    assert "data" in columns


# ---------------------------------------------------------------------------
# The sweep: no string literal makes this mistake again, in any module
# ---------------------------------------------------------------------------

def _sql_string_literals(tree: ast.Module) -> list[str]:
    """Every string constant in the module.

    AST constants ONLY, which excludes comments and therefore excludes the comments that
    EXPLAIN this defect -- in this file, in order_watchdog.py and in
    transactional_execution_manager.py. A text search for `positions` and `symbol` matches
    those explanations and reports a defect that is not there; this cannot.
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            # f-string: the literal halves carry the SQL, the interpolations carry the
            # rendered column expressions. Join the literal parts so `FROM positions` and a
            # bare `symbol =` in the same f-string are still seen together.
            out.append(
                "".join(
                    v.value
                    for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
            )
    return out


def _python_modules() -> list[pathlib.Path]:
    return sorted(
        p
        for p in BACKEND_APP.rglob("*.py")
        if "__pycache__" not in p.parts and "alembic" not in p.parts
    )


def test_no_sql_in_backend_app_treats_a_json_payload_field_as_a_column() -> None:
    """The generalised form of the defect, so a third module cannot acquire it.

    `transactional_execution_manager.py` had it with `json_extract` (wrong dialect, right
    idea). `order_watchdog.py` had it with a bare `symbol` (no idea at all). Both are the same
    mistake: addressing a field inside a JSON payload column as though it were a column.
    """
    violations: list[str] = []

    for module in _python_modules():
        try:
            tree = ast.parse(module.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue

        for sql in _sql_string_literals(tree):
            lowered = sql.lower()
            if "select" not in lowered and "update" not in lowered:
                continue
            for table in JSON_PAYLOAD_TABLES:
                if not re.search(rf"\b(from|update|into|join)\s+{table}\b", lowered):
                    continue
                for field in PAYLOAD_FIELDS_MISTAKEN_FOR_COLUMNS:
                    # A bare `field =` or `field IN`, i.e. used as a column. A bound
                    # parameter `:field` and a JSON extraction `data ->> 'field'` or
                    # `json_extract(data, '$.field')` are all fine and must not match.
                    bare = re.search(
                        rf"(?<![:'\"\w.]){field}\s*(=|<|>|\bin\b|\bis\b)", lowered
                    )
                    if bare and f"'$.{field}'" not in lowered and f"'{field}'" not in lowered:
                        violations.append(
                            f"{module.relative_to(REPO_ROOT)}: {table} queried with a bare "
                            f"{field!r} column -- {sql.strip()[:120]!r}"
                        )

    assert not violations, (
        "SQL is addressing a JSON payload field as though it were a column:\n\n"
        + "\n".join(f"  {v}" for v in sorted(set(violations)))
        + "\n\n`positions`, `orders` and `fills` each store their payload in one `data` JSON "
        "column (d97ffff9c3bb_execution_schema_rebuild.py). PostgreSQL answers "
        "`UndefinedColumn` for a bare reference to anything inside it. Render the access with "
        "`transactional_execution_manager.json_text(column, key, dialect)`, which emits "
        "`data ->> 'key'` for PostgreSQL and `json_extract(data, '$.key')` otherwise."
    )


def _sweep_violations(source: str) -> list[str]:
    """The sweep above, factored so a positive control can drive it over fabricated source."""
    tree = ast.parse(source)
    found = []
    for sql in _sql_string_literals(tree):
        lowered = sql.lower()
        if "select" not in lowered and "update" not in lowered:
            continue
        for table in JSON_PAYLOAD_TABLES:
            if not re.search(rf"\b(from|update|into|join)\s+{table}\b", lowered):
                continue
            for field in PAYLOAD_FIELDS_MISTAKEN_FOR_COLUMNS:
                bare = re.search(
                    rf"(?<![:'\"\w.]){field}\s*(=|<|>|\bin\b|\bis\b)", lowered
                )
                if bare and f"'$.{field}'" not in lowered and f"'{field}'" not in lowered:
                    found.append(f"{table}.{field}")
    return found


class TestTheSweepIsNotVacuous:
    """A sweep that cannot fail is worth nothing. These drive it over fabricated source.

    The first case is the EXACT statement `order_watchdog.py` shipped, so this is also the
    record of what the defect looked like.
    """

    def test_it_catches_the_statement_the_watchdog_actually_shipped(self) -> None:
        source = (
            'q = text("SELECT * FROM positions WHERE tenant_id = :tenant_id '
            'AND symbol = :symbol FOR UPDATE")'
        )
        assert _sweep_violations(source) == ["positions.symbol"]

    def test_it_catches_the_same_mistake_in_an_f_string(self) -> None:
        source = 'q = text(f"SELECT {col} FROM orders WHERE symbol = :symbol")'
        assert "orders.symbol" in _sweep_violations(source)

    def test_it_catches_quantity_too(self) -> None:
        source = 'q = text("UPDATE positions SET x = 1 WHERE quantity > :q")'
        assert "positions.quantity" in _sweep_violations(source)

    @pytest.mark.parametrize(
        "source",
        [
            # The PostgreSQL rendering.
            'q = text("SELECT * FROM positions WHERE data ->> \'symbol\' = :symbol")',
            # The SQLite rendering.
            "q = text(\"SELECT * FROM positions WHERE json_extract(data, '$.symbol') = :symbol\")",
            # A bound parameter of the same name is not a column reference.
            'q = text("SELECT * FROM positions WHERE tenant_id = :symbol")',
            # A different table that really does have the column.
            'q = text("SELECT * FROM paper_positions WHERE symbol = :symbol")',
            # Not SQL at all.
            'msg = "the symbol = the ticker, for positions"',
        ],
    )
    def test_it_does_not_fire_on_correct_sql(self, source: str) -> None:
        assert _sweep_violations(source) == [], (
            "the sweep flagged SQL that is correct, which would make it unusable as a gate"
        )


# ---------------------------------------------------------------------------
# The fix, executed: the predicate resolves against the real five columns
# ---------------------------------------------------------------------------

def _real_positions_table(engine) -> None:
    """A `positions` table with exactly the declared columns, and one row to match."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE positions ("
                "  id VARCHAR(36) NOT NULL PRIMARY KEY,"
                "  tenant_id VARCHAR(36),"
                "  data JSON,"
                "  created_at DATETIME,"
                "  updated_at DATETIME"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO positions (id, tenant_id, data) "
                "VALUES ('p1', 'test_tenant', '{\"symbol\": \"BTC/USDT\", \"quantity\": 2}')"
            )
        )


def test_the_old_watchdog_predicate_cannot_run_against_the_real_schema() -> None:
    """The defect, executed rather than described. This is the failing-first assertion.

    The statement is the one `order_watchdog.py` shipped, verbatim, minus `FOR UPDATE` --
    SQLite has no `FOR UPDATE`, and its absence is not what breaks this.
    """
    engine = create_engine("sqlite://")
    _real_positions_table(engine)

    with engine.connect() as conn:
        with pytest.raises(Exception) as excinfo:
            conn.execute(
                text(
                    "SELECT * FROM positions "
                    "WHERE tenant_id = :tenant_id AND symbol = :symbol"
                ),
                {"tenant_id": "test_tenant", "symbol": "BTC/USDT"},
            )

    assert "symbol" in str(excinfo.value), (
        "the old predicate was expected to fail on the missing `symbol` column; it failed "
        f"for some other reason instead: {excinfo.value}"
    )


def test_the_watchdog_lock_predicate_now_resolves_and_finds_the_row() -> None:
    """The fix, executed: the rendered predicate matches the seeded row by its payload symbol."""
    from backend_app.backend.order_watchdog import _position_lock_sql

    engine = create_engine("sqlite://")
    _real_positions_table(engine)

    sql = _position_lock_sql("sqlite")
    assert "FOR UPDATE" in sql, "the lock clause must survive the fix"

    # SQLite cannot parse FOR UPDATE; the clause is what PostgreSQL needs and what
    # test_position_delta_race_condition_fix.py exercises there. Strip it to prove the
    # PREDICATE resolves, which is the part that was broken.
    with engine.connect() as conn:
        rows = conn.execute(
            text(sql.replace("FOR UPDATE", "")),
            {"tenant_id": "test_tenant", "symbol": "BTC/USDT"},
        ).fetchall()

    assert len(rows) == 1, (
        "the rendered predicate must match the row whose data payload carries that symbol; "
        f"it matched {len(rows)} rows"
    )


def test_the_lock_statement_is_rendered_for_postgres_too() -> None:
    """Production is PostgreSQL, so the PostgreSQL rendering is the one that matters."""
    from backend_app.backend.order_watchdog import _position_lock_sql

    pg = _position_lock_sql("postgresql")
    assert "->>" in pg, f"PostgreSQL rendering must use ->>, got: {pg}"
    assert "json_extract" not in pg, (
        "json_extract is a SQLite function; PostgreSQL has no such function and this is the "
        "exact defect the json_text renderer exists to prevent"
    )
    assert "FOR UPDATE" in pg
    # The caller's bound parameters must still be the ones the statement expects.
    assert ":tenant_id" in pg and ":symbol" in pg
