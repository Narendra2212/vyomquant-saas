"""Run every database security / integrity probe and report one table of results.

The launch readiness report cites these individually; this runs the set so that
"all probes exit 0" is a single reproducible claim rather than nine separate ones.

Exit 0 -> every probe exited 0.
Exit 1 -> at least one did not. The failing probe's own output is printed in full.
"""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dsn import redact, resolve_dsn  # noqa: E402

PROBES = [
    ("exposure_check.py", [], "per-customer tables with RLS switched OFF"),
    ("permissive_policy_probe.py", [], "RLS ON but policy predicate is TRUE"),
    ("truncate_probe.py", [], "the privileges row level security cannot filter"),
    ("billing_exposure_probe.py", [], "the 22 original billing exposures"),
    ("library_ratings_probe.py", [], "library_ratings documented write paths"),
    ("anon_read_sweep.py", ["anon"], "behavioural: anon reads a seeded row"),
    ("anon_read_sweep.py", ["authenticated"],
     "behavioural: another customer reads a seeded row"),
    ("overlapping_policy_detail.py", [],
     "overlapping permissive policies, predicate by predicate"),
    ("identity_type_matrix.py", [], "tenant/user identity column types and their FKs"),
    ("which_referral_funcs.py", [], "which referral_wallets shape the functions write"),
    ("verify_live_trading_off.py", [], "live trading refused on every path"),
    ("verify_schema.py", [], "23 structural schema checks"),
]


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)

    # Resolved once and passed down explicitly, so every probe in the set is guaranteed
    # to be looking at the same database. Letting each one resolve independently would
    # make a mixed result possible, and a mixed result here would be indistinguishable
    # from a real finding.
    dsn = resolve_dsn(announce=False)
    env = {**os.environ, "AERORA_TEST_DATABASE_URL": dsn}

    # Printed before any result, because "all 10 probes exited 0" is only meaningful
    # alongside which database they ran against. A stale AERORA_TEST_DATABASE_URL
    # inherited from a parent shell beat the DSN file during this work; had it pointed at
    # a different live database the whole set would have passed against the wrong target.
    print(f"target: {redact(dsn)}")
    print()

    results: list[tuple[str, int, str, str]] = []
    for script, args, description in PROBES:
        path = os.path.join(here, script)
        if not os.path.exists(path):
            results.append((f"{script} {' '.join(args)}".strip(), -1, description,
                            "SCRIPT NOT FOUND"))
            continue
        proc = subprocess.run(
            [sys.executable, path, *args],
            cwd=root, env=env, capture_output=True, text=True, timeout=1800,
        )
        label = f"{script} {' '.join(args)}".strip()
        results.append((label, proc.returncode, description,
                        proc.stdout + proc.stderr))

    print("=" * 78)
    print("DATABASE PROBE SET")
    print("=" * 78)
    width = max(len(r[0]) for r in results)
    for label, rc, description, _ in results:
        verdict = "PASS" if rc == 0 else f"FAIL({rc})"
        print(f"  {verdict:<8} {label:<{width}}  {description}")

    failed = [r for r in results if r[1] != 0]
    if failed:
        for label, rc, _, output in failed:
            print()
            print("-" * 78)
            print(f"OUTPUT OF {label} (exit {rc})")
            print("-" * 78)
            print(output.rstrip())
        print()
        print(f"SUMMARY: {len(failed)} of {len(results)} probe(s) FAILED")
        return 1

    print()
    print(f"SUMMARY: all {len(results)} probes exited 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
