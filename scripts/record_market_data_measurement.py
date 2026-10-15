#!/usr/bin/env python3
"""RT-04 operator tool: run the real market-data latency experiment and PERSIST the result.

WHAT THIS DOES NOT DO
    It does not measure anything itself, and running it in a sandbox with no real exchange
    reachability (exactly where this tool was written) will report BLOCKED, honestly,
    because nothing was reachable to measure. This only wires together two pieces that
    already existed separately:

    * ``tests/perf/test_market_data_latency.py::run_experiment`` -- the real experiment
      design.md prescribes (24h window, both candidates, the correctness floor applied).
      It already measures whatever it can and writes a prose report. It never persisted
      a machine-readable result anywhere.
    * ``backend/market_data_measurement_store`` -- somewhere for a machine-readable result
      to live, added alongside this script. Nothing wrote to it before this script existed.

    This script is the missing third piece: run the experiment, and for every candidate it
    actually measured (not the unmeasured ones -- recording "unmeasured" teaches the store
    nothing `paper_market_feed.measurement_for`'s own default does not already answer),
    persist it. `routers/paper_trading._recorded_measurements()` reads what this writes.

HOW AN OPERATOR USES THIS FOR REAL
    Run from an environment with real outbound network access to the exchange, Redis and
    the MDS process up, against production or a staging environment that mirrors it:

        AERORA_MARKET_DATA_LATENCY_EXPERIMENT=1 \\
        python scripts/record_market_data_measurement.py --recorded-by you@example.com

    `AERORA_MARKET_DATA_LATENCY_SECONDS` is left UNSET so the prescribed 86400s (24h) window
    runs -- see `CorrectnessFloor.min_completeness_sample_bars`'s own docstring for why a
    shorter smoke run cannot admit a source at the 99.99% threshold. This process must stay
    running for that whole window; it is not a request/response call.

    Re-run it periodically (a feed's behaviour is not provably permanent): each run records
    a NEW row rather than overwriting the last one (migration 039 is append-only), and
    `latest_measurements()` always reads the newest.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


async def _main(recorded_by: str, report_path: str | None) -> int:
    from backend_app.backend.market_data_measurement_store import record_measurement
    from tests.perf.test_market_data_latency import run_experiment

    print("Running the market-data latency experiment. With the prescribed window "
          "(AERORA_MARKET_DATA_LATENCY_SECONDS unset), this runs for ~24h and does not "
          "return until it is done.")
    outcome = run_experiment(report_path=report_path)
    decision = outcome["decision"]
    print(f"Report written to: {outcome['report_path']}")
    print(f"Decision: {decision.outcome} ({decision.rule}) - {decision.reason}")

    recorded = []
    for measurement in decision.measurements:
        if not measurement.measured:
            print(f"  {measurement.source}: NOT measured ({measurement.unavailable_reason}) "
                  f"- not recorded; the store's absence of this source already means this.")
            continue
        await record_measurement(measurement, recorded_by=recorded_by)
        print(f"  {measurement.source}: measured, completeness={measurement.completeness}, "
              f"recorded to the store.")
        recorded.append(measurement.source)

    if not recorded:
        print("\nNothing was recorded: no candidate was measured this run. Paper sessions "
              "remain refused with PAPER_MARKET_DATA_UNAVAILABLE until one is.")
        return 1

    if decision.blocked:
        print(f"\n{len(recorded)} candidate(s) recorded, but the decision is still BLOCKED "
              f"({decision.reason}). Recording a measurement that fails the floor is honest "
              f"evidence, not a fix -- paper sessions remain refused.")
        return 1

    print(f"\nSelected source: {decision.selected}. Paper sessions can now start.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recorded-by", required=True,
        help="Who ran this (an email or identifier), stored on the row for audit.",
    )
    parser.add_argument(
        "--report-path", default=None,
        help="Where to write the prose decision report. Defaults to the experiment's own "
             "report path (reports/market_data_latency_decision.md).",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.recorded_by, args.report_path)))
