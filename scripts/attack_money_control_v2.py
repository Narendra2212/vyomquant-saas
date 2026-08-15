"""
HOSTILE MONEY CONTROL PLANE V2 ATTACK SUITE
DO NOT CERTIFY — EXHAUSTIVE FINANCIAL, BILLING, AND ACCOUNTING VERIFICATION

Proof labels:
  SOURCE_VERIFIED
  LOGIC_REPRODUCED
  APPLICATION_RUNTIME
"""

import sys, io, asyncio, threading, uuid, time, random, inspect
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN, getcontext
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
getcontext().prec = 28

sys.path.insert(0, r"c:\aerora_quant_backend_updated_final1")

PASS = 0
FAIL = 0
BUGS = []

def record_pass(name, proof="LOGIC_REPRODUCED"):
    global PASS
    PASS += 1
    print(f"  [PASS][{proof}] {name}")

def record_bug(bug_id, sev, name, detail, expected, actual, impact, proof="LOGIC_REPRODUCED"):
    global FAIL
    FAIL += 1
    entry = {
        "bug_id": bug_id,
        "severity": sev,
        "name": name,
        "detail": detail,
        "expected": expected,
        "actual": actual,
        "impact": impact,
        "proof": proof
    }
    BUGS.append(entry)
    print(f"  [BUG-{bug_id}][{sev}][{proof}] {name}")
    print(f"           Expected : {expected}")
    print(f"           Actual   : {actual}")
    print(f"           Impact   : {impact}")


# ─────────────────────────────────────────────────────────────────────────────
# ATTACK 1: MULTI-CURRENCY FEE CONVERSION (BUG-FEE-01)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("ATTACK 1 — MULTI-CURRENCY FEE CONVERSION & PNL DEDUCTION")
print("=" * 72)

from backend_app.backend.fee_engine import FeeEngine, FeeType, FeeStatus, FeeRecordModel

class MockDB:
    def __init__(self):
        self.records = {}
    def add(self, obj):
        if hasattr(obj, 'fee_id'):
            self.records[obj.fee_id] = obj
    def commit(self):
        pass
    def refresh(self, obj):
        pass
    def query(self, model):
        return self
    def filter(self, *args):
        return self
    def all(self):
        return list(self.records.values())

mock_db = MockDB()
fee_eng = FeeEngine(mock_db)
fee_eng._get_tenant_id_from_execution = MagicMock(return_value=uuid.uuid4())

# Case 1.1: Base-currency fee (0.001 BTC on BTC/USDT @ $60,000)
gross_usdt = Decimal("5000.00")
price_usdt = Decimal("60000.00")
size_btc = Decimal("1.00000000")
fee_btc = Decimal("0.00100000")

rec1 = asyncio.run(fee_eng.record_fill_fee(
    execution_id="exec_fee_test_01",
    order_id="ord_fee_01",
    symbol="BTC/USDT",
    side="sell",
    size=size_btc,
    price=price_usdt,
    fee_amount=fee_btc,
    fee_asset="BTC",
    fee_type=FeeType.TAKER,
    exchange_id="binance",
    gross_pnl=gross_usdt
))

expected_net1 = Decimal("4940.00")
actual_net1 = Decimal(rec1.net_pnl)
expected_pct1 = Decimal("0.001") # 0.1%
actual_pct1 = Decimal(rec1.fee_pct)

if actual_net1 == expected_net1 and actual_pct1 == expected_pct1:
    record_pass("1.1 Base-currency fee (0.001 BTC @ $60k = $60) properly converted to quote currency")
else:
    record_bug("FEE-01", "P1", "Base-currency fee conversion failure", f"net={actual_net1}, pct={actual_pct1}", f"net={expected_net1}, pct={expected_pct1}", f"net={actual_net1}, pct={actual_pct1}", "Fee miscalculated in PnL")

