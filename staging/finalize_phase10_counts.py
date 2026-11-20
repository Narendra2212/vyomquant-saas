"""Phase 10 FINAL full-regression counts for BOTH lanes, recalculated and cross-checked.

IMPORTS the proven parsers rather than copying them: `lane()` from `finalize_phase7_counts`
and the UTF-16/segment-decode normaliser from `finalize_phase8_counts`. Re-implementing
either would let the measuring instrument drift from the one that produced the Phase 5-9
figures.

The CI lane (FULLREG_P10) is written through `Tee-Object` (UTF-16LE on PowerShell 5.1), so
it needs the Phase 8 normaliser before `lane()` can read it. The PG lane (FULLREG_P10PG) is
written with `Add-Content -Encoding ascii`, read directly.

The Phase 9 figures are a HISTORICAL baseline, not a target. The counts here are recalculated
from the Phase 10 runs.

Run:  .venv\\Scripts\\python.exe staging\\finalize_phase10_counts.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from finalize_phase7_counts import lane  # noqa: E402
from finalize_phase8_counts import ACCOUNTING_CONVENTION, normalise_encoding  # noqa: E402

STAGING = pathlib.Path(__file__).resolve().parent
OUT = STAGING / "fullreg_phase10_counts.json"

CI_NONCE = "FULLREG_P10"
PG_NONCE = "FULLREG_P10PG"

#: Phase 9 FINAL authoritative pair, as handed to this phase. Historical baselines only.
BASELINE_PHASE_9 = {
    "ci": {
        "collected": 14158, "passed": 13564, "failed": 1, "errors": 0,
        "skipped": 584, "xfailed": 6, "xpassed": 3, "nonce": "FULLREG_P9FINAL",
    },
    "postgresql": {
        "collected": 14158, "passed": 14084, "failed": 1, "errors": 0,
        "skipped": 64, "xfailed": 6, "xpassed": 3, "nonce": "FULLREG_P9PGFINAL",
    },
}


def main() -> int:
    for nonce in (CI_NONCE, PG_NONCE):
        note = normalise_encoding(nonce)
        if note:
            print(note)

    ci = lane(
        CI_NONCE,
        "CI lane (AUTHORITATIVE, Phase 10)",
        "No DATABASE_URL and no AERORA_TEST_DATABASE_URL. Reproduces 01-pr-check.yml's "
        "'Run Unit Tests' job, comparable to the Phase 5-9 baselines. Every PostgreSQL-only "
        "suite SKIPS here. Run on the fully-remediated Phase 10 tree: the referral_obligations "
        "sweep-coverage fix, the TEST-05 deterministic @example boundaries, the CONN-01 "
        "store_keys eviction, the QUOTA-01 metered-reservation tests, the TENANT-01 "
        "credential-vault isolation tests, the referral payout payout_method fix, the fee_pct "
        "nullable model change + migration 031, and the reconciliation startup assessment.",
    )
    pg = lane(
        PG_NONCE,
        "PostgreSQL lane (AUTHORITATIVE, Phase 10)",
        "AERORA_TEST_DATABASE_URL set from staging/pg_dsn.txt against a LOCAL PostgreSQL "
        "16.2 with migrations through 031 applied, so the PostgreSQL-only suites execute. "
        "LOCAL-substrate evidence; no production database was connected to.",
    )

    record = {
        "lanes": {"ci": ci, "postgresql": pg},
        "accounting_convention": ACCOUNTING_CONVENTION,
        "why_two_lanes": (
            "Reporting only the CI lane would report a pass on tests that never ran, "
            "because that lane skips every PostgreSQL-only suite by design. Reporting only "
            "the PostgreSQL lane would not be comparable to the earlier baselines. The "
            "difference between the two IS the infrastructure-gated surface; both are kept."
        ),
        "baseline_phase_9": BASELINE_PHASE_9,
        "substrate": (
            "LOCAL PostgreSQL 16.2 via pgserver. No production database was connected to in "
            "any phase. Live trading remained OFF and NOT CERTIFIED; no venue sandbox was "
            "reachable, so TRADING PAPER stays NOT CLASSIFIED."
        ),
    }

    for key in ("ci", "postgresql"):
        current = record["lanes"][key]
        base = BASELINE_PHASE_9[key]
        if not current.get("collected"):
            continue
        record[f"delta_vs_phase_9_{key}_lane"] = {
            field: current[field] - base[field]
            for field in ("collected", "passed", "failed", "skipped", "xfailed", "xpassed")
            if field in current and field in base
        }

    if record["lanes"]["ci"].get("collected") and record["lanes"]["postgresql"].get("collected"):
        record["infrastructure_gated_surface"] = {
            "skipped_in_ci_lane": ci["skipped"],
            "skipped_in_postgresql_lane": pg["skipped"],
            "skips_converted_to_runs": ci["skipped"] - pg["skipped"],
        }

    outstanding = {
        name: {"failed": data.get("failed"), "failed_ids": data.get("failed_ids", [])}
        for name, data in record["lanes"].items()
        if data.get("failed")
    }
    record["lanes_with_outstanding_failures"] = outstanding
    record["fully_green"] = (
        bool(record["lanes"]["ci"].get("collected"))
        and bool(record["lanes"]["postgresql"].get("collected"))
        and not outstanding
    )

    OUT.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\nWROTE {OUT}")
    print(f"FULLY_GREEN = {record['fully_green']}")
    for name, info in outstanding.items():
        print(f"  {name} lane still reports {info['failed']} failure(s)")
        for fid in info["failed_ids"]:
            print(f"    {fid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
