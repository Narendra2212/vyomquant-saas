"""Account for every test the Phase 6 run collected that the Phase 5 run did not.

Phase 5: 13,435 collected. Phase 6: 13,534. That is +99, and Phase 6 added 94 tests in three
files -- so five are unexplained by the three new files, and "roughly" is not an account.
Skipped also moved, 363 -> 434, which needs the same treatment.

Diffs the two junit XMLs by test id, so the answer is the set of ids rather than a
subtraction. Reports which FILE each added or removed id belongs to.
"""
from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET
from collections import Counter

STAGING = pathlib.Path(__file__).resolve().parent
PHASE5 = STAGING / "fullreg_FINAL2.xml"
PHASE6 = STAGING / "fullreg_FULLREG_P6.xml"


def ids_and_outcomes(path: pathlib.Path) -> tuple[set[str], dict[str, str]]:
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    ids: set[str] = set()
    outcome: dict[str, str] = {}
    for case in suite.iter("testcase"):
        key = f"{case.get('classname')}::{case.get('name')}"
        ids.add(key)
        state = "passed"
        if case.find("failure") is not None:
            state = "failed"
        elif case.find("error") is not None:
            state = "error"
        elif case.find("skipped") is not None:
            kind = case.find("skipped").get("type", "")
            state = "xfailed" if "xfail" in kind.lower() else "skipped"
        outcome[key] = state
    return ids, outcome


def file_of(test_id: str) -> str:
    return test_id.split("::")[0].split(".")[0] if "::" in test_id else test_id


def main() -> int:
    for path in (PHASE5, PHASE6):
        if not path.exists():
            print(f"ABSENT: {path.name}")
            return 1

    old_ids, old_out = ids_and_outcomes(PHASE5)
    new_ids, new_out = ids_and_outcomes(PHASE6)

    print(f"PHASE5_IDS={len(old_ids)}")
    print(f"PHASE6_IDS={len(new_ids)}")
    print(f"DELTA={len(new_ids) - len(old_ids)}")

    added = new_ids - old_ids
    removed = old_ids - new_ids
    print(f"\nADDED={len(added)}  REMOVED={len(removed)}")

    print("\n--- ADDED, grouped by classname ---")
    for name, n in sorted(
        Counter(i.split("::")[0] for i in added).items(), key=lambda kv: (-kv[1], kv[0])
    ):
        print(f"  {n:>4}  {name}")

    if removed:
        print("\n--- REMOVED, grouped by classname ---")
        for name, n in sorted(
            Counter(i.split("::")[0] for i in removed).items(),
            key=lambda kv: (-kv[1], kv[0]),
        ):
            print(f"  {n:>4}  {name}")

    print("\n--- OUTCOME TOTALS ---")
    for label, out in (("phase5", old_out), ("phase6", new_out)):
        c = Counter(out.values())
        print(f"  {label}: {dict(sorted(c.items()))}")

    print("\n--- SKIPPED DELTA: which classnames gained skips ---")
    gained: Counter[str] = Counter()
    for key, state in new_out.items():
        if state != "skipped":
            continue
        if old_out.get(key) != "skipped":
            gained[key.split("::")[0]] += 1
    for name, n in sorted(gained.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  +{n:<4} {name}")
    print(f"  TOTAL_NEWLY_SKIPPED={sum(gained.values())}")

    lost: Counter[str] = Counter()
    for key, state in old_out.items():
        if state == "skipped" and new_out.get(key) not in (None, "skipped"):
            lost[key.split("::")[0]] += 1
    if lost:
        print("--- no longer skipped ---")
        for name, n in sorted(lost.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  -{n:<4} {name}")
        print(f"  TOTAL_NO_LONGER_SKIPPED={sum(lost.values())}")

    print("\n--- FAILED in phase 6 ---")
    for key, state in sorted(new_out.items()):
        if state in ("failed", "error"):
            was = old_out.get(key, "<absent in phase 5>")
            print(f"  {key}\n      phase5={was}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
