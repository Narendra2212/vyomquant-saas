"""The guard against a new module re-implementing a service that already exists.

Feature: marketplace-subscriptions-paper-trading, task 33.6.
Requirements 30.2, 30.5. Requirement 30.2 is the one this file enforces: "THE Implementation SHALL
place each responsibility in one module, AND SHALL NOT duplicate an existing service, model,
validation rule or API client."

THE BASELINE - WHAT MUST NOT BE RE-IMPLEMENTED
---------------------------------------------
The six surfaces task 33.6 names, which are the ones ``design.md``'s reuse map says this spec
reuses rather than rebuilds:

* ``backend_app/core/entitlement_engine.py``   - the platform's plan entitlement decision
* ``backend_app/routers/billing.py``           - the platform's billing and checkout endpoints
* ``backend_app/backend/backtest_service.py``  - the backtest service
* ``backend_app/backend/market_data_*.py``     - the market-data contract, latency floor and
  validation (globbed, so a new ``market_data_*`` module joins the baseline automatically)
* ``backend_app/api_ws/ws_manager.py``         - the WebSocket connection registry
* ``algo22-terminal/src/api/modules/*.js``     - the browser API client

A baseline file that is absent is named in a skip rather than passed over, because a baseline that
silently shrinks to nothing would make this file assert nothing.

THE NEW-MODULE SET - HOW IT IS DERIVED
-------------------------------------
Not hardcoded. Three derivations, unioned:

1. **The two packages this spec created**, globbed: every ``.py`` under
   ``backend_app/backend/marketplace/`` and ``backend_app/backend/paper/``. A module added to
   either is audited without an edit here.
2. **The modules ``tasks.md`` says this spec creates**: the paths in a "Create ``x.py``",
   "Implement ``x.py``" or "Add the ... routes to ``x.py``" phrase in the spec's own task list.
   That is what pulls in ``backend_app/backend/execution_environment.py`` (task 5.4) and the two
   routers this spec's endpoints live in - ``routers/library.py`` (task 14.4) and
   ``routers/paper_trading.py`` (task 28.1) - which is where a duplicated service is most likely
   to be planted, since that is where the new endpoints are.
3. Minus the baseline itself, minus ``tests/``, minus anything outside ``backend_app/``.

What is deliberately **not** in the set: the modules ``tasks.md``'s overview declares "extended in
place" other than those two routers - ``paper_trading_service.py``, ``backtest_runtime.py``,
``ws_channels.py``, ``core/audit_trail.py``, ``core/websocket_auth.py`` - and pre-existing routers
this spec only touches. Their name overlaps with ``market_data_*`` pre-date this spec (thirteen
``to_dict`` methods, for one), and Requirement 30.2 constrains what the Implementation introduces.
Including them buries this spec's own signal in another effort's debt. The derivation is asserted
to cover both packages and both routers, so it cannot quietly shrink.

THE RULE - WHY A BARE NAME MATCH IS NOT ENOUGH
---------------------------------------------
A shared name alone is a weak signal: ``validate``, ``stop`` and ``frame`` are English, and two
modules may both have one without either copying the other. Matching only on names produces so
much noise that the report stops being read, which is the failure mode this file is written to
avoid. So a collision carries one of three strengths, and each is derived from the source:

* :attr:`Signal.STRONG` - same name **and** either the same non-empty set of parameter names, or a
  body-shape similarity of 0.80 or more. Body shape is the sequence of AST node type names in the
  function body with constants dropped, compared with ``difflib``; a copy-pasted function scores
  near 1.00 even after its parameters and literals are renamed, and two unrelated functions that
  merely share a name score near 0.0. An empty parameter list is not counted as "the same
  parameters", because every zero-argument function trivially matches every other one.
* :attr:`Signal.NAME_ONLY` - the name matches and neither similarity test does. Kept and reported
  rather than discarded, because it is where a genuine duplicate with renamed parameters would
  first show up, and because it is where the interesting judgement calls live
  (``library.cancel_subscription`` next to ``billing.cancel_subscription`` is the one this whole
  spec has to get right).
* :attr:`Signal.CROSS_SURFACE` - a new Python function whose name matches a JavaScript API-client
  function after ``camelCase`` is normalised to ``snake_case``, **and** whose parameter count
  matches. Parameter *names* cannot be compared across the two languages, so arity is the
  signature signal available. Without the arity requirement this check reports every endpoint
  whose client method is named after it, which is the correct design rather than a duplicate.

Every collision at any strength must appear in :data:`REGISTER` with the strength it was reviewed
at and the reason it is not a re-implementation. Nothing is filtered by module, package or
directory - dunder methods (``__init__``, ``__post_init__``, ``__eq__``) are the single kind of
exclusion, on the stated ground that they are protocol slots every dataclass has rather than
services anyone could re-implement.

The registered strength is re-derived on every run and compared, which is what gives the register
teeth: a pair reviewed as ``NAME_ONLY`` that is later edited into a copy of its namesake becomes
``STRONG`` here and the test fails naming both strengths. A reviewed pair cannot decay into a real
duplicate silently.

WHY THIS TEST CANNOT PASS VACUOUSLY
-----------------------------------
:func:`test_the_baseline_surface_is_non_empty` and
:func:`test_the_new_module_set_covers_the_packages_and_the_derived_routers` assert that both sides
of the comparison were actually found - a broken glob, a renamed package or a ``tasks.md`` whose
phrasing changed would otherwise leave this file comparing an empty set against an empty set and
passing. :class:`TestTheDetectorBites` drives the same extractor and the same rule over synthetic
Python and JavaScript sources, including a deliberate re-implementation that must be caught.
"""

