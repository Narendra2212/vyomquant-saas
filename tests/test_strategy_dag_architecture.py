# -*- coding: utf-8 -*-
"""
tests/test_strategy_dag_architecture.py

Architecture (layering) test for `backend_app/backend/strategy_dag/`.

Spec: strategy-builder task 1.20. Requirement 21.10 - "THE Strategy_Compiler and
Graph_Validator SHALL operate without importing the execution engine, the exchange
executor or the Credential_Vault." design.md states the same rule twice: the API layer
never contains graph logic, `strategy_dag` never performs I/O, the engines never re-derive
the schema; and under Financial safety: "An architecture test asserts that no module under
`strategy_dag/` imports `execution_engine`, `exchange_executor` or the vault - the Builder
cannot place an order even by accident."

Why this file exists
--------------------
`strategy_dag` is the pure core: schema, block specs, registry, validator, plan. The
compiler, the `dag_worker` and the backtester must all be able to import it without
dragging in FastAPI, a database handle, CCXT or credentials. The old router-embedded
`DAGCompiler` could not be imported outside a FastAPI request context, which is why it was
untestable in isolation and why two divergent compilers were allowed to exist (defect
SB-01). This test makes that layering violation impossible to reintroduce.

Module-level vs lazy imports
----------------------------
`block_specs.py` legitimately *references* `exchange_executor` and `data_seeking_engine`:
`action_specs()` generates the ACTION descriptors from the real
`exchange_executor.OrderType` rather than a duplicated list (Requirement 4.10), and DATA
`runtime_ref`s resolve to `data_seeking_engine.DataEngine` methods. Those imports live
*inside* the functions that need them, which is the mechanism that keeps the package pure:
declaring a descriptor costs nothing, only assembly pays for CCXT.

So a substring search for "exchange_executor" would fail on correct code. Instead every
module is parsed with `ast` and only *import-time* statements are inspected: statements at
module scope, plus the bodies of module-scope `if`/`try`/`with`/`for`/`class` (those run at
import), but **not** function bodies (lazy) and **not** `if TYPE_CHECKING:` blocks (never
executed at runtime). A lazy in-function import is permitted; the same import at module
scope is a failure, because it pollutes the import graph of every consumer.

The static rule is then backed by the runtime property, measured in a subprocess: after
importing each module in a fresh interpreter, none of `fastapi`, `ccxt`, `sqlalchemy`,
`asyncpg` or `supabase` may appear in `sys.modules`. `tests/test_block_registry.py` and
`tests/test_compiled_plan.py` each carry a narrower version of that check for one module;
this file is the general one, covering every module in the package.

Credentials are held to a stricter rule than the executor: `credential_vault` and
`api_key_vault` must be unreachable *at all*, not even lazily and not through a dynamic
`importlib.import_module`, because unlike the order-type enum there is no legitimate reason
for the pure core to touch a secret.

Modules are discovered by walking the package directory, so a module added later is covered
automatically. A hardcoded list of today's six filenames would let module seven slip
through - the same drift failure mode the block registry exists to prevent.

Nothing is mocked. The guard-bites tests at the end run the real checker over synthetic
sources written to `tmp_path`, never into the real package.
"""

import ast
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator, List, NamedTuple, Sequence, Tuple

import pytest

import backend_app.backend.strategy_dag as strategy_dag_pkg

# ---------------------------------------------------------------------------
# 1. What is forbidden
# ---------------------------------------------------------------------------

#: Real module locations, verified against the repository rather than assumed:
#: `backend_app/core/execution_engine.py`, `backend_app/backend/exchange_executor.py`,
#: `backend_app/core/credential_vault.py`, `backend_app/backend/api_key_vault.py`.
#: Matching is done per dotted segment, so any package path ending in one of these names
#: is caught, as is a bare `import fastapi` or `from fastapi import APIRouter`.
FORBIDDEN_AT_IMPORT_TIME: Tuple[str, ...] = (
    "execution_engine",
    "exchange_executor",
    "credential_vault",
    "api_key_vault",
    "fastapi",
    # starlette is FastAPI's core; reaching it is the same layering breach by another name.
    "starlette",
)

