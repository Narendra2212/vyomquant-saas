# -*- coding: utf-8 -*-
"""
tests/test_compiler_architecture.py

Architecture test for the single compilation authority.

Spec: strategy-builder task 2.9. Requirement 3.1 - "THE Strategy_Builder_API SHALL compile
and validate every submitted Canonical_Graph through one Strategy_Compiler entry point."

What this file holds in place
----------------------------
SB-01 was *two* DAG compilers with drifted rule sets: `routers/strategies.py` carried a
`DAGCompiler` + `CompiledDAG` pair with its own ten validation steps and its own
topological sort, alongside `backend/strategy_compiler.py`. One graph could therefore be
accepted on one path and rejected on another. Task 2.4 deleted the duplicate, which fixes
the *instance*. This file makes the *class* of defect structurally unrepresentable: after
this test exists, a second graph compiler cannot be added anywhere under `backend_app/`
without a red test.

Relationship to `tests/test_task_2_4_call_site_migration.py`
------------------------------------------------------------
That file's `TestCompilerSingularity` already covers the first clause of task 2.9 -
`DAGCompiler` and `CompiledDAG` are not *defined* under `backend_app/routers/`, the router
module exposes neither of them (nor `TYPE_COMPATIBILITY` / `NodeType`), and neither name
appears as an `ast.Name` reference there. That coverage is not duplicated here. What this
file adds:

1. The **second clause**, which was untested anywhere: `strategy_compiler` is the only
   module that defines a graph `compile` entry point. That is a repo-wide uniqueness
   property, not a routers-scoped absence property.
2. A **name-independent** layering guard: no module under `backend_app/routers/` defines a
   graph-compile authority *under any name*, and no router defines a topological sort of
   its own. `DAGCompiler` is gone; `GraphBuilder.build_plan` doing the same job would slip
   past a name-based check.
3. The two SB-01 class names are checked as absent from the whole of `backend_app/`, not
   just from the routers - a strict superset of the routers-scoped assertion, kept here so
   this file stands alone as the architecture gate for phase 2.

What "a graph `compile` entry point" means here
-----------------------------------------------
The phrase needs a definition sharp enough to be useful, because the repository legitimately
contains many unrelated `compile` names: `re.compile` in `api_key_vault.py` and
`strategy_dag/schema.py`, and Keras `model.compile(...)` four times in `ml_models.py`. A
test that fires on `re.compile` is noise; a test that only matches the exact deleted class
name is vacuous.

Two filters, applied in order.

**Filter 1 - it must be a definition.** Only `FunctionDef` / `AsyncFunctionDef` /
`ClassDef` nodes are candidates, and only those whose name contains `compile`, or whose
class name ends in `Compiler`. `re.compile(...)` and `model.compile(...)` are *calls* on
someone else's object; they are not definitions and are never candidates. Everything is
parsed with `ast`, never grepped, because these modules deliberately carry prose recording
what was deleted - `routers/strategies.py` mentions `DAGCompiler` five times in comments -
and a docstring about a removed compiler must not read as a reintroduced one.

**Filter 2 - it must be about a graph, and it must own the compilation.** Each candidate is
classified from evidence in its own signature and body:

`NOT_A_GRAPH_COMPILE`
    No graph vocabulary in its signature, annotations or body, or unmistakably other
    subject matter (regex, SQL, template, Keras/optimizer/loss). A `compile_pattern` that
    wraps `re.compile` lands here.

`GRAPH_COMPILE_DELEGATE`
    Graph-shaped, but reaches the canonical compiler - `compile_plan`, `compile_package`,
    `compile_graph`, `compile_version`, `get_compiler`, `StrategyCompiler` - and derives no
    ordering and asserts no rules of its own. A delegate is a *caller*, not a second
    authority, so a delegate may live anywhere, including in a router: the thin
    `_compile_payload` seam is exactly what task 2.4 replaced the router compiler with.
    Every delegate is asserted to actually name a canonical target, so "delegate" is a
    measured fact and not a label.

`GRAPH_COMPILE_AUTHORITY`
    Graph-shaped and owning the compilation, evidenced by any of:

    * **order derivation** - an inline Kahn (it assigns `in_degree` / `indegree`) or
      `graphlib.TopologicalSorter`, or a call to an ordering helper *defined in its own
      module* (`self._execution_order`, `self._generate_execution_order`);
    * **plan assembly with no delegation** - it constructs a `CompiledPlan`,
      `StrategyPackage`, `ExecutionGraph` or `CompiledDAG` while naming no canonical entry
      point at all;
    * **its own edge-legality table** - it reads a module-local `TYPE_COMPATIBILITY`-shaped
      constant, which is precisely what the deleted `DAGCompiler` did;
    * **no delegation at all** - a graph-shaped `compile` that reaches no canonical entry
      point necessarily implements its own, whatever idiom it uses. This is the conservative
      fallback that keeps the guard honest when a reintroduction uses a sort this file never
      anticipated.

Order derivation is the sharp criterion because a drifted *order* and a drifted *rule set*
were the two halves of SB-01. Note the asymmetry that keeps false positives out: calling
`strategy_compiler.loose_execution_order` is *delegating* a sort, whereas calling a sort
defined in your own module is *owning* one. That is why `routers/strategies.py`'s
`_optimize_dag_structure` (graph pruning for `POST /api/strategies/optimize`) is clean while
the deleted `DAGCompiler.compile` would not be, and why
`strategy_operations.compile_strategy` is clean even though it constructs an
`ExecutionGraph` - it copies `plan.execution_order` out of a plan the canonical compiler
produced rather than deriving an order.

The authority set is then asserted twice: the set of *modules* holding one must be exactly
`{backend_app/backend/strategy_compiler.py}`, and the set of *qualified names* must be
exactly the two methods that own the two rule sets. Pinning the names means a third
authority added to `strategy_compiler.py` itself also trips this test and gets reviewed,
rather than being waved through because the file is the blessed one.

Resilience
----------
Modules are discovered by walking `backend_app/`, so a module added tomorrow is covered
without editing this test - a hardcoded file list is the same drift failure mode the
registry tests exist to prevent. Discovery is asserted against a floor, so a broken walker
cannot make every other assertion vacuously pass.

Nothing is mocked and no production code is touched. The guard-bites section runs the real
classifier over synthetic sources written to `tmp_path`: a second graph compiler placed in a
fake `routers/` tree must be caught, and a `re.compile` wrapper, a Keras `compile_model` and
a thin delegate must not be.
"""

