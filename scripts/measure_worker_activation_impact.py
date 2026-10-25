#!/usr/bin/env python3
"""INF-03: measure what starting each worker would actually DO, before starting it.

WHY THIS EXISTS
===============
INF-03's remaining step creates long-running production ECS services. Two of the five
workers were described (by me, in this finding's own ledger entry and in the summary
handed to the owner) in terms that turned out to overstate one and understate the other:

  * marketplace_expiry was described as something that "would begin expiring paid
    marketplace access immediately". IT CANNOT TAKE ACCESS FROM ANYONE.
    ``entitlement_resolver.resolve`` compares ``now`` against each subscription's own
    ``period_expiry`` on EVERY call and refuses a lapsed row before it would trust the
    stored ``status`` label, so access already ends at the expiry instant whether this
    worker has ever run (Requirement 11.7, property P-11). expiry_sweep.py's own module
    docstring states this at length and backs it structurally: the module has no
    resolve-shaped function, never imports entitlement_resolver, and writes only
    ``status`` + ``updated_at`` -- ``period_expiry``, ``period_start`` and ``expires_at``
    are in _FORBIDDEN_UPDATE_COLUMNS and asserted absent from the payload on every sweep.
    What starting it actually does is reconcile stale labels and STOP WORK that belongs to
    subscriptions which have ALREADY lapsed. That is still a real, user-visible side
    effect and still deserves a number before anyone turns it on -- but it is enforcement
    catching up, not a revocation.

  * training is ~10x the hourly cost of any other worker (2048 cpu / 8192 MB against
    256/512), which is worth knowing; but it is also pointless to pay for if nothing has
    ever been queued for it.

A blanket yes to "start the workers" is a worse decision than a yes informed by these
counts, and the counts take one read-only query each.

WHAT IT DOES
============
Read-only. One SELECT per question, no UPDATE, no INSERT, no DDL, and the connection is
opened read-only at the session level so a mistake in this file cannot write. It reports:

  * how many subscriptions satisfy the expiry sweep's EXACT predicate
    (``period_expiry IS NOT NULL AND period_expiry <= now() AND status IN
    ('active','suspended')`` -- transcribed from expiry_sweep.EXPIRABLE_STATUS_TEXTS
    rather than guessed, and asserted against it at runtime);
  * how many strategy_deployments and paper_sessions the sweep would consequently stop,
    scoped the way stop_running_sessions_and_deployments scopes them -- by the
    (purchaser, listing) pair, in STOPPABLE_* statuses only;
  * how many training jobs are waiting in each status, so the training worker's cost can
    be weighed against its actual backlog.

    python scripts/measure_worker_activation_impact.py
"""
from __future__ import annotations

import json
import subprocess
import sys

SECRET_ID = "vyomquant/production/database_url"
REGION = "ap-southeast-1"


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


def table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",))
    return bool(cur.fetchone()[0])


def counts_by(cur, table: str, column: str):
    if not table_exists(cur, table):
        return None
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
        (table, column),
    )
    if not cur.fetchone():
        return "NO_SUCH_COLUMN"
    cur.execute(
        f"SELECT COALESCE({column}::text,'(null)') AS k, count(*) "
        f"FROM public.{table} GROUP BY 1 ORDER BY 2 DESC"
    )
    return cur.fetchall()


