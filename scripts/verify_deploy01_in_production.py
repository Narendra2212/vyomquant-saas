#!/usr/bin/env python3
"""DEPLOY-01: decide, from the real system, whether the per-deployment execution limits
have actually been EXERCISED -- and flip `production_verified` honestly.

WHY THIS SCRIPT EXISTS
----------------------
DEPLOY-01's enforcement is DEPLOYED: the running ECS task pins its image to a commit sha,
and that commit contains `place_order`'s `timeout_seconds`, the `asyncio.wait_for` around
the ccxt `create_order`, the engine plumbing and the ENFORCED field list. That much is
checkable offline and is recorded in the ledger.

What was NOT checkable was whether any of it had ever RUN against a real order, and at the
time of writing it could not be: `public.strategy_deployments` held ZERO rows, so nothing
on the platform was deployed and therefore nothing was being enforced.

Manufacturing the evidence was considered and rejected, for a reason worth keeping:
`execution_config` is plain `jsonb` with NO database constraint (verified -- no CHECK
mentions the column), so inserting `{"order_timeout_seconds": 30}` into production would
have demonstrated nothing about enforcement. The component that accepts or refuses the
field is application code (`deployment_binding.normalise_execution_config`), already proven
present in the running build. And a synthetic deployment is not one row: the FKs require an
`auth.users` row, a `strategies` row and a `strategy_versions` row beneath it. A four-row
object graph in a live trading database, to produce evidence weaker than a commit sha, is a
bad trade.

So the gap closes at the natural moment instead: THE FIRST REAL DEPLOYMENT that sets one of
these fields. This script is what to run then. It is READ-ONLY.

USAGE
    python scripts/verify_deploy01_in_production.py

    Exit 0  at least one deployment carries an enforced field -> the evidence exists;
            what it prints is what belongs in the ledger.
    Exit 1  no deployment carries one yet -> `production_verified` must stay false.
    Exit 2  could not reach the database.
"""
from __future__ import annotations

import json
import subprocess
import sys
from urllib.parse import urlparse, urlunparse

REGION = "ap-southeast-1"
SECRET = "vyomquant/production/database_url"

#: PORT 5432, not the stored 6543. The 6543 endpoint is Supabase's transaction-mode
#: pooler; session state does not survive it, which matters for any session setting a
#: reader relies on. Same choice `scripts/verify_schema_drift.py` makes, for the same
#: reason.
DIRECT_PORT = 5432

#: Mirrors `deployment_binding.ENFORCED_EXECUTION_CONFIG_FIELDS`. Imported rather than
#: restated where possible, so the two cannot drift.
try:  # pragma: no cover - import shape depends on how this is invoked
    from backend_app.backend.deployment_binding import (
        ENFORCED_EXECUTION_CONFIG_FIELDS as ENFORCED,
    )
except Exception:  # noqa: BLE001
    ENFORCED = ("max_order_notional", "max_open_positions", "order_timeout_seconds")


def _dsn() -> str:
    out = subprocess.run(
        ["aws", "secretsmanager", "get-secret-value", "--region", REGION,
         "--secret-id", SECRET, "--query", "SecretString", "--output", "text"],
        capture_output=True, text=True, shell=True,
    )
    if out.returncode != 0:
        print(f"could not read {SECRET}: {out.stderr[:300]}", file=sys.stderr)
        raise SystemExit(2)
    raw = out.stdout.strip()
    if raw.startswith("{"):
        obj = json.loads(raw)
        for key in ("DATABASE_URL", "database_url", "url", "dsn"):
            if key in obj:
                raw = obj[key]
                break
    parsed = urlparse(raw)
    if parsed.port != DIRECT_PORT:
        raw = urlunparse(parsed._replace(
            netloc=f"{parsed.username}:{parsed.password}@{parsed.hostname}:{DIRECT_PORT}"))
    return raw


def main() -> int:
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is required", file=sys.stderr)
        return 2

    try:
        conn = psycopg2.connect(_dsn(), connect_timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"could not connect: {exc}", file=sys.stderr)
        return 2

    # Read-only at the SERVER, not merely by intent: a stray write would be rejected with
    # 25006 rather than relying on this file never issuing one.
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("SHOW default_transaction_read_only")
    if cur.fetchone()[0] != "on":
        print("REFUSING: this session is not read-only (a pooler may have discarded the "
              "setting). Connect on port 5432, not 6543.", file=sys.stderr)
        return 2

    cur.execute("SELECT count(*) FROM public.strategy_deployments")
    total = cur.fetchone()[0]
    print(f"strategy_deployments rows: {total}")

    if total == 0:
        print("\nNothing is deployed, so nothing is being enforced.")
        print("production_verified must stay FALSE for DEPLOY-01.")
        return 1

    predicate = " OR ".join(
        f"execution_config ? '{field}'" for field in sorted(ENFORCED)
    )
    cur.execute(f"""
        SELECT id, user_id, status, mode, execution_config, created_at
          FROM public.strategy_deployments
         WHERE execution_config IS NOT NULL AND ({predicate})
         ORDER BY created_at DESC
    """)
    carrying = cur.fetchall()

    print(f"deployments carrying an enforced field: {len(carrying)}")
    for row in carrying:
        ident, user_id, status, mode, config, created = row
        present = sorted(k for k in config if k in ENFORCED)
        print(f"\n  id={ident}")
        print(f"    user={user_id}  status={status}  mode={mode}  created={created}")
        print(f"    enforced fields set: {present}")
        for key in present:
            print(f"      {key} = {config[key]!r}")

    if not carrying:
        print("\nDeployments exist, but none sets an enforced field, so the limits have "
              "still never been applied to a real order.")
        print("production_verified must stay FALSE for DEPLOY-01.")
        return 1

    # A deployment that is RUNNING is the one whose BotRunner actually consults the limits
    # on every tick; a 'failed' or 'deploying' row proves only that the value was accepted
    # and stored.
    running = [r for r in carrying if str(r[2]).lower() in ("running", "active")]
    print(f"\nof those, RUNNING (so a BotRunner is consulting them per tick): {len(running)}")

    print("\n" + "=" * 72)
    print("WHAT THIS DOES AND DOES NOT ESTABLISH")
    print("  ESTABLISHED: the deployed build ACCEPTED an enforced field and persisted it,")
    print("    which the pre-DEPLOY-01 build refused with EXECUTION_CONFIG_NOT_ENFORCED.")
    if running:
        print("    A running deployment carries it, so evaluate_deployment_execution_limits")
        print("    is being consulted before every order this bot constructs.")
    print("  STILL NOT ESTABLISHED by this script alone: that a limit has REFUSED an order,")
    print("    or that order_timeout_seconds has TIMED OUT. A refusal needs an order that")
    print("    actually breaches the cap; a timeout needs a venue that stops answering, and")
    print("    neither can be manufactured on demand. Search the logs for")
    print("    'DEPLOY-01' and 'ORDER SUBMISSION UNKNOWN' to find them if they have")
    print("    occurred.")
    print("=" * 72)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
