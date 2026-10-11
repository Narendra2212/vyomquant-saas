"""Phase 11B-A9. READ-ONLY. Confirm the A9 report contains every section the brief requires."""

from __future__ import annotations

import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PATH = r"c:\aerora_quant_backend_updated_final1\staging\PHASE11B_A9_D5_CONSOLIDATION_REPORT.md"

# The ten items the brief's Phase 5 enumerates.
REQUIRED = [
    ("1  preflight baseline + diffs from A8", "Preflight baseline"),
    ("2  canonical PR decision / blocked", "Canonical PR"),
    ("3  exact files changed and why", "Exact files changed"),
    ("4  before/after Redis db + keyspace", "Redis database and keyspace"),
    ("5  production-path quota test results", "Production-path quota test"),
    ("6  test commands, results, skips, CI", "Focused tests, skips, and CI evidence"),
    ("7  remaining baseline failures + risks", "Remaining baseline failures and unresolved risks"),
    ("8  fingerprint, staged paths, HEAD, reflog", "Staged-content fingerprint"),
    ("9  mutations performed + authorization", "Mutations performed"),
    ("10 QUOTA-01 + live-trading status", "Final status"),
]

# Completion rules the brief forbids violating.
FORBIDDEN_CLAIMS = [
    "QUOTA-01 is closed",
    "QUOTA-01: CLOSED",
    "live trading certified",
    "LIVE TRADING CERTIFIED",
]

MUST_STATE = [
    "QUOTA-01 **OPEN**",
    "OFF / NOT CERTIFIED",
    "UNEXECUTED",
]


def main() -> int:
    text = open(PATH, encoding="utf-8").read()
    print(f"bytes={os.path.getsize(PATH)}  lines={len(text.splitlines())}")
    tops = re.findall(r"^## (\d+)\.", text, re.M)
    print(f"sections={tops}  ascending={tops == sorted(tops, key=int)}")
    print()

    print("--- required sections ---")
    missing = []
    for label, needle in REQUIRED:
        ok = needle.lower() in text.lower()
        if not ok:
            missing.append(label)
        print(f"  {'OK  ' if ok else 'MISS'}  {label}")
    print()

    print("--- forbidden claims (must be absent) ---")
    bad = []
    for claim in FORBIDDEN_CLAIMS:
        present = claim.lower() in text.lower()
        if present:
            bad.append(claim)
        print(f"  {'VIOLATION' if present else 'clear   '}  {claim}")
    print()

    print("--- required statements (must be present) ---")
    absent = []
    for s in MUST_STATE:
        present = s in text
        if not present:
            absent.append(s)
        print(f"  {'OK  ' if present else 'MISS'}  {s}")
    print()

    fail = bool(missing or bad or absent) or tops != sorted(tops, key=int)
    print("VALIDATION:", "FAIL" if fail else "PASS")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
