"""Phase 9 - measure execution_records.order_id's FK and OrderStatusResult's fields.

Two surprises surfaced while testing TRADE-07 recovery:

1. `fk_execution_records_order_id` exists, referencing `orders`. If recovery writes a
   VENUE order id into that column it would violate the constraint -- and so, apparently,
   would the existing COMPLETED transition. Needs measuring, not assuming.
2. `OrderStatusResult` has no `error_code` field, which I assumed it did.
"""
from __future__ import annotations

import dataclasses
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2


def main() -> int:
    dsn = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
    conn = psycopg2.connect(dsn)
    conn.autocommit = True

    print("=" * 78)
    print("execution_records FOREIGN KEYS")
    print("=" * 78)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT conname, pg_get_constraintdef(oid), condeferrable, condeferred
              FROM pg_constraint
             WHERE conrelid = to_regclass('public.execution_records')
               AND contype = 'f'
             ORDER BY conname
        """)
        for name, definition, deferrable, deferred in cur.fetchall():
            print(f"  {name}")
            print(f"      {definition}")
            print(f"      deferrable={deferrable} deferred={deferred}")

        print()
        print("order_id column:")
        cur.execute("""
            SELECT data_type, is_nullable
              FROM information_schema.columns
             WHERE table_schema='public' AND table_name='execution_records'
               AND column_name='order_id'
        """)
        print("   ", cur.fetchone())

        print()
        print("orders table:")
        cur.execute("SELECT to_regclass('public.orders') IS NOT NULL")
        print("    exists =", cur.fetchone()[0])
        cur.execute("""
            SELECT column_name, data_type, is_nullable
              FROM information_schema.columns
             WHERE table_schema='public' AND table_name='orders'
             ORDER BY ordinal_position
        """)
        for row in cur.fetchall():
            print("   ", row)

        print()
        print("how many execution_records currently carry a non-null order_id:")
        cur.execute(
            "SELECT count(*) FROM public.execution_records WHERE order_id IS NOT NULL"
        )
        print("    ", cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM public.orders")
        print("    orders rows:", cur.fetchone()[0])

    conn.close()

    print()
    print("=" * 78)
    print("OrderStatusResult FIELDS")
    print("=" * 78)
    from backend_app.backend.exchange_executor import OrderStatusResult

    if dataclasses.is_dataclass(OrderStatusResult):
        for f in dataclasses.fields(OrderStatusResult):
            print(f"  {f.name}: {f.type} default={f.default!r}")
    else:
        print("  not a dataclass; annotations:")
        print("  ", getattr(OrderStatusResult, "__annotations__", {}))

    print()
    print("OrderResult FIELDS (for comparison)")
    from backend_app.backend.exchange_executor import OrderResult

    if dataclasses.is_dataclass(OrderResult):
        for f in dataclasses.fields(OrderResult):
            print(f"  {f.name}: {f.type} default={f.default!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
