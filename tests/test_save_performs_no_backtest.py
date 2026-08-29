# -*- coding: utf-8 -*-
"""
tests/test_save_performs_no_backtest.py

Saving is saving. Property 24, Requirements 22.1 and 22.2.

Spec: strategy-builder task 8.11.

* **Requirement 22.1.** "WHEN an author saves a strategy version, THE
  Strategy_Builder_API SHALL create no backtest record and SHALL start no backtest job."
* **Requirement 22.2.** "THE Strategy_Builder SHALL operate without depending on any
  backtest execution module."
* **Property 24.** "For all save operations: no backtest is executed as a side effect."

Why this file exists at all
--------------------------
``design.md`` -> Boundaries, item 1: "Save is not backtest." The separation is currently
held by nothing but the shape of the code, and the code that holds it is not obviously
separate: ``POST /api/strategy-operations/strategies/{id}/versions`` (the save) and
``POST /api/strategies/backtest`` (the execution) are both FastAPI routes, both routers
hosting them import ``backtest_runtime`` at module scope, and a save that "helpfully"
warmed a backtest would read like a feature in a diff. So the claim is asserted three
independent ways, because each one alone has a hole the other two cover:

1. **No record.** A real save through the real router, the real service, the real compiler
   and the real registry writes rows - and the tables and columns those rows land in are
   asserted, not the response. A write to any relation whose name mentions ``backtest``
   fails, as does a persisted column that does. ``strategy_versions.backtest_results``
   is a real column of ``strategy_versions`` in the pre-004 schema
   (``001_strategy_architecture.sql``, kept by ``003``), so "no backtest record" has a
   column-level meaning here as well as a table-level one - the row a save writes is the
   row that column lives on, and the fake would accept a write to it.
2. **No job, and no execution.** Every entry point that can start or record a backtest is
   armed with a tripwire that *records* the call and then raises a ``BaseException`` - the
   record is the assertion, and the raise only exists to stop the work before it reaches
   Redis, CCXT or a real engine. The ``BaseException`` matters because the save routes and
   the backtest route both wrap their bodies in ``except Exception``: an ``AssertionError``
   would be caught and re-reported as something else, whereas this escapes. The save must
   succeed with all of them armed. The tripwires are proved to bite by firing them from
   ``POST /api/strategies/backtest``, the endpoint whose whole purpose is to trip them - and
   that control asserts the *record*, because the shared test client is deliberately built
   ``raise_server_exceptions=False`` and renders anything escaping a handler as a 500.
3. **No dependency.** Two halves. Statically, the module-level import closure of the save
   path contains no backtest execution module, and the save-path functions reference no
   backtest identifier. Dynamically, an import recorder wrapped around a live save proves
   that nothing on the executed path *imports* one either - which is the half a static scan
   cannot make, because this repository defers imports into function bodies constantly.

What is deliberately NOT claimed
-------------------------------
* **The routers are not claimed to be backtest-free, because they are not.**
  ``routers/strategies.py`` and ``routers/strategy_operations.py`` each import
  ``backtest_runtime`` at module scope, and the second imports ``backtest_service`` too.
  That is a fact about two 4000-line modules hosting both surfaces, not a defect in the
  save path, and choosing a scope that happened to exclude it would make this file
  decorative. It is *pinned* instead: :class:`TestTheBacktestImportersArePinned` asserts the
  exact set of modules importing a backtest module, so a new importer - including one added
  to a save-path module - trips this test and gets read. What is asserted about the routers
  is narrower and true: the save *handlers* reference none of it.
* **``BacktestRuntime.load_version_plan`` is not a tripwire.** It loads a stored
  ``compiled_plan``; it executes nothing. Task 8.10
  (``tests/test_version_consumer_agreement.py``) is built on it being callable, and
  Requirement 22.3 requires both consumers to share it. A test that convicted the shared
  *loader* would be arguing against the design rather than for it. Only the five execution
  entry points, the two record-writing ones and the job queue are armed.
* **No PostgreSQL, so "no row" is a statement about a model.** Migrations 004-004e are
  unapplied in this environment, so both persistence shapes are exercised: the canonical
  one (constructed by hand) and the degraded one (which is what a save actually writes
  here). The degraded path must warn and keep working, never 500 - asserted directly.
* **The frontend half of Property 24 is not re-asserted here, because it already exists.**
  ``design.md`` -> Testing strategy states property 24 as "``StrategyBuilder.jsx`` imports
  nothing from the Backtester module tree", and that is task 3.17's
  ``algo22-terminal/tests/unit/builder.architecture.test.js``: it walks the JS import
  closure from the builder page, follows lazy ``import()`` forms, and records that the
  handoff is a route string plus an injected ``onBacktest`` prop rather than a module edge.
  Re-implementing a JS module resolver in Python would be a second, worse answer to a
  question already answered. What this file owns is the *server* half of the same property,
  which that file cannot see: the API creating no record, starting no job, and depending on
  no backtest module.
* **Nothing here proves the Backtester is correct**, or that a backtest launched
  *deliberately* works. This file's entire subject is what a save does *not* do.

Doubles, and where they come from
---------------------------------
Nothing is re-spelled. ``FakeDB``/``seeded_db``/``service_on``/``valid_graph`` come from
``tests/test_strategy_version_canonical_persistence.py`` (task 2.3) - that model enforces
the migration-004 column vocabulary, ``blueprint NOT NULL``, the three CHECK constraints
and an RLS-shaped refusal, and it logs every statement, which is what makes "which tables
did the save touch" answerable at all. The AST machinery (``iter_python_modules``,
``module_facts``, ``definition_use``, ``_walk_definitions``) comes from
``tests/test_compiler_architecture.py`` (task 2.9), so the architecture half reasons over
parsed syntax and never over grep - which matters more here than anywhere, because these
modules mention the backtester in prose constantly (``_insert_version_row``'s own comment
does). The training-branch doubles (``FakeSupabase``, ``model_graph``, ``window``) come from
``tests/test_training_service_admission.py`` (task 6.1), and the real-app client
(``app_and_client``, ``_Supabase``) from ``tests/security/test_builder_tenant_isolation.py``
(task 8.7). The only substitution anywhere is the database client and, on the ML branch,
the single bar-fetch boundary.
"""

import ast
import builtins
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple
from unittest import mock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.strategy_dag import registry as registry_module

# ── The AST machinery, from the phase-2 architecture gate ──────────────────
from tests.test_compiler_architecture import (  # noqa: E402 - shared machinery
    BACKEND_APP,
    REPO_ROOT,
    _walk_definitions,
    definition_use,
    iter_python_modules,
    module_facts,
    rel,
)

# ── The persistence model and graph builders, from task 2.3 ───────────────
from tests.test_strategy_version_canonical_persistence import (  # noqa: E402
    LEGACY_VERSION_COLUMNS,
    USER_A,
    FakeDB,
    graph_with_unfed_action,
    reg,  # noqa: F401 - module-scoped real registry, used as a fixture
    seeded_db,
    service_on,
    valid_graph,
)

# ── The training-branch doubles, from task 6.1 ────────────────────────────
from tests.test_training_service_admission import (  # noqa: E402 - shared doubles
    FakeSupabase,
    db,  # noqa: F401 - fixture
    default_hooks,  # noqa: F401 - autouse fixture
    forget_column_probe,  # noqa: F401 - autouse fixture
    handoffs,  # noqa: F401 - autouse fixture
    linear_graph,
    model_graph,
    owner,
    save,
    service,  # noqa: F401 - fixture
    window,  # noqa: F401 - fixture
)
from tests.test_training_service_admission import STRATEGY_ID as TRAINING_STRATEGY_ID

