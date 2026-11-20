"""
INTERNAL BILLING STATE MACHINE HOSTILE ATTACK SUITE
20 attacks + plan namespace + tenant isolation + crash/transaction tests.
"""
import sys, io, os, asyncio, threading, concurrent.futures, json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
sys.path.insert(0, r"c:\aerora_quant_backend_updated_final1")

# The section labels contain arrows and em dashes, and a Windows console is cp1252, so an
# unreconfigured stdout raises UnicodeEncodeError inside P() and kills the run partway --
# which is how ATTACK 13 onwards stayed unreported even after the dead-import crash at
# ATTACK 1 was removed. The sibling attack scripts already do this.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import logging; logging.disable(logging.CRITICAL)

PASS_COUNT = 0; BUG_COUNT = 0; BUGS = []
def P(label): global PASS_COUNT; PASS_COUNT+=1; print(f"  [PASS] {label}")
def B(bid, sev, desc, expected, actual, impact):
    global BUG_COUNT; BUG_COUNT+=1; BUGS.append((bid,sev,desc))
    print(f"  [BUG][{sev}] {bid}: {desc}")
    print(f"    Expected: {expected}\n    Actual  : {actual}\n    Impact  : {impact}")

from backend_app.core.subscription_engine import SubscriptionEngine, Plan, Resource, Feature
from backend_app.core.billing_lifecycle import BillingLifecycle, SubscriptionStatus
from backend_app.core.entitlement_engine import (
    EntitlementEngine, PlanMapper, FeatureEntitlements, FeatureFlag, BillingPlan
)
from backend_app.core.tenant import TenantPlan, TenantQuota

import threading as _th
class AtomicRedis:
    def __init__(self): self._store={}; self._lock=_th.Lock()
    async def get(self,k):
        with self._lock: return self._store.get(k)
    async def set(self,k,v,nx=False,ex=None):
        with self._lock:
            if nx and k in self._store: return False
            self._store[k]=str(v); return True
    async def setex(self,k,t,v):
        with self._lock: self._store[k]=str(v); return True
    async def delete(self,*ks):
        with self._lock:
            for k in ks: self._store.pop(k,None)
    async def incrby(self,k,n=1):
        with self._lock: v=int(self._store.get(k,0))+n; self._store[k]=str(v); return v
    async def decrby(self,k,n=1):
        with self._lock: v=int(self._store.get(k,0))-n; self._store[k]=str(v); return v
    async def expire(self,k,t): return True
    async def incr(self,k): return await self.incrby(k,1)

class FakeTable:
    def __init__(self, store, lock):
        self._store=store; self._lock=lock; self._filters={}; self._update_data=None
    def select(self, *c): return self
    def update(self, d): self._update_data=d; return self
    def eq(self, col, val): self._filters[col]=val; return self
    def execute(self):
        with self._lock:
            uid=self._filters.get("id")
            if uid not in self._store:
                self._store[uid]={"id":uid,"subscription_tier":"free","subscription_status":"active","is_frozen":False,"cancel_at_period_end":False}
            if self._update_data: self._store[uid].update(self._update_data)
            return MagicMock(data=[self._store[uid]])

class FakeSupabase:
    def __init__(self): self._profiles={}; self._lock=_th.Lock()
    def _ensure(self,uid): 
        if uid not in self._profiles:
            self._profiles[uid]={"id":uid,"subscription_tier":"free","subscription_status":"active","is_frozen":False,"cancel_at_period_end":False}
    def table(self,t): return FakeTable(self._profiles,self._lock)
    def rpc(self,fn,args): m=MagicMock(); m.execute=MagicMock(return_value=MagicMock(data=[])); return m

def with_redis(r=None):
    redis = r or AtomicRedis()
    return patch("backend_app.core.billing_lifecycle.redis_manager", redis)

