"""Attributes the unit-test job's failures between this commit and the pre-existing baseline.

The three test FILES in the D5' commit are new, so every failure inside them is
attributable to this change. Everything else was already failing — which is consistent with
all 14 prior `01 PR Check` runs having concluded `failure`.

Also extracts the pytest summary line and the first failure reason per new file.

READ-ONLY. Usage: python staging/a7_analyse_failures.py
"""

from __future__ import annotations

import re
from collections import Counter

LOG = "staging/a7_unit_log.txt"

NEW_FILES = (
    "tests/test_quota01_metered_reservation_is_hard.py",
    "tests/test_quota01_exchange_slot_is_atomic.py",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
)


def main() -> int:
    with open(LOG, encoding="utf-8", errors="replace") as handle:
        lines = [l.rstrip("\n") for l in handle]

    print("=== PYTEST SUMMARY ===")
    for line in lines:
        body = line.split("\t")[-1]
        if re.search(r"\d+ (passed|failed)", body) and ("=====" in body or " in " in body):
            print(f"  {body.strip()[-180:]}")
    print()

    outcomes = Counter()
    per_file_fail = Counter()
    for line in lines:
        m = re.search(r"(tests/\S+\.py)::(\S+)\s+(PASSED|FAILED|SKIPPED|ERROR)", line)
        if not m:
            continue
        outcomes[m.group(3)] += 1
        if m.group(3) in ("FAILED", "ERROR"):
            per_file_fail[m.group(1)] += 1

    print("=== COLLECTED OUTCOMES (from -v lines) ===")
    for k in ("PASSED", "FAILED", "SKIPPED", "ERROR"):
        print(f"  {k:<9} {outcomes[k]}")
    print()

    print("=== FAILURES BY FILE ===")
    attributable = 0
    preexisting = 0
    for path, count in per_file_fail.most_common():
        tag = "THIS COMMIT" if path in NEW_FILES else "pre-existing"
        if path in NEW_FILES:
            attributable += count
        else:
            preexisting += count
        print(f"  {count:>4}  {tag:<13} {path}")
    print()
    print(f"  attributable to this commit : {attributable}")
    print(f"  pre-existing                : {preexisting}")
    print()

    print("=== SKIPPED REAL-REDIS TESTS (must be 0) ===")
    skipped_redis = [
        l for l in lines
        if "real_redis" in l and "SKIPPED" in l
    ]
    print(f"  {len(skipped_redis)}")
    print()

    print("=== FIRST FAILURE REASON PER NEW FILE ===")
    for path in NEW_FILES:
        stem = path.split("/")[-1].replace(".py", "")
        reason = None
        for idx, line in enumerate(lines):
            body = line.split("\t")[-1]
            if re.match(r"_{3,}.*_{3,}", body.strip()) and stem in "".join(lines[max(0, idx - 3):idx + 3]):
                pass
            if ("Error" in body or "assert" in body or "Failed:" in body) and stem in body:
                reason = body.strip()
                break
        # fall back: find the errors block for this file
        if reason is None:
            for line in lines:
                body = line.split("\t")[-1].strip()
                if body.startswith("E ") and stem in "".join(lines[:1]):
                    reason = body
                    break
        print(f"  {path}")
        print(f"      {reason[-200:] if reason else '(no single-line reason extracted; see log)'}")
    print()

    print("=== DISTINCT 'E ' ERROR LINES NEAR THE NEW FILES ===")
    seen = set()
    for idx, line in enumerate(lines):
        body = line.split("\t")[-1]
        if not body.strip().startswith("E "):
            continue
        window = "\n".join(l.split("\t")[-1] for l in lines[max(0, idx - 60):idx])
        if not any(p.split("/")[-1] in window for p in NEW_FILES):
            continue
        key = body.strip()[:160]
        if key in seen:
            continue
        seen.add(key)
        print(f"  {key}")
        if len(seen) >= 12:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
