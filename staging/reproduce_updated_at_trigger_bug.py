"""Reproduce: does EVERY UPDATE to library_subscriptions fail, including a completely
legitimate service-role settlement confirmation -- because
`trg_library_subscriptions_updated_at` unconditionally assigns `NEW.updated_at`, and
`library_subscriptions` has no `updated_at` column?

This is not an attack scenario. This is the question of whether the real payment
confirmation path (`settlement_service.settle` -> `UPDATE library_subscriptions SET
status='active', ...`) can succeed AT ALL in this schema.

Tested as `postgres` (superuser, bypasses RLS -- exactly the privilege the service-role
client effectively has for this purpose) issuing the SAME shape of UPDATE
`settlement_service.py:1549` issues: a `status` transition plus the period/split columns,
guarded by `.eq("id", ...)`.

Rolled back unconditionally.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2

sys.path.insert(0, "staging")
from dsn import resolve_dsn  # noqa: E402


def main() -> int:
    conn = psycopg2.connect(resolve_dsn())
    conn.autocommit = False
    author = str(uuid.uuid4())
    buyer = str(uuid.uuid4())

    try:
        with conn.cursor() as cur:
            for ident, label in ((author, "author"), (buyer, "buyer")):
                cur.execute(
                    "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
                    (ident, f"trigbug-{label}-{ident[:8]}@example.invalid"),
                )
                cur.execute("INSERT INTO public.profiles (id) VALUES (%s)", (ident,))

            cur.execute(
                "INSERT INTO public.library_strategies "
                "(author_id, source_strategy_id, name, category, symbol, timeframe, "
                " exchange_id, moderation_status, is_active, price_minor, currency) "
                "VALUES (%s, %s, 'probe-listing', 'trend', 'BTC/USDT', '1h', 'binance', "
                "        'approved', true, 2999, 'USD') RETURNING id",
                (author, str(uuid.uuid4())),
            )
            listing_id = cur.fetchone()[0]

            cur.execute(
                "INSERT INTO public.library_subscriptions "
                "(library_id, user_id, status, subscription_tier, price_minor, currency) "
                "VALUES (%s, %s, 'pending', 'pro', 2999, 'USD') RETURNING id",
                (listing_id, buyer),
            )
            sub_id = cur.fetchone()[0]

            # The settlement row MUST exist first -- the guard trigger demands it, and
            # this probe is specifically about what happens AFTER a real payment, not
            # about bypassing the payment gate (that is the companion probe).
            cur.execute(
                "INSERT INTO public.marketplace_settlements "
                "(subscription_id, listing_id, owner_id, purchaser_id, amount_minor, "
                " currency, owner_share_minor, platform_fee_minor, provider, "
                " provider_reference, is_reversal, settled_at) "
                "VALUES (%s, %s, %s, %s, 2999, 'USD', 2699, 300, 'stripe', %s, false, now())",
                (sub_id, listing_id, author, buyer, f"pi_probe_{uuid.uuid4().hex[:12]}"),
            )
            print("seeded: PENDING subscription + a REAL, paid, non-reversal "
                  "marketplace_settlements row -- exactly what a successful Stripe "
                  "webhook produces before settlement_service tries to activate it")

            now = datetime.now(timezone.utc)
            expiry = now + timedelta(days=30)

            # This is the exact shape of settlement_service.py:1549-1553's UPDATE: as the
            # privileged service role (here: postgres, which bypasses RLS the same way),
            # transitioning PENDING -> ACTIVE with the period and split populated.
            cur.execute("SAVEPOINT attempt")
            try:
                cur.execute(
                    "UPDATE public.library_subscriptions SET "
                    "  status = 'active', "
                    "  period_start = %s, "
                    "  period_expiry = %s, "
                    "  owner_share_minor = 2699, "
                    "  platform_fee_minor = 300 "
                    "WHERE id = %s",
                    (now, expiry, sub_id),
                )
                print(f"LEGITIMATE settlement UPDATE affected {cur.rowcount} row(s): SUCCESS")
                cur.execute("RELEASE SAVEPOINT attempt")
                blocked = False
            except psycopg2.Error as exc:
                cur.execute("ROLLBACK TO SAVEPOINT attempt")
                print(f"LEGITIMATE settlement UPDATE REFUSED: {exc.pgcode} "
                      f"{str(exc).splitlines()[0]}")
                blocked = True

            if blocked:
                print()
                print("CONFIRMED P0: library_subscriptions carries a trigger "
                      "(trg_library_subscriptions_updated_at) that unconditionally "
                      "assigns NEW.updated_at on every UPDATE, but the table has NO "
                      "updated_at column. EVERY update to this table fails with "
                      "42703, including the legitimate settlement confirmation that "
                      "activates a subscription after real payment. A paying customer's "
                      "subscription can never reach 'active' through this code path on "
                      "a database provisioned by this migration set.")
                return 1

            print()
            print("NOT reproduced on this database -- the trigger did not fire as "
                  "described, or the column exists after all.")
            return 0
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