import ast
import re
from pathlib import Path
from typing import FrozenSet, Iterable, List, NamedTuple, Sequence, Set, Tuple

import pytest

# ---------------------------------------------------------------------------
# 1. Where we look
# ---------------------------------------------------------------------------

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
BACKEND_APP: Path = REPO_ROOT / "backend_app"
ROUTERS_DIR: Path = BACKEND_APP / "routers"

#: The one module permitted to define a graph-compile authority (Requirement 3.1).
CANONICAL_COMPILER: Path = BACKEND_APP / "backend" / "strategy_compiler.py"

#: The authorities that module is permitted to define, pinned by qualified name.
#:
#: * ``StrategyCompiler`` - the class itself, which owns both rule sets.
#: * ``compile_plan`` - the canonical path: validate -> Kahn with a sorted ready set ->
#:   ``CompiledPlan``.
#: * ``compile_package`` - the legacy ``DAGConfig`` -> ``StrategyPackage`` path, still
#:   carrying the pre-SB-01 rule set. It is *inside* the canonical module, which is the
#:   whole point: one file owns every rule set, so the two cannot drift unseen. Task 2.4's
#:   notes schedule its removal; when it goes, this set shrinks by one and the test says so.
EXPECTED_AUTHORITIES: FrozenSet[str] = frozenset(
    {
        "StrategyCompiler",
        "StrategyCompiler.compile_plan",
        "StrategyCompiler.compile_package",
    }
)

#: Discovery floor. Fewer modules than this means the walker is broken, not that the
#: repository is clean.
MIN_DISCOVERED_MODULES: int = 50

#: Candidate floor: the compile-named definitions known to exist when this was written.
#: If the classifier stops seeing these, it has stopped seeing anything.
KNOWN_CANDIDATES: FrozenSet[str] = frozenset(
    {
        "backend_app/backend/strategy_compiler.py::StrategyCompiler",
        "backend_app/backend/strategy_compiler.py::StrategyCompiler.compile_plan",
        "backend_app/backend/strategy_compiler.py::StrategyCompiler.compile_package",
        "backend_app/backend/strategy_compiler.py::StrategyCompiler.compile",
        "backend_app/backend/strategy_compiler.py::compile_graph",
        "backend_app/backend/strategy_builder.py::compile_version",
        "backend_app/routers/strategies.py::_compile_payload",
        "backend_app/routers/strategy_operations.py::compile_strategy",
    }
)

#: The two classes SB-01 lived in. Neither may be defined anywhere under `backend_app/`.
SB01_CLASS_NAMES: FrozenSet[str] = frozenset({"DAGCompiler", "CompiledDAG"})

# ---------------------------------------------------------------------------
# 2. The vocabulary the classifier reasons over
# ---------------------------------------------------------------------------

#: A function is a candidate when `compile` appears in its name as a *verb*: `compile`,
#: `compile_plan`, `_compile_payload`, `recompile_version`, `compile_dag`. The
#: past-participle form is excluded, because `CompiledPlan`, `CompiledVersion` and
#: `compiled_columns` name a compile *result* - a data structure - and a data structure is
#: not an entry point. The one participle that matters, `CompiledDAG`, is covered by
#: `TestSB01ClassNamesAreGone` and, under `routers/`, by the name-independent behavioural
#: guard.
_COMPILE_FUNCTION_NAME = re.compile(r"(?:^|_)(?:re)?compile(?:_|$)", re.IGNORECASE)
#: A class is a candidate when it is named as a compiler.
_COMPILER_CLASS_NAME = re.compile(r"Compiler$|(?:^|_)(?:re)?compile(?:_|$)")

#: Graph vocabulary. Presence of any of these in a candidate's signature, annotations or
#: body identifiers makes it graph-shaped.
GRAPH_TOKENS: FrozenSet[str] = frozenset(
    {
        "graph",
        "graphs",
        "graph_json",
        "strategy_graph",
        "StrategyGraph",
        "dag",
        "dag_config",
        "dag_nodes",
        "dag_edges",
        "DAGConfig",
        "node",
        "nodes",
        "edge",
        "edges",
        "NodeSpec",
        "EdgeSpec",
        "blueprint",
        "plan",
        "CompiledPlan",
        "execution_order",
        "execution_levels",
        "execution_graph",
        "ExecutionGraph",
        "StrategyPackage",
        "topological_order",
        "warmup_bars",
        "dag_hash",
        "payload",
        "body",
    }
)

#: Unmistakably-not-a-graph vocabulary. Only decides the verdict when no graph token is
#: present at all, so a graph compiler that happens to mention a model cannot hide here.
NON_GRAPH_TOKENS: FrozenSet[str] = frozenset(
    {
        "re",
        "regex",
        "pattern",
        "sql",
        "query",
        "template",
        "jinja",
        "keras",
        "tensorflow",
        "tf",
        "torch",
        "optimizer",
        "loss",
        "epochs",
        "metrics",
        "layers",
    }
)