from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, FrozenSet, List, NamedTuple, Optional, Sequence, Tuple

import pytest

REPO_ROOT: Path = Path(__file__).resolve().parents[1]

TASKS_FILE: Path = (
    REPO_ROOT
    / ".kiro"
    / "specs"
    / "marketplace-subscriptions-paper-trading"
    / "tasks.md"
)

# ══════════════════════════════════════════════════════════════════════════
#  THE BASELINE SURFACE
# ══════════════════════════════════════════════════════════════════════════

#: The named Python baseline modules, plus the ``market_data_*`` family resolved by glob.
BASELINE_NAMED: Tuple[str, ...] = (
    "backend_app/core/entitlement_engine.py",
    "backend_app/routers/billing.py",
    "backend_app/backend/backtest_service.py",
    "backend_app/api_ws/ws_manager.py",
)

BASELINE_GLOBS: Tuple[Tuple[str, str], ...] = (("backend_app/backend", "market_data_*.py"),)

#: The browser API client. Task 33.6 names ``src/api/modules/*``.
JS_BASELINE_DIR: Path = REPO_ROOT / "algo22-terminal" / "src" / "api" / "modules"


def baseline_python_modules() -> Tuple[List[Path], List[str]]:
    """The baseline Python files that exist, and the named ones that do not.

    Returns both halves so a caller can report an absent baseline instead of comparing against a
    smaller surface without saying so.
    """
    present: List[Path] = []
    missing: List[str] = []
    for relative in BASELINE_NAMED:
        path = REPO_ROOT / relative
        if path.is_file():
            present.append(path.resolve())
        else:
            missing.append(relative)
    for directory, pattern in BASELINE_GLOBS:
        base = REPO_ROOT / directory
        if not base.is_dir():
            missing.append(f"{directory}/{pattern}")
            continue
        matched = sorted(base.glob(pattern))
        if not matched:
            missing.append(f"{directory}/{pattern}")
        present.extend(path.resolve() for path in matched)
    return sorted(set(present)), missing


# ══════════════════════════════════════════════════════════════════════════
#  THE NEW-MODULE SET
# ══════════════════════════════════════════════════════════════════════════

#: The two packages this spec created, in full.
CREATED_PACKAGES: Tuple[Path, ...] = (
    REPO_ROOT / "backend_app" / "backend" / "marketplace",
    REPO_ROOT / "backend_app" / "backend" / "paper",
)

#: "Create ``x.py``", "Implement ``x.py``", "Add the ... routes to ``x.py``" in the task list.
_CREATES_RE = re.compile(
    r"(?:Create|Implement|Add the [^`\n]{0,60}routes to)\s+`([A-Za-z0-9_./-]+\.py)`",
    re.IGNORECASE,
)

#: Where a bare path from ``tasks.md`` is looked for. The task list writes some paths in full and
#: some as a bare module name, so each candidate is resolved against the roots it could be
#: relative to and dropped if it does not name a file on disk.
_RESOLUTION_BASES: Tuple[Path, ...] = (
    REPO_ROOT,
    REPO_ROOT / "backend_app",
    REPO_ROOT / "backend_app" / "routers",
    *CREATED_PACKAGES,
)


def _resolve_task_path(raw: str) -> Optional[Path]:
    for base in _RESOLUTION_BASES:
        candidate = base / raw
        if candidate.is_file():
            return candidate.resolve()
    return None


def modules_tasks_md_says_were_created() -> List[Path]:
    """Derivation 2: the production modules the spec's own task list says it creates."""
    if not TASKS_FILE.is_file():
        return []
    text = TASKS_FILE.read_text(encoding="utf-8")
    found: List[Path] = []
    for raw in _CREATES_RE.findall(text):
        path = _resolve_task_path(raw)
        if path is None:
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative.startswith("tests/") or not relative.startswith("backend_app/"):
            continue
        found.append(path)
    return sorted(set(found))


def new_modules() -> List[Path]:
    """The audited set: the two created packages, plus what ``tasks.md`` says was created."""
    missing = [d for d in CREATED_PACKAGES if not d.is_dir()]
    if missing:
        pytest.skip(
            "the package(s) "
            + ", ".join(str(d.relative_to(REPO_ROOT)) for d in missing)
            + " do not exist, so there is no new module to audit; this is not a pass"
        )
    audited = set()
    for package in CREATED_PACKAGES:
        audited |= {path.resolve() for path in package.rglob("*.py")}
    audited |= set(modules_tasks_md_says_were_created())
    baseline, _ = baseline_python_modules()
    audited -= set(baseline)
    return sorted(audited)


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


# ══════════════════════════════════════════════════════════════════════════
#  THE EXTRACTORS
# ══════════════════════════════════════════════════════════════════════════


class Function(NamedTuple):
    """One function or method, with everything the rule compares."""

    name: str
    qualname: str
    module: str
    line: int
    params: FrozenSet[str]
    arity: int
    shape: Tuple[str, ...]


def _body_shape(node: ast.AST) -> Tuple[str, ...]:
    """The function body as a sequence of AST node type names, constants dropped.

    Constants are dropped so a copy that changed its strings and numbers still matches. Parameter
    *names* do not appear either - they are ``ast.Name`` nodes, and only the type name is kept - so
    a copy whose arguments were renamed still matches. That is the point: this is the signal that
    survives the edits someone makes while duplicating a function.
    """
    return tuple(
        type(child).__name__
        for statement in getattr(node, "body", [])
        for child in ast.walk(statement)
        if not isinstance(child, ast.Constant)
    )


