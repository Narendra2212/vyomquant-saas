"""The property-coverage scoreboard: one property, one property-based test.

Requirement 29.3 demands a property-based test for each of the fifty-eight correctness
properties in `requirements.md` section "Correctness properties for property-based testing".
A prose list of properties is not a check. This module turns it into one: it parses every
test module in the property-test search roots and asserts that each of P-1...P-58 is claimed
by exactly one `test_p{n}_...` function -- none missing, none duplicated, none unknown.

Closed at spec task 35.1, and now in the default lane
-----------------------------------------------------
This guard was written at spec task 2.3, before any of the fifty-eight property tests
existed, so it failed immediately and reported all fifty-eight as missing. That failure was
the point: it was the running scoreboard for tasks 4 through 34. Its *collection* -- never its
assertion -- was gated on `AERORA_PROPERTY_SCOREBOARD=1` by a `collect_ignore` in
`tests/property/conftest.py` so the shared `unit-tests` job in
`.github/workflows/01-pr-check.yml` (`pytest tests/ -k "not chaos and not load"`) kept exactly
the scope it had while the plan was in flight.

Spec task 35.1 deleted that conftest. All fifty-eight properties now have exactly one test
function each, so the scoreboard is green and belongs in the default `pytest tests/` lane
permanently: a property test deleted, renamed away from its number, or duplicated from now on
reds CI. No assertion here was ever weakened, skipped or marked expected-to-fail
(Requirements 29.8 and 29.9 forbid making a check non-blocking), and the CI `-k` selector is
untouched -- removing the gate only widens what is checked.

The by-number guard alone is not enough, which is why this module carries a second check.
`test_every_property_has_exactly_one_property_based_test` only reads the *number* in
`test_p{n}_`, so renaming `test_p1_conservation` to `test_p1_whatever`, or moving it to
another module, would leave it satisfied while the design's stated mapping silently rotted.
`test_design_table_matches_the_collected_property_tests` closes that hole: it parses the
per-property table in `design.md` at run time and compares function name character for
character and module path exactly. Neither check subsumes the other -- the first catches a
missing or duplicated property, the second catches a renamed or relocated one.

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
from typing import Dict, Iterator, List, NamedTuple, Sequence, Tuple

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


class PropertyTest(NamedTuple):
    """One `test_p{n}_...` function found in the sources."""

    number: int
    #: The function name exactly as written, for the character-for-character comparison.
    function_name: str
    #: Repository-relative module path, POSIX separators, e.g. `tests/property/test_x.py`.
    module: str

    @property
    def qualified_name(self) -> str:
        """`tests/property/test_x.py::test_p1_conservation`."""
        return f"{self.module}::{self.function_name}"


def _property_functions_in(path: Path) -> Iterator[PropertyTest]:
    """Yield one `PropertyTest` per property-based test function declared in `path`.

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
        yield PropertyTest(
            number=int(match.group(1)),
            function_name=node.name,
            module=f"tests/{relative}",
        )


def collect_property_tests() -> Dict[int, List[PropertyTest]]:
    """Map each discovered property number to every test function claiming it."""
    found: Dict[int, List[PropertyTest]] = {}
    for path in _search_paths():
        for declaration in _property_functions_in(path):
            found.setdefault(declaration.number, []).append(declaration)
    for declarations in found.values():
        declarations.sort(key=lambda declaration: declaration.qualified_name)
    return found


def collect_property_functions() -> Dict[int, List[str]]:
    """Map each discovered property number to every qualified test name claiming it."""
    return {
        number: [declaration.qualified_name for declaration in declarations]
        for number, declarations in collect_property_tests().items()
    }


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


# ---------------------------------------------------------------------------------------
# The character-for-character cross-check against the design's per-property table
# ---------------------------------------------------------------------------------------
# The guard above reads only the number in `test_p{n}_`, so a renamed or relocated property
# test still satisfies it. `design.md` states, per property, both the test-function name and
# the module it lives in; this section parses those two tables at run time and compares them
# to the collected functions exactly, so a future rename reds the suite instead of quietly
# making the design's mapping fiction.

#: The design document that declares the per-property mapping (spec `design.md`).
DESIGN_PATH: Path = (
    TESTS_ROOT.parent
    / ".kiro"
    / "specs"
    / "marketplace-subscriptions-paper-trading"
    / "design.md"
)

#: The one section of `design.md` that carries the mapping; both tables live inside it.
DESIGN_SECTION_HEADING: str = "### Property-to-test mapping"

#: Header row of the grouped table that names the module for a range of properties.
MODULE_TABLE_HEADER: str = "| Properties | Module | Generators | Oracle |"

