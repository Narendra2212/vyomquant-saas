# -*- coding: utf-8 -*-
"""Compilation performs no input or output, so it can run inline in a request.

Spec: strategy-builder task 9.5. `design.md` -> Performance: "Compile budget | p95 < 50 ms at
200 nodes | **pure in-memory work, no I/O**" and "Compile is deliberately I/O-free so it can
run inline in a request; only training and backtests go through the queue." Requirement 25.3.

Why this is a structural test and not a paragraph
------------------------------------------------
"I/O-free" is a claim about what the code *can* do, not about what one run happened to do.
Timing a compile proves nothing: a compile that reads Redis on a warm cache is fast and still
not I/O-free, and the first request after a deploy is the one that would find out. A
statement in a task note ages the moment somebody adds a lazy `from ... import supabase`
inside a validation stage to look up an entitlement. So the property is asserted the way task
2.9's `tests/test_compiler_architecture.py` asserts the single-compiler property: by parsing
the code and reasoning over the AST, so that the *class* of regression is unrepresentable
rather than the instance being absent today.

This file reuses that file's discipline (`ast`, never `grep`, because these modules carry
prose that names the very things being forbidden), its walker shape and its resilience rules
(a floor on what discovery finds, and guard-bites tests that prove the scanner catches what it
claims to catch). It does not reuse its classifier: that one answers "who owns a compile", and
this one answers "what does the compile touch".

How the compile path is delimited
---------------------------------
A **call closure** from the three entry points a request actually reaches -
`StrategyCompiler.compile_plan` (the canonical path, called directly by three endpoints), the
module-level `compile_graph`, and `strategy_builder.compile_version` (which the remaining call
sites reach through `compile_graph`) - followed through the modules that implement
compilation:

    strategy_compiler.py   the compiler
    strategy_builder.py    validate + compile + artifact, what the routers call
    strategy_dag/plan.py   the compiled artifact
    strategy_dag/validator.py   the eleven validation stages
    strategy_dag/schema.py      parsing, canonicalisation, the hash
    strategy_dag/block_specs.py param validation
    metrics.py             the `builder.compile.*` recorders (task 9.1)
    ml_training_policy.py  `ml_readiness_stage`, called by the readiness validation stage

Resolution is **deliberately conservative**: a called name is followed into *every* definition
of that bare name in any of those modules. That over-approximates the closure - it enters
functions the real control flow may never reach - and over-approximation is the safe
direction, because it can only make the scan look at more code than it has to. The measured
closure is 229 definitions.

`strategy_dag/registry.py` is the one **boundary**, and it is a boundary for a stated reason
rather than an exclusion of convenience: `get_registry()` is memoised, so descriptor assembly
happens at most once per process and is not on the compile path at all. That reason is not
taken on trust - `TestTheRegistryBoundaryIsMemoised` compiles twice with a counted
`build_registry` and asserts zero assemblies, and a static check asserts the compile path
names *only* `get_registry` and never `build_registry`, `reset_registry` or
`default_sources`, so compilation cannot trigger an assembly even once. Assembly itself is
not I/O-free by this file's definition - it imports `exchange_executor` for the `OrderType`
enum - which is exactly why the boundary is asserted instead of assumed.

What counts as input or output
------------------------------
An explicit denylist, in three parts, each entry present for a reason:

* **Builtins** that are I/O or arbitrary execution: `open`, `input`, `print`, `breakpoint`,
  `eval`, `exec`, `__import__`.
* **Import roots** that are the door to a filesystem, a socket, a subprocess, a database, a
  cache or an event loop: `os`, `pathlib`, `socket`, `subprocess`, `asyncio`, `requests`,
  `httpx`, `supabase`, `redis`, `celery`, … and the first-party modules that hold the
  platform's own I/O - `exchange_executor`, `data_seeking_engine`, `redis_manager`,
  `core.database`, `core.dependencies`, and so on.
* **Method names that no dict, list or dataclass has**, so they cannot be a false positive:
  `read_text`, `write_bytes`, `urlopen`, `check_output`, `apply_async`, `run_in_executor`,
  `read_csv`, `to_sql`, `sleep`, `getenv`, `import_module`, … Generic names (`get`, `run`,
  `save`, `load`, `execute`, `table`) are **not** on this list: `node.get("id")` and
  `path.remove(x)` are dict and list operations, and a scanner that convicts them is a
  scanner nobody will keep. Those idioms are caught by the import root instead - you cannot
  reach a Supabase table without importing something.
* **`await` and `async def`** anywhere in the closure. Not I/O by itself, but a compile that
  needed to await is a compile that cannot be called from a synchronous seam, and the
  design's "inline in a request" is exactly that call.

Deliberately **not** forbidden, recorded so the omissions read as decisions:

* **`logging`.** Every module in the closure logs, and the platform's handler configuration
  is not this task's to relitigate. Logging is buffered, non-blocking in this application's
  configuration, and universal; a rule that forbade it would have to be suppressed everywhere
  and would then be measuring nothing.
* **`time.perf_counter`, `time.time`, `secrets.token_hex`, `hashlib`.** A monotonic clock read
  and a CSPRNG draw are syscalls, not input or output: no descriptor is opened, nothing
  blocks on a peer, and no result depends on state outside the process. `time.sleep` *is*
  forbidden, because deliberately not doing work inside a request budget is the failure this
  is about.
* **`json.dumps` / `json.loads`.** String work. `json.load` / `json.dump` take a file object
  and are reached only through `open`, which is forbidden.

Nothing is mocked, no production code is touched, and the guard-bites section runs the real
scanner over synthetic sources in `tmp_path`: a lazy `supabase` import, a `redis` read, an
`open()`, a `time.sleep`, an `await` and a queue dispatch must each be caught, while a
`json.dumps`, a `hashlib` hash, a `time.perf_counter` and a `logger.info` must not.
"""