def python_functions(source: str, module: str) -> List[Function]:
    """Every ``def``/``async def`` in one module's source, methods included, dunders dropped.

    ``self``/``cls`` is stripped from a method's parameters so a method and a free function are
    comparable. Nested functions are walked too: a re-implementation hidden inside another function
    is still a re-implementation.
    """
    tree = ast.parse(source, filename=module)
    found: List[Function] = []

    def walk(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}.{child.name}" if prefix else child.name, True)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = child.args
                names = [
                    argument.arg
                    for argument in (
                        list(arguments.posonlyargs)
                        + list(arguments.args)
                        + list(arguments.kwonlyargs)
                    )
                ]
                if in_class and names and names[0] in ("self", "cls"):
                    names = names[1:]
                qualified = f"{prefix}.{child.name}" if prefix else child.name
                if not (child.name.startswith("__") and child.name.endswith("__")):
                    found.append(
                        Function(
                            name=child.name,
                            qualname=qualified,
                            module=module,
                            line=child.lineno,
                            params=frozenset(names),
                            arity=len(names),
                            shape=_body_shape(child),
                        )
                    )
                walk(child, qualified, False)

    walk(tree, "", False)
    return found


def python_functions_of(path: Path) -> List[Function]:
    return python_functions(path.read_text(encoding="utf-8"), _relative(path))


# ── the JavaScript side ──

#: The four shapes an api-client function takes in ``algo22-terminal/src/api/modules``: a
#: declaration, an arrow assigned to a binding, an arrow as an object property (which is how
#: ``billing.js`` and the rest are written), and a shorthand method.
_JS_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(
        r"(?:export\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)\s*"
        r"\((?P<params>[^)]*)\)"
    ),
    re.compile(
        r"(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
        r"(?:async\s*)?\((?P<params>[^)]*)\)\s*=>"
    ),
    re.compile(
        r"^[ \t]+(?P<name>[A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?\((?P<params>[^)]*)\)\s*=>",
        re.MULTILINE,
    ),
    re.compile(
        r"^[ \t]+(?:async\s+)?(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^)]*)\)\s*\{",
        re.MULTILINE,
    ),
)

#: Control-flow keywords the patterns above would otherwise read as function names.
_JS_KEYWORDS: FrozenSet[str] = frozenset(
    {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "function",
        "typeof",
        "await",
        "new",
        "do",
        "else",
        "try",
    }
)

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def to_snake_case(name: str) -> str:
    """``cancelSubscription`` -> ``cancel_subscription``; a name already snake_case is unchanged."""
    return _CAMEL_BOUNDARY.sub("_", name).lower()


class JsFunction(NamedTuple):
    """One JavaScript API-client function, with its name normalised for comparison."""

    name: str
    snake_name: str
    module: str
    line: int
    arity: int


def javascript_functions(source: str, module: str) -> List[JsFunction]:
    """Every api-client function in one JavaScript module.

    A regex rather than a parser, because this repository has no JavaScript AST available to the
    Python test suite. The cost is stated rather than hidden: the extraction is over-inclusive at
    the edges (a keyword, an ALL_CAPS constant, a name with an underscore already in it are
    filtered out by name) and a function whose parameter list spans lines is missed. It is good
    enough for the check it feeds, which is a name-and-arity comparison, and
    :func:`test_the_baseline_surface_is_non_empty` fails if it ever stops finding functions at all.
    """
    found: Dict[Tuple[str, int], JsFunction] = {}
    for pattern in _JS_PATTERNS:
        for match in pattern.finditer(source):
            name = match.group("name")
            if name in _JS_KEYWORDS or name.isupper() or "_" in name:
                continue
            raw = match.group("params") or ""
            arity = len([part for part in raw.split(",") if part.strip()])
            line = source[: match.start()].count("\n") + 1
            found.setdefault(
                (name, line),
                JsFunction(
                    name=name,
                    snake_name=to_snake_case(name),
                    module=module,
                    line=line,
                    arity=arity,
                ),
            )
    return [found[key] for key in sorted(found, key=lambda key: (key[1], key[0]))]


def javascript_baseline() -> List[JsFunction]:
    """Every api-client function across the JavaScript baseline directory."""
    if not JS_BASELINE_DIR.is_dir():
        return []
    found: List[JsFunction] = []
    for path in sorted(JS_BASELINE_DIR.glob("*.js")):
        found.extend(
            javascript_functions(
                path.read_text(encoding="utf-8", errors="replace"), _relative(path)
            )
        )
    return found


# ══════════════════════════════════════════════════════════════════════════
#  THE RULE
# ══════════════════════════════════════════════════════════════════════════

#: A body-shape similarity at or above this is treated as the same implementation.
SHAPE_THRESHOLD: float = 0.80


class Signal(Enum):
    """How strong the evidence is that a collision is a re-implementation."""

    STRONG = "strong"
    NAME_ONLY = "name-only"
    CROSS_SURFACE = "cross-surface"


@dataclass(frozen=True)
class Collision:
    """One new function whose name already exists on the baseline surface."""

    name: str
    new_module: str
    new_qualname: str
    new_line: int
    base_module: str
    base_qualname: str
    base_line: int
    signal: Signal
    shape_ratio: float
    same_params: bool

    @property
    def key(self) -> Tuple[str, str, str, str]:
        return (self.new_module, self.new_qualname, self.base_module, self.base_qualname)

    def describe(self) -> str:
        return (
            f"  {self.name!r}: {self.new_module}:{self.new_line} ({self.new_qualname})\n"
            f"      already exists as {self.base_module}:{self.base_line} "
            f"({self.base_qualname})\n"
            f"      signal: {self.signal.value}, body-shape similarity "
            f"{self.shape_ratio:.2f}, same parameter names: {self.same_params}"
        )


