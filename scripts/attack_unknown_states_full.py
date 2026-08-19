"""
UNKNOWN STATE -> DEFINITIVE STATE HOSTILE ATTACK SUITE
BUG-UK-01: fetch_open_orders exception returns [] -> ALL orders cancelled
BUG-UK-02: DB unavailable -> subscription defaults to "active" (fail-open)
BUG-UK-03: Any middleware exception -> pass -> request proceeds (fail-open)
BUG-UK-04: Redis miss -> get_user_subscription returns "active" (fail-open)
BUG-UK-05: ReconciliationEngine ghost-cancel on empty exchange list
BUG-UK-06: Missing fill quantity field -> zero-quantity fill mutation
DO NOT CERTIFY.
"""
import sys, os, asyncio, concurrent.futures
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
sys.path.insert(0, r"c:\aerora_quant_backend_updated_final1")
import logging; logging.disable(logging.CRITICAL)

PASS_COUNT = 0; BUG_COUNT = 0; BUGS = []

def record_pass(label, proof="LOGIC_REPRODUCED"):
    global PASS_COUNT; PASS_COUNT += 1
    print(f"  [PASS][{proof}] {label}")

def record_bug(bug_id, severity, desc, expected, actual, impact):
    global BUG_COUNT; BUG_COUNT += 1; BUGS.append(bug_id)
    print(f"  [BUG][{severity}] {bug_id}: {desc}")
    print(f"    Expected: {expected}"); print(f"    Actual  : {actual}")
    print(f"    Impact  : {impact}")

print("=" * 72)
print("SECTION 1 - BUG-UK-01: fetch_open_orders exception must re-raise not return []")
print("=" * 72)

from backend_app.backend.exchange_reconciliation import ExchangeReconciliationService

class TimeoutExchange:
    async def fetch_open_orders(self): raise ConnectionError("Exchange timeout HTTP 504")
    async def fetch_order(self, oid, sym): raise ConnectionError("Exchange timeout HTTP 504")

mock_db = MagicMock(); mock_sm = MagicMock()
svc = ExchangeReconciliationService(db_session=mock_db, exchange_client=TimeoutExchange(), state_machine=mock_sm)

try:
    result = asyncio.run(svc._fetch_exchange_orders("tenant_test", None))
    if result == []:
        record_bug("BUG-UK-01","P0","_fetch_exchange_orders returned [] on timeout","re-raise","returned []","Mass order cancellation during exchange outage")
    else:
        record_bug("BUG-UK-01","P0",f"Unexpected return: {result}","re-raise",str(result),"unknown")
except Exception:
    record_pass("1.1 _fetch_exchange_orders re-raises on timeout (NOT empty list)")

# 1.2: reconcile_open_orders must abort, not cancel DB orders
class FakeSvc(ExchangeReconciliationService):
    def _fetch_db_active_orders(self, tenant_id, exchange_id=None):
        orders = []
        for i in range(3):
            o = MagicMock(); o.order_id=f"ord_{i}"; o.execution_id=f"exec_{i}"
            o.filled_size="0"; o.symbol="BTCUSDT"
            o.status = MagicMock(); o.status.value = "EXECUTING"
            orders.append(o)
        return orders
    def _update_sync_timestamp(self, ids): pass

svc2 = FakeSvc(db_session=MagicMock(), exchange_client=TimeoutExchange(), state_machine=mock_sm)
try:
    results = asyncio.run(svc2.reconcile_open_orders("tenant_test"))
    cancel_actions = [r for r in results if hasattr(r,"action") and "cancel" in str(r.action).lower()]
    if cancel_actions:
        record_bug("BUG-UK-01b","P0",f"reconcile_open_orders cancelled {len(cancel_actions)} orders on timeout","0 cancellations",f"{len(cancel_actions)} cancels","Mass cancellation")
    else:
        record_pass("1.2 reconcile_open_orders: no cancellations after timeout (abort guard works)")
except Exception:
    record_pass("1.2 reconcile_open_orders aborted (raised) on exchange timeout - no cancellations possible")

print()
print("=" * 72)
print("SECTION 2 - BUG-UK-02/03: Subscription middleware fail-open attacks")
print("=" * 72)

from backend_app.core.subscription_middleware import SubscriptionMiddleware
from fastapi import HTTPException, Response

middleware = SubscriptionMiddleware(app=MagicMock())

def _make_req(path="/api/bots/start", uid="usr_cancelled"):
    req = MagicMock()
    req.url = MagicMock(); req.url.path = path
    req.state = MagicMock(); req.state.user = {"id": uid, "access_token": "tok"}
    return req

# 2.1 DB unavailable -> must NOT default to active
async def run21():
    req = _make_req("/api/bots/start")
    call_next = AsyncMock(return_value=Response(content=b"ok"))
    with patch("backend_app.core.subscription_middleware.redis_manager.get", AsyncMock(return_value=None)), \
         patch("backend_app.core.subscription_middleware.redis_manager.setex", AsyncMock()), \
         patch("backend_app.core.dependencies.get_request_supabase", return_value=None):
        try:
            await middleware.dispatch(req, call_next)
            return "ALLOWED" if call_next.called else "BLOCKED"
        except HTTPException as e:
            return "BLOCKED" if e.status_code in (403,503) else f"WRONG_{e.status_code}"

