"""Requirement 25.7 / 25.8 / 17.12 — the pre-change baseline still holds.

``tests/regression/capture_baseline.py`` recorded the behaviour of the live-order path, the
credential vault, the live runtime, signal generation, the Signal Trace live recording, the
eight billing paths, the five authentication paths, the seventeen Requirement 25.4 surfaces,
the risk-utilisation arithmetic and the six existing ``/api/paper/*`` endpoints — one JSON
file per named path and per surface under ``baseline/``.

This module re-runs **every** one of those captures and asserts per-file equality against the
stored file. One parametrised test per baseline file, as Requirement 25.7's "for each named
path and surface individually" requires: a failure names the file it came from in the test id
itself, before its message is even read.

WHAT COUNTS AS A REGRESSION
    A key that the baseline records and the re-run does not → **failure** (removed or renamed).
    A key whose recorded type or value changed → **failure** (retyped, or re-meaninged where
    the capture pins values rather than types, as ``risk_utilisation`` deliberately does).
    A reordering of a recorded sequence → **failure**: the collaborator call sequences in
    ``live_order_path`` and ``credential_vault`` are contracts about order.

WHAT IS PERMITTED
    Exactly one thing: an **added** key whose name is one of the five additive fields
    ``design.md`` declares for the paper surfaces — ``execution_environment``,
    ``is_simulated``, ``session_id``, ``stale``, ``last_price_at``
    (``design.md § Paper account persistence``, Requirements 13.6, 17.12, 18.15). Any other
    addition is a failure too, because an unannounced new field on a live-trading, billing or
    authentication response is exactly the kind of drift Requirement 25 exists to catch.

    The allowance is by **leaf name**, so it holds at any depth: a ``stale`` key inside
    ``positions[]`` is permitted, and so is the ``positions[].stale`` entry that appears in
    the capture's own ``keys`` list.

AT THIS POINT IN THE PLAN THIS MODULE IS A TAUTOLOGY
    Nothing has been changed yet, so the re-run and the file agree trivially. That is the
    point: it establishes that the captures are reproducible, and the assertion that matters
    is that this module keeps passing after every later task. It is the guard, not the proof.
"""

from __future__ import annotations

import ast
import json
from typing import Any, Dict, List, Sequence

import pytest

from tests.regression.capture_baseline import (
    BASELINE_DIR,
    CAPTURES,
    baseline_path,
    capture,
    read_baseline,
)

# ══════════════════════════════════════════════════════════════════════════
#  THE ADDITIVE ALLOWANCE
# ══════════════════════════════════════════════════════════════════════════

#: The only field names this design is permitted to add to an existing response
#: (``design.md § Paper account persistence``). Anything else added is a regression.
ADDITIVE_FIELDS = frozenset(
    {
        "execution_environment",  # Requirement 13.6
        "is_simulated",  # Requirement 17.12
        "session_id",  # null on default-account responses
        "stale",  # Requirement 18.15
        "last_price_at",  # Requirement 18.15
    }
)


def _leaf_name(key_path: str) -> str:
    """``positions[].last_price_at`` → ``last_price_at``; ``stale`` → ``stale``."""
    return key_path.rsplit(".", 1)[-1].replace("[]", "").strip()


def is_additive(key_or_path: str) -> bool:
    """Whether an added key — or an added entry in a capture's ``keys`` list — is allowed."""
    return _leaf_name(key_or_path) in ADDITIVE_FIELDS


# ══════════════════════════════════════════════════════════════════════════
#  THE DIFFER
#
#  Every difference is reported as one line naming its key path, so a regression names
#  itself down to the field rather than dumping two JSON documents side by side.
# ══════════════════════════════════════════════════════════════════════════


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def _rendered(value: Any) -> str:
    """A value short enough to read inside a failure line."""
    text = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
    return text if len(text) <= 120 else text[:117] + "…"


def _at(path: str) -> str:
    return path or "<root>"


