"""Phase 11B-A8. READ-ONLY. Structural validation of the A8 report.

Checks that every section cross-reference resolves to a heading that exists, that headings
are in ascending order, and that the report's load-bearing figures are actually present.

stdout is forced to UTF-8: the default cp1252 console encoding cannot print the minus sign
used in the diffstats, and the first version of this script died on exactly that.
"""

from __future__ import annotations

import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PATH = r"c:\aerora_quant_backend_updated_final1\staging\PHASE11B_A8_READ_ONLY_DIAGNOSTICS.md"

FIGURES = [
    "62 failed, 12029 passed",
    "+268 / \u221215",
    "+253 / \u22120",
    "ed8d2ff8",
    "redis://localhost:6379/0",
    "d5-redis-diagnostics",
    "6d965c8eafdd23eeccea41a6bff25df5d9f1ee9fc64dfa17c29c965210458613",
    "074b772ab16cece62cbfca0f2b78497be2ae56d5",
    "38046625040",
    "114197790236",
    "QUOTA-01",
    "NOT CERTIFIED",
]


def main() -> int:
    text = open(PATH, encoding="utf-8").read()
    print(f"bytes={os.path.getsize(PATH)}  lines={len(text.splitlines())}")
    print()

    tops = re.findall(r"^## (\d+)\.", text, re.M)
    print(f"top-level section order: {tops}")
    ordered = tops == sorted(tops, key=int)
    print(f"ascending: {ordered}")
    print()

    present: set[str] = set()
    for num in re.findall(r"^#{2,3} (\d+(?:\.\d+)?)[. ]", text, re.M):
        present.add(num)
        present.add(num.split(".")[0])

    refs = sorted(set(re.findall(r"\u00a7(\d+(?:\.\d+)?)", text)))
    dangling = [r for r in refs if r not in present]
    print(f"refs used    : {refs}")
    print(f"sections     : {sorted(present, key=lambda s: [int(p) for p in s.split('.')])}")
    print(f"dangling refs: {dangling or 'none'}")
    print()

    print("--- load-bearing figures ---")
    missing = []
    for needle in FIGURES:
        ok = needle in text
        if not ok:
            missing.append(needle)
        print(f"  {'OK ' if ok else 'MISS'}  {needle[:66]}")

    print()
    bad = bool(dangling) or not ordered or bool(missing)
    print("VALIDATION:", "FAIL" if bad else "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
