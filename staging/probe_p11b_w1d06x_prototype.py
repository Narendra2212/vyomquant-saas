"""Prototype the proposed W1C-02 fix and prove it on the REAL snapshot. Production untouched.

PHASE 11B / W1D-06 Phase 3 evidence — read-only. No database, no network. The candidate
implementation lives HERE, not in `scripts/`; nothing under `scripts/` or `tests/` is
modified by this probe.

WHAT IS BEING PROVEN
--------------------
That a keyed, multiset-aware classification:

  * resolves D1 -- a modified attribute reads as ONE modification, not as a deletion plus
    an addition;
  * resolves D2 and D3 as a consequence -- a reorder and a duplicate-count difference stop
    producing `0 missing, 0 unexpected` hard failures;
  * preserves the two compatibility invariants, so legacy counts remain derivable and
    pass/fail semantics need not change:

        missing_legacy == missing + modified
        extra_legacy   == added   + modified

  * does NOT introduce a new blind spot. A dict keyed by identity would silently collapse
    two rows sharing a key and keep whichever arrived last -- trading a loud wrong answer
    for a quiet one. The prototype detects duplicate keys and reports them as their own
    category, because `privileges` can project two identical rows (same grant, two
    grantors; no DISTINCT, no GROUP BY) and a future dimension could key non-uniquely.

  * produces zero drift when the REAL 396 KB snapshot is compared against itself, and
    exactly one `modified` when one attribute of one row in a copy of it is changed.
"""

from __future__ import annotations

import collections
import copy
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT = REPO / "staging" / "provisioning_snapshot.json"
sys.path.insert(0, str(REPO))

from scripts.verify_provisioning import SNAPSHOT_QUERIES                 # noqa: E402
from scripts.verify_schema_drift import (                                # noqa: E402
    ADVISORY_DIMENSIONS, _as_tuples, compare as legacy_compare,
)

# The declaration proposed for `scripts/verify_provisioning.py`, beside SNAPSHOT_QUERIES.
# Imported from the W1D-05 test today so there is ONE copy in the tree while this is still
# a proposal -- duplicating it here would be a second definition of identity, which is the
# defect class this whole exercise is about.
from tests.test_snapshot_dimension_invariants import (                   # noqa: E402
    COMPARISON_KEY, WHOLE_ROW,
)


# ═════════════════════════════════════════════════════════════════════════════
#  The proposed addition: one pure function. No I/O, no session, no DSN.
# ═════════════════════════════════════════════════════════════════════════════

def classify_dimension(expected, actual, key_width):
    """Classify two already-ordered row lists into four disjoint groups.

    `key_width` is an int, or WHOLE_ROW when the entire row is the identity.

    Returns a dict with:
        unchanged  [row, ...]
        modified   [(key, before, after), ...]
        missing    [row, ...]        in the baseline, absent from the database
        added      [row, ...]        in the database, absent from the baseline
        duplicated [(key, n_expected, n_actual), ...]   a key is not unique on some side

    `duplicated` is reported rather than resolved. A key that repeats means the key
    declaration is wrong for that dimension or the query lost a distinguishing column;
    either way the operator has to know, and silently keeping one of the rows would hide
    it.
    """
    exp = _as_tuples(list(expected))
    act = _as_tuples(list(actual))

    if key_width is WHOLE_ROW:
        exp_counts = collections.Counter(exp)
        act_counts = collections.Counter(act)
        duplicated = [
            (row, exp_counts[row], act_counts[row])
            for row in sorted(set(exp_counts) | set(act_counts), key=str)
            if exp_counts[row] > 1 or act_counts[row] > 1
        ]
        return {
            "unchanged": sorted((exp_counts & act_counts).elements(), key=str),
            "modified": [],
            "missing": sorted((exp_counts - act_counts).elements(), key=str),
            "added": sorted((act_counts - exp_counts).elements(), key=str),
            "duplicated": duplicated,
        }

    width = int(key_width)

    def key_of(row):
        return tuple(row[:width])

    exp_groups: dict[tuple, list] = collections.defaultdict(list)
    act_groups: dict[tuple, list] = collections.defaultdict(list)
    for row in exp:
        exp_groups[key_of(row)].append(row)
    for row in act:
        act_groups[key_of(row)].append(row)

    duplicated = [
        (k, len(exp_groups.get(k, [])), len(act_groups.get(k, [])))
        for k in sorted(set(exp_groups) | set(act_groups), key=str)
        if len(exp_groups.get(k, [])) > 1 or len(act_groups.get(k, [])) > 1
    ]

    unchanged, modified, missing, added = [], [], [], []
    for k in sorted(set(exp_groups) | set(act_groups), key=str):
        before_rows = exp_groups.get(k, [])
        after_rows = act_groups.get(k, [])
        if before_rows and not after_rows:
            missing.extend(before_rows)
        elif after_rows and not before_rows:
            added.extend(after_rows)
        else:
            # Both sides have this key. Pair them off by multiset so that an identical
            # row counts as unchanged even when a duplicate exists alongside it.
            bc = collections.Counter(before_rows)
            ac = collections.Counter(after_rows)
            unchanged.extend(sorted((bc & ac).elements(), key=str))
            left = sorted((bc - ac).elements(), key=str)
            right = sorted((ac - bc).elements(), key=str)
            for i in range(min(len(left), len(right))):
                modified.append((k, left[i], right[i]))
            missing.extend(left[len(right):])
            added.extend(right[len(left):])
    return {"unchanged": unchanged, "modified": modified,
            "missing": missing, "added": added, "duplicated": duplicated}