import ast
from collections import defaultdict
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, NamedTuple, Sequence, Set, Tuple

import pytest


REPO_ROOT: Path = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 1. The compile path: which modules implement it, and where it starts
# ---------------------------------------------------------------------------

#: The modules the closure is followed through. Every one of them is code the compile path
#: executes; none is here to pad the count.
COMPILE_SURFACE: Tuple[str, ...] = (
    "backend_app/backend/strategy_compiler.py",
    "backend_app/backend/strategy_builder.py",
    "backend_app/backend/strategy_dag/plan.py",
    "backend_app/backend/strategy_dag/validator.py",
    "backend_app/backend/strategy_dag/schema.py",
    "backend_app/backend/strategy_dag/block_specs.py",
    "backend_app/backend/metrics.py",
    "backend_app/backend/ml_training_policy.py",
)

#: The subset of the surface whose *module scope* must also be clean, because these modules
#: exist to compile and nothing else. `metrics.py` qualifies too.
#:
#: `ml_training_policy.py` is deliberately absent. The compile path enters it at exactly one
#: door - `ml_readiness_stage`, called by the readiness validation stage - but the module as a
#: whole is the ML **training admission gate**, and its module scope legitimately imports
#: `os` (a configuration default read once at import) and `core.tenant` (the plan and quota
#: dataclasses the admission gate compares against). Neither is reachable from the closure,
#: which is the claim that matters and which the closure scan already makes: every definition
#: this file follows into that module is scanned, and the property test below covers all
#: thirteen of them. Forbidding the module's own imports would be forbidding the training
#: gate from existing, which is not what "compile is I/O-free" says.
ML_TRAINING_POLICY = "backend_app/backend/ml_training_policy.py"

COMPILE_CORE: Tuple[str, ...] = tuple(
    module for module in COMPILE_SURFACE if module != ML_TRAINING_POLICY
)

#: The three entry points a request reaches. `compile_plan` is called directly by
#: `/strategies/compile`, the node-preview endpoint and the data-quality endpoint;
#: `compile_version` is what `routers/strategies._compile_payload` and `strategy_service`
#: call, and it reaches `compile_plan` through the module-level `compile_graph`.
COMPILE_ENTRY_POINTS: Tuple[Tuple[str, str], ...] = (
    ("backend_app/backend/strategy_compiler.py", "StrategyCompiler.compile_plan"),
    ("backend_app/backend/strategy_compiler.py", "compile_graph"),
    ("backend_app/backend/strategy_builder.py", "compile_version"),
)

#: The declared boundary. Memoised, and both halves of that claim are asserted below.
REGISTRY_MODULE = "backend_app/backend/strategy_dag/registry.py"

#: Registry names that would *trigger* an assembly. The compile path must name none of them.
ASSEMBLY_TRIGGERS: FrozenSet[str] = frozenset(
    {"build_registry", "reset_registry", "default_sources", "DescriptorSources"}
)

#: Closure floor. A broken walker would make every assertion below vacuously true, so the
#: size is pinned to well under the measured 229 and the key definitions are named.
MIN_CLOSURE_SIZE: int = 150