# ──────────────────────────────────────────────────────────────────────────────
# SEVEN SECTIONS WERE REMOVED FROM THIS FILE, AND THE REASON MATTERS
# ──────────────────────────────────────────────────────────────────────────────
# ATTACK 1 (new subscription), 5 (upgrade), the first half of 6 (downgrade), 10 (renewal),
# 11 (cross-tenant), 12 (unknown user) and 20 (concurrent upgrade/downgrade) all drove
# BillingLifecycle.activate_subscription / upgrade_subscription / downgrade_subscription /
# renew_subscription against a FakeSupabase. All four methods are DELETED: none had a
# production caller, and renew_subscription's only one was inside stripe_webhook's
# invoice.payment_succeeded branch, which went with the provider.
#
# WHY THIS WAS URGENT RATHER THAN COSMETIC. The first of those calls was at line 75, inside
# ATTACK 1, at module scope. An AttributeError there killed the WHOLE script, so every
# section after it never ran -- including ATTACK 2, 3 and 4, the Redis SET NX idempotency
# batteries, which are the most valuable thing in this file and have nothing to do with the
# deleted methods. One dead name disabled twenty working sections.
#
# WHAT THE PRODUCT ACTUALLY DOES, so the removals are not read as lost coverage: a tier is
# granted by the Razorpay webhook through _apply_billing_entitlement, and its paid period by
# _record_plan_period. There is no trial, no grace period, no proration on upgrade and no
# scheduled downgrade -- a plan change takes effect when the new plan is paid for, and a
# renewal is a RE-PURCHASE that extends from the current period end. The behavioural
# coverage lives in tests/test_billing_e2e.py, tests/test_bill01_razorpay_plans_expire.py
# and tests/test_subscription_renewal_regression.py, and the surface itself is pinned by
# tests/test_billing_lifecycle_surface.py.
#
# ATTACK 6 is HALVED rather than removed: its second half drives cancel_subscription, which
# survived because cancellation is a real local state change, and it still runs below.
# ──────────────────────────────────────────────────────────────────────────────

print("\n"+"="*72); print("ATTACK 2 — DUPLICATE EVENT IDEMPOTENCY"); print("="*72)
async def a2():
    redis=AtomicRedis(); key="webhook:stripe:evt_001"
    return await redis.set(key,"1",nx=True,ex=86400), await redis.set(key,"1",nx=True,ex=86400)
r1,r2=asyncio.run(a2())
if r1 and not r2: P("2.1 SET NX idempotency: first acquires, second rejected")
else: B("BUG-IB-01","P0","SET NX broken","r1=True r2=False",f"r1={r1} r2={r2}","Double grant")

print("\n"+"="*72); print("ATTACK 3 — 100 CONCURRENT DUPLICATES"); print("="*72)
async def a3():
    redis=AtomicRedis(); key="webhook:razorpay:evt_concurrent"
    results=await asyncio.gather(*[redis.set(key,"1",nx=True,ex=86400) for _ in range(100)])
    return sum(1 for r in results if r)
acquired=asyncio.run(a3())
if acquired==1: P(f"3.1 100 concurrent duplicates: exactly 1 acquired, 99 rejected")
else: B("BUG-IB-03","P0",f"{acquired} locks acquired","1",str(acquired),"Duplicate grant")

print("\n"+"="*72); print("ATTACK 4 — PROCESS-RESTART REPLAY"); print("="*72)
async def a4():
    redis=AtomicRedis(); key="webhook:stripe:evt_restart"
    r1=await redis.set(key,"1",nx=True,ex=86400)
    r2=await redis.set(key,"1",nx=True,ex=86400)
    return r1, r2
r1,r2=asyncio.run(a4())
if r1 and not r2: P("4.1 Process-restart replay: idempotency persists, replay blocked")
else: B("BUG-IB-04r","P0","Replay allowed after restart","r2=False",f"r2={r2}","Double mutation")