#: Header row of the per-property table that names one test function per property.
NAME_TABLE_HEADER: str = "| Property | Test function |"

#: `P-17` or `P-17 ... P-20` / `P-17 … P-20`; the design uses the unicode ellipsis.
PROPERTY_ID_PATTERN: re.Pattern = re.compile(
    r"^P-(\d+)(?:\s*(?:\.\.\.|\.\.|\u2026)\s*P-(\d+))?$"
)


def _design_lines() -> List[str]:
    """Return the lines of the mapping section of `design.md`.

    Scoping the parse to the one section is what makes the two tables unambiguous: a table is
    identified by its exact header row, and a duplicated or missing heading is reported rather
    than guessed at.
    """
    assert DESIGN_PATH.is_file(), (
        f"{DESIGN_PATH} does not exist, so the per-property test-function table "
        "(Requirement 29.3) cannot be cross-checked against the collected tests."
    )
    lines = DESIGN_PATH.read_text(encoding="utf-8-sig").splitlines()

    starts = [
        index
        for index, line in enumerate(lines)
        if line.strip() == DESIGN_SECTION_HEADING
    ]
    assert len(starts) == 1, (
        f"design.md must contain exactly one '{DESIGN_SECTION_HEADING}' heading for the "
        f"cross-check to know which tables to read; found {len(starts)} at lines "
        f"{[index + 1 for index in starts]}."
    )

    start = starts[0]
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("### "):
            return lines[start:index]
    return lines[start:]