def _shape_ratio(left: Tuple[str, ...], right: Tuple[str, ...]) -> float:
    if not left and not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def compare(new: Sequence[Function], baseline: Sequence[Function]) -> List[Collision]:
    """Every name collision between the two sides, each classified by strength."""
    by_name: Dict[str, List[Function]] = {}
    for function in baseline:
        by_name.setdefault(function.name, []).append(function)

    collisions: List[Collision] = []
    for function in new:
        for other in by_name.get(function.name, ()):
            ratio = _shape_ratio(function.shape, other.shape)
            same_params = bool(function.params) and function.params == other.params
            signal = (
                Signal.STRONG
                if same_params or ratio >= SHAPE_THRESHOLD
                else Signal.NAME_ONLY
            )
            collisions.append(
                Collision(
                    name=function.name,
                    new_module=function.module,
                    new_qualname=function.qualname,
                    new_line=function.line,
                    base_module=other.module,
                    base_qualname=other.qualname,
                    base_line=other.line,
                    signal=signal,
                    shape_ratio=ratio,
                    same_params=same_params,
                )
            )
    return sorted(collisions, key=lambda hit: hit.key)


def compare_cross_surface(
    new: Sequence[Function], baseline: Sequence[JsFunction]
) -> List[Collision]:
    """Name-and-arity collisions between new Python functions and the JavaScript client."""
    by_name: Dict[str, List[JsFunction]] = {}
    for function in baseline:
        by_name.setdefault(function.snake_name, []).append(function)

    collisions: List[Collision] = []
    for function in new:
        for other in by_name.get(function.name, ()):
            if other.arity != function.arity:
                continue
            collisions.append(
                Collision(
                    name=function.name,
                    new_module=function.module,
                    new_qualname=function.qualname,
                    new_line=function.line,
                    base_module=other.module,
                    base_qualname=other.name,
                    base_line=other.line,
                    signal=Signal.CROSS_SURFACE,
                    shape_ratio=0.0,
                    same_params=False,
                )
            )
    return sorted(collisions, key=lambda hit: hit.key)


def all_python_collisions() -> List[Collision]:
    baseline: List[Function] = []
    for path in baseline_python_modules()[0]:
        baseline.extend(python_functions_of(path))
    new: List[Function] = []
    for path in new_modules():
        new.extend(python_functions_of(path))
    return compare(new, baseline)


def all_cross_surface_collisions() -> List[Collision]:
    new: List[Function] = []
    for path in new_modules():
        new.extend(python_functions_of(path))
    return compare_cross_surface(new, javascript_baseline())


# ══════════════════════════════════════════════════════════════════════════
#  THE REVIEWED REGISTER
# ══════════════════════════════════════════════════════════════════════════


class Reviewed(NamedTuple):
    """One collision that was read on both sides and found not to be a re-implementation.

    Keyed on the four names, never on a line number, so an edit above either function does not
    invalidate the review. ``signal`` is the strength the pair was reviewed at and is re-derived on
    every run: a pair reviewed as ``NAME_ONLY`` that later becomes a copy of its namesake escalates
    to ``STRONG`` and fails :func:`test_the_reviewed_signal_still_matches_the_source`.
    """

    name: str
    new_qualname: str
    new_module: str
    base_qualname: str
    base_module: str
    signal: Signal
    reason: str


_MP = "backend_app/backend/marketplace"
_PP = "backend_app/backend/paper"
_MDC = "backend_app/backend/market_data_contract.py"
_MDV = "backend_app/backend/market_data_validation.py"
_WSM = "backend_app/api_ws/ws_manager.py"
_BTS = "backend_app/backend/backtest_service.py"
_BILLING = "backend_app/routers/billing.py"
_JS = "algo22-terminal/src/api/modules"