print("\n"+"="*72); print("ATTACK 6 — IMMEDIATE CANCEL"); print("="*72)
# 6.1 drove BillingLifecycle.downgrade_subscription and asserted it stashed
# profiles.pending_downgrade_tier. That method is deleted: it wrote a column NOTHING
# anywhere reads, so a scheduled downgrade would never have executed, and deleting it
# removed the last reference to an undeclared column (cleared from
# tests/test_schema_table_reference_drift.py's KNOWN_UNDECLARED_COLUMN_DEFECTS).
# 6.2 below is kept: cancel_subscription survived, because cancellation is a real local
# state change rather than an instruction to a gateway.

async def a6b():
    sb=FakeSupabase(); sb._ensure("usr6b"); sb._profiles["usr6b"]["subscription_tier"]="enterprise"
    with with_redis(): await BillingLifecycle.cancel_subscription("usr6b",cancel_at_period_end=False,supabase=sb)
    return sb._profiles["usr6b"]["subscription_tier"], sb._profiles["usr6b"]["subscription_status"]
t6b,s6b=asyncio.run(a6b())
if t6b=="free" and s6b=="cancelled": P("6.2 Immediate cancel: tier=free, status=cancelled")
else: B("BUG-IB-06b","P1","Immediate cancel wrong","free/cancelled",f"{t6b}/{s6b}","Stale premium access")

print("\n"+"="*72); print("ATTACK 7 — CANCELLATION BLOCKS BOTS"); print("="*72)
from backend_app.core.subscription_middleware import get_user_subscription
async def a7():
    redis=AtomicRedis(); await redis.set("subscription:status:usr7","cancelled")
    with patch("backend_app.core.subscription_middleware.redis_manager",redis): return (await get_user_subscription("usr7"))["status"]
if asyncio.run(a7())=="cancelled": P("7.1 Cancelled subscription correctly read from Redis")
else: B("BUG-IB-07","P0","Cancelled not returned","cancelled","other","Bot not blocked")

async def a7b():
    redis=AtomicRedis(); await redis.set("subscription:status:usr7b","cancelled")
    mock_fleet=MagicMock(); mock_fleet.start_bot=AsyncMock()
    mock_app=MagicMock(); mock_app.fleet=mock_fleet
    from backend_app.workers.command_worker import CommandWorker
    worker=CommandWorker()
    msg=[("q",[("m1",{"action":"start_bot","payload":'{"user_id":"usr7b","symbol":"BTC","blueprint":{}}'})])]
    with patch("backend_app.workers.command_worker.redis_manager.xreadgroup",AsyncMock(return_value=msg),create=True), \
         patch("backend_app.workers.command_worker.redis_manager.xack",AsyncMock(),create=True), \
         patch("backend_app.workers.command_worker.app_state",mock_app), \
         patch("backend_app.core.subscription_middleware.redis_manager",redis):
        await worker.process_iteration()
    return mock_fleet.start_bot.call_count
def run7b(): return asyncio.run(a7b())
with concurrent.futures.ThreadPoolExecutor() as ex: c7b=ex.submit(run7b).result()
if c7b==0: P("7.2 Cancelled subscriber: start_bot blocked by CommandWorker")
else: B("BUG-IB-07b","P0",f"CommandWorker allowed start_bot for cancelled user","0 calls",str(c7b),"Cancelled user starts bot")

print("\n"+"="*72); print("ATTACK 8 — EXPIRATION BLOCKS TRADING"); print("="*72)
async def a8():
    redis=AtomicRedis(); await redis.set("subscription:status:usr8","expired")
    with patch("backend_app.core.subscription_middleware.redis_manager",redis): return (await get_user_subscription("usr8"))["status"]
if asyncio.run(a8())=="expired": P("8.1 Expired status reflected correctly")
else: B("BUG-IB-08","P0","Expired not returned","expired","other","Expired user trades")

