"""Prints the log region around a named failing test. READ-ONLY.

Usage: python staging/a7_show_block.py <test-name-substring> [lines-after]
"""

from __future__ import annotations

import re
import sys

LOG = "staging/a7_unit_log.txt"
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")


def main() -> int:
    needle = sys.argv[1]
    after = int(sys.argv[2]) if len(sys.argv) > 2 else 40

    with open(LOG, encoding="utf-8", errors="replace") as handle:
        bodies = [TS.sub("", l.split("\t")[-1]).rstrip() for l in handle]

    # Prefer the FAILURES-section header (underscored) over the progress line.
    for idx, body in enumerate(bodies):
        if re.match(r"_{3,}.*_{3,}$", body.strip()) and needle in body:
            print(f"--- found header at log line {idx + 1} ---")
            for line in bodies[idx: idx + after]:
                print(line[:220])
            return 0

    # Fall back to any occurrence that is not the -v progress line.
    for idx, body in enumerate(bodies):
        if needle in body and "PASSED" not in body and "FAILED [" not in body:
            print(f"--- found mention at log line {idx + 1} ---")
            for line in bodies[idx: idx + after]:
                print(line[:220])
            return 0

    print(f"(no block found for {needle!r})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
