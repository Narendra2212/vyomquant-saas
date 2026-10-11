#!/usr/bin/env python3
"""
PHASE 38 / PROTOTYPE ONLY -- do the `# nosec` directives suppress anything?

Earlier phases treated the suppression inventory as a risk surface that HIDES
findings. This measures whether that is true at either severity threshold, by
reading Bandit's own `metrics._totals.nosec` counter and its
"nosec encountered ... but no failed test" diagnostics.

Reads only. Writes nothing. Changes no directive.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def run(args: list[str]) -> tuple[int, dict, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "bandit"] + args + ["-f", "json"],
        cwd=str(REPO), capture_output=True,
    )
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    i = out.find("{")
    doc: dict = {}
    if i != -1:
        try:
            doc = json.loads(out[i:])
        except json.JSONDecodeError:
            pass
    return proc.returncode, doc, err


def count_directives() -> tuple[int, int, collections.Counter]:
    """Count real `# nosec` directives in backend_app, excluding word matches."""
    files = 0
    total = 0
    forms: collections.Counter = collections.Counter()
    pat = re.compile(r"#\s*nosec(?P<sep>\s*:\s*|\s+|\s*$)(?P<rest>[^\n]*)")
    for path in (REPO / "backend_app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = list(pat.finditer(text))
        if not hits:
            continue
        files += 1
        for h in hits:
            total += 1
            sep = h.group("sep")
            rest = h.group("rest").strip()
            if not rest:
                forms["bare (no rule id)"] += 1
            elif ":" in sep:
                forms["colon form  `# nosec: Bxxx`"] += 1
            else:
                forms["space form  `# nosec Bxxx`"] += 1
    return files, total, forms


def main() -> int:
    print("=" * 96)
    print("Directive inventory in backend_app (textual)")
    print("=" * 96)
    files, total, forms = count_directives()
    print("  files containing a directive : %d" % files)
    print("  directives                   : %d" % total)
    for form, n in forms.most_common():
        print("    %-32s %d" % (form, n))

    for label, args in (
        ("CI threshold   `-ll`  (MEDIUM and above)", ["-r", "backend_app", "-ll"]),
        ("ALL severities        (LOW and above)",    ["-r", "backend_app"]),
    ):
        print("")
        print("=" * 96)
        print(label)
        print("=" * 96)
        rc, doc, err = run(args)
        t = doc.get("metrics", {}).get("_totals", {})
        results = doc.get("results", [])
        sev = {k.split(".")[1]: v for k, v in t.items() if k.startswith("SEVERITY")}
        stale = re.findall(
            r"nosec encountered \((\S+)\), but no failed test on file (\S+)", err)
        print("  exit code                        : %d" % rc)
        print("  findings reported                : %d" % len(results))
        print("  severity totals                  : %s" % sev)
        print("  metrics._totals.nosec            : %s   <-- findings SUPPRESSED"
              % t.get("nosec"))
        print("  'nosec but no failed test' warns : %d   <-- directives that did nothing"
              % len(set(stale)))
        if results:
            print("  reported test ids                : %s"
                  % dict(collections.Counter(x["test_id"] for x in results)))

    print("")
    print("=" * 96)
    print("Reading")
    print("=" * 96)
    print("  `metrics._totals.nosec` is Bandit's count of findings it withheld because")
    print("  a directive told it to. If that number is 0 while dozens of directives")
    print("  exist, the directives are inert: they sit on lines where the rule they")
    print("  name does not fire. That is not the same as 'the code is safe' -- it means")
    print("  neither the scan nor the directive tells you anything about those lines.")
    print("")
    print("  This REFINES the earlier framing of the suppression inventory as findings")
    print("  being hidden. Measure first: a directive that suppresses nothing is a")
    print("  documentation defect and a false-confidence risk, not a concealed finding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