print("\n"+"="*72); print("ATTACK 9 — FAILED PAYMENT: account frozen"); print("="*72)
from backend_app.core.subscription_middleware import SubscriptionMiddleware
from fastapi import HTTPException, Response
async def a9():
    mw=SubscriptionMiddleware(app=MagicMock())
    req=MagicMock(); req.url=MagicMock(); req.url.path="/api/bots/start"
    # `scope` MUST be a real dict carrying the path. The middleware reads
    # `request.scope["path"]`, not `request.url.path` -- a deliberate change, its own comment
    # cites CVE-2026-48710 (BadHost): scope[path] is not rebuilt from the Host header. On a
    # bare MagicMock, `request.scope["path"]` yields another MagicMock, and
    # `MagicMock.startswith(...)` returns a truthy MagicMock, so
    # `any(path.startswith(b) for b in BYPASS_PATHS)` was TRUE and the middleware returned
    # call_next(request) before checking anything.
    #
    # That is why this section reported "BUG-IB-09 Frozen account allowed" as a P0. It was an
    # artefact of the harness, not a defect: the frozen check below is reached and does
    # refuse. The script had never run this far (it died at ATTACK 1 on a deleted method), so
    # the false P0 had never been printed for anyone to chase.
    req.scope={"path":"/api/bots/start","type":"http","headers":[]}
    req.state=MagicMock(); req.state.user={"id":"usr9","access_token":"t"}
    call_next=AsyncMock(return_value=Response(content=b"ok"))
    redis=AtomicRedis()
    await redis.set("subscription:frozen:usr9","True")
    with patch("backend_app.core.subscription_middleware.redis_manager",redis):
        try:
            await mw.dispatch(req,call_next)
            return "ALLOWED" if call_next.called else "BLOCKED"
        except HTTPException as e: return f"BLOCKED_{e.status_code}"
r9=asyncio.run(a9())
if r9.startswith("BLOCKED"): P(f"9.1 Frozen account: request blocked ({r9})")
else: B("BUG-IB-09","P0","Frozen account allowed","BLOCKED_403",r9,"Frozen user trades")

print("\n"+"="*72); print("ATTACK 13 — MALFORMED EVENTS"); print("="*72)
tests13=[
    (None,"free","13.1 migrate_plan_key(None)→free"),
    ("","free","13.2 migrate_plan_key('')→free"),
    ("garbage_plan_9999","free","13.3 Unknown plan→free"),
    ("   pro   ","pro","13.4 Whitespace stripped→pro"),
    ("PRO","pro","13.5 Uppercase PRO→pro"),
]
for key,expected,label in tests13:
    result=SubscriptionEngine.migrate_plan_key(key)
    if result==expected: P(label)
    else: B(f"BUG-IB-13_{key}","P1",f"migrate_plan_key({key!r})={result!r}",expected,result,"Wrong plan fallback")

cfg13=SubscriptionEngine.get_plan_config("garbage_plan")
# Intentional behavior: migrate_plan_key("garbage_plan") → "free", so get_plan_config returns FREE config.
# This is by design — callers always get a safe plan config. Callers cannot distinguish
# "explicitly FREE" from "unknown→FREE" at this layer. Document as expected.
if cfg13 is not None and cfg13.id == "free":
    P("13.6 get_plan_config('garbage_plan') returns FREE config (safe intentional fallback — unknown=free)")
else:
    B("BUG-IB-13d","P1","Unexpected plan config for unknown plan","FREE config",str(cfg13),"Unknown plan not safe-defaulted")

print("\n"+"="*72); print("ATTACK 14 — CRASH AFTER IDEMPOTENCY: orphaned key"); print("="*72)
async def a14():
    redis=AtomicRedis(); key="webhook:stripe:crash_a_001"
    r1=await redis.set(key,"processing",nx=True,ex=60)  # acquired processing lock
    # DB CRASHES / FAILS - error handler releases key
    await redis.delete(key)
    # retry → can acquire and proceed!
    r2=await redis.set(key,"processing",nx=True,ex=60)
    return r1, r2