# ── The real app and its client, from task 8.7 ────────────────────────────
from tests.security.test_builder_tenant_isolation import (  # noqa: E402 - shared doubles
    OWNER_ID,
    _module_state,  # noqa: F401 - autouse fixture
    _user,
    _valid_save_body,
    app_and_client,  # noqa: F401 - fixture
    sb,  # noqa: F401 - fixture
)
from tests.security.test_builder_tenant_isolation import STRATEGY_ID as HTTP_STRATEGY_ID


# ═══════════════════════════════════════════════════════════════════════════
# 1. What "a Backtester execution module" is, and what may reach one
# ═══════════════════════════════════════════════════════════════════════════

#: Every module under ``backend_app/`` whose file name names the backtester. Discovered by
#: walking the tree, then pinned here, so a fourth one added tomorrow is *found* by the scan
#: and *reported* by the pin rather than escaping both.
EXPECTED_BACKTEST_MODULES: FrozenSet[str] = frozenset(
    {
        "backend_app/backend/backtest_runtime.py",
        "backend_app/backend/backtest_service.py",
        "backend_app/backend/backtesting_engine.py",
    }
)

#: The same three, by dotted name, for import-graph work.
BACKTEST_MODULE_NAMES: FrozenSet[str] = frozenset(
    {
        "backend_app.backend.backtest_runtime",
        "backend_app.backend.backtest_service",
        "backend_app.backend.backtesting_engine",
    }
)

#: Every ``(module, depth, backtest_module)`` import edge under ``backend_app/``, where
#: depth 0 is module scope and depth 1 is deferred into a function body.
#:
#: This is a *pin*, not a permission list. Two entries are the routers that host the
#: backtest endpoints alongside the builder ones; one is the optimization engine, which runs
#: walk-forward backtests by definition; two are ``backtest_runtime`` importing its own
#: collaborators. None is on the save path, which
#: :class:`TestTheSavePathImportsNoBacktester` establishes independently. If this set grows,
#: the new entry has to be read against Requirement 22.2 rather than merged.
EXPECTED_BACKTEST_IMPORTERS: FrozenSet[Tuple[str, int, str]] = frozenset(
    {
        (
            "backend_app/backend/backtest_runtime.py",
            0,
            "backend_app.backend.backtesting_engine",
        ),
        (
            "backend_app/backend/backtest_runtime.py",
            0,
            "backend_app.backend.backtest_service",
        ),
        (
            "backend_app/backend/optimization_engine.py",
            0,
            "backend_app.backend.backtest_runtime",
        ),
        ("backend_app/routers/strategies.py", 0, "backend_app.backend.backtest_runtime"),
        ("backend_app/routers/strategies.py", 1, "backend_app.backend.backtest_runtime"),
        (
            "backend_app/routers/strategy_operations.py",
            0,
            "backend_app.backend.backtest_service",
        ),
        (
            "backend_app/routers/strategy_operations.py",
            0,
            "backend_app.backend.backtest_runtime",
        ),
    }
)

#: The modules that *implement* a version save. Seeds for the import closure below.
#:
#: These are what ``POST .../versions`` and ``POST /api/strategies`` delegate to, and the
#: same ones ``dag_worker`` and a training job reach - the property SB-01 was about. The
#: routers are deliberately absent as *seeds*: a router is an HTTP surface hosting many
#: unrelated endpoints, so its closure says nothing about one handler. What the routers
#: contribute is checked function-by-function instead, in
#: :class:`TestTheSaveHandlersReferenceNoBacktester`.
SAVE_PATH_SEEDS: Tuple[str, ...] = (
    "backend_app.backend.strategy_service",
    "backend_app.backend.strategy_builder",
    "backend_app.backend.strategy_compiler",
    "backend_app.backend.strategy_dag",
    "backend_app.backend.strategy_dag.schema",
    "backend_app.backend.strategy_dag.validator",
    "backend_app.backend.strategy_dag.plan",
    "backend_app.backend.strategy_dag.registry",
    "backend_app.backend.registry_snapshot_service",
    "backend_app.backend.strategy_lifecycle",
    "backend_app.backend.ml_training_policy",
)

#: Closure floor. A walker that resolved nothing would satisfy "no backtester in the
#: closure" vacuously and perfectly.
MIN_SAVE_PATH_CLOSURE: int = 20

#: The functions a save actually runs, by ``module::qualname``. Each is asserted to name no
#: backtest identifier in its own body - the assertion that survives the two routers
#: legitimately importing ``backtest_runtime`` at module scope.
SAVE_PATH_FUNCTIONS: Tuple[str, ...] = (
    "backend_app/routers/strategy_operations.py::save_strategy_version",
    "backend_app/backend/strategy_service.py::StrategyService.create_version",
    "backend_app/backend/strategy_service.py::StrategyService.create_version_and_maybe_train",
    "backend_app/backend/strategy_service.py::StrategyService._insert_version_row",
    "backend_app/backend/strategy_builder.py::compile_version",
    "backend_app/backend/strategy_compiler.py::StrategyCompiler.compile_plan",
    "backend_app/routers/strategies.py::create_strategy",
    "backend_app/routers/strategies.py::_compile_payload",
    "backend_app/routers/strategies.py::_dag_fields",
)

#: Relations that hold backtest records in this schema. ``strategy_backtests`` is what
#: ``backtest_service`` writes; ``backtest_jobs`` is the Redis stream name, kept here so a
#: table of that name would be caught too.
BACKTEST_RELATIONS: FrozenSet[str] = frozenset({"strategy_backtests", "backtest_jobs"})

#: The only relations a version save is permitted to touch. ``block_registry_snapshots`` is
#: the provenance record (task 3.2); ``training_jobs`` and ``profiles`` appear only on the
#: ML branch; ``strategy_audit_log`` is the Requirement 9.8 audit record.
SAVE_PATH_RELATIONS: FrozenSet[str] = frozenset(
    {
        "strategies",
        "strategy_versions",
        "block_registry_snapshots",
        "training_jobs",
        "profiles",
        "strategy_audit_log",
    }
)


def _mentions_backtest(text: Any) -> bool:
    return "backtest" in str(text).lower()