#: Every collision the audit currently finds, reviewed one pair at a time.
REGISTER: Tuple[Reviewed, ...] = (
    # ── no strong hit remains ─────────────────────────────────────────────
    # There was one: ``paper_market_feed._metrics`` against ``market_data_contract._metrics``,
    # reviewed as duplication of an idiom rather than of a responsibility, with the note that the
    # follow-up was to promote a ``guarded_collector()`` into ``backend_app/backend/metrics.py``.
    # That follow-up is done. The paper package now calls
    # ``backend_app.backend.metrics.guarded_collector`` - which is where the one collector lives,
    # so reaching it is one function in one place - and holds no ``_metrics`` of its own, so the
    # collision no longer exists and its entry would be stale.
    # ── name-only, reviewed ───────────────────────────────────────────────
    Reviewed("validate", "validate", f"{_MP}/evidence_validator.py",
             "StructuralValidator.validate", _MDV, Signal.NAME_ONLY,
             "different subject entirely: this validates a submission's backtest evidence "
             "conditions against Requirement 3's admission bar; the baseline validates the "
             "structure of an OHLCV DataFrame. Shape similarity 0.02."),
    Reviewed("validate", "validate", f"{_MP}/evidence_validator.py",
             "CandleIntegrityValidator.validate", _MDV, Signal.NAME_ONLY,
             "the same pair against the candle-integrity validator of the same baseline module: "
             "evidence conditions versus candle integrity, shape similarity 0.04."),
    Reviewed("validate", "validate", f"{_MP}/evidence_validator.py",
             "MarketDataValidator.validate", _MDV, Signal.NAME_ONLY,
             "and against that module's aggregate validator. The paper feed reuses these market "
             "data validators rather than re-implementing them (Requirement 14.2, pinned by "
             "tests/test_paper_no_random.py); the evidence validator is a different rule set for "
             "a different artefact."),
    Reviewed("subscribe", "PaperChannelRegistry.subscribe", f"{_PP}/paper_channel.py",
             "ConnectionManager.subscribe", _WSM, Signal.NAME_ONLY,
             "a delegation, not a re-implementation: PaperChannelRegistry holds ws_manager's "
             "ConnectionManager and calls its subscribe inside its own, adding the paper-specific "
             "ownership check and handler registration. The registry of connections stays in one "
             "module, which is what Requirement 30.2 asks for. Shape similarity 0.07."),
    Reviewed("unsubscribe", "PaperChannelRegistry.unsubscribe", f"{_PP}/paper_channel.py",
             "ConnectionManager.unsubscribe", _WSM, Signal.NAME_ONLY,
             "the same delegation on the release path: it takes a paper Subscription rather than a "
             "(channel, key, ws, user_id) tuple and calls ws_manager's unsubscribe to do the "
             "removal. Shape similarity 0.13."),
    Reviewed("broadcast", "PaperChannelRegistry.broadcast", f"{_PP}/paper_channel.py",
             "ConnectionManager.broadcast", _WSM, Signal.NAME_ONLY,
             "different fan-out: ws_manager broadcasts one message to every connection it knows; "
             "this delivers one paper event to the subscriptions of one session after an ownership "
             "read, tracks per-connection queue depth and disconnects a slow consumer "
             "(Requirement 19.11). It writes through the same manager. Shape similarity 0.26."),
    Reviewed("subscribe", "subscribe", f"{_PP}/paper_channel.py",
             "ConnectionManager.subscribe", _WSM, Signal.NAME_ONLY,
             "the module-level convenience wrapper that forwards to the registry method above; it "
             "is one statement long. Shape similarity 0.01."),
    Reviewed("unsubscribe", "unsubscribe", f"{_PP}/paper_channel.py",
             "ConnectionManager.unsubscribe", _WSM, Signal.NAME_ONLY,
             "the module-level wrapper for the registry's unsubscribe, one statement long."),
    Reviewed("broadcast", "broadcast", f"{_PP}/paper_channel.py",
             "ConnectionManager.broadcast", _WSM, Signal.NAME_ONLY,
             "the module-level wrapper for the registry's broadcast, one statement long."),
    Reviewed("broadcast", "broadcast", f"{_PP}/paper_events.py",
             "ConnectionManager.broadcast", _WSM, Signal.NAME_ONLY,
             "paper_events.broadcast is the sequencing and envelope layer: it reaches "
             "paper_channel through a function-local import for the circularity reason its "
             "docstring gives, and delivery itself is still ws_manager's. Shape similarity 0.13."),
    Reviewed("frame", "PaperEventEnvelope.frame", f"{_PP}/paper_events.py",
             "ClosedBarIngest.frame", _MDC, Signal.NAME_ONLY,
             "homonym: this 'frame' is the WebSocket frame dict a subscriber receives, in "
             "ENVELOPE_FIELDS order; the baseline's is a pandas OHLCV DataFrame. Both zero-argument, "
             "which is why the empty parameter list is not treated as a signature match, and their "
             "bodies score 0.47."),
    Reviewed("_execute", "_execute", f"{_PP}/paper_repository.py",
             "_execute", _BTS, Signal.NAME_ONLY,
             "the baseline's is a nested one-line helper inside "
             "BacktestService.update_backtest_results; the paper repository's is its "
             "supabase-statement boundary, which classifies the response error, counts a round "
             "trip for P-57 and raises the paper error catalogue's exceptions. Shape similarity "
             "0.19."),
    Reviewed("cancel_subscription", "cancel_subscription", "backend_app/routers/library.py",
             "cancel_subscription", _BILLING, Signal.NAME_ONLY,
             "the judgement call this spec exists to get right, and it is deliberate: billing's "
             "endpoint cancels the user's PLATFORM PLAN subscription through the payment provider "
             "(POST /api/billing/cancel); library's cancels one LIBRARY subscription to one "
             "published strategy by its sub_id (Requirement 11.x), moving library_subscriptions to "
             "CANCELLED and stopping that strategy's deployments and Paper_Sessions. Two "
             "subscription concepts, two tables, two lifecycles - Requirement 1.1 makes "
             "library_subscriptions the authoritative table for the second and it is not the plan "
             "table. Neither calls the other and neither could. Shape similarity 0.20."),
    # ── cross-surface (Python vs the browser API client) ──────────────────
    Reviewed("price_range", "price_range", f"{_MP}/pricing_evaluator.py",
             "priceRange", f"{_JS}/library.js", Signal.CROSS_SURFACE,
             "opposite sides of the wire: library.js's priceRange builds the query string for a "
             "price-filtered Listing request, and this is the server-side admissible-price-range "
             "evaluation the endpoint applies (Requirement 7.1). A browser client calling an "
             "endpoint is the reuse Requirement 30.2 wants, not a duplicate of it."),
    Reviewed("reject", "ReferenceLedger.reject", f"{_PP}/paper_replay.py",
             "reject", f"{_JS}/library.js", Signal.CROSS_SURFACE,
             "library.js's reject posts an admin submission rejection; ReferenceLedger.reject is "
             "the in-process reference model's order rejection, used to check the simulator "
             "against an independent implementation. No wire call, no submission, no shared "
             "concept beyond the English word."),
    Reviewed("stop", "ReferenceLedger.stop", f"{_PP}/paper_replay.py",
             "stop", f"{_JS}/paper.js", Signal.CROSS_SURFACE,
             "paper.js's stop posts a Paper_Session stop request; the reference ledger's stop is "
             "the model's own stop-loss/stop-order arm inside the replay check. Same word, "
             "different layer."),
    Reviewed("stop", "ReferenceLedger.stop", f"{_PP}/paper_replay.py",
             "stop", f"{_JS}/strategies.js", Signal.CROSS_SURFACE,
             "and strategies.js's stop, which stops a deployment. The reference ledger is a test "
             "oracle that makes no HTTP call at all."),
)