#: Definitions the closure must contain, or it is not the compile path.
KNOWN_IN_CLOSURE: FrozenSet[str] = frozenset(
    {
        "backend_app/backend/strategy_compiler.py::StrategyCompiler.compile_plan",
        "backend_app/backend/strategy_compiler.py::StrategyCompiler._execution_order",
        "backend_app/backend/strategy_compiler.py::StrategyCompiler._assert_plan_postconditions",
        "backend_app/backend/strategy_builder.py::compile_version",
        "backend_app/backend/strategy_dag/validator.py::validate",
        "backend_app/backend/strategy_dag/validator.py::topological_order",
        "backend_app/backend/strategy_dag/plan.py::CompiledPlan.from_graph",
        "backend_app/backend/strategy_dag/schema.py::compute_dag_hash",
        "backend_app/backend/strategy_dag/schema.py::parse_v2",
        "backend_app/backend/strategy_dag/block_specs.py::validate_block_params",
    }
)

# ---------------------------------------------------------------------------
# 2. What counts as input or output
# ---------------------------------------------------------------------------

DENIED_BUILTINS: FrozenSet[str] = frozenset(
    {"open", "input", "print", "breakpoint", "eval", "exec", "__import__", "compile"}
)

#: Import roots that are a door to a filesystem, socket, subprocess, database, cache or loop.
DENIED_IMPORT_ROOTS: FrozenSet[str] = frozenset(
    {
        "os",
        "io",
        "pathlib",
        "shutil",
        "tempfile",
        "glob",
        "fileinput",
        "socket",
        "select",
        "selectors",
        "ssl",
        "subprocess",
        "asyncio",
        "multiprocessing",
        "concurrent",
        "signal",
        "mmap",
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "http",
        "ftplib",
        "smtplib",
        "telnetlib",
        "ccxt",
        "supabase",
        "postgrest",
        "psycopg",
        "psycopg2",
        "asyncpg",
        "sqlalchemy",
        "sqlite3",
        "redis",
        "aioredis",
        "celery",
        "kombu",
        "pika",
        "boto3",
        "botocore",
        "pickle",
        "shelve",
        "dbm",
        "csv",
        "webbrowser",
        "importlib",
        "fsspec",
        "aiofiles",
    }
)

#: The platform's own I/O, by module prefix. Reaching any of these from the compile path is
#: the regression this file exists to make loud - a lazy
#: `from backend_app.core.dependencies import get_db` inside a validation stage is exactly the
#: shape that would turn a 50 ms budget into a database round trip.
DENIED_FIRST_PARTY: Tuple[str, ...] = (
    "backend_app.backend.exchange_executor",
    "backend_app.backend.exchange_vault",
    "backend_app.backend.data_seeking_engine",
    "backend_app.backend.master_executor",
    "backend_app.backend.ml_models",
    "backend_app.backend.ml_dataset",
    "backend_app.backend.backtesting_engine",
    "backend_app.backend.backtest_service",
    "backend_app.backend.backtest_runtime",
    "backend_app.backend.redis_manager",
    "backend_app.backend.event_pipeline",
    "backend_app.backend.api_key_vault",
    "backend_app.backend.strategy_service",
    "backend_app.backend.deployment_manager",
    "backend_app.core.database",
    "backend_app.core.dependencies",
    "backend_app.core.supabase",
    "backend_app.core.entitlement_engine",
    "backend_app.core.subscription_engine",
    "backend_app.core.tenant",
    "backend_app.core.ml_safety",
)

#: Method names no dict, list, set or dataclass carries, so a match cannot be a container
#: operation. Generic names are excluded on purpose - see the module docstring.
DENIED_METHODS: FrozenSet[str] = frozenset(
    {
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
        "readline",
        "readlines",
        "writelines",
        "urlopen",
        "urlretrieve",
        "check_output",
        "check_call",
        "apply_async",
        "run_in_executor",
        "run_until_complete",
        "create_task",
        "ensure_future",
        "iterdir",
        "makedirs",
        "mkdir",
        "rmtree",
        "getcwd",
        "listdir",
        "walk",
        "to_csv",
        "read_csv",
        "to_parquet",
        "read_parquet",
        "to_sql",
        "read_sql",
        "to_pickle",
        "read_pickle",
        "sleep",
        "getenv",
        "putenv",
        "system",
        "popen",
        "spawn",
        "import_module",
        "reload",
        "connect",
        "socketpair",
        "sendall",
        "recv",
    }
)


# ---------------------------------------------------------------------------
# 3. Discovery: every definition in the surface, and a bare-name index
# ---------------------------------------------------------------------------


class Definition(NamedTuple):
    module: str
    qualname: str
    node: ast.AST

    @property
    def key(self) -> str:
        return f"{self.module}::{self.qualname}"


