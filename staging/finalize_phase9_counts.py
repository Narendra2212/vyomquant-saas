"""Phase 9 FINAL full-regression counts for BOTH lanes, recalculated and cross-checked.

IMPORTS the proven parsers rather than copying them: `lane()` (with `parse_summary`,
`parse_collected`, `parse_junit`, `parse_failed_ids`) from `finalize_phase7_counts`, and the
UTF-16/segment-decode normaliser from `finalize_phase8_counts`. Re-implementing either would
let the measuring instrument drift from the one that produced the Phase 5/6/7/8 figures.

Both imported modules guard `main()` behind `__name__ == "__main__"`, so importing them is
side-effect free apart from module constants.

THE CI LANE (FULLREG_P9FINAL) is written through `Tee-Object`, which on PowerShell 5.1 is
UTF-16LE with no -Encoding parameter, so it needs the Phase 8 normaliser before `lane()` can
read it. THE PG LANE (FULLREG_P9PGFINAL) is written with `Add-Content -Encoding ascii`, which
`lane()` reads directly; the normaliser is a no-op there but is run anyway for symmetry.

THE PHASE 8 FIGURES ARE A HISTORICAL BASELINE, NOT A TARGET -- recorded for the delta only.
The counts here are recalculated from the FINAL Phase 9 runs.

Run:  .venv\\Scripts\\python.exe staging\\finalize_phase9_counts.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from finalize_phase7_counts import lane  # noqa: E402
from finalize_phase8_counts import ACCOUNTING_CONVENTION, normalise_encoding  # noqa: E402

STAGING = pathlib.Path(__file__).resolve().parent
OUT = STAGING / "fullreg_phase9_counts.json"

CI_NONCE = "FULLREG_P9FINAL"
PG_NONCE = "FULLREG_P9PGFINAL"

#: Phase 8's authoritative pair, as handed to this phase. Historical baselines only.
BASELINE_PHASE_8 = {
    "ci": {
        "collected": 13997, "passed": 13468, "failed": 2, "errors": 0,
        "skipped": 518, "xfailed": 6, "xpassed": 3, "nonce": "FULLREG_P8",
    },
    "postgresql": {
        "collected": 13997, "passed": 13922, "failed": 2, "errors": 0,
        "skipped": 64, "xfailed": 6, "xpassed": 3, "nonce": "FULLREG_P8PG2",
    },
}

#: The intermediate Phase 9 CI run, kept because it is the run that FOUND the twelve
#: collateral failures (the F821 bug, the SessionLocal module-ref bypass, the missing
#: _Repo.mark_submitted, the stale algo22.db schema, the mark_submitted absent-row
#: asymmetry, and the two baselines that legitimately shifted). All twelve were fixed or
#: re-recorded after it, so its figures no longer describe HEAD.
INTERMEDIATE_CI_NONCE = "FULLREG_P9"


def main() -> int:
    for nonce in (CI_NONCE, PG_NONCE, INTERMEDIATE_CI_NONCE):
        note = normalise_encoding(nonce)
        if note:
            print(note)

    ci = lane(
        CI_NONCE,
        "CI lane (AUTHORITATIVE, Phase 9 FINAL)",
        "No DATABASE_URL and no AERORA_TEST_DATABASE_URL. Reproduces 01-pr-check.yml's "
        "'Run Unit Tests' job, so the figures stay comparable to the Phase 5/6/7/8 "
        "baselines. Every PostgreSQL-only suite SKIPS here. Run on the fully-remediated "
        "Phase 9 tree: migrations 028/029/030, the KILL-02/03/04 authoritative-halt work, "
        "MONEY-06/07, TRADE-07/11/12, FEE-01, ML-01 reclassification, TEST-06, the bandit "
        "gate rewrite, the 10 collateral fixes made after the intermediate FULLREG_P9 run, "
        "and the 2 sanctioned baseline re-records (auth_admin_role, live_order_path).",
    )
    pg = lane(
        PG_NONCE,
        "PostgreSQL lane (AUTHORITATIVE, Phase 9 FINAL)",
        "AERORA_TEST_DATABASE_URL set from staging/pg_dsn.txt against a LOCAL PostgreSQL "
        "16.2 with migrations through 030 applied, so the PostgreSQL-only suites actually "
        "execute rather than skipping. This is LOCAL-SUBSTRATE evidence; no production "
        "database was connected to in any phase of this audit.",
    )
    ci_pre = lane(
        INTERMEDIATE_CI_NONCE,
        "CI lane (intermediate, pre-collateral-fix)",
        "The first Phase 9 CI-lane run. It FOUND the twelve collateral failures that the "
        "Phase 9 product changes introduced into previously-green suites; all twelve were "
        "fixed or re-recorded afterwards, so its figures predate HEAD and are kept only as "
        "the record of what was found.",
    )

    record = {
        "lanes": {"ci": ci, "postgresql": pg},
        "intermediate_lanes": {"ci_first_run": ci_pre},
        "accounting_convention": ACCOUNTING_CONVENTION,
        "why_two_lanes": (
            "Reporting only the CI lane would report a pass on tests that never ran, "
            "because that lane skips every PostgreSQL-only suite by design. Reporting "
            "only the PostgreSQL lane would not be comparable to the earlier baselines. "
            "The difference between the two IS the infrastructure-gated surface, so both "
            "result sets are preserved separately and neither is merged into the other."
        ),
        "baseline_phase_8": BASELINE_PHASE_8,
        "substrate": (
            "LOCAL PostgreSQL 16.2 via pgserver. No production database was connected to "
            "in any phase of this audit, so every database figure here is "
            "local-substrate evidence. Live trading remained OFF and NOT CERTIFIED; no "
            "venue sandbox was reachable with a safe credential, so TRADING PAPER stays "
            "NOT CLASSIFIED."
        ),
    }

    for key in ("ci", "postgresql"):
        current = record["lanes"][key]
        base = BASELINE_PHASE_8[key]
        if not current.get("collected"):
            continue
        record[f"delta_vs_phase_8_{key}_lane"] = {
            field: current[field] - base[field]
            for field in ("collected", "passed", "failed", "skipped", "xfailed", "xpassed")
            if field in current and field in base
        }

    if record["lanes"]["ci"].get("collected") and record["lanes"]["postgresql"].get("collected"):
        record["infrastructure_gated_surface"] = {
            "skipped_in_ci_lane": ci["skipped"],
            "skipped_in_postgresql_lane": pg["skipped"],
            "skips_converted_to_runs": ci["skipped"] - pg["skipped"],
            "note": "The reduction is the number of tests the CI lane could not measure "
                    "and the PostgreSQL lane did. A test that still skips in the "
                    "PostgreSQL lane needs something else - Redis, a running HTTP "
                    "server, a built frontend bundle - named in the top skip reasons.",
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
