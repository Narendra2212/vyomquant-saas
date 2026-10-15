"""API-05 -- importing main.py no longer issues schema-mutating DDL.

THE DEFECT
    ``main.py`` called ``Base.metadata.create_all(bind=engine)`` unconditionally, at
    IMPORT time -- merely importing this module (the web server, a worker, a test
    collection run) attempted a schema-mutating DDL statement against whatever
    ``DATABASE_URL`` happened to resolve. Two real risks, not one:

    1. This is a THIRD schema-management mechanism alongside the hand-written numbered
       SQL migrations (``backend_app/migrations/*.sql``, which declare RLS, triggers,
       check constraints and grants) and Alembic. In any environment where the real
       migrations have not yet been applied, ``create_all`` would silently materialise
       whatever a bare SQLAlchemy model class declares -- a table that exists, but with
       none of the safety properties the real migration gives it.
    2. It ran as an import-time side effect, so a test collection run or a short-lived
       worker process issued DDL simply by importing ``backend_app.main``.

THE FIX
    The call (and its now-unused ``Base``/``engine`` imports) was removed. Migrations
    are the only source of truth for schema now.

WHAT THIS FILE PROVES
    * the literal call is gone from source;
    * ``Base``/``engine`` are no longer imported into ``main.py`` at all (so nothing
      could reintroduce the call without reintroducing the import first, which is the
      harder-to-miss half of a regression);
    * the second, config-gated (not import-time) occurrence in
      ``backend/state_persistence.py`` is UNCHANGED and deliberately out of scope -- it
      is inside ``DatabaseStateStore.__init__``, runs only when that class is actually
      constructed with ``use_database=True``, and that class has no caller anywhere in
      the tree. This file pins that it is still there and still gated, so a future
      change that starts constructing it does not inherit an assumption this fix never
      made about it.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _main_source() -> str:
    return (REPO_ROOT / "backend_app" / "main.py").read_text(encoding="utf-8")


def test_create_all_no_longer_appears_in_main_py():
    source = _main_source()
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_all"
    ]
    assert not calls, (
        "backend_app/main.py still calls .create_all(...) -- API-05's import-time "
        "schema-mutating DDL call was supposed to be removed"
    )


def test_base_and_engine_are_no_longer_imported_in_main_py():
    tree = ast.parse(_main_source())
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)
    assert "Base" not in imported_names, (
        "main.py still imports `Base` -- nothing in this module should need the "
        "declarative base now that create_all is gone"
    )
    assert "engine" not in imported_names, (
        "main.py still imports `engine` -- nothing in this module should need a raw "
        "SQLAlchemy engine now that create_all is gone"
    )
    # SessionLocal is a real, used dependency (the in-process watchdog/pnl recorder use
    # it) and must survive this change untouched.
    assert "SessionLocal" in imported_names


def test_the_config_gated_occurrence_in_state_persistence_is_unchanged_and_out_of_scope():
    from backend_app.backend import state_persistence

    source = inspect.getsource(state_persistence.DatabaseStateStore.__init__)
    assert "create_all" in source, (
        "DatabaseStateStore.__init__ no longer calls create_all -- if this was "
        "intentionally removed too, update this test; if not, something unexpected "
        "changed in a file API-05 did not touch"
    )
    assert "use_database" in source, (
        "the config-gate this test relies on being not-import-time is gone"
    )

    import backend_app.main as main_module

    assert not any(
        isinstance(getattr(main_module, name, None), state_persistence.DatabaseStateStore)
        for name in dir(main_module)
    ), "main.py now constructs a DatabaseStateStore at module scope -- that would make API-05's reasoning (this class has no caller) stale"