#: Naming the canonical compiler counts as delegation.
CANONICAL_TARGETS: FrozenSet[str] = frozenset(
    {
        "compile_plan",
        "compile_package",
        "compile_graph",
        "compile_version",
        "get_compiler",
        "StrategyCompiler",
        "load_plan",
    }
)

#: Modules whose import, inside a candidate, also counts as delegation. `strategy_dag` is
#: deliberately absent: importing `strategy_dag.plan` to build a `CompiledPlan` by hand is
#: the opposite of delegating a compile, and importing `strategy_dag.schema` to parse a
#: payload says nothing about who compiles it.
CANONICAL_MODULES: Tuple[str, ...] = (
    "strategy_compiler",
    "strategy_builder",
)

#: The canonical compiled artifact. Building one by hand, without reaching the compiler,
#: means claiming to have compiled something. Nowhere but the compiler may do that.
CANONICAL_ARTIFACTS: FrozenSet[str] = frozenset(
    {
        "CompiledPlan",
        "CompiledDAG",
        "from_graph",
    }
)

#: Pre-canonical transport shapes. `strategy_operations.compile_strategy` builds an
#: `ExecutionGraph` for its response, and `routers/strategies.py`'s walk-forward path wraps
#: a loose `dag_config` in an `ExecutionGraph` + `StrategyPackage` to hand to the
#: optimization engine with `execution_order=[]`. Neither derives an order, applies a rule
#: or emits a hash - they are adapters, not compilers - so constructing one is counted as
#: assembly for a *compile-named* definition but not as behavioural ownership for an
#: arbitrary one. Drawing the line anywhere else either misses a hand-built plan or
#: convicts a DTO.
LEGACY_TRANSPORT_SHAPES: FrozenSet[str] = frozenset({"StrategyPackage", "ExecutionGraph"})

ASSEMBLY_CONSTRUCTORS: FrozenSet[str] = CANONICAL_ARTIFACTS | LEGACY_TRANSPORT_SHAPES

#: An inline Kahn announces itself by its in-degree map.
INLINE_SORT_ASSIGNMENTS: FrozenSet[str] = frozenset({"in_degree", "indegree", "in_degrees"})

#: Reaching an ordering helper. Counts as *owning* only when the helper is defined in the
#: candidate's own module; the same call into `strategy_compiler` is delegation.
_ORDERING_HELPER = re.compile(r"(execution_order|topological|topo_sort|kahn)", re.IGNORECASE)

#: A private edge-legality table, the shape of the one the deleted `DAGCompiler` owned.
_COMPAT_TABLE = re.compile(r"(TYPE_COMPATIBILITY|PORT_COMPATIBILITY|LEGAL_EDGES|ALLOWED_EDGES)")

AUTHORITY = "GRAPH_COMPILE_AUTHORITY"
DELEGATE = "GRAPH_COMPILE_DELEGATE"
NOT_GRAPH = "NOT_A_GRAPH_COMPILE"


# ---------------------------------------------------------------------------
# 3. Module discovery
# ---------------------------------------------------------------------------


def iter_python_modules(root: Path) -> List[Path]:
    """Every `.py` file under `root`, sorted, excluding caches and virtualenvs.

    Walked rather than listed, so a module added later is classified without this test
    being edited.
    """
    skip = {"__pycache__", ".venv", "venv", "node_modules", ".git", "site-packages"}
    return sorted(
        path
        for path in root.rglob("*.py")
        if not skip.intersection(path.parts)
    )


