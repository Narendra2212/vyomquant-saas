"""Confirm every Workstream D fix is still on disk.

Written because a concurrent tab was editing `fee_engine.py` and `referral.py` in the same
session (report section 0). A whole-file write from a stale buffer would silently drop these
edits, so this is the cheap check that they survived.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

CHECKS = [
    ("backend_app/routers/referral.py", "_compensate_debit",
     "sec 2  payout debit compensated on BOTH failure modes"),
    ("backend_app/routers/referral.py", '"payout_method": payout_request.payment_method',
     "sec 2  payout insert uses the live column name (other tab)"),
    ("backend_app/backend/fee_engine.py", "totals_by_asset",
     "sec 1b A  fee summary groups by denomination"),
    ("backend_app/backend/fee_engine.py", "{ft: Decimal('0') for ft in FeeType}",
     "sec 1b B  summary covers the whole FeeType enum"),
    ("backend_app/backend/fee_engine.py", "uuid4().hex[:12]",
     "sec 1b C  fee_id unique per call"),
    ("backend_app/backend/fee_engine.py", "fee_funding_{position_id}_{funding_period}",
     "sec 1b C  funding fee keeps its deterministic idempotency key"),
    ("backend_app/backend/fee_engine.py", "fee_pct = Column(String, nullable=True)",
     "sec 1  fee_pct nullable in the ORM (other tab)"),
    ("backend_app/backend/exchange_reconciliation.py",
     "cannot reconcile: no exchange client",
     "sec 4  no exchange client refuses instead of returning []"),
    ("backend_app/backend/exchange_reconciliation.py", "RECONCILIATION WITHHELD",
     "sec 4  unconfirmable missing order left alone"),
    ("backend_app/backend/exchange_websocket_listener.py", "NOT IMPLEMENTED for",
     "sec 4  WS fallback no longer imports a non-existent class"),
    ("backend_app/migrations/031_fee_records_fee_pct_nullable.sql", "DROP NOT NULL",
     "sec 1  migration 031 present (other tab, APPLIED)"),
    ("backend_app/migrations/032_referral_payouts_terminal_evidence.sql", "NOT APPLIED",
     "sec 3  migration 032 prepared and still declared unapplied"),
]

TESTS = [
    "tests/test_referral_payout_debit_is_compensated_on_insert_failure.py",
    "tests/test_p10d_exchange_reconciliation_fails_safely.py",
    "tests/test_p10d_referral_settlement_lifecycle_postgres.py",
    "tests/test_p10d_fee_summary_does_not_mix_denominations_postgres.py",
]

missing = 0
print("=" * 78)
print("PHASE 10 / WS-D — are all fixes still on disk?")
print("=" * 78)
for rel, needle, label in CHECKS:
    path = ROOT / rel
    ok = path.exists() and needle in path.read_text(encoding="utf-8", errors="replace")
    if not ok:
        missing += 1
    print(f"  [{'ok ' if ok else 'GONE'}] {label}")

print("-" * 78)
for rel in TESTS:
    ok = (ROOT / rel).exists()
    if not ok:
        missing += 1
    print(f"  [{'ok ' if ok else 'GONE'}] test file {rel}")

print("=" * 78)
print("ALL PRESENT" if missing == 0 else f"{missing} ITEM(S) LOST — re-apply before reporting")
sys.exit(0 if missing == 0 else 1)