#: Held to the stricter rule: unreachable even lazily.
FORBIDDEN_ALWAYS: Tuple[str, ...] = ("credential_vault", "api_key_vault")

#: Distributions whose presence in `sys.modules` proves the web layer, a database handle or
#: an exchange client was dragged in. Checked in a fresh interpreter per module.
HEAVY_RUNTIME_MODULES: Tuple[str, ...] = (
    "fastapi",
    "ccxt",
    "sqlalchemy",
    "asyncpg",
    "supabase",
)

PACKAGE_NAME: str = strategy_dag_pkg.__name__
PACKAGE_DIR: Path = Path(strategy_dag_pkg.__file__).resolve().parent
#: backend_app/backend/strategy_dag -> backend_app/backend -> backend_app -> repo root
REPO_ROOT: Path = PACKAGE_DIR.parents[2]

#: The modules known to exist when this test was written. Used only as a *floor*: if the
#: walker ever returns fewer than these, discovery itself is broken and every other
#: assertion in this file would vacuously pass.
KNOWN_MODULES_FLOOR: Tuple[str, ...] = (
    "__init__",
    "block_specs",
    "plan",
    "registry",
    "schema",
    "validator",
)


# ---------------------------------------------------------------------------
# 2. Dynamic module discovery
# ---------------------------------------------------------------------------


class DiscoveredModule(NamedTuple):
    """One importable module inside the package."""

    dotted: str
    path: Path

    @property
    def relative(self) -> str:
        """Path relative to the package, for readable failure messages.

        Falls back to the bare name for the synthetic modules the guard-bites tests write
        to `tmp_path`, which are not under the package.
        """
        try:
            return self.path.relative_to(PACKAGE_DIR).as_posix()
        except ValueError:
            return self.path.name


def discover_modules(package_dir: Path = PACKAGE_DIR, package_name: str = PACKAGE_NAME) -> List[DiscoveredModule]:
    """Every `.py` module under the package, found by walking the directory.

    Nothing is hardcoded, so a module added tomorrow is covered without editing this test.
    """
    found: List[DiscoveredModule] = []
    for path in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(package_dir).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        dotted = ".".join([package_name, *parts]) if parts else package_name
        found.append(DiscoveredModule(dotted=dotted, path=path))
    return found


MODULES: List[DiscoveredModule] = discover_modules()


def _ids(modules: Sequence[DiscoveredModule]) -> List[str]:
    return [m.relative for m in modules]


def test_discovery_finds_the_whole_package():
    """Discovery is dynamic; this asserts it is not silently empty or shrinking."""
    assert MODULES, f"no modules discovered under {PACKAGE_DIR}"
    stems = {m.path.stem for m in MODULES}
    missing = [name for name in KNOWN_MODULES_FLOOR if name not in stems]
    assert not missing, f"discovery missed known modules {missing}; walker is broken"
    assert len(MODULES) >= len(KNOWN_MODULES_FLOOR)


# ---------------------------------------------------------------------------
# 3. The checker: import-time statements vs lazy ones
# ---------------------------------------------------------------------------


class ImportRecord(NamedTuple):
    """One import target, with the line it appeared on."""

    dotted: str
    lineno: int
    statement: str


def _is_type_checking_test(test: ast.expr) -> bool:
    """True for `if TYPE_CHECKING:` and `if typing.TYPE_CHECKING:`."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _iter_import_time_statements(body: Iterable[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield statements that execute when the module is imported.

    Descends into module-scope `if`/`try`/`with`/`for`/`while`/`class` bodies, because those
    run at import. Does **not** descend into `def`/`async def` bodies - an import there runs
    only when the function is called, which is the permitted lazy seam. `if TYPE_CHECKING:`
    bodies are skipped (never executed at runtime) while their `else:` branch is not.
    """
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            yield from _iter_import_time_statements(node.orelse)
            continue
        yield node
        for field in ("body", "orelse", "finalbody"):
            nested = getattr(node, field, None)
            if isinstance(nested, list):
                yield from _iter_import_time_statements(
                    [child for child in nested if isinstance(child, ast.stmt)]
                )
        for handler in getattr(node, "handlers", None) or []:
            yield from _iter_import_time_statements(handler.body)