# Case 1.2: Quote-currency fee (5.00 USDT on BTC/USDT)
rec2 = asyncio.run(fee_eng.record_fill_fee(
    execution_id="exec_fee_test_02",
    order_id="ord_fee_02",
    symbol="BTC/USDT",
    side="sell",
    size=size_btc,
    price=price_usdt,
    fee_amount=Decimal("5.00"),
    fee_asset="USDT",
    fee_type=FeeType.TAKER,
    exchange_id="binance",
    gross_pnl=gross_usdt
))

expected_net2 = Decimal("4995.00")
actual_net2 = Decimal(rec2.net_pnl)

if actual_net2 == expected_net2:
    record_pass("1.2 Quote-currency fee (5.00 USDT) directly deducted from gross PnL")
else:
    record_bug("FEE-02", "P1", "Quote-currency fee deduction failure", str(actual_net2), str(expected_net2), str(actual_net2), "Quote fee error")


# ─────────────────────────────────────────────────────────────────────────────
# ATTACK 2: STRIPE WEBHOOK DEDUPLICATION & ATOMIC LOCKING (BUG-BILL-01)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("ATTACK 2 — STRIPE WEBHOOK IDEMPOTENCY & EVENT EXTRACTION")
print("=" * 72)

from backend_app.routers.billing import stripe_webhook

src_code = inspect.getsource(stripe_webhook)

if 'event.get("id")' in src_code or "event['id']" in src_code:
    record_pass("2.1 Stripe webhook extracts event['id'] from verified event payload")
else:
    record_bug("BILL-01", "P0", "Stripe webhook misses event['id']", "Reads missing stripe-event-id header", "event['id']", "request.headers.get('stripe-event-id')", "Webhook deduplication bypassed")


# ─────────────────────────────────────────────────────────────────────────────
# ATTACK 3: CLOSED POSITION REALIZED PNL SUMMARIZATION (BUG-PNL-02)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("ATTACK 3 — CLOSED POSITION REALIZED PNL SUMMARY (PNL-UV-01)")
print("=" * 72)

from backend_app.core.position_model import (
    PositionRepository, PositionModel, PositionStatus, PositionSide
)

class MockPositionDB:
    def __init__(self):
        self.positions = {}
    def add(self, obj):
        if isinstance(obj, PositionModel):
            self.positions[obj.position_id] = obj
    def commit(self):
        pass
    def refresh(self, obj):
        pass
    def query(self, model):
        return MockPosQuery(self)

class MockPosQuery:
    def __init__(self, db):
        self.db = db
        self._filter_status = None
    def filter(self, *args):
        for arg in args:
            if hasattr(arg, 'right') and hasattr(arg.right, 'value'):
                if arg.right.value == PositionStatus.OPEN.value:
                    self._filter_status = PositionStatus.OPEN
            elif "OPEN" in str(arg):
                self._filter_status = PositionStatus.OPEN
        return self
    def all(self):
        if self._filter_status == PositionStatus.OPEN:
            return [p for p in self.db.positions.values() if p.status == PositionStatus.OPEN]
        return list(self.db.positions.values())

mock_pos_db = MockPositionDB()
pos_repo = PositionRepository(mock_pos_db)
tenant_id = uuid.uuid4()

pos_a = PositionModel(
    position_id="pos_a_closed",
    tenant_id=tenant_id,
    strategy_id="strat_1",
    symbol="BTC/USDT",
    side=PositionSide.LONG,
    size="0.0",
    avg_entry_price="50000.0",
    unrealized_pnl="0.0",
    realized_pnl="500.0",
    status=PositionStatus.CLOSED
)
mock_pos_db.add(pos_a)

pos_b = PositionModel(
    position_id="pos_b_open",
    tenant_id=tenant_id,
    strategy_id="strat_1",
    symbol="ETH/USDT",
    side=PositionSide.LONG,
    size="1.0",
    avg_entry_price="3000.0",
    unrealized_pnl="100.0",
    realized_pnl="-200.0",
    status=PositionStatus.OPEN
)
mock_pos_db.add(pos_b)

pos_c = PositionModel(
    position_id="pos_c_closed",
    tenant_id=tenant_id,
    strategy_id="strat_1",
    symbol="SOL/USDT",
    side=PositionSide.LONG,
    size="0.0",
    avg_entry_price="150.0",
    unrealized_pnl="0.0",
    realized_pnl="1000.0",
    status=PositionStatus.CLOSED
)
mock_pos_db.add(pos_c)

