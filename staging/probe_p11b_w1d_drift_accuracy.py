"""W1C-02: why a modified object reports as missing+extra, and which dimensions it hits.

PHASE 11B / WORKSTREAM 1D — STRICTLY READ-ONLY, and NO DATABASE CONNECTION.
It reads `scripts/verify_provisioning.py`, `scripts/verify_schema_drift.py` and
`staging/provisioning_snapshot.json`, and runs the proposed classification against
SYNTHETIC objects. It does not edit the checker, does not add tests, and does not claim
the checker is fixed.

THE MECHANISM, FROM SOURCE
--------------------------
`scripts/verify_schema_drift.py:compare()`:

    expected = _as_tuples(baseline.get(key, []))
    actual   = _as_tuples(live.get(key, []))
    if expected == actual:
        continue
    missing = [r for r in expected if r not in actual]
    extra   = [r for r in actual   if r not in expected]

Rows are compared as WHOLE TUPLES. There is no notion of an object's identity separate
from its attributes. So changing one attribute of one object removes a tuple from
`expected` and adds a different tuple to `actual`, and the object is reported twice --
once as MISSING and once as NOT IN THIS REPOSITORY -- with nothing saying they are the
same object.

A dimension is vulnerable exactly when its SELECT list is wider than its natural key.
Where the key IS the whole tuple, a difference can only be a genuine add or remove.

A SECOND, LATENT MODE
---------------------
`expected == actual` is Python LIST equality, which is order-sensitive, while the
`missing`/`extra` computation is order-INsensitive. A dimension whose rows arrived in a
different order but with identical content would therefore fall past the fast path and
then produce `0 missing, 0 unexpected` -- a drift report with no items in it, counted as
a hard failure. Today every one of the 16 queries carries an `ORDER BY` that is a
superkey, so this cannot fire. It is unenforced, though: a new dimension added without a
total `ORDER BY` would turn the checker into a false-positive generator. That is worth a
test of its own.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT = REPO / "staging" / "provisioning_snapshot.json"
sys.path.insert(0, str(REPO))

from scripts.verify_provisioning import SNAPSHOT_QUERIES          # noqa: E402
from scripts.verify_schema_drift import ADVISORY_DIMENSIONS, _as_tuples  # noqa: E402

#: Natural key WIDTH per dimension: how many leading columns identify the object.
#: Derived from each query's ORDER BY and the catalogue's own uniqueness rules, both of
#: which are quoted in the report. `None` means the whole tuple is the key.
KEY_WIDTH: dict[str, int | None] = {
    "tables": 1,                          # relname | owner
    "columns": 2,                          # table_name, column_name | 7 attributes
    "primary_keys": 2,                     # relname, conname | column list
    "foreign_keys": 2,                     # relname, conname | definition
    "unique_and_check_constraints": 2,     # relname, conname | definition
    "indexes": 2,                          # tablename, indexname | indexdef
    "row_level_security": 1,               # relname | rls flags
    "policies": 2,                         # tablename, policyname | 5 attributes
    "privileges": None,                    # table_name, grantee, privilege_type = key
    "extensions": None,                    # extname = key
    "functions": 2,                        # proname, identity_args | prosecdef
    "triggers": 2,                         # relname, tgname | function name
    "sequences": None,                     # sequence_name = key
    "enum_types": 1,                       # typname | label list
    "alembic_version": None,               # version_num = key
    "recorded_migrations": 1,              # filename | sha256, step
}


def classify(expected: list, actual: list, key_width: int | None) -> dict:
    """The proposed four-way classification. Pure function, no I/O.

    Backwards compatible by construction: `missing` and `added` together are exactly
    today's `missing`/`extra` minus the rows that pair up as `modified`, so existing
    counts can still be derived (`missing_today = missing + modified`,
    `extra_today = added + modified`).
    """
    exp = _as_tuples(list(expected))
    act = _as_tuples(list(actual))
    if key_width is None:
        exp_set, act_set = set(exp), set(act)
        return {"unchanged": sorted(exp_set & act_set, key=str),
                "modified": [],
                "missing": sorted(exp_set - act_set, key=str),
                "added": sorted(act_set - exp_set, key=str)}

    def key(row):
        return tuple(row[:key_width])

    exp_by = {key(r): r for r in exp}
    act_by = {key(r): r for r in act}
    unchanged, modified, missing, added = [], [], [], []
    for k, row in exp_by.items():
        if k not in act_by:
            missing.append(row)
        elif act_by[k] == row:
            unchanged.append(row)
        else:
            modified.append((k, row, act_by[k]))
    for k, row in act_by.items():
        if k not in exp_by:
            added.append(row)
    return {"unchanged": unchanged, "modified": modified,
            "missing": missing, "added": added}


def _attr_diff(key_width: int, before: tuple, after: tuple) -> list[str]:
    out = []
    for i in range(key_width, max(len(before), len(after))):
        b = before[i] if i < len(before) else "<absent>"
        a = after[i] if i < len(after) else "<absent>"
        if b != a:
            out.append(f"col[{i}]: {b!r} -> {a!r}")
    return out


# ═════════════════════════════════════════════════════════════════════════════
#  1. Which dimensions are vulnerable
# ═════════════════════════════════════════════════════════════════════════════

def dimension_table() -> None:
    print("=" * 78)
    print("1. WHICH DIMENSIONS CAN MISREPORT A MODIFICATION")
    print("=" * 78)
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    print(f"   {'dimension':32} {'cols':>5} {'key':>4} {'attrs':>6}  vulnerable  advisory")
    print("   " + "-" * 74)
    vulnerable = []
    for dim in SNAPSHOT_QUERIES:
        rows = snap.get(dim, [])
        ncols = len(rows[0]) if rows and isinstance(rows[0], (list, tuple)) else 1
        kw = KEY_WIDTH[dim]
        attrs = 0 if kw is None else max(0, ncols - kw)
        vuln = attrs > 0
        if vuln:
            vulnerable.append(dim)
        print(f"   {dim:32} {ncols:>5} {str(kw if kw is not None else 'all'):>4} "
              f"{attrs:>6}  {'YES' if vuln else 'no ':>10}  "
              f"{'advisory' if dim in ADVISORY_DIMENSIONS else ''}")
    print(f"\n   VULNERABLE: {len(vulnerable)} of {len(SNAPSHOT_QUERIES)} dimensions")
    for dim in vulnerable:
        print(f"     - {dim}")
    print(f"\n   NOT vulnerable (key is the whole tuple): "
          f"{[d for d in SNAPSHOT_QUERIES if KEY_WIDTH[d] is None]}")
    print("\n   Consequence for W1C's accounting: in `privileges` the key IS the whole")
    print("   tuple, so its 5 'missing' rows are GENUINE removals. In `policies` and")
    print("   `functions` the key is narrower than the row, so their 1 'missing' each is")
    print("   a MODIFICATION reported twice. Of the 7 reported disappearances, 5 are real")
    print("   deletions and 2 are modifications -- a distinction the tool cannot express.")
    print()


# ═════════════════════════════════════════════════════════════════════════════
#  2. Order-sensitivity of the fast path
# ═════════════════════════════════════════════════════════════════════════════

def order_check() -> None:
    print("=" * 78)
    print("2. THE LATENT ORDER-SENSITIVITY MODE")
    print("=" * 78)
    missing_order = []
    for dim, sql in SNAPSHOT_QUERIES.items():
        has = re.search(r"\border\s+by\b", sql, re.IGNORECASE) is not None
        if not has:
            missing_order.append(dim)
        print(f"   {dim:32} ORDER BY present: {'yes' if has else 'NO'}")
    print(f"\n   dimensions without ORDER BY: {missing_order or 'none'}")
    if not missing_order:
        print("   The order-sensitive fast path cannot misfire today. The guarantee is")
        print("   UNENFORCED, though -- nothing fails if a future dimension omits it.")
    print("\n   Demonstration on synthetic data (same content, different order):")
    exp = [("a", "1"), ("b", "2")]
    act = [("b", "2"), ("a", "1")]
    print(f"     expected == actual        : {exp == act}   <- fast path NOT taken")
    print(f"     missing                   : {[r for r in exp if r not in act]}")
    print(f"     extra                     : {[r for r in act if r not in exp]}")
    print("     => today's checker would print '0 missing, 0 unexpected' and count it")
    print("        as a HARD FAILURE for any non-advisory dimension.")
    print()


# ═════════════════════════════════════════════════════════════════════════════
#  3. The proposed classification, on synthetic objects
# ═════════════════════════════════════════════════════════════════════════════

CASES = [
    ("policies / unchanged", "policies", 2,
     [("t", "p", "PERMISSIVE", "authenticated", "UPDATE", "(a = b)", "")],
     [("t", "p", "PERMISSIVE", "authenticated", "UPDATE", "(a = b)", "")]),
    ("policies / MODIFIED with_check  <- the real W1C-02 case", "policies", 2,
     [("t", "p", "PERMISSIVE", "authenticated", "UPDATE", "(a = b)", "")],
     [("t", "p", "PERMISSIVE", "authenticated", "UPDATE", "(a = b)", "(a = b)")]),
    ("policies / missing", "policies", 2,
     [("t", "p", "PERMISSIVE", "authenticated", "UPDATE", "(a = b)", "")],
     []),
    ("policies / added", "policies", 2,
     [],
     [("t", "p", "PERMISSIVE", "authenticated", "UPDATE", "(a = b)", "")]),
    ("functions / MODIFIED prosecdef  <- the real 024 case", "functions", 2,
     [("process_referral_commission", "p_x uuid", False)],
     [("process_referral_commission", "p_x uuid", True)]),
    ("privileges / genuine removal (key is whole tuple)", "privileges", None,
     [("library_strategies", "anon", "INSERT"),
      ("library_strategies", "anon", "SELECT")],
     [("library_strategies", "anon", "SELECT")]),
    ("columns / MODIFIED nullability", "columns", 2,
     [("fee_records", "fee_pct", "numeric", "NO")],
     [("fee_records", "fee_pct", "numeric", "YES")]),
    ("recorded_migrations / MODIFIED checksum  <- a mutated migration file", "recorded_migrations", 1,
     [("migrations/023_x.sql", "aaaa", "phase8")],
     [("migrations/023_x.sql", "bbbb", "phase8")]),
    ("tables / MODIFIED owner", "tables", 1,
     [("orders", "postgres")],
     [("orders", "app_role")]),
    ("mixed: one unchanged, one modified, one missing, one added", "policies", 2,
     [("t", "keep", "P", "r", "SELECT", "(x)", ""),
      ("t", "mod", "P", "r", "SELECT", "(x)", ""),
      ("t", "gone", "P", "r", "SELECT", "(x)", "")],
     [("t", "keep", "P", "r", "SELECT", "(x)", ""),
      ("t", "mod", "P", "r", "SELECT", "(y)", ""),
      ("t", "new", "P", "r", "SELECT", "(x)", "")]),
]


def synthetic_demo() -> int:
    print("=" * 78)
    print("3. PROPOSED CLASSIFICATION, EXERCISED ON SYNTHETIC OBJECTS")
    print("=" * 78)
    print("   These are the regression cases the report proposes. Each is pure data --")
    print("   no database, no fixture, no server -- so they can run in the default lane.\n")
    failures = 0
    for label, dim, kw, exp, act in CASES:
        res = classify(exp, act, kw)
        today_missing = [r for r in _as_tuples(exp) if r not in _as_tuples(act)]
        today_extra = [r for r in _as_tuples(act) if r not in _as_tuples(exp)]
        print("-" * 78)
        print(f"   {label}")
        print(f"     dimension={dim}  key_width={kw}")
        print(f"     TODAY    : {len(today_missing)} missing, {len(today_extra)} unexpected")
        print(f"     PROPOSED : {len(res['unchanged'])} unchanged, "
              f"{len(res['modified'])} modified, {len(res['missing'])} missing, "
              f"{len(res['added'])} added")
        for k, before, after in res["modified"]:
            print(f"       MODIFIED {k}")
            for line in _attr_diff(kw, before, after):
                print(f"         {line}")
        # Backwards-compatibility invariant.
        derived_missing = len(res["missing"]) + len(res["modified"])
        derived_extra = len(res["added"]) + len(res["modified"])
        ok = (derived_missing == len(today_missing)
              and derived_extra == len(today_extra))
        print(f"     COMPAT   : missing+modified={derived_missing} (today "
              f"{len(today_missing)}), added+modified={derived_extra} (today "
              f"{len(today_extra)})  {'OK' if ok else 'MISMATCH'}")
        if not ok:
            failures += 1
    print("-" * 78)
    print(f"\n   compatibility invariant held: {len(CASES) - failures}/{len(CASES)} cases")
    if failures:
        print("   !! the proposed classification does NOT reduce to today's counts in")
        print("      every case; the invariant needs refining before implementation.")
    else:
        print("   Every case reduces exactly to today's counts, so the improvement can")
        print("   ship behind a reporting change with no change to pass/fail semantics.")
    return failures


def main() -> int:
    print("READ-ONLY. No database connection. The checker and its tests are NOT edited.\n")
    dimension_table()
    order_check()
    failures = synthetic_demo()
    print("\n" + "=" * 78)
    print("STATUS")
    print("=" * 78)
    print("   W1C-02 remains OPEN. This probe explains the mechanism, scopes it to the")
    print("   affected dimensions, and demonstrates a classification that is")
    print("   count-compatible with today's output. It is NOT an implementation and no")
    print("   regression evidence for a fixed checker exists.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
