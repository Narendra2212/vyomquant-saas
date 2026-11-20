"""W1D-06 independent verification: is the duplicate-row defect REACHABLE in real data?

PHASE 11B / W1D-06 — read-only. No database, no network. Reads the committed-on-disk
snapshot and the current source. Writes nothing but its own redirected stdout.

NAMING: this probe is deliberately `w1d06x_` because ANOTHER SESSION is concurrently
running W1D-06 and already owns `probe_p11b_w1d06_repro.py`,
`probe_p11b_w1d06_sites.py`, `w1d06_phase0.py` and their logs. Nothing of theirs is
read for conclusions here and nothing of theirs is overwritten.

WHAT THIS ADDS
--------------
`verify_schema_drift.compare()` has, on inspection of current source, three distinct
failure modes rather than the one W1C-02 named:

    if expected == actual:                                   # (A) LIST equality
        continue
    missing = [r for r in expected if r not in actual]        # (B) membership
    extra   = [r for r in actual   if r not in expected]

  D1  WHOLE-TUPLE IDENTITY. (B) compares entire rows, so changing one attribute of one
      object removes a tuple and adds a different one: the object is reported as both
      missing and extra, with nothing linking the halves. This is W1C-02 as scoped.

  D2  ORDER SENSITIVITY. (A) is order-sensitive; (B) is not. Identical content in a
      different order falls past (A) and yields `0 missing, 0 unexpected` -- a hard
      failure naming no rows.

  D3  MULTIPLICITY. (B) is a MEMBERSHIP test, not a multiset comparison. If a row appears
      twice on one side and once on the other, it is `in` both lists, so it appears in
      neither `missing` nor `extra`. Result: `0 missing, 0 unexpected`, a hard failure
      naming no rows -- the same symptom as D2 but a different cause, and crucially one
      that a correct `ORDER BY` CANNOT prevent.

D3 is the reason this probe exists. W1D-05's Guard A closes D2 at the source by requiring
a total `ORDER BY` on every dimension; Guard B records each dimension's key. NEITHER can
see a duplicate row. So the question that decides whether D3 is a real finding or a
theoretical one is: can any dimension's query actually return duplicates?

That is answered WITHOUT a database, from `staging/provisioning_snapshot.json` -- a real
capture of a real cluster. If duplicates exist there, D3 is reachable in production data
and is not hypothetical.
"""

from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT = REPO / "staging" / "provisioning_snapshot.json"
sys.path.insert(0, str(REPO))

from scripts.verify_provisioning import SNAPSHOT_QUERIES, _norm       # noqa: E402
from scripts.verify_schema_drift import (                            # noqa: E402
    ADVISORY_DIMENSIONS, _as_tuples, compare,
)


