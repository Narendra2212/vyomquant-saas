"""PAPER-02, measured before any fix: what the database actually holds.

Three measurements against real PostgreSQL, because the defect is a PERSISTED STATE and a
returned HTTP body cannot establish it either way.

  A. A single unfundable market buy. Expect (pre-fix) order_state=ACCEPTED,
     legacy_status=OPEN, filled_quantity=0, rejection_reason=NULL -- a live order that can
     never fill and says nothing about why.

  B. The brief's concurrency case, in two separate OS PROCESSES so the race is real and not
     two coroutines on one event loop:
         capital 100, worker A buys 80, worker B buys 80
         required per order = 80 * (1 + fee 0.001 + slippage 0.0005) = 80.12
         both cannot be funded; exactly one must be
     Reported from `paper_orders` / `paper_accounts` / `paper_fills` / `paper_positions`,
     not from what the workers printed.

  C. Whether a losing worker leaves a PHANTOM FILL. `_execute_fill` writes `paper_fills` and
     `paper_positions` BEFORE its version-guarded `bump_version`, so a worker that loses the
     version race can leave a fill row with no balance change behind it. Counted directly.

Usage:
    .venv\\Scripts\\python.exe staging\\probe_paper02_state.py
    .venv\\Scripts\\python.exe staging\\probe_paper02_state.py --worker <uid> <qty> <tag>
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
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
os.environ["AERORA_TEST_DATABASE_URL"] = DSN

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from tests.paper_pg_client import PostgresSupabase, purge_paper_user  # noqa: E402

from backend_app.backend.paper_trading_service import PaperTradingService  # noqa: E402

SYMBOL = "BTC-USDT"
PRICE = 1.0
CAPITAL = 100.0


# ──────────────────────────────────────────────────────────────────────────
#  READ-BACK over an INDEPENDENT connection
# ──────────────────────────────────────────────────────────────────────────

def snapshot(uid: str) -> dict:
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    out: dict = {}
    cur.execute(
        "SELECT order_state, legacy_status, side, quantity, filled_quantity, "
        "       avg_fill_price, rejection_reason, created_at "
        "  FROM paper_orders WHERE user_id = %s ORDER BY created_at",
        (uid,),
    )
    out["orders"] = [dict(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT available_balance, locked_balance, realized_pnl, total_equity, version "
        "  FROM paper_accounts WHERE user_id = %s",
        (uid,),
    )
    out["account"] = [dict(r) for r in cur.fetchall()]

    for table in ("paper_fills", "paper_positions", "paper_balance_events", "paper_trades"):
        cur.execute(f"SELECT count(*) AS n FROM {table} WHERE user_id = %s", (uid,))
        out[f"{table}_count"] = cur.fetchone()["n"]

    cur.execute(
        "SELECT cause, available_delta, locked_delta FROM paper_balance_events "
        " WHERE user_id = %s ORDER BY occurred_at",
        (uid,),
    )
    out["balance_events"] = [dict(r) for r in cur.fetchall()]

    cur.close()
    conn.close()
    return out


def show(label: str, snap: dict) -> None:
    print(f"  --- {label} ---")
    for row in snap["orders"]:
        print(
            f"    order side={row['side']:<4} qty={row['quantity']} "
            f"state={row['order_state']:<16} legacy={row['legacy_status']:<10} "
            f"filled={row['filled_quantity']} reason={row['rejection_reason']!r}"
        )
    for row in snap["account"]:
        print(
            f"    account avail={row['available_balance']} locked={row['locked_balance']} "
            f"equity={row['total_equity']} version={row['version']}"
        )
    print(
        f"    counts fills={snap['paper_fills_count']} "
        f"positions={snap['paper_positions_count']} "
        f"balance_events={snap['paper_balance_events_count']} "
        f"trades={snap['paper_trades_count']}"
    )
    for row in snap["balance_events"]:
        print(
            f"    event cause={row['cause']:<12} avail_delta={row['available_delta']} "
            f"locked_delta={row['locked_delta']}"
        )


# ──────────────────────────────────────────────────────────────────────────
#  THE WORKER (also the child process entry point)
# ──────────────────────────────────────────────────────────────────────────

async def _place(uid: str, qty: float, tag: str) -> dict:
    client = PostgresSupabase(DSN)
    service = PaperTradingService(supabase=client, default_capital=CAPITAL)
    try:
        order = await service.place_order(
            user_id=uid,
            symbol=SYMBOL,
            side="buy",
            order_type="market",
            quantity=qty,
            price=PRICE,
            idempotency_key=f"probe-{tag}",
        )
        return {"tag": tag, "outcome": "returned", "order": _jsonable(order)}
    except Exception as exc:  # noqa: BLE001
        return {
            "tag": tag,
            "outcome": "raised",
            "error_type": type(exc).__name__,
            "error_code": getattr(exc, "code", None),
            "error": str(exc)[:300],
        }
    finally:
        client.close()


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def worker_main() -> int:
    uid, qty, tag = sys.argv[2], float(sys.argv[3]), sys.argv[4]
    result = asyncio.run(_place(uid, qty, tag))
    print("WORKER_RESULT=" + json.dumps(result))
    return 0


# ──────────────────────────────────────────────────────────────────────────
#  MEASUREMENTS
# ──────────────────────────────────────────────────────────────────────────

def measurement_a() -> None:
    print("A. ONE UNFUNDABLE MARKET BUY (capital 100, buy 500 @ 1 -> required 500.75)")
    uid = str(uuid.uuid4())
    try:
        client = PostgresSupabase(DSN)
        service = PaperTradingService(supabase=client, default_capital=CAPITAL)
        service.get_or_create_account(uid)
        result = asyncio.run(_place(uid, 500.0, "unfundable"))
        client.close()
        print(f"  call -> {result['outcome']} {result.get('error_type', '')} "
              f"{result.get('error_code') or ''}")
        if result["outcome"] == "raised":
            print(f"       {result['error'][:200]}")
        snap = snapshot(uid)
        show("database after the unfundable buy", snap)

        orders = snap["orders"]
        stranded = [
            o for o in orders
            if o["order_state"] not in ("REJECTED", "FILLED", "CANCELLED")
        ]
        print(f"  PAPER02_STRANDED_ORDERS={len(stranded)}")
        print(
            "  PAPER02_REASON_RECORDED="
            + str(bool(orders and orders[0]["rejection_reason"]))
        )
    finally:
        purge_paper_user(DSN, uid)


def measurement_b_and_c() -> None:
    print("\nB/C. TWO SEPARATE OS PROCESSES, capital 100, each buying 80 @ 1")
    print("     required per order = 80 * (1 + 0.001 + 0.0005) = 80.12")
    uid = str(uuid.uuid4())
    try:
        client = PostgresSupabase(DSN)
        service = PaperTradingService(supabase=client, default_capital=CAPITAL)
        service.get_or_create_account(uid)
        client.close()

        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        procs = [
            subprocess.Popen(
                [sys.executable, __file__, "--worker", uid, "80", tag],
                cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for tag in ("A", "B")
        ]
        results = []
        for proc in procs:
            out, err = proc.communicate(timeout=300)
            line = next(
                (l for l in out.splitlines() if l.startswith("WORKER_RESULT=")), None
            )
            if line is None:
                results.append({"outcome": "no-result", "stderr": err[-300:]})
            else:
                results.append(json.loads(line[len("WORKER_RESULT="):]))

        for res in results:
            print(
                f"  worker {res.get('tag', '?')} -> {res['outcome']} "
                f"{res.get('error_type', '')} {res.get('error_code') or ''}"
            )
            if res.get("error"):
                print(f"       {res['error'][:180]}")

        snap = snapshot(uid)
        show("database after the race", snap)

        orders = snap["orders"]
        filled = [o for o in orders if o["order_state"] == "FILLED"]
        rejected = [o for o in orders if o["order_state"] == "REJECTED"]
        stranded = [
            o for o in orders
            if o["order_state"] not in ("REJECTED", "FILLED", "CANCELLED")
        ]
        avail = (
            Decimal(str(snap["account"][0]["available_balance"]))
            if snap["account"] else None
        )

        print(f"  RACE_ORDERS={len(orders)}")
        print(f"  RACE_FILLED={len(filled)}")
        print(f"  RACE_REJECTED={len(rejected)}")
        print(f"  RACE_STRANDED={len(stranded)}")
        print(f"  RACE_FILL_ROWS={snap['paper_fills_count']}")
        print(f"  RACE_AVAILABLE={avail}")
        print(f"  RACE_NEGATIVE_CASH={bool(avail is not None and avail < 0)}")
        print(
            "  RACE_PHANTOM_FILL="
            + str(snap["paper_fills_count"] != len(filled))
            + f"  (fill rows {snap['paper_fills_count']} vs FILLED orders {len(filled)})"
        )
        print(
            "  RACE_VERDICT="
            + (
                "CORRECT (exactly one filled, exactly one rejected)"
                if len(filled) == 1 and len(rejected) == 1 and not stranded
                else "INCORRECT"
            )
        )
    finally:
        purge_paper_user(DSN, uid)


def main() -> int:
    print(f"DSN={DSN}")
    measurement_a()
    measurement_b_and_c()
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        raise SystemExit(worker_main())
    raise SystemExit(main())
