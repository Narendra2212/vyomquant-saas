"""The guard that keeps the dormant Marketplace tables dormant.

Feature: marketplace-subscriptions-paper-trading, task 16.6.
Design reference: ``design.md`` / ``tasks.md`` -> "``marketplace_listings`` and
``strategy_subscriptions`` stay dormant. No task reads or writes either (Requirement 1.2)".
Requirements 1.1, 1.2, 1.8.

What this asserts, and why mechanically
---------------------------------------
Two tables were provisioned by an earlier effort and then abandoned: ``marketplace_listings``
and ``strategy_subscriptions``. This spec's authoritative tables are ``library_strategies``
(the Listing table, Requirement 1.1) and ``library_subscriptions`` (the subscription table).
A read or write against either dormant name is a Requirement 1.1/1.2/1.8 violation: it splits
the source of truth, and it does so silently because the dormant tables exist and a query
against them succeeds at runtime.

The only place such a reference can live is the first string argument of a supabase-py
``.table(...)``, ``.from_(...)`` or ``.rpc(...)`` call. So this test walks every module
reachable from the three Marketplace/paper roots, collects every such literal statically
(``ast`` only, no import, no database, no event loop), and fails if any collected literal
*contains* either forbidden name.

The reachable set
-----------------
"Reachable from" is the import graph rooted at ``backend_app/routers/library.py`` and at
every module under ``backend_app/backend/marketplace/`` and ``backend_app/backend/paper/``.
The walk is a static BFS: each file is parsed, its ``import backend_app...`` and
``from backend_app... import ...`` edges are resolved to files *on disk* (never imported), and
their targets are enqueued. The walk stays inside ``backend_app`` - a ``from fastapi import``
or ``from supabase import`` edge is not followed, because a dormant reference this spec could
introduce lives in first-party handler code, and third-party packages are not this spec's to
audit. Relative imports (``from .errors import ...``, ``from ..core import ...``) are resolved
against the importing module's package.

Excluded by path
----------------
``*.sql`` files are excluded (they are not Python and ``ast`` would not parse them; the walk
only ever enqueues ``.py`` files, so this is belt-and-braces). ``tests/`` is outside
``backend_app`` and is never reached; ``tests/test_schema_as_code_completeness.py`` is named
excluded explicitly by the task and is filtered defensively even though the walk cannot reach
it.

Why the test is non-vacuous
---------------------------
A walk that silently collected nothing - a broken BFS, a renamed root, a parse that quietly
failed - would pass this test trivially while auditing zero code. So the test first asserts
the walk reached the three roots and collected a *non-empty* set of ``.table(...)`` literals
(the Marketplace and paper handlers are full of them, ``library_strategies`` and
``library_subscriptions`` chief among them), and only then asserts the two forbidden names are
absent from that non-empty set. Both halves must hold.
"""

from __future__ import annotations

import ast
import re
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ══════════════════════════════════════════════════════════════════════════
# The roots, the forbidden names, and what is excluded
# ══════════════════════════════════════════════════════════════════════════

REPO_ROOT: Path = Path(__file__).resolve().parents[1]

#: The top-level first-party package the walk stays inside.
ROOT_PACKAGE: str = "backend_app"

#: The importable package root on disk.
PACKAGE_DIR: Path = REPO_ROOT / ROOT_PACKAGE

#: The three roots task 16.6 names. The router module plus every ``.py`` under the two
#: backend packages seed the BFS; their transitive first-party imports are then walked.
ROOT_FILES: Tuple[Path, ...] = (
    PACKAGE_DIR / "routers" / "library.py",
    PACKAGE_DIR / "backend" / "marketplace",
    PACKAGE_DIR / "backend" / "paper",
)

#: The dormant/wrong table names this spec forbids (Requirement 1.2). The real tables are
#: ``library_strategies`` (Requirement 1.1) and ``library_subscriptions``.
FORBIDDEN_TABLE_SUBSTRINGS: Tuple[str, ...] = (
    "marketplace_listings",
    "strategy_subscriptions",
)

#: The supabase-py entry points whose first string argument names a table or routine.
TABLE_SELECTING_CALLS: frozenset = frozenset({"table", "from_", "rpc"})

#: Named excluded by the task. It intentionally lists table names as data, so walking it
#: would produce false positives - but it lives under ``tests/`` and the BFS, staying inside
#: ``backend_app``, cannot reach it anyway. Filtered here as belt-and-braces.
EXCLUDED_RELATIVE_PATHS: frozenset = frozenset(
    {Path("tests") / "test_schema_as_code_completeness.py"}
)


