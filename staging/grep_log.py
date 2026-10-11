"""Print the lines following a marker in a log file.

A helper because long PowerShell one-liners in this workspace echo their own command
text back character by character and then time out, which kills the PostgreSQL process
tree started from the same shell.

    python staging/grep_log.py <logfile> <marker> [lines-after] [filter-substring]
"""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    path, marker = sys.argv[1], sys.argv[2]
    after = int(sys.argv[3]) if len(sys.argv) > 3 else 40
    needle = sys.argv[4] if len(sys.argv) > 4 else None

    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    hits = [i for i, l in enumerate(lines) if marker in l]
    if not hits:
        print(f"marker not found: {marker!r}")
        return 1

    for start in hits[:1]:
        window = lines[start: start + after]
        for line in window:
            if needle is None or needle in line:
                print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