REGISTER_BY_KEY: Dict[Tuple[str, str, str, str], Reviewed] = {
    (entry.new_module, entry.new_qualname, entry.base_module, entry.base_qualname): entry
    for entry in REGISTER
}


def _reviewed(collision: Collision) -> Optional[Reviewed]:
    return REGISTER_BY_KEY.get(collision.key)


# ══════════════════════════════════════════════════════════════════════════
#  THE ASSERTIONS
# ══════════════════════════════════════════════════════════════════════════


def test_no_new_module_reimplements_a_baseline_function() -> None:
    """No unregistered strong collision with a baseline Python module.

    A strong collision is the same function name with the same non-empty parameter names, or with a
    body-shape similarity of 0.80 or more. That is the shape of a re-implementation: the same job,
    done again in a new module, when Requirement 30.2 says the responsibility belongs in one place.

    To clear a failure: call the existing function instead, or - if the two really are different
    jobs that happen to share a name - add a :class:`Reviewed` entry that says what each one does.

    _Requirements: 30.2_
    """
    unreviewed = [
        collision
        for collision in all_python_collisions()
        if collision.signal is Signal.STRONG and _reviewed(collision) is None
    ]
    assert not unreviewed, (
        f"{len(unreviewed)} function(s) in a new module re-implement something that already "
        "exists (Requirement 30.2):\n" + "\n".join(hit.describe() for hit in unreviewed)
    )


def test_every_name_collision_with_a_baseline_module_is_reviewed() -> None:
    """No unregistered collision of any strength with a baseline Python module.

    The weaker half of the rule, kept because a duplicate written with renamed parameters and a
    reorganised body is exactly what the strong test would miss, and because a shared name between
    two services is worth one reviewer's sentence either way. The register makes that sentence a
    condition of merging rather than a comment someone might write.

    _Requirements: 30.2_
    """
    unreviewed = [
        collision for collision in all_python_collisions() if _reviewed(collision) is None
    ]
    assert not unreviewed, (
        f"{len(unreviewed)} name collision(s) with a baseline module are not reviewed "
        "(Requirement 30.2). Each needs a Reviewed entry saying what the two functions do, or the "
        "new one needs to call the old one:\n" + "\n".join(hit.describe() for hit in unreviewed)
    )


def test_no_new_module_reimplements_an_api_client_function() -> None:
    """No unregistered name-and-arity collision with the browser API client.

    ``cancelSubscription`` in ``billing.js`` and ``cancel_subscription`` in Python are the same
    name once camelCase is normalised, so this is the check that catches an api-client function
    transliterated into the backend, or a second client written next to the existing one. Arity
    must match too - without that, every endpoint whose client method is named after it would be
    reported, and naming the client method after the endpoint is the design rather than a fault.

    _Requirements: 30.2_
    """
    if not JS_BASELINE_DIR.is_dir():
        pytest.skip(
            f"{_relative(JS_BASELINE_DIR)} does not exist, so there is no api client to compare "
            "against; this is not a pass"
        )
    unreviewed = [
        collision
        for collision in all_cross_surface_collisions()
        if _reviewed(collision) is None
    ]
    assert not unreviewed, (
        f"{len(unreviewed)} new Python function(s) share a name and an arity with an api-client "
        "function (Requirement 30.2):\n" + "\n".join(hit.describe() for hit in unreviewed)
    )


def test_the_reviewed_signal_still_matches_the_source() -> None:
    """A reviewed pair cannot decay into a real duplicate silently.

    The strength is re-derived from the two functions on every run and compared with the strength
    the pair was reviewed at. A ``NAME_ONLY`` pair whose bodies grow together crosses
    :data:`SHAPE_THRESHOLD`, becomes ``STRONG``, and fails here naming both strengths - so the
    reviewer's sentence has to be written again against the code as it now is.

    _Requirements: 30.2_
    """
    collisions = list(all_python_collisions())
    if JS_BASELINE_DIR.is_dir():
        collisions.extend(all_cross_surface_collisions())
    disagreements = [
        (collision, entry)
        for collision in collisions
        for entry in (_reviewed(collision),)
        if entry is not None and entry.signal is not collision.signal
    ]
    assert not disagreements, (
        f"{len(disagreements)} reviewed collision(s) are no longer what they were reviewed as "
        "(Requirement 30.2):\n"
        + "\n".join(
            f"  {collision.name!r} {collision.new_module}::{collision.new_qualname} vs "
            f"{collision.base_module}::{collision.base_qualname} was reviewed as "
            f"{entry.signal.value!r} and now reads {collision.signal.value!r} "
            f"(shape similarity {collision.shape_ratio:.2f}, same parameter names "
            f"{collision.same_params})"
            for collision, entry in disagreements
        )
    )


def test_the_register_has_no_stale_entry() -> None:
    """Every reviewed pair still exists, so the register cannot rot into a blanket pass.

    A function that was renamed or deleted leaves its justification behind, and the next function
    to take that name would inherit a reason written about different code.

    _Requirements: 30.2_
    """
    live = {collision.key for collision in all_python_collisions()}
    if JS_BASELINE_DIR.is_dir():
        live |= {collision.key for collision in all_cross_surface_collisions()}
    stale = [
        entry
        for entry in REGISTER
        if (entry.new_module, entry.new_qualname, entry.base_module, entry.base_qualname)
        not in live
    ]
    assert not stale, (
        f"{len(stale)} register entry(ies) no longer describe a real collision - delete them, or "
        "re-review the pair that replaced them:\n"
        + "\n".join(
            f"  {entry.name!r} {entry.new_module}::{entry.new_qualname} vs "
            f"{entry.base_module}::{entry.base_qualname}"
            for entry in stale
        )
    )


