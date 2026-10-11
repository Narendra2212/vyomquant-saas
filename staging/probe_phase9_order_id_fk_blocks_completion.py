"""Phase 9 - does fk_execution_records_order_id block recording a VENUE order id?

WHY THIS PROBE EXISTS
---------------------
While building TRADE-07 recovery, inserting a test row with `order_id='VENUE-123'` was
refused:

    ForeignKeyViolation: insert or update on table "execution_records" violates foreign
    key constraint "fk_execution_records_order_id"
    DETAIL: Key (order_id)=(VENUE-123) is not present in table "orders".

Measured: `FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL`, NOT
deferrable, and `0` of the current `execution_records` carry a non-null `order_id`.

That matters well beyond the test, because the EXISTING success path does exactly this:

    repo.update_status(..., status=COMPLETED, order_id=trade_result.get('order_id'), ...)

where `trade_result['order_id']` is `result.exchange_order_id` -- the VENUE's identifier,
straight from `create_order`'s response. A venue id is not a row in this platform's own
`orders` table, so if the constraint bites, the COMPLETED write fails.

This probe establishes, against the live catalogue and with a transaction that is rolled
back, whether:
  1. a venue-shaped id is actually refused;
  2. an id present in `orders` is accepted;
  3. what happens to the session afterwards -- because `update_status` commits, and a
     failed commit leaves the transaction aborted, so the enclosing error handler's own
     write would fail too.

Writes nothing: everything happens inside a transaction that is rolled back.
"""
from __future__ import annotations

import pathlib
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2


def main() -> int:
    dsn = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
    conn = psycopg2.connect(dsn)
    conn.autocommit = False

    tenant = str(uuid.uuid4())
    exec_id = f"probe_fk_{uuid.uuid4().hex[:10]}"

    print("=" * 78)
    print("DOES A VENUE ORDER ID FIT execution_records.order_id?")
    print("=" * 78)

    try:
        with conn.cursor() as cur:
            # Minimal parent rows so the tenant FK is satisfied.
            cur.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (tenant, f"probe-{tenant[:8]}@example.invalid"),
            )
            cur.execute(
                "INSERT INTO public.profiles (id, email) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (tenant, f"probe-{tenant[:8]}@example.invalid"),
            )
            cur.execute(
                "INSERT INTO public.execution_records "
                "  (execution_id, tenant_id, strategy_id, symbol, side, size, price, "
                "   status, created_at, updated_at) "
                "VALUES (%s, %s, 'probe', 'BTCUSDT', 'buy', '1', '1000', "
                "        CAST('EXECUTING' AS executionstatus), now(), now())",
                (exec_id, tenant),
            )
            print("SETUP = ok (pending row inserted inside a transaction)")

            # 1. A venue-shaped id, i.e. what create_order actually returns.
            cur.execute("SAVEPOINT s1")
            try:
                cur.execute(
                    "UPDATE public.execution_records SET order_id = %s, "
                    "       status = CAST('COMPLETED' AS executionstatus) "
                    " WHERE execution_id = %s",
                    ("1234567890", exec_id),
                )
                print("VENUE_NUMERIC_ID = ACCEPTED")
                cur.execute("ROLLBACK TO SAVEPOINT s1")
            except Exception as exc:
                print(f"VENUE_NUMERIC_ID = REJECTED  {exc.__class__.__name__}: "
                      f"{str(exc).splitlines()[0]}")
                cur.execute("ROLLBACK TO SAVEPOINT s1")

            cur.execute("SAVEPOINT s2")
            try:
                cur.execute(
                    "UPDATE public.execution_records SET order_id = %s "
                    " WHERE execution_id = %s",
                    ("VENUE-ABC-123", exec_id),
                )
                print("VENUE_STRING_ID = ACCEPTED")
                cur.execute("ROLLBACK TO SAVEPOINT s2")
            except Exception as exc:
                print(f"VENUE_STRING_ID = REJECTED  {exc.__class__.__name__}: "
                      f"{str(exc).splitlines()[0]}")
                cur.execute("ROLLBACK TO SAVEPOINT s2")

            # 2. An id that IS present in orders.
            cur.execute("SELECT id FROM public.orders LIMIT 1")
            existing = cur.fetchone()
            if existing:
                cur.execute("SAVEPOINT s3")
                try:
                    cur.execute(
                        "UPDATE public.execution_records SET order_id = %s "
                        " WHERE execution_id = %s",
                        (existing[0], exec_id),
                    )
                    print(f"EXISTING_ORDERS_ID = ACCEPTED  ({existing[0]!r})")
                    cur.execute("ROLLBACK TO SAVEPOINT s3")
                except Exception as exc:
                    print(f"EXISTING_ORDERS_ID = REJECTED  {exc.__class__.__name__}")
                    cur.execute("ROLLBACK TO SAVEPOINT s3")
            else:
                print("EXISTING_ORDERS_ID = no orders rows to test with")

            # 3. What the venue actually returns, per exchange_executor:
            #    `order_id_str = str(response.get("id"))`. Show the shape so the
            #    conclusion is not hypothetical.
            print()
            print("NOTE: exchange_executor builds order_id as str(create_order "
                  "response['id']) -- the VENUE's identifier, which is not generated "
                  "by this platform and therefore has no reason to exist in "
                  "public.orders.")

            cur.execute("SELECT count(*) FROM public.orders")
            print("ORDERS_ROWS =", cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM public.execution_records "
                " WHERE order_id IS NOT NULL"
            )
            print("EXECUTION_RECORDS_WITH_ORDER_ID =", cur.fetchone()[0])
    finally:
        conn.rollback()
        conn.close()
        print()
        print("ROLLED BACK - this probe wrote nothing.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
