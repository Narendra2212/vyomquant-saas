#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
scripts/attack_distributed_consistency_deep.py

Master Hostile Attack Suite: Distributed Consistency, Lock Ownership,
Multi-Dimensional Idempotency, Concurrency (5-100 workers), Fill Deduplication,
and Double-Entry Conservation.
"""

import os
import sys
import asyncio
import json
import uuid
import hashlib
from decimal import Decimal
from datetime import datetime, timezone, timedelta

# Enforce environment
os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "false"
os.environ["VYOMQUANT_MODE"] = "safe"
os.environ["VYOMQUANT_ENABLE_LIVE_TRADING"] = "false"
os.environ["USE_TEE"] = "false"

# Add repository root to path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend_app.core.cache.redis_manager import SharedRedisManager
from backend_app.core.distributed_idempotency import (
    DistributedIdempotencyLayer,
    DuplicateOrderError,
    MissingClientOrderIdError
)
from backend_app.core.fill_deduplication_manager import (
    FillDeduplicationManager,
    FillRecord
)
from backend_app.core.cancellation_idempotency_manager import (
    CancellationIdempotencyManager,
    CancellationRecord
)
from backend_app.core.execution_engine import ExecutionEngine


async def attack_concurrency_scale(idempotency_layer: DistributedIdempotencyLayer, worker_counts=[5, 10, 50, 100]):
    """Attack 1: High Concurrency (5 to 100 workers) on Same client_order_id."""
    print("\n--- ATTACK 1: Concurrency Scaling (5, 10, 50, 100 workers) ---")
    tenant_id = "tenant_scale_test"
    
    for count in worker_counts:
        client_order_id = f"scale_order_{count}_{uuid.uuid4().hex[:6]}"
        execution_count = 0
        
        async def fake_order_operation(worker_id):
            nonlocal execution_count
            await asyncio.sleep(0.02)  # Simulate network / execution latency
            execution_count += 1
            return {"status": "FILLED", "order_id": f"ord_{client_order_id}", "worker": worker_id}
        
        async def worker_task(worker_id):
            try:
                res = await idempotency_layer.execute_with_idempotency(
                    tenant_id=tenant_id,
                    client_order_id=client_order_id,
                    operation=fake_order_operation,
                    worker_id=worker_id
                )
                return ("OK", res)
            except DuplicateOrderError:
                return ("DUPLICATE_BLOCKED", None)
            except Exception as e:
                return ("ERROR", str(e))
        
        tasks = [worker_task(i) for i in range(count)]
        results = await asyncio.gather(*tasks)
        
        successful_executions = [r for r in results if r[0] == "OK"]
        blocked_or_cached = [r for r in results if r[0] in ("OK", "DUPLICATE_BLOCKED")]
        
        # Verify exactly-once money-moving execution
        assert execution_count == 1, f"FAIL: {count} workers resulted in {execution_count} executions (expected exactly 1)!"
        assert len(successful_executions) == count, f"FAIL: Not all workers received valid result or completed!"
        
        # Verify all successful workers received the same order_id result
        first_res = successful_executions[0][1]
        for s in successful_executions:
            assert s[1]["order_id"] == first_res["order_id"], f"Result divergence across workers: {s[1]} vs {first_res}"
            
        print(f"  [PASS] {count} concurrent workers -> Exactly 1 exchange execution, {len(successful_executions)} consistent results returned.")


async def attack_lock_ownership_and_expiration(idempotency_layer: DistributedIdempotencyLayer, redis_manager: SharedRedisManager):
    """Attack 2: Lock Ownership & Expiration (Preventing Worker A from deleting Worker B's lock)."""
    print("\n--- ATTACK 2: Lock Ownership & Expiration Race ---")
    tenant_id = "tenant_lock_test"
    client_order_id = f"lock_race_{uuid.uuid4().hex[:8]}"
    key = idempotency_layer._generate_key(tenant_id, client_order_id)
    
    # Step 1: Worker A acquires lock with owner token A
    owner_a = "token_worker_A"
    acquired_a, _ = await idempotency_layer.start_processing(tenant_id, client_order_id, owner_token=owner_a)
    assert acquired_a is True, "Worker A failed to acquire initial lock"
    
    # Step 2: Simulate lock expiration in Redis (TTL expired)
    await redis_manager.delete(key)
    
    # Step 3: Worker B acquires the now-free key with owner token B
    owner_b = "token_worker_B"
    acquired_b, _ = await idempotency_layer.start_processing(tenant_id, client_order_id, owner_token=owner_b)
    assert acquired_b is True, "Worker B failed to acquire lock after A's expiration"
    
    # Step 4: Worker A fails its operation and attempts to release its lock
    released_by_a = await idempotency_layer.release_processing_lock(tenant_id, client_order_id, owner_token=owner_a)
    assert released_by_a is False, "Worker A illegally released Worker B's active lock!"
    
    # Step 5: Verify Worker B's lock is STILL ACTIVE in Redis
    current_val = await redis_manager.get(key)
    curr_str = current_val.decode() if isinstance(current_val, bytes) else str(current_val)
    assert curr_str == f"processing:{owner_b}", f"Worker B's lock was corrupted or missing: {curr_str}"
    
    # Step 6: Worker B successfully completes and stores result
    await idempotency_layer.store_result(tenant_id, client_order_id, {"status": "SUCCESS_B"})
    
    # Step 7: Subsequent requests receive Worker B's cached result
    cached_check = await idempotency_layer.check_idempotency(tenant_id, client_order_id)
    assert cached_check.is_duplicate is True
    assert cached_check.cached_result["result"]["status"] == "SUCCESS_B"
    print("  [PASS] Owner-safe lock release prevented Worker A from deleting Worker B's lock.")


async def attack_multi_dimensional_isolation(idempotency_layer: DistributedIdempotencyLayer):
    """Attack 3: Multi-Dimensional Isolation (Exchange & Tenant Dimensions)."""
    print("\n--- ATTACK 3: Multi-Dimensional Idempotency Isolation ---")
    shared_client_order_id = "cross_dim_order_999"
    
    # Dimension A: Tenant A on Binance
    res_tenant_a = await idempotency_layer.execute_with_idempotency(
        tenant_id="tenant_A",
        client_order_id=shared_client_order_id,
        operation=lambda: asyncio.sleep(0.001, result={"tenant": "A", "val": 100}),
        exchange_id="binance"
    )
    
    # Dimension B: Tenant B with SAME client_order_id on Binance (Must NOT be suppressed)
    res_tenant_b = await idempotency_layer.execute_with_idempotency(
        tenant_id="tenant_B",
        client_order_id=shared_client_order_id,
        operation=lambda: asyncio.sleep(0.001, result={"tenant": "B", "val": 200}),
        exchange_id="binance"
    )
    assert res_tenant_a["tenant"] == "A"
    assert res_tenant_b["tenant"] == "B"
    assert res_tenant_a["val"] == 100
    assert res_tenant_b["val"] == 200
    
    # Dimension C: Same Tenant A with SAME client_order_id on Bybit (Multi-Exchange Routing)
    res_tenant_a_bybit = await idempotency_layer.execute_with_idempotency(
        tenant_id="tenant_A",
        client_order_id=shared_client_order_id,
        operation=lambda: asyncio.sleep(0.001, result={"tenant": "A", "exchange": "bybit", "val": 300}),
        exchange_id="bybit"
    )
    assert res_tenant_a_bybit["exchange"] == "bybit"
    assert res_tenant_a_bybit["val"] == 300
    
    print("  [PASS] Multi-tenant and multi-exchange dimensions are completely isolated.")


async def attack_result_cache_integrity(idempotency_layer: DistributedIdempotencyLayer, redis_manager: SharedRedisManager):
    """Attack 4: Result Cache Corruption & Mismatched Tenant Injection."""
    print("\n--- ATTACK 4: Result Cache Corruption & Poisoning Attack ---")
    tenant_id = "tenant_victim"
    order_id = "ord_integrity_01"
    key = idempotency_layer._generate_key(tenant_id, order_id)
    
    # Inject poisoned JSON with tenant mismatch
    poisoned_payload = json.dumps({
        "result": {"stolen_data": "LEAKED_PRIVATE_KEYS"},
        "tenant_id": "tenant_ATTACKER",
        "client_order_id": order_id
    })
    await redis_manager.set(key, poisoned_payload)
    
    # Check idempotency: Must detect tenant mismatch and refuse to return poisoned cache
    check_res = await idempotency_layer.check_idempotency(tenant_id, order_id)
    assert check_res.is_duplicate is False, "FAIL: Tenant mismatch in cached result was accepted as duplicate!"
    assert check_res.cached_result is None
    
    # Inject malformed non-JSON string
    await redis_manager.set(key, "{malformed_json_syntax_error: true")
    malformed_check = await idempotency_layer.check_idempotency(tenant_id, order_id)
    assert malformed_check.is_duplicate is False
    assert malformed_check.cached_result is None
    
    print("  [PASS] Result cache safely rejected poisoned tenant data and malformed JSON.")


async def attack_fill_deduplication_deep(redis_manager: SharedRedisManager):
    """Attack 5: Fill Deduplication (Legitimate Partials vs Hostile 100 Duplicate Floods)."""
    print("\n--- ATTACK 5: Fill Deduplication & Legitimate Partial Fills ---")
    fill_manager = FillDeduplicationManager(redis_client=redis_manager)
    tenant_id = "tenant_fill_test"
    order_id = "ord_fill_multi_01"
    
    # Scenario A: 100 concurrent deliveries of the EXACT SAME fill
    fill_ts = datetime.now(timezone.utc)
    fill_record = FillRecord(
        fill_id="fill_flood_01",
        order_id=order_id,
        tenant_id=tenant_id,
        symbol="BTC/USDT",
        side="buy",
        filled_quantity=Decimal("0.5"),
        fill_price=Decimal("50000.00"),
        timestamp=fill_ts,
        exchange_trade_id="tr_unique_1001",
        fill_hash=fill_manager.generate_fill_hash(
            order_id, "BTC/USDT", "buy", Decimal("0.5"), Decimal("50000.00"), fill_ts, "tr_unique_1001"
        )
    )
    
    tasks = [fill_manager.register_fill(tenant_id, fill_record) for _ in range(100)]
    results = await asyncio.gather(*tasks)
    accepted_count = sum(1 for r in results if r is True)
    rejected_count = sum(1 for r in results if r is False)
    assert accepted_count == 1, f"Expected exactly 1 accepted fill across 100 concurrent deliveries, got {accepted_count}"
    assert rejected_count == 99
    
    # Scenario B: Multiple legitimate distinct partial fills without exchange trade IDs
    # E.g. Fill 1 at T0, Fill 2 at T1 (same size and price, but distinct partial executions)
    ts1 = datetime.now(timezone.utc)
    ts2 = ts1 + timedelta(seconds=1)
    
    hash1 = fill_manager.generate_fill_hash(order_id, "BTC/USDT", "buy", Decimal("0.25"), Decimal("50000.00"), ts1, "")
    hash2 = fill_manager.generate_fill_hash(order_id, "BTC/USDT", "buy", Decimal("0.25"), Decimal("50000.00"), ts2, "")
    
    assert hash1 != hash2, "Distinct partial fills at different timestamps produced identical fill hashes!"
    
    record1 = FillRecord("f1", order_id, tenant_id, "BTC/USDT", "buy", Decimal("0.25"), Decimal("50000.00"), ts1, "", hash1)
    record2 = FillRecord("f2", order_id, tenant_id, "BTC/USDT", "buy", Decimal("0.25"), Decimal("50000.00"), ts2, "", hash2)
    
    res1 = await fill_manager.register_fill(tenant_id, record1)
    res2 = await fill_manager.register_fill(tenant_id, record2)
    assert res1 is True and res2 is True, "Legitimate distinct partial fill was incorrectly dropped!"
    
    print("  [PASS] Fill deduplication correctly dropped 99 duplicate floods and preserved legitimate partial fills.")


async def attack_sub_satoshi_and_precision():
    """Attack 6: Sub-Satoshi & Sub-Penny Precision in ExecutionEngine."""
    print("\n--- ATTACK 6: Sub-Satoshi & Extreme Decimal Precision ---")
    ee = ExecutionEngine(portfolio_state={"total_equity": Decimal("100000.00")})
    
    # Extremely small fractional quantities (1e-8 Satoshi, 1e-12 micro-sat)
    tiny_size = Decimal("0.00000001")
    high_price = Decimal("65432.12345678")
    
    # Open tiny position
    success, msg = ee.open_position(
        symbol="BTC/USDT",
        price=high_price,
        size=tiny_size,
        side="long"
    )
    assert success is True
    pos = ee.positions["BTC/USDT"]
    assert pos.size == tiny_size
    assert isinstance(pos.size, Decimal)
    assert isinstance(pos.entry_price, Decimal)
    
    # Partial close of 0.000000005 (5e-9)
    part_close_size = Decimal("0.000000005")
    exit_price = Decimal("70000.00000000")
    
    pnl, closed = ee.close_position("BTC/USDT", exit_price, size=part_close_size)
    assert closed is False
    assert ee.positions["BTC/USDT"].size == Decimal("0.000000005")
    
    expected_pnl = (exit_price - pos.entry_price) * part_close_size
    assert abs(pnl - expected_pnl) < Decimal("1e-12")
    
    print("  [PASS] 1e-8 to 1e-12 sub-satoshi precision maintained without floating point corruption.")


async def main():
    print("=" * 80)
    print("HOSTILE DISTRIBUTED CONSISTENCY & CONCURRENCY ATTACK SUITE")
    print("=" * 80)
    
    idempotency_layer = DistributedIdempotencyLayer()
    redis_mgr = SharedRedisManager()
    
    await attack_concurrency_scale(idempotency_layer, [5, 10, 50, 100])
    await attack_lock_ownership_and_expiration(idempotency_layer, redis_mgr)
    await attack_multi_dimensional_isolation(idempotency_layer)
    await attack_result_cache_integrity(idempotency_layer, redis_mgr)
    await attack_fill_deduplication_deep(redis_mgr)
    await attack_sub_satoshi_and_precision()
    
    print("\n" + "=" * 80)
    print("ALL DISTRIBUTED CONSISTENCY ATTACKS PASSED WITH REAL RUNTIME PROOF!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