def compare(baseline: Any, current: Any, path: str = "") -> List[str]:
    """Every way ``current`` differs from ``baseline``, other than a permitted addition."""
    if _json_type(baseline) != _json_type(current):
        return [
            f"  {_at(path)}: type changed — baseline {_json_type(baseline)}, "
            f"now {_json_type(current)}"
        ]

    if isinstance(baseline, dict):
        return _compare_objects(baseline, current, path)
    if isinstance(baseline, list):
        return _compare_arrays(baseline, current, path)

    if baseline != current:
        return [
            f"  {_at(path)}: value changed — baseline {_rendered(baseline)}, "
            f"now {_rendered(current)}"
        ]
    return []


def _compare_objects(baseline: Dict[str, Any], current: Dict[str, Any], path: str) -> List[str]:
    failures: List[str] = []
    for key in baseline:
        child = f"{path}.{key}" if path else str(key)
        if key not in current:
            failures.append(f"  {child}: key removed (present in baseline, absent now)")
            continue
        failures.extend(compare(baseline[key], current[key], child))
    for key in current:
        if key in baseline:
            continue
        child = f"{path}.{key}" if path else str(key)
        if not is_additive(key):
            failures.append(
                f"  {child}: key added — not one of the additive fields "
                f"{sorted(ADDITIVE_FIELDS)} that design.md declares"
            )
    return failures


def _compare_arrays(baseline: Sequence[Any], current: Sequence[Any], path: str) -> List[str]:
    """Baseline entries must all still be there, in order. Extras must be additive.

    Recorded arrays are of two kinds. An array of strings — a ``keys`` list, a collaborator
    call sequence — is diffed entry by entry *and* on order, so a lost key names itself and a
    reordering is still caught. An array of objects is diffed by requiring the baseline to
    remain an ordered subsequence, which catches removal, change and reordering together.
    """
    if all(isinstance(item, str) for item in baseline) and all(
        isinstance(item, str) for item in current
    ):
        return _compare_string_arrays(list(baseline), list(current), path)

    unmatched = list(current)
    extras: List[Any] = []
    position = 0
    for item in unmatched:
        if position < len(baseline) and not compare(baseline[position], item, f"{path}[…]"):
            position += 1
        else:
            extras.append(item)

    if position == len(baseline):
        failures: List[str] = []
        for extra in extras:
            if isinstance(extra, str) and is_additive(extra):
                continue
            failures.append(
                f"  {_at(path)}: entry added — {_rendered(extra)} is not one of the additive "
                f"fields {sorted(ADDITIVE_FIELDS)} that design.md declares"
            )
        return failures

    # The baseline is not an ordered subsequence of the re-run: something was removed,
    # changed or reordered. Report it index by index, which is what a reader needs.
    failures = []
    for index in range(min(len(baseline), len(current))):
        failures.extend(compare(baseline[index], current[index], f"{path}[{index}]"))
    if len(current) < len(baseline):
        failures.append(
            f"  {_at(path)}: {len(baseline) - len(current)} entr(y/ies) removed — baseline has "
            f"{len(baseline)}, now {len(current)}; first missing: "
            f"{_rendered(baseline[len(current)])}"
        )
    elif len(current) > len(baseline):
        failures.append(
            f"  {_at(path)}: {len(current) - len(baseline)} entr(y/ies) added at a position that "
            f"breaks the recorded order — baseline has {len(baseline)}, now {len(current)}"
        )
    if not failures:
        failures.append(f"  {_at(path)}: recorded order changed")
    return failures


