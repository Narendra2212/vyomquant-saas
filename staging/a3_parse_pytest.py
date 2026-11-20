"""Per-test outcome table from a pytest -v log.

The PowerShell wrapper on this host mangles backslash-heavy regexes and `$_` pipelines, so
the per-test accounting the brief requires is done here instead of in the shell.

Read-only. Usage: python staging/a3_parse_pytest.py <logfile>
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict

OUTCOME = re.compile(
    r"^(?P<file>tests[\\/][^\s:]+)::(?P<test>[^\s]+)\s+(?P<result>PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)"
)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "staging/a3_local_quota.log"
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()

    per_file: dict[str, Counter] = defaultdict(Counter)
    non_pass: list[tuple[str, str, str]] = []

    for line in lines:
        m = OUTCOME.match(line.strip())
        if not m:
            continue
        f = m.group("file").replace("\\", "/")
        per_file[f][m.group("result")] += 1
        if m.group("result") != "PASSED":
            non_pass.append((f, m.group("test"), m.group("result")))

    total = Counter()
    print(f"{'file':<62} {'PASS':>5} {'SKIP':>5} {'FAIL':>5} {'ERR':>5}")
    print("-" * 86)
    for f in sorted(per_file):
        c = per_file[f]
        total.update(c)
        print(
            f"{f:<62} {c['PASSED']:>5} {c['SKIPPED']:>5} {c['FAILED']:>5} {c['ERROR']:>5}"
        )
    print("-" * 86)
    print(
        f"{'TOTAL':<62} {total['PASSED']:>5} {total['SKIPPED']:>5} "
        f"{total['FAILED']:>5} {total['ERROR']:>5}"
    )

    if non_pass:
        print("\nNon-passing tests, by name:")
        for f, test, result in non_pass:
            print(f"  {result:<8} {f}::{test}")
    else:
        print("\nEvery collected test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