def rel(path: Path) -> str:
    """Repo-relative posix path, or the bare name for a synthetic `tmp_path` module."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


# ---------------------------------------------------------------------------
# 4. Per-module static facts
# ---------------------------------------------------------------------------


class ModuleFacts(NamedTuple):
    """The identifiers a module binds, needed to tell "local helper" from "import"."""

    path: Path
    tree: ast.Module
    #: Names bound by any `def`/`class` in the module, at any depth.
    defined: FrozenSet[str]
    #: Names bound by any import anywhere in the module, including lazy in-function ones.
    imported: FrozenSet[str]
    #: Module-scope constant assignments (where a private compat table would live).
    module_constants: FrozenSet[str]


def module_facts(path: Path) -> ModuleFacts:
    tree = ast.parse(path.read_text(encoding="utf-8"))

    defined: Set[str] = set()
    imported: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)

    constants: Set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            constants.add(node.target.id)

    return ModuleFacts(
        path=path,
        tree=tree,
        defined=frozenset(defined),
        imported=frozenset(imported),
        module_constants=frozenset(constants),
    )


# ---------------------------------------------------------------------------
# 5. What one definition does, read off its AST subtree
# ---------------------------------------------------------------------------


class DefinitionUse(NamedTuple):
    """Identifiers a definition uses. Docstrings and comments are absent by construction:
    only `Name`, `Attribute`, `Call` and `arg` nodes are read, never string constants."""

    identifiers: FrozenSet[str]
    #: `foo(...)` -> "foo";  `x.foo(...)` -> "foo".
    called: FrozenSet[str]
    #: `self.foo(...)` / `cls.foo(...)` -> "foo".
    self_called: FrozenSet[str]
    #: Plain-`Name` assignment targets.
    assigned: FrozenSet[str]
    #: Module names appearing in imports inside the definition.
    imported_modules: FrozenSet[str]


def definition_use(node: ast.AST) -> DefinitionUse:
    identifiers: Set[str] = set()
    called: Set[str] = set()
    self_called: Set[str] = set()
    assigned: Set[str] = set()
    imported_modules: Set[str] = set()

    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            identifiers.add(child.id)
        elif isinstance(child, ast.Attribute):
            identifiers.add(child.attr)
        elif isinstance(child, ast.arg):
            identifiers.add(child.arg)
            if child.annotation is not None:
                for sub in ast.walk(child.annotation):
                    if isinstance(sub, ast.Name):
                        identifiers.add(sub.id)
                    elif isinstance(sub, ast.Attribute):
                        identifiers.add(sub.attr)

        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
                if isinstance(func.value, ast.Name) and func.value.id in {"self", "cls"}:
                    self_called.add(func.attr)

        if isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    assigned.add(target.id)
        elif isinstance(child, (ast.AnnAssign, ast.AugAssign)) and isinstance(
            child.target, ast.Name
        ):
            assigned.add(child.target.id)

        if isinstance(child, ast.Import):
            for alias in child.names:
                imported_modules.add(alias.name)
        elif isinstance(child, ast.ImportFrom):
            if child.module:
                imported_modules.add(child.module)
            for alias in child.names:
                imported_modules.add(alias.name)

    return DefinitionUse(
        identifiers=frozenset(identifiers),
        called=frozenset(called),
        self_called=frozenset(self_called),
        assigned=frozenset(assigned),
        imported_modules=frozenset(imported_modules),
    )


# ---------------------------------------------------------------------------
# 6. Classification
# ---------------------------------------------------------------------------


class Candidate(NamedTuple):
    """One definition, with its verdict and the evidence behind it."""

    module: str
    qualname: str
    lineno: int
    kind: str
    verdict: str
    evidence: Tuple[str, ...]
    delegates_to: Tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.module}::{self.qualname}"

    def describe(self) -> str:
        target = f" -> {', '.join(self.delegates_to)}" if self.delegates_to else ""
        return (
            f"{self.module}:{self.lineno} {self.kind} {self.qualname}"
            f"  [{self.verdict}]{target}  evidence={list(self.evidence)}"
        )


def _is_candidate_definition(node: ast.AST) -> bool:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return bool(_COMPILE_FUNCTION_NAME.search(node.name))
    if isinstance(node, ast.ClassDef):
        return bool(_COMPILER_CLASS_NAME.search(node.name))
    return False


def _walk_definitions(
    tree: ast.Module,
) -> Iterable[Tuple[ast.AST, str]]:
    """Every function/class definition in the module, with a dotted qualified name."""

    def recurse(body: Sequence[ast.stmt], prefix: str) -> Iterable[Tuple[ast.AST, str]]:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = f"{prefix}{node.name}"
                yield node, qualname
                yield from recurse(node.body, f"{qualname}.")
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                yield from recurse(node.body, prefix)
                yield from recurse(getattr(node, "orelse", []) or [], prefix)
                yield from recurse(getattr(node, "finalbody", []) or [], prefix)
                for handler in getattr(node, "handlers", []) or []:
                    yield from recurse(handler.body, prefix)

    yield from recurse(tree.body, "")


def _delegation_targets(use: DefinitionUse) -> Tuple[str, ...]:
    targets = set(use.called & CANONICAL_TARGETS)
    targets |= set(use.identifiers & CANONICAL_TARGETS)
    for module in use.imported_modules:
        for canonical in CANONICAL_MODULES:
            if canonical in module:
                targets.add(module)
    return tuple(sorted(targets))


def _ownership_evidence(
    use: DefinitionUse,
    facts: ModuleFacts,
    delegates: Sequence[str],
    assembly: FrozenSet[str] = ASSEMBLY_CONSTRUCTORS,
) -> Tuple[str, ...]:
    evidence: List[str] = []

    if use.assigned & INLINE_SORT_ASSIGNMENTS:
        evidence.append(
            "inline_kahn:" + ",".join(sorted(use.assigned & INLINE_SORT_ASSIGNMENTS))
        )
    if "TopologicalSorter" in use.identifiers:
        evidence.append("graphlib_TopologicalSorter")

    local_order = sorted(
        name
        for name in (use.self_called | (use.called & facts.defined))
        if _ORDERING_HELPER.search(name)
        and name in facts.defined
        and name not in facts.imported
    )
    if local_order:
        evidence.append("module_local_ordering:" + ",".join(local_order))

    local_tables = sorted(
        name
        for name in use.identifiers
        if _COMPAT_TABLE.search(name) and name in facts.module_constants
    )
    if local_tables:
        evidence.append("module_local_edge_legality_table:" + ",".join(local_tables))

    if not delegates:
        assembled = sorted(use.called & assembly)
        if assembled:
            evidence.append("plan_assembly_without_delegation:" + ",".join(assembled))
        evidence.append("reaches_no_canonical_entry_point")

    return tuple(evidence)


def classify_definition(
    node: ast.AST, qualname: str, facts: ModuleFacts
) -> Candidate:
    use = definition_use(node)
    kind = "class" if isinstance(node, ast.ClassDef) else "function"

    graph_tokens = sorted(use.identifiers & GRAPH_TOKENS)
    non_graph_tokens = sorted(use.identifiers & NON_GRAPH_TOKENS)
    delegates = _delegation_targets(use)

    if not graph_tokens:
        reason = (
            "no_graph_vocabulary"
            if not non_graph_tokens
            else "other_subject_matter:" + ",".join(non_graph_tokens)
        )
        return Candidate(
            module=rel(facts.path),
            qualname=qualname,
            lineno=getattr(node, "lineno", 0),
            kind=kind,
            verdict=NOT_GRAPH,
            evidence=(reason,),
            delegates_to=delegates,
        )

    ownership = _ownership_evidence(use, facts, delegates)
    verdict = AUTHORITY if ownership else DELEGATE
    evidence = ownership or ("graph_tokens:" + ",".join(graph_tokens[:6]),)

    return Candidate(
        module=rel(facts.path),
        qualname=qualname,
        lineno=getattr(node, "lineno", 0),
        kind=kind,
        verdict=verdict,
        evidence=evidence,
        delegates_to=delegates,
    )


def classify_module(path: Path) -> List[Candidate]:
    """Every compile-named definition in one module, classified."""
    facts = module_facts(path)
    return [
        classify_definition(node, qualname, facts)
        for node, qualname in _walk_definitions(facts.tree)
        if _is_candidate_definition(node)
    ]


def classify_tree(root: Path) -> List[Candidate]:
    """Every compile-named definition under `root`, classified."""
    found: List[Candidate] = []
    for path in iter_python_modules(root):
        found.extend(classify_module(path))
    return found


def authorities(candidates: Iterable[Candidate]) -> List[Candidate]:
    return [c for c in candidates if c.verdict == AUTHORITY]


def graph_compile_owners_by_behaviour(path: Path) -> List[str]:
    """Definitions of *any* name in one module that own a graph compile.

    Name-independent, so a reintroduced compiler called `GraphBuilder.build_plan` is caught
    just as `DAGCompiler.compile` would be.
    """
    facts = module_facts(path)
    owners: List[str] = []
    for node, qualname in _walk_definitions(facts.tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        use = definition_use(node)
        if not (use.identifiers & GRAPH_TOKENS):
            continue
        delegates = _delegation_targets(use)
        evidence = [
            item
            # Narrower than the compile-named scan on purpose: only the *canonical*
            # artifact counts as assembly here, so wrapping a loose payload in a legacy
            # `ExecutionGraph` for the optimization engine is not convicted, while
            # hand-building a `CompiledPlan` is.
            for item in _ownership_evidence(use, facts, delegates, CANONICAL_ARTIFACTS)
            # The "no delegation" fallback is too broad to apply to every definition in a
            # router: a pure helper that touches a node dict delegates to nothing and
            # compiles nothing. Behavioural ownership needs positive machinery.
            if item != "reaches_no_canonical_entry_point"
        ]
        if evidence:
            owners.append(f"{rel(path)}:{node.lineno} {qualname} {evidence}")
    return owners


def own_topological_sorts(path: Path) -> List[str]:
    """Definitions in one module that implement a topological sort inline."""
    facts = module_facts(path)
    hits: List[str] = []
    for node, qualname in _walk_definitions(facts.tree):
        use = definition_use(node)
        if (use.assigned & INLINE_SORT_ASSIGNMENTS) or (
            "TopologicalSorter" in use.identifiers
        ):
            hits.append(f"{rel(path)}:{node.lineno} {qualname}")
    return hits


def defined_names(path: Path, names: Iterable[str]) -> List[str]:
    """Occurrences of `names` as class/function *definitions* in one module."""
    wanted = set(names)
    facts = module_facts(path)
    return [
        f"{rel(path)}:{node.lineno} {node.name}"
        for node in ast.walk(facts.tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]


# ---------------------------------------------------------------------------
# 7. Fixtures - the scan runs once
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def modules() -> List[Path]:
    return iter_python_modules(BACKEND_APP)


@pytest.fixture(scope="module")
def candidates() -> List[Candidate]:
    return classify_tree(BACKEND_APP)


def _table(candidates: Sequence[Candidate]) -> str:
    return "\n".join(c.describe() for c in candidates)


# ---------------------------------------------------------------------------
# 8. Discovery is real
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_the_walker_finds_the_backend(self, modules: List[Path]):
        assert BACKEND_APP.is_dir(), BACKEND_APP
        assert len(modules) >= MIN_DISCOVERED_MODULES, (
            f"only {len(modules)} modules discovered under {BACKEND_APP}; the walker is "
            "broken and every other assertion in this file would pass vacuously"
        )
        assert CANONICAL_COMPILER in modules
        assert any(ROUTERS_DIR in path.parents for path in modules)

    def test_every_discovered_module_parses(self, modules: List[Path]):
        """A module that cannot be parsed is a module that cannot be checked."""
        broken = []
        for path in modules:
            try:
                ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                broken.append(f"{rel(path)}: {exc}")
        assert broken == [], broken

    def test_the_known_compile_named_definitions_are_all_seen(
        self, candidates: List[Candidate]
    ):
        """The classifier's input floor. Zero candidates would satisfy every property."""
        seen = {c.key for c in candidates}
        missing = sorted(KNOWN_CANDIDATES - seen)
        assert missing == [], f"classifier stopped seeing:\n{missing}\n\n{_table(candidates)}"

    def test_every_candidate_carries_a_verdict_and_evidence(
        self, candidates: List[Candidate]
    ):
        for candidate in candidates:
            assert candidate.verdict in {AUTHORITY, DELEGATE, NOT_GRAPH}, candidate
            assert candidate.evidence, candidate


