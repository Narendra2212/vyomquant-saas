"""Do the W1D-05 guards actually BITE? Mutation-test them against the real test functions.

PHASE 11B / W1D-05 — read-only. No database, no network, no file written except this
probe's own redirected stdout. It edits nothing: mutations are applied to the imported
test module's globals in memory and reverted in a `finally`.

WHY
---
`tests/test_snapshot_dimension_invariants.py` passes 48/48. That is necessary and not
sufficient: a suite of "there are no problems" assertions also passes when the thing
doing the checking is broken. The file carries negative controls, but those exercise the
EXTRACTOR HELPERS (`outer_order_by`, `select_columns`, `normalise`) rather than the guard
test functions themselves. A helper can be correct while the guard that consumes it fails
to assert on the result.

So this drives the REAL guard functions with the REAL dicts perturbed, one mutation at a
time, and requires each to raise `AssertionError`. A mutation that does not trip its
guard is a guard that does not guard.

Each mutation corresponds to a way a future change could weaken drift-checker
correctness:

  M1  a dimension's ORDER BY is removed entirely
  M2  a dimension's ORDER BY is narrowed to a non-unique prefix
  M3  a dimension's ORDER BY is reordered onto the wrong columns
  M4  a dimension's ORDER BY is replaced by one that exists only in a subquery
  M5  a new dimension is added with no key declaration
  M6  a key declaration is left behind for a removed dimension
  M7  a key is declared as None instead of WHOLE_ROW
  M8  a key width is declared wider than the SELECT list
  M9  a WHOLE_ROW dimension gains a descriptive column
  M10 the drift checker stops sharing the one dimension set
  M11 ADVISORY_DIMENSIONS names a dimension that does not exist
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("ENV", "testing")

import tests.test_snapshot_dimension_invariants as T   # noqa: E402
from scripts import verify_schema_drift as drift        # noqa: E402


@contextlib.contextmanager
def patched(**attrs):
    """Swap module globals, restore unconditionally."""
    saved = {k: getattr(T, k) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(T, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(T, k, v)


def expect_assertion(label: str, fn, *args) -> bool:
    """`fn(*args)` must raise AssertionError. Returns True when the guard bit."""
    try:
        fn(*args)
    except AssertionError:
        print(f"   [BITES ] {label}")
        return True
    except Exception as exc:                                   # noqa: BLE001
        print(f"   [ERROR ] {label}: raised {type(exc).__name__}: {exc}")
        return False
    print(f"   [SILENT] {label}  <-- the guard did NOT reject this mutation")
    return False


def main() -> int:
    print("=" * 78)
    print("W1D-05 GUARD MUTATION TEST")
    print("=" * 78)
    print(f"   guards under test : {T.__file__}")
    print(f"   dimensions        : {len(T.SNAPSHOT_QUERIES)}")
    print(f"   declared keys     : {len(T.COMPARISON_KEY)}\n")

    bit = 0
    total = 0

    def run(label, mutation, guard, *args):
        nonlocal bit, total
        total += 1
        with patched(**mutation):
            if expect_assertion(label, guard, *args):
                bit += 1

    print("-- Guard A: result-set ordering -----------------------------------------")

    # M1 -- ORDER BY removed outright.
    q = dict(T.SNAPSHOT_QUERIES)
    q["policies"] = "SELECT tablename, policyname, cmd FROM pg_policies"
    run("M1  policies: ORDER BY removed", {"SNAPSHOT_QUERIES": q},
        T.test_every_dimension_has_a_result_set_order_by)
    run("M1b policies: ORDER BY removed (key-coverage guard)", {"SNAPSHOT_QUERIES": q},
        T.test_order_by_covers_the_declared_key, "policies")

    # M2 -- narrowed to a non-unique prefix: tablename alone does not order policies.
    q = dict(T.SNAPSHOT_QUERIES)
    q["policies"] = ("SELECT tablename, policyname, cmd FROM pg_policies "
                     "ORDER BY tablename")
    run("M2  policies: ORDER BY narrowed to a non-unique prefix",
        {"SNAPSHOT_QUERIES": q}, T.test_order_by_covers_the_declared_key, "policies")

    # M3 -- right arity, wrong columns.
    q = dict(T.SNAPSHOT_QUERIES)
    q["policies"] = ("SELECT tablename, policyname, cmd FROM pg_policies "
                     "ORDER BY cmd, tablename")
    run("M3  policies: ORDER BY on the wrong columns",
        {"SNAPSHOT_QUERIES": q}, T.test_order_by_covers_the_declared_key, "policies")

    # M4 -- the only ORDER BY is inside a subquery. This is the disguise that matters:
    # two real dimensions legitimately contain subquery ORDER BY clauses.
    q = dict(T.SNAPSHOT_QUERIES)
    q["enum_types"] = """
        SELECT t.typname, array_to_string(ARRAY(
                   SELECT e.enumlabel FROM pg_enum e
                   WHERE e.enumtypid = t.oid ORDER BY e.enumsortorder
               ), ',')
        FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typtype = 'e'
    """
    run("M4  enum_types: result-set ORDER BY removed, subquery one left",
        {"SNAPSHOT_QUERIES": q}, T.test_every_dimension_has_a_result_set_order_by)

    print("\n-- Guard B: key declaration ---------------------------------------------")

    # M5 -- a new dimension with no declared key.
    q = dict(T.SNAPSHOT_QUERIES)
    q["a_new_dimension"] = "SELECT x, y FROM z ORDER BY x"
    run("M5  a new dimension with no key declaration", {"SNAPSHOT_QUERIES": q},
        T.test_every_dimension_declares_a_comparison_key)

    # M6 -- a stale declaration for a dimension that no longer exists.
    k = dict(T.COMPARISON_KEY)
    k["a_removed_dimension"] = 1
    run("M6  a stale key declaration", {"COMPARISON_KEY": k},
        T.test_every_dimension_declares_a_comparison_key)

    # M7 -- None instead of the WHOLE_ROW sentinel.
    k = dict(T.COMPARISON_KEY)
    k["privileges"] = None
    run("M7  privileges: key declared None instead of WHOLE_ROW",
        {"COMPARISON_KEY": k},
        T.test_a_declared_key_is_whole_row_or_a_valid_width, "privileges")

    # M8 -- width wider than the SELECT list.
    k = dict(T.COMPARISON_KEY)
    k["tables"] = 9
    run("M8  tables: key width 9 against 2 selected columns",
        {"COMPARISON_KEY": k},
        T.test_a_declared_key_is_whole_row_or_a_valid_width, "tables")

    # M9 -- a WHOLE_ROW dimension gains a descriptive column that is not ordered on.
    q = dict(T.SNAPSHOT_QUERIES)
    q["sequences"] = ("SELECT sequence_name, data_type FROM information_schema.sequences "
                      "WHERE sequence_schema = 'public' ORDER BY sequence_name")
    run("M9  sequences: WHOLE_ROW dimension gains an attribute column",
        {"SNAPSHOT_QUERIES": q}, T.test_whole_row_dimensions_really_have_no_attribute_columns)

    print("\n-- Surrounding assumptions ----------------------------------------------")

    # M10 -- the drift checker stops sharing the single dimension set.
    saved = drift.SNAPSHOT_QUERIES
    total += 1
    try:
        drift.SNAPSHOT_QUERIES = dict(saved)      # equal content, different object
        if expect_assertion("M10 verify_schema_drift redefines SNAPSHOT_QUERIES",
                            T.test_the_drift_checker_shares_the_one_dimension_set):
            bit += 1
    finally:
        drift.SNAPSHOT_QUERIES = saved

    # M11 -- ADVISORY_DIMENSIONS naming a non-existent dimension.
    total += 1
    with patched(ADVISORY_DIMENSIONS=frozenset({"alembic_version", "typo_dimension"})):
        if expect_assertion("M11 ADVISORY_DIMENSIONS contains a typo",
                            T.test_advisory_dimensions_are_real_dimensions):
            bit += 1

    print("\n" + "=" * 78)
    print("RESULT")
    print("=" * 78)
    print(f"   mutations applied : {total}")
    print(f"   guards bit        : {bit}")
    print(f"   silent failures   : {total - bit}")

    print("\n-- the real dicts are unchanged after all mutations ----------------------")
    import importlib
    fresh_q = importlib.import_module("scripts.verify_provisioning").SNAPSHOT_QUERIES
    print(f"   T.SNAPSHOT_QUERIES is the real object : "
          f"{T.SNAPSHOT_QUERIES is fresh_q}")
    print(f"   dimensions                            : {len(T.SNAPSHOT_QUERIES)}")
    print(f"   declared keys                         : {len(T.COMPARISON_KEY)}")
    print(f"   drift.SNAPSHOT_QUERIES restored       : "
          f"{drift.SNAPSHOT_QUERIES is fresh_q}")

    if bit != total:
        print("\n   !! AT LEAST ONE GUARD IS VACUOUS. It passes today but would not")
        print("      reject the change it exists to reject.")
        return 1
    print("\n   Every mutation was rejected by its guard. The guards are not vacuous.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
