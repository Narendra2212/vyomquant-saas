"""Phase 11B-W1D-06. READ-ONLY PROBE. Demonstrate the proposed correction.

Nothing here edits production code. The proposed classifier is implemented in THIS file and
run side by side with the real `scripts.verify_schema_drift.compare()` on identical inputs,
so the report can show measured before/after behaviour rather than a described intention.

Three properties the proposal must have:
  1. an attribute change on an object present on both sides is reported as MODIFIED (W1C-02);
  2. a multiplicity or ordering difference can never produce "0 missing, 0 unexpected";
  3. the aggregate pass/fail verdict is unchanged for every case that is genuinely drift,
     so adopting it cannot turn a currently-failing run green.

Property 3 is the compatibility check that decides whether this is safe to land.
"""

from __future__ import annotations

import collections
import os
import pathlib
import sys

os.environ.setdefault("PGHOST", "255.255.255.255")
os.environ.setdefault("PGPORT", "1")
os.environ.setdefault("PGCONNECT_TIMEOUT", "1")

ROOT = pathlib.Path(r"c:\aerora_quant_backend_updated_final1")
sys.path.insert(0, str(ROOT))

from scripts.verify_schema_drift import ADVISORY_DIMENSIONS, compare, _as_tuples  # noqa: E402
from scripts.verify_provisioning import SNAPSHOT_QUERIES  # noqa: E402

WHOLE_ROW = "whole-row"

#: PROPOSED: this declaration moves next to SNAPSHOT_QUERIES in verify_provisioning.py.
#: Reproduced here only because this phase may not edit production code. Widths are the
#: ones W1D-05's guard already enforces against each query's ORDER BY.
COMPARISON_KEY: dict[str, object] = {
    "alembic_version": WHOLE_ROW,
    "columns": 2,
    "enum_types": 1,
    "extensions": WHOLE_ROW,
    "foreign_keys": 2,
    "functions": 2,
    "indexes": 2,
    "policies": 2,
    "primary_keys": 2,
    "privileges": WHOLE_ROW,
    "recorded_migrations": 1,
    "row_level_security": 1,
    "sequences": WHOLE_ROW,
    "tables": 1,
    "triggers": 2,
    "unique_and_check_constraints": 2,
}


def classify(dimension: str, expected: list, actual: list) -> dict:
    """PROPOSED replacement for the per-dimension diff inside compare().

    Keyed and multiset-aware. For a whole-row dimension the key IS the row, so `modified`
    is meaningless and the result degenerates to a multiset difference -- which is the
    correct behaviour there, not a special case bolted on.
    """
    expected = _as_tuples(expected)
    actual = _as_tuples(actual)
    width = COMPARISON_KEY.get(dimension)

    if width == WHOLE_ROW or width is None:
        # Multiset difference. Counter, not `not in`, so multiplicity is honoured.
        ce, ca = collections.Counter(expected), collections.Counter(actual)
        missing = list((ce - ca).elements())
        added = list((ca - ce).elements())
        return {"missing": missing, "added": added, "modified": [], "unchanged": len(ce & ca)}

    def grouped(rows):
        out: dict[tuple, collections.Counter] = {}
        for r in rows:
            out.setdefault(tuple(r[:width]), collections.Counter())[tuple(r[width:])] += 1
        return out

    ge, ga = grouped(expected), grouped(actual)
    missing, added, modified, unchanged = [], [], [], 0

    for key in sorted(set(ge) | set(ga)):
        if key in ge and key not in ga:
            missing += [key + a for a, n in ge[key].items() for _ in range(n)]
        elif key in ga and key not in ge:
            added += [key + a for a, n in ga[key].items() for _ in range(n)]
        else:
            same = ge[key] & ga[key]
            lost = ge[key] - ga[key]
            gained = ga[key] - ge[key]
            unchanged += sum(same.values())
            if lost or gained:
                modified.append({
                    "key": key,
                    "was": list(lost.elements()),
                    "now": list(gained.elements()),
                })
    return {"missing": missing, "added": added, "modified": modified, "unchanged": unchanged}


def render(dimension: str, c: dict) -> list[str]:
    """PROPOSED report lines. Shows a modified object ONCE, with its deltas."""
    if not (c["missing"] or c["added"] or c["modified"]):
        return []
    lines = [
        f"{dimension}: {len(c['modified'])} modified, {len(c['missing'])} missing, "
        f"{len(c['added'])} added, {c['unchanged']} unchanged"
    ]
    for m in c["modified"][:12]:
        lines.append(f"    MODIFIED {m['key']}")
        for was, now in zip(m["was"], m["now"]):
            for i, (a, b) in enumerate(zip(was, now)):
                if a != b:
                    lines.append(f"        attr[{i}]: {a!r} -> {b!r}")
    for r in c["missing"][:12]:
        lines.append(f"    MISSING from the database : {r}")
    for r in c["added"][:12]:
        lines.append(f"    NOT IN this repository    : {r}")
    return lines


