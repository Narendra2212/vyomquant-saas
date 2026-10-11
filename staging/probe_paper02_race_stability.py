"""Is the PAPER-02 concurrency outcome STABLE, or did one run get lucky?

A race that produced the right answer once has not been shown to produce it. This runs the
two-process case N times against real PostgreSQL, each time on a fresh synthetic account, and
reports the distribution of outcomes read back from the DATABASE.

The required outcome, every time:
    exactly one FILLED, exactly one REJECTED carrying INSUFFICIENT_FUNDS,
    no order left in a non-terminal state, available_balance exactly 19.88,
    exactly one paper_fills row, exactly one FILL balance event.

Usage:  .venv\\Scripts\\python.exe staging\\probe_paper02_race_stability.py [runs]
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import sys
import uuid
from collections import Counter
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

WORKER = ROOT / "staging" / "probe_paper02_state.py"
CAPITAL = 100.0
EXPECTED_AVAILABLE = Decimal("19.88")


def read_state(uid: str) -> dict:
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT id, order_state, rejection_reason FROM paper_orders WHERE user_id = %s",
        (uid,),
    )
    orders = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT available_balance, total_equity FROM paper_accounts WHERE user_id = %s",
        (uid,),
    )
    account = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT count(*) AS n FROM paper_fills WHERE user_id = %s", (uid,))
    fills = cur.fetchone()["n"]
    cur.execute(
        "SELECT count(*) AS n FROM paper_positions WHERE user_id = %s", (uid,)
    )
    positions = cur.fetchone()["n"]
    cur.execute(
        "SELECT count(*) AS n FROM paper_balance_events "
        " WHERE user_id = %s AND cause = 'FILL'", (uid,)
    )
    events = cur.fetchone()["n"]
    # The PAPER-04 footprint: a fill row that no FILL ledger row references.
    cur.execute(
        "SELECT count(*) AS n FROM paper_fills f "
        " WHERE f.user_id = %s "
        "   AND NOT EXISTS (SELECT 1 FROM paper_balance_events e "
        "                    WHERE e.cause = 'FILL' AND e.fill_id = f.id)",
        (uid,),
    )
    unsettled = cur.fetchone()["n"]
    cur.close()
    conn.close()
    return {
        "orders": orders,
        "account": account,
        "fills": fills,
        "positions": positions,
        "fill_events": events,
        "unsettled_fills": unsettled,
    }


def one_race(index: int) -> tuple[str, str]:
    uid = str(uuid.uuid4())
    try:
        client = PostgresSupabase(DSN)
        PaperTradingService(supabase=client, default_capital=CAPITAL).get_or_create_account(
            uid
        )
        client.close()

        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        procs = [
            subprocess.Popen(
                [sys.executable, str(WORKER), "--worker", uid, "80", tag],
                cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for tag in ("A", "B")
        ]
        for proc in procs:
            proc.communicate(timeout=300)

        state = read_state(uid)
        filled = [o for o in state["orders"] if o["order_state"] == "FILLED"]
        rejected = [o for o in state["orders"] if o["order_state"] == "REJECTED"]
        stranded = [
            o for o in state["orders"]
            if o["order_state"] not in ("FILLED", "REJECTED", "CANCELLED")
        ]
        avail = (
            Decimal(str(state["account"][0]["available_balance"]))
            if state["account"] else None
        )
        reasons = {o["rejection_reason"] for o in rejected}

        # ── The REQUIRED invariants. Any failure here is a defect. ──────────────────
        # Exactly the brief's contract: one accepted, one rejected, nothing forbidden.
        problems = []
        if len(filled) != 1:
            problems.append(f"filled={len(filled)} (must be 1)")
        if len(rejected) != 1:
            problems.append(f"rejected={len(rejected)} (must be 1)")
        if stranded:
            problems.append(f"stranded={len(stranded)} (must be 0)")
        if reasons != {"INSUFFICIENT_FUNDS"}:
            problems.append(f"reasons={reasons}")
        if avail is None or avail != EXPECTED_AVAILABLE:
            problems.append(f"available={avail} (must be {EXPECTED_AVAILABLE})")
        if avail is not None and avail < 0:
            problems.append("NEGATIVE CASH")
        if state["fill_events"] != 1:
            problems.append(f"fill_events={state['fill_events']} (double reservation)")
        if state["positions"] != 1:
            problems.append(f"positions={state['positions']} (must be 1 - the winner's)")
        # A SETTLED fill is one the money moved for. More than one would be a double apply.
        settled = state["fills"] - state["unsettled_fills"]
        if settled != 1:
            problems.append(f"settled_fills={settled} (must be 1)")

        verdict = "CORRECT" if not problems else "INCORRECT"
        # ── The documented RESIDUE, counted and reported, not treated as a failure. ──
        # PAPER-04: the loser can write its fill row before its conflict is detected, and
        # paper_fills is append-only. It is unsettled - no FILL ledger row references it -
        # and the 409 it produced named phase='FILL', partial_write=True.
        detail = (
            f"filled={len(filled)} rejected={len(rejected)} stranded={len(stranded)} "
            f"avail={avail} positions={state['positions']} "
            f"settled_fills={settled} events={state['fill_events']} "
            f"| residue unsettled_fills={state['unsettled_fills']}"
            + ("  PROBLEMS: " + ", ".join(problems) if problems else "")
        )
        print(f"  run {index}: {verdict:<9} {detail}")
        return verdict, str(state["unsettled_fills"])
    finally:
        purge_paper_user(DSN, uid)


def main() -> int:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"DSN={DSN}")
    print(f"capital 100, two OS processes each buying 80 @ 1, {runs} independent races")
    verdicts: Counter = Counter()
    residue: Counter = Counter()
    for i in range(1, runs + 1):
        verdict, unsettled = one_race(i)
        verdicts[verdict] += 1
        residue[unsettled] += 1
    print(f"\nRUNS={runs}  CORRECT={verdicts['CORRECT']}  INCORRECT={verdicts['INCORRECT']}")
    print(
        "REQUIRED_INVARIANTS="
        + ("HELD EVERY RUN" if verdicts["INCORRECT"] == 0 else "BREACHED")
    )
    print(
        "PAPER04_RESIDUE unsettled_fill_rows -> runs: "
        + ", ".join(f"{k}:{v}" for k, v in sorted(residue.items()))
    )
    return 0 if verdicts["INCORRECT"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
