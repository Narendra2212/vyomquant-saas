"""What does the CANONICAL default-account path do with a sell that has no position?

The pre-fix BotRunner paper branch answered:

    if symbol not in self.positions:
        success, message = False, f"No position found for {symbol}"

The canonical model has an explicit SHORT side and a documented reversal-through-zero, so
it may well open a short instead. That difference is a behaviour change my PAPER-01 fix
would introduce for every paper bot, so it is measured here before anything is decided.

Three questions:
  1. sell with NO position at all -> refused, or a SHORT opened?
  2. sell LARGER than the open long -> refused, or reversed through zero into a short?
  3. does a full close write a paper_trades row and a realized_pnl?
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
import psycopg2.extras  # noqa: E402

from tests.paper_pg_client import PostgresSupabase, purge_paper_user  # noqa: E402
from backend_app.backend.paper_trading_service import PaperTradingService  # noqa: E402

SYMBOL = "BTC-USDT"


def rows(table, user):
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f'SELECT * FROM public."{table}" WHERE user_id = %s', (user,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def show(label, user):
    acct = rows("paper_accounts", user)
    pos = rows("paper_positions", user)
    trades = rows("paper_trades", user)
    print(f"  {label}")
    if acct:
        a = acct[0]
        print(f"    account  available={a['available_balance']} locked={a['locked_balance']} "
              f"realized={a['realized_pnl']} equity={a['total_equity']}")
    for p in pos:
        print(f"    position {p['symbol']} side={p['side']} size={p['size']} "
              f"entry={p['entry_price']} closed_at={'set' if p['closed_at'] else 'NULL'}")
    print(f"    orders={len(rows('paper_orders', user))} fills={len(rows('paper_fills', user))} "
          f"trades={len(trades)}")
    for t in trades:
        print(f"    trade    {t['symbol']} side={t['side']} qty={t['quantity']} "
              f"entry={t['entry_price']} exit={t['exit_price']} pnl={t['realized_pnl']} "
              f"fee_minor={t['fee_minor']}")


async def case(name, steps):
    user = str(uuid.uuid4())
    sb = PostgresSupabase(DSN)
    service = PaperTradingService(supabase=sb)
    print(f"\n=== {name} ===")
    try:
        service.get_or_create_account(user)
        for i, (side, qty, price) in enumerate(steps):
            try:
                await service.place_order(
                    user_id=user, symbol=SYMBOL, side=side, order_type="market",
                    quantity=Decimal(str(qty)), price=Decimal(str(price)),
                    idempotency_key=f"probe-{name}-{i}",
                )
                print(f"  step {i}: {side} {qty} @ {price} -> ACCEPTED")
            except Exception as exc:
                print(f"  step {i}: {side} {qty} @ {price} -> REFUSED "
                      f"{type(exc).__name__}: {str(exc)[:160]}")
        show("final state", user)
    finally:
        purge_paper_user(DSN, user)
        sb.close()


async def main():
    await case("1-sell-with-no-position", [("sell", "0.01", "50000")])
    await case("2-sell-larger-than-long", [("buy", "0.01", "50000"),
                                           ("sell", "0.03", "51000")])
    await case("3-full-close", [("buy", "0.01", "50000"),
                                ("sell", "0.01", "51000")])
    await case("4-partial-close", [("buy", "0.02", "50000"),
                                   ("sell", "0.01", "51000")])
    await case("5-buy-beyond-cash", [("buy", "100", "50000")])


asyncio.run(main())
print("\nDONE")