def today(dimension: str, expected: list, actual: list):
    base = {k: [] for k in SNAPSHOT_QUERIES}
    live = {k: [] for k in SNAPSHOT_QUERIES}
    base[dimension] = list(expected)
    live[dimension] = list(actual)
    f, a = compare(base, live)
    return (a if dimension in ADVISORY_DIMENSIONS else f)


POL_OLD = ("fee_records", "fee_select", "PERMISSIVE", "{authenticated}", "SELECT", "(uid = x)", "")
POL_NEW = ("fee_records", "fee_select", "PERMISSIVE", "{authenticated}", "SELECT", "(uid = x)", "(a = b)")
C1 = ("t1", "c1", "integer")
C2 = ("t2", "c2", "text")

CASES = [
    ("W1C-02: attribute modified, object on both sides", "policies", [POL_OLD], [POL_NEW], True),
    ("reordered rows, identical content", "columns", [C1, C2], [C2, C1], False),
    ("duplicate multiplicity differs", "columns", [C1, C1], [C1], True),
    ("genuine removal", "policies", [POL_OLD], [], True),
    ("genuine addition", "policies", [], [POL_NEW], True),
    ("unchanged", "policies", [POL_OLD], [POL_OLD], False),
    ("both empty", "policies", [], [], False),
    ("whole-row dimension changed", "privileges",
     [("fee_records", "authenticated", "SELECT")], [("fee_records", "authenticated", "UPDATE")], True),
    ("advisory dimension modified", "recorded_migrations",
     [("001_init.sql", "abc123", "2026-01-01")], [("001_init.sql", "def456", "2026-01-01")], True),
    ("null attribute vs empty string", "columns",
     [("t", "c", None)], [("t", "c", "")], True),
    ("attribute becomes null", "columns",
     [("t", "c", "integer")], [("t", "c", None)], True),
]


def main() -> int:
    print("=" * 78)
    print("PROPOSED CLASSIFICATION vs TODAY -- measured, same inputs")
    print("=" * 78)
    compat_ok = True
    for title, dim, exp, act, should_flag in CASES:
        t = today(dim, exp, act)
        c = classify(dim, exp, act)
        p = render(dim, c)
        print("-" * 78)
        print(f"  {title}   [dimension={dim}, key={COMPARISON_KEY.get(dim)}]")
        print(f"    TODAY    : {t[0] if t else '(no drift reported)'}")
        for l in t[1:]:
            print(f"               {l.strip()}")
        print(f"    PROPOSED : {p[0] if p else '(no drift reported)'}")
        for l in p[1:]:
            print(f"               {l.strip()}")

        # Property 3: the verdict (drift / no drift) must not change.
        flagged_today, flagged_prop = bool(t), bool(p)
        verdict = "SAME" if flagged_today == flagged_prop else "*** VERDICT CHANGED ***"
        if flagged_today != flagged_prop:
            compat_ok = False
        print(f"    verdict  : today={'DRIFT' if flagged_today else 'clean'}  "
              f"proposed={'DRIFT' if flagged_prop else 'clean'}  {verdict}")
        if flagged_prop != should_flag:
            print(f"    NOTE     : expected flag={should_flag}, proposed gave {flagged_prop}")

        # Property 2: no empty-handed drift report.
        if flagged_prop and len(p) == 1 and c["unchanged"] and not (
            c["missing"] or c["added"] or c["modified"]
        ):
            print("    *** PROPOSED produced an empty-handed report -- defect not fixed ***")
            compat_ok = False
    print("=" * 78)
    print("PROPERTY CHECKS")
    print("=" * 78)
    print(f"  P1  W1C-02 case reported as MODIFIED : "
          f"{'YES' if 'modified' in render('policies', classify('policies', [POL_OLD], [POL_NEW]))[0] else 'NO'}")
    r2 = render("columns", classify("columns", [C1, C2], [C2, C1]))
    print(f"  P2a reorder no longer drift          : {'YES' if not r2 else 'NO -> ' + r2[0]}")
    r3 = render("columns", classify("columns", [C1, C1], [C1]))
    print(f"  P2b multiplicity reported with rows  : "
          f"{'YES' if r3 and ('missing' in r3[0]) and len(r3) > 1 else 'NO'}")
    if r3:
        for l in r3:
            print(f"        {l}")
    print(f"  P3  no verdict regressions           : {'YES' if compat_ok else 'NO'}")
    print()
    print("  NOTE on P2a: today a reorder is a HARD FAILURE naming nothing. The proposal")
    print("  treats it as clean, which is correct -- identical content in a different order")
    print("  is not drift. W1D-05's Guard A independently keeps today's 16 queries from")
    print("  reaching that path, so this changes no current behaviour; it removes a trap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