def _table_rows(lines: Sequence[str], header: str) -> List[List[str]]:
    """Return the body rows of the markdown table introduced by `header`, cell by cell."""
    matches = [index for index, line in enumerate(lines) if line.strip() == header]
    assert len(matches) == 1, (
        f"the '{DESIGN_SECTION_HEADING}' section of design.md must contain exactly one table "
        f"with the header row '{header}'; found {len(matches)}. The cross-check will not guess "
        "which table states the mapping."
    )

    rows: List[List[str]] = []
    # +1 is the header, +2 the `|---|---|` delimiter, so the body starts at +2.
    for line in lines[matches[0] + 2 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        rows.append([cell.strip() for cell in stripped.strip("|").split("|")])
    assert rows, f"the table '{header}' in design.md has no rows."
    return rows


def _parse_property_ids(cell: str) -> List[int]:
    """Expand a `Properties` cell -- `P-8 … P-10, P-15` -- into `[8, 9, 10, 15]`."""
    numbers: List[int] = []
    for chunk in cell.split(","):
        match = PROPERTY_ID_PATTERN.match(chunk.strip())
        assert match is not None, (
            f"design.md property cell {cell!r} contains the fragment {chunk.strip()!r}, which "
            "is not 'P-<n>' or 'P-<n> … P-<m>'. The mapping table cannot be parsed "
            "unambiguously; fix the table rather than hand-listing property names in Python."
        )
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else first
        assert first <= last, (
            f"design.md property range {chunk.strip()!r} runs backwards ({first} > {last})."
        )
        numbers.extend(range(first, last + 1))
    return numbers


def _one_entry_per_property(rows: Dict[int, List[str]], table: str) -> Dict[int, str]:
    """Assert the parsed table names exactly P-1…P-58 once each, and flatten it."""
    duplicated = {number: values for number, values in rows.items() if len(values) > 1}
    missing = sorted(EXPECTED_PROPERTIES - set(rows))
    unknown = sorted(number for number in rows if number not in EXPECTED_PROPERTIES)
    assert not duplicated and not missing and not unknown, (
        f"design.md's '{table}' table does not state exactly one entry for each of "
        f"P-1..P-{PROPERTY_COUNT} (Requirement 29.3).\n"
        f"  missing:    {_compact(missing)}\n"
        f"  outside range: {[f'P-{number}' for number in unknown] or '(none)'}\n"
        f"  duplicated: {{{', '.join(f'P-{n}: {v}' for n, v in sorted(duplicated.items()))}}}"
    )
    return {number: values[0] for number, values in rows.items()}


def design_property_function_names() -> Dict[int, str]:
    """Map each property to the test-function name `design.md` states for it."""
    collected: Dict[int, List[str]] = {}
    for cells in _table_rows(_design_lines(), NAME_TABLE_HEADER):
        assert len(cells) == 2, (
            f"a row of design.md's per-property table has {len(cells)} cells, expected 2: "
            f"{cells}"
        )
        property_cell, name_cell = cells
        function_name = name_cell.strip().strip("`").strip()
        assert PROPERTY_FUNCTION_PATTERN.match(function_name), (
            f"design.md's per-property table gives {property_cell} the test function "
            f"{function_name!r}, which is not named `test_p<n>_...`."
        )
        for number in _parse_property_ids(property_cell):
            collected.setdefault(number, []).append(function_name)
    return _one_entry_per_property(collected, NAME_TABLE_HEADER)


def design_property_modules() -> Dict[int, str]:
    """Map each property to the test module `design.md` states for it."""
    collected: Dict[int, List[str]] = {}
    for cells in _table_rows(_design_lines(), MODULE_TABLE_HEADER):
        assert len(cells) >= 2, (
            f"a row of design.md's property-to-module table has {len(cells)} cells, expected "
            f"at least 2: {cells}"
        )
        property_cell, module_cell = cells[0], cells[1]
        module = module_cell.strip().strip("`").strip()
        assert module.startswith("tests/") and module.endswith(".py"), (
            f"design.md's property-to-module table maps {property_cell} to {module!r}, which "
            "is not a `tests/...py` module path."
        )
        for number in _parse_property_ids(property_cell):
            collected.setdefault(number, []).append(module)
    return _one_entry_per_property(collected, MODULE_TABLE_HEADER)


def _cross_check_findings(
    designed_names: Dict[int, str],
    designed_modules: Dict[int, str],
    collected: Dict[int, List[PropertyTest]],
) -> List[str]:
    """Return one line per property whose collected test disagrees with the design."""
    findings: List[str] = []
    for number in sorted(EXPECTED_PROPERTIES):
        expected = f"{designed_modules[number]}::{designed_names[number]}"
        declarations = collected.get(number, [])

        if not declarations:
            findings.append(
                f"  P-{number}: design.md declares {expected}, but no test function claims "
                f"P-{number} -- a genuine coverage hole."
            )
            continue
        if len(declarations) > 1:
            found = ", ".join(
                declaration.qualified_name for declaration in declarations
            )
            findings.append(
                f"  P-{number}: design.md declares one test, {expected}, but "
                f"{len(declarations)} claim P-{number}: {found}"
            )
            continue

        declaration = declarations[0]
        if declaration.function_name != designed_names[number]:
            findings.append(
                f"  P-{number}: name differs -- design.md says "
                f"{designed_names[number]!r}, the source declares "
                f"{declaration.function_name!r} (in {declaration.module})."
            )
        if declaration.module != designed_modules[number]:
            findings.append(
                f"  P-{number}: module differs -- design.md says "
                f"{designed_modules[number]!r}, {declaration.function_name} lives in "
                f"{declaration.module!r}."
            )

    reused = {
        name: numbers
        for name, numbers in _by_function_name(collected).items()
        if len(numbers) > 1
    }
    for name, numbers in sorted(reused.items()):
        findings.append(
            f"  {name}: one function claims several properties "
            f"({', '.join(f'P-{number}' for number in numbers)})."
        )
    return findings


def _by_function_name(
    collected: Dict[int, List[PropertyTest]]
) -> Dict[str, List[int]]:
    """Invert the collection: qualified test name -> the properties it claims."""
    inverted: Dict[str, List[int]] = {}
    for number, declarations in collected.items():
        for declaration in declarations:
            inverted.setdefault(declaration.qualified_name, []).append(number)
    for numbers in inverted.values():
        numbers.sort()
    return inverted


@pytest.mark.property_scoreboard
def test_design_table_matches_the_collected_property_tests() -> None:
    """Every P-1...P-58 test matches design.md's name and module exactly.

    The by-number guard above cannot see a rename: `test_p1_anything` satisfies it. This
    compares the collected function name character for character, and the module path
    exactly, against the two tables in design.md's "Property-to-test mapping" section, so a
    rename or a move must be reflected in the design instead of silently invalidating it.

    Validates: Requirements 29.3
    """
    designed_names = design_property_function_names()
    designed_modules = design_property_modules()
    collected = collect_property_tests()

    findings = _cross_check_findings(designed_names, designed_modules, collected)

    assert not findings, "\n".join(
        [
            "design.md's per-property test-function table and the collected property tests "
            f"disagree in {len(findings)} place(s) (Requirement 29.3). A renamed or moved "
            "test is a coverage gap the by-number scoreboard reports as satisfied:",
            "",
            *findings,
            "",
            "Fix whichever side is wrong: if the test was renamed deliberately, update the "
            f"tables in {DESIGN_PATH.name}; if the design name is correct, the test is "
            "missing or misnamed.",
        ]
    )
