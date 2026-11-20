"""What state does the orphan order row sit in after an unfundable market buy?

Probe 5 of staging/probe_paper01_sell_semantics.py showed `orders=1 fills=0` and a
`PAPER_INVARIANT_VIOLATION` for a buy of 100 BTC @ 50000 against a $100,000 account. Two
things need pinning before anything is written about it:

  1. the order's `order_state` / `legacy_status` / `rejection_reason` -- is it a clean
     REJECTED carrying a reason (Requirement 16.5), or is it stranded non-terminal?
  2. whether `place_order`'s own buy-side funds check fired at all, or whether the refusal
     came from `accounting.assert_invariants` inside `_execute_fill`.

The distinction matters: a REJECTED order carrying INSUFFICIENT_FUNDS is the canonical
path working as designed, and a stranded ACCEPTED order with no fill is a defect.
"""
from __future__ import annotations

import asyncio
import logging
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

logging.basicConfig(level=logging.INFO, format="LOG %(levelname)s %(name)s: %(message)s")


def orders(user):
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                'SELECT id, symbol, side, order_type, quantity, filled_quantity, '
                '       order_state, legacy_status, rejection_reason, idempotency_key '
                '  FROM public."paper_orders" WHERE user_id = %s ORDER BY created_at',
                (user,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


async def main():
    for label, side, qty, price in (
        ("unfundable market buy", "buy", "100", "50000"),
        ("fundable market buy", "buy", "0.01", "50000"),
        ("sell with no position", "sell", "0.01", "50000"),
    ):
        user = str(uuid.uuid4())
        sb = PostgresSupabase(DSN)
        service = PaperTradingService(supabase=sb)
        print(f"\n=== {label}: {side} {qty} @ {price} ===")
        try:
            service.get_or_create_account(user)
            try:
                body = await service.place_order(
                    user_id=user, symbol="BTC-USDT", side=side, order_type="market",
                    quantity=Decimal(qty), price=Decimal(price),
                    idempotency_key=f"probe-unf-{uuid.uuid4().hex[:8]}",
                )
                print(f"  RESULT: accepted, status={body.get('status')}")
            except Exception as exc:
                print(f"  RESULT: refused {type(exc).__name__}: {str(exc)[:200]}")
            for row in orders(user):
                print(f"  ORDER   state={row['order_state']} legacy={row['legacy_status']} "
                      f"filled={row['filled_quantity']} reason={row['rejection_reason']!r}")
            print(f"  order rows: {len(orders(user))}")
        finally:
            purge_paper_user(DSN, user)
            sb.close()


asyncio.run(main())
print("\nDONE")