def proposed_compare(baseline: dict, live: dict):
    """Drop-in replacement for `compare()`. SAME 2-tuple signature, richer lines."""
    failures: list[str] = []
    advisories: list[str] = []
    for key in SNAPSHOT_QUERIES:
        expected = _as_tuples(baseline.get(key, []))
        actual = _as_tuples(live.get(key, []))
        res = classify_dimension(expected, actual, COMPARISON_KEY[key])
        if not (res["modified"] or res["missing"] or res["added"]
                or res["duplicated"]):
            continue
        lines = [
            f"{key}: {len(res['missing'])} missing, {len(res['added'])} added, "
            f"{len(res['modified'])} modified"
            + (f", {len(res['duplicated'])} duplicated key(s)"
               if res["duplicated"] else "")
        ]
        for k, before, after in res["modified"][:12]:
            lines.append(f"    MODIFIED {k}")
            for i in range(len(before)):
                if i < len(after) and before[i] != after[i]:
                    lines.append(f"        col[{i}]: {before[i]!r} -> {after[i]!r}")
        for row in res["missing"][:12]:
            lines.append(f"    MISSING from the database : {row}")
        for row in res["added"][:12]:
            lines.append(f"    NOT IN this repository    : {row}")
        for k, ne, na in res["duplicated"][:12]:
            lines.append(f"    DUPLICATED KEY {k}: baseline x{ne}, database x{na}")
        (advisories if key in ADVISORY_DIMENSIONS else failures).extend(lines)
    return failures, advisories


def dimension_marker(baseline: dict, live: dict, key: str) -> str:
    """What `main()`'s summary line should say, derived from the classification.

    `verify_schema_drift.py:269` currently recomputes `expected == actual` itself, so a
    reorder or a duplicate-count difference prints `DRIFT` in the summary while the detail
    section prints nothing. Deriving the marker from the same classification removes that
    contradiction.
    """
    res = classify_dimension(_as_tuples(baseline.get(key, [])),
                             _as_tuples(live.get(key, [])), COMPARISON_KEY[key])
    if res["modified"] or res["missing"] or res["added"] or res["duplicated"]:
        return "DRIFT"
    return "same "


# ═════════════════════════════════════════════════════════════════════════════
#  Cases
# ═════════════════════════════════════════════════════════════════════════════

P_A = ("fee_records", "p1", "PERMISSIVE", "{authenticated}", "SELECT", "(a)", "")
P_A2 = ("fee_records", "p1", "PERMISSIVE", "{authenticated}", "SELECT", "(a)", "(a)")
P_B = ("orders", "p2", "PERMISSIVE", "{authenticated}", "SELECT", "(b)", "")
G_A = ("fee_records", "authenticated", "SELECT")
G_B = ("orders", "authenticated", "SELECT")

