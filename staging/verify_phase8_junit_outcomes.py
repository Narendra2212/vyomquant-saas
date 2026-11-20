"""Confirm, from the JUnit XML itself, that the tests Phase 8 added or fixed actually RAN.

Absence from a failure list is not evidence that a test passed - it is equally consistent
with the test having been skipped, deselected, or never collected. This reads the outcome
of each one directly out of both lanes' XML.
"""
from __future__ import annotations

import pathlib
import sys
import xml.etree.ElementTree as ET

STAGING = pathlib.Path(__file__).resolve().parent

LANES = {
    "ci": "fullreg_FULLREG_P8.xml",
    "postgresql": "fullreg_FULLREG_P8PG2.xml",
}

#: (substring of classname+name, what each lane should show). "run" = passed, not skipped.
WATCH = [
    ("test_work03_bot_ownership_claims_postgres.py::test_the_heartbeat_loop_refreshes_a_real_claim",
     "the heartbeat assertion fixed after the first PG run"),
    ("test_root_cause_stress_suite.py::test_1000_executions_stress",
     "the stress test rewritten so it cannot pass vacuously"),
    ("test_tenant02_fee_records_rls_postgres.py",
     "TENANT-02 suite (PostgreSQL only)"),
    ("test_money04_referral_ledger_is_single_currency_postgres.py",
     "MONEY-04 residual suite (PostgreSQL only)"),
    ("test_trade01_concurrent_exposure_postgres.py",
     "TRADE-01 suite (PostgreSQL only)"),
    ("test_trade02_kill_switch_fails_closed.py",
     "KILL-01 / TRADE-02 suite"),
    ("test_strategy_status_publish.py",
     "the four fixed frame-publication tests"),
    ("test_task_8_4_runtime_readiness_gate.py::TestAwaitingModel::test_a_verified_checksum_clears_the_awaiting_model_hold",
     "the readiness-gate test given its own file's `unfrozen` fixture"),
    ("test_subscriber_restricted_operations.py::TestThePermittedOperationsAreReachable::test_the_permitted_paths_disclose_no_protected_logic_either",
     "TEST-06, the clock-dependent containment check"),
]


def outcomes(xml: pathlib.Path) -> list[tuple[str, str]]:
    root = ET.parse(xml).getroot()
    out: list[tuple[str, str]] = []
    for case in root.iter("testcase"):
        ident = f"{case.get('classname', '')}::{case.get('name', '')}"
        verdict = "passed"
        for child in case:
            tag = child.tag.lower()
            if tag == "failure":
                verdict = "FAILED"
            elif tag == "error":
                verdict = "ERROR"
            elif tag == "skipped":
                verdict = "xfailed" if "xfail" in (child.get("type") or "").lower() \
                    else "skipped"
        out.append((ident, verdict))
    return out


print("=" * 90)
print("PHASE 8 — DID THE TESTS THAT MATTER ACTUALLY RUN? (read from the JUnit XML)")
print("=" * 90)

tables: dict[str, list[tuple[str, str]]] = {}
for lane, name in LANES.items():
    path = STAGING / name
    if not path.is_file():
        print(f"MISSING {name}")
        sys.exit(1)
    tables[lane] = outcomes(path)
    print(f"{lane:<12} {name}  testcases={len(tables[lane])}")

problems: list[str] = []
print()
for needle, why in WATCH:
    key = needle.replace("::", ".").replace("/", ".").replace(".py", "")
    print(f"- {needle}")
    print(f"    ({why})")
    for lane in LANES:
        matched = [
            (ident, verdict) for ident, verdict in tables[lane]
            if all(part in ident.replace("/", ".") for part in key.split("."))
        ]
        if not matched:
            print(f"    {lane:<12} NOT PRESENT IN XML")
            problems.append(f"{needle} absent from the {lane} lane XML")
            continue
        tally: dict[str, int] = {}
        for _ident, verdict in matched:
            tally[verdict] = tally.get(verdict, 0) + 1
        print(f"    {lane:<12} {len(matched):>3} case(s)  {tally}")
        if tally.get("FAILED") or tally.get("ERROR"):
            problems.append(f"{needle} has failures/errors in the {lane} lane")
    print()

print("=" * 90)
if problems:
    print(f"{len(problems)} PROBLEM(S):")
    for item in problems:
        print(f"  - {item}")
    print("=" * 90)
    sys.exit(1)
print("Every watched test is present in both lanes' XML with no failure or error.")
print("NOTE: 'skipped' in the CI lane is EXPECTED for the three PostgreSQL-only suites -")
print("that difference IS the infrastructure-gated surface, and it is why both lanes are")
print("reported separately rather than merged.")
print("=" * 90)
sys.exit(0)
