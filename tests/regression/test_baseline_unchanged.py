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

RE-RECORDING ONE ENTRY
    A baseline entry may be re-recorded only where a requirement *authorises* the change, and
    the authorisation is written into the baseline file beside the entry as a
    ``_baseline_note…`` member (see ``BASELINE_NOTE_PREFIX``). A re-record with no such note is
    a regression that was papered over, and is to be treated as one.

AT THIS POINT IN THE PLAN THIS MODULE IS A TAUTOLOGY
    Nothing has been changed yet, so the re-run and the file agree trivially. That is the
    point: it establishes that the captures are reproducible, and the assertion that matters
    is that this module keeps passing after every later task. It is the guard, not the proof.
"""

from __future__ import annotations

import ast
import json
from typing import Any, Dict, List, Optional, Sequence

import pytest

from tests.regression.capture_baseline import (
    BASELINE_DIR,
    CAPTURES,
    api_module_index,
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
#  PROVENANCE ANNOTATIONS INSIDE A BASELINE FILE
# ══════════════════════════════════════════════════════════════════════════

#: A baseline entry may only be re-recorded when a requirement in this spec *authorises* the
#: change, and the authorisation has to be readable next to the entry it excuses — a silently
#: re-recorded baseline is indistinguishable from a regression that was papered over. JSON has
#: no comments, so the justification is carried as an object member whose name begins with this
#: prefix. Such a member is documentation, not contract: the differ skips it on the baseline
#: side so it is not reported as "key removed" when the re-run (which never emits one) is
#: compared against it.
#:
#: This grants no licence to hide a regression. Dropping a *real* recorded key still requires
#: deleting it from the baseline file, which is exactly what shows up in review; the prefix
#: only makes the reason for that deletion travel with it.
BASELINE_NOTE_PREFIX = "_baseline_note"


def is_baseline_note(key: str) -> bool:
    """Whether a baseline object member is a provenance annotation rather than a contract key."""
    return str(key).startswith(BASELINE_NOTE_PREFIX)


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
        if is_baseline_note(key):
            continue  # a provenance annotation, not a recorded contract key
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


#: Key tuples that *identify* a record, tried in this order against an array of objects.
#:
#: WHY AN IDENTITY IS NEEDED AT ALL. Some recorded arrays are sequences, where position is
#: the meaning — a collaborator call order, a handler's ``returns`` in source order. Others
#: are *sets of identity-bearing records* that happen to be written in a sorted order:
#: ``api_calls`` is derived from the frontend source and sorted by ``(method, path_template,
#: via)``, so a surface that starts calling one more endpoint INSERTS an entry and shifts
#: every entry after it. Diffed by position, one insertion is then reported as N changed
#: fields, N removals and N additions, none of which happened: ``api_calls[4]`` simply holds
#: a different record now. Those reports are not merely noisy, they are false, and a guard
#: that cries wolf about a route it invented is a guard nobody can read.
#:
#: WHAT THIS DOES NOT DO. It does not make an insertion permissible. A baseline record with
#: no match in the re-run is still reported as removed, an unmatched re-run record is still
#: reported as added, and a matched pair is still compared field by field with every rule
#: below applying unchanged — including the order check, which is re-applied to the records
#: the two lists have in common so a genuine reordering is still caught.
#:
#: A tuple is only used when it identifies *every* entry of both lists UNIQUELY. An array
#: whose entries carry no such key — ``raises``, ``returns``, a ``_CallLog`` sequence whose
#: ``call`` names repeat — falls through to the positional comparison below rather than
#: being matched on a key invented for it.
IDENTITY_KEYS: Sequence[Sequence[str]] = (
    ("via", "method", "path_template"),  # an ``api_calls`` entry
    ("module", "handler"),  # a route contract's ``handlers`` entry
    ("method", "path_template"),  # a ``frontend_bindings`` entry
    ("method", "path"),  # a ``routes`` entry
)


def _identity_of(entry: Dict[str, Any], keys: Sequence[str]) -> str:
    return json.dumps([entry[key] for key in keys], sort_keys=True)


def identity_keys(baseline: Sequence[Any], current: Sequence[Any]) -> Optional[Sequence[str]]:
    """The first tuple in ``IDENTITY_KEYS`` that uniquely identifies every entry, or ``None``."""
    entries = list(baseline) + list(current)
    if not entries or not all(isinstance(entry, dict) for entry in entries):
        return None
    for keys in IDENTITY_KEYS:
        if not all(all(key in entry for key in keys) for entry in entries):
            continue
        if all(
            len({_identity_of(entry, keys) for entry in side}) == len(list(side))
            for side in (baseline, current)
        ):
            return keys
    return None


def _compare_records_by_identity(
    baseline: Sequence[Any], current: Sequence[Any], path: str, keys: Sequence[str]
) -> List[str]:
    """Match the two lists on ``keys``, then diff each matched pair as a whole record.

    The reported path keeps the entry's **baseline** index — ``api_calls[4].handlers[0]`` —
    because that is the position a reader opens the baseline file at to see what the
    recorded record was, whatever position the re-run happens to hold it at now.
    """
    current_by_identity = {_identity_of(entry, keys): entry for entry in current}
    baseline_identities = {_identity_of(entry, keys) for entry in baseline}

    failures: List[str] = []
    matched_in_baseline_order: List[str] = []
    for index, entry in enumerate(baseline):
        identity = _identity_of(entry, keys)
        if identity not in current_by_identity:
            failures.append(
                f"  {_at(path)}: entry removed — {_rendered(entry)} (matched on "
                f"{list(keys)}; present in baseline at [{index}], absent now)"
            )
            continue
        matched_in_baseline_order.append(identity)
        failures.extend(compare(entry, current_by_identity[identity], f"{path}[{index}]"))

    for entry in current:
        if _identity_of(entry, keys) in baseline_identities:
            continue
        failures.append(
            f"  {_at(path)}: entry added — {_rendered(entry)} is not one of the additive "
            f"fields {sorted(ADDITIVE_FIELDS)} that design.md declares"
        )

    # Order is still contractual, so it is checked on the records the two lists have in
    # common: a permitted addition must not be reported as a reordering of its neighbours.
    matched_in_current_order = [
        identity
        for identity in (_identity_of(entry, keys) for entry in current)
        if identity in baseline_identities
    ]
    if matched_in_baseline_order != matched_in_current_order:
        failures.append(f"  {_at(path)}: recorded order changed")
    return failures


def _compare_arrays(baseline: Sequence[Any], current: Sequence[Any], path: str) -> List[str]:
    """Baseline entries must all still be there, in order. Extras must be additive.

    Recorded arrays are of three kinds. An array of strings — a ``keys`` list, a collaborator
    call sequence — is diffed entry by entry *and* on order, so a lost key names itself and a
    reordering is still caught. An array of **identity-bearing records** is matched on that
    identity (see ``IDENTITY_KEYS``) and then diffed pair by pair, so an insertion is one
    addition rather than a cascade of invented removals. Any other array of objects is diffed
    by requiring the baseline to remain an ordered subsequence, which catches removal, change
    and reordering together.
    """
    if all(isinstance(item, str) for item in baseline) and all(
        isinstance(item, str) for item in current
    ):
        return _compare_string_arrays(list(baseline), list(current), path)

    keys = identity_keys(baseline, current)
    if keys is not None:
        return _compare_records_by_identity(baseline, current, path, keys)

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


def test_baseline_provenance_annotation_is_not_a_recorded_key() -> None:
    """A ``_baseline_note*`` member documents an authorised re-record; it is not a contract key.

    It must not read as "key removed" when the re-run is diffed against the file, and it must
    not buy any other slack: a real key that disappears next to one is still a failure.
    """
    annotated = {
        "_baseline_note_returns": "Requirement 8.11 authorises the removal of suggested_price.",
        "library_id": "computed",
        "moderation_status": "str",
    }
    assert compare(annotated, {"library_id": "computed", "moderation_status": "str"}) == []
    assert any(
        "moderation_status" in failure and "removed" in failure
        for failure in compare(annotated, {"library_id": "computed"})
    )


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


# ══════════════════════════════════════════════════════════════════════════
#  IDENTITY-MATCHED RECORD ARRAYS
#
#  An insertion into a sorted array of identity-bearing records must be reported as one
#  addition and nothing else — and every verdict the positional comparison gave must
#  still be given. Both halves are asserted, because the first without the second would
#  be a differ that had been quieted rather than corrected.
# ══════════════════════════════════════════════════════════════════════════


def _api_call(via: str, method: str, path: str, handler: str) -> Dict[str, Any]:
    return {
        "via": via,
        "method": method,
        "path_template": path,
        "path": path,
        "resolved": True,
        "handler_count": 1,
        "handlers": [
            {
                "module": "backend_app.routers.orders",
                "handler": handler,
                "route_path": path,
                "decorators": [f"router.get('{path}')"],
                "parameters": {"limit": {"annotation": "int"}},
            }
        ],
    }


_FIRST = _api_call("apiClient direct", "GET", "/api/orders/history", "get_history")
_SECOND = _api_call("api.strategies.list", "GET", "/api/strategies", "list_strategies")
_INSERTED = _api_call("api.library.myStrategies", "GET", "/api/library/my", "my_strategies")


def test_inserted_record_is_reported_once_and_shifts_nothing() -> None:
    """The Cause-1 defect: an insertion must not report the entries after it as changed."""
    failures = compare({"api_calls": [_FIRST, _SECOND]}, {"api_calls": [_FIRST, _INSERTED, _SECOND]})
    assert len(failures) == 1, failures
    assert "entry added" in failures[0]
    assert "my_strategies" in failures[0]
    # Nothing about the entry the insertion displaced may be reported at all.
    assert not any("get_history" in failure or "list_strategies" in failure for failure in failures)


def test_a_genuinely_removed_record_is_still_a_failure() -> None:
    failures = compare({"api_calls": [_FIRST, _SECOND]}, {"api_calls": [_SECOND]})
    assert any(
        "entry removed" in failure and "get_history" in failure for failure in failures
    ), failures


def test_a_genuinely_added_record_is_still_a_failure() -> None:
    failures = compare({"api_calls": [_FIRST]}, {"api_calls": [_FIRST, _INSERTED]})
    assert any(
        "entry added" in failure and "my_strategies" in failure for failure in failures
    ), failures


def test_a_field_change_on_a_matched_record_is_still_a_failure() -> None:
    """The identity aligns the pair; every field on it is then compared as before."""
    weakened = json.loads(json.dumps(_FIRST))
    weakened["handlers"][0]["decorators"] = []
    failures = compare({"api_calls": [_FIRST, _SECOND]}, {"api_calls": [weakened, _SECOND]})
    assert any(
        "api_calls[0].handlers[0].decorators" in failure and "removed" in failure
        for failure in failures
    ), failures


def test_a_lost_parameter_on_a_record_after_an_insertion_is_still_a_failure() -> None:
    """The insertion is reported, and so is the real loss it would otherwise have masked."""
    weakened = json.loads(json.dumps(_SECOND))
    del weakened["handlers"][0]["parameters"]["limit"]
    failures = compare(
        {"api_calls": [_FIRST, _SECOND]}, {"api_calls": [_FIRST, _INSERTED, weakened]}
    )
    assert any("entry added" in failure and "my_strategies" in failure for failure in failures)
    assert any(
        "api_calls[1].handlers[0].parameters.limit" in failure and "removed" in failure
        for failure in failures
    ), failures


def test_reordering_identity_bearing_records_is_still_a_failure() -> None:
    failures = compare({"api_calls": [_FIRST, _SECOND]}, {"api_calls": [_SECOND, _FIRST]})
    assert any("recorded order changed" in failure for failure in failures), failures


def test_an_array_whose_entries_have_no_identity_stays_positional() -> None:
    """``raises`` carries no stable key, so no key is invented for it."""
    raises = [{"status_code": 404, "detail_type": "str"}, {"status_code": 500, "detail_type": "str"}]
    assert identity_keys(raises, raises) is None
    failures = compare({"raises": raises}, {"raises": [raises[1], raises[0]]})
    assert failures, "a reordered raises list must still be reported"


def test_a_repeated_identity_falls_back_to_the_positional_comparison() -> None:
    """Two routes can share ``(module, handler)``; a non-unique key identifies nothing."""
    twin = {"module": "backend_app.routers.strategies", "handler": "list_strategies"}
    assert identity_keys([twin, dict(twin)], [twin, dict(twin)]) is None


# ══════════════════════════════════════════════════════════════════════════
#  THE FRONTEND EXTRACTOR'S ATTRIBUTION
#
#  ``api_module_index`` states which paths a named api method requests. A baseline built
#  on it is only worth reading if that statement is true of the source, so the two facts
#  the Cause-2 defect got wrong are asserted directly against the real modules.
# ══════════════════════════════════════════════════════════════════════════


def test_a_nested_api_method_is_attributed_to_its_own_name() -> None:
    """``api.paper.sessions.create`` is recorded as ``sessions.create``, not as ``getSummary``.

    ``paper.js`` declares the fourteen session routes inside a nested ``sessions: { … }``
    object literal. An indentation-blind scan finds no member there, does not descend, and
    files all fourteen paths under the last top-level method before them — a false statement
    about the code that a baseline must not be allowed to record.
    """
    paper = api_module_index()["paper"]

    assert paper["getSummary"] == [{"method": "GET", "path_template": "/api/paper/summary"}], (
        "getSummary calls get('/api/paper/summary') and nothing else"
    )
    assert paper["sessions.create"] == [
        {"method": "POST", "path_template": "/api/paper/sessions"}
    ]
    assert paper["sessions.get"] == [
        {"method": "GET", "path_template": "/api/paper/sessions/{expr}"}
    ]
    assert paper["sessions.events"] == [
        {
            "method": "GET",
            "path_template": "/api/paper/sessions/{expr}/events?since_sequence={expr}",
        }
    ]
    nested = sorted(name for name in paper if name.startswith("sessions."))
    assert len(nested) == 14, nested


def test_an_object_spliced_in_by_shorthand_is_attributed_through_its_member_name() -> None:
    """``library.js`` splices ``submissions`` and ``admin`` in by shorthand, not inline.

    They are separate top-level ``const`` objects, so an indentation-blind scan attributed
    their methods to ``libraryApi`` itself — ``api.library.approve``, a method that does not
    exist. The name a caller writes is ``api.library.admin.approve``.
    """
    library = api_module_index()["library"]

    assert library["submissions.create"] == [
        {"method": "POST", "path_template": "/api/library/submissions"}
    ]
    assert library["admin.approve"] == [
        {"method": "POST", "path_template": "/api/library/admin/submissions/{expr}/approve"}
    ]
    for absent in ("create", "approve", "priceRange", "listSubmissions", "unpublish"):
        assert absent not in library, f"api.library.{absent} is not a method of libraryApi"
    assert library["myStrategies"] == [
        {"method": "GET", "path_template": "/api/library/my-strategies"}
    ]


def test_a_path_inside_a_comment_is_not_attributed_to_the_method_above_it() -> None:
    """A commented-out call is not a call the module makes.

    ``strategies.js`` documents the removal of ``getBlocks`` by quoting its former body. The
    body-to-next-match slice swallowed that comment into ``trainMl`` and recorded
    ``GET /api/strategies/blocks`` as a path ``trainMl`` requests.
    """
    assert api_module_index()["strategies"]["trainMl"] == [
        {"method": "POST", "path_template": "/api/strategies/{strategyId}/train"}
    ]
