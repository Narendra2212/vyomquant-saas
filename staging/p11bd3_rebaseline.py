"""One-shot helper: re-record ONLY `live_order_path`, preserving provenance notes.

`capture_baseline.main()` rewrites a baseline file from the live capture, and the capture
never emits `_baseline_note*` members -- so a blanket re-record would silently DELETE the
TRADE-01 and TRADE-12 authorisations already recorded in the file. This script re-records
through the official capture and then puts every pre-existing note back, so the only
content difference is the one the DUP-02 change actually introduced.

Run from the repository root. Kept as provenance for the one baseline entry this phase
re-recorded; it is not collected by pytest (its name does not match `test_*`).
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.regression.capture_baseline import (  # noqa: E402
    baseline_path,
    capture,
    write_baseline,
)
from tests.regression.test_baseline_unchanged import is_baseline_note  # noqa: E402

NAME = "live_order_path"

NEW_NOTE_KEY = "_baseline_note_ambiguous_submission_refusal_dup02"
NEW_NOTE_TEXT = (
    "Phase 11B-D3 (DUP-02). `contract.returns` gained ONE entry: the "
    "`skipped_ambiguous_submission` refusal returned by "
    "`ExecutionEngine.execute_with_idempotency` when "
    "`check_idempotent_execution` reports that a previous attempt for this "
    "execution may already have reached the venue (`submitted_at` committed "
    "before `create_order`, or a status of UNKNOWN). Before this change the "
    "`allow_retry` action had no branch in the engine and reached the claim by "
    "fall-through, which resubmitted an order that may already have been live -- "
    "undetectable at coinbase and kraken, which do not honour our clientOrderId "
    "on ccxt 4.3.92. The added entry is shape-identical to the existing refusal "
    "returns (status/execution_id/result/message); it is a NEW OUTCOME, not a "
    "changed or removed one, and no recorded key was renamed, retyped or "
    "re-meaninged. Evidence: staging/PHASE11B_D3_DUP01_RETRY_FIX_REPORT.md; "
    "regression: tests/test_dup02_ambiguous_submission_is_not_retried.py."
)


def collect_notes(node, trail=()):
    """Every `_baseline_note*` member in the document, keyed by its container path."""
    found = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if is_baseline_note(key):
                found[(trail, key)] = value
            else:
                found.update(collect_notes(value, trail + (key,)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.update(collect_notes(value, trail + (index,)))
    return found


def put(document, trail, key, value):
    cursor = document
    for step in trail:
        cursor = cursor[step]
    cursor[key] = value


def main() -> int:
    path = baseline_path(NAME)
    before = json.loads(path.read_text(encoding="utf-8"))
    notes = collect_notes(before)
    print(f"preserving {len(notes)} existing provenance note(s):")
    for (trail, key) in sorted(notes, key=lambda item: (item[0], item[1])):
        print(f"  {'.'.join(str(step) for step in trail) or '<root>'} :: {key}")

    fresh = copy.deepcopy(capture(NAME))

    returns_before = before.get("contract", {}).get("returns", [])
    returns_after = fresh.get("contract", {}).get("returns", [])
    print(f"contract.returns: {len(returns_before)} -> {len(returns_after)}")
    if len(returns_after) != len(returns_before) + 1:
        print(
            "REFUSING: expected exactly one added return entry. "
            "A different drift is present and must be reviewed by hand."
        )
        return 2

    for (trail, key), value in notes.items():
        try:
            put(fresh, trail, key, value)
        except (KeyError, IndexError, TypeError) as exc:
            print(f"REFUSING: cannot restore note at {trail}/{key}: {exc!r}")
            return 3

    fresh[NEW_NOTE_KEY] = NEW_NOTE_TEXT
    write_baseline(NAME, fresh)
    print(f"wrote {path}")
    print(f"notes now present: {len(collect_notes(fresh))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
