import os
import sys
import asyncio
import time
import json
import uuid
from decimal import Decimal
from datetime import datetime, timezone

sys.path.insert(0, '.')
os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "false"

from backend_app.core.cache.redis_manager import SharedRedisManager
from backend_app.core.subscription_engine import (
    SubscriptionEngine,
    Plan,
    Feature,
    Resource
)
from backend_app.core.entitlement_engine import (
    EntitlementEngine,
    FeatureFlag,
    BillingPlan
)
from backend_app.core.hard_quota_enforcer import (
    HardQuotaEnforcer,
    EnforcementContext
)
from backend_app.core.tenant import TenantContext, TenantPlan, TenantQuota
from backend_app.core.risk_manager import RiskManager
from backend_app.core.execution_engine import ExecutionEngine, Position

print("=" * 80)
print("ULTIMATE HOSTILE BUSINESS LOGIC & ENTITLEMENT ATTACK SUITE")
print("=" * 80)

async def test_business_logic():
    redis = SharedRedisManager()
    sub_engine = SubscriptionEngine()
    ent_engine = EntitlementEngine()
    quota_enforcer = HardQuotaEnforcer()

    # -------------------------------------------------------------------------
    # ATTACK 3: Quota Race Attack (Atomic Reservation vs TOCTOU GET+SET)
    # -------------------------------------------------------------------------
    print("\n[ATTACK 3]: Attacking Quota TOCTOU Race (50 Concurrent Bot Deployments on max_bots=1)...")
    user_id = f"user_quota_race_{int(time.time()*1000)}"
    plan_key = Plan.STARTER.value  # Starter has quota: bots = 2
    
    # Reset quota in redis for this user
    cache_key = f"quota:{user_id}:{Resource.BOTS.value}"
    await redis.delete(cache_key)
    
    # 50 concurrent requests try to check quota and increment
    async def simulate_deploy_bot_request(req_id: int):
        # Current implementation in subscription_dependencies.require_quota:
        current_usage = await SubscriptionEngine.get_quota_usage(user_id, Resource.BOTS.value)
        has_quota = await SubscriptionEngine.check_quota_entitlement(
            user_id, plan_key, Resource.BOTS.value, current_usage
        )
        if not has_quota:
            return {"req_id": req_id, "status": "quota_exceeded"}
        
        # Simulate slight delay between check and increment (network/DB latency)
        await asyncio.sleep(0.01)
        
        new_usage = await SubscriptionEngine.increment_quota_usage(user_id, Resource.BOTS.value)
        return {"req_id": req_id, "status": "deployed", "usage": new_usage}
    
    tasks = [simulate_deploy_bot_request(i) for i in range(50)]
    results = await asyncio.gather(*tasks)
    
    deployed = [r for r in results if r["status"] == "deployed"]
    quota_exceeded = [r for r in results if r["status"] == "quota_exceeded"]
    final_usage = await SubscriptionEngine.get_quota_usage(user_id, Resource.BOTS.value)
    limit = SubscriptionEngine.get_quota_limit(plan_key, Resource.BOTS.value)
    
    print(f"  Plan limit: {limit} bots")
    print(f"  Successful deployments across 50 concurrent requests: {len(deployed)}")
    print(f"  Quota exceeded rejections: {len(quota_exceeded)}")
    print(f"  Final recorded usage in Redis: {final_usage}")
    
    if len(deployed) > limit:
        print(f"  🔴 [BUG-QUOTA-01] VULNERABILITY REPRODUCED: {len(deployed)} bots deployed when limit is {limit}!")
    else:
        print(f"  Quota race: PASSED (deployed: {len(deployed)} <= {limit})")

    # -------------------------------------------------------------------------
    # ATTACK 8: Risk Limit Concurrency Race (Max Position 1.0 BTC)
    # -------------------------------------------------------------------------
    print("\n[ATTACK 8]: Attacking Risk Manager Concurrent Order Sizing Race...")
    rm = RiskManager(initial_equity=100000.0, max_position_size=1.0)
    
    # 2 concurrent orders of 0.7 BTC each
    # Total would be 1.4 BTC, exceeding max_position_size of 1.0 BTC
    order_a_allowed, reason_a = rm.check_risk_guardrails("BTC/USDT", "buy", 0.7, 50000.0)
    # If order A is accepted, position is not updated until execution
    order_b_allowed, reason_b = rm.check_risk_guardrails("BTC/USDT", "buy", 0.7, 50000.0)
    
    print(f"  Order A (0.7 BTC) allowed: {order_a_allowed}")
    print(f"  Order B (0.7 BTC) allowed concurrently before A fills: {order_b_allowed}")
    
    # -------------------------------------------------------------------------
    # ATTACK 1: Subscription Downgrade with Active Resources
    # -------------------------------------------------------------------------
    print("\n[ATTACK 1]: Attacking Subscription Downgrade Grandfathering vs Revocation...")
    # User was Enterprise (12 bots allowed), deployed 10 bots
    user_downgrade = f"user_down_{int(time.time()*1000)}"
    # Set usage to 10 bots
    await redis.set(f"quota:{user_downgrade}:{Resource.BOTS.value}", "10")
    
    # User downgrades to Free (0 bots allowed)
    can_deploy_new = await SubscriptionEngine.check_quota_entitlement(
        user_downgrade, Plan.FREE.value, Resource.BOTS.value, current_usage=10
    )
    print(f"  New bot deployment allowed after downgrade to Free with 10 existing bots: {can_deploy_new}")
    assert can_deploy_new is False, "Free user with 10 existing bots allowed to deploy more!"
    print("  --> Downgrade new deployment blocked: PASS")

    # -------------------------------------------------------------------------
    # ATTACK 12: Cross-Tenant Quota & Key Isolation
    # -------------------------------------------------------------------------
    print("\n[ATTACK 12]: Attacking Cross-Tenant Quota Isolation...")
    tenant_1 = "tenant_alpha"
    tenant_2 = "tenant_beta"
    
    await redis.set(f"quota:{tenant_1}:{Resource.BOTS.value}", "5")
    await redis.set(f"quota:{tenant_2}:{Resource.BOTS.value}", "1")
    
    u1 = await SubscriptionEngine.get_quota_usage(tenant_1, Resource.BOTS.value)
    u2 = await SubscriptionEngine.get_quota_usage(tenant_2, Resource.BOTS.value)
    print(f"  Tenant 1 usage: {u1}, Tenant 2 usage: {u2}")
    assert u1 == 5 and u2 == 1, "Cross-tenant quota leakage detected!"
    print("  --> Tenant Quota Isolation: PASS")

    print("\n" + "=" * 80)
    print("INITIAL BUSINESS LOGIC ATTACK CYCLE COMPLETE")
    print("=" * 80)

asyncio.run(test_business_logic())