def walk_definitions(body: Sequence[ast.stmt], prefix: str = "") -> Iterable[Tuple[ast.AST, str]]:
    """Every function/class definition, with a dotted qualified name.

    Descends into `if`/`try`/`with`/loops so a definition guarded by an availability check -
    which is how the optional model libraries are handled - is discovered too.
    """
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            qualname = f"{prefix}{node.name}"
            yield node, qualname
            yield from walk_definitions(node.body, f"{qualname}.")
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            yield from walk_definitions(node.body, prefix)
            yield from walk_definitions(getattr(node, "orelse", []) or [], prefix)
            yield from walk_definitions(getattr(node, "finalbody", []) or [], prefix)
            for handler in getattr(node, "handlers", []) or []:
                yield from walk_definitions(handler.body, prefix)


def load_surface(
    modules: Sequence[str] = COMPILE_SURFACE, root: Path = REPO_ROOT
) -> Tuple[Dict[Tuple[str, str], ast.AST], Dict[str, List[Tuple[str, str]]]]:
    """``(definitions keyed by (module, qualname), bare name -> those keys)``."""
    definitions: Dict[Tuple[str, str], ast.AST] = {}
    by_bare_name: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for relative in modules:
        path = root / relative
        assert path.is_file(), f"compile surface module missing: {relative}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node, qualname in walk_definitions(tree.body):
            definitions[(relative, qualname)] = node
            by_bare_name[qualname.split(".")[-1]].append((relative, qualname))
    return definitions, by_bare_name


def referenced_names(node: ast.AST) -> Set[str]:
    """Every identifier a definition mentions as a `Name` or an attribute.

    String constants are never read, so a docstring that says "supabase" is not a call.
    Attributes are included unqualified, which is what makes the resolution conservative:
    ``collector.record_builder_compile(...)`` resolves even though ``collector`` is a local.
    """
    names: Set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def compile_closure(
    definitions: Dict[Tuple[str, str], ast.AST],
    by_bare_name: Dict[str, List[Tuple[str, str]]],
    entry_points: Sequence[Tuple[str, str]] = COMPILE_ENTRY_POINTS,
) -> List[Definition]:
    """Definitions reachable from ``entry_points``, over-approximated by bare name."""
    missing = [entry for entry in entry_points if entry not in definitions]
    assert not missing, f"compile entry point not found: {missing}"

    reached: Set[Tuple[str, str]] = set()
    stack = list(entry_points)
    while stack:
        key = stack.pop()
        if key in reached:
            continue
        reached.add(key)
        for name in referenced_names(definitions[key]):
            for target in by_bare_name.get(name, ()):
                if target not in reached:
                    stack.append(target)
    return sorted(
        (Definition(module, qualname, definitions[(module, qualname)])
         for module, qualname in reached),
        key=lambda definition: definition.key,
    )


# ---------------------------------------------------------------------------
# 4. The scanner
# ---------------------------------------------------------------------------


class Finding(NamedTuple):
    where: str
    lineno: int
    kind: str
    detail: str

    def describe(self) -> str:
        return f"{self.where}:{self.lineno} {self.kind} -> {self.detail}"


def _denied_import(name: str) -> bool:
    if not name:
        return False
    return name.split(".")[0] in DENIED_IMPORT_ROOTS or name.startswith(DENIED_FIRST_PARTY)


def scan_for_io(definition: Definition) -> List[Finding]:
    """Every input/output idiom inside one definition, with its evidence."""
    findings: List[Finding] = []
    where = definition.key

    for child in ast.walk(definition.node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name) and func.id in DENIED_BUILTINS:
                findings.append(Finding(where, child.lineno, "builtin", func.id))
            elif isinstance(func, ast.Attribute) and func.attr in DENIED_METHODS:
                findings.append(
                    Finding(where, child.lineno, "method", ast.unparse(func)[:80])
                )
        elif isinstance(child, ast.Await):
            findings.append(Finding(where, child.lineno, "await", ast.unparse(child)[:80]))
        elif isinstance(child, ast.AsyncFunctionDef) and child is not definition.node:
            findings.append(Finding(where, child.lineno, "async def", child.name))
        elif isinstance(child, ast.Import):
            for alias in child.names:
                if _denied_import(alias.name):
                    findings.append(Finding(where, child.lineno, "import", alias.name))
        elif isinstance(child, ast.ImportFrom):
            module = child.module or ""
            if _denied_import(module):
                findings.append(Finding(where, child.lineno, "from-import", module))

    if isinstance(definition.node, ast.AsyncFunctionDef):
        findings.append(
            Finding(where, definition.node.lineno, "async def", definition.qualname)
        )
    return findings