# ---------------------------------------------------------------------------
# 9. Requirement 3.1 - one compile authority, in one module
# ---------------------------------------------------------------------------


class TestSingleGraphCompileAuthority:
    def test_only_strategy_compiler_defines_a_graph_compile_authority(
        self, candidates: List[Candidate]
    ):
        """The uniqueness property. This is the half task 2.4's tests do not cover."""
        holders = sorted({c.module for c in authorities(candidates)})
        assert holders == [rel(CANONICAL_COMPILER)], (
            "a graph compile authority exists outside the canonical compiler - that is "
            f"defect SB-01 reintroduced.\n\n{_table(authorities(candidates))}"
        )

    def test_the_authority_surface_is_exactly_the_pinned_set(
        self, candidates: List[Candidate]
    ):
        """Pinned by name, so a *third* rule set added to the blessed file is reviewed too."""
        found = {c.qualname for c in authorities(candidates)}
        assert found == set(EXPECTED_AUTHORITIES), (
            f"authority surface changed.\nexpected={sorted(EXPECTED_AUTHORITIES)}\n"
            f"found={sorted(found)}\n\n{_table(candidates)}"
        )

    def test_every_other_compile_named_definition_delegates_or_is_unrelated(
        self, candidates: List[Candidate]
    ):
        for candidate in candidates:
            if candidate.verdict == AUTHORITY:
                continue
            if candidate.verdict == DELEGATE:
                assert candidate.delegates_to, (
                    f"{candidate.key} is graph-shaped but names no canonical entry point; "
                    "it cannot be called a delegate"
                )

    def test_compile_version_delegates_rather_than_holding_its_own_rules(
        self, candidates: List[Candidate]
    ):
        """`strategy_builder.compile_version` is an orchestration seam, not a compiler.

        It is compile-named and graph-shaped, so it has to be accounted for explicitly: it
        is permitted precisely because it *delegates*. It validates once through
        `strategy_dag.validator` and compiles through `strategy_compiler.compile_graph`,
        adding persistence-shaped packaging (`CompiledVersion`, `canonical_columns`) and no
        graph rules at all. If it ever grew its own ordering or its own legality table it
        would classify as an authority and the uniqueness test above would fail.
        """
        seam = [
            c
            for c in candidates
            if c.key == "backend_app/backend/strategy_builder.py::compile_version"
        ]
        assert len(seam) == 1, candidates
        assert seam[0].verdict == DELEGATE, seam[0].describe()
        assert "compile_graph" in seam[0].delegates_to, seam[0].describe()

    def test_no_router_defines_a_graph_compile_authority(
        self, candidates: List[Candidate]
    ):
        """A router owning graph logic is the exact layering violation SB-01 came from."""
        offenders = [
            c.describe()
            for c in authorities(candidates)
            if c.module.startswith("backend_app/routers/")
        ]
        assert offenders == [], offenders

    def test_no_router_owns_a_graph_compile_under_any_name(self):
        """Name-independent. `DAGCompiler` is gone; `GraphPlanner.build_plan` would be next.

        Ownership here means deriving an order, holding an edge-legality table, or building
        a `CompiledPlan` by hand. It deliberately does *not* mean touching a legacy
        `ExecutionGraph`: `walk_forward_optimization._execute_walk_forward` wraps a loose
        `dag_config` in an `ExecutionGraph` + `StrategyPackage` with `execution_order=[]`
        purely to satisfy the optimization engine's signature. That is a pre-canonical
        adapter and a fair thing to migrate, but it derives nothing, validates nothing and
        emits no hash, so it is not a second compiler and this test must not claim it is.
        """
        offenders: List[str] = []
        for path in iter_python_modules(ROUTERS_DIR):
            offenders.extend(graph_compile_owners_by_behaviour(path))
        assert offenders == [], offenders

    def test_no_router_implements_a_topological_sort(self):
        """`_optimize_dag_structure` prunes a graph, but borrows the one loose-dict Kahn
        from `strategy_compiler.loose_execution_order` rather than carrying a second."""
        offenders: List[str] = []
        for path in iter_python_modules(ROUTERS_DIR):
            offenders.extend(own_topological_sorts(path))
        assert offenders == [], offenders

    def test_the_router_compile_seams_reach_the_canonical_compiler(
        self, candidates: List[Candidate]
    ):
        """The positive half: the routers still compile, through the one entry point."""
        router_seams = {
            c.qualname: c
            for c in candidates
            if c.module.startswith("backend_app/routers/") and c.verdict == DELEGATE
        }
        assert "_compile_payload" in router_seams, sorted(router_seams)
        assert "compile_strategy" in router_seams, sorted(router_seams)
        assert "compile_version" in router_seams["_compile_payload"].delegates_to
        assert CANONICAL_TARGETS & set(router_seams["compile_strategy"].delegates_to)