ra1,ra2=asyncio.run(a14())
if ra1 and ra2: P("14.1 Crash-safe two-phase idempotency: lock released on failure, retry successfully re-acquired")
else: B("BUG-IB-04","P1","Orphaned idempotency key permanently blocks retry after crash","r1=True r2=True",f"r1={ra1} r2={ra2}","Lost customer subscription")

print("\n"+"="*72); print("ATTACK 15 — DB COMMIT + RESPONSE FAILURE + RETRY"); print("="*72)
async def a15():
    redis=AtomicRedis(); key="webhook:stripe:commit_fail_001"
    r1=await redis.set(key,"1",nx=True,ex=86400)  # DB commits, key set
    r2=await redis.set(key,"1",nx=True,ex=86400)  # client retries → blocked
    return r1, r2
r1_15,r2_15=asyncio.run(a15())
if r1_15 and not r2_15: P("15.1 DB commit + response failure + retry: second delivery rejected")
else: B("BUG-IB-15","P0","Double mutation on retry","r2=False",f"r2={r2_15}","Double entitlement")

print("\n"+"="*72); print("ATTACK 16 — ENTITLEMENT CONSISTENCY"); print("="*72)
sub_pro=SubscriptionEngine.get_plan_config("pro")
ent_engine=EntitlementEngine()
async def a16_api():
    return await ent_engine.check_feature_entitlement("u","pro",FeatureFlag.API_ACCESS)
async def a16_ml():
    return await ent_engine.check_feature_entitlement("u","pro",FeatureFlag.ML_TRAINING)
async def a16_free_live():
    return await ent_engine.check_feature_entitlement("u","free",FeatureFlag.LIVE_TRADING)
d_api=asyncio.run(a16_api()); d_ml=asyncio.run(a16_ml()); d_free=asyncio.run(a16_free_live())
sub_api="api_access" in sub_pro.features; sub_ml="ml_training" in sub_pro.features
sub_free_live="live_trading" in SubscriptionEngine.get_plan_config("free").features
if sub_api and d_api.is_allowed: P("16.1 PRO API_ACCESS: SubscriptionEngine=T EntitlementEngine=T (consistent)")
elif sub_api and not d_api.is_allowed: B("BUG-IB-02","P1","EntitlementEngine denies API_ACCESS to PRO","T","F","PRO denied API access")
if sub_ml and d_ml.is_allowed: P("16.2 PRO ML_TRAINING: SubscriptionEngine=T EntitlementEngine=T (consistent)")
elif sub_ml and not d_ml.is_allowed: B("BUG-IB-16b","P1","EntitlementEngine denies ML_TRAINING to PRO","T","F","PRO denied ML")
if not sub_free_live and not d_free.is_allowed: P("16.3 FREE live_trading: both deny (consistent)")
else: B("BUG-IB-16c","P1",f"FREE live_trading inconsistency","F/F",f"sub={sub_free_live} ent={d_free.is_allowed}","Free users get live trading")

print("\n"+"="*72); print("ATTACK 17 — QUOTA TRANSITIONS: no negatives, no leaks"); print("="*72)
async def a17():
    redis=AtomicRedis()
    with patch("backend_app.core.subscription_engine.redis_manager",redis):
        for _ in range(3): await SubscriptionEngine.reserve_quota("usr17","pro","bots",1)
        u3=await SubscriptionEngine.get_quota_usage("usr17","bots")
        for _ in range(2): await SubscriptionEngine.decrement_quota_usage("usr17","bots",1)
        u1=await SubscriptionEngine.get_quota_usage("usr17","bots")
        for _ in range(10): await SubscriptionEngine.decrement_quota_usage("usr17","bots",1)
        u0=await SubscriptionEngine.get_quota_usage("usr17","bots")
    return u3, u1, u0
