"""Exact pre-027 state of the referral ledger: columns, constraints, row counts, writers.

027 declares a `currency` column on three tables. Everything it assumes is read off the
live catalogue here first, including whether `NOT NULL DEFAULT 'USD'` would rewrite rows
that already exist and whether the installed credit function names the column.
"""
from __future__ import annotations

import os
import pathlib

import psycopg2
import psycopg2.extras

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = (os.environ.get("AERORA_TEST_DATABASE_URL") or "").strip() or (
    (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
)
print(f"DSN={DSN}")

TABLES = ("referral_commissions", "referral_wallets", "referral_payouts")

conn = psycopg2.connect(DSN, connect_timeout=10)
with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
    for table in TABLES:
        cur.execute("SELECT to_regclass(%s)::text", (f"public.{table}",))
        print(f"\n=== {table}  regclass={cur.fetchone()[0]}")

        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
            (table,),
        )
        cols = cur.fetchall()
        print(f"  COLUMN_COUNT={len(cols)}")
        for r in cols:
            print(
                f"    {r['column_name']:<26} {r['data_type']:<28} "
                f"null={r['is_nullable']:<3} default={r['column_default']}"
            )
        names = {r["column_name"] for r in cols}
        print(f"  HAS_CURRENCY={'currency' in names}")

        cur.execute(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = to_regclass(%s) ORDER BY 1",
            (f"public.{table}",),
        )
        for r in cur.fetchall():
            print(f"  CONSTRAINT {r['conname']}: {r['def']}")

        cur.execute(f'SELECT count(*) FROM public."{table}"')
        print(f"  ROW_COUNT={cur.fetchone()[0]}")

        cur.execute(
            "SELECT c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner) AS owner "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=%s",
            (table,),
        )
        r = cur.fetchone()
        if r:
            print(f"  rls={r['relrowsecurity']} forced={r['relforcerowsecurity']} owner={r['owner']}")

    # Every installed function that writes the ledger, and whether it names `currency`.
    print("\n=== INSTALLED FUNCTIONS THAT WRITE THE REFERRAL LEDGER")
    cur.execute(
        """
        SELECT p.oid::regprocedure::text AS sig,
               pg_get_functiondef(p.oid) ILIKE '%currency%' AS names_currency,
               pg_get_functiondef(p.oid) ILIKE '%insert into%referral_commissions%' AS writes_comm,
               pg_get_functiondef(p.oid) ILIKE '%insert into%referral_wallets%'    AS writes_wallet,
               pg_get_functiondef(p.oid) ILIKE '%insert into%referral_payouts%'    AS writes_payout
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.prokind = 'f'
           AND pg_get_functiondef(p.oid) ILIKE '%referral_%'
         ORDER BY 1
        """
    )
    for r in cur.fetchall():
        if r["writes_comm"] or r["writes_wallet"] or r["writes_payout"]:
            print(
                f"  {r['sig']}\n"
                f"      names_currency={r['names_currency']} "
                f"comm={r['writes_comm']} wallet={r['writes_wallet']} payout={r['writes_payout']}"
            )

conn.rollback()
conn.close()