def _backtest_keys(payload: Any) -> List[str]:
    """Every key at any depth whose name names the backtester."""
    found: List[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if _mentions_backtest(key):
                found.append(str(key))
            found.extend(_backtest_keys(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found.extend(_backtest_keys(item))
    return found


# ═══════════════════════════════════════════════════════════════════════════
# 2. The static import graph
# ═══════════════════════════════════════════════════════════════════════════


def _import_targets(node: ast.AST, modname: str, is_package: bool) -> List[str]:
    """Dotted module names one ``import`` statement resolves to.

    ``from x import y`` yields both ``x`` and ``x.y``: the second is what catches
    ``from backend_app.backend import backtest_runtime``, which names a module through a
    ``fromlist`` rather than through ``node.module``.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []

    base = modname if is_package else modname.rsplit(".", 1)[0]
    if node.level:
        for _ in range(node.level - 1):
            base = base.rsplit(".", 1)[0]
        module = f"{base}.{node.module}" if node.module else base
    else:
        module = node.module or ""
    if not module:
        return []
    return [module] + [f"{module}.{alias.name}" for alias in node.names]


def _module_path(dotted: str) -> Optional[Path]:
    candidate = REPO_ROOT.joinpath(*dotted.split("."))
    if candidate.with_suffix(".py").is_file():
        return candidate.with_suffix(".py")
    if (candidate / "__init__.py").is_file():
        return candidate / "__init__.py"
    return None


def _scoped_imports(path: Path, modname: str) -> List[Tuple[int, str, int]]:
    """``(depth, dotted_target, lineno)`` for every import in one module.

    ``depth 0`` is module scope, including inside a module-level ``if``/``try``/``with`` -
    a conditional import still runs at import time. ``depth 1+`` is deferred into a
    function or class body.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    is_package = path.name == "__init__.py"
    out: List[Tuple[int, str, int]] = []

    def walk(body: List[ast.stmt], depth: int) -> None:
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in _import_targets(node, modname, is_package):
                    out.append((depth, target, node.lineno))
            transparent = isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith))
            child_depth = depth if transparent else depth + 1
            for field in ("body", "orelse", "finalbody"):
                sub = getattr(node, field, None)
                if isinstance(sub, list):
                    walk([s for s in sub if isinstance(s, ast.stmt)], child_depth)
            for handler in getattr(node, "handlers", []) or []:
                walk(handler.body, child_depth)

    walk(tree.body, 0)
    return out


def import_closure(
    seeds: Tuple[str, ...], *, include_deferred: bool = False
) -> Dict[str, str]:
    """Modules reachable from ``seeds`` by import, mapped to the edge that reached them.

    ``include_deferred=False`` follows only module-scope imports: exactly the set of
    modules that enter the process when the save path is imported.
    """
    reached: Dict[str, str] = {}
    stack: List[Tuple[str, str]] = [(seed, "(seed)") for seed in seeds]

    while stack:
        dotted, via = stack.pop()
        if dotted in reached:
            continue
        path = _module_path(dotted)
        if path is None:
            continue
        reached[dotted] = via
        for depth, target, line in _scoped_imports(path, dotted):
            if depth and not include_deferred:
                continue
            if target.startswith("backend_app") and target not in reached:
                stack.append((target, f"{dotted}:{line}"))

    return reached


def backtest_importers() -> Set[Tuple[str, int, str]]:
    """Every ``(module, depth, backtest_module)`` import edge under ``backend_app/``."""
    found: Set[Tuple[str, int, str]] = set()
    for path in iter_python_modules(BACKEND_APP):
        dotted = rel(path)[: -len(".py")].replace("/", ".")
        for depth, target, _line in _scoped_imports(path, dotted):
            if target in BACKTEST_MODULE_NAMES:
                found.add((rel(path), min(depth, 1), target))
    return found


def named_definition(key: str) -> Tuple[ast.AST, Any]:
    """``(node, module_facts)`` for a ``path/to/module.py::Qual.name`` key."""
    module_rel, qualname = key.split("::")
    path = REPO_ROOT / module_rel
    assert path.is_file(), f"{module_rel} does not exist"
    facts = module_facts(path)
    for node, found in _walk_definitions(facts.tree):
        if found == qualname:
            return node, facts
    raise AssertionError(f"{qualname} is not defined in {module_rel}")


# ═══════════════════════════════════════════════════════════════════════════
# 3. The tripwires: every way a backtest can be started or recorded
# ═══════════════════════════════════════════════════════════════════════════


class BacktestStarted(BaseException):
    """Raised by a tripwire.

    A ``BaseException`` on purpose. ``routers/strategies.py::create_strategy`` wraps its
    whole body in ``except Exception`` and re-raises a 500, and ``POST /backtest`` wraps its
    enqueue in ``except Exception`` and *falls back to running the backtest synchronously*.
    An ``AssertionError`` would be swallowed by both and reported as something else. This
    escapes them - and the call is recorded before it is raised either way, so the record is
    the assertion and the raise only exists to stop the work.
    """


#: ``(module, attribute)`` for every entry point that starts or records a backtest.
#:
#: The two record-writing ones are included because Requirement 22.1 has two clauses -
#: "create no backtest record" and "start no backtest job" - and ``create_backtest`` is the
#: one function in this codebase that inserts into ``strategy_backtests``.
TRIPWIRE_TARGETS: Tuple[Tuple[str, str], ...] = (
    # execution
    ("backend_app.backend.backtest_runtime", "get_backtest_runtime"),
    ("backend_app.backend.backtest_runtime", "BacktestRuntime.run_backtest"),
    ("backend_app.backend.backtest_runtime", "BacktestRuntime.run_version_backtest"),
    ("backend_app.backend.backtesting_engine", "BacktestEngine.run_backtest_async"),
    ("backend_app.routers.strategies", "backtest_internal"),
    # records
    ("backend_app.backend.backtest_service", "get_backtest_service"),
    ("backend_app.backend.backtest_service", "BacktestService.create_backtest"),
    # the job queue
    ("backend_app.core.event_bus", "publish_backtest_job"),
    ("backend_app.worker", "_write_status"),
    # The bindings the two routers took at import time: patching the origin module does not
    # reach a name already bound into a router's namespace by `from ... import ...`.
    ("backend_app.routers.strategies", "get_backtest_runtime"),
    ("backend_app.routers.strategy_operations", "get_backtest_runtime"),
    ("backend_app.routers.strategy_operations", "get_backtest_service"),
)

#: Floor. If fewer than this many tripwires arm, the arming is broken and every "nothing
#: fired" assertion below is vacuous.
MIN_ARMED_TRIPWIRES: int = 10


def _resolve(dotted_module: str, attribute: str) -> Tuple[Any, str]:
    """``(owner, attribute_name)`` for ``"Class.method"`` or a plain module attribute."""
    import importlib

    owner: Any = importlib.import_module(dotted_module)
    parts = attribute.split(".")
    for part in parts[:-1]:
        owner = getattr(owner, part)
    return owner, parts[-1]


@contextmanager
def armed_tripwires():
    """Arm every backtest entry point. Yields the list of calls that were made.

    Nothing is mocked *away*: each target is replaced by something that appends to the
    record and then raises. Tests assert the record is empty, which holds even if some
    handler swallowed the raise.
    """
    fired: List[str] = []
    armed: List[str] = []

    with ExitStack() as stack:
        for dotted_module, attribute in TRIPWIRE_TARGETS:
            try:
                owner, name = _resolve(dotted_module, attribute)
            except (ImportError, AttributeError):  # pragma: no cover - defensive
                continue

            label = f"{dotted_module}.{attribute}"

            def trip(*_args, _label=label, **_kwargs):
                fired.append(_label)
                raise BacktestStarted(_label)

            stack.enter_context(mock.patch.object(owner, name, trip))
            armed.append(label)

        assert len(armed) >= MIN_ARMED_TRIPWIRES, (
            f"only {len(armed)} tripwires armed ({armed}); 'no backtest ran' would be a "
            "vacuous claim"
        )
        yield fired


class ImportRecorder:
    """Records every module import performed inside its ``with`` block.

    Two independent mechanisms, because neither alone is enough:

    * ``builtins.__import__`` is replaced, so an ``import`` *statement* is recorded even
      when the module is already in ``sys.modules`` and the statement is a cache hit. This
      is the mechanism that matters, because by the time this file has armed its tripwires
      the backtest modules are necessarily loaded.
    * the ``sys.modules`` delta, which catches ``importlib.import_module`` - that call does
      not go through ``builtins.__import__`` - for anything not yet loaded.

    Relative imports are recorded by their unresolved suffix; ``backend_app`` uses absolute
    imports throughout, so nothing checked here is missed by that.
    """

    def __init__(self) -> None:
        self.statements: List[str] = []
        self.fresh: Set[str] = set()
        self._before: Set[str] = set()
        self._real = builtins.__import__

    def __enter__(self) -> "ImportRecorder":
        self._real = builtins.__import__
        self._before = set(sys.modules)

        def hook(name, globals=None, locals=None, fromlist=(), level=0):
            self.statements.append(str(name))
            for item in fromlist or ():
                self.statements.append(f"{name}.{item}")
            return self._real(name, globals, locals, fromlist, level)

        builtins.__import__ = hook
        return self

    def __exit__(self, *_exc) -> None:
        builtins.__import__ = self._real
        self.fresh = set(sys.modules) - self._before

    @property
    def backtest_hits(self) -> List[str]:
        return sorted(
            {name for name in self.statements if _mentions_backtest(name)}
            | {name for name in self.fresh if _mentions_backtest(name)}
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. Small helpers
# ═══════════════════════════════════════════════════════════════════════════

_REGISTRY_CACHE: Dict[str, Any] = {}


def cached_registry():
    """The real assembled registry, once per process. Assembly is not free."""
    if "reg" not in _REGISTRY_CACHE:
        _REGISTRY_CACHE["reg"] = registry_module.build_registry()
    return _REGISTRY_CACHE["reg"]


@pytest.fixture(autouse=True)
def _forget_snapshot_state():
    """The registry-snapshot verdict is process-cached; no test may inherit another's."""
    from backend_app.backend.registry_snapshot_service import (
        reset_registry_snapshot_state,
    )

    reset_registry_snapshot_state()
    yield
    reset_registry_snapshot_state()


def touched_relations(db_double: FakeDB) -> Set[str]:
    return {table for table, _op, _cols in db_double.statements}


def written_relations(db_double: FakeDB) -> Set[str]:
    return {
        table
        for table, op, _cols in db_double.statements
        if op in {"insert", "update", "upsert", "delete"}
    }


def assert_no_backtest_relation(names, where: str) -> None:
    offenders = sorted(name for name in names if _mentions_backtest(name))
    assert offenders == [], f"{where} touched backtest relation(s) {offenders}"


def assert_no_backtest_payload(payload: Any, where: str) -> None:
    keys = _backtest_keys(payload)
    assert keys == [], f"{where} carries backtest key(s) {keys}"


def _legacy_save_body() -> Dict[str, Any]:
    """The body ``StrategyBuilder.jsx``'s ``buildSavePayload`` sends to ``POST /api/strategies``.

    Kept faithful to the client on purpose: ``graph_json`` carries the canonical envelope and
    ``nodes``/``edges`` are the flattened copy the router has always accepted, because
    ``schema.load_graph`` picks the envelope first and a test that sent only the flat pair
    would be exercising the version 1 reader instead of the save (which is asserted
    separately, by name). ``symbol`` and ``timeframe`` are along for the ride exactly as the
    client sends them; the router reads market identity from the DATA node regardless (SB-06).
    """
    graph = _valid_save_body()["blueprint"]
    data_params: Dict[str, Any] = next(
        (
            node.get("params", {})
            for node in graph["nodes"]
            if str(node.get("category", "")).upper() == "DATA"
        ),
        {},
    )
    return {
        "name": "no backtest here",
        "graph_json": graph,
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "symbol": data_params.get("symbol"),
        "timeframe": data_params.get("timeframe"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 5. The vocabulary this file reasons over is real
# ═══════════════════════════════════════════════════════════════════════════


class TestTheBacktesterIsFoundAndPinned:
    """Before asserting an absence, establish that the thing looked for exists."""

    def test_the_backtest_modules_are_exactly_the_pinned_three(self):
        discovered = {
            rel(path)
            for path in iter_python_modules(BACKEND_APP)
            if "backtest" in path.stem.lower()
        }
        assert discovered == set(EXPECTED_BACKTEST_MODULES), (
            "the set of Backtester execution modules changed; a new one has to be read "
            f"against Requirement 22.2 before this pin moves. found={sorted(discovered)}"
        )

    def test_every_backtest_module_resolves_by_dotted_name(self):
        for dotted in sorted(BACKTEST_MODULE_NAMES):
            path = _module_path(dotted)
            assert path is not None, dotted
            assert rel(path) in EXPECTED_BACKTEST_MODULES

    def test_the_entry_points_exist_where_the_tripwires_expect_them(self):
        """Every tripwire target resolves. A stale target arms nothing and proves nothing."""
        for dotted_module, attribute in TRIPWIRE_TARGETS:
            owner, name = _resolve(dotted_module, attribute)
            assert hasattr(owner, name), f"{dotted_module}.{attribute} is gone"
            assert callable(getattr(owner, name)), f"{dotted_module}.{attribute}"

    def test_the_shared_plan_loader_is_not_treated_as_execution(self):
        """``load_version_plan`` loads; it does not run. Requirement 22.3 depends on it.

        Convicting the loader would put this file in opposition to task 8.10, which requires
        *both* consumers to reach the same stored plan through it.
        """
        from backend_app.backend.backtest_runtime import BacktestRuntime

        assert hasattr(BacktestRuntime, "load_version_plan")
        armed = {f"{module}.{attr}" for module, attr in TRIPWIRE_TARGETS}
        assert not any("load_version_plan" in name for name in armed)


class TestTheBacktestImportersArePinned:
    """The exact set of modules importing a Backtester execution module.

    Pinned rather than forbidden, because two of the entries are the routers that host the
    backtest endpoints themselves. What this catches is *growth*: a save-path module, a
    ``strategy_dag`` module or a new helper acquiring such an import shows up as a set
    difference and has to be justified.
    """

    def test_the_importer_set_has_not_grown(self):
        found = backtest_importers()
        added = found - set(EXPECTED_BACKTEST_IMPORTERS)
        removed = set(EXPECTED_BACKTEST_IMPORTERS) - found
        assert added == set(), (
            "a new module imports a Backtester execution module; check it against "
            f"Requirement 22.2: {sorted(added)}"
        )
        assert removed == set(), (
            f"a pinned backtest import is gone - shrink the pin deliberately: {sorted(removed)}"
        )

    def test_no_save_path_seed_is_an_importer(self):
        importer_modules = {module for module, _depth, _target in backtest_importers()}
        seeds = {
            rel(path)
            for path in (_module_path(seed) for seed in SAVE_PATH_SEEDS)
            if path is not None
        }
        assert seeds, "no save-path seed resolved"
        assert seeds & importer_modules == set()

    def test_the_scan_finds_the_importers_it_is_supposed_to(self):
        """Non-vacuity: the scanner sees a known module-scope import."""
        found = backtest_importers()
        assert (
            "backend_app/backend/optimization_engine.py",
            0,
            "backend_app.backend.backtest_runtime",
        ) in found
        assert len(found) >= 5


# ═══════════════════════════════════════════════════════════════════════════
# 6. Requirement 22.2, statically: the save path's import closure
# ═══════════════════════════════════════════════════════════════════════════


class TestTheSavePathImportsNoBacktester:
    """Requirement 22.2. The module-level closure of the save path, walked.

    "Operate without depending on any backtest execution module" is a statement about a
    dependency graph, so a graph is what is computed: start at the modules that implement a
    save, follow every module-scope import, and look at what arrived.
    """

    def test_the_closure_contains_no_backtest_module(self):
        closure = import_closure(SAVE_PATH_SEEDS)
        offenders = sorted(
            f"{name} (reached from {closure[name]})"
            for name in closure
            if _mentions_backtest(name)
        )
        assert offenders == [], (
            "the save path depends on a Backtester execution module: "
            + "; ".join(offenders)
        )

    def test_the_closure_reaches_no_router_and_no_optimization_engine(self):
        """The hops that *would* reach the backtester, asserted absent by name.

        Both routers and ``optimization_engine`` import a backtest module at module scope,
        so any module-scope edge from the save path into one of the three would already fail
        the assertion above. Naming them separately says *why* the closure is clean, and
        fails readably when a layering inversion is introduced.
        """
        closure = import_closure(SAVE_PATH_SEEDS)
        offenders = sorted(
            f"{name} (reached from {closure[name]})"
            for name in closure
            if name.startswith("backend_app.routers")
            or name == "backend_app.backend.optimization_engine"
        )
        assert offenders == [], offenders

    def test_the_walker_is_not_returning_an_empty_graph(self):
        closure = import_closure(SAVE_PATH_SEEDS)
        assert len(closure) >= MIN_SAVE_PATH_CLOSURE, (
            f"only {len(closure)} modules in the save-path closure; the walker is broken "
            "and the assertions above pass vacuously"
        )
        for seed in SAVE_PATH_SEEDS:
            assert seed in closure, seed
        assert "backend_app.backend.strategy_dag.plan" in closure

    def test_the_walker_finds_the_backtester_when_it_is_actually_there(self):
        """The guard bites. Seeded at a real consumer, the same walker reports the hits."""
        closure = import_closure(("backend_app.backend.optimization_engine",))
        assert "backend_app.backend.backtest_runtime" in closure
        # And transitively, through backtest_runtime's own module-scope imports.
        assert "backend_app.backend.backtesting_engine" in closure
        assert "backend_app.backend.backtest_service" in closure

    def test_no_deferred_import_inside_the_closure_names_a_backtest_module(self):
        """Function-local imports too: a lazy import is still a dependency.

        Scoped to the closure's own modules rather than to what they can transitively reach,
        because the one edge out of the closure is
        ``deployment_binding._pipeline_timeframes`` deliberately reusing the router's
        timeframe intersection (task 7.2) - which is not on the save path, and is pinned by
        the next test rather than hidden by this one.
        """
        offenders: List[str] = []
        for dotted in import_closure(SAVE_PATH_SEEDS):
            path = _module_path(dotted)
            if path is None:  # pragma: no cover - closure members always resolve
                continue
            for depth, target, line in _scoped_imports(path, dotted):
                if depth and target in BACKTEST_MODULE_NAMES:
                    offenders.append(f"{rel(path)}:{line} -> {target}")
        assert offenders == [], offenders

    def test_the_one_edge_out_of_the_closure_is_pinned_and_off_the_save_path(self):
        """``deployment_binding`` defers an import of a router, and nothing else does.

        That router imports ``backtest_runtime``, so the edge is worth knowing about. It
        lives inside a helper the *deploy* path calls to reuse the published timeframe
        intersection; no save function reaches it. Pinned so it cannot quietly become a
        module-scope import, and so a second such edge is a failure rather than a surprise.
        """
        deferred: List[Tuple[str, str, int]] = []
        for dotted in import_closure(SAVE_PATH_SEEDS):
            path = _module_path(dotted)
            if path is None:  # pragma: no cover
                continue
            for depth, target, line in _scoped_imports(path, dotted):
                if target.startswith("backend_app.routers"):
                    assert depth > 0, (
                        f"{rel(path)}:{line} imports a router at module scope, which puts "
                        "the whole router - backtest imports included - in the save path"
                    )
                    deferred.append((rel(path), target, line))

        assert {(module, target) for module, target, _line in deferred} == {
            (
                "backend_app/backend/deployment_binding.py",
                "backend_app.routers.strategy_operations",
            ),
            (
                "backend_app/backend/deployment_binding.py",
                "backend_app.routers.strategy_operations._pipeline_timeframes",
            ),
        }, sorted(deferred)


class TestTheSaveHandlersReferenceNoBacktester:
    """The narrower claim that survives the routers: the save *functions* are clean.

    Read from the AST, so prose does not count. ``_insert_version_row``'s own comment
    explains that the legacy column exists because "the runtime and the backtester need" the
    plan; a grep-based version of this test would fail on that sentence, and the obvious fix
    would be to delete a true comment.
    """

    @pytest.mark.parametrize("key", SAVE_PATH_FUNCTIONS)
    def test_the_function_names_no_backtest_identifier(self, key: str):
        node, _facts = named_definition(key)
        use = definition_use(node)
        offenders = sorted(
            name
            for name in (use.identifiers | use.called | use.imported_modules)
            if _mentions_backtest(name)
        )
        assert offenders == [], f"{key} references {offenders}"

    @pytest.mark.parametrize("key", SAVE_PATH_FUNCTIONS)
    def test_the_function_imports_no_backtest_module(self, key: str):
        node, _facts = named_definition(key)
        module_rel = key.split("::")[0]
        dotted = module_rel[: -len(".py")].replace("/", ".")
        offenders: List[str] = []
        for child in ast.walk(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for target in _import_targets(child, dotted, False):
                    if _mentions_backtest(target):
                        offenders.append(f"{child.lineno}: {target}")
        assert offenders == [], f"{key} imports {offenders}"

    def test_the_scan_bites_on_a_function_that_does_reach_the_backtester(self):
        """Non-vacuity, against production code rather than a synthetic sample.

        ``walk_forward_optimization`` is the endpoint that genuinely runs backtests. The same
        reader applied to it must convict it, or the parametrized tests above measure nothing.
        """
        node, _facts = named_definition(
            "backend_app/routers/strategies.py::walk_forward_optimization"
        )
        use = definition_use(node)
        names = use.identifiers | use.called | use.imported_modules
        assert [name for name in names if _mentions_backtest(name)] != []

    def test_every_pinned_save_function_still_exists(self):
        """A renamed handler must break this file, not silently empty it."""
        for key in SAVE_PATH_FUNCTIONS:
            node, _facts = named_definition(key)
            assert isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ), key


# ═══════════════════════════════════════════════════════════════════════════
# 7. Requirement 22.1: the service-level save writes no backtest record
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_save_touches_only_the_version_relations(reg):
    """Requirement 22.1, first clause, on the statement log.

    ``FakeDB`` records every statement it is asked to run, so "no backtest record" is
    answered by what the save asked the database to do rather than by what it returned.
    """
    db_double, strategy_id = seeded_db(USER_A)
    service_under_test = service_on(db_double)

    with armed_tripwires() as fired:
        result = await service_under_test.create_version(
            USER_A, strategy_id, valid_graph(reg), registry=reg
        )

    assert result["dag_hash"]
    assert len(db_double.versions) == 1

    assert_no_backtest_relation(touched_relations(db_double), "the save")
    assert touched_relations(db_double) <= SAVE_PATH_RELATIONS, touched_relations(db_double)
    assert written_relations(db_double) == {
        "strategies",
        "strategy_versions",
        "block_registry_snapshots",
    }
    assert fired == []


@pytest.mark.asyncio
async def test_the_persisted_version_row_carries_no_backtest_column(reg):
    """``strategy_versions.backtest_results`` exists in this schema and stays empty.

    The column is real - it is in ``LEGACY_VERSION_COLUMNS``, so the fake would accept a
    write to it - which is what makes this an assertion rather than a tautology.
    """
    assert "backtest_results" in LEGACY_VERSION_COLUMNS

    db_double, strategy_id = seeded_db(USER_A)
    service_under_test = service_on(db_double)
    await service_under_test.create_version(
        USER_A, strategy_id, valid_graph(reg), registry=reg
    )

    row = db_double.versions[0]
    assert "backtest_results" not in row
    assert_no_backtest_payload(row, "the persisted version row")
    # Including inside the two documents, at any depth.
    assert_no_backtest_payload(row["graph_json"], "graph_json")
    assert_no_backtest_payload(row["compiled_plan"], "compiled_plan")


@pytest.mark.asyncio
async def test_the_strategy_update_carries_no_backtest_metric(reg):
    """The save's follow-up write to ``strategies`` names no backtest column either.

    ``strategies`` has no ``backtest_*`` column in any migration - which is the point:
    ``STRATEGY_COLUMNS`` in the borrowed model is that exact vocabulary and
    ``_assert_columns`` raises ``42703`` for anything outside it, so a save that decided to
    stamp a backtest metric onto the strategy row would fail loudly rather than pass
    quietly. Asserted directly all the same, because a column list is easier to widen than a
    test is to delete: the *only* thing this write is permitted to move is
    ``current_version`` and ``updated_at``.
    """
    db_double, strategy_id = seeded_db(USER_A)
    service_under_test = service_on(db_double)
    await service_under_test.create_version(
        USER_A, strategy_id, valid_graph(reg), registry=reg
    )

    updates = [
        columns
        for table, op, columns in db_double.statements
        if table == "strategies" and op == "update"
    ]
    assert updates, "the save did not repoint current_version"
    for columns in updates:
        assert_no_backtest_relation(columns, "a strategies update")
        assert set(columns) == {"current_version", "updated_at"}, columns

    assert_no_backtest_payload(db_double.stores["strategies"][0], "the strategy row")


@pytest.mark.asyncio
async def test_a_save_against_the_unapplied_migration_still_starts_no_backtest(reg, caplog):
    """Migrations 004-004e are unapplied here, so this is the *normal* shape locally.

    The degraded write puts the plan in ``execution_graph`` and warns. It must not reach for
    a backtest to fill in what it could not persist, and it must not fail.
    """
    db_double, strategy_id = seeded_db(USER_A, canonical_columns=False)
    service_under_test = service_on(db_double)

    with caplog.at_level("WARNING", logger="StrategyService"):
        with armed_tripwires() as fired:
            result = await service_under_test.create_version(
                USER_A, strategy_id, valid_graph(reg), registry=reg
            )

    assert result["canonical_persisted"] is False
    assert fired == []
    assert len(db_double.versions) == 1
    row = db_double.versions[0]
    assert set(row) <= LEGACY_VERSION_COLUMNS
    assert "backtest_results" not in row
    assert row["execution_graph"] == result["plan"].to_dict()
    assert_no_backtest_relation(touched_relations(db_double), "the degraded save")

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("004_strategy_builder_canonical.sql" in message for message in warnings)


@pytest.mark.asyncio
async def test_a_refused_save_starts_no_backtest_either(reg):
    """The other half of "no side effects": the failure path does nothing at all."""
    from backend_app.backend.strategy_compiler import ValidationError

    db_double, strategy_id = seeded_db(USER_A)
    service_under_test = service_on(db_double)

    with armed_tripwires() as fired:
        with pytest.raises(ValidationError):
            await service_under_test.create_version(
                USER_A, strategy_id, graph_with_unfed_action(reg), registry=reg
            )

    assert fired == []
    assert db_double.statements == []
    assert db_double.versions == []


# ═══════════════════════════════════════════════════════════════════════════
# 8. Requirement 22.1 through the real HTTP surface
# ═══════════════════════════════════════════════════════════════════════════


class TestTheSaveEndpointCreatesNoBacktest:
    """``POST /api/strategy-operations/strategies/{id}/versions``, through the real app.

    The router, the service, the compiler, the validator and the registry are all production
    code. Only the request-scoped database client is a double, and it applies its writes, so
    what is asserted is the state of the rows.
    """

    def test_the_save_succeeds_with_every_tripwire_armed(self, app_and_client, sb):
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)

        with armed_tripwires() as fired:
            response = client.post(
                f"/api/strategy-operations/strategies/{HTTP_STRATEGY_ID}/versions",
                json=_valid_save_body(),
            )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "saved"
        assert fired == [], f"the save started a backtest: {fired}"

    def test_the_save_writes_no_backtest_relation(self, app_and_client, sb):
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)

        response = client.post(
            f"/api/strategy-operations/strategies/{HTTP_STRATEGY_ID}/versions",
            json=_valid_save_body(),
        )
        assert response.status_code == 200, response.text
        assert sb.rows.get("strategy_versions"), "the save wrote no version row"

        assert_no_backtest_relation(
            {table for _kind, table, _payload, _filters in sb.writes}, "the save"
        )
        assert_no_backtest_relation(sb.rows.keys(), "the save")
        for relation in sorted(BACKTEST_RELATIONS):
            assert sb.rows.get(relation, []) == [], relation

    def test_no_written_payload_names_a_backtest(self, app_and_client, sb):
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)

        response = client.post(
            f"/api/strategy-operations/strategies/{HTTP_STRATEGY_ID}/versions",
            json=_valid_save_body(),
        )
        assert response.status_code == 200, response.text

        for kind, table, payload, _filters in sb.writes:
            assert_no_backtest_payload(payload, f"{table} {kind}")
        for row in sb.rows.get("strategy_versions", []):
            assert_no_backtest_payload(row, "the persisted version row")

    def test_the_save_response_promises_no_backtest(self, app_and_client, sb):
        """Requirement 22.1 is also a contract with the client: nothing was queued."""
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)

        response = client.post(
            f"/api/strategy-operations/strategies/{HTTP_STRATEGY_ID}/versions",
            json=_valid_save_body(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert_no_backtest_payload(body, "the save response")
        assert "job_id" not in body
        assert body["training"]["state"] == "NOT_REQUIRED"

    def test_the_legacy_strategy_save_creates_no_backtest_either(self, app_and_client, sb):
        """``POST /api/strategies`` is the Builder's other write, and it also compiles.

        The body is the one ``StrategyBuilder.jsx`` really sends: ``buildSavePayload``
        returns the canonical envelope on ``graph_json`` *and* the flattened
        ``nodes``/``edges`` this router has always accepted. ``schema.load_graph`` reads
        ``graph_json`` first, so the canonical graph is what gets compiled. This endpoint
        matters because it is the path the save button in production takes today - the
        versioned save is the one ``design.md`` names, and both must be backtest-free.
        """
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)

        with armed_tripwires() as fired:
            response = client.post("/api/strategies", json=_legacy_save_body())

        assert response.status_code == 200, response.text
        assert response.json()["dag_hash"], "nothing was compiled, so nothing was saved"
        assert fired == [], f"the legacy save started a backtest: {fired}"
        assert sb.writes, "the legacy save wrote nothing, so this asserts nothing"
        assert_no_backtest_relation(
            {table for _kind, table, _payload, _filters in sb.writes}, "the legacy save"
        )
        for kind, table, payload, _filters in sb.writes:
            assert_no_backtest_payload(payload, f"{table} {kind}")

    def test_a_legacy_save_refused_as_a_version_1_payload_starts_nothing_either(
        self, app_and_client, sb
    ):
        """The same graph with the envelope removed, which is a *version 1* body.

        ``load_graph`` reads a bare ``{"nodes": ..., "edges": ...}`` body as schema version
        1 and migrates it at read time - that is its documented contract, and it is what
        keeps the pre-canonical clients working. Canonical v2 nodes sent that way therefore
        arrive with no ``block_id`` the v1 reader can see, and the save is refused 422 with
        the full report. That is a correct refusal, not a defect, and it is asserted here
        rather than avoided: Property 24 quantifies over save *operations*, so the refusal
        must also write no row and start no backtest.
        """
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)
        graph = _valid_save_body()["blueprint"]

        with armed_tripwires() as fired:
            response = client.post(
                "/api/strategies",
                json={
                    "name": "no backtest here either",
                    "nodes": graph["nodes"],
                    "edges": graph["edges"],
                },
            )

        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"] == "STRATEGY_GRAPH_INVALID"
        assert fired == [], f"a refused save started a backtest: {fired}"
        # Requirement 3.6: the failure path never opened a database client.
        assert sb.writes == [], sb.writes


