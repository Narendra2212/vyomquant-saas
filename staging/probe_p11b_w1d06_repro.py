"""Phase 11B-W1D-06. READ-ONLY PROBE. Reproduce W1C-02 against the CURRENT source.

The prior W1D probe asserts these outcomes; this one re-derives them by calling the REAL
`scripts.verify_schema_drift.compare()` as it stands today, on pure in-memory data. No
database, no fixture, no server -- `compare(baseline, live)` takes two dicts and is a pure
function, which is exactly why the defect is reproducible without PostgreSQL.

Run with a poisoned PG environment so that any accidental connection attempt would be
obvious rather than silent.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

os.environ.setdefault("PGHOST", "255.255.255.255")
os.environ.setdefault("PGPORT", "1")
os.environ.setdefault("PGCONNECT_TIMEOUT", "1")
os.environ["AERORA_TEST_DATABASE_URL"] = ""

ROOT = pathlib.Path(r"c:\aerora_quant_backend_updated_final1")
sys.path.insert(0, str(ROOT))

t0 = time.time()
from scripts.verify_schema_drift import ADVISORY_DIMENSIONS, compare  # noqa: E402
from scripts.verify_provisioning import SNAPSHOT_QUERIES  # noqa: E402

IMPORT_SECONDS = time.time() - t0

# Key widths as declared by W1D-05's guard test (test-file constant, not production).
WHOLE_ROW = "whole-row"
KEY_WIDTH = {
    "policies": 2,
    "functions": 2,
    "columns": 2,
    "recorded_migrations": 1,
    "privileges": WHOLE_ROW,
}


def empty_snapshot() -> dict[str, list]:
    return {k: [] for k in SNAPSHOT_QUERIES}


def run_case(title: str, dimension: str, expected_rows: list, actual_rows: list) -> dict:
    base = empty_snapshot()
    live = empty_snapshot()
    base[dimension] = list(expected_rows)
    live[dimension] = list(actual_rows)
    failures, advisories = compare(base, live)
    lines = advisories if dimension in ADVISORY_DIMENSIONS else failures
    print("-" * 78)
    print(f"  CASE: {title}")
    print(f"    dimension      : {dimension}  (key_width={KEY_WIDTH.get(dimension, '?')})")
    print(f"    advisory        : {dimension in ADVISORY_DIMENSIONS}")
    print(f"    baseline rows   : {expected_rows}")
    print(f"    live rows       : {actual_rows}")
    print(f"    equal fast-path : {expected_rows == actual_rows}")
    print(f"    hard failures   : {len(failures)} line(s)")
    print(f"    advisories      : {len(advisories)} line(s)")
    if lines:
        print("    REPORT AS PRINTED TODAY:")
        for l in lines:
            print(f"      | {l}")
    else:
        print("    REPORT AS PRINTED TODAY: (no output -- treated as no drift)")
    return {"failures": failures, "advisories": advisories}


def main() -> int:
    print("=" * 78)
    print("W1D-06 -- W1C-02 REPRODUCTION AGAINST CURRENT SOURCE")
    print("=" * 78)
    print(f"  compare() imported from : scripts/verify_schema_drift.py")
    print(f"  SNAPSHOT_QUERIES source : scripts/verify_provisioning.py ({len(SNAPSHOT_QUERIES)} dimensions)")
    print(f"  ADVISORY_DIMENSIONS     : {sorted(ADVISORY_DIMENSIONS)}")
    print(f"  import time             : {IMPORT_SECONDS:.3f}s under a routed-to-nowhere PGHOST")
    print(f"  -> no connection is made at import or by compare(); this is pure data")
    print()

    # ---------------------------------------------------------------- case 1
    # The headline W1C-02 case: an EXISTING policy whose with_check expression changed.
    # Key is (tablename, policyname); columns 2..6 are attributes.
    pol_old = ("fee_records", "fee_select", "PERMISSIVE", "{authenticated}", "SELECT", "(uid = x)", "")
    pol_new = ("fee_records", "fee_select", "PERMISSIVE", "{authenticated}", "SELECT", "(uid = x)", "(a = b)")
    r1 = run_case(
        "MODIFIED attribute on an object present on BOTH sides (W1C-02)",
        "policies", [pol_old], [pol_new],
    )
    print("    WHY THIS IS MISLEADING:")
    print("      The policy `fee_select` on `fee_records` EXISTS in both the baseline and")
    print("      the database. Only its WITH CHECK expression changed. The report says it")
    print("      is MISSING from the database and simultaneously NOT IN this repository.")
    print("      A reader following the script's own remediation advice would write a")
    print("      migration to CREATE a policy that already exists.")
    print()

    # ---------------------------------------------------------------- case 2
    # Same content, different order / multiplicity: the fast path fails but neither
    # membership list populates, so a hard failure is raised with nothing to show.
    a = ("t1", "c1", "integer")
    b = ("t2", "c2", "text")
    r2 = run_case(
        "REORDERED rows -- drift declared with ZERO explanatory rows",
        "columns", [a, b], [b, a],
    )
    r3 = run_case(
        "DUPLICATE multiplicity differs -- also zero explanatory rows",
        "columns", [a, a], [a],
    )
    print("    WHY THESE ARE WORSE:")
    print("      `expected == actual` is False so the fast path is skipped, but `r not in`")
    print("      membership finds every row on the other side, so both lists come back")
    print("      empty. The run FAILS with the literal text '0 missing, 0 unexpected'.")
    print("      W1D-05's Guard A makes the reorder variant unreachable for TODAY's 16")
    print("      queries (all have a total ORDER BY), but the multiplicity variant does")
    print("      not depend on ordering at all.")
    print()

    # ---------------------------------------------------------------- controls
    print("=" * 78)
    print("NEGATIVE CONTROLS -- cases the current code reports CORRECTLY")
    print("=" * 78)
    run_case("genuine REMOVAL", "policies", [pol_old], [])
    run_case("genuine ADDITION", "policies", [], [pol_new])
    run_case("UNCHANGED", "policies", [pol_old], [pol_old])
    run_case("EMPTY on both sides", "policies", [], [])
    run_case(
        "whole-row dimension changed (no key -> removal+addition is CORRECT here)",
        "privileges",
        [("fee_records", "authenticated", "SELECT")],
        [("fee_records", "authenticated", "UPDATE")],
    )
    run_case(
        "ADVISORY dimension modified -> must route to advisories, not failures",
        "recorded_migrations",
        [("001_init.sql", "abc123", "2026-01-01")],
        [("001_init.sql", "def456", "2026-01-01")],
    )

    # ---------------------------------------------------------------- summary
    print("=" * 78)
    print("SUMMARY OF CONFIRMED DEFECTS")
    print("=" * 78)
    d1 = r1["failures"]
    print("  D1 (W1C-02): an attribute change on an object present on both sides is")
    print("      classified as MISSING + UNEXPECTED rather than MODIFIED.")
    print(f"      evidence: first reported line = {d1[0]!r}" if d1 else "      NOT REPRODUCED")
    zero_rows_2 = bool(r2["failures"]) and "0 missing, 0 unexpected" in r2["failures"][0]
    zero_rows_3 = bool(r3["failures"]) and "0 missing, 0 unexpected" in r3["failures"][0]
    print("  D2 (reorder):     hard failure with no explanatory rows ->"
          f" {'REPRODUCED' if zero_rows_2 else 'not reproduced'}")
    print("  D3 (multiplicity):hard failure with no explanatory rows ->"
          f" {'REPRODUCED' if zero_rows_3 else 'not reproduced'}")
    print()
    print("  ROOT CAUSE CLASSIFICATION (choosing among the brief's options):")
    print("    * query coverage        : NO  -- all 16 dimensions are queried")
    print("    * row ordering          : NO for today's queries (Guard A), but D2 shares")
    print("                              the same failure signature as D3")
    print("    * KEY CONSTRUCTION      : YES -- compare() has no notion of a key; it tests")
    print("                              whole-tuple membership, so it cannot pair an old")
    print("                              and a new version of the same object")
    print("    * missing-vs-null       : NO  -- _norm() maps None to None consistently")
    print("    * snapshot shape        : NO  -- list-of-tuples is adequate; JSON list/tuple")
    print("                              coercion is already handled by _as_tuples()")
    print("    * COMPARISON LOGIC      : YES -- set-style membership instead of a keyed,")
    print("                              multiset-aware comparison")
    print()
    print("  => The defect is entirely DOWNSTREAM of W1D-05's static guards. The guards")
    print("     are not defective and need no change: they constrain the SQL, while this")
    print("     is a flaw in how two already-ordered result sets are diffed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
