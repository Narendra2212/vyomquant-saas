"""tests/test_db_alm01_baseline_refuses_on_populated_tables.py

DB-ALM-01 (P3) -- ``backend_app/alembic/versions/4ef23035a692_baseline.py`` has no
``down_revision``: it is the first thing ``alembic upgrade head`` runs against a
database Alembic has never stamped. Its ``upgrade()`` unconditionally
``DROP TABLE ... CASCADE``s ``orders``, ``positions``, ``fills``,
``transaction_records``, ``transaction_checkpoints``, ``transaction_rollbacks`` and
``idempotency_keys``. On a fresh database that is a no-op; on an EXISTING, unstamped
production database -- this platform's own parallel raw-SQL migration system
(``backend_app/migrations/*.sql``) already creates several of these tables with real
rows in them -- running it for the first time would silently destroy every one of them.

THE FIX
-------
``_refuse_if_any_target_table_has_rows`` checks every one of the seven tables for even
one row before anything is dropped, and raises -- having altered nothing -- if any of
them does, naming ``alembic stamp 4ef23035a692`` as the operator's actual next step.

WHAT THIS FILE PROVES
----------------------
Loaded by path (same convention as ``tests/test_verify_rls_script.py`` for
``scripts/``-style files that are not an importable package module), against a real,
disposable SQLite engine -- the guard takes only a DB-API connection/engine, not a full
Alembic migration context, so it is exercised directly without needing ``alembic``'s own
runner:

1. a target table that exists and holds one row makes the guard refuse, naming that
   table and leaving the row in place;
2. a target table that exists and is EMPTY does not trip the guard (negative control --
   this is not "refuse if the table exists", only "refuse if it has a row");
3. a target table that does not exist at all does not trip the guard either (the
   fresh-database case this revision was written for);
4. ``upgrade()`` itself calls the guard before issuing any DROP, by source inspection --
   proving the guard is actually wired into the function a real ``alembic upgrade``
   would run, not merely defined and never called.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

sa = pytest.importorskip("sqlalchemy")

REPO = Path(__file__).resolve().parents[1]
REVISION_FILE = REPO / "backend_app" / "alembic" / "versions" / "4ef23035a692_baseline.py"


def _load_revision():
    spec = importlib.util.spec_from_file_location("baseline_revision_under_test", REVISION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def revision():
    return _load_revision()


@pytest.fixture
def engine():
    eng = sa.create_engine("sqlite:///:memory:")
    yield eng
    eng.dispose()


def test_refuses_when_a_target_table_has_one_row(revision, engine):
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE orders (id TEXT PRIMARY KEY)"))
        conn.execute(sa.text("INSERT INTO orders (id) VALUES ('order-1')"))

    with engine.connect() as bind:
        with pytest.raises(RuntimeError, match="orders"):
            revision._refuse_if_any_target_table_has_rows(bind)

    with engine.connect() as conn:
        count = conn.execute(sa.text("SELECT count(*) FROM orders")).scalar()
    assert count == 1, "the guard must refuse without altering anything"


def test_does_not_refuse_when_the_target_table_exists_but_is_empty(revision, engine):
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE positions (id TEXT PRIMARY KEY)"))

    with engine.connect() as bind:
        revision._refuse_if_any_target_table_has_rows(bind)  # must not raise


def test_does_not_refuse_when_the_target_table_does_not_exist_at_all(revision, engine):
    """NEGATIVE CONTROL: the fresh-database case this revision was originally written
    for -- none of the seven tables exist yet.
    """
    with engine.connect() as bind:
        revision._refuse_if_any_target_table_has_rows(bind)  # must not raise


def test_names_every_populated_table_not_just_the_first(revision, engine):
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE orders (id TEXT PRIMARY KEY)"))
        conn.execute(sa.text("INSERT INTO orders (id) VALUES ('o1')"))
        conn.execute(sa.text("CREATE TABLE fills (id TEXT PRIMARY KEY)"))
        conn.execute(sa.text("INSERT INTO fills (id) VALUES ('f1')"))
        conn.execute(sa.text("CREATE TABLE positions (id TEXT PRIMARY KEY)"))  # empty

    with engine.connect() as bind:
        with pytest.raises(RuntimeError) as exc_info:
            revision._refuse_if_any_target_table_has_rows(bind)

    message = str(exc_info.value)
    assert "orders" in message
    assert "fills" in message
    assert "positions" not in message


def test_upgrade_calls_the_guard_before_any_drop_statement():
    """Source-inspection, not execution: ``upgrade()`` needs a real Alembic migration
    context (``op.get_bind()``) that this file does not construct -- what matters here
    is that the guard call is the FIRST statement in the function body, ahead of every
    ``op.execute(...)`` DROP.
    """
    tree = ast.parse(REVISION_FILE.read_text(encoding="utf-8"))
    upgrade_fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )

    call_order = []
    for node in ast.walk(upgrade_fn):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_refuse_if_any_target_table_has_rows":
                call_order.append("guard")
            elif isinstance(func, ast.Attribute) and func.attr == "execute":
                call_order.append("op.execute")

    assert "guard" in call_order, "upgrade() must call the guard"
    assert call_order.index("guard") < call_order.index("op.execute"), (
        "the guard must run before the first DROP, not after"
    )