class TestTheTripwiresBite:
    """The control every "nothing fired" assertion above depends on.

    ``POST /api/strategies/backtest`` exists to do the thing this file forbids the save from
    doing. If the tripwires do not fire for it, they would not fire for a regression either.

    The assertion is on the **record**, never on a raised exception reaching this frame. The
    shared client is built ``raise_server_exceptions=False`` (task 8.7 needs that, so a
    handler fault is a status code rather than a collection error), and Starlette's error
    middleware turns anything escaping a handler into a 500 response. That is precisely why
    every tripwire appends its label *before* raising: the record survives the handler's
    ``except Exception``, the middleware, and a client that swallows the raise - which is the
    same property the save tests rely on when they assert ``fired == []``.
    """

    #: The two seams the queued branch reaches, in the order it reaches them: it writes the
    #: job's status hash and then publishes to the stream. Either one firing proves the wires
    #: are live; asserting the *set* rather than the sequence keeps a reordering of two
    #: statements inside the backtest endpoint from failing this file for no reason.
    QUEUE_PATH_WIRES = frozenset(
        {
            "backend_app.worker._write_status",
            "backend_app.core.event_bus.publish_backtest_job",
        }
    )

    def test_the_backtest_endpoint_fires_a_tripwire(self, app_and_client):
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)

        with armed_tripwires() as fired:
            response = client.post(
                "/api/strategies/backtest", json={"dag_config": {"nodes": []}}
            )

        assert fired, "the backtest endpoint tripped nothing; the wires are dead"
        assert set(fired) <= self.QUEUE_PATH_WIRES, fired
        # The wire raised, so the endpoint did not complete. A 200 here would mean the
        # tripwire was swallowed and the record above is the only evidence left.
        assert response.status_code == 500, response.text

    def test_the_synchronous_backtest_path_fires_a_tripwire(self, app_and_client):
        """The ``sync=true`` branch calls ``backtest_internal`` directly, bypassing Redis.

        Worth its own test because it is the branch the queued path *falls back to* when
        Redis is unreachable, inside an ``except Exception``. If only the queue wires were
        proved live, a save that lost its Redis connection could reach a real backtest
        through a wire this file never demonstrated.
        """
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)

        with armed_tripwires() as fired:
            response = client.post(
                "/api/strategies/backtest",
                json={"sync": True, "dag_config": {"nodes": []}},
            )

        assert fired == ["backend_app.routers.strategies.backtest_internal"], fired
        assert response.status_code == 500, response.text

    def test_a_direct_call_to_each_entry_point_is_recorded(self):
        """Every armed target, exercised individually. No target is a dud."""
        for dotted_module, attribute in TRIPWIRE_TARGETS:
            with armed_tripwires() as fired:
                owner_obj, name = _resolve(dotted_module, attribute)
                with pytest.raises(BacktestStarted):
                    getattr(owner_obj, name)()
            assert fired == [f"{dotted_module}.{attribute}"], (dotted_module, attribute)