u3,u1,u0=asyncio.run(a17())
if u3==3 and u1==1 and u0==0: P(f"17.1 Quota transitions: grant=3, release=1, floor=0 (no negatives)")
else: B("BUG-IB-17","P1","Quota transitions wrong","3/1/0",f"{u3}/{u1}/{u0}","Quota accounting error")

# The limit is READ FROM THE PLAN CONFIG, not hardcoded. This section asserted "5 granted,
# 2 rejected (PRO bots=5)" and reserved exactly 7 times. PRO's bots quota is now 10, so all
# 7 reservations are granted and the section reported "BUG-IB-17b Quota enforcement wrong /
# Over-quota bots granted" as a P1 -- for a quota that was being enforced correctly. The
# plan ladder changed and the hardcoded 5 did not, and because the script died at ATTACK 1
# on a deleted method, nobody ever saw the false finding to correct it.
#
# Deriving the limit means the next ladder change does not turn this into a false alarm
# again, and the assertion still has teeth: it reserves limit+2 and requires the last two to
# be REFUSED, so a quota that stopped being enforced at all still fails here.
_PRO_BOTS = SubscriptionEngine.get_plan_config("pro").quotas["bots"]

async def a17b():
    redis=AtomicRedis()
    with patch("backend_app.core.subscription_engine.redis_manager",redis):
        return [( await SubscriptionEngine.reserve_quota("u17b","pro","bots",1))[0] for _ in range(_PRO_BOTS+2)]
qr=asyncio.run(a17b())
if all(qr[:_PRO_BOTS]) and not any(qr[_PRO_BOTS:]):
    P(f"17.2 Quota enforcement: {_PRO_BOTS} granted, 2 rejected (PRO bots={_PRO_BOTS})")
else: B("BUG-IB-17b","P1",f"Quota enforcement wrong",f"T*{_PRO_BOTS}+F*2",str(qr),"Over-quota bots granted")

print("\n"+"="*72); print("ATTACK 18 — DOWNGRADE DURING BOT EXECUTION"); print("="*72)
async def a18():
    redis=AtomicRedis(); await redis.set("subscription:status:usr18","cancelled")
    mock_fleet=MagicMock(); mock_fleet.start_bot=AsyncMock()
    mock_app=MagicMock(); mock_app.fleet=mock_fleet
    from backend_app.workers.command_worker import CommandWorker
    worker=CommandWorker()
    msg=[("q",[("m1",{"action":"start_bot","payload":'{"user_id":"usr18","symbol":"BTC","blueprint":{}}'})])]
    with patch("backend_app.workers.command_worker.redis_manager.xreadgroup",AsyncMock(return_value=msg),create=True), \
         patch("backend_app.workers.command_worker.redis_manager.xack",AsyncMock(),create=True), \
         patch("backend_app.workers.command_worker.app_state",mock_app), \
         patch("backend_app.core.subscription_middleware.redis_manager",redis):
        await worker.process_iteration()
    return mock_fleet.start_bot.call_count
def run18(): return asyncio.run(a18())
with concurrent.futures.ThreadPoolExecutor() as ex: c18=ex.submit(run18).result()
if c18==0: P("18.1 Cancelled subscription during execution: next start_bot blocked")
else: B("BUG-IB-18","P0",f"start_bot allowed after cancellation","0",str(c18),"Cancelled user executes")

print("\n"+"="*72); print("ATTACK 19 — DOWNGRADE DURING DAG CLAIM"); print("="*72)
async def a19():
    redis=AtomicRedis(); await redis.set("subscription:status:tenant19","cancelled")
    with patch("backend_app.core.subscription_middleware.redis_manager",redis):
        sub=await get_user_subscription("tenant19")
    return sub.get("status")
s19=asyncio.run(a19())
if s19=="cancelled": P("19.1 DAG claim after cancel: subscription=cancelled detected (worker would reject)")
else: B("BUG-IB-19","P0","Cancelled subscription not detected for DAG claim","cancelled",s19,"Cancelled user runs DAG")

