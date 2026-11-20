"""Reproduce Stage 6A: a third-asset fee writes fee_pct=NULL, and the provisioned column
is NOT NULL, so the insert is refused and the (real) fee is lost. Read-only except for a
rolled-back savepoint, so the database is unchanged."""
from __future__ import annotations

import pathlib
import uuid
import psycopg2

ROOT = pathlib.Path(__file__).resolve().parents[1]
dsn = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
c = psycopg2.connect(dsn)
cur = c.cursor()

# Current nullability of fee_pct.
cur.execute("""
    SELECT is_nullable FROM information_schema.columns
    WHERE table_schema='public' AND table_name='fee_records' AND column_name='fee_pct'
""")
print("fee_pct is_nullable (before):", cur.fetchone()[0])

# A real tenant to satisfy NOT NULL tenant_id.
tenant = str(uuid.uuid4())
cur.execute("INSERT INTO auth.users (id, email) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (tenant, f"{tenant[:8]}@fee.test"))
cur.execute("INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING", (tenant,))
c.commit()

cur.execute("SAVEPOINT s")
try:
    # Exactly the shape record_fee writes for a THIRD-ASSET fee: fee_pct NULL, everything
    # else present (the fee amount and asset are preserved).
    cur.execute(
        "INSERT INTO public.fee_records "
        "(fee_id, tenant_id, order_id, symbol, side, fee_type, fee_amount, fee_asset, "
        " fee_pct, trade_volume, status, exchange_id, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())",
        (f"fee_{uuid.uuid4().hex}", tenant, "ord-1", "BTC/USDT", "buy", "MAKER",
         "0.0005", "BNB", None, "61234.50", "DEDUCTED", "binance"),
    )
    print("THIRD-ASSET FEE INSERT: SUCCEEDED (fee_pct NULL accepted)")
except psycopg2.Error as e:
    print("THIRD-ASSET FEE INSERT: REFUSED ->", type(e).__name__, str(e).splitlines()[0])
cur.execute("ROLLBACK TO SAVEPOINT s")

c.rollback()
c.close()
