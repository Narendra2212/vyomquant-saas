"""W1D-15 probe -- does the DELIVERED classifier satisfy the ledger's compatibility invariant?

`staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` section 3.5 states the compatibility
invariant that makes the 4-way classification a safe drop-in:

    missing_today = missing + modified
    extra_today   = added   + modified

and section 3.6 test 6 (`test_counts_reduce_to_the_legacy_output`) requires a regression test
asserting it "for every case".

W1D-13 delivered the classifier but NO test asserts this arithmetic. This probe measures
whether the invariant actually holds against the delivered `classify_dimension()`, so the
W1D-15 acceptance matrix can state the gap precisely instead of inferring it.

The legacy ("today") counts are reconstructed in BOTH plausible readings of "compares whole
tuples", because the pre-W1D-13 source is untracked and has no git history to recover:

  * SET reading      -- set(expected) - set(actual), the classic whole-tuple membership diff
  * MULTISET reading -- Counter(expected) - Counter(actual), which also sees duplicate counts

READ-ONLY. No database, no network, no file written except this probe's own stdout.
"""

from __future__ import annotations

import collections
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts.verify_provisioning import classify_dimension  # noqa: E402

# Fixtures copied from tests/test_drift_classification.py so the probe exercises the same
# rows the delivered suite does.
POLICY = ("fee_records", "fee_select", "PERMISSIVE", "{authenticated}", "SELECT", "(t = u)", "")
POLICY_CHANGED = ("fee_records", "fee_select", "PERMISSIVE", "{authenticated}", "SELECT",
                  "(t = u)", "(a = b)")
POLICY_COLLIDES = ("fee_records", "fee_select", "RESTRICTIVE", "{anon}", "DELETE", "(x = y)", "")
GRANT = ("fee_records", "authenticated", "SELECT")
GRANT_OTHER = ("fee_records", "anon", "SELECT")
MIGRATION = ("001_init.sql", "abc123", "step1")
MIGRATION_CHANGED = ("001_init.sql", "def456", "step1")
COL_A = ("fee_records", "id", "uuid", "NO", None, None, None, None, None)
COL_B = ("fee_records", "amount", "numeric", "YES", None, None, None, None, None)

CASES = [
    ("modified attribute",      "policies",            [POLICY],                   [POLICY_CHANGED]),
    ("genuine removal",         "policies",            [POLICY],                   []),
    ("genuine addition",        "policies",            [],                         [POLICY]),
    ("whole-row change",        "privileges",          [GRANT],                    [GRANT_OTHER]),
    ("whole-row removal",       "privileges",          [GRANT],                    []),
    ("duplicate multiplicity baseline", "privileges",  [GRANT, GRANT],             [GRANT]),
    ("duplicate multiplicity live",     "privileges",  [GRANT],                    [GRANT, GRANT]),
    ("ambiguous key",           "policies",            [POLICY, POLICY_COLLIDES],  [POLICY]),
    ("advisory checksum change", "recorded_migrations", [MIGRATION],               [MIGRATION_CHANGED]),
    ("reordered identical",     "columns",             [COL_A, COL_B],             [COL_B, COL_A]),
    ("identical",               "policies",            [POLICY],                   [POLICY]),
    ("both empty",              "policies",            [],                         []),
]


def legacy_set(expected, actual):
    """The classic whole-tuple membership diff."""
    missing = [r for r in expected if r not in actual]
    extra = [r for r in actual if r not in expected]
    return len(missing), len(extra)


def legacy_multiset(expected, actual):
    """Whole-tuple diff that also counts duplicates."""
    e = collections.Counter(expected)
    a = collections.Counter(actual)
    return sum((e - a).values()), sum((a - e).values())


def main() -> int:
    print("W1D-15 compatibility-invariant probe")
    print("ledger 3.5:  missing_today == missing + modified   and   extra_today == added + modified")
    print("=" * 108)
    header = (f"{'case':<36} {'verdict':<13} "
              f"{'new m/mod/add/dup/amb':<23} {'SET':<9} {'MULTISET':<10} {'SET inv':<8} {'MS inv'}")
    print(header)
    print("-" * 108)

    set_holds = set_fails = ms_holds = ms_fails = 0

    for label, dim, expected, actual in CASES:
        r = classify_dimension(dim, expected, actual)
        n_missing, n_modified, n_added = len(r.missing), len(r.modified), len(r.added)
        n_dup, n_amb = len(r.duplicated), len(r.ambiguous)

        s_missing, s_extra = legacy_set(expected, actual)
        m_missing, m_extra = legacy_multiset(expected, actual)

        set_ok = (s_missing == n_missing + n_modified) and (s_extra == n_added + n_modified)
        ms_ok = (m_missing == n_missing + n_modified) and (m_extra == n_added + n_modified)

        set_holds, set_fails = (set_holds + 1, set_fails) if set_ok else (set_holds, set_fails + 1)
        ms_holds, ms_fails = (ms_holds + 1, ms_fails) if ms_ok else (ms_holds, ms_fails + 1)

        print(f"{label:<36} {r.verdict:<13} "
              f"{f'{n_missing}/{n_modified}/{n_added}/{n_dup}/{n_amb}':<23} "
              f"{f'{s_missing}m {s_extra}x':<9} {f'{m_missing}m {m_extra}x':<10} "
              f"{('HOLDS' if set_ok else 'BREAKS'):<8} {'HOLDS' if ms_ok else 'BREAKS'}")

    print("-" * 108)
    print(f"SET reading:      {set_holds} hold, {set_fails} break")
    print(f"MULTISET reading: {ms_holds} hold, {ms_fails} break")
    print()
    print("Interpretation: the ledger invariant is a TWO-category identity (missing/extra). The")
    print("delivered classifier adds `duplicated` and `ambiguous`, which that identity cannot")
    print("express. Any case listed BREAKS is a case where ledger 3.6 test 6, as literally")
    print("written, could not pass -- so the missing test is not merely unwritten, it needs a")
    print("scope qualification before it can be written. That qualification is a decision.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