def _records_for(node: ast.stmt) -> List[ImportRecord]:
    """The dotted targets an import statement reaches."""
    records: List[ImportRecord] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            records.append(
                ImportRecord(alias.name, node.lineno, f"import {alias.name}")
            )
    elif isinstance(node, ast.ImportFrom):
        prefix = "." * node.level + (node.module or "")
        if node.module:
            records.append(ImportRecord(node.module, node.lineno, f"from {prefix} import ..."))
        for alias in node.names:
            target = f"{node.module}.{alias.name}" if node.module else alias.name
            records.append(
                ImportRecord(target, node.lineno, f"from {prefix} import {alias.name}")
            )
    return records


def import_time_imports(source: str) -> List[ImportRecord]:
    """Every import target reached while the module is being imported."""
    tree = ast.parse(source)
    records: List[ImportRecord] = []
    for node in _iter_import_time_statements(tree.body):
        records.extend(_records_for(node))
    return records


def every_import(source: str) -> List[ImportRecord]:
    """Every import target anywhere in the module, lazy ones included."""
    tree = ast.parse(source)
    records: List[ImportRecord] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            records.extend(_records_for(node))
    return records


def dynamic_import_targets(source: str) -> List[ImportRecord]:
    """String literals handed to `importlib.import_module(...)` or `__import__(...)`."""
    tree = ast.parse(source)
    records: List[ImportRecord] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in ("import_module", "__import__"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                records.append(
                    ImportRecord(arg.value, node.lineno, f"{name}({arg.value!r})")
                )
    return records


def forbidden_hits(records: Iterable[ImportRecord], forbidden: Iterable[str]) -> List[ImportRecord]:
    """Records whose dotted path contains a forbidden segment."""
    banned = set(forbidden)
    hits: List[ImportRecord] = []
    for record in records:
        segments = {segment for segment in record.dotted.split(".") if segment}
        if segments & banned:
            hits.append(record)
    return hits


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. Static purity - Requirement 21.10
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", MODULES, ids=_ids(MODULES))
def test_no_import_time_reference_to_execution_credentials_or_fastapi(module: DiscoveredModule):
    """No module under `strategy_dag/` imports the executor, the engine, a vault or FastAPI.

    Validates: Requirements 21.10
    """
    hits = forbidden_hits(import_time_imports(_read(module.path)), FORBIDDEN_AT_IMPORT_TIME)
    assert not hits, (
        f"{module.relative} imports a forbidden module at import time: "
        + "; ".join(f"line {h.lineno}: {h.statement}" for h in hits)
        + ". strategy_dag is the pure core - move the import inside the function that "
        "needs it, as block_specs does for exchange_executor."
    )


@pytest.mark.parametrize("module", MODULES, ids=_ids(MODULES))
def test_no_reference_at_all_to_a_credential_vault(module: DiscoveredModule):
    """Credentials are unreachable, not even lazily and not via `import_module`.

    Validates: Requirements 21.10
    """
    source = _read(module.path)
    records = every_import(source) + dynamic_import_targets(source)
    hits = forbidden_hits(records, FORBIDDEN_ALWAYS)
    assert not hits, (
        f"{module.relative} reaches a credential store: "
        + "; ".join(f"line {h.lineno}: {h.statement}" for h in hits)
        + ". The pure core never touches a secret."
    )


@pytest.mark.parametrize("module", MODULES, ids=_ids(MODULES))
def test_no_dynamic_import_of_the_execution_path(module: DiscoveredModule):
    """A lazy in-function import is fine; hiding one behind `import_module` at import time
    is not. This closes the string-indirection escape hatch for the whole forbidden set.

    Validates: Requirements 21.10
    """
    source = _read(module.path)
    tree = ast.parse(source)
    import_time_lines = {node.lineno for node in _iter_import_time_statements(tree.body)}
    dynamic = [r for r in dynamic_import_targets(source) if r.lineno in import_time_lines]
    hits = forbidden_hits(dynamic, FORBIDDEN_AT_IMPORT_TIME)
    assert not hits, (
        f"{module.relative} dynamically imports a forbidden module at import time: "
        + "; ".join(f"line {h.lineno}: {h.statement}" for h in hits)
    )


# ---------------------------------------------------------------------------
# 5. Runtime purity - the same rule, measured rather than parsed
# ---------------------------------------------------------------------------


def _import_in_fresh_interpreter(dotted: str, probe: str) -> str:
    """Import `dotted` in a fresh interpreter and print the result of `probe`."""
    code = f"import sys\nimport {dotted}\nprint({probe})\n"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"importing {dotted} failed:\n{result.stdout}\n{result.stderr}"
    )
    return result.stdout.strip()


