"""Phase 11B-W1D-13. READ-ONLY. The W1C-02 fix on REAL captured data.

Takes one real policy out of staging/provisioning_snapshot.json, changes one attribute IN
MEMORY ONLY, and shows what the drift report says now. The file on disk is never written.

Also confirms the three functions this task was forbidden to change behave as before:
`_norm`, `_as_tuples`, and `diff_snapshots`.
"""

from __future__ import annotations

import copy
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ.setdefault("PGHOST", "255.255.255.255")
os.environ.setdefault("PGCONNECT_TIMEOUT", "1")

REPO = r"c:\aerora_quant_backend_updated_final1"
sys.path.insert(0, REPO)

from scripts.verify_provisioning import _norm, diff_snapshots  # noqa: E402
from scripts.verify_schema_drift import _as_tuples, compare  # noqa: E402

SNAP = os.path.join(REPO, "staging", "provisioning_snapshot.json")


def main() -> int:
    before = os.path.getmtime(SNAP), os.path.getsize(SNAP)
    baseline = json.loads(open(SNAP, encoding="utf-8").read())
    print(f"snapshot: {before[1]:,} B   policies rows: {len(baseline.get('policies', []))}")
    print()

    live = copy.deepcopy(baseline)
    target = live["policies"][0]
    original = list(target)
    print("=== THE REAL ROW BEING PERTURBED (in memory only) ===")
    print(f"  table  : {target[0]}")
    print(f"  policy : {target[1]}")
    print(f"  col[6] : {target[6]!r}")
    target[6] = "(W1D13_SENTINEL)"
    print(f"  col[6] -> {target[6]!r}")
    print()

    failures, advisories = compare(baseline, live)
    print("=== THE DRIFT REPORT, AS IT NOW READS ===")
    for l in failures:
        print(f"  {l}")
    print()
    print("=== WHAT THE PREVIOUS IMPLEMENTATION WOULD HAVE SAID ===")
    exp = _as_tuples(baseline["policies"])
    act = _as_tuples(live["policies"])
    old_missing = [r for r in exp if r not in act]
    old_extra = [r for r in act if r not in exp]
    print(f"  policies: {len(old_missing)} missing, {len(old_extra)} unexpected")
    print(f"      MISSING from the database : {old_missing[0] if old_missing else None}")
    print(f"      NOT IN this repository    : {old_extra[0] if old_extra else None}")
    print("  -> the same policy, asserted both absent from the database AND foreign to")
    print("     the repository. One object, two contradictory claims, nothing linking them.")
    print()

    print("=== UNTOUCHED HELPERS STILL BEHAVE AS BEFORE ===")
    print(f"  _norm(None)        -> {_norm(None)!r}")
    print(f"  _norm('')          -> {_norm('')!r}")
    print(f"  _norm(False)       -> {_norm(False)!r}")
    print(f"  _norm([1,2])       -> {_norm([1, 2])!r}")
    print(f"  _as_tuples([[1,2]])-> {_as_tuples([[1, 2]])!r}")
    same = diff_snapshots(baseline, baseline)
    drifted = diff_snapshots(baseline, live)
    print(f"  diff_snapshots(x,x)    -> {len(same)} problem(s) (expect 0)")
    print(f"  diff_snapshots(x,y)    -> {len(drifted)} line(s); first: "
          f"{drifted[0] if drifted else None}")
    print("  (diff_snapshots was NOT routed through the classifier -- that is the second,")
    print("   separately-approved change, and D5's sign-off is still outstanding.)")
    print()

    after = os.path.getmtime(SNAP), os.path.getsize(SNAP)
    print("=== THE SNAPSHOT FILE WAS NOT WRITTEN ===")
    print(f"  mtime/size before : {before}")
    print(f"  mtime/size after  : {after}")
    print(f"  identical         : {before == after}")
    print(f"  col[6] on disk still: "
          f"{json.loads(open(SNAP, encoding='utf-8').read())['policies'][0][6]!r}")
    print(f"  (original value was {original[6]!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
