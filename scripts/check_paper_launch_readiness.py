#!/usr/bin/env python3
"""Can a Paper_Session start in production today? Read-only, and the answer is a chain.

WHY THIS EXISTS
===============
paper_sessions holds 0 rows in production and it was not obvious why. Tracing the start
path explains it, and the explanation is a DEPENDENCY CHAIN with a multi-day item in the
middle -- which matters a great deal to anyone planning a launch date, and which no
existing script reports.

    routers/paper_trading.start_session
      -> _recorded_measurements()                  reads market_data_source_measurements
      -> paper_session_service.start_session
        -> paper_market_feed.open_feed(measurements=...)
          -> market_data_latency.choose_market_data_source
            -> evaluate_correctness_floor

`open_feed` REFUSES when no measurement is recorded, by design: Requirement 14.4 says a
session whose market-data source is unmeasured "SHALL refuse to start and SHALL report a
market-data-unavailable condition, rather than starting on an unvalidated feed". So the
refusal is correct behaviour, not a bug -- but it is also an absolute gate on the paper
product, and the measurement it wants is not cheap:

    CorrectnessFloor.min_completeness = 0.9999
    CorrectnessFloor.min_completeness_sample_bars -> derived from that, ~10,000 bars

At a 1-minute timeframe that is roughly a WEEK of continuous observation of a real feed.
And the feed being measured is the one `mds/main.py` publishes, so mds has to be running
for the measurement to be collectable at all.

    fresh mds image -> mds deployed -> ~1 week of bars -> record measurement
        -> paper sessions can start

This script reports where that chain currently stands, so the gate is visible on a
dashboard rather than discovered by a user getting a 409.

Read-only: SELECTs only, and the session is opened READ ONLY.

    python scripts/check_paper_launch_readiness.py
"""
from __future__ import annotations

import json
import subprocess
import sys

SECRET_ID = "vyomquant/production/database_url"
REGION = "ap-southeast-1"
TABLE = "market_data_source_measurements"