def test_every_register_entry_carries_a_reason() -> None:
    """An entry without an argument is an exclusion, which this file does not have.

    _Requirements: 30.2_
    """
    non_reasons = {"different", "unrelated", "not a duplicate", "intentional", "n/a", "homonym"}
    thin = [
        entry
        for entry in REGISTER
        if len(entry.reason.strip()) < 40
        or entry.reason.strip().lower().rstrip(".") in non_reasons
    ]
    assert not thin, (
        "register entry(ies) without a stated reason:\n"
        + "\n".join(f"  {entry.name!r} in {entry.new_module}: {entry.reason!r}" for entry in thin)
    )


def test_no_two_register_entries_share_a_key() -> None:
    """Two entries under one key would review one pair twice and another not at all.

    _Requirements: 30.2_
    """
    keys = [
        (entry.new_module, entry.new_qualname, entry.base_module, entry.base_qualname)
        for entry in REGISTER
    ]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, f"duplicated register key(s): {duplicates}"


def test_the_baseline_surface_is_non_empty() -> None:
    """Both baseline surfaces were found and yielded functions.

    The first way this file could pass while checking nothing: a baseline that resolved to no file,
    or to files with no functions in them. The JavaScript half is reported as a skip when the
    directory is absent - other work is landing in ``algo22-terminal`` and a missing directory is a
    fact to state, not a pass to take.

    _Requirements: 30.2_
    """
    present, missing = baseline_python_modules()
    assert present, f"no baseline Python module resolved; missing: {missing}"
    assert not missing, (
        "baseline module(s) named by task 33.6 are absent, so this audit is comparing against a "
        f"smaller surface than the task requires: {missing}"
    )

    functions = [function for path in present for function in python_functions_of(path)]
    assert len(functions) > 50, (
        f"the baseline modules yielded only {len(functions)} function(s); billing.py, "
        "ws_manager.py, backtest_service.py and the market_data family hold hundreds, so this "
        "means the extractor is broken and every comparison above is vacuous"
    )

    if not JS_BASELINE_DIR.is_dir():
        pytest.skip(f"{_relative(JS_BASELINE_DIR)} does not exist; the JavaScript half is unaudited")
    js_functions = javascript_baseline()
    assert len(js_functions) > 20, (
        f"the api client yielded only {len(js_functions)} function(s) across "
        f"{len(list(JS_BASELINE_DIR.glob('*.js')))} module(s); the extraction is broken"
    )


def test_the_new_module_set_covers_the_packages_and_the_derived_routers() -> None:
    """The audited set was derived, and the derivation still finds what it must.

    The second way this file could pass while checking nothing: an empty new-module set. Both
    created packages must contribute every module they hold, and the ``tasks.md`` derivation must
    still resolve the two routers this spec's endpoints live in - if its phrasing changes, this
    fails rather than quietly auditing less.

    _Requirements: 30.2_
    """
    audited = {_relative(path) for path in new_modules()}

    for package in CREATED_PACKAGES:
        expected = {_relative(path) for path in package.rglob("*.py")}
        missing = sorted(expected - audited)
        assert not missing, f"the audited set is missing {len(missing)} module(s): {missing}"

    derived = {_relative(path) for path in modules_tasks_md_says_were_created()}
    assert derived, (
        "the tasks.md derivation resolved no module at all - the 'Create `x.py`' phrasing this "
        "test reads has changed, and the audited set has silently shrunk to the two packages"
    )
    for required in (
        "backend_app/routers/library.py",
        "backend_app/routers/paper_trading.py",
    ):
        assert required in derived and required in audited, (
            f"{required} is where this spec's endpoints live and it is no longer being audited; "
            f"the derivation found {sorted(derived)}"
        )


def test_dunder_methods_are_the_only_thing_excluded_by_name() -> None:
    """The one exclusion, stated as a test so it cannot widen unnoticed.

    ``__init__`` and ``__post_init__`` collide across every dataclass in the repository - 20 pairs
    of them - and none is a re-implementation of anything: they are protocol slots. They are the
    only names this file filters, and this asserts both halves of that: dunders are dropped, and a
    single-underscore private name is not.

    _Requirements: 30.2_
    """
    source = (
        "class Thing:\n"
        "    def __init__(self, a):\n"
        "        self.a = a\n"
        "    def __post_init__(self):\n"
        "        pass\n"
        "    def _private(self):\n"
        "        pass\n"
        "    def public(self):\n"
        "        pass\n"
    )
    names = {function.name for function in python_functions(source, "<synthetic>")}
    assert names == {"_private", "public"}


# ══════════════════════════════════════════════════════════════════════════
#  PROOF THAT THE DETECTOR BITES
# ══════════════════════════════════════════════════════════════════════════


