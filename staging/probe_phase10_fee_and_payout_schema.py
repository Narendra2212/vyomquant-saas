"""Read-only probe for Stage 6 observations A (fee_records.fee_pct nullability) and B
(referral_payouts column name: payment_method vs payout_method). Grounds the remediation in
the ACTUAL provisioned schema rather than the ORM model or the migration text alone."""
from __future__ import annotations

import pathlib
import psycopg2

ROOT = pathlib.Path(__file__).resolve().parents[1]
dsn = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
c = psycopg2.connect(dsn)
cur = c.cursor()

def cols(table):
    cur.execute("""
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
        ORDER BY ordinal_position
    """, (table,))
    return cur.fetchall()

for t in ("fee_records", "referral_payouts"):
    cur.execute("SELECT to_regclass(%s)", (f"public.{t}",))
    present = cur.fetchone()[0]
    print("=" * 70)
    print(f"TABLE public.{t}: {'present' if present else 'ABSENT in provisioned DB'}")
    print("=" * 70)
    if present:
        for row in cols(t):
            print(f"  {row[0]:<28} {row[1]:<24} nullable={row[2]:<4} default={row[3]}")

# For referral_payouts specifically: is there a payout_method or payment_method column?
print()
print("referral_payouts method-column check:")
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_schema='public' AND table_name='referral_payouts'
      AND column_name IN ('payment_method','payout_method')
""")
found = [r[0] for r in cur.fetchall()]
print("  columns found:", found or "(neither — table may be absent or named differently)")

c.close()
