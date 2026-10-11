"""Report which column set each installed referral function actually writes.

Two migrations define the same three functions against two different shapes of
`referral_wallets`:

    006_reconcile_production_database.sql   balance_usd / total_earned_usd / pending_usd
    referral_system_redesign.sql            pending_balance_usd / approved_balance_usd /
                                            paid_balance_usd / lifetime_earnings_usd

`CREATE OR REPLACE FUNCTION` means the file that runs LAST wins, and which one that is
decides whether the old columns are still load-bearing. Read from `pg_proc` rather than
from the migration files, because the question is what the database has.
"""

from __future__ import annotations

import sys

import psycopg2

from dsn import resolve_dsn

FUNCS = (
    "create_referral_profile",
    "process_referral_commission",
    "reverse_referral_commission",
    "create_referral_code_for_user",
)

OLD = ("balance_usd", "total_earned_usd", "pending_usd")
NEW = (
    "pending_balance_usd",
    "approved_balance_usd",
    "paid_balance_usd",
    "lifetime_earnings_usd",
)


def main() -> int:
    conn = psycopg2.connect(resolve_dsn())
    conn.set_session(readonly=True, autocommit=True)

    print("=" * 78)
    print("INSTALLED REFERRAL FUNCTIONS -- which referral_wallets shape each one writes")
    print("=" * 78)

    for name in FUNCS:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.proname, pg_get_functiondef(p.oid) "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname = %s",
                (name,),
            )
            rows = cur.fetchall()
        if not rows:
            print(f"  {name:<32} NOT INSTALLED")
            continue
        for _, body in rows:
            # `pending_usd` is a substring of nothing in NEW, but `balance_usd` IS a
            # substring of `pending_balance_usd`, so the old-shape test has to exclude
            # the new names first or every function looks like it uses both.
            stripped = body
            for new in NEW:
                stripped = stripped.replace(new, "")
            uses_old = [c for c in OLD if c in stripped]
            uses_new = [c for c in NEW if c in body]
            if uses_old and uses_new:
                verdict = "BOTH -- divergent"
            elif uses_old:
                verdict = "006 three-column shape"
            elif uses_new:
                verdict = "redesign four-state shape"
            else:
                verdict = "touches referral_wallets in neither shape"
            print(f"  {name:<32} {verdict}")
            if uses_old:
                print(f"      old columns referenced: {', '.join(uses_old)}")
            if uses_new:
                print(f"      new columns referenced: {', '.join(uses_new)}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
