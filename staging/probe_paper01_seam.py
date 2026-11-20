"""Smoke-drive the PAPER-01 seam against REAL PostgreSQL before writing the test matrix.

Proves, or fails to prove, four things in order:
  1. the psycopg2-backed PostgREST shim can satisfy ``paper_repository``;
  2. ``PaperTradingService.place_order`` writes the canonical rows through it;
  3. ``BotPaperLedger.record_fill`` returns the STORED figures;
  4. ``ExecutionEngine``'s PAPER branch, with a ledger installed, persists a fill.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("ENV", "development")
os.environ.setdefault("DEV_MODE", "true")

DSN = "postgresql://postgres:@127.0.0.1:62674/postgres"

import psycopg2  # noqa: E402

from tests.paper_pg_client import PostgresSupabase, purge_paper_user  # noqa: E402

from backend_app.backend.execution_environment import ExecutionEnvironment  # noqa: E402
from backend_app.backend.paper.bot_paper_ledger import BotPaperLedger  # noqa: E402
from backend_app.backend.paper_trading_service import PaperTradingService  # noqa: E402
from backend_app.core.execution_engine import ExecutionEngine  # noqa: E402

USER = str(uuid.uuid4())
SYMBOL = "BTC/USDT"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")


def rows(table, user_id=USER):
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f'SELECT * FROM public."{table}" WHERE user_id = %s', (user_id,)
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


import psycopg2.extras  # noqa: E402

sb = PostgresSupabase(DSN)


async def main():
    service = PaperTradingService(supabase=sb)

    # ── 1. the account ────────────────────────────────────────────────────
    try:
        account = service.get_or_create_account(USER)
        check("1. get_or_create_account writes a paper_accounts row",
              len(rows("paper_accounts")) == 1,
              f"available={account.get('available_balance')} equity={account.get('total_equity')}")
    except Exception as exc:
        check("1. get_or_create_account", False, f"{type(exc).__name__}: {exc}")
        return

    # ── 2. the canonical service places and fills an order ───────────────
    try:
        order = await service.place_order(
            user_id=USER, symbol=SYMBOL, side="buy", order_type="market",
            quantity=Decimal("0.01"), price=Decimal("50000"),
            idempotency_key="probe-direct-1",
        )
        o = rows("paper_orders"); f = rows("paper_fills"); p = rows("paper_positions")
        check("2. place_order persists order+fill+position",
              len(o) == 1 and len(f) == 1 and len(p) == 1,
              f"orders={len(o)} fills={len(f)} positions={len(p)} status={order.get('status')}")
    except Exception as exc:
        check("2. place_order", False, f"{type(exc).__name__}: {exc}")
        return

    # ── 3. the ledger adapter returns STORED figures ─────────────────────
    try:
        ledger = BotPaperLedger(USER, service=service, supabase=sb)
        recorded = await ledger.record_fill(
            symbol=SYMBOL, side="buy", size=Decimal("0.01"),
            price=Decimal("51000"), execution_id="exec_probe_abc123",
        )
        acct = rows("paper_accounts")[0]
        agree = (
            recorded.available_balance == Decimal(str(acct["available_balance"]))
            and recorded.total_equity == Decimal(str(acct["total_equity"]))
        )
        check("3. BotPaperLedger.record_fill returns the stored figures", agree,
              f"ledger equity={recorded.total_equity} stored={acct['total_equity']} "
              f"order={recorded.order_id}")
    except Exception as exc:
        check("3. BotPaperLedger.record_fill", False, f"{type(exc).__name__}: {exc}")
        return

    # ── 4. the engine's PAPER branch persists ────────────────────────────
    try:
        engine = ExecutionEngine(
            fee_rate=0.001, slippage=0.0005,
            portfolio_state={"total_equity": 10000.0},
            exchange_executor=None,
            execution_environment=ExecutionEnvironment.PAPER,
            paper_ledger=BotPaperLedger(USER, service=service, supabase=sb),
        )
        before = len(rows("paper_fills"))
        ok, detail = await engine._execute_trade_internal(
            symbol=SYMBOL, side="buy", size=Decimal("0.02"),
            price=Decimal("52000"), execution_id="exec_probe_engine_1",
        )
        after = len(rows("paper_fills"))
        check("4. ExecutionEngine PAPER branch persists a fill",
              ok and after == before + 1 and isinstance(detail, dict)
              and detail.get("paper_ledger") == "canonical",
              f"ok={ok} fills {before}->{after} detail={detail if not isinstance(detail, dict) else {k: detail[k] for k in ('order_id','executed_price','fee','paper_ledger') if k in detail}}")
    except Exception as exc:
        check("4. ExecutionEngine PAPER branch", False, f"{type(exc).__name__}: {exc}")
        return

    # ── 5. the engine reconciled to the stored figures ───────────────────
    acct = rows("paper_accounts")[0]
    stored_equity = Decimal(str(acct["total_equity"]))
    stored_cash = (Decimal(str(acct["available_balance"]))
                   + Decimal(str(acct["locked_balance"])))
    check("5. engine cash/equity == stored cash/equity",
          engine.cash == stored_cash and engine.current_equity == stored_equity,
          f"engine.cash={engine.cash} stored_cash={stored_cash} "
          f"engine.equity={engine.current_equity} stored_equity={stored_equity}")

    # ── 6. the accounting invariant, read from the database ──────────────
    pos = [r for r in rows("paper_positions") if Decimal(str(r["size"])) > 0]
    pmv = Decimal("0")
    for r in pos:
        size = Decimal(str(r["size"])); entry = Decimal(str(r["entry_price"]))
        cur_p = r["current_price"]
        price = entry if cur_p is None else Decimal(str(cur_p))
        pmv += (size * price if str(r["side"]).upper() == "LONG"
                else size * (Decimal("2") * entry - price))
    check("6. cash + position market value == equity (from the database)",
          stored_cash + pmv == stored_equity,
          f"{stored_cash} + {pmv} = {stored_cash + pmv} vs equity {stored_equity}")

    # ── 7. a duplicate execution_id does not move the money twice ────────
    before_fills = len(rows("paper_fills"))
    before_equity = stored_equity
    ok2, detail2 = await engine._execute_trade_internal(
        symbol=SYMBOL, side="buy", size=Decimal("0.02"),
        price=Decimal("52000"), execution_id="exec_probe_engine_1",
    )
    after_fills = len(rows("paper_fills"))
    after_equity = Decimal(str(rows("paper_accounts")[0]["total_equity"]))
    check("7. a repeated execution_id adds no fill and moves no money",
          after_fills == before_fills and after_equity == before_equity,
          f"fills {before_fills}->{after_fills} equity {before_equity}->{after_equity} ok={ok2}")


try:
    asyncio.run(main())
finally:
    print()
    print("ROW COUNTS")
    for t in ("paper_accounts", "paper_orders", "paper_fills", "paper_positions",
              "paper_balance_events", "paper_trades", "paper_equity_snapshots"):
        try:
            print(f"  {t:<24} {len(rows(t))}")
        except Exception as exc:
            print(f"  {t:<24} ERROR {exc}")
    purge_paper_user(DSN, USER)
    sb.close()
    failed = sum(1 for _, ok, _ in results if not ok)
    print()
    print(f"VERDICT={'ALL_PASS' if failed == 0 else f'{failed}_FAILED'} of {len(results)}")