@pytest.mark.parametrize("module", MODULES, ids=_ids(MODULES))
def test_importing_a_module_drags_in_no_web_layer_db_or_exchange_client(module: DiscoveredModule):
    """Every module must be importable by the worker and the backtester for free.

    Validates: Requirements 21.10
    """
    probe = (
        "sorted(m for m in "
        f"{HEAVY_RUNTIME_MODULES!r}"
        " if m in sys.modules)"
    )
    assert _import_in_fresh_interpreter(module.dotted, probe) == "[]", (
        f"importing {module.dotted} pulled in a heavy dependency"
    )


@pytest.mark.parametrize("module", MODULES, ids=_ids(MODULES))
def test_importing_a_module_loads_no_credential_store(module: DiscoveredModule):
    """Transitive reach counts: no vault module may end up in `sys.modules`.

    Validates: Requirements 21.10
    """
    probe = (
        "sorted(m for m in list(sys.modules) "
        f"if m.rsplit('.', 1)[-1] in {FORBIDDEN_ALWAYS!r})"
    )
    assert _import_in_fresh_interpreter(module.dotted, probe) == "[]", (
        f"importing {module.dotted} loaded a credential store"
    )


# ---------------------------------------------------------------------------
# 6. The guard bites - the checker tested against synthetic sources
# ---------------------------------------------------------------------------
#
# A layering test that cannot fail is worse than no test. These run the real checker over
# sources written to tmp_path, never into the package.


def _synthetic(tmp_path: Path, body: str) -> DiscoveredModule:
    path = tmp_path / "synthetic_module.py"
    path.write_text(body, encoding="utf-8")
    return DiscoveredModule(dotted="synthetic_module", path=path)


MODULE_LEVEL_VIOLATIONS = {
    "absolute_from": "from backend_app.backend import exchange_executor\n",
    "absolute_import": "import backend_app.core.execution_engine\n",
    "aliased": "import backend_app.backend.exchange_executor as ee\n",
    "relative_from": "from . import exchange_executor\n",
    "relative_dotted": "from ..exchange_executor import OrderType\n",
    "fastapi": "from fastapi import APIRouter\n",
    "fastapi_plain": "import fastapi\n",
    "starlette": "from starlette.responses import JSONResponse\n",
    "inside_try": (
        "try:\n"
        "    from backend_app.backend import exchange_executor\n"
        "except ImportError:\n"
        "    exchange_executor = None\n"
    ),
    "inside_if": "import os\nif os.environ.get('X'):\n    import fastapi\n",
    "inside_class": "class C:\n    from backend_app.backend import exchange_executor\n",
    "type_checking_else": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    pass\n"
        "else:\n"
        "    import fastapi\n"
    ),
}


@pytest.mark.parametrize("label", sorted(MODULE_LEVEL_VIOLATIONS))
def test_checker_rejects_an_import_time_violation(tmp_path: Path, label: str):
    """Adding a module-level executor/engine/FastAPI import must fail the guard."""
    module = _synthetic(tmp_path, MODULE_LEVEL_VIOLATIONS[label])
    assert forbidden_hits(import_time_imports(_read(module.path)), FORBIDDEN_AT_IMPORT_TIME)
    with pytest.raises(AssertionError):
        test_no_import_time_reference_to_execution_credentials_or_fastapi(module)


