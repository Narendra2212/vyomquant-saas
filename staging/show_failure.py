"""Print the assertion / exception block of the first failure in a pytest log.

Scans for the line pytest prints when an assertion fails (`E   `) and shows a window
around it, which is the part that actually says what went wrong. Written because the
surrounding docstrings in this codebase are long enough that a fixed -Tail window shows
prose instead of the error.

    python staging/show_failure.py <logfile> [occurrence]
"""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    path = sys.argv[1]
    which = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("E   ") or line.startswith("E\t") or line == "E":
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    if not blocks:
        print("no 'E   ' assertion block found in this log")
        return 1

    if which >= len(blocks):
        print(f"only {len(blocks)} block(s) in this log")
        return 1

    print(f"[block {which + 1} of {len(blocks)}]")
    for line in blocks[which][:40]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
