"""The property-coverage scoreboard: one property, one property-based test.

Requirement 29.3 demands a property-based test for each of the fifty-eight correctness
properties in `requirements.md` section "Correctness properties for property-based testing".
A prose list of properties is not a check. This module turns it into one: it parses every
test module in the property-test search roots and asserts that each of P-1...P-58 is claimed
by exactly one `test_p{n}_...` function -- none missing, none duplicated, none unknown.

Why it fails today, and why that does not red the shared CI gate
---------------------------------------------------------------
This guard is written at spec task 2.3, before any of the fifty-eight property tests exist,
so it fails immediately and reports all fifty-eight as missing. That failure is the point:
it is the running scoreboard for tasks 4 through 34, and spec task 35.1 closes it.

The assertion below is therefore NOT weakened, skipped or marked expected-to-fail --
Requirements 29.8 and 29.9 forbid making a check non-blocking, and a scoreboard that can go
green while properties are missing is worth nothing. What is gated instead is *collection*:
`tests/property/conftest.py` collects this one module only when
`AERORA_PROPERTY_SCOREBOARD=1` is set, so the deliberate run is

    AERORA_PROPERTY_SCOREBOARD=1 pytest tests/property/test_property_coverage.py

while the shared `unit-tests` job in `.github/workflows/01-pr-check.yml`
(`pytest tests/ -k "not chaos and not load"`) keeps exactly the scope it has today for the
whole duration of the plan. No existing check is removed, loosened or excluded; the `-k`
selector is untouched; nothing here is `skip` or `xfail`. This mirrors the precedent already
in `pytest.ini`, where the long-running market-data-latency harness is gated on
`AERORA_MARKET_DATA_LATENCY_EXPERIMENT` rather than being allowed to run by accident.

**Spec task 35.1 must delete `tests/property/conftest.py` (or empty its `collect_ignore`)**,
which puts this module in the default `pytest tests/` lane permanently, and must then
cross-check the collected function names character for character against the design's
per-property test-function table -- a renamed test is a coverage gap this by-number guard
would otherwise report as satisfied.

Search roots
------------
`tasks.md` task 2.3 says "across `tests/property/`", but the authoritative
property-to-test mapping in `design.md` places P-58's test in
`tests/test_marketplace_paper_schema_contract.py` (see spec task 11.8,
`test_p58_migration_set_is_idempotent`), because that property is about migration
application orders and belongs with the schema contract. Scanning only `tests/property/`
would make this guard permanently unsatisfiable at task 35.1, so the one declared
out-of-tree module is included explicitly rather than by widening the scan to all of
`tests/`. Adding a property test outside these roots is itself reported, as a missing
property.

Parsing, not importing
----------------------
Discovery is a `ast` parse. Importing the modules would drag in production modules that
later tasks have not created yet, and an ImportError in a sibling module must not be able to
corrupt the scoreboard's reading.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, Iterator, List, Sequence, Tuple

import pytest

#: The number of correctness properties in `requirements.md`; P-1 through P-58 inclusive.
PROPERTY_COUNT: int = 58

#: Every property number that must be claimed by exactly one test function.
EXPECTED_PROPERTIES: frozenset = frozenset(range(1, PROPERTY_COUNT + 1))

#: A property-based test function is named `test_p{number}_{description}`.
PROPERTY_FUNCTION_PATTERN: re.Pattern = re.compile(r"^test_p(\d+)_")

TESTS_ROOT: Path = Path(__file__).resolve().parents[1]
PROPERTY_ROOT: Path = TESTS_ROOT / "property"

#: Property tests that `design.md` deliberately places outside `tests/property/`.
OUT_OF_TREE_PROPERTY_MODULES: Tuple[Path, ...] = (
    TESTS_ROOT / "test_marketplace_paper_schema_contract.py",
)


def _search_paths() -> List[Path]:
    """Return every existing test module that may declare a property-based test."""
    paths: List[Path] = sorted(PROPERTY_ROOT.rglob("test_*.py"))
    paths.extend(path for path in OUT_OF_TREE_PROPERTY_MODULES if path.is_file())
    return paths


def _property_functions_in(path: Path) -> Iterator[Tuple[int, str]]:
    """Yield `(property_number, qualified_name)` for each property test in `path`.

    A module that does not parse is a hard failure rather than a silent zero: an
    unparseable module could otherwise hide the very test it is supposed to contain.
    """
    # utf-8-sig, not utf-8: an editor-written BOM must not be read as a syntax error.
    source = path.read_text(encoding="utf-8-sig")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - reported, never swallowed
        raise AssertionError(
            f"tests/{path.relative_to(TESTS_ROOT).as_posix()} does not parse, so the "
            f"property coverage of this module cannot be read: {exc}"
        ) from exc

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        match = PROPERTY_FUNCTION_PATTERN.match(node.name)
        if match is None:
            continue
        relative = path.relative_to(TESTS_ROOT).as_posix()
        yield int(match.group(1)), f"tests/{relative}::{node.name}"


def collect_property_functions() -> Dict[int, List[str]]:
    """Map each discovered property number to every test function claiming it."""
    found: Dict[int, List[str]] = {}
    for path in _search_paths():
        for number, qualified_name in _property_functions_in(path):
            found.setdefault(number, []).append(qualified_name)
    for names in found.values():
        names.sort()
    return found


def _compact(numbers: Sequence[int]) -> str:
    """Render a sorted number sequence as compact ranges: `P-1..P-4, P-12, P-14`."""
    if not numbers:
        return "(none)"
    parts: List[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        parts.append(f"P-{start}" if start == previous else f"P-{start}..P-{previous}")
        start = previous = number
    parts.append(f"P-{start}" if start == previous else f"P-{start}..P-{previous}")
    return ", ".join(parts)


def _report(
    found: Dict[int, List[str]],
    missing: Sequence[int],
    duplicated: Dict[int, List[str]],
    unknown: Dict[int, List[str]],
) -> str:
    """Build the failure message: the whole gap, named, in one read."""
    covered = sorted(
        number
        for number, names in found.items()
        if number in EXPECTED_PROPERTIES and len(names) == 1
    )
    lines: List[str] = [
        "Property coverage (Requirement 29.3): "
        f"{len(covered)}/{PROPERTY_COUNT} properties have exactly one property-based test.",
        "",
        "Search roots:",
    ]
    lines.append(
        f"  tests/{PROPERTY_ROOT.relative_to(TESTS_ROOT).as_posix()}/test_*.py"
    )
    lines.extend(
        f"  tests/{path.relative_to(TESTS_ROOT).as_posix()}"
        f"{'' if path.is_file() else '  (not created yet)'}"
        for path in OUT_OF_TREE_PROPERTY_MODULES
    )
    lines.append("")

    if missing:
        lines.append(f"MISSING -- no test_p{{n}}_ function ({len(missing)}):")
        lines.append(f"  {_compact(missing)}")
        lines.append("")
    if duplicated:
        lines.append(f"DUPLICATED -- more than one test function ({len(duplicated)}):")
        for number in sorted(duplicated):
            lines.append(f"  P-{number}:")
            lines.extend(f"    {name}" for name in duplicated[number])
        lines.append("")
    if unknown:
        lines.append(
            f"UNKNOWN -- outside P-1..P-{PROPERTY_COUNT} ({len(unknown)}); either the "
            "number is a typo or the property is not in requirements.md:"
        )
        for number in sorted(unknown):
            lines.extend(f"  P-{number}: {name}" for name in unknown[number])
        lines.append("")

    lines.append(
        "Each property is implemented by its own spec sub-task; this guard is the "
        "scoreboard for those tasks and is closed by task 35.1."
    )
    return "\n".join(lines)


@pytest.mark.property_scoreboard
def test_every_property_has_exactly_one_property_based_test() -> None:
    """Each of P-1...P-58 is claimed by exactly one `test_p{n}_` function.

    Validates: Requirements 29.3
    """
    found = collect_property_functions()

    missing = sorted(EXPECTED_PROPERTIES - set(found))
    duplicated = {
        number: names
        for number, names in found.items()
        if number in EXPECTED_PROPERTIES and len(names) > 1
    }
    unknown = {
        number: names
        for number, names in found.items()
        if number not in EXPECTED_PROPERTIES
    }

    assert not missing and not duplicated and not unknown, _report(
        found, missing, duplicated, unknown
    )