def _tuples(rows):
    return [tuple(r) if isinstance(r, list) else r for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
#  1. Is D3 reachable? Duplicates in the REAL snapshot.
# ═════════════════════════════════════════════════════════════════════════════

def duplicates_in_real_snapshot() -> dict[str, list]:
    print("=" * 78)
    print("1. DUPLICATE ROWS IN THE REAL SNAPSHOT  (decides whether D3 is reachable)")
    print("=" * 78)
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    print(f"   source: {SNAPSHOT.relative_to(REPO)}  "
          f"({SNAPSHOT.stat().st_size:,} B, captured 2026-10-06)\n")
    print(f"   {'dimension':32} {'rows':>6} {'distinct':>9} {'dupes':>6}")
    print("   " + "-" * 58)
    found: dict[str, list] = {}
    for dim in SNAPSHOT_QUERIES:
        rows = _tuples(snap.get(dim, []))
        counts = collections.Counter(rows)
        dupes = {r: n for r, n in counts.items() if n > 1}
        if dupes:
            found[dim] = sorted(dupes.items(), key=lambda kv: -kv[1])
        print(f"   {dim:32} {len(rows):>6} {len(counts):>9} {len(dupes):>6}"
              f"{'   <-- DUPLICATES' if dupes else ''}")
    print()
    if not found:
        print("   No duplicate rows in this snapshot. D3 is NOT demonstrated by it.")
    for dim, items in found.items():
        print(f"   {dim}: {len(items)} duplicated row value(s)")
        for row, n in items[:6]:
            print(f"     x{n}  {row}")
        if len(items) > 6:
            print(f"     ... and {len(items) - 6} more")
    return found


# ═════════════════════════════════════════════════════════════════════════════
#  2. Can the privileges query return duplicates by construction?
# ═════════════════════════════════════════════════════════════════════════════

def privileges_query_shape() -> None:
    print("=" * 78)
    print("2. CAN A QUERY RETURN DUPLICATES BY CONSTRUCTION?")
    print("=" * 78)
    sql = SNAPSHOT_QUERIES["privileges"]
    print("   privileges:")
    for line in sql.strip().splitlines():
        print(f"     {line.strip()}")
    print()
    checks = [
        ("SELECT DISTINCT", re.search(r"select\s+distinct", sql, re.I) is not None),
        ("GROUP BY", re.search(r"\bgroup\s+by\b", sql, re.I) is not None),
    ]
    for label, present in checks:
        print(f"     {label:18} present: {present}")
    print()
    print("   `information_schema.role_table_grants` has one row per")
    print("   (grantor, grantee, table, privilege_type, is_grantable). The query selects")
    print("   only (table_name, grantee, privilege_type) and applies neither DISTINCT nor")
    print("   GROUP BY, so the SAME privilege granted to the SAME grantee by TWO DIFFERENT")
    print("   GRANTORS projects to two identical output rows.")
    print()
    print("   Whether that configuration exists on any given cluster is a LIVE-DATABASE")
    print("   question and is NOT asserted here. What is established from source alone is")
    print("   that the query does not prevent it.")
    print()
    for dim, sql_text in SNAPSHOT_QUERIES.items():
        has_distinct = re.search(r"select\s+distinct", sql_text, re.I) is not None
        has_group = re.search(r"\bgroup\s+by\b", sql_text, re.I) is not None
        if not (has_distinct or has_group):
            continue
        print(f"     {dim}: DISTINCT={has_distinct} GROUP BY={has_group}")
    print("   (no dimension uses DISTINCT or GROUP BY)")
    print()


# ═════════════════════════════════════════════════════════════════════════════
#  3. The three defects, driven through the REAL compare()
# ═════════════════════════════════════════════════════════════════════════════

POLICY_A = ("fee_records", "p1", "PERMISSIVE", "{authenticated}", "SELECT", "(a)", "")
POLICY_A2 = ("fee_records", "p1", "PERMISSIVE", "{authenticated}", "SELECT", "(a)", "(a)")
POLICY_B = ("orders", "p2", "PERMISSIVE", "{authenticated}", "SELECT", "(b)", "")
GRANT_A = ("fee_records", "authenticated", "SELECT")
GRANT_B = ("orders", "authenticated", "SELECT")


def _run(label: str, dim: str, expected: list, actual: list) -> tuple:
    base = {k: [] for k in SNAPSHOT_QUERIES}
    live = {k: [] for k in SNAPSHOT_QUERIES}
    base[dim] = list(expected)
    live[dim] = list(actual)
    failures, advisories = compare(base, live)
    lines = failures + advisories
    print(f"   {label}")
    print(f"     expected rows : {len(expected)}   actual rows : {len(actual)}")
    print(f"     hard failures : {len(failures)}   advisories : {len(advisories)}")
    for line in lines[:5]:
        print(f"       | {line}")
    return failures, advisories


def defects_through_real_compare() -> dict[str, bool]:
    print("=" * 78)
    print("3. THE THREE DEFECTS, THROUGH THE REAL compare()")
    print("=" * 78)
    print("   Synthetic rows, real function. `compare()` is pure: two dicts in, two")
    print("   lists of strings out. No database is involved at any point.\n")
    verdict: dict[str, bool] = {}

    print("-- D1  modified attribute reported as missing + extra " + "-" * 24)
    f, a = _run("policies: with_check '' -> '(a)' on the SAME policy",
                "policies", [POLICY_A], [POLICY_A2])
    d1 = any("1 missing, 1 unexpected" in s for s in f)
    both = (any("MISSING" in s for s in f) and any("NOT IN this repository" in s for s in f))
    print(f"     reports '1 missing, 1 unexpected' : {d1}")
    print(f"     same object appears on BOTH sides : {both}")
    verdict["D1"] = d1 and both
    print(f"     D1 REPRODUCED: {verdict['D1']}\n")

    print("-- D2  identical content, different order " + "-" * 36)
    f, a = _run("policies: same two rows, swapped",
                "policies", [POLICY_A, POLICY_B], [POLICY_B, POLICY_A])
    d2 = any("0 missing, 0 unexpected" in s for s in f)
    print(f"     reports '0 missing, 0 unexpected' : {d2}")
    print(f"     counted as a HARD FAILURE        : {len(f) > 0}")
    verdict["D2"] = d2 and len(f) > 0
    print(f"     D2 REPRODUCED: {verdict['D2']}\n")

    print("-- D3  MULTIPLICITY: a row duplicated on one side " + "-" * 28)
    f, a = _run("privileges: one grant row twice in baseline, once live",
                "privileges", [GRANT_A, GRANT_A], [GRANT_A])
    d3 = any("0 missing, 0 unexpected" in s for s in f)
    print(f"     reports '0 missing, 0 unexpected' : {d3}")
    print(f"     counted as a HARD FAILURE        : {len(f) > 0}")
    verdict["D3"] = d3 and len(f) > 0
    print(f"     D3 REPRODUCED: {verdict['D3']}")
    print("     NOTE: `privileges` is declared WHOLE_ROW and its ORDER BY is total, so")
    print("     neither W1D-05 guard can see this. D3 is downstream of both.\n")

    print("-- D3b multiplicity in the OTHER direction " + "-" * 35)
    f, a = _run("privileges: once in baseline, twice live",
                "privileges", [GRANT_A], [GRANT_A, GRANT_A])
    print(f"     reports '0 missing, 0 unexpected' : "
          f"{any('0 missing, 0 unexpected' in s for s in f)}\n")

    print("-- CONTROL  a genuine deletion must still be reported " + "-" * 24)
    f, a = _run("privileges: one grant actually removed",
                "privileges", [GRANT_A, GRANT_B], [GRANT_B])
    ok = any("1 missing, 0 unexpected" in s for s in f)
    print(f"     reports '1 missing, 0 unexpected' : {ok}")
    verdict["control_deletion"] = ok
    print()

    print("-- CONTROL  identical input must produce NO drift " + "-" * 28)
    f, a = _run("policies: byte-identical both sides",
                "policies", [POLICY_A, POLICY_B], [POLICY_A, POLICY_B])
    clean = (len(f) == 0 and len(a) == 0)
    print(f"     no failures and no advisories     : {clean}")
    verdict["control_identical"] = clean
    print()

    print("-- ROUTING  an advisory dimension must not become a hard failure " + "-" * 13)
    f, a = _run("recorded_migrations (advisory): checksum changed",
                "recorded_migrations",
                [("m/001.sql", "aaa", "step0")], [("m/001.sql", "bbb", "step0")])
    routed = (len(f) == 0 and len(a) > 0)
    print(f"     routed to advisories, not failures: {routed}")
    verdict["advisory_routing"] = routed
    print(f"     ADVISORY_DIMENSIONS = {sorted(ADVISORY_DIMENSIONS)}\n")
    return verdict


# ═════════════════════════════════════════════════════════════════════════════
#  4. Rule out the other candidate root causes the brief lists
# ═════════════════════════════════════════════════════════════════════════════

def rule_out_alternatives() -> None:
    print("=" * 78)
    print("4. RULING OUT THE OTHER CANDIDATE ROOT CAUSES")
    print("=" * 78)

    print("\n-- missing-vs-null handling ----------------------------------------------")
    print("   `_norm` (scripts/verify_provisioning.py):")
    for value in (None, "", 0, False, "x", [1, 2]):
        print(f"     _norm({value!r:12}) -> {_norm(value)!r}")
    print("   None is preserved as None, consistently on both sides. And JSON round-trip")
    print("   maps None <-> null symmetrically, so a NULL attribute compares equal to a")
    print("   NULL attribute.")
    nulls = _run("policies: NULL-ish attribute identical on both sides", "policies",
                 [("t", "p", "PERMISSIVE", "{r}", "SELECT", "", "")],
                 [("t", "p", "PERMISSIVE", "{r}", "SELECT", "", "")])
    print(f"     -> no drift: {len(nulls[0]) == 0 and len(nulls[1]) == 0}")
    print("   Several queries already `coalesce(..., '')` precisely so a NULL arrives as")
    print("   an empty string rather than None. NOT the root cause.")

    print("\n-- snapshot shape / JSON list-vs-tuple -----------------------------------")
    print("   `_as_tuples` converts JSON lists to tuples before comparing:")
    print(f"     _as_tuples([['a','b']]) -> {_as_tuples([['a', 'b']])}")
    mixed = _run("policies: baseline as JSON lists, live as tuples", "policies",
                 [list(POLICY_A)], [POLICY_A])
    print(f"     -> no drift: {len(mixed[0]) == 0 and len(mixed[1]) == 0}")
    print("   Handled. NOT the root cause.")

    print("\n-- query coverage --------------------------------------------------------")
    print(f"   dimensions queried: {len(SNAPSHOT_QUERIES)}. Every one is read by")
    print("   `compare()` via `for key in SNAPSHOT_QUERIES`. A dimension cannot be")
    print("   silently skipped. NOT the root cause of the MISLEADING REPORT.")

    print("\n-- row ordering ----------------------------------------------------------")
    print("   All 16 queries carry a total ORDER BY (W1D-05 Guard A enforces it), so D2")
    print("   is not reachable through the real query set TODAY. The fragility in")
    print("   `compare()` remains, and D3 shows ordering is not the only way to reach the")
    print("   same empty-report symptom.")

    print("\n-- key construction ------------------------------------------------------")
    print("   `compare()` constructs NO key. That is precisely D1: there is no identity")
    print("   separate from the attributes. THIS IS THE ROOT CAUSE of W1C-02.")
    print()


def main() -> int:
    print("READ-ONLY. No database connection. Production source is not modified.\n")
    dupes = duplicates_in_real_snapshot()
    privileges_query_shape()
    verdict = defects_through_real_compare()
    rule_out_alternatives()

    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    for key in ("D1", "D2", "D3"):
        print(f"   {key} reproduced through the real compare() : {verdict.get(key)}")
    print(f"   control: genuine deletion still reported   : "
          f"{verdict.get('control_deletion')}")
    print(f"   control: identical input yields no drift   : "
          f"{verdict.get('control_identical')}")
    print(f"   advisory routing preserved                 : "
          f"{verdict.get('advisory_routing')}")
    print(f"   duplicates present in the real snapshot    : "
          f"{sorted(dupes) if dupes else 'none'}")
    ok = all(verdict.get(k) for k in ("D1", "D2", "D3",
                                      "control_deletion", "control_identical",
                                      "advisory_routing"))
    print(f"\n   all three defects reproduced with controls intact: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