# ---------------------------------------------------------------------------
# 5. Fixtures - the scan runs once
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def surface():
    return load_surface()


@pytest.fixture(scope="module")
def closure(surface) -> List[Definition]:
    definitions, by_bare_name = surface
    return compile_closure(definitions, by_bare_name)


@pytest.fixture(scope="module")
def findings(closure) -> List[Finding]:
    return [finding for definition in closure for finding in scan_for_io(definition)]


@pytest.fixture
def report(request, capsys):
    """Print the measurement only when output is not being captured (`pytest -s`).

    The closure is 229 definitions. Emitting it unconditionally would add 229 lines to every
    run of a 4 800-test suite, and a scan nobody can read is no better than one nobody
    printed - so it is available on demand and silent otherwise.
    """
    enabled = request.config.getoption("capture") == "no"

    def emit(*lines: str) -> None:
        if not enabled:
            return
        with capsys.disabled():
            for line in lines:
                print(line)

    return emit


# ---------------------------------------------------------------------------
# 6. Discovery is real, so nothing below passes vacuously
# ---------------------------------------------------------------------------


class TestTheClosureIsReal:
    def test_the_compile_surface_parses(self, surface):
        definitions, _ = surface
        assert definitions, "no definitions discovered in the compile surface"

    def test_the_closure_reaches_the_expected_size(self, closure, report):
        by_module: Dict[str, int] = defaultdict(int)
        for definition in closure:
            by_module[definition.module] += 1
        report(
            f"\n  compile closure: {len(closure)} definitions",
            *(f"    {by_module[module]:>4}  {module}" for module in sorted(by_module)),
        )

        assert len(closure) >= MIN_CLOSURE_SIZE, (
            f"only {len(closure)} definitions reached; the walker is broken and the I/O "
            "scan below would pass vacuously"
        )

    def test_the_closure_holds_the_definitions_that_do_the_compiling(self, closure):
        keys = {definition.key for definition in closure}
        missing = sorted(KNOWN_IN_CLOSURE - keys)
        assert missing == [], f"the closure stopped reaching:\n{missing}"

    def test_every_surface_module_contributes(self, closure):
        """A module in the surface that the closure never enters is a surface that lies."""
        reached = {definition.module for definition in closure}
        assert reached == set(COMPILE_SURFACE), sorted(set(COMPILE_SURFACE) - reached)


# ---------------------------------------------------------------------------
# 7. The property
# ---------------------------------------------------------------------------


class TestCompilationPerformsNoInputOrOutput:
    def test_the_compile_closure_holds_no_input_or_output(self, findings):
        """`design.md` -> Performance: "pure in-memory work, no I/O"."""
        assert [finding.describe() for finding in findings] == [], (
            "the compile path reached input or output:\n"
            + "\n".join(finding.describe() for finding in findings)
        )

    def test_nothing_on_the_compile_path_is_a_coroutine(self, closure):
        """"Inline in a request" means a synchronous seam can call it and get an answer."""
        coroutines = [
            definition.key
            for definition in closure
            if isinstance(definition.node, ast.AsyncFunctionDef)
        ]
        assert coroutines == [], coroutines

    def test_the_entry_points_are_synchronous_callables(self):
        """Asserted on the real objects, not only on the AST."""
        import inspect

        from backend_app.backend.strategy_builder import compile_version
        from backend_app.backend.strategy_compiler import (
            StrategyCompiler,
            compile_graph,
        )

        for entry in (StrategyCompiler.compile_plan, compile_graph, compile_version):
            assert not inspect.iscoroutinefunction(entry), entry
            assert not inspect.isasyncgenfunction(entry), entry

    def test_no_core_module_imports_input_or_output_at_module_scope(self):
        """A module-scope import runs once, but it is the easiest place to put a client that
        a stage then reaches through a module global - no import statement inside the
        function for the closure scan to see.

        Scoped to `COMPILE_CORE`; see that constant for why `ml_training_policy.py` is not in
        it and what is asserted about it instead.
        """
        offenders: List[str] = []
        for relative in COMPILE_CORE:
            tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _denied_import(alias.name):
                            offenders.append(f"{relative}:{node.lineno} {alias.name}")
                elif isinstance(node, ast.ImportFrom) and _denied_import(node.module or ""):
                    offenders.append(f"{relative}:{node.lineno} {node.module}")
        assert offenders == [], offenders


# ---------------------------------------------------------------------------
# 8. The one boundary, asserted rather than assumed
# ---------------------------------------------------------------------------


