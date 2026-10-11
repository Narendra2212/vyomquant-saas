"""Re-record tests/regression/baseline/live_order_path.json for TRADE-01.

`tests/regression/test_baseline_unchanged.py` permits a re-record ONLY where a
requirement authorises the change and the authorisation travels with the file as a
`_baseline_note…` member: "A re-record with no such note is a regression that was papered
over, and is to be treated as one."

`write_baseline` serialises the capture and nothing else, so running the capture CLI
directly would silently delete the TRADE-05 note already in this file. This script
re-runs the capture, carries every existing `_baseline_note*` member back to the exact
path it occupied, and adds the TRADE-01 note. It refuses if an existing note's parent path
no longer exists, rather than dropping it.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.regression.capture_baseline import (  # noqa: E402
    baseline_path,
    capture,
    write_baseline,
)

NAME = "live_order_path"
NOTE_PREFIX = "_baseline_note"

TRADE01_NOTE_KEY = "_baseline_note_collaborator_sequence_trade01"
TRADE01_NOTE = (
    "TRADE-01 AUTHORISES the replacement of `ExecutionRecordRepository.claim_execution` "
    "with `ExecutionRecordRepository.claim_execution_within_capacity` in the live order "
    "path's collaborator sequence. ONE entry is re-recorded; nothing else in this file is "
    "added, removed or retyped, and the ORDER of the sequence is unchanged - the claim "
    "occupies exactly the position it did before. WHY. The only capital bound on this path "
    "was a read-then-compare in `_execute_trade_internal` "
    "(`_notional > _ceiling`, where `_ceiling` is `self.current_equity * self.max_leverage` "
    "read from PROCESS MEMORY), with nothing reserved, decremented or locked, and no "
    "`FOR UPDATE`, advisory lock or exposure table anywhere on the path. `claim_execution` "
    "guarantees that one worker executes a GIVEN execution and says nothing about two "
    "DIFFERENT ones - and `generate_execution_id` folds quantity and price into the "
    "canonical string, so two orders differing in either take different execution ids AND "
    "different Redis locks, clear the identical ceiling independently, and both proceed. "
    "Two orders at 60% of the ceiling committed 120% of it. "
    "`claim_execution_within_capacity` makes the claim and the capacity decision one "
    "transaction serialised per tenant by `pg_advisory_xact_lock`, summing admitted "
    "notional from `execution_records` instead of reading equity from memory. The "
    "reservation IS the `executing` row, so no separate reservation state can leak. The "
    "per-order ceiling is UNCHANGED and still runs. Declared simulator engines keep "
    "`claim_execution`: there is no real account whose capacity could be oversubscribed. "
    "Evidence: tests/test_trade01_concurrent_exposure_postgres.py (28 tests, real "
    "PostgreSQL, multi-connection), including "
    "`test_without_the_reservation_two_rows_both_reach_executing` which demonstrates the "
    "pre-fix defect against authoritative database state."
)


def collect_notes(node, path=()):
    found = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.startswith(NOTE_PREFIX):
                found[path + (key,)] = value
            else:
                found.update(collect_notes(value, path + (key,)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.update(collect_notes(value, path + (index,)))
    return found


def place(root, path, value):
    node = root
    for step in path[:-1]:
        if isinstance(node, dict):
            if step not in node:
                return False
            node = node[step]
        elif isinstance(node, list):
            if not isinstance(step, int) or step >= len(node):
                return False
            node = node[step]
        else:
            return False
    if not isinstance(node, dict):
        return False
    node[path[-1]] = value
    return True


old = json.loads(baseline_path(NAME).read_text(encoding="utf-8"))
notes = collect_notes(old)
print(f"EXISTING_NOTES={len(notes)}")
for path in notes:
    print(f"  {'.'.join(str(p) for p in path)}")

fresh = capture(NAME)

carried, lost = 0, []
for path, value in notes.items():
    if place(fresh, path, value):
        carried += 1
    else:
        lost.append(path)

if lost:
    print("REFUSING: these existing notes have no home in the new capture:")
    for path in lost:
        print(f"  {'.'.join(str(p) for p in path)}")
    raise SystemExit(1)

print(f"NOTES_CARRIED={carried}")

fresh[TRADE01_NOTE_KEY] = TRADE01_NOTE

# Show what actually moved, so the re-record is reviewable rather than trusted.
def sequences(doc):
    return {
        s["scenario"]: [c["call"] for c in s.get("collaborator_sequence", [])]
        for s in doc.get("scenarios", [])
    }


before, after = sequences(old), sequences(fresh)
for scenario in sorted(set(before) | set(after)):
    b, a = before.get(scenario, []), after.get(scenario, [])
    if b != a:
        print(f"CHANGED {scenario}")
        print(f"  before: {b}")
        print(f"  after : {a}")
    else:
        print(f"SAME    {scenario}")

path = write_baseline(NAME, fresh)
print(f"WROTE={path}")
