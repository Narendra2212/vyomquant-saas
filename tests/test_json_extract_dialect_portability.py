"""
tests/test_json_extract_dialect_portability.py

The live fill/reconciliation path must not query PostgreSQL with SQLite-only SQL.

WHAT WAS WRONG
--------------
``backend_app/backend/transactional_execution_manager.py`` queried the ``positions`` table
through ``json_extract`` at four statements (five occurrences)::

    SELECT json_extract(data, '$.quantity') FROM positions
     WHERE json_extract(data, '$.symbol') = :symbol AND tenant_id = :tid     # :613
    SELECT id FROM positions WHERE json_extract(data, '$.symbol') = :symbol  # :663
    SELECT id, data FROM positions WHERE json_extract(data, '$.symbol') = …  # :705
    SELECT id, data FROM positions WHERE tenant_id = … AND json_extract(…)   # :1053

``json_extract`` is a **SQLite** function. PostgreSQL has no such function — the equivalent
is the ``->>`` operator (or ``jsonb_extract_path_text``). Production is PostgreSQL 17.6
(``ecs-task-definition-full.json`` supplies a ``postgresql://`` ``DATABASE_URL``, and
``backend_app/migrations/020_declare_pre_existing_tables.sql`` records the live catalogue as
"PostgreSQL 17.6, 71 base tables in ``public``"), so every one of those statements would
raise ``UndefinedFunction``.

The column itself is real and correctly addressed: ``orders``, ``positions`` and ``fills``
are each declared ``id / tenant_id / data JSON / created_at / updated_at`` by
``alembic/versions/d97ffff9c3bb_execution_schema_rebuild.py``. The defect is purely the
dialect of the accessor.

WHY THE TEST SUITE COULD NOT CATCH IT
-------------------------------------
``tests/conftest.py`` blanks ``DATABASE_URL`` unless ``AERORA_TEST_DATABASE_URL`` is set,
and ``.github/workflows/01-pr-check.yml`` states the reason in terms: the 11,000+ test lane
is measured against **SQLite**, where ``json_extract`` is the correct function. So the only
engine that could have reported this defect is the one the suite does not run on. This is
the same shape as the mock/Lua divergence in
``tests/test_idempotency_lua_prefix_agreement.py``: the substitute is correct, so the real
thing is never exercised.

SEVERITY, STATED ACCURATELY
---------------------------
This path cannot currently execute at all. ``TransactionalExecutionManager`` has exactly one
construction site — ``exchange_websocket_listener.py:343`` — and nothing starts that
listener (no entry in ``main.py``, ``worker.py``, ``workers/``, ``startup.sh``,
``docker-compose.yml`` or a ``Procfile``). So this is a landmine rather than a live
outage: it arms the moment the fill listener is wired up, which is the change that would
otherwise finally give live trading a durable ledger.

WHAT IS ASSERTED
----------------
1. No bare ``json_extract`` literal remains in the module — every JSON access goes through
   the one dialect-aware renderer.
2. That renderer emits ``->>`` for PostgreSQL and ``json_extract`` for SQLite.
3. It refuses a key that is not a plain identifier, so building SQL through it cannot become
   an injection seam.
4. The SQLite rendering still works, executed for real against a real SQLite table.
5. A repo-wide guard: no new module introduces a bare ``json_extract``.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path
from typing import List

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import transactional_execution_manager as tem

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "backend_app" / "backend" / "transactional_execution_manager.py"

#: The renderer every JSON access in that module must go through.
RENDERER = "json_text"


def _json_extract_sites(path: Path) -> List[int]:
    """Line numbers where ``path`` builds SQL with ``json_extract(``.

    Three things are deliberately NOT counted, because a guard that cannot tell code from
    prose is a guard somebody deletes:

    * comment lines — the fix documents the defective form so a reader learns what went
      wrong;
    * docstrings — ``json_text``'s own docstring states which spelling it emits;
    * the body of :data:`RENDERER` itself — that function is the ONE place the SQLite form
      is supposed to be produced, which is the entire point of routing through it.

    The function body is located through the AST rather than by matching text, so renaming
    or moving it does not quietly widen the exemption.
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - a module that does not parse is a worse failure
        return []

    exempt: set = set()
    docstring_lines: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == RENDERER:
            exempt.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        # Docstrings: a bare string expression statement.
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            docstring_lines.update(
                range(node.lineno, (node.end_lineno or node.lineno) + 1)
            )

    sites: List[int] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        if lineno in exempt or lineno in docstring_lines:
            continue
        if line.lstrip().startswith("#"):
            continue
        if re.search(r"json_extract\s*\(", line):
            sites.append(lineno)
    return sites


# ---------------------------------------------------------------------------
# 1. No bare json_extract remains
# ---------------------------------------------------------------------------

def test_the_module_contains_no_bare_sqlite_json_extract():
    """The defect, stated as a test.

    Matched against SQL string content only — the module's own prose explains the defect and
    must be allowed to name it, so the pattern requires the function-call shape
    ``json_extract(`` rather than the bare word.
    """
    offenders = _json_extract_sites(MODULE)
    assert not offenders, (
        f"{MODULE.name} still builds SQL with json_extract( at line(s) {offenders}. That "
        f"function does not exist in PostgreSQL, which is what production runs, so each of "
        f"those statements raises UndefinedFunction. Route the access through "
        f"{RENDERER}() instead."
    )


# ---------------------------------------------------------------------------
# 2 + 3. The renderer is dialect-correct and refuses a non-identifier key
# ---------------------------------------------------------------------------

def test_the_renderer_emits_postgres_syntax_for_postgres():
    rendered = tem.json_text("data", "symbol", "postgresql")
    assert "->>" in rendered, (
        f"the PostgreSQL rendering is {rendered!r}, which does not use the ->> operator"
    )
    assert "json_extract" not in rendered, (
        f"the PostgreSQL rendering still calls json_extract: {rendered!r}"
    )
    assert "symbol" in rendered and "data" in rendered


def test_the_renderer_emits_sqlite_syntax_for_sqlite():
    rendered = tem.json_text("data", "symbol", "sqlite")
    assert "json_extract" in rendered, (
        f"the SQLite rendering is {rendered!r}; json_extract is the correct function there "
        f"and the test lane runs on SQLite"
    )
    assert "->>" not in rendered


def test_an_unknown_dialect_falls_back_to_the_portable_sqlite_form():
    """A dialect this renderer has not been taught must not produce empty or broken SQL."""
    rendered = tem.json_text("data", "symbol", "mysql")
    assert rendered.strip(), "the renderer produced nothing for an unknown dialect"
    assert "symbol" in rendered


@pytest.mark.parametrize(
    "bad_key",
    ["sym'bol", "symbol; DROP TABLE positions", "a b", "", "sym\"bol", "$.symbol"],
)
def test_the_renderer_refuses_a_key_that_is_not_a_plain_identifier(bad_key: str):
    """It builds SQL by interpolation, so the key must be constrained.

    Every key in use today is a literal written in the module (``symbol``, ``quantity``),
    so this is defence rather than a live hole — but a renderer that interpolates into SQL
    and accepts anything is one refactor away from being handed a request value.
    """
    with pytest.raises(ValueError):
        tem.json_text("data", bad_key, "postgresql")


# ---------------------------------------------------------------------------
# 4. The SQLite rendering actually works, executed for real
# ---------------------------------------------------------------------------

def test_the_sqlite_rendering_executes_and_returns_the_value():
    """Not a string assertion: a real table, a real row, the real rendered SQL.

    This is the half that proves the renderer produces usable SQL rather than
    plausible-looking SQL. The PostgreSQL branch cannot be executed here — there is no
    server in this lane, which is the whole reason the defect survived — so that branch is
    asserted structurally above and is what `AERORA_TEST_DATABASE_URL` would exercise.
    """
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE positions ("
                " id VARCHAR(36) PRIMARY KEY,"
                " tenant_id VARCHAR(36),"
                " data JSON"
                ")"
            )
        )
        conn.execute(
            text("INSERT INTO positions (id, tenant_id, data) VALUES (:i, :t, :d)"),
            {
                "i": "pos-1",
                "t": "tenant-1",
                "d": '{"symbol": "BTC/USDT", "quantity": 0.5}',
            },
        )

        qty = tem.json_text("data", "quantity", "sqlite")
        sym = tem.json_text("data", "symbol", "sqlite")
        row = conn.execute(
            text(
                f"SELECT {qty} FROM positions "
                f"WHERE {sym} = :symbol AND tenant_id = :tid"
            ),
            {"symbol": "BTC/USDT", "tid": "tenant-1"},
        ).fetchone()

    assert row is not None, (
        "the rendered SQLite query matched no row, so the renderer's output does not "
        "address the data column the way the table stores it"
    )
    assert float(row[0]) == 0.5, (
        f"the rendered query returned {row[0]!r}, not the stored quantity 0.5"
    )


# ---------------------------------------------------------------------------
# 5. Repo-wide guard
# ---------------------------------------------------------------------------

def test_no_module_under_backend_app_builds_sql_with_json_extract():
    """So a second module cannot acquire the same defect.

    Scoped to ``backend_app`` and skipping comment lines, for the reasons given above.
    This file and the renderer's own definition are exempt: both name the function
    deliberately, one to document the defect and one to emit it for SQLite.
    """
    offenders: List[str] = []
    searched = 0
    for path in sorted((REPO_ROOT / "backend_app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        searched += 1
        for lineno in _json_extract_sites(path):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")

    assert searched, "the sweep found no modules at all, so it proves nothing"
    assert not offenders, (
        "SQLite-only json_extract( appears in SQL built at:\n  "
        + "\n  ".join(offenders)
        + "\nProduction is PostgreSQL, where that function does not exist."
    )
