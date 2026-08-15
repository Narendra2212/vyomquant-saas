import os
import sys
import asyncio
import time
import json
import uuid
import copy
from decimal import Decimal
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '.')
os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "false"

from backend_app.core.cache.redis_manager import SharedRedisManager, MockRedisClient
from backend_app.core.distributed_idempotency import (
    DistributedIdempotencyLayer,
    DuplicateOrderError,
    MissingClientOrderIdError,
    IdempotencyResult
)
from backend_app.core.fill_deduplication_manager import (
    FillDeduplicationManager,
    FillRecord
)
from backend_app.core.cancellation_idempotency_manager import (
    CancellationIdempotencyManager,
    CancellationRecord
)
from backend_app.core.execution_engine import ExecutionEngine, Position
from backend_app.core.reconciliation_engine import (
    ReconciliationEngine,
    OrderMismatch,
    PositionMismatch,
    FillMismatch
)

print("=" * 80)
print("COMPREHENSIVE ADVERSARIAL STRESS TEST & CONSISTENCY ATTACK")
print("=" * 80)

async def test_suite():
    redis = SharedRedisManager()
    
    # -------------------------------------------------------------------------
    # SUITE 1: 5, 10, 50, 100 Concurrent Workers on Same client_order_id
    # -------------------------------------------------------------------------
    print("\n--- [SUITE 1]: High-Concurrency Workers (5, 10, 50, 100) on Same ID ---")
    idempotency = DistributedIdempotencyLayer()
    
    for worker_count in [5, 10, 50, 100]:
        tenant_id = f"tenant_scale_{worker_count}"
        coid = f"coid_scale_{worker_count}_{int(time.time()*1000)}"
        exec_count = 0
        
        async def place_order():
            nonlocal exec_count
            exec_count += 1
            await asyncio.sleep(0.02)
            return {"order_id": f"exch_{coid}", "status": "filled", "worker_count": worker_count}
        
        tasks = [
            idempotency.execute_with_idempotency(tenant_id, coid, place_order)
            for _ in range(worker_count)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successes = [r for r in results if isinstance(r, dict) and r.get("status") == "filled"]
        duplicates_blocked = [r for r in results if isinstance(r, DuplicateOrderError)]
        other_exceptions = [r for r in results if isinstance(r, Exception) and not isinstance(r, DuplicateOrderError)]
        
        print(f"  Workers: {worker_count:3d} | Executions: {exec_count} | Successes (same result): {len(successes)} | Duplicates Blocked: {len(duplicates_blocked)} | Other Errors: {len(other_exceptions)}")
        assert exec_count == 1, f"CRITICAL: {exec_count} executions for {worker_count} workers!"
        assert len(other_exceptions) == 0, f"Unexpected exceptions: {other_exceptions}"

    # -------------------------------------------------------------------------
    # SUITE 1b: Key Dimensions & Payload Integrity
    # -------------------------------------------------------------------------
    print("\n--- [SUITE 1b]: Payload Tampering & Key Dimensions ---")
    tenant_a = "tenant_dim_a"
    tenant_b = "tenant_dim_b"
    shared_coid = "coid_dim_shared_001"
    
    # 1. Tenant Isolation: same client_order_id for two different tenants must both execute!
    exec_a = 0
    exec_b = 0
    async def op_a():
        nonlocal exec_a; exec_a += 1; return {"tenant": tenant_a, "order_id": "ord_a"}
    async def op_b():
        nonlocal exec_b; exec_b += 1; return {"tenant": tenant_b, "order_id": "ord_b"}
    
    res_a = await idempotency.execute_with_idempotency(tenant_a, shared_coid, op_a)
    res_b = await idempotency.execute_with_idempotency(tenant_b, shared_coid, op_b)
    
    print(f"  Tenant A result: {res_a['tenant']} (Exec: {exec_a})")
    print(f"  Tenant B result: {res_b['tenant']} (Exec: {exec_b})")
    assert exec_a == 1 and exec_b == 1, "Cross-tenant collision detected! Different tenants shared idempotency key!"
    assert res_a["tenant"] == tenant_a and res_b["tenant"] == tenant_b, "Tenant isolation failure in idempotency results!"

    # 2. Multi-exchange partitioning: same client_order_id on Binance vs Bybit
    exec_binance = 0
    exec_bybit = 0
    async def op_binance():
        nonlocal exec_binance; exec_binance += 1; return {"exch": "binance", "id": "b1"}
    async def op_bybit():
        nonlocal exec_bybit; exec_bybit += 1; return {"exch": "bybit", "id": "by1"}
    
    res_binance = await idempotency.execute_with_idempotency(tenant_a, shared_coid, op_binance, exchange_id="binance")
    res_bybit = await idempotency.execute_with_idempotency(tenant_a, shared_coid, op_bybit, exchange_id="bybit")
    print(f"  Binance result: {res_binance['exch']} (Exec: {exec_binance})")
    print(f"  Bybit result: {res_bybit['exch']} (Exec: {exec_bybit})")
    assert exec_binance == 1 and exec_bybit == 1, "Multi-exchange collision: Binance suppressed Bybit!"

    # -------------------------------------------------------------------------
    # SUITE 3: Lock Ownership & Release Safety
    # -------------------------------------------------------------------------
    print("\n--- [SUITE 3]: Lock Ownership & Safe Release ---")
    tenant_lock = "tenant_lock_test"
    coid_lock = "coid_lock_test_001"
    
    # Worker A acquires lock with token_A
    acquired_a, token_a = await idempotency.start_processing(tenant_lock, coid_lock, owner_token="token_A")
    assert acquired_a is True, "Worker A failed to acquire lock"
    
    # Worker A's lock expires / simulated TTL expiry where Worker B acquires with token_B
    # Overwrite in Redis to simulate TTL expiry + Worker B acquisition
    key = idempotency._generate_key(tenant_lock, coid_lock)
    await redis.set(key, f"processing:token_B")
    
    # Worker A resumes and tries to release lock with token_A
    released = await idempotency.release_processing_lock(tenant_lock, coid_lock, owner_token=token_a)
    print(f"  Worker A attempted release with expired token: released={released}")
    
    # Key should STILL exist and belong to token_B!
    val_after = await redis.get(key)
    val_str = val_after.decode() if isinstance(val_after, bytes) else str(val_after)
    print(f"  Current lock value in Redis: {val_str}")
    assert "token_B" in val_str, f"CRITICAL RACE: Worker A deleted Worker B's lock! Value was: {val_str}"
    print("  --> Lock Ownership Release: SAFE (Worker B's lock preserved)")

    # -------------------------------------------------------------------------
    # SUITE 5: Fill Deduplication Complex Attack
    # -------------------------------------------------------------------------
    print("\n--- [SUITE 5]: Fill Deduplication Attack ---")
    fill_mgr = FillDeduplicationManager(redis)
    tenant_f = "tenant_fill_test"
    
    # Test 5.1: 100 concurrent deliveries of the EXACT same fill
    fill_hash_1 = fill_mgr.generate_fill_hash(
        "ord_100", "BTC/USDT", "buy", Decimal("1.0"), Decimal("50000"),
        datetime.now(timezone.utc), "tx_trade_100"
    )
    fill_rec_1 = FillRecord(
        fill_id="fill_100",
        order_id="ord_100",
        tenant_id=tenant_f,
        symbol="BTC/USDT",
        side="buy",
        filled_quantity=Decimal("1.0"),
        fill_price=Decimal("50000"),
        timestamp=datetime.now(timezone.utc),
        exchange_trade_id="tx_trade_100",
        fill_hash=fill_hash_1
    )
    
    tasks = [fill_mgr.register_fill(tenant_f, fill_rec_1) for _ in range(100)]
    reg_results = await asyncio.gather(*tasks)
    accepted = sum(1 for r in reg_results if r is True)
    print(f"  100 concurrent identical fills: Accepted={accepted} (Expected: 1)")
    assert accepted == 1, f"DUPLICATE FILL BUG: {accepted} accepted!"

    # Test 5.2: Multiple legitimate partial fills on same order
    p1_hash = fill_mgr.generate_fill_hash("ord_multi", "BTC/USDT", "buy", Decimal("0.3"), Decimal("50000"), datetime.now(timezone.utc), "tx_part_1")
    p2_hash = fill_mgr.generate_fill_hash("ord_multi", "BTC/USDT", "buy", Decimal("0.3"), Decimal("50000"), datetime.now(timezone.utc), "tx_part_2")
    p3_hash = fill_mgr.generate_fill_hash("ord_multi", "BTC/USDT", "buy", Decimal("0.4"), Decimal("50000"), datetime.now(timezone.utc), "tx_part_3")
    
    r1 = await fill_mgr.register_fill(tenant_f, FillRecord("f1", "ord_multi", tenant_f, "BTC/USDT", "buy", Decimal("0.3"), Decimal("50000"), datetime.now(timezone.utc), "tx_part_1", p1_hash))
    r2 = await fill_mgr.register_fill(tenant_f, FillRecord("f2", "ord_multi", tenant_f, "BTC/USDT", "buy", Decimal("0.3"), Decimal("50000"), datetime.now(timezone.utc), "tx_part_2", p2_hash))
    r3 = await fill_mgr.register_fill(tenant_f, FillRecord("f3", "ord_multi", tenant_f, "BTC/USDT", "buy", Decimal("0.4"), Decimal("50000"), datetime.now(timezone.utc), "tx_part_3", p3_hash))
    
    print(f"  3 distinct partial fills on same order: r1={r1}, r2={r2}, r3={r3}")
    assert r1 and r2 and r3, "CRITICAL: Legitimate partial fills were falsely rejected as duplicates!"

    # Test 5.3: Process fill duplicate classification
    proc_res1 = await fill_mgr.process_fill(tenant_f, "ord_p", "BTC/USDT", "buy", Decimal("1.0"), Decimal("50000"), datetime.now(timezone.utc), "tx_p_1")
    proc_res2 = await fill_mgr.process_fill(tenant_f, "ord_p", "BTC/USDT", "buy", Decimal("1.0"), Decimal("50000"), datetime.now(timezone.utc), "tx_p_1")
    print(f"  process_fill call 1: status={proc_res1['status']}")
    print(f"  process_fill call 2 (duplicate): status={proc_res2['status']}")
    assert proc_res1["status"] == "processed", f"Expected processed, got {proc_res1['status']}"
    assert proc_res2["status"] == "duplicate_rejected", f"Expected duplicate_rejected, got {proc_res2['status']}"

    # -------------------------------------------------------------------------
    # SUITE 6: Cancellation Idempotency & Duplicate Suppression
    # -------------------------------------------------------------------------
    print("\n--- [SUITE 6]: Cancellation Idempotency Attack ---")
    cancel_mgr = CancellationIdempotencyManager(redis)
    tenant_c = "tenant_cancel_test"
    order_c = "ord_cancel_001"
    
    # Attack: Test process_cancellation with same order_id multiple times
    c_res1 = await cancel_mgr.process_cancellation(tenant_c, order_c, reason="User stop")
    c_res2 = await cancel_mgr.process_cancellation(tenant_c, order_c, reason="User stop")
    
    print(f"  process_cancellation call 1: status={c_res1.get('status')}, idemp_key={c_res1.get('idempotency_key')}")
    print(f"  process_cancellation call 2: status={c_res2.get('status')}, idemp_key={c_res2.get('idempotency_key')}")
    
    # -------------------------------------------------------------------------
    # SUITE 10 & 11: Micro-Decimals, Sub-Satoshi & Double-Entry Math Conservation
    # -------------------------------------------------------------------------
    print("\n--- [SUITE 10 & 11]: Sub-Satoshi (1e-8, 1e-12) & Mathematical Invariance ---")
    ee = ExecutionEngine(portfolio_state={"total_equity": Decimal("50000.00")})
    
    # 1 satoshi buy @ $65,432.10987654
    satoshi_qty = Decimal("0.00000001")
    price_1 = Decimal("65432.10987654")
    fee_1 = Decimal("0.00000065")
    
    ee.handle_partial_fill("BTC", satoshi_qty, price_1, satoshi_qty, "buy", fee_1)
    pos_sat = ee.positions.get("BTC")
    assert pos_sat is not None, "Position must exist"
    assert pos_sat.size == Decimal("0.00000001"), f"Precision loss: {pos_sat.size}"
    print(f"  Satoshi position size: {pos_sat.size} BTC @ {pos_sat.entry_price}")
    
    # Close 1 satoshi @ $70,000.00000000
    price_2 = Decimal("70000.00000000")
    fee_2 = Decimal("0.00000070")
    ee.handle_partial_fill("BTC", satoshi_qty, price_2, satoshi_qty, "sell", fee_2)
    
    # Gross PnL = (70000 - 65432.10987654) * 0.00000001 = 4567.89012346 * 1e-8 = 0.0000456789012346
    # Net PnL = Gross PnL - fee_1 - fee_2
    expected_gross_pnl = (price_2 - price_1) * satoshi_qty
    expected_net_equity = Decimal("50000.00") + expected_gross_pnl - fee_1 - fee_2
    print(f"  Calculated Equity: {ee.current_equity}")
    print(f"  Expected Equity:   {expected_net_equity}")
    equity_diff = abs(ee.current_equity - expected_net_equity)
    print(f"  Precision Delta: {equity_diff}")
    assert equity_diff < Decimal("1e-10"), f"Decimal math divergence: diff={equity_diff}"
    print("  --> Sub-Satoshi Invariance: PASS")

    print("\n" + "=" * 80)
    print("ALL ATTACK SUITES EXECUTED")
    print("=" * 80)

asyncio.run(test_suite())