# ══════════════════════════════════════════════════════════════════════════
# Path <-> module-name mapping, all on disk, never importing
# ══════════════════════════════════════════════════════════════════════════


def _is_excluded(path: Path) -> bool:
    """True when ``path`` is excluded by the task's path rules."""
    if path.suffix == ".sql":
        return True
    try:
        relative = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    return relative in EXCLUDED_RELATIVE_PATHS


def _module_name_for_file(path: Path) -> Optional[str]:
    """The dotted ``backend_app...`` module name of a ``.py`` file, or ``None``.

    ``backend_app/backend/marketplace/errors.py`` -> ``backend_app.backend.marketplace.errors``
    ``backend_app/backend/marketplace/__init__.py`` -> ``backend_app.backend.marketplace``
    A file outside ``backend_app`` maps to ``None`` and is never walked.
    """
    path = path.resolve()
    try:
        relative = path.relative_to(PACKAGE_DIR)
    except ValueError:
        return None
    parts = list(relative.parts)
    if not parts or parts[-1].endswith(".sql"):
        return None
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts[-1].endswith(".py"):
        parts[-1] = parts[-1][: -len(".py")]
    else:
        return None
    return ".".join([ROOT_PACKAGE, *parts])


def _file_for_module(module: str) -> Optional[Path]:
    """The ``.py`` file backing a dotted ``backend_app...`` module, or ``None``.

    A package resolves to its ``__init__.py``; a module to ``<name>.py``. A dotted name that
    does not resolve to a first-party file on disk (a submodule attribute, a name that is
    really an imported symbol) yields ``None`` and is dropped from the walk.
    """
    if module != ROOT_PACKAGE and not module.startswith(ROOT_PACKAGE + "."):
        return None
    parts = module.split(".")[1:]  # drop the leading ``backend_app``
    base = PACKAGE_DIR.joinpath(*parts) if parts else PACKAGE_DIR
    module_file = base.with_suffix(".py")
    if module_file.is_file():
        return module_file.resolve()
    package_init = base / "__init__.py"
    if package_init.is_file():
        return package_init.resolve()
    return None


# ══════════════════════════════════════════════════════════════════════════
# Import-edge extraction (static, first-party only)
# ══════════════════════════════════════════════════════════════════════════


def _package_of(module: str) -> str:
    """The package a module lives in - the dotted prefix used to resolve relative imports."""
    return module.rsplit(".", 1)[0] if "." in module else module


def _resolve_relative(package: str, level: int, suffix: Optional[str]) -> Optional[str]:
    """Resolve ``from . / .. import`` to an absolute dotted name within ``backend_app``.

    ``level`` is the number of leading dots; ``suffix`` the dotted tail after them (``None``
    for a bare ``from . import x``). Walking above ``backend_app`` yields ``None``.
    """
    base_parts = package.split(".")
    # level 1 == current package; each extra dot climbs one package up.
    climb = level - 1
    if climb > len(base_parts) - 1:
        return None
    kept = base_parts[: len(base_parts) - climb] if climb else base_parts
    if not kept or kept[0] != ROOT_PACKAGE:
        return None
    tail = [part for part in (suffix or "").split(".") if part]
    return ".".join([*kept, *tail]) if (kept or tail) else None