# ---------------------------------------------------------------------------
# 10. The SB-01 class names are gone from the whole backend
# ---------------------------------------------------------------------------


class TestSB01ClassNamesAreGone:
    """Task 2.9's first clause. `tests/test_task_2_4_call_site_migration.py`
    (`TestCompilerSingularity`) already asserts this for `backend_app/routers/` and also
    checks the router module's attributes and `ast.Name` references. Asserted here over the
    whole of `backend_app/` so this file stands alone as the phase-2 architecture gate."""

    def test_neither_name_is_defined_anywhere_under_backend_app(
        self, modules: List[Path]
    ):
        offenders: List[str] = []
        for path in modules:
            offenders.extend(defined_names(path, SB01_CLASS_NAMES))
        assert offenders == [], offenders

    def test_the_names_survive_only_as_prose(self, modules: List[Path]):
        """They *are* still mentioned - in comments recording the deletion. That is why
        every check in this file parses instead of grepping; a grep would fail here."""
        mentions = sum(
            path.read_text(encoding="utf-8").count(name)
            for path in modules
            for name in SB01_CLASS_NAMES
        )
        assert mentions > 0, (
            "the SB-01 narrative has been scrubbed from the source; the parse-not-grep "
            "precaution this file relies on is no longer being exercised"
        )


