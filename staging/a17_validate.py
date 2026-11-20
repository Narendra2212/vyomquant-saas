"""Phase 11B-A17. READ-ONLY. Validate the report against the brief's output contract."""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = r"c:\aerora_quant_backend_updated_final1"
PATH = os.path.join(REPO, "staging", "PHASE11B_A17_D5_MANIFEST_RECONCILIATION.md")

SECTIONS = [
    ("1 entry/exit preservation invariants", "Entry and exit preservation invariants"),
    ("2 report discrepancy matrix", "Report discrepancy matrix"),
    ("3 current PR and CI state", "Current PR"),
    ("4 recomputed three-file manifest", "Independently recomputed three-file manifest"),
    ("5 patch applicability", "Patch applicability"),
    ("6 exchange-slot ownership/boundary", "Exchange-slot ownership and boundary"),
    ("7 approval matrix", "Approval matrix"),
    ("8 smallest next decision + unauthorized", "smallest next human decision"),
]

MUST = [
    "967b4a28e29157f97f75d31a48eb9ce5b4133ce5",
    "6f40856cee2bc7ea4d741f69f4b1f68b463d70a4",
    "QUOTA-01 | **OPEN**",
    "TENANT-01 | **OPEN**",
    "UNPROVEN",
    "UNEXECUTED",
    "OFF / NOT CERTIFIED",
    "BLOCKED pending approvals",
    "CONCURRENT CHANGE DETECTED",
    "VERIFIED",
    "PROPOSED",
    "UNRESOLVED",
]

FORBIDDEN = ["a2f39ebb is correct", "approval granted", "QUOTA-01 CLOSED", "live trading certified"]


def run(args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True, errors="replace")


def main() -> int:
    text = open(PATH, encoding="utf-8").read()
    print(f"bytes={os.path.getsize(PATH)}  lines={len(text.splitlines())}")
    tops = re.findall(r"^## (\d+)\.", text, re.M)
    print(f"sections={tops}  ascending={tops == sorted(tops, key=int)}")
    print()
    print("--- required sections ---")
    miss = []
    for label, needle in SECTIONS:
        ok = needle.lower() in text.lower()
        miss += [] if ok else [label]
        print(f"  {'OK  ' if ok else 'MISS'}  {label}")
    print()
    print("--- required content ---")
    absent = []
    for m in MUST:
        ok = m in text
        absent += [] if ok else [m]
        print(f"  {'OK  ' if ok else 'MISS'}  {m[:58]}")
    print()
    print("--- forbidden claims ---")
    bad = [f for f in FORBIDDEN if f.lower() in text.lower()]
    for f in FORBIDDEN:
        print(f"  {'VIOLATION' if f.lower() in text.lower() else 'clear    '}  {f}")
    print()
    print("--- live invariants at validation time ---")
    ls = run(["git", "ls-files", "-s"])
    entries = [l for l in ls.stdout.splitlines() if l.strip()]
    a = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    di = run(["git", "diff-index", "--cached", "HEAD"])
    rows = [l for l in di.stdout.splitlines() if l.strip()]
    c = hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()
    print(f"  HEAD         {run(['git','rev-parse','HEAD']).stdout.strip()}")
    print(f"  digest A     {a}  expected 98ae5bdf...  {'OK' if a.startswith('98ae5bdf') else 'DRIFT'}")
    print(f"  digest C     {c}  expected 8d9c475b...  {'OK' if c.startswith('8d9c475b') else 'DRIFT'}")
    print(f"  staged       {len(rows)}  expected 22  {'OK' if len(rows) == 22 else 'DRIFT'}")
    slot = run(["git", "hash-object", "tests/test_quota01_exchange_slot_is_atomic.py"]).stdout.strip()
    print(f"  slot test    {slot}  {'OK unchanged' if slot.startswith('2d7ccbbb') else 'CHANGED'}")
    fail = bool(miss or absent or bad) or tops != sorted(tops, key=int)
    print()
    print("VALIDATION:", "FAIL" if fail else "PASS")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