class TestTheDetectorBites:
    """The rule, exercised over synthetic sources by the functions the real audit uses."""

    BASELINE_SOURCE = (
        "class BacktestService:\n"
        "    def create_backtest(self, strategy_id, user):\n"
        "        rows = self.db.table('backtests').insert({'s': strategy_id}).execute()\n"
        "        if not rows.data:\n"
        "            raise RuntimeError('no row')\n"
        "        return rows.data[0]\n"
    )

    def test_a_verbatim_reimplementation_is_strong(self) -> None:
        new = python_functions(
            "def create_backtest(strategy_id, user):\n"
            "    rows = db.table('backtests').insert({'s': strategy_id}).execute()\n"
            "    if not rows.data:\n"
            "        raise RuntimeError('no row')\n"
            "    return rows.data[0]\n",
            "new.py",
        )
        baseline = python_functions(self.BASELINE_SOURCE, "baseline.py")
        found = compare(new, baseline)
        assert [hit.signal for hit in found] == [Signal.STRONG]
        assert found[0].same_params is True

    def test_a_reimplementation_with_renamed_parameters_is_still_strong(self) -> None:
        """The body-shape half of the rule: renaming the arguments does not hide a copy."""
        new = python_functions(
            "def create_backtest(sid, caller):\n"
            "    rows = db.table('backtests').insert({'s': sid}).execute()\n"
            "    if not rows.data:\n"
            "        raise RuntimeError('nope')\n"
            "    return rows.data[0]\n",
            "new.py",
        )
        baseline = python_functions(self.BASELINE_SOURCE, "baseline.py")
        found = compare(new, baseline)
        assert [hit.signal for hit in found] == [Signal.STRONG]
        assert found[0].same_params is False
        assert found[0].shape_ratio >= SHAPE_THRESHOLD

    def test_an_unrelated_function_with_the_same_name_is_name_only(self) -> None:
        new = python_functions(
            "def create_backtest(request, body):\n"
            "    return await forward(request, body)\n",
            "new.py",
        )
        baseline = python_functions(self.BASELINE_SOURCE, "baseline.py")
        found = compare(new, baseline)
        assert [hit.signal for hit in found] == [Signal.NAME_ONLY]

    def test_a_different_name_is_no_collision_at_all(self) -> None:
        new = python_functions("def start_backtest(strategy_id, user):\n    return 1\n", "new.py")
        baseline = python_functions(self.BASELINE_SOURCE, "baseline.py")
        assert compare(new, baseline) == []

    def test_two_empty_parameter_lists_are_not_a_signature_match(self) -> None:
        """Otherwise every zero-argument function in the repository matches every other one."""
        new = python_functions("def frame():\n    return {'a': 1}\n", "new.py")
        baseline = python_functions(
            "class Ingest:\n    def frame(self):\n        import pandas\n"
            "        return pandas.DataFrame(self.rows).sort_index()\n",
            "baseline.py",
        )
        found = compare(new, baseline)
        assert [hit.signal for hit in found] == [Signal.NAME_ONLY]
        assert found[0].same_params is False

    def test_a_method_is_compared_without_self(self) -> None:
        new = python_functions(
            "class Registry:\n    def subscribe(self, channel, key):\n        return 1\n",
            "new.py",
        )
        baseline = python_functions(
            "class Manager:\n    def subscribe(self, channel, key):\n        return 2\n",
            "baseline.py",
        )
        found = compare(new, baseline)
        assert [hit.signal for hit in found] == [Signal.STRONG]
        assert found[0].new_qualname == "Registry.subscribe"
        assert found[0].base_qualname == "Manager.subscribe"

    def test_a_nested_function_is_compared_too(self) -> None:
        new = python_functions(
            "def outer():\n    def create_backtest(strategy_id, user):\n        return 1\n"
            "    return create_backtest\n",
            "new.py",
        )
        baseline = python_functions(self.BASELINE_SOURCE, "baseline.py")
        found = compare(new, baseline)
        assert [hit.new_qualname for hit in found] == ["outer.create_backtest"]

    def test_the_javascript_extractor_reads_all_four_shapes(self) -> None:
        source = (
            "export async function getPlans(currency) { return 1; }\n"
            "export const setCurrency = (currency) => post('/x', { currency });\n"
            "export const api = {\n"
            "  cancelSubscription: () => post('/api/billing/cancel', {}),\n"
            "  async updateOrder(orderId, body) { return 1; }\n"
            "};\n"
        )
        found = {function.name: function for function in javascript_functions(source, "x.js")}
        assert set(found) == {"getPlans", "setCurrency", "cancelSubscription", "updateOrder"}
        assert found["cancelSubscription"].snake_name == "cancel_subscription"
        assert found["cancelSubscription"].arity == 0
        assert found["updateOrder"].arity == 2

    def test_a_transliterated_api_client_function_is_caught(self) -> None:
        new = python_functions("def cancel_subscription(sub_id):\n    return post(sub_id)\n", "new.py")
        js = javascript_functions(
            "export const api = {\n  cancelSubscription: (subId) => post('/x', subId),\n};\n",
            "billing.js",
        )
        found = compare_cross_surface(new, js)
        assert [hit.signal for hit in found] == [Signal.CROSS_SURFACE]
        assert found[0].base_qualname == "cancelSubscription"

    def test_a_client_method_named_after_an_endpoint_is_not_reported(self) -> None:
        """The arity requirement, doing its job: same name, different signature, no report."""
        new = python_functions(
            "def cancel_subscription(sub_id, user, request):\n    return 1\n", "new.py"
        )
        js = javascript_functions(
            "export const api = {\n  cancelSubscription: () => post('/x', {}),\n};\n",
            "billing.js",
        )
        assert compare_cross_surface(new, js) == []

    def test_a_javascript_keyword_is_not_read_as_a_function(self) -> None:
        found = javascript_functions("if (x) {\n  doThing();\n}\n", "x.js")
        assert [function.name for function in found] == []

    def test_the_report_names_both_sides(self) -> None:
        new = python_functions("def create_backtest(strategy_id, user):\n    return 1\n", "new.py")
        baseline = python_functions(self.BASELINE_SOURCE, "baseline.py")
        described = compare(new, baseline)[0].describe()
        assert "new.py:1" in described
        assert "BacktestService.create_backtest" in described
        assert "body-shape similarity" in described
