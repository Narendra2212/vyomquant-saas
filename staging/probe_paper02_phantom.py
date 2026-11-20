"""Can a fill-time version conflict leave a PHANTOM FILL behind?

`_execute_fill` issues six statements and PostgREST gives it no transaction. The order is:

    1. insert_fill          <- WRITE
    2. upsert_position      <- WRITE
    3. bump_version(expected_version=...)   <- the CONDITIONAL write
    4. insert_balance_event
    5. insert_trade (maybe)
    6. update_order(FILLED)

If step 3 loses the version race, steps 1 and 2 have already landed. That would be a fill
row and a position with **no balance change behind them** - a worse inconsistency than
PAPER-02 itself, and the thing the concurrency brief calls a "double reservation / silent
overwrite" class of failure.

The natural race in `probe_paper02_state.py` did not hit this window (the loser's account
read happened after the winner's bump, so `apply_fill` refused before writing anything).
Not hitting a window is not the same as the window being closed, so it is forced here:
`lock_account_for_update` is wrapped to hand back a STALE `version`, which is exactly what a
worker that read before a concurrent bump would hold. Everything else is the real code path.

Usage:  .venv\\Scripts\\python.exe staging\\probe_paper02_phantom.py
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import uuid
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ENV", "testing")
os.environ.setdefault("DEV_MODE", "true")
os.environ.setdefault("VYOMQUANT_MODE", "safe")
os.environ.setdefault("REDIS_URL", "")

DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
)

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from tests.paper_pg_client import PostgresSupabase, purge_paper_user  # noqa: E402

from backend_app.backend import paper_trading_service as pts  # noqa: E402
from backend_app.backend.paper import paper_repository as repo  # noqa: E402

SYMBOL = "BTC-USDT"


def counts(uid: str) -> dict:
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    out = {}
    for table in ("paper_orders", "paper_fills", "paper_positions",
                  "paper_balance_events", "paper_trades"):
        cur.execute(f"SELECT count(*) AS n FROM {table} WHERE user_id = %s", (uid,))
        out[table] = cur.fetchone()["n"]
    cur.execute(
        "SELECT order_state, filled_quantity, rejection_reason FROM paper_orders "
        " WHERE user_id = %s ORDER BY created_at", (uid,))
    out["orders"] = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT available_balance, total_equity, version FROM paper_accounts "
        " WHERE user_id = %s", (uid,))
    out["account"] = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return out


async def run() -> int:
    uid = str(uuid.uuid4())
    client = PostgresSupabase(DSN)
    service = pts.PaperTradingService(supabase=client, default_capital=100000.0)
    try:
        service.get_or_create_account(uid)

        original = repo.lock_account_for_update
        hits = {"n": 0}

        def stale_version(supabase, user_id, **kwargs):
            """The real read, with the version rewound by one - a worker that read early."""
            row = dict(original(supabase, user_id, **kwargs))
            hits["n"] += 1
            row["version"] = int(row["version"]) - 1
            return row

        pts.repo.lock_account_for_update = stale_version
        try:
            raised = None
            try:
                await service.place_order(
                    user_id=uid, symbol=SYMBOL, side="buy", order_type="market",
                    quantity=1.0, price=100.0, idempotency_key="phantom-probe",
                )
            except Exception as exc:  # noqa: BLE001
                raised = exc
        finally:
            pts.repo.lock_account_for_update = original

        print(f"stale_version_reads={hits['n']}")
        print(
            f"call -> {'raised ' + type(raised).__name__ if raised else 'returned'}"
            + (f" code={getattr(raised, 'code', None)}" if raised else "")
        )
        if raised is not None:
            print(f"     {str(raised)[:200]}")

        snap = counts(uid)
        print("database:")
        for row in snap["orders"]:
            print(
                f"  order state={row['order_state']:<16} "
                f"filled={row['filled_quantity']} reason={row['rejection_reason']!r}"
            )
        for row in snap["account"]:
            print(
                f"  account avail={row['available_balance']} "
                f"equity={row['total_equity']} version={row['version']}"
            )
        print(
            f"  fills={snap['paper_fills']} positions={snap['paper_positions']} "
            f"balance_events={snap['paper_balance_events']} trades={snap['paper_trades']}"
        )

        filled_orders = [o for o in snap["orders"] if o["order_state"] == "FILLED"]
        phantom_fill = snap["paper_fills"] > len(filled_orders)
        unbacked = snap["paper_fills"] > snap["paper_balance_events"]

        print(f"PHANTOM_FILL_ROW={phantom_fill}")
        print(f"FILL_WITHOUT_BALANCE_EVENT={unbacked}")
        print(
            "VERDICT="
            + (
                "WINDOW OPEN - a conflicted fill leaves a fill row with no money behind it"
                if phantom_fill or unbacked
                else "WINDOW CLOSED on this path"
            )
        )
        return 0
    finally:
        client.close()
        purge_paper_user(DSN, uid)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