def _compare_string_arrays(
    baseline: List[str], current: List[str], path: str
) -> List[str]:
    """A ``keys`` list or a call sequence: report the entries, then the order."""
    failures: List[str] = []
    removed = [entry for entry in baseline if entry not in current]
    added = [entry for entry in current if entry not in baseline]

    for entry in removed:
        failures.append(f"  {_at(path)}: entry removed — {_rendered(entry)}")
    for entry in added:
        if is_additive(entry):
            continue
        failures.append(
            f"  {_at(path)}: entry added — {_rendered(entry)} is not one of the additive fields "
            f"{sorted(ADDITIVE_FIELDS)} that design.md declares"
        )

    # Order is contractual for a call sequence, so it is checked on what the two lists have
    # in common: a permitted addition must not be reported as a reordering of its neighbours.
    if [e for e in baseline if e in current] != [e for e in current if e in baseline]:
        failures.append(f"  {_at(path)}: recorded order changed")
    return failures


def _normalised(payload: Any) -> Any:
    """The captured payload as it would have been written, so the comparison is like-for-like.

    ``write_baseline`` serialises with ``default=str``; a ``Decimal`` or a ``UUID`` in a
    capture therefore reaches the file as a string. Re-running the capture in memory has to
    pass through the same conversion or every such value would read as a spurious type change.
    """
    return json.loads(json.dumps(payload, sort_keys=True, default=str))


# ══════════════════════════════════════════════════════════════════════════
#  THE REGRESSION ASSERTION — one test per baseline file
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("name", sorted(CAPTURES))
def test_baseline_unchanged(name: str) -> None:
    """Re-run one capture and assert it still equals ``baseline/{name}.json``.

    Requirements 25.7, 25.8, 17.12.
    """
    path = baseline_path(name)
    assert path.exists(), (
        f"{path.name} is missing. The Requirement 25.7 baseline can only be taken from the "
        f"unmodified system; it cannot be regenerated after a change has landed."
    )

    expected = read_baseline(name)
    actual = _normalised(capture(name))

    failures = compare(expected, actual)
    if failures:
        pytest.fail(
            f"{path.name}: {len(failures)} difference(s) from the pre-change baseline.\n"
            f"Only an added {sorted(ADDITIVE_FIELDS)} field is permitted; a removed, renamed, "
            f"retyped or re-meaninged key is a regression (Requirements 25.7, 25.8, 17.12).\n"
            + "\n".join(failures),
            pytrace=False,
        )