def _imported_modules(path: Path, module: str) -> Set[str]:
    """Every first-party dotted module ``path`` imports, resolved absolutely.

    Handles ``import backend_app.x.y``, ``import backend_app.x as z``,
    ``from backend_app.x import a, b`` (``backend_app.x`` plus the ``backend_app.x.a`` /
    ``backend_app.x.b`` candidates, since either could be a submodule or a symbol - the
    non-file candidates are dropped later) and relative ``from . / .. import`` forms.
    Non-``backend_app`` imports are ignored.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    package = _package_of(module)
    found: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == ROOT_PACKAGE or name.startswith(ROOT_PACKAGE + "."):
                    found.add(name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                base = _resolve_relative(package, node.level, node.module)
            elif node.module and (
                node.module == ROOT_PACKAGE
                or node.module.startswith(ROOT_PACKAGE + ".")
            ):
                base = node.module
            else:
                base = None
            if base is None:
                continue
            found.add(base)
            for alias in node.names:
                if alias.name != "*":
                    found.add(f"{base}.{alias.name}")

    return found


# ══════════════════════════════════════════════════════════════════════════
# The reachable-set BFS
# ══════════════════════════════════════════════════════════════════════════


def _seed_files(roots: Tuple[Path, ...] = ROOT_FILES) -> List[Path]:
    """Every ``.py`` file the given roots expand to, deduplicated, existence-checked.

    ``roots`` defaults to the three roots task 16.6 names. It is a parameter so the
    bite-proof test can point the identical machinery at a synthetic package on disk instead
    of at ``backend_app``.
    """
    seeds: List[Path] = []
    seen: Set[Path] = set()
    for root in roots:
        if root.is_dir():
            candidates = sorted(root.rglob("*.py"))
        elif root.is_file():
            candidates = [root]
        else:
            candidates = []
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen or _is_excluded(resolved):
                continue
            seen.add(resolved)
            seeds.append(resolved)
    return seeds


def reachable_files(roots: Tuple[Path, ...] = ROOT_FILES) -> List[Path]:
    """Static BFS over first-party import edges from the given roots.

    Returns every ``backend_app`` ``.py`` file reachable, parsed exactly once. No module is
    imported: every edge is resolved to a file on disk, so FastAPI, supabase and the database
    are never loaded.

    ``roots`` defaults to the three roots task 16.6 names. When a root lies outside
    ``backend_app`` - which only happens in the bite-proof test's synthetic package - its
    files still seed the walk and are still scanned, but their import edges are not followed,
    because :func:`_module_name_for_file` cannot give them a first-party dotted name.
    """
    queue: deque[Path] = deque()
    visited: Set[Path] = set()

    for seed in _seed_files(roots):
        if seed not in visited:
            visited.add(seed)
            queue.append(seed)

    while queue:
        current = queue.popleft()
        module = _module_name_for_file(current)
        if module is None:
            continue
        for imported in _imported_modules(current, module):
            target = _file_for_module(imported)
            if target is None or target in visited or _is_excluded(target):
                continue
            visited.add(target)
            queue.append(target)

    return sorted(visited)


# ══════════════════════════════════════════════════════════════════════════
# Literal collection
# ══════════════════════════════════════════════════════════════════════════


def _first_string_argument(call: ast.Call) -> Optional[str]:
    """The call's first positional argument if it is a string literal, else ``None``."""
    if call.args and isinstance(call.args[0], ast.Constant):
        value = call.args[0].value
        if isinstance(value, str):
            return value
    return None


def collect_table_literals_from_source(
    source: str, path: Path
) -> List[Tuple[str, Path, int]]:
    """Every ``(literal, path, line)`` for a ``.table``/``.from_``/``.rpc`` string argument.

    A match is an ``ast.Call`` whose ``func`` is an ``ast.Attribute`` with attr in
    :data:`TABLE_SELECTING_CALLS` and whose first positional argument is a string constant.
    A non-literal argument (``table(SOME_CONSTANT)``) carries no literal to inspect and is not
    collected - this test can only reason about literal table names.

    ``path`` is used for reporting only; nothing is read from disk here. Keeping the walker
    source-addressable is what lets the bite-proof test drive it with a synthetic module and
    know it is exercising the same code the real audit runs.
    """
    literals: List[Tuple[str, Path, int]] = []
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in TABLE_SELECTING_CALLS:
            continue
        literal = _first_string_argument(node)
        if literal is not None:
            literals.append((literal, path, node.lineno))
    return literals


def collect_table_literals(files: List[Path]) -> List[Tuple[str, Path, int]]:
    """:func:`collect_table_literals_from_source` applied to every file, in order."""
    literals: List[Tuple[str, Path, int]] = []
    for path in files:
        literals.extend(
            collect_table_literals_from_source(path.read_text(encoding="utf-8"), path)
        )
    return literals


# ══════════════════════════════════════════════════════════════════════════
# The forbidden-name rule
# ══════════════════════════════════════════════════════════════════════════


def dormant_offenders(
    literals: List[Tuple[str, Path, int]]
) -> List[Tuple[str, Path, int, str]]:
    """Every collected literal that *contains* a forbidden dormant table name.

    Returns ``(literal, path, line, forbidden_name)`` tuples, sorted by file then line. A
    containment test rather than equality, so ``"public.marketplace_listings"`` and
    ``"marketplace_listings_view"`` are caught too. This is the single rule both the real
    audit and the bite-proof test evaluate.
    """
    offenders: List[Tuple[str, Path, int, str]] = []
    for literal, path, line in sorted(literals, key=lambda item: (str(item[1]), item[2])):
        for forbidden in FORBIDDEN_TABLE_SUBSTRINGS:
            if forbidden in literal:
                offenders.append((literal, path, line, forbidden))
    return offenders