r21 = asyncio.run(run21())
if r21 == "BLOCKED":
    record_pass("2.1 DB unavailable -> subscription gate blocks request (not fail-open)")
else:
    record_bug("BUG-UK-02","P1","DB unavailable -> middleware defaulted to active (fail-open)","BLOCKED",r21,"Cancelled users access paid features when DB down")

# 2.2 Redis exception -> must NOT silently pass
async def run22():
    req = _make_req("/api/strategies/create")
    call_next = AsyncMock(return_value=Response(content=b"ok"))
    with patch("backend_app.core.subscription_middleware.redis_manager.get", AsyncMock(side_effect=RuntimeError("Redis down"))):
        try:
            await middleware.dispatch(req, call_next)
            return "ALLOWED" if call_next.called else "BLOCKED"
        except HTTPException as e:
            return "BLOCKED" if e.status_code in (403,503) else f"WRONG_{e.status_code}"

r22 = asyncio.run(run22())
if r22 == "BLOCKED":
    record_pass("2.2 Redis exception -> 503 returned (not silent pass-through)")
else:
    record_bug("BUG-UK-03","P0","Middleware exception silently allowed request","503","ALLOWED","All users bypass subscription gate on Redis failure")

# 2.3 Billing path exempt
async def run23():
    req = _make_req("/api/billing/webhook")
    call_next = AsyncMock(return_value=Response(content=b"ok"))
    with patch("backend_app.core.subscription_middleware.redis_manager.get", AsyncMock(return_value=None)), \
         patch("backend_app.core.dependencies.get_request_supabase", return_value=None):
        try:
            await middleware.dispatch(req, call_next)
            return "ALLOWED" if call_next.called else "BLOCKED"
        except HTTPException:
            return "BLOCKED"

r23 = asyncio.run(run23())
if r23 == "ALLOWED":
    record_pass("2.3 Billing/webhook path correctly bypasses gate even when DB unavailable")
else:
    record_bug("BUG-UK-03b","P1","Billing path blocked when DB unavailable","ALLOWED",r23,"Users cannot renew subscription")

print()
print("=" * 72)
print("SECTION 3 - BUG-UK-04: get_user_subscription Redis-miss fail-open")
print("=" * 72)

from backend_app.core.subscription_middleware import get_user_subscription

async def test31():
    with patch("backend_app.core.subscription_middleware.redis_manager.get", AsyncMock(return_value=None)):
        return await get_user_subscription("usr_test")

r31 = asyncio.run(test31())
if r31.get("status") == "unknown":
    record_pass("3.1 get_user_subscription Redis miss returns 'unknown' (not 'active')")
else:
    record_bug("BUG-UK-04","P1",f"Redis miss returned status='{r31.get('status')}'","status=unknown",f"status={r31.get('status')}","Cancelled subscribers trade freely when Redis cache expires")

async def test32():
    with patch("backend_app.core.subscription_middleware.redis_manager.get", AsyncMock(side_effect=ConnectionError("Redis down"))):
        return await get_user_subscription("usr_test")

r32 = asyncio.run(test32())
if r32.get("status") == "unknown":
    record_pass("3.2 get_user_subscription Redis exception returns 'unknown' (not 'active')")
else:
    record_bug("BUG-UK-04b","P1",f"Redis exception returned status='{r32.get('status')}'","status=unknown",f"status={r32.get('status')}","Cancelled subscribers trade freely when Redis cluster is down")

# 3.3 CommandWorker blocks "unknown" status
def run_worker_test():
    async def _inner():
        mock_fleet = MagicMock(); mock_fleet.start_bot = AsyncMock()
        mock_app = MagicMock(); mock_app.fleet = mock_fleet
        from backend_app.workers.command_worker import CommandWorker
        worker = CommandWorker()
        msg = [("command_queue", [("msg-001", {"action": "start_bot", "payload": '{"user_id":"usr_c","symbol":"BTCUSDT","blueprint":{}}'})])]
        with patch("backend_app.workers.command_worker.redis_manager.xreadgroup", AsyncMock(return_value=msg), create=True), \
             patch("backend_app.workers.command_worker.redis_manager.xack", AsyncMock(), create=True), \
             patch("backend_app.workers.command_worker.app_state", mock_app), \
             patch("backend_app.core.subscription_middleware.redis_manager.get", AsyncMock(return_value=None)):
            await worker.process_iteration()
        return mock_fleet.start_bot.call_count
    return asyncio.run(_inner())

with concurrent.futures.ThreadPoolExecutor() as ex:
    start_calls = ex.submit(run_worker_test).result()

if start_calls == 0:
    record_pass("3.3 CommandWorker blocks start_bot when status='unknown' (Redis unavailable)")
else:
    record_bug("BUG-UK-04c","P0","CommandWorker allowed start_bot when status='unknown'","0 calls",f"{start_calls} calls","Cancelled subscribers start bots when Redis is down")