def main() -> int:
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is required", file=sys.stderr)
        return 2

    # The predicate is imported, not retyped, so this measurement cannot drift from the
    # sweep it is measuring.
    sys.path.insert(0, ".")
    from backend_app.backend.marketplace import expiry_sweep as sw

    expirable = list(sw.EXPIRABLE_STATUS_TEXTS)
    assert sw.EXPIRED_STATUS_TEXT not in expirable, "sweep predicate includes its own output"

    conn = psycopg2.connect(read_dsn(), connect_timeout=90)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()

    print("=" * 78)
    print("What starting each worker would actually do (read-only measurement)")
    print("=" * 78)
    print(f"sweep predicate : period_expiry IS NOT NULL AND period_expiry <= now()")
    print(f"                  AND status IN {tuple(expirable)}")
    print(f"writes only     : status -> {sw.EXPIRED_STATUS_TEXT!r} (+ updated_at)")
    print(f"never writes    : {sorted(sw._FORBIDDEN_UPDATE_COLUMNS)}")
    print()

    # ---- marketplace_expiry -------------------------------------------------
    print("-" * 78)
    print("marketplace_expiry")
    print("-" * 78)
    if not table_exists(cur, sw.SUBSCRIPTIONS_TABLE):
        print(f"   {sw.SUBSCRIPTIONS_TABLE} does not exist -> sweep would expire nothing")
        candidates = []
    else:
        cur.execute(f"SELECT count(*) FROM public.{sw.SUBSCRIPTIONS_TABLE}")
        print(f"   {sw.SUBSCRIPTIONS_TABLE} total rows : {cur.fetchone()[0]}")
        dist = counts_by(cur, sw.SUBSCRIPTIONS_TABLE, "status")
        print(f"   status distribution         : "
              f"{dict(dist) if isinstance(dist, list) else dist}")

        cur.execute(
            f"SELECT id, user_id, library_id, status, period_expiry "
            f"FROM public.{sw.SUBSCRIPTIONS_TABLE} "
            f"WHERE period_expiry IS NOT NULL AND period_expiry <= now() "
            f"  AND status = ANY(%s)",
            (expirable,),
        )
        candidates = cur.fetchall()
        print(f"   WOULD EXPIRE ON FIRST SWEEP  : {len(candidates)} subscription(s)")
        for row in candidates[:10]:
            print(f"      id={row[0]} status={row[3]} expired_at={row[4]}")

    # Requirement 11.15's enforcement, scoped exactly as the sweep scopes it.
    pairs = [(r[1], r[2]) for r in candidates if r[1] and r[2]]
    for table, state_col, stoppable, label in (
        (sw.DEPLOYMENTS_TABLE, "status", sw.STOPPABLE_DEPLOYMENT_STATUSES,
         "strategy_deployments"),
        (sw.PAPER_SESSIONS_TABLE, "session_state", sw.STOPPABLE_SESSION_STATES,
         "paper_sessions"),
    ):
        if not table_exists(cur, table):
            print(f"   {label:<22} : table absent -> 0 stopped")
            continue
        cur.execute(f"SELECT count(*) FROM public.{table}")
        total = cur.fetchone()[0]
        if not pairs:
            print(f"   {label:<22} : {total} row(s) in table, 0 would be stopped "
                  f"(no expiring subscription to scope by)")
            continue
        users = [p[0] for p in pairs]
        listings = [p[1] for p in pairs]
        cur.execute(
            f"SELECT count(*) FROM public.{table} "
            f"WHERE user_id = ANY(%s) AND marketplace_listing_id = ANY(%s) "
            f"  AND {state_col} = ANY(%s)",
            (users, listings, list(stoppable)),
        )
        print(f"   {label:<22} : {total} row(s) in table, "
              f"{cur.fetchone()[0]} WOULD BE STOPPED")

    # ---- training -----------------------------------------------------------
    print()
    print("-" * 78)
    print("training  (2048 cpu / 8192 MB -- the expensive one)")
    print("-" * 78)
    found_any = False
    for table in ("ml_training_jobs", "training_jobs", "ml_jobs", "model_training_jobs"):
        if not table_exists(cur, table):
            continue
        found_any = True
        cur.execute(f"SELECT count(*) FROM public.{table}")
        print(f"   {table}: {cur.fetchone()[0]} row(s)")
        for col in ("status", "state", "job_status"):
            dist = counts_by(cur, table, col)
            if isinstance(dist, list):
                print(f"      by {col}: {dict(dist)}")
                break
    if not found_any:
        print("   no training-job table found under any expected name "
              "(ml_training_jobs, training_jobs, ml_jobs, model_training_jobs)")
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND (table_name LIKE '%train%' "
            "   OR table_name LIKE '%ml_%') ORDER BY 1"
        )
        rows = [r[0] for r in cur.fetchall()]
        print(f"   tables matching train/ml_: {rows or 'none'}")

    # ---- backtest / dag -----------------------------------------------------
    print()
    print("-" * 78)
    print("backtest / dag  (queue depth, for context)")
    print("-" * 78)
    for table, col in (("backtest_results", "status"), ("backtests", "status"),
                       ("dag_runs", "status"), ("strategy_deployments", "status")):
        if table_exists(cur, table):
            dist = counts_by(cur, table, col)
            print(f"   {table:<22}: {dict(dist) if isinstance(dist, list) else dist}")

    cur.close()
    conn.close()
    print()
    print("=" * 78)
    print("Read-only: the session was opened READ ONLY, so nothing above could write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