def _describe(offenders: List[Tuple[str, Path, int, str]]) -> str:
    """Render offenders as one indented ``file:line`` line each."""
    lines: List[str] = []
    for literal, path, line, forbidden in offenders:
        try:
            location = path.resolve().relative_to(REPO_ROOT)
        except ValueError:
            location = path
        lines.append(f"  {location}:{line} references {forbidden!r} via literal {literal!r}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# The migration set (Requirement 1.2 retention)
# ══════════════════════════════════════════════════════════════════════════

#: The applied migration set. ``*.sql`` is excluded from the *reference* audit by path, but
#: it is exactly where Requirement 1.2 requires the dormant tables to remain.
MIGRATIONS_DIR: Path = PACKAGE_DIR / "migrations"

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:(?:public|auth)\.)?"
    r"[\"`]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)[\"`]?",
    re.IGNORECASE,
)

_DROP_TABLE_RE = re.compile(
    r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:(?:public|auth)\.)?"
    r"[\"`]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)[\"`]?",
    re.IGNORECASE,
)


def migration_created_tables() -> Dict[str, str]:
    """Every table name with a ``CREATE TABLE`` in ``backend_app/migrations/*.sql``.

    Maps table name -> the migration file name that creates it.
    """
    created: Dict[str, str] = {}
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        text = sql_file.read_text(encoding="utf-8")
        for match in _CREATE_TABLE_RE.finditer(text):
            created.setdefault(match.group("name"), sql_file.name)
    return created


def migration_dropped_tables() -> Dict[str, str]:
    """Every table name with a ``DROP TABLE`` in ``backend_app/migrations/*.sql``."""
    dropped: Dict[str, str] = {}
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        text = sql_file.read_text(encoding="utf-8")
        for match in _DROP_TABLE_RE.finditer(text):
            dropped.setdefault(match.group("name"), sql_file.name)
    return dropped


# ══════════════════════════════════════════════════════════════════════════
# The assertions
# ══════════════════════════════════════════════════════════════════════════


def test_reachable_walk_covers_the_three_roots() -> None:
    """The BFS reaches the router and every module under both backend packages.

    A walk that dropped a root - a rename, a moved file, a resolver bug - would audit less
    code than the task requires while still passing the forbidden-name check. This holds the
    walk honest: the router file and every ``.py`` under ``backend/marketplace`` and
    ``backend/paper`` must be in the reachable set.

    _Requirements: 1.1, 1.2, 1.8_
    """
    reached = {path.resolve() for path in reachable_files()}
    expected = {seed.resolve() for seed in _seed_files()}

    missing = sorted(str(path.relative_to(REPO_ROOT)) for path in expected - reached)
    assert not missing, (
        "the reachable-set walk did not include every root module it must audit; "
        f"{len(missing)} root file(s) missing: {missing}"
    )


def test_no_dormant_table_reference() -> None:
    """No reachable module reads or writes a dormant table.

    Collect every literal handed to ``.table(...)``, ``.from_(...)`` or ``.rpc(...)`` across
    the reachable set, assert the collection is non-empty (so a broken walk cannot pass
    trivially), then assert no collected literal contains ``marketplace_listings`` or
    ``strategy_subscriptions``. A hit is a Requirement 1.1/1.2/1.8 violation and is reported
    with the exact file, line and literal.

    _Requirements: 1.1, 1.2, 1.8_
    """
    files = reachable_files()
    literals = collect_table_literals(files)

    table_literals = {literal for literal, _path, _line in literals}
    assert table_literals, (
        "the reachable-set walk collected zero .table(...)/.from_(...)/.rpc(...) literals; "
        "the Marketplace and paper handlers are full of them, so an empty collection means "
        "the walk is broken and this test would otherwise pass vacuously. Reachable files "
        f"examined: {len(files)}."
    )

    offenders = dormant_offenders(literals)
    assert not offenders, (
        f"{len(offenders)} reference(s) to a dormant table found - the real tables are "
        "'library_strategies' and 'library_subscriptions' (Requirements 1.1, 1.2, 1.8):\n"
        + _describe(offenders)
    )