# ---------------------------------------------------------------------------
# 11. No false positives on the unrelated `compile` names
# ---------------------------------------------------------------------------


class TestUnrelatedCompileNamesAreIgnored:
    #: Real modules that call something named `compile` that has nothing to do with a DAG.
    UNRELATED = (
        "backend_app/backend/ml_models.py",  # Keras model.compile(...)
        "backend_app/backend/api_key_vault.py",  # re.compile(...)
        "backend_app/backend/strategy_dag/schema.py",  # re.compile(...)
    )

    @pytest.mark.parametrize("relative", UNRELATED)
    def test_those_call_sites_exist(self, relative: str):
        """Assert the premise first, so the next test is not passing for a lazy reason."""
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert ".compile(" in source, relative

    @pytest.mark.parametrize("relative", UNRELATED)
    def test_and_are_not_mistaken_for_a_compile_entry_point(self, relative: str):
        """`re.compile` and `model.compile` are calls on someone else's object. A call is
        not a definition, so neither can ever be a candidate."""
        found = classify_module(REPO_ROOT / relative)
        assert [c.describe() for c in found] == [], relative

    def test_the_data_classes_around_the_compiler_are_not_candidates(
        self, candidates: List[Candidate]
    ):
        """`CompiledPlan`, `CompiledVersion`, `CompilerError`, `StrategyCompileRequest` and
        `get_compiler` all sit next to the compiler without being an entry point."""
        names = {c.qualname for c in candidates}
        for benign in (
            "CompiledPlan",
            "CompiledVersion",
            "CompilerError",
            "StrategyCompileRequest",
            "get_compiler",
        ):
            assert benign not in names, benign


# ---------------------------------------------------------------------------
# 12. The guard bites - the classifier run over synthetic sources
# ---------------------------------------------------------------------------
#
# A uniqueness test that cannot fail is worse than no test. Everything below writes to
# `tmp_path` and never into `backend_app/`.

SECOND_GRAPH_COMPILER = '''
"""A second DAG compiler, of the shape task 2.4 deleted."""

TYPE_COMPATIBILITY = {("indicator", "logic"): True}


class DAGCompiler:
    def compile(self, nodes, edges):
        in_degree = {n["id"]: 0 for n in nodes}
        for edge in edges:
            in_degree[edge["target"]] += 1
        ready = sorted(k for k, v in in_degree.items() if v == 0)
        order = []
        while ready:
            current = ready.pop(0)
            order.append(current)
        return {"execution_order": order, "legal": TYPE_COMPATIBILITY}
'''

RENAMED_GRAPH_COMPILER = '''
"""The same second compiler, under a name no `compile` filter would catch."""


class GraphPlanner:
    def build_plan(self, graph):
        nodes = graph["nodes"]
        edges = graph["edges"]
        in_degree = {n["id"]: 0 for n in nodes}
        for edge in edges:
            in_degree[edge["target"]] += 1
        return sorted(in_degree)
'''

GRAPHLIB_COMPILER = '''
from graphlib import TopologicalSorter


def compile_dag(nodes, edges):
    sorter = TopologicalSorter({n["id"]: [] for n in nodes})
    return list(sorter.static_order())
'''

THIN_DELEGATE = '''
def compile_payload(payload, registry=None):
    """Exactly the seam task 2.4 replaced the router compiler with."""
    from backend_app.backend.strategy_builder import compile_version

    return compile_version(payload, registry)
'''

REGEX_COMPILE_WRAPPER = '''
import re


def compile_pattern(pattern: str):
    """A regex helper. Not a graph in sight."""
    return re.compile(pattern)
'''

KERAS_COMPILE_WRAPPER = '''
def compile_model(model, optimizer="adam", loss="mse"):
    """A Keras model, compiled. `model.compile` is a call, not a definition."""
    model.compile(optimizer=optimizer, loss=loss, metrics=["mae"])
    return model
'''

HANDBUILT_PLAN = '''
def build_response(graph):
    """Claims to have compiled something without asking the compiler."""
    from backend_app.backend.strategy_dag.plan import CompiledPlan

    return CompiledPlan(
        nodes=graph["nodes"],
        edges=graph["edges"],
        execution_order=[n["id"] for n in graph["nodes"]],
        dag_hash="deadbeef",
    )
'''

RESPONSE_PROJECTION = '''
def compile_strategy(body):
    """Copies a finished plan into a response DTO. Derives no order of its own."""
    from backend_app.backend.strategy_compiler import ExecutionGraph, get_compiler

    plan = get_compiler().compile_plan(body["blueprint"])
    graph = ExecutionGraph(
        nodes=body["nodes"], edges=body["edges"], execution_order=list(plan.execution_order)
    )
    return {"execution_graph": graph.to_dict(), "dag_hash": plan.dag_hash}
'''


def _synthetic_tree(tmp_path: Path, filename: str, source: str) -> Path:
    """Write one synthetic module into a fake `backend_app/routers/` tree."""
    routers = tmp_path / "backend_app" / "routers"
    routers.mkdir(parents=True, exist_ok=True)
    path = routers / filename
    path.write_text(source, encoding="utf-8")
    return path