class TestTheRegistryBoundaryIsMemoised:
    def test_the_registry_is_outside_the_followed_surface(self):
        """The boundary is stated, so it is visible rather than implied by an omission."""
        assert REGISTRY_MODULE not in COMPILE_SURFACE
        assert (REPO_ROOT / REGISTRY_MODULE).is_file()

    def test_the_compile_path_never_names_an_assembly_trigger(self, closure):
        """Compilation reads the memoised registry; it can never cause an assembly.

        `build_registry` imports `exchange_executor` for the `OrderType` enum, so assembly is
        *not* I/O-free by this file's definition. That is precisely why it must be off the
        compile path, and why this is a test rather than a remark.
        """
        offenders = []
        for definition in closure:
            named = referenced_names(definition.node) & ASSEMBLY_TRIGGERS
            if named:
                offenders.append(f"{definition.key} names {sorted(named)}")
        assert offenders == [], offenders

    def test_get_registry_is_memoised(self):
        from backend_app.backend.strategy_dag import registry as registry_module

        first = registry_module.get_registry()
        second = registry_module.get_registry()
        assert first is second

    def test_compiling_twice_assembles_no_registry(self, monkeypatch):
        """The boundary's justification, measured on a real compile of a real graph."""
        from backend_app.backend.strategy_dag import registry as registry_module

        # Warm the memo first, so what is counted is the compile and not the first touch.
        registry_module.get_registry()

        calls: List[int] = []
        real_build = registry_module.build_registry

        def counted(*args, **kwargs):
            calls.append(1)
            return real_build(*args, **kwargs)

        monkeypatch.setattr(registry_module, "build_registry", counted)

        graph = _linear_graph()
        from backend_app.backend.strategy_compiler import get_compiler

        first = get_compiler().compile_plan(graph)
        second = get_compiler().compile_plan(graph)

        assert calls == [], f"compiling assembled the registry {len(calls)} time(s)"
        assert first.dag_hash == second.dag_hash


def _linear_graph():
    """``ohlcv_feed -> ema -> gt -> action_buy_market``, the smallest tradeable graph.

    The same shape `tests/test_task_9_1_builder_metrics.py` compiles, so a change to the
    registry's required params breaks both files together rather than one of them silently.
    """
    from backend_app.backend.strategy_dag.registry import get_registry
    from backend_app.backend.strategy_dag.schema import EdgeSpec, NodeSpec, StrategyGraph

    registry = get_registry()

    def node(node_id, block_id, **params):
        return NodeSpec(
            id=node_id,
            block_id=block_id,
            category=registry[block_id].category,
            params=dict(params),
        )

    return StrategyGraph(
        schema_version=2,
        strategy_id="s-9-5",
        version="1.0.0",
        name="io-free compile",
        nodes=[
            node(
                "n_data",
                "ohlcv_feed",
                symbol="ETH/USDT",
                timeframe="5m",
                market_type="spot",
                mode="streaming",
            ),
            node("n_ema", "ema", window=20, source="close"),
            node("n_const", "constant", value=1.0),
            node("n_gt", "gt"),
            node("n_buy", "action_buy_market", quantity_type="base_amount", quantity=1.0),
        ],
        edges=[
            EdgeSpec(id="e1", source="n_data", source_port="close",
                     target="n_ema", target_port="series"),
            EdgeSpec(id="e2", source="n_ema", source_port="value",
                     target="n_gt", target_port="left"),
            EdgeSpec(id="e3", source="n_const", source_port="value",
                     target="n_gt", target_port="right"),
            EdgeSpec(id="e4", source="n_gt", source_port="out",
                     target="n_buy", target_port="signal"),
        ],
    )


# ---------------------------------------------------------------------------
# 9. It really does run inline in a request
# ---------------------------------------------------------------------------


