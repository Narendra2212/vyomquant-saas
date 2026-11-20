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
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

#: RT-04. Everything this experiment needs beyond network access already exists in the
#: production account; it was simply never wired to the operator's shell, so running the
#: experiment meant hand-exporting four values correctly first. That friction is why the
#: measurement had not been started -- see ``--resolve-config-from-aws``.
_REGION = "ap-southeast-1"
_API_TASK_FAMILY = "vyomquant-api"
#: Secrets Manager ids for the values that must never be typed or echoed.
_SECRET_IDS = {
    "SUPABASE_SERVICE_ROLE_KEY": "vyomquant/production/supabase_service_role_key",
    "REDIS_URL": "vyomquant/production/redis_url",
}
#: Non-secret settings read from the deployed task definition, so the experiment measures
#: against the same configuration production runs rather than a guess.
_TASKDEF_ENV_KEYS = ("SUPABASE_URL", "EXCHANGE_ID", "DEFAULT_EXCHANGE")


def _aws(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["aws", *args, "--region", _REGION],
                          capture_output=True, text=True)


def resolve_config_from_aws() -> list[str]:
    """Populate os.environ from the production account. Returns the names it set.

    WHY. Candidate B needs only exchange reachability, but PERSISTING the result needs the
    Supabase service-role credential, and Candidate A needs REDIS_URL. Neither is something
    an operator should paste into a terminal. Both are already in Secrets Manager, and the
    non-secret settings are already on the deployed task definition, so this reads them from
    the authoritative place instead.

    NOTHING IS PRINTED BUT THE NAMES. A value already present in the environment is left
    alone, so an operator can still override any single setting deliberately.
    """
    resolved: list[str] = []

    out = _aws("ecs", "describe-task-definition", "--task-definition", _API_TASK_FAMILY,
               "--query", "taskDefinition.containerDefinitions[0].environment", "--output",
               "json")
    if out.returncode == 0:
        try:
            for entry in json.loads(out.stdout):
                name = entry.get("name")
                if name in _TASKDEF_ENV_KEYS and not os.environ.get(name):
                    os.environ[name] = entry.get("value") or ""
                    resolved.append(name)
        except (ValueError, TypeError):
            print("could not parse the task definition environment; continuing without it",
                  file=sys.stderr)
    else:
        print(f"could not read task definition {_API_TASK_FAMILY}: "
              f"{out.stderr.strip()[:160]}", file=sys.stderr)

    for name, secret_id in _SECRET_IDS.items():
        if os.environ.get(name):
            continue
        out = _aws("secretsmanager", "get-secret-value", "--secret-id", secret_id,
                   "--query", "SecretString", "--output", "text")
        if out.returncode != 0:
            print(f"could not read {secret_id} ({name}): {out.stderr.strip()[:140]}",
                  file=sys.stderr)
            continue
        raw = out.stdout.strip()
        if raw.startswith("{"):
            try:
                blob = json.loads(raw)
                raw = blob.get(name) or blob.get(name.lower()) or next(iter(blob.values()))
            except (ValueError, StopIteration):
                pass
        os.environ[name] = raw
        resolved.append(name)

    return resolved


async def _persist(measurements, recorded_by: str) -> list[str]:
    """Record every measured candidate. Kept separate from the experiment deliberately --
    see the comment in :func:`_main` about nested event loops."""
    from backend_app.backend.market_data_measurement_store import record_measurement

    recorded: list[str] = []
    for measurement in measurements:
        if not measurement.measured:
            print(f"  {measurement.source}: NOT measured "
                  f"({measurement.unavailable_reason}) - not recorded; the store's "
                  f"absence of this source already means this.")
            continue
        await record_measurement(measurement, recorded_by=recorded_by)
        print(f"  {measurement.source}: measured, completeness={measurement.completeness}, "
              f"recorded to the store.")
        recorded.append(measurement.source)
    return recorded


def _main(recorded_by: str, report_path: str | None) -> int:
    """Run the experiment, then persist what it measured.

    THIS FUNCTION IS DELIBERATELY SYNCHRONOUS, AND THAT IS THE WHOLE POINT.
    ------------------------------------------------------------------------
    It used to be ``async def``, invoked as ``asyncio.run(_main(...))``, with
    ``run_experiment()`` called from inside it. ``run_experiment`` is synchronous but drives
    its own coroutines with ``asyncio.run()`` internally -- at
    ``observe_ingest_processing_cost`` (tests/perf/test_market_data_latency.py:616) and in
    ``_measure_candidate_b``'s poll loop -- and ``asyncio.run()`` refuses to start a loop
    inside a running one:

        RuntimeError: asyncio.run() cannot be called from a running event loop

    So this script raised before it ever reached the persistence step, and had therefore
    NEVER successfully recorded a measurement. That mattered far more than an ordinary bug:
    the prescribed window is ~24h and the correctness floor needs ~7 days of it, so the
    failure would have been discovered at the END of a multi-day run, with nothing
    persisted and the whole window wasted. Found by running a 60-second window first, which
    is why a smoke run before a real one is worth the minute it costs.

    The fix is structural, not a workaround: the experiment runs here, at top level, with no
    loop running, and only the persistence -- which is genuinely async, because
    ``record_measurement`` is -- gets its own ``asyncio.run``.
    """
    from tests.perf.test_market_data_latency import run_experiment

    print("Running the market-data latency experiment. With the prescribed window "
          "(AERORA_MARKET_DATA_LATENCY_SECONDS unset), this runs for ~24h and does not "
          "return until it is done.")
    outcome = run_experiment(report_path=report_path)
    decision = outcome["decision"]
    print(f"Report written to: {outcome['report_path']}")
    print(f"Decision: {decision.outcome} ({decision.rule}) - {decision.reason}")

    recorded = asyncio.run(_persist(decision.measurements, recorded_by))

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
    parser.add_argument(
        "--resolve-config-from-aws", action="store_true",
        help="Read SUPABASE_URL/EXCHANGE_ID from the deployed vyomquant-api task "
             "definition and SUPABASE_SERVICE_ROLE_KEY/REDIS_URL from Secrets Manager, "
             "instead of requiring them to be exported by hand. Only names are printed, "
             "never values, and an already-set variable is left alone. Requires AWS "
             "credentials with ecs:DescribeTaskDefinition and "
             "secretsmanager:GetSecretValue.",
    )
    args = parser.parse_args()

    if args.resolve_config_from_aws:
        names = resolve_config_from_aws()
        print(f"resolved from AWS: {', '.join(names) if names else '(nothing new)'}")
        missing = [n for n in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
                   if not os.environ.get(n)]
        if missing:
            print(f"\nWARNING: {', '.join(missing)} still unset. The experiment will "
                  f"still MEASURE (Candidate B needs only exchange reachability), but "
                  f"PERSISTING the result will fail -- and discovering that at the end of "
                  f"a multi-day window would waste the whole run. Fix this before "
                  f"starting the prescribed window.", file=sys.stderr)

    if not os.environ.get("AERORA_MARKET_DATA_LATENCY_EXPERIMENT"):
        print("\nAERORA_MARKET_DATA_LATENCY_EXPERIMENT is not set, so the measuring code "
              "refuses to run and every candidate will come back unmeasured. Set it to 1.",
              file=sys.stderr)

    # NOT asyncio.run(_main(...)) -- see _main's docstring. run_experiment() drives its own
    # event loops and cannot be called from inside one.
    sys.exit(_main(args.recorded_by, args.report_path))