# ═══════════════════════════════════════════════════════════════════════════
# 9. Requirement 22.2, dynamically: the executed save imports no backtester
# ═══════════════════════════════════════════════════════════════════════════


class TestNoBacktestModuleIsImportedByASave:
    """The half a static scan cannot make.

    This repository defers imports into function bodies as a matter of routine - the save
    handler itself imports its exception types that way - so "the module graph is clean"
    leaves room for a lazy import on the executed path. The recorder closes that gap by
    watching the imports a save actually performs.
    """

    def test_the_recorder_sees_an_import_that_is_already_cached(self):
        """The mechanism, established first.

        By the time this test runs the backtest modules are in ``sys.modules`` - this file
        imported them to arm its tripwires. A recorder watching only the ``sys.modules``
        delta would therefore report nothing, forever, for any save. The ``__import__`` hook
        is what makes the assertion real, and this proves it fires on a cache hit.
        """
        import backend_app.backend.backtest_runtime  # noqa: F401 - warm the cache

        assert "backend_app.backend.backtest_runtime" in sys.modules

        with ImportRecorder() as recorder:
            import backend_app.backend.backtest_runtime  # noqa: F401,F811
            from backend_app.backend.backtest_service import (  # noqa: F401
                get_backtest_service,
            )

        assert recorder.backtest_hits, "the recorder is blind to a cached import"
        assert "backend_app.backend.backtest_runtime" in recorder.backtest_hits

    @pytest.mark.asyncio
    async def test_a_service_level_save_imports_no_backtest_module(self, reg):
        db_double, strategy_id = seeded_db(USER_A)
        service_under_test = service_on(db_double)

        with ImportRecorder() as recorder:
            await service_under_test.create_version(
                USER_A, strategy_id, valid_graph(reg), registry=reg
            )

        assert recorder.backtest_hits == [], recorder.backtest_hits
        assert len(db_double.versions) == 1

    def test_a_save_through_the_real_app_imports_no_backtest_module(
        self, app_and_client, sb
    ):
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)

        with ImportRecorder() as recorder:
            response = client.post(
                f"/api/strategy-operations/strategies/{HTTP_STRATEGY_ID}/versions",
                json=_valid_save_body(),
            )

        assert response.status_code == 200, response.text
        assert recorder.backtest_hits == [], recorder.backtest_hits

    def test_the_recorder_was_not_inert_during_that_save(self, app_and_client, sb):
        """The save defers imports, so a recorder that saw nothing saw nothing at all."""
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)

        with ImportRecorder() as recorder:
            response = client.post(
                f"/api/strategy-operations/strategies/{HTTP_STRATEGY_ID}/versions",
                json=_valid_save_body(),
            )

        assert response.status_code == 200, response.text
        assert recorder.statements, "the recorder recorded nothing; it was not armed"
        assert any(
            name.startswith("backend_app.backend.strategy_compiler")
            for name in recorder.statements
        ), (
            "the save handler's own deferred import of the compiler was not observed, so "
            "this recorder is not watching the save"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 10. The ML branch: a save that queues *training* still queues no backtest
# ═══════════════════════════════════════════════════════════════════════════


class TestTheTrainingBranchStartsNoBacktest:
    """``create_version_and_maybe_train`` is the save path with the most moving parts.

    It fetches bars, builds a dataset, runs the feature pipeline through the real executors,
    admits against the caps and inserts ``training_jobs`` rows. Requirement 22.1 has to hold
    on that branch too - and "we have the data loaded, so let us warm a backtest" is exactly
    the shortcut it forbids. ``FakeSupabase`` raises ``42P01`` for any relation it was not
    seeded with, so an attempt to write ``strategy_backtests`` would fail the save outright
    as well as showing up in ``touched``.
    """

    @pytest.mark.asyncio
    async def test_a_queued_training_save_starts_no_backtest(
        self, service, reg, db, window
    ):
        graph, _model_id = model_graph(reg)

        with armed_tripwires() as fired:
            result = await save(service, reg, graph, epochs=100)

        assert result["training"]["state"] == "QUEUED"
        assert db.jobs, "no training job was created, so this asserts nothing about a save"
        assert fired == []
        assert_no_backtest_relation(set(db.touched), "the training save")
        for job in db.jobs:
            assert_no_backtest_payload(job, "the training_jobs row")
        for version_row in db.versions:
            assert_no_backtest_payload(version_row, "the version row")

    @pytest.mark.asyncio
    async def test_a_blocked_training_save_starts_no_backtest(
        self, service, reg, db, window
    ):
        """Three lags produce three feature columns; every model block requires five.

        The version is still saved and the training half is refused. A refusal must not
        substitute a backtest for the training it declined to run.
        """
        graph, _model_id = model_graph(reg, lags=(1, 2, 3))

        with armed_tripwires() as fired:
            result = await save(service, reg, graph)

        assert result["training"]["state"] == "BLOCKED"
        assert db.jobs == []
        assert len(db.versions) == 1
        assert fired == []
        assert_no_backtest_relation(set(db.touched), "the blocked training save")

    @pytest.mark.asyncio
    async def test_a_no_model_save_reports_not_required_and_starts_no_backtest(
        self, service, reg, db
    ):
        """Requirement 14.10's branch, which never opens the dataset at all."""
        with armed_tripwires() as fired:
            result = await save(service, reg, linear_graph(reg))

        assert result["training"]["state"] == "NOT_REQUIRED"
        assert fired == []
        assert len(db.versions) == 1
        assert_no_backtest_relation(set(db.touched), "the no-model save")
        assert set(db.touched) <= SAVE_PATH_RELATIONS, sorted(set(db.touched))

    @pytest.mark.asyncio
    async def test_the_training_save_imports_no_backtest_module(
        self, service, reg, db, window
    ):
        graph, _model_id = model_graph(reg)

        with ImportRecorder() as recorder:
            result = await save(service, reg, graph, epochs=100)

        assert result["training"]["state"] == "QUEUED"
        assert recorder.backtest_hits == [], recorder.backtest_hits
        # Not inert: this branch defers its policy import into the function body.
        assert any(
            "ml_training_policy" in name for name in recorder.statements
        ), "the recorder did not observe the branch's own deferred import"

    @pytest.mark.asyncio
    async def test_the_double_would_refuse_a_backtest_write(self, db):
        """The fake is strict, so "no backtest table was touched" is not a courtesy.

        ``FakeSupabase`` was seeded with ``strategies``, ``strategy_versions``, ``profiles``
        and ``training_jobs``. Anything else raises 42P01, the way PostgreSQL would for a
        relation that is not there - which is what a save reaching for ``strategy_backtests``
        would meet.
        """
        from tests.test_training_service_admission import MissingRelation

        with pytest.raises(MissingRelation):
            await db.table("strategy_backtests").insert({"id": "x"}).execute()
        assert "strategy_backtests" in db.touched


# ═══════════════════════════════════════════════════════════════════════════
# 11. Property 24
# ═══════════════════════════════════════════════════════════════════════════

#: Hypothesis draws over the dimensions that actually vary a save: the graph's identity
#: (which changes the plan, the hash and the warmup), the version label, the two flags that
#: decide whether ``is_current`` and ``strategies.current_version`` move, and - the one that
#: matters most in this environment - whether migration 004 part 1 is applied, because the
#: degraded branch is a different code path with a different written row.
#:
#: Node ids, block ids, ports and edges are fixed: a graph that does not validate never
#: reaches a database at all, so drawing invalid graphs would be drawing examples that say
#: nothing about what a *save* does. The refusal path is asserted separately, by name.


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    window_bars=st.integers(min_value=2, max_value=200),
    threshold=st.floats(
        min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False
    ),
    version_label=st.one_of(st.none(), st.sampled_from(["v3.0", "v9.7"])),
    make_current=st.booleans(),
    is_draft=st.booleans(),
    canonical_columns=st.booleans(),
)
def test_property_24_a_save_creates_no_backtest_record_and_starts_no_backtest_job(
    window_bars, threshold, version_label, make_current, is_draft, canonical_columns
):
    """**Property 24: Saving a version creates no backtest row and starts no backtest job.**

    **Validates: Requirements 22.1, 22.2**

    One drawn save, asserted four ways: every tripwire silent (no job, no execution, no
    record written by ``backtest_service``), no relation touched whose name names the
    backtester, no persisted key that does, and no import of a backtest module on the
    executed path. The save is also required to have *succeeded* - a property about the
    absence of a side effect is worthless if the operation never happened.
    """
    import asyncio

    from backend_app.backend.registry_snapshot_service import (
        reset_registry_snapshot_state,
    )
    from backend_app.backend.strategy_service import reset_canonical_column_support

    registry = cached_registry()
    reset_canonical_column_support()
    reset_registry_snapshot_state()

    db_double, strategy_id = seeded_db(USER_A, canonical_columns=canonical_columns)
    service_under_test = service_on(db_double)
    graph = valid_graph(registry, window=window_bars, threshold=threshold)

    try:
        with armed_tripwires() as fired:
            with ImportRecorder() as recorder:
                result = asyncio.run(
                    service_under_test.create_version(
                        USER_A,
                        strategy_id,
                        graph,
                        registry=registry,
                        version=version_label,
                        make_current=make_current,
                        is_draft=is_draft,
                    )
                )

        # The save happened.
        assert len(db_double.versions) == 1
        assert result["dag_hash"]
        assert result["canonical_persisted"] is canonical_columns
        if version_label is not None:
            assert db_double.versions[0]["version"] == version_label

        # No backtest was started, executed or recorded.
        assert fired == [], fired
        assert recorder.backtest_hits == [], recorder.backtest_hits

        # No backtest relation, and no backtest column.
        assert_no_backtest_relation(touched_relations(db_double), "the save")
        assert touched_relations(db_double) <= SAVE_PATH_RELATIONS
        assert_no_backtest_payload(db_double.versions[0], "the version row")
        assert_no_backtest_payload(db_double.stores["strategies"][0], "the strategy row")
    finally:
        reset_canonical_column_support()
        reset_registry_snapshot_state()


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(window_bars=st.integers(min_value=2, max_value=200))
def test_property_24_holds_on_the_refusal_path_too(window_bars):
    """A refused save is still a save operation, and it must also start nothing.

    The quantifier in Property 24 is over save *operations*, not over successful ones. This
    is the half where a "let us at least backtest what we have" fallback would be tempting.
    """
    import asyncio

    from backend_app.backend.registry_snapshot_service import (
        reset_registry_snapshot_state,
    )
    from backend_app.backend.strategy_compiler import ValidationError
    from backend_app.backend.strategy_service import reset_canonical_column_support

    registry = cached_registry()
    reset_canonical_column_support()
    reset_registry_snapshot_state()

    db_double, strategy_id = seeded_db(USER_A)
    service_under_test = service_on(db_double)
    graph = graph_with_unfed_action(registry)

    try:
        with armed_tripwires() as fired:
            with ImportRecorder() as recorder:
                raised = False
                try:
                    asyncio.run(
                        service_under_test.create_version(
                            USER_A, strategy_id, graph, registry=registry
                        )
                    )
                except ValidationError:
                    raised = True

        assert raised, "the invalid graph was accepted, so this example proves nothing"
        assert fired == []
        assert recorder.backtest_hits == []
        assert db_double.statements == []
    finally:
        reset_canonical_column_support()
        reset_registry_snapshot_state()