class TestTheGuardBites:
    def test_a_second_graph_compiler_is_caught(self, tmp_path: Path):
        path = _synthetic_tree(tmp_path, "second_compiler.py", SECOND_GRAPH_COMPILER)

        found = classify_module(path)
        verdicts = {c.qualname: c.verdict for c in found}

        assert verdicts.get("DAGCompiler") == AUTHORITY, [c.describe() for c in found]
        assert verdicts.get("DAGCompiler.compile") == AUTHORITY, [
            c.describe() for c in found
        ]
        # And the uniqueness property, run over the synthetic tree, fails.
        holders = sorted({c.module for c in authorities(classify_tree(tmp_path))})
        assert holders != [rel(CANONICAL_COMPILER)]
        with pytest.raises(AssertionError):
            assert holders == [rel(CANONICAL_COMPILER)]

    def test_the_second_compiler_would_also_fail_the_router_guards(self, tmp_path: Path):
        _synthetic_tree(tmp_path, "second_compiler.py", SECOND_GRAPH_COMPILER)
        routers = tmp_path / "backend_app" / "routers"

        owners: List[str] = []
        sorts: List[str] = []
        for path in iter_python_modules(routers):
            owners.extend(graph_compile_owners_by_behaviour(path))
            sorts.extend(own_topological_sorts(path))

        assert owners, "the name-independent router guard missed a second compiler"
        assert sorts, "the topological-sort guard missed an inline Kahn"

    def test_a_renamed_second_compiler_is_still_caught_behaviourally(
        self, tmp_path: Path
    ):
        """`build_plan` carries no `compile` in its name, so only the behavioural guard
        can see it. It must."""
        path = _synthetic_tree(tmp_path, "planner.py", RENAMED_GRAPH_COMPILER)

        assert classify_module(path) == [], "a name-based guard should not see this one"
        assert graph_compile_owners_by_behaviour(path), (
            "the behavioural guard missed a renamed graph compiler"
        )

    def test_a_graphlib_based_compiler_is_caught(self, tmp_path: Path):
        path = _synthetic_tree(tmp_path, "graphlib_compiler.py", GRAPHLIB_COMPILER)
        found = {c.qualname: c for c in classify_module(path)}
        assert found["compile_dag"].verdict == AUTHORITY, found["compile_dag"].describe()

    def test_a_hand_built_compiled_plan_in_a_router_is_caught(self, tmp_path: Path):
        """The canonical artifact may only come from the compiler. A router that fabricates
        a `CompiledPlan` has asserted a verdict the compiler never gave - SB-01 by another
        route - and its name carries no `compile` at all, so only the behavioural guard
        stands between it and production."""
        path = _synthetic_tree(tmp_path, "handbuilt.py", HANDBUILT_PLAN)

        assert classify_module(path) == [], "not compile-named, so tier 1 cannot see it"
        assert graph_compile_owners_by_behaviour(path), (
            "the behavioural guard missed a hand-built CompiledPlan"
        )

    def test_a_thin_delegate_is_permitted(self, tmp_path: Path):
        path = _synthetic_tree(tmp_path, "seam.py", THIN_DELEGATE)
        found = {c.qualname: c for c in classify_module(path)}
        assert found["compile_payload"].verdict == DELEGATE, (
            found["compile_payload"].describe()
        )
        assert authorities(classify_module(path)) == []
        assert graph_compile_owners_by_behaviour(path) == []

    def test_a_response_projection_off_a_finished_plan_is_permitted(
        self, tmp_path: Path
    ):
        """Constructing an `ExecutionGraph` from `plan.execution_order` is packaging, not
        compiling - the shape `strategy_operations.compile_strategy` really has."""
        path = _synthetic_tree(tmp_path, "projection.py", RESPONSE_PROJECTION)
        found = {c.qualname: c for c in classify_module(path)}
        assert found["compile_strategy"].verdict == DELEGATE, (
            found["compile_strategy"].describe()
        )

    @pytest.mark.parametrize(
        "filename,source",
        [
            ("regex_helper.py", REGEX_COMPILE_WRAPPER),
            ("keras_helper.py", KERAS_COMPILE_WRAPPER),
        ],
    )
    def test_an_unrelated_compile_definition_is_not_flagged(
        self, tmp_path: Path, filename: str, source: str
    ):
        """The false-positive test the task calls out: a guard that fires on `re.compile`
        or on a Keras `compile_model` is useless."""
        path = _synthetic_tree(tmp_path, filename, source)
        found = classify_module(path)

        assert found, "the definition should still be inspected"
        assert [c.verdict for c in found] == [NOT_GRAPH], [c.describe() for c in found]
        assert authorities(found) == []

    def test_discovery_covers_a_module_added_later(self, tmp_path: Path):
        """A module dropped into the tree tomorrow is classified without editing this
        test - the reason discovery walks instead of reading a list."""
        _synthetic_tree(tmp_path, "existing.py", THIN_DELEGATE)
        before = {c.key for c in classify_tree(tmp_path)}

        _synthetic_tree(tmp_path, "added_tomorrow.py", SECOND_GRAPH_COMPILER)
        after = {c.key for c in classify_tree(tmp_path)}

        assert after - before, "the walker did not pick up a newly added module"
        assert any("added_tomorrow" in key for key in after - before)


# ---------------------------------------------------------------------------
# 13. The reviewable inventory
# ---------------------------------------------------------------------------


def test_the_classified_inventory_is_printable(capsys):
    """Emits the whole classified candidate list, so the classification is reviewable
    rather than merely asserted. Run with `-s` to read it."""
    found = classify_tree(BACKEND_APP)
    print("\n--- compile-named definitions under backend_app/ ---")
    for candidate in found:
        print(candidate.describe())
    print(f"--- {len(found)} candidate(s), {len(authorities(found))} authority(ies) ---")
    assert found


if __name__ == "__main__":  # pragma: no cover - reviewer convenience
    inventory = classify_tree(BACKEND_APP)
    for item in inventory:
        print(item.describe())
    print(f"\n{len(inventory)} candidate(s); authorities:")
    for item in authorities(inventory):
        print(f"  {item.module}::{item.qualname}")
