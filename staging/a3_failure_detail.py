"""Prints the FAILURES section of a pytest log, excluding the warnings summary.

The shell wrapper on this host cannot reliably slice a log around a match, and `-rs`
output interleaves a long PydanticDeprecated warnings block that drowns the assertion.

Read-only. Usage: python staging/a3_failure_detail.py <logfile>
"""

from __future__ import annotations

import sys


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "staging/a3_local_api.log"
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = [line.rstrip("\n") for line in handle]

    inside = False
    printed = 0
    for line in lines:
        if "FAILURES" in line and set(line.strip()) <= set("=_ FAILURES"):
            inside = True
            print(line)
            continue
        # The warnings summary terminates the failures block.
        if inside and ("warnings summary" in line or "short test summary" in line):
            break
        if inside:
            print(line)
            printed += 1
            if printed > 120:
                print("... (truncated at 120 lines)")
                break

    if printed == 0:
        print("(no FAILURES section found)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
