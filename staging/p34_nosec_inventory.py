"""Per-directive `# nosec` inventory at the CI scan scope. Read-only.

Phase 33 counted 56 directives in 17 files within `bandit -r backend_app`, of which 54 are
BARE (no rule id, so they suppress every Bandit check on their line). Counting is not a
disposition. Brief §4.1 asks for the per-directive record: path, line, exact directive, bare
vs scoped, the source operation it sits on, whether a rationale is present nearby, and whether
the file is the one the gate test scans.

This produces that inventory so a security owner can triage it, rather than re-deriving the
same totals. It changes nothing and suppresses nothing.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCOPE = ROOT / "backend_app"          # exactly the CI scan path: bandit -r backend_app
GATED = SCOPE / "backend" / "transactional_execution_manager.py"

NOSEC = re.compile(r"#\s*nosec(?P<ids>\s+[A-Z][A-Z0-9,\s]*)?")

#: Heuristic classification of the operation the suppressed line performs. Reported as a hint
#: for triage, never as a security conclusion -- a human must read the line.
OPS = [
    (re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b", re.I), "SQL string"),
    (re.compile(r"\b(execute|executescript|exec_driver_sql|text)\s*\("), "SQL execution"),
    (re.compile(r"\bsubprocess\b|\bPopen\b|\bos\.system\b"), "subprocess"),
    (re.compile(r"\brandom\b"), "random"),
    (re.compile(r"\b(md5|sha1)\b", re.I), "weak hash"),
    (re.compile(r"\betree\b|\bminidom\b|\bExpatError\b|xml\."), "XML parsing"),
    (re.compile(r"0\.0\.0\.0"), "bind all interfaces"),
    (re.compile(r"\bpickle\b|\byaml\.load\b|\beval\b|\bexec\b"), "deserialization/eval"),
    (re.compile(r"\bassert\b"), "assert"),
    (re.compile(r"\bverify\s*=\s*False\b|\bssl\b", re.I), "TLS/verify"),
]


def classify(line: str) -> str:
    for rx, label in OPS:
        if rx.search(line):
            return label
    return "(unclassified)"


def main() -> int:
    rows = []
    for path in sorted(SCOPE.rglob("*.py")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            m = NOSEC.search(line)
            if not m:
                continue
            ids = (m.group("ids") or "").strip()
            # A rationale is "present" if the suppressed line or either neighbour carries a
            # comment beyond the bare directive itself.
            context = lines[max(0, i - 3): i]
            rationale = any(
                c.strip().startswith("#") and "nosec" not in c for c in context
            )
            rows.append({
                "path": path.relative_to(ROOT).as_posix(),
                "line": i,
                "ids": ids or "BARE",
                "bare": not ids,
                "op": classify(line),
                "rationale": rationale,
                "gated_file": path == GATED,
            })

    bare = [r for r in rows if r["bare"]]
    scoped = [r for r in rows if not r["bare"]]

    print("=" * 108)
    print("PER-DIRECTIVE `# nosec` INVENTORY — CI SCAN SCOPE (bandit -r backend_app)")
    print("=" * 108)
    print(f"  total directives : {len(rows)}")
    print(f"  files            : {len({r['path'] for r in rows})}")
    print(f"  BARE (suppress all checks on the line) : {len(bare)}")
    print(f"  rule-scoped                            : {len(scoped)}")
    print(f"  with a nearby rationale comment        : {sum(1 for r in rows if r['rationale'])}")
    print(f"  without any nearby rationale           : {sum(1 for r in rows if not r['rationale'])}")
    print(f"  inside the gate test's only scanned file ({GATED.name}) : "
          f"{sum(1 for r in rows if r['gated_file'])}")

    print("\n### BARE directives by suppressed operation (triage buckets)")
    buckets: dict[str, int] = {}
    for r in bare:
        buckets[r["op"]] = buckets.get(r["op"], 0) + 1
    for op, n in sorted(buckets.items(), key=lambda kv: -kv[1]):
        print(f"  {op:<28} x{n}")

    print("\n### BARE directives WITHOUT a nearby rationale — highest triage priority")
    unexplained = [r for r in bare if not r["rationale"]]
    print(f"  count: {len(unexplained)}")
    for r in unexplained:
        flag = "  <-- gate-test file" if r["gated_file"] else ""
        print(f"    {r['path']}:{r['line']:<5} op={r['op']}{flag}")

    print("\n### rule-scoped directives (already narrow)")
    for r in scoped:
        print(f"    {r['path']}:{r['line']:<5} ids={r['ids']:<12} op={r['op']}")

    print("\n### per-file totals")
    per: dict[str, list] = {}
    for r in rows:
        per.setdefault(r["path"], []).append(r)
    for p, rs in sorted(per.items(), key=lambda kv: -len(kv[1])):
        nb = sum(1 for r in rs if r["bare"])
        print(f"  {len(rs):>3} total / {nb:>3} bare   {p}")

    print("\nNOTE: `op` is a regex hint for triage, not a security conclusion. A bare directive")
    print("also suppresses Bandit checks added in FUTURE versions on that line, which is why")
    print("scoping matters independently of whether the current code is safe.")
    print("Disposition for every row below remains: UNREVIEWED (no owner acceptance on record).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