LAZY_PERMITTED = {
    "lazy_executor": (
        "def order_types():\n"
        "    from backend_app.backend.exchange_executor import OrderType\n"
        "    return tuple(OrderType)\n"
    ),
    "lazy_data_engine": (
        "def data_engine_attr(name):\n"
        "    from backend_app.backend.data_seeking_engine import DataEngine\n"
        "    return getattr(DataEngine, name, None)\n"
    ),
    "lazy_in_method": (
        "class Registry:\n"
        "    def build(self):\n"
        "        from backend_app.backend import exchange_executor\n"
        "        return exchange_executor\n"
    ),
    "lazy_async": (
        "async def go():\n"
        "    from backend_app.core import execution_engine\n"
        "    return execution_engine\n"
    ),
    "type_checking_only": (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from backend_app.backend.exchange_executor import OrderType\n"
    ),
}


@pytest.mark.parametrize("label", sorted(LAZY_PERMITTED))
def test_checker_permits_a_lazy_in_function_import(tmp_path: Path, label: str):
    """The lazy seam block_specs relies on must stay legal, or the ACTION descriptors
    would have to be duplicated - the exact drift Requirement 4.10 forbids."""
    module = _synthetic(tmp_path, LAZY_PERMITTED[label])
    assert not forbidden_hits(
        import_time_imports(_read(module.path)), FORBIDDEN_AT_IMPORT_TIME
    )
    test_no_import_time_reference_to_execution_credentials_or_fastapi(module)


CREDENTIAL_VIOLATIONS = {
    "module_level": "from backend_app.core.credential_vault import CredentialVault\n",
    "lazy_credential_vault": (
        "def decrypt(x):\n"
        "    from backend_app.core.credential_vault import get_credential_vault\n"
        "    return get_credential_vault()\n"
    ),
    "lazy_api_key_vault": (
        "def keys():\n"
        "    from backend_app.backend.api_key_vault import APIKeyVault\n"
        "    return APIKeyVault\n"
    ),
    "dynamic": (
        "import importlib\n"
        "def vault():\n"
        "    return importlib.import_module('backend_app.backend.api_key_vault')\n"
    ),
}


@pytest.mark.parametrize("label", sorted(CREDENTIAL_VIOLATIONS))
def test_checker_rejects_any_credential_reach(tmp_path: Path, label: str):
    """Unlike the executor, a vault is not permitted even behind a lazy seam."""
    module = _synthetic(tmp_path, CREDENTIAL_VIOLATIONS[label])
    with pytest.raises(AssertionError):
        test_no_reference_at_all_to_a_credential_vault(module)


def test_checker_rejects_a_dynamic_import_at_module_scope(tmp_path: Path):
    """String indirection is not an escape hatch."""
    module = _synthetic(
        tmp_path,
        "import importlib\n"
        "exchange_executor = importlib.import_module('backend_app.backend.exchange_executor')\n",
    )
    with pytest.raises(AssertionError):
        test_no_dynamic_import_of_the_execution_path(module)


def test_checker_ignores_an_unrelated_import(tmp_path: Path):
    """No false positives on the imports the package actually makes."""
    module = _synthetic(
        tmp_path,
        "from __future__ import annotations\n"
        "import hashlib\n"
        "import numpy as np\n"
        "from backend_app.backend import indicators_backend\n"
        "from backend_app.backend.strategy_dag.schema import BlockCategory\n",
    )
    test_no_import_time_reference_to_execution_credentials_or_fastapi(module)
    test_no_reference_at_all_to_a_credential_vault(module)
    test_no_dynamic_import_of_the_execution_path(module)


def test_discovery_would_cover_a_module_added_later(tmp_path: Path):
    """A module added to the package tomorrow is inspected without editing this test."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "seventh_module.py").write_text(
        "from backend_app.backend import exchange_executor\n", encoding="utf-8"
    )
    nested = package / "sub"
    nested.mkdir()
    (nested / "__init__.py").write_text("", encoding="utf-8")
    (nested / "deep.py").write_text("import fastapi\n", encoding="utf-8")

    discovered = discover_modules(package, "pkg")
    dotted = {m.dotted for m in discovered}
    assert dotted == {"pkg", "pkg.seventh_module", "pkg.sub", "pkg.sub.deep"}

    offenders = [
        m
        for m in discovered
        if forbidden_hits(import_time_imports(_read(m.path)), FORBIDDEN_AT_IMPORT_TIME)
    ]
    assert {m.dotted for m in offenders} == {"pkg.seventh_module", "pkg.sub.deep"}