print()
print("=" * 72)
print("SECTION 4 - BUG-UK-05: ReconciliationEngine ghost-cancel on empty exchange list")
print("=" * 72)

from backend_app.core.reconciliation_engine import ReconciliationEngine

async def test41():
    engine = ReconciliationEngine(MagicMock(), MagicMock(), None, auto_correct=False, enable_fill_reconciliation=False)
    local_orders = [{"order_id":f"ord_{i}","status":"open","quantity":"1.0"} for i in range(5)]
    mismatches = await engine._reconcile_orders(local_orders=local_orders, exchange_orders=[], tenant_id="t")
    return [m for m in mismatches if m.mismatch_type=="ghost_order"]

ghosts = asyncio.run(test41())
if len(ghosts) == 0:
    record_pass("4.1 Empty exchange list + 5 active local orders -> 0 ghost-cancel mismatches (abort guard active)")
else:
    record_bug("BUG-UK-05","P0",f"Empty exchange list caused {len(ghosts)} ghost_order mismatches","0 ghost mismatches",f"{len(ghosts)} mismatches","Exchange timeout -> entire order book cancelled")

async def test42():
    engine = ReconciliationEngine(MagicMock(), MagicMock(), None, auto_correct=False, enable_fill_reconciliation=False)
    local = [{"order_id":"A","status":"open","quantity":"1"},{"order_id":"B","status":"open","quantity":"1"}]
    exchange = [{"order_id":"A","status":"open","quantity":"1"},{"order_id":"B","status":"open","quantity":"1"},{"order_id":"X","status":"open","quantity":"2"}]
    mismatches = await engine._reconcile_orders(local_orders=local, exchange_orders=exchange, tenant_id="t")
    return [m for m in mismatches if m.mismatch_type=="missing_order"]

missing = asyncio.run(test42())
if len(missing) == 1:
    record_pass("4.2 Legitimate missing_order (exchange has it, local doesn't) still detected with guard active")
else:
    record_bug("BUG-UK-05b","P1","Missing order detection broken after guard","1 mismatch",str(len(missing)),"Ghost order detection disabled")

print()
print("=" * 72)
print("SECTION 5 - BUG-UK-06: Missing fill quantity must not create zero-quantity fill")
print("=" * 72)

async def test51():
    engine = ReconciliationEngine(MagicMock(), MagicMock(), None, auto_correct=False, enable_fill_reconciliation=True)
    exchange_fills = [{"exchange_trade_id":"fill_no_qty","order_id":"ord_1","price":"50000"}]
    mismatches = await engine._reconcile_fills(local_fills=[], exchange_fills=exchange_fills, tenant_id="t")
    return mismatches

m51 = asyncio.run(test51())
if len(m51) == 0:
    record_pass("5.1 Fill with missing 'quantity' field rejected - no zero-quantity fill mismatch created")
else:
    record_bug("BUG-UK-06","P1",f"Fill missing quantity created {len(m51)} mismatches","0 mismatches",str(len(m51)),"Zero-quantity fill corrupts position ledger")

async def test52():
    engine = ReconciliationEngine(MagicMock(), MagicMock(), None, auto_correct=False, enable_fill_reconciliation=True)
    exchange_fills = [{"exchange_trade_id":"fill_zero","order_id":"ord_1","quantity":"0","price":"50000"}]
    mismatches = await engine._reconcile_fills(local_fills=[], exchange_fills=exchange_fills, tenant_id="t")
    return mismatches

m52 = asyncio.run(test52())
if len(m52) == 0:
    record_pass("5.2 Fill with quantity=0 rejected - no zero-quantity fill mismatch created")
else:
    record_bug("BUG-UK-06b","P1",f"Fill quantity=0 created {len(m52)} mismatches","0 mismatches",str(len(m52)),"Zero-quantity create_fill corrupts ledger")

async def test53():
    engine = ReconciliationEngine(MagicMock(), MagicMock(), None, auto_correct=False, enable_fill_reconciliation=True)
    exchange_fills = [{"exchange_trade_id":"fill_valid","order_id":"ord_2","quantity":"0.5","price":"50000"}]
    mismatches = await engine._reconcile_fills(local_fills=[], exchange_fills=exchange_fills, tenant_id="t")
    return [m for m in mismatches if m.exchange_quantity > Decimal("0")]

valid = asyncio.run(test53())
if len(valid) == 1:
    record_pass("5.3 Valid fill (qty=0.5) still detected as missing_fill after quantity guard")
else:
    record_bug("BUG-UK-06c","P1","Valid fill not detected after zero-quantity guard added","1 mismatch",str(len(valid)),"Legitimate missing fills not recovered")

print()
print("=" * 72)
print(f"UNKNOWN STATE AUDIT COMPLETE")
print(f"PASS: {PASS_COUNT} | BUGS FOUND: {BUG_COUNT}")
print("=" * 72)
if BUGS:
    for b in BUGS: print(f"  {b}")
if BUG_COUNT > 0:
    sys.exit(1)