def test_every_baseline_file_has_a_capture() -> None:
    """No baseline file may be orphaned, and no capture may lose its file.

    Deleting a baseline file would make its regression test vanish silently, which is the one
    way this suite could stop protecting something without anything going red.
    """
    on_disk = {path.stem for path in BASELINE_DIR.glob("*.json")}
    registered = set(CAPTURES)
    assert on_disk - registered == set(), (
        f"baseline file(s) with no capture in CAPTURES: {sorted(on_disk - registered)}"
    )
    assert registered - on_disk == set(), (
        f"capture(s) with no baseline file: {sorted(registered - on_disk)}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  THE CI-SELECTOR NAMING GUARD
# ══════════════════════════════════════════════════════════════════════════

#: The shared `unit-tests` job runs `pytest tests/ -k "not chaos and not load"`. `-k` matches
#: **substrings** of the node id, so a test named `..._payloads_...` or `..._downloaded_...`
#: is silently deselected: it looks present, is never run, and can never go red. Requirements
#: 29.8 and 29.9 forbid a check being excluded by selector and forbid changing the selector,
#: so the burden falls on the test name.
CI_SELECTOR_EXCLUDED_SUBSTRINGS = ("chaos", "load")

#: The directories this spec adds as test packages. Scoped deliberately: `tests/` at large
#: holds chaos and load suites whose names are excluded on purpose.
SPEC_TEST_PACKAGES = (
    BASELINE_DIR.parent,  # tests/regression
    BASELINE_DIR.parents[1] / "property",
    BASELINE_DIR.parents[1] / "strategies",
)


def test_no_spec_test_name_is_deselected_by_the_ci_selector() -> None:
    """No test added by this spec may carry a name the CI `-k` selector filters out.

    Discovery is an `ast` parse rather than an import: a module from a later task that does
    not yet import cleanly must not be able to hide a badly named test from this guard.

    Validates: Requirements 29.8, 29.9
    """
    offenders: List[str] = []
    for package in SPEC_TEST_PACKAGES:
        if not package.is_dir():
            continue
        for path in sorted(package.rglob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not node.name.startswith("test_"):
                    continue
                hits = [
                    excluded
                    for excluded in CI_SELECTOR_EXCLUDED_SUBSTRINGS
                    if excluded in node.name.lower()
                ]
                if hits:
                    relative = path.relative_to(BASELINE_DIR.parents[1]).as_posix()
                    offenders.append(f"tests/{relative}::{node.name} contains {hits}")

    assert not offenders, (
        'These test names are silently deselected by the CI gate `pytest tests/ -k "not '
        'chaos and not load"`, so they never run (Requirements 29.8, 29.9). Rename the '
        "test; the selector must not be changed.\n" + "\n".join(offenders)
    )


# ══════════════════════════════════════════════════════════════════════════
#  THE DIFFER'S OWN TESTS
#
#  The differ is the only thing standing between a regression and a green suite, so its
#  verdicts are asserted directly rather than trusted.
# ══════════════════════════════════════════════════════════════════════════


def test_identical_captures_report_no_difference() -> None:
    captured = {"keys": ["a", "b"], "type_map": {"a": "str", "b": {"list_of": "int"}}}
    assert compare(captured, json.loads(json.dumps(captured))) == []


def test_removed_key_is_a_failure_naming_its_path() -> None:
    failures = compare({"account": {"total_equity": "str"}}, {"account": {}})
    assert len(failures) == 1
    assert "account.total_equity" in failures[0]
    assert "removed" in failures[0]


def test_retyped_key_is_a_failure_naming_its_path() -> None:
    failures = compare(
        {"account": {"total_equity": "str"}}, {"account": {"total_equity": "float"}}
    )
    assert len(failures) == 1
    assert "account.total_equity" in failures[0]


def test_renamed_key_is_reported_as_both_a_removal_and_an_addition() -> None:
    failures = compare({"total_equity": "str"}, {"equity_total": "str"})
    assert any("total_equity" in failure and "removed" in failure for failure in failures)
    assert any("equity_total" in failure and "added" in failure for failure in failures)


def test_added_additive_field_is_permitted_at_any_depth() -> None:
    baseline = {
        "keys": ["positions", "positions[].symbol"],
        "type_map": {"positions": {"list_of": {"symbol": "str"}}},
    }
    current = {
        "keys": ["positions", "positions[].stale", "positions[].symbol"],
        "type_map": {"positions": {"list_of": {"stale": "bool", "symbol": "str"}}},
    }
    assert compare(baseline, current) == []


def test_added_undeclared_field_is_a_failure() -> None:
    failures = compare(
        {"account": {"currency": "str"}}, {"account": {"currency": "str", "tier": "str"}}
    )
    assert len(failures) == 1
    assert "account.tier" in failures[0]


def test_reordered_sequence_is_a_failure() -> None:
    failures = compare(
        {"call_sequence": ["kill_switch.check", "risk.validate", "exchange.place"]},
        {"call_sequence": ["risk.validate", "kill_switch.check", "exchange.place"]},
    )
    assert failures
    assert any("call_sequence" in failure for failure in failures)


def test_changed_number_is_a_failure() -> None:
    """``risk_utilisation`` pins figures, not types — Requirement 25.5's semantics claim."""
    failures = compare({"values": {"margin_ratio": 9.76}}, {"values": {"margin_ratio": 12.4}})
    assert len(failures) == 1
    assert "values.margin_ratio" in failures[0]
    assert "9.76" in failures[0] and "12.4" in failures[0]


def test_shortened_sequence_is_a_failure() -> None:
    failures = compare({"events": ["created", "filled"]}, {"events": ["created"]})
    assert failures
    assert any("removed" in failure for failure in failures)