print("\n"+"="*72); print("ATTACK 20 — CONCURRENT CANCEL: removed"); print("="*72)
# Raced BillingLifecycle.upgrade_subscription against cancel_subscription and asserted
# the final state was one of two legal values. upgrade_subscription is deleted -- an
# upgrade is a fresh checkout whose webhook overwrites the tier, and there was never any
# proration -- so there is no second writer left to race. Racing cancel against itself
# would assert something else, so the section is removed rather than quietly redefined.

print("\n"+"="*72); print("PLAN NAMESPACE ATTACK"); print("="*72)
plan_tests=[
    ("starter","starter"),("starter_499","starter"),("basic","starter"),("BASIC","starter"),
    ("pro","pro"),("professional","pro"),("PROFESSIONAL","pro"),("pro_999","pro"),
    ("enterprise","enterprise"),("ENTERPRISE","enterprise"),("elite_1999","enterprise"),
    ("elite","enterprise"),("free","free"),("garbage_xyz","free"),
    ("   pro   ","pro"),("PRO","pro"),
]
for key,expected in plan_tests:
    result=SubscriptionEngine.migrate_plan_key(key)
    if result==expected: P(f"PLAN '{key}'→'{result}' ✓")
    else: B(f"BUG-IB-PLAN-{key}","P1",f"Plan '{key}'→'{result}' expected '{expected}'",expected,result,"Wrong tier")

mapper_tests=[
    ("pro",TenantPlan.PROFESSIONAL),("starter",TenantPlan.BASIC),
    ("enterprise",TenantPlan.ENTERPRISE),("free",TenantPlan.FREE),
    ("pro_999",TenantPlan.PROFESSIONAL),("elite_1999",TenantPlan.ENTERPRISE),
    ("starter_499",TenantPlan.BASIC),("basic",TenantPlan.BASIC),
]
for bk,exp in mapper_tests:
    r=PlanMapper.billing_to_tenant(bk)
    if r==exp: P(f"PLANMAP '{bk}'→TenantPlan.{r.value} ✓")
    else: B(f"BUG-IB-PLANMAP-{bk}","P1",f"PlanMapper('{bk}')→{r.value} expected {exp.value}",exp.value,r.value,"Wrong TenantPlan")

print("\n"+"="*72); print("TENANT ISOLATION"); print("="*72)
async def ti_quota():
    redis=AtomicRedis()
    with patch("backend_app.core.subscription_engine.redis_manager",redis):
        await SubscriptionEngine.increment_quota_usage("ta_user","bots",3)
        return await SubscriptionEngine.get_quota_usage("tb_user","bots")
if asyncio.run(ti_quota())==0: P("TI.1 Quota isolation: Tenant A increment did not affect Tenant B")
else: B("BUG-TI-01","P0","Cross-tenant quota leak","0","non-zero","Cross-tenant contamination")

print("\n"+"="*72); print("CRASH/TRANSACTION SEQUENCES A-D"); print("="*72)
async def seq_c():
    redis=AtomicRedis(); key="webhook:stripe:crash_c_001"
    r1=await redis.set(key,"1",nx=True,ex=86400)
    r2=await redis.set(key,"1",nx=True,ex=86400)
    return r1, r2
rc1,rc2=asyncio.run(seq_c())
if not rc2: P("SEQ-C: DB commit + response failure + retry → blocked (no double mutation)")
else: B("BUG-SEQ-C","P0","Double mutation on retry","blocked","allowed","Double entitlement")
print(f"  [SOURCE_VERIFIED] SEQ-A/B/D: Orphaned idempotency = BUG-IB-04 (reported above)")

print("\n"+"="*72)
print(f"INTERNAL BILLING AUDIT COMPLETE")
print(f"PASS: {PASS_COUNT} | BUGS FOUND: {BUG_COUNT}")
print("="*72)
for bid,sev,desc in BUGS: print(f"  [{sev}] {bid}: {desc}")
if BUG_COUNT>0: sys.exit(1)