CASES = [
    ("D1  modified attribute", "policies", [P_A], [P_A2],
     dict(modified=1, missing=0, added=0)),
    ("D2  identical content, reordered", "policies", [P_A, P_B], [P_B, P_A],
     dict(modified=0, missing=0, added=0)),
    ("D3  duplicate in baseline only", "privileges", [G_A, G_A], [G_A],
     dict(modified=0, missing=1, added=0)),
    ("D3b duplicate in database only", "privileges", [G_A], [G_A, G_A],
     dict(modified=0, missing=0, added=1)),
    ("control  genuine deletion", "privileges", [G_A, G_B], [G_B],
     dict(modified=0, missing=1, added=0)),
    ("control  genuine addition", "privileges", [G_B], [G_A, G_B],
     dict(modified=0, missing=0, added=1)),
    ("control  identical", "policies", [P_A, P_B], [P_A, P_B],
     dict(modified=0, missing=0, added=0)),
    ("control  both empty", "policies", [], [],
     dict(modified=0, missing=0, added=0)),
    ("control  baseline empty, database has one", "policies", [], [P_A],
     dict(modified=0, missing=0, added=1)),
    ("control  baseline has one, database empty", "policies", [P_A], [],
     dict(modified=0, missing=1, added=0)),
    ("mixed  unchanged + modified + missing + added", "policies",
     [P_A, P_B, ("t", "gone", "P", "{r}", "SELECT", "(x)", "")],
     [P_A2, P_B, ("t", "new", "P", "{r}", "SELECT", "(x)", "")],
     dict(modified=1, missing=1, added=1)),
    ("null attribute identical on both sides", "policies",
     [("t", "p", "PERMISSIVE", "{r}", "SELECT", "", "")],
     [("t", "p", "PERMISSIVE", "{r}", "SELECT", "", "")],
     dict(modified=0, missing=0, added=0)),
    ("null -> non-null attribute is a modification", "policies",
     [("t", "p", "PERMISSIVE", "{r}", "SELECT", "", "")],
     [("t", "p", "PERMISSIVE", "{r}", "SELECT", "(q)", "")],
     dict(modified=1, missing=0, added=0)),
]


def run_cases() -> int:
    print("=" * 78)
    print("1. CASES: legacy vs proposed, with the compatibility invariant checked")
    print("=" * 78)
    bad = 0
    print(f"   {'case':46} {'legacy':>16} {'proposed':>26} inv")
    print("   " + "-" * 94)
    for label, dim, exp, act, want in CASES:
        base = {k: [] for k in SNAPSHOT_QUERIES}
        live = {k: [] for k in SNAPSHOT_QUERIES}
        base[dim], live[dim] = list(exp), list(act)

        lf, la = legacy_compare(base, live)
        legacy_missing = [r for r in _as_tuples(exp) if r not in _as_tuples(act)]
        legacy_extra = [r for r in _as_tuples(act) if r not in _as_tuples(exp)]
        legacy_txt = f"{len(legacy_missing)}m/{len(legacy_extra)}x"
        legacy_fails = len(lf) + len(la) > 0

        res = classify_dimension(exp, act, COMPARISON_KEY[dim])
        got = dict(modified=len(res["modified"]), missing=len(res["missing"]),
                   added=len(res["added"]))
        prop_txt = (f"{got['missing']}m/{got['added']}a/{got['modified']}mod"
                    f"{'/dup' if res['duplicated'] else ''}")

        # SCOPE OF THE COMPATIBILITY INVARIANT.
        #
        # A first run of this probe applied `missing + modified == legacy_missing` to
        # every case and flagged D3/D3b as failures. That was the CHECK being wrong, not
        # the design. Where a row's multiplicity differs, legacy's membership test
        # reports `0 missing, 0 unexpected` -- a count that is simply incorrect -- so
        # requiring the proposed output to reproduce it would require reproducing the
        # defect. The invariant is therefore asserted only where legacy is correct, and
        # the multiplicity cases are asserted to DIVERGE.
        #
        # This also refines a claim in PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md section
        # 3.5, which reported the invariant holding 10/10. Those 10 cases contained no
        # multiplicity case, so the blanket statement was untested in the one region
        # where it cannot hold.
        multiplicity = bool(res["duplicated"])
        inv_missing = len(res["missing"]) + len(res["modified"]) == len(legacy_missing)
        inv_added = len(res["added"]) + len(res["modified"]) == len(legacy_extra)
        if multiplicity:
            inv = not (inv_missing and inv_added)      # divergence is REQUIRED here
            inv_label = "div"
        else:
            inv = inv_missing and inv_added
            inv_label = "OK " if inv else "BAD"
        shape_ok = got == want
        mark = inv_label if (inv and shape_ok) else "BAD"
        if not (inv and shape_ok):
            bad += 1
        print(f"   {label:46} {legacy_txt:>16} {prop_txt:>26} {mark}")
        if not shape_ok:
            print(f"       expected shape {want}, got {got}")
        if multiplicity:
            print(f"       multiplicity case: legacy {len(legacy_missing)}m/"
                  f"{len(legacy_extra)}x is WRONG; proposed "
                  f"{len(res['missing'])}m/{len(res['added'])}a diverges "
                  f"deliberately, and the duplicate key is named")
        elif not inv:
            print(f"       invariant broken: legacy {len(legacy_missing)}m/"
                  f"{len(legacy_extra)}x vs proposed "
                  f"{len(res['missing'])}+{len(res['modified'])} / "
                  f"{len(res['added'])}+{len(res['modified'])}")
        # the empty-report symptom
        if any("0 missing, 0 unexpected" in s for s in lf + la):
            pf, pa = proposed_compare(base, live)
            empty_now = any("0 missing, 0 added, 0 modified" in s for s in pf + pa)
            print(f"       legacy emitted '0 missing, 0 unexpected'; proposed emits "
                  f"{'the same empty report' if empty_now else 'no such line'}"
                  f"  -> {'BAD' if empty_now else 'fixed'}")
            if empty_now:
                bad += 1
    print(f"\n   cases: {len(CASES)}   problems: {bad}")
    return bad


