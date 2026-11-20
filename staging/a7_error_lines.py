"""Groups the distinct `E ...` assertion lines in a GitHub Actions pytest log.

READ-ONLY. Usage: python staging/a7_error_lines.py [substring-filter]

The log is one GitHub Actions line per row, prefixed with
"<job>\t<step>\t<timestamp> ", so the pytest text is the last tab-separated field with a
leading ISO timestamp to strip.
"""

from __future__ import annotations

import collections
import re
import sys

LOG = "staging/a7_unit_log.txt"
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")


def main() -> int:
    needle = sys.argv[1] if len(sys.argv) > 1 else ""

    counts: collections.Counter[str] = collections.Counter()
    # Which failing test each E-line belongs to: track the most recent FAILED header.
    current = "?"
    per_test: dict[str, list[str]] = collections.defaultdict(list)

    with open(LOG, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            body = TS.sub("", raw.split("\t")[-1]).rstrip()
            stripped = body.strip()

            header = re.match(r"_{3,}\s*(.+?)\s*_{3,}$", stripped)
            if header:
                current = header.group(1)
                continue

            if stripped.startswith("E ") and len(stripped) > 3:
                text = stripped[2:].strip()[:200]
                counts[text] += 1
                per_test[current].append(text)

    print(f"distinct E-lines: {len(counts)}\n")
    print("=== MOST COMMON ===")
    for text, n in counts.most_common(15):
        print(f"{n:>4}  {text}")
    print()

    if needle:
        print(f"=== FAILING TESTS MATCHING {needle!r} ===")
        shown = 0
        for test, errs in per_test.items():
            if needle.lower() not in test.lower():
                continue
            print(f"  {test}")
            for e in errs[:2]:
                print(f"      E {e}")
            shown += 1
            if shown >= 12:
                print("  ... truncated")
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
