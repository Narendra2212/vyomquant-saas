"""Phase 11B-A10. READ-ONLY. Validate the A10 report against the brief's output contract."""

from __future__ import annotations

import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PATH = r"c:\aerora_quant_backend_updated_final1\staging\PHASE11B_A10_D5_APPROVAL_READINESS.md"

REQUIRED_SECTIONS = [
    ("1 fresh baseline + digest method", "Fresh baseline and digest method"),
    ("2 current PR comparison", "Current PR comparison"),
    ("3 coverage + real gaps", "Existing test coverage, and gaps"),
    ("4 exact proposed file boundary", "Exact proposed file boundary"),
    ("5 four decision requests + owners", "Four decision requests, and their unresolved owners"),
    ("6 real-Redis prerequisites", "Real-Redis execution prerequisites"),
    ("7 command log + preservation", "Command / action log and preservation checks"),
    ("8 what was not changed", "What was not changed"),
]

MUST_STATE = [
    "BLOCKED pending approvals",
    "QUOTA-01 | **OPEN**",
    "UNEXECUTED",
    "UNPROVEN",
    "OFF / NOT CERTIFIED",
]

FORBIDDEN = [
    "approval has been obtained",
    "approved by the owner",
    "QUOTA-01 is closed",
    "green build",
]


def main() -> int:
    text = open(PATH, encoding="utf-8").read()
    print(f"bytes={os.path.getsize(PATH)}  lines={len(text.splitlines())}")
    tops = re.findall(r"^## (\d+)\.", text, re.M)
    print(f"sections={tops}  ascending={tops == sorted(tops, key=int)}")
    print()

    print("--- required sections ---")
    missing = []
    for label, needle in REQUIRED_SECTIONS:
        ok = needle.lower() in text.lower()
        if not ok:
            missing.append(label)
        print(f"  {'OK  ' if ok else 'MISS'}  {label}")
    print()

    print("--- required statements ---")
    absent = []
    for s in MUST_STATE:
        ok = s in text
        if not ok:
            absent.append(s)
        print(f"  {'OK  ' if ok else 'MISS'}  {s}")
    print()

    print("--- phrases that must only appear NEGATED ---")
    for s in FORBIDDEN:
        idxs = [m.start() for m in re.finditer(re.escape(s), text, re.I)]
        if not idxs:
            print(f"  absent    {s}")
            continue
        for i in idxs:
            ctx = text[max(0, i - 90) : i + len(s) + 30].replace("\n", " ")
            negated = any(
                w in ctx.lower() for w in ("not ", "no ", "never", "nor ", "neither", "is not")
            )
            print(f"  {'ok-negated' if negated else 'CHECK     '}  {s}  ...{ctx[-110:]}")
    print()

    fail = bool(missing or absent) or tops != sorted(tops, key=int)
    print("VALIDATION:", "FAIL" if fail else "PASS")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