def show_reports() -> None:
    print("\n" + "=" * 78)
    print("2. THE REPORT TEXT, SIDE BY SIDE (D1)")
    print("=" * 78)
    base = {k: [] for k in SNAPSHOT_QUERIES}
    live = {k: [] for k in SNAPSHOT_QUERIES}
    base["policies"], live["policies"] = [P_A], [P_A2]
    print("   LEGACY:")
    for line in sum(legacy_compare(base, live), []):
        print(f"     | {line}")
    print("   PROPOSED:")
    for line in sum(proposed_compare(base, live), []):
        print(f"     | {line}")
    print("\n   summary marker consistency (verify_schema_drift.py:269):")
    base2 = {k: [] for k in SNAPSHOT_QUERIES}
    live2 = {k: [] for k in SNAPSHOT_QUERIES}
    base2["policies"], live2["policies"] = [P_A, P_B], [P_B, P_A]
    legacy_mark = ("same " if _as_tuples(base2["policies"]) == _as_tuples(live2["policies"])
                   else "DRIFT")
    print(f"     reordered rows -> legacy marker {legacy_mark!r}, "
          f"proposed marker {dimension_marker(base2, live2, 'policies')!r}")
    lf, _ = legacy_compare(base2, live2)
    print(f"     legacy detail section: {lf or '(nothing)'}")
    print("     => legacy prints DRIFT in the summary and nothing in the detail; the")
    print("        proposed marker agrees with the detail.")