class TestTheRequestPathCallsItInline:
    #: Every place a request handler reaches the compiler.
    CALL_SITES = (
        ("backend_app/routers/strategy_operations.py", "compile_strategy"),
        ("backend_app/routers/strategy_operations.py", "preview_node"),
        ("backend_app/routers/strategy_operations.py", "get_strategy_data_quality"),
        ("backend_app/routers/strategies.py", "_compile_payload"),
    )

    #: Idioms that hand work to somewhere other than this request. `design.md`: "only training
    #: and backtests go through the queue".
    #:
    #: Note the assertion is about **the compile call**, not about the handler: `preview_node`
    #: legitimately does `await asyncio.to_thread(engine.execute_dag, …)`, because *executing*
    #: a preview is pandas work that would block the event loop for as long as the bars take.
    #: The compile, which is the thing this file is about, is a plain synchronous call above
    #: it. A test that convicted the handler for the executor's deferral would be measuring
    #: the wrong call and would have to be suppressed, which is how a guard stops guarding.
    DEFERRAL = frozenset(
        {
            "delay",
            "apply_async",
            "send_task",
            "enqueue",
            "run_in_executor",
            "create_task",
            "ensure_future",
            "add_task",
            "to_thread",
            "spawn",
            "submit",
        }
    )

    COMPILER_NAMES = frozenset({"compile_plan", "compile_version", "compile_graph"})

    @staticmethod
    def _callee_name(call: ast.Call):
        func = call.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return None

    def _handler(self, relative: str, name: str):
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
        found = [
            node
            for node, qualname in walk_definitions(tree.body)
            if qualname.split(".")[-1] == name
        ]
        assert found, f"{relative} defines no {name}"
        return found

    @pytest.mark.parametrize("relative,name", CALL_SITES)
    def test_the_handler_still_reaches_the_compiler(self, relative, name):
        for node in self._handler(relative, name):
            called = {
                self._callee_name(child)
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
            }
            assert called & self.COMPILER_NAMES, (
                f"{relative}::{name} no longer calls the compiler"
            )

    @pytest.mark.parametrize("relative,name", CALL_SITES)
    def test_the_compile_call_is_not_handed_to_a_queue_or_a_pool(self, relative, name):
        """The compiler is called, not scheduled."""
        for node in self._handler(relative, name):
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                if self._callee_name(child) not in self.DEFERRAL:
                    continue
                arguments = list(child.args) + [kw.value for kw in child.keywords]
                for argument in arguments:
                    smuggled = referenced_names(argument) & self.COMPILER_NAMES
                    assert smuggled == set(), (
                        f"{relative}::{name}:{child.lineno} hands {sorted(smuggled)} to "
                        f"{self._callee_name(child)}; the design puts only training and "
                        "backtests on a queue"
                    )

    @pytest.mark.parametrize("relative,name", CALL_SITES)
    def test_the_compile_call_is_not_awaited(self, relative, name):
        """An awaited compile would be a compile that does I/O, by construction."""
        for node in self._handler(relative, name):
            for child in ast.walk(node):
                if not isinstance(child, ast.Await):
                    continue
                awaited = referenced_names(child.value) & self.COMPILER_NAMES
                assert awaited == set(), (
                    f"{relative}::{name}:{child.lineno} awaits {sorted(awaited)}"
                )

    def test_a_compile_endpoint_answers_in_one_request(self):
        """End to end: the endpoint returns the plan, not a job id to poll."""
        from fastapi.testclient import TestClient

        from backend_app.core.dependencies import get_current_user, get_request_supabase
        from backend_app.main import app

        app.dependency_overrides[get_current_user] = lambda: {
            "id": "usr_io_free",
            "email": "iofree@example.com",
            "role": "authenticated",
            "access_token": "token_io_free",
        }
        app.dependency_overrides[get_request_supabase] = lambda: None
        try:
            client = TestClient(app)
            response = client.post(
                "/api/strategies/compile",
                json={"blueprint": _linear_graph().to_dict(), "version": "1.0.0"},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "compiled"
        assert body["compiled_plan"]["execution_order"], body["compiled_plan"]
        assert body["dag_hash"]
        # A queued compile would answer with something to poll. This one answers with a plan.
        assert "job_id" not in body and "task_id" not in body


# ---------------------------------------------------------------------------
# 10. The scanner bites
# ---------------------------------------------------------------------------


DIRTY_SOURCES: Tuple[Tuple[str, str, str], ...] = (
    (
        "lazy_supabase_import",
        "from-import",
        """
def validate_entitlement(graph, registry):
    from backend_app.core.dependencies import get_db
    db = get_db()
    return db
""",
    ),
    (
        "redis_read",
        "import",
        """
def warm_cache(graph):
    import redis
    return redis.Redis().get("plan")
""",
    ),
    (
        "reads_a_file",
        "builtin",
        """
def load_limits(graph):
    with open("limits.json") as handle:
        return handle.read()
""",
    ),
    (
        "sleeps_inside_the_budget",
        "method",
        """
import time


def throttle(graph):
    time.sleep(0.5)
    return graph
""",
    ),
    (
        "awaits_a_round_trip",
        "await",
        """
async def compile_plan(graph, registry):
    return await fetch(graph)
""",
    ),
    (
        "defers_to_a_pool",
        "method",
        """
def compile_plan(graph, loop):
    return loop.run_in_executor(None, _work, graph)
""",
    ),
    (
        "imports_a_module_by_name",
        "method",
        """
import importlib


def resolve(graph):
    return importlib.import_module(graph.runtime_ref)
""",
    ),
    (
        "reaches_the_exchange",
        "from-import",
        """
def resolve_symbol(graph):
    from backend_app.backend.exchange_executor import CCXTExchangeExecutor
    return CCXTExchangeExecutor
""",
    ),
    (
        "writes_a_path",
        "method",
        """
def dump_plan(plan, path):
    return path.write_text(plan.to_json())
""",
    ),
)

CLEAN_SOURCES: Tuple[Tuple[str, str], ...] = (
    (
        "serialises_in_memory",
        """
import json


def to_json(plan):
    return json.dumps(plan, separators=(",", ":"), sort_keys=True)
""",
    ),
    (
        "hashes",
        """
import hashlib


def dag_hash(canonical):
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
""",
    ),
    (
        "measures_its_own_duration",
        """
import time


def compile_plan(graph):
    started = time.perf_counter()
    return (time.perf_counter() - started) * 1000.0
""",
    ),
    (
        "logs",
        """
import logging

logger = logging.getLogger(__name__)


def compile_plan(graph):
    logger.info("Compiled %s nodes", len(graph.nodes))
    return graph
""",
    ),
    (
        "walks_containers",
        """
def order(nodes, edges):
    in_degree = {node.get("id"): 0 for node in nodes}
    ready = sorted(in_degree)
    ready.remove(ready[0])
    return ready
""",
    ),
    (
        "draws_a_token",
        """
import secrets


def new_id():
    return "n_" + secrets.token_hex(4)
""",
    ),
)


def _scan_source(tmp_path: Path, filename: str, source: str) -> List[Finding]:
    """Run the real scanner over a synthetic module's every definition."""
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    tree = ast.parse(source)
    return [
        finding
        for node, qualname in walk_definitions(tree.body)
        for finding in scan_for_io(Definition(filename, qualname, node))
    ]


class TestTheScannerBites:
    @pytest.mark.parametrize(
        "name,expected_kind,source",
        [(name, kind, source) for name, kind, source in DIRTY_SOURCES],
        ids=[name for name, _, _ in DIRTY_SOURCES],
    )
    def test_an_input_or_output_idiom_is_caught(
        self, tmp_path: Path, name: str, expected_kind: str, source: str
    ):
        findings = _scan_source(tmp_path, f"{name}.py", source)
        assert findings, f"the scanner missed {name}"
        assert expected_kind in {finding.kind for finding in findings}, [
            finding.describe() for finding in findings
        ]

    @pytest.mark.parametrize(
        "name,source",
        list(CLEAN_SOURCES),
        ids=[name for name, _ in CLEAN_SOURCES],
    )
    def test_pure_in_memory_work_is_not_flagged(
        self, tmp_path: Path, name: str, source: str
    ):
        findings = _scan_source(tmp_path, f"{name}.py", source)
        assert findings == [], [finding.describe() for finding in findings]

    def test_a_dirty_definition_inside_the_real_surface_would_fail_the_property(
        self, tmp_path: Path, surface
    ):
        """The whole gate, exercised: a stage that reads the database is caught by the same
        closure walk the property uses, not only by the scanner in isolation."""
        definitions, by_bare_name = surface
        # A validation stage that the compiler's own `validate` call would reach.
        injected = ast.parse(
            "def validate(graph, registry):\n"
            "    from backend_app.core.database import get_client\n"
            "    return get_client()\n"
        ).body[0]
        polluted = dict(definitions)
        polluted[("synthetic_stage.py", "validate")] = injected
        polluted_index = {name: list(keys) for name, keys in by_bare_name.items()}
        polluted_index.setdefault("validate", []).append(("synthetic_stage.py", "validate"))

        closure = compile_closure(polluted, polluted_index)
        assert any(definition.module == "synthetic_stage.py" for definition in closure)

        findings = [f for definition in closure for f in scan_for_io(definition)]
        assert findings, "the gate did not catch a database read on the compile path"
        assert all("synthetic_stage.py" in f.where for f in findings), [
            f.describe() for f in findings
        ]


def test_the_scanned_closure_is_printable(closure, findings, report):
    """Emits the scanned closure so the property is reviewable, not merely asserted.
    Run with `-s` to read it."""
    report(
        f"\n  scanned {len(closure)} definitions, {len(findings)} finding(s)",
        *(f"    {definition.key}" for definition in closure),
    )
    assert findings == []
