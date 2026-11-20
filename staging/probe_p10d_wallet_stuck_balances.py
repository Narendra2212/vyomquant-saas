"""PHASE 10 WORKSTREAM D — how much real money the unimplemented settlement step holds.

The pending -> approved step has no writer, and `POST /referral/payouts` validates against
`approved_balance_usd` with a $10 minimum. If balances are sitting in `pending_balance_usd`
with nothing able to move them, every referrer is unable to withdraw and the figure is the
size of the stuck obligation. Read-only: SELECT only, no transaction opened for writing.
"""
from __future__ import annotations

import pathlib

import psycopg2

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()

conn = psycopg2.connect(DSN)
cur = conn.cursor()

print("=" * 78)
print("PHASE 10 / WS-D — referral_wallets balance distribution (read-only)")
print("=" * 78)

cur.execute("""
    select count(*)                                        as wallets,
           coalesce(sum(pending_balance_usd), 0)           as pending_total,
           coalesce(sum(approved_balance_usd), 0)          as approved_total,
           coalesce(sum(paid_balance_usd), 0)              as paid_total,
           coalesce(sum(lifetime_earnings_usd), 0)         as lifetime_total,
           count(*) filter (where pending_balance_usd  > 0) as with_pending,
           count(*) filter (where approved_balance_usd > 0) as with_approved
      from public.referral_wallets
""")
(wallets, pending, approved, paid, lifetime, with_pending, with_approved) = cur.fetchone()

print(f"  wallet rows ................................ {wallets}")
print(f"  SUM pending_balance_usd .................... {pending}")
print(f"  SUM approved_balance_usd ................... {approved}")
print(f"  SUM paid_balance_usd ....................... {paid}")
print(f"  SUM lifetime_earnings_usd .................. {lifetime}")
print(f"  wallets with pending  > 0 .................. {with_pending}")
print(f"  wallets with approved > 0 .................. {with_approved}")

cur.execute("select count(*) from public.referral_commissions")
print(f"  referral_commissions rows .................. {cur.fetchone()[0]}")
cur.execute("select count(*) from public.referral_payouts")
print(f"  referral_payouts rows ...................... {cur.fetchone()[0]}")
cur.execute("select count(*) from public.referral_obligations")
print(f"  referral_obligations rows .................. {cur.fetchone()[0]}")

print("-" * 78)
if with_pending == 0 and with_approved == 0 and pending == 0 and approved == 0:
    print("  NO MONEY IS CURRENTLY STUCK: every wallet balance is zero, so the missing")
    print("  pending -> approved step holds nothing today. It is a capability gap that")
    print("  blocks the FIRST payout, not a present loss.")
else:
    print(f"  {pending} USD sits in pending_balance_usd with no implemented route to")
    print("  approved_balance_usd, and POST /referral/payouts validates against")
    print("  approved_balance_usd with a $10 minimum — so that amount is unwithdrawable.")

conn.close()