def read_dsn() -> str:
    out = subprocess.run(
        ["aws", "secretsmanager", "get-secret-value", "--secret-id", SECRET_ID,
         "--region", REGION, "--query", "SecretString", "--output", "text"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        print(f"could not read {SECRET_ID}: {out.stderr.strip()[:200]}", file=sys.stderr)
        sys.exit(2)
    raw = out.stdout.strip()
    if raw.startswith("{"):
        blob = json.loads(raw)
        for key in ("DATABASE_URL", "database_url", "url", "dsn"):
            if key in blob:
                return blob[key]
        return next(iter(blob.values()))
    return raw


def main() -> int:
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is required", file=sys.stderr)
        return 2

    sys.path.insert(0, ".")
    from backend_app.backend.market_data_latency import (SOURCE_A, SOURCE_B,
                                                         CorrectnessFloor,
                                                         choose_market_data_source)

    floor = CorrectnessFloor()
    print("=" * 78)
    print("Can a Paper_Session start in production today?")
    print("=" * 78)
    print(f"required completeness : {floor.min_completeness:.4%}")
    print(f"required sample bars  : {floor.min_completeness_sample_bars:,}")
    print(f"  at 1m bars that is  : ~{floor.min_completeness_sample_bars / 1440:.1f} days "
          f"of continuous real-feed observation")
    print()

    conn = psycopg2.connect(read_dsn(), connect_timeout=90)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()

    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{TABLE}",))
    if not cur.fetchone()[0]:
        print(f"{TABLE}: TABLE ABSENT -- migration 039 has not been applied, so there is")
        print("nowhere to record a measurement even if one were run.")
        print("\nVERDICT: no Paper_Session can start.")
        return 1

    cur.execute(f"SELECT count(*) FROM public.{TABLE}")
    total = cur.fetchone()[0]
    print(f"{TABLE}: {total} row(s)")

    # Column names are read rather than assumed -- the first version of this script
    # guessed `created_at` and the table does not have it.
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (TABLE,),
    )
    columns = cur.fetchall()
    print(f"   columns: {', '.join(c for c, _ in columns)}")

    recorded = {}
    if total:
        cur.execute(f"SELECT source, payload FROM public.{TABLE}")
        for source, payload in cur.fetchall():
            recorded[source] = payload
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    payload = {}
            payload = payload if isinstance(payload, dict) else {}
            print(f"   {source}")
            for k in ("completeness", "sample_bars", "measured", "p99_ms",
                      "monotonicity_violations", "duplicates"):
                if k in payload:
                    print(f"      {k:<26} {payload[k]}")

    print()
    for s in (SOURCE_A, SOURCE_B):
        print(f"   {s:<40} {'RECORDED' if s in recorded else 'NOT RECORDED'}")

    print()
    cur.execute("SELECT count(*) FROM public.paper_sessions")
    sessions = cur.fetchone()[0]
    print(f"paper_sessions rows   : {sessions}")

    # THE VERDICT IS ASKED OF THE REAL DECISION FUNCTION, NOT REIMPLEMENTED HERE.
    # The first version of this script asserted "1 of 2 candidates unmeasured -> BLOCKED"
    # and that is FALSE: choose_market_data_source's own docstring says "Exactly one
    # admitted -> that one, whatever the latencies are. Correctness decided; latency did
    # not vote." A second copy of the rule in a readiness checker is a second rule that
    # can disagree with the one that actually gates sessions -- which is the whole class of
    # bug this script exists to surface. So it calls the real thing.
    print()
    print("-" * 78)
    print("The real decision, from choose_market_data_source itself")
    print("-" * 78)
    from backend_app.backend.market_data_latency import SourceMeasurement
    from backend_app.backend.paper.paper_market_feed import measurement_for

    # Converted exactly as market_data_measurement_store.latest_measurements() does it
    # (SourceMeasurement.from_dict on the row's payload), so this reads the same objects
    # the router hands to open_feed rather than a lookalike.
    store = {}
    for source, payload in recorded.items():
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        if isinstance(payload, dict):
            try:
                store[source] = SourceMeasurement.from_dict(payload)
            except Exception as exc:  # noqa: BLE001
                print(f"   {source}: payload could not be read as a SourceMeasurement "
                      f"({exc}); treating as unmeasured")
    decision = choose_market_data_source(
        measurement_for(SOURCE_A, measurements=store),
        measurement_for(SOURCE_B, measurements=store),
    )
    print(f"   outcome  : {decision.outcome}")
    print(f"   selected : {decision.selected}")
    print(f"   rule     : {decision.rule}")
    print(f"   reason   : {str(decision.reason)[:600]}")
    for v in (decision.floor or ()):
        verdict = "ADMITTED" if not v.rejection_reason else f"REJECTED {v.rejection_reason}"
        print(f"   floor    : {v.source:<40} {verdict}")
        if v.rejection_detail:
            print(f"              {str(v.rejection_detail)[:220]}")

    print()
    print("=" * 78)
    blocked = str(decision.outcome).upper().endswith("BLOCKED") or decision.selected is None
    if blocked:
        print("VERDICT: NO PAPER_SESSION CAN START -- open_feed raises 409")
        print("PAPER_MARKET_DATA_UNAVAILABLE and every start_session call fails.")
        print()
        print("This is CORRECT behaviour (Requirement 14.4), and also a hard launch gate.")
        print("To clear it:")
        print(f"   1. observe a real feed for >= {floor.min_completeness_sample_bars:,} "
              f"bars at >= {floor.min_completeness:.4%} completeness")
        print(f"      (~{floor.min_completeness_sample_bars / 1440:.1f} days at 1m);")
        print("   2. record it with scripts/record_market_data_measurement.py.")
        print()
        print("THE mds PREREQUISITE IS ALREADY MET, which is why it is no longer step 1.")
        print("This block used to say 'build a FRESH mds image' and 'deploy mds with")
        print("REDIS_URL wired in (it currently declares none)'. Both were true of the")
        print("hand-made vyomquant-mds:1, and both are now false. Measured against")
        print("production on 2026-10-28: vyomquant-mds-service runs vyomquant-mds:4,")
        print("whose container is the API IMAGE -- so it carries PAPER-02's last-price")
        print("write, which the 2026-07-20 mds image predated -- and whose task")
        print("definition declares REDIS_URL. Its log stream confirms both, in order:")
        print("'[RedisManager] Connected to 3 databases' then 'Listening to")
        print("mds:commands...' then 'Health server listening on port 8081'. mds is")
        print("IDLE, not missing: it opens a stream when something asks, and with no")
        print("paper session nothing has asked yet. Re-measure before trusting this")
        print("paragraph -- it is a dated observation, not a live check.")
        print()
        print("Step 1 is the long pole and nothing shortens it except a coarser")
        print("timeframe, which is a Requirement 14.4 decision rather than a")
        print("configuration change.")
        return 1

    print(f"VERDICT: THE FEED GATE IS CLEAR -- {decision.selected} is selected.")
    print("A Paper_Session start does not fail on market-data source selection.")
    print(f"paper_sessions is {sessions} rows, so if no session has ever started the")
    print("cause is elsewhere in start_session, not in this gate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