def real_snapshot_checks() -> int:
    print("\n" + "=" * 78)
    print("3. THE REAL SNAPSHOT (396 KB, captured 2026-10-06)")
    print("=" * 78)
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    bad = 0

    f, a = proposed_compare(snap, snap)
    print(f"   self-comparison -> failures {len(f)}, advisories {len(a)}  "
          f"{'OK' if not (f or a) else 'BAD'}")
    if f or a:
        bad += 1
        for line in (f + a)[:6]:
            print(f"     | {line}")

    markers = {k: dimension_marker(snap, snap, k) for k in SNAPSHOT_QUERIES}
    drifting = [k for k, v in markers.items() if v != "same "]
    print(f"   all 16 summary markers 'same ' : {not drifting}"
          f"{'  (' + str(drifting) + ')' if drifting else ''}")
    if drifting:
        bad += 1

    # One attribute of one real policy row changed.
    mutated = copy.deepcopy(snap)
    row = list(mutated["policies"][0])
    original = row[6]
    row[6] = "(W1D06_PROTOTYPE_SENTINEL)"
    mutated["policies"][0] = row
    res = classify_dimension(snap["policies"], mutated["policies"],
                             COMPARISON_KEY["policies"])
    ok = (len(res["modified"]) == 1 and not res["missing"] and not res["added"])
    print(f"   one real policy attribute changed -> "
          f"{len(res['missing'])} missing, {len(res['added'])} added, "
          f"{len(res['modified'])} modified  {'OK' if ok else 'BAD'}")
    if not ok:
        bad += 1
    lm = [r for r in _as_tuples(snap["policies"])
          if r not in _as_tuples([tuple(x) for x in mutated["policies"]])]
    print(f"     legacy would report: {len(lm)} missing + {len(lm)} unexpected "
          f"for the SAME single change")
    for k, before, after in res["modified"]:
        print(f"     MODIFIED {k}")
        print(f"       col[6]: {before[6]!r} -> {after[6]!r}")
    print(f"     (baseline value restored in memory only; the file on disk is "
          f"untouched: {original!r} still at index 6 of the real snapshot)")

    # A deletion in the real data must still read as a deletion.
    trimmed = copy.deepcopy(snap)
    removed = trimmed["policies"].pop(0)
    res = classify_dimension(snap["policies"], trimmed["policies"],
                             COMPARISON_KEY["policies"])
    ok = (len(res["missing"]) == 1 and not res["modified"] and not res["added"])
    print(f"   one real policy removed -> {len(res['missing'])} missing, "
          f"{len(res['added'])} added, {len(res['modified'])} modified  "
          f"{'OK' if ok else 'BAD'}")
    if not ok:
        bad += 1

    # Reordering the real data must read as no drift.
    shuffled = copy.deepcopy(snap)
    shuffled["policies"] = list(reversed(shuffled["policies"]))
    res = classify_dimension(snap["policies"], shuffled["policies"],
                             COMPARISON_KEY["policies"])
    ok = not (res["missing"] or res["added"] or res["modified"])
    print(f"   all 188 real policies reversed -> no drift: {ok}  "
          f"{'OK' if ok else 'BAD'}")
    if not ok:
        bad += 1
    legacy_same = _as_tuples(snap["policies"]) == _as_tuples(
        [tuple(x) for x in shuffled["policies"]])
    print(f"     legacy fast path would call them equal: {legacy_same} "
          f"-> legacy reports a hard failure with no rows")
    return bad


def duplicate_key_hazard() -> int:
    print("\n" + "=" * 78)
    print("4. THE NEW BLIND SPOT A NAIVE FIX WOULD CREATE")
    print("=" * 78)
    print("   A dict keyed by identity would keep whichever row arrived last and lose")
    print("   the other. The prototype reports duplicate keys instead.\n")
    bad = 0
    dup_a = ("fee_records", "p1", "PERMISSIVE", "{a}", "SELECT", "(1)", "")
    dup_b = ("fee_records", "p1", "PERMISSIVE", "{a}", "SELECT", "(2)", "")
    res = classify_dimension([dup_a, dup_b], [dup_a], COMPARISON_KEY["policies"])
    print(f"   two baseline rows share key ('fee_records','p1'):")
    print(f"     duplicated : {res['duplicated']}")
    print(f"     missing    : {len(res['missing'])}  modified: {len(res['modified'])}")
    reported = bool(res["duplicated"])
    print(f"     duplicate key REPORTED, not collapsed: {reported}  "
          f"{'OK' if reported else 'BAD'}")
    if not reported:
        bad += 1
    naive = {tuple(r[:2]): r for r in [dup_a, dup_b]}
    print(f"     a naive dict comprehension would retain only {naive[('fee_records','p1')][5]!r}"
          f" and lose {dup_a[5]!r}")
    return bad


def main() -> int:
    print("READ-ONLY PROTOTYPE. scripts/ and tests/ are NOT modified.\n")
    bad = run_cases()
    show_reports()
    bad += real_snapshot_checks()
    bad += duplicate_key_hazard()
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    print(f"   problems: {bad}")
    if bad:
        print("   The proposed design does NOT yet satisfy every case. Do not implement.")
        return 1
    print("   The proposed classification resolves D1, D2 and D3, keeps every control")
    print("   intact, produces zero drift on the real snapshot compared with itself, and")
    print("   reports the duplicate-key hazard a naive keyed fix would hide.")
    print()
    print("   Compatibility, stated precisely: legacy counts remain derivable as")
    print("   missing+modified and added+modified for every case EXCEPT the multiplicity")
    print("   cases, where legacy reports 0/0 and is wrong. The proposed output diverges")
    print("   there deliberately. That is the only behavioural change to counts.")
    print("\n   W1C-02 is NOT closed: this is a prototype in staging/, not an")
    print("   implementation, and no production file was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