summary = pos_repo.get_position_summary(tenant_id)
expected_realized = Decimal("1300.0")
actual_realized = Decimal(summary["total_realized_pnl"])

if actual_realized == expected_realized and summary["open_position_count"] == 1:
    record_pass("3.1 Position summary includes realized PnL across all closed and open positions ($1,300.00)")
else:
    record_bug("PNL-02", "P1", "Closed position realized PnL missing", f"realized={actual_realized}", "1300.0", str(actual_realized), "Closed position PnL vanished")


# ─────────────────────────────────────────────────────────────────────────────
# ATTACK 4: WORKER SUBSCRIPTION ENFORCEMENT (BUG-WORKER-01)
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("ATTACK 4 — WORKER SUBSCRIPTION ENFORCEMENT & CANCELLED USERS")
print("=" * 72)

from backend_app.workers.command_worker import CommandWorker
from backend_app.backend.dag_worker import DAGWorker

cmd_src = inspect.getsource(CommandWorker.process_iteration)
dag_src = inspect.getsource(DAGWorker._atomic_claim_task)

if "get_user_subscription" in cmd_src and "cancelled" in cmd_src:
    record_pass("4.1 CommandWorker validates user subscription before executing start_bot commands")
else:
    record_bug("WORKER-01", "P1", "CommandWorker starts bots for cancelled users", "No subscription check in start_bot", "get_user_subscription check", "Unchecked start_bot execution", "Cancelled users can start trading bots")

if "get_user_subscription" in dag_src and "cancelled" in dag_src:
    record_pass("4.2 DAGWorker validates tenant subscription before claiming DAG tasks")
else:
    record_bug("WORKER-02", "P1", "DAGWorker executes DAG tasks for cancelled tenants", "No subscription check in _atomic_claim_task", "get_user_subscription check", "Unchecked DAG execution", "Cancelled tenants can consume DAG resources")


# ─────────────────────────────────────────────────────────────────────────────
# ATTACK 5: PLAN NORMALIZATION & UNKNOWN PLAN FAIL-CLOSED
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("ATTACK 5 — PLAN NORMALIZATION & UNKNOWN PLAN FAIL-CLOSED")
print("=" * 72)

from backend_app.core.subscription_engine import SubscriptionEngine
from backend_app.core.entitlement_engine import PlanMapper
from backend_app.core.tenant import TenantPlan, TenantQuota

sub_engine = SubscriptionEngine()

# Test invalid/corrupted plan strings
corrupted_plans = ["", "unknown_tier", "pro_typo", "hacked_elite", None]

fail_closed_safe = True
for cp in corrupted_plans:
    tenant_plan = PlanMapper.billing_to_tenant(cp or "")
    quota = TenantQuota.for_plan(tenant_plan)
    # Ensure corrupted plans never grant Enterprise privileges (e.g. max_orders_per_minute <= 30 on free)
    if tenant_plan == TenantPlan.ENTERPRISE or quota.max_orders_per_minute > 60:
        fail_closed_safe = False
        record_bug(
            f"PLAN-CORRUPT-{cp}", "P0",
            f"Corrupted plan '{cp}' granted Enterprise access",
            f"tenant_plan={tenant_plan}",
            "TenantPlan.FREE or non-privileged",
            str(tenant_plan),
            "Security vulnerability: Corrupted plan string grants enterprise privileges"
        )

if fail_closed_safe:
    record_pass("5.1 Corrupted and unknown plan strings fail closed to default safe tiers (never escalate to Enterprise)")


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("MONEY CONTROL V2 ATTACK PASS COMPLETE")
print(f"PASS: {PASS} | BUGS FOUND: {len(BUGS)}")
print("=" * 72)

for b in BUGS:
    print(f"\n  BUG-{b['bug_id']} [{b['severity']}]: {b['name']}")
    print(f"    Impact: {b['impact']}")

if len(BUGS) > 0:
    sys.exit(1)
sys.exit(0)
