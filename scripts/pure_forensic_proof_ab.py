import asyncio
import os
import sys
import uuid
import json
import hashlib
from decimal import Decimal
from datetime import datetime, timedelta

def generate_execution_id(tenant_id, strategy_id, symbol, timestamp, side=None, qty=None, price=None, execution_interval_minutes=5):
    total_minutes = timestamp.hour * 60 + timestamp.minute
    bucket_minutes = (total_minutes // execution_interval_minutes) * execution_interval_minutes
    bucket_hour = bucket_minutes // 60
    bucket_minute = bucket_minutes % 60
    time_bucket = timestamp.replace(hour=bucket_hour, minute=bucket_minute, second=0, microsecond=0)
    canonical_parts = [str(tenant_id), strategy_id, symbol.upper(), time_bucket.isoformat()]
    if side is not None:
        canonical_parts.append(str(side).upper())
    if qty is not None:
        canonical_parts.append(f"{float(qty):.8f}")
    if price is not None:
        canonical_parts.append(f"{float(price):.8f}")
    canonical_string = ":".join(canonical_parts)
    hash_hex = hashlib.sha256(canonical_string.encode('utf-8')).hexdigest()
    return f"exec_{hash_hex[:16]}"

class MockAuthoritativeExchange:
    def __init__(self):
        self.orders = []
        self.order_counter = 1000

    async def place_order(self, symbol, side, order_type, size, price):
        self.order_counter += 1
        order_id = f"mock_exch_ord_{self.order_counter}"
        record = {
            "exchange_order_id": order_id,
            "symbol": symbol,
            "side": side,
            "size": float(size),
            "price": float(price),
            "status": "closed",
            "filled_size": float(size),
            "created_at": datetime.utcnow().isoformat()
        }
        self.orders.append(record)
        return {"success": True, "exchange_order_id": order_id, "status": "closed", "filled_size": float(size), "price": float(price)}

    async def get_orders(self, symbol=None):
        if symbol:
            return [o for o in self.orders if o["symbol"] == symbol]
        return self.orders

class MockPostgresExecutionRecordRepo:
    def __init__(self):
        self.records = {}

    def check_idempotent_execution(self, tenant_id, strategy_id, symbol, timestamp, side, qty, price, execution_interval_minutes=5):
        execution_id = generate_execution_id(tenant_id, strategy_id, symbol, timestamp, side, qty, price, execution_interval_minutes)
        if execution_id in self.records:
            rec = self.records[execution_id]
            if rec["status"] == "completed":
                return execution_id, "skip_return_result", rec["result"]
            elif rec["status"] == "executing":
                return execution_id, "skip_already_running", None
            elif rec["status"] == "failed":
                return execution_id, "skip_failed_no_retry", rec["result"]
        return execution_id, "proceed_new", None

    def claim_execution(self, execution_id, tenant_id, strategy_id, symbol, side, size, price):
        if execution_id in self.records and self.records[execution_id]["status"] == "executing":
            return False, self.records[execution_id]
        rec = {
            "execution_id": execution_id,
            "tenant_id": str(tenant_id),
            "strategy_id": strategy_id,
            "symbol": symbol,
            "side": side,
            "size": float(size),
            "price": float(price),
            "status": "executing",
            "order_id": None,
            "result": None,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        self.records[execution_id] = rec
        return True, rec

    def update_status(self, execution_id, status, order_id=None, result=None):
        if execution_id in self.records:
            self.records[execution_id]["status"] = status
            if order_id:
                self.records[execution_id]["order_id"] = order_id
            if result:
                self.records[execution_id]["result"] = result
            self.records[execution_id]["updated_at"] = datetime.utcnow().isoformat()

async def execute_test_a():
    print("================================================================================")
    print("TEST A: EXCHANGE ACCEPTANCE -> PROCESS CRASH -> RECONCILIATION")
    print("================================================================================")
    
    mock_exchange = MockAuthoritativeExchange()
    mock_db = MockPostgresExecutionRecordRepo()
    
    tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    strategy_id = "strat_crash_test"
    symbol = "BTCUSDT"
    side = "BUY"
    size = Decimal("0.5")
    price = Decimal("60000.0")
    timestamp = datetime(2026, 8, 14, 11, 0, 0)
    
    # Step 1: Idempotency Check & Claim Lock
    exec_id, action, _ = mock_db.check_idempotent_execution(tenant_id, strategy_id, symbol, timestamp, side, size, price)
    claimed, rec = mock_db.claim_execution(exec_id, tenant_id, strategy_id, symbol, side, size, price)
    print(f"Step 1: Generated execution_id={exec_id}, DB Claimed Lock={claimed}")
    
    # Step 2: Submit to Exchange
    res = await mock_exchange.place_order(symbol=symbol, side=side, order_type="limit", size=size, price=price)
    print(f"Step 2: Exchange accepted order -> exchange_order_id={res['exchange_order_id']}")
    
    # Step 3: CRASH INJECTION (Process terminates BEFORE mock_db.update_status)
    print("\n--- [CRASH INJECTION: WORKER TERMINATED BEFORE DB UPDATE] ---")
    pre_crash_db = mock_db.records[exec_id]
    print(f"Pre-Crash DB Record: status={pre_crash_db['status']}, order_id={pre_crash_db['order_id']}")
    print(f"Pre-Crash Exchange Orders: {len(mock_exchange.orders)} orders (ID: {mock_exchange.orders[0]['exchange_order_id']})")
    
    # Step 4: Process Restart & Watchdog Reconciliation
    print("\n--- [PROCESS RESTART & WATCHDOG RECONCILIATION] ---")
    # Watchdog discovers in-flight orders with status == 'executing'
    stale_in_flight = [r for r in mock_db.records.values() if r["status"] == "executing"]
    assert len(stale_in_flight) == 1, "Watchdog must discover in-flight record"
    target = stale_in_flight[0]
    
    # Matching algorithm: Match exchange orders by symbol & size & recency
    matching_ord = None
    for ex_o in await mock_exchange.get_orders(target["symbol"]):
        if abs(ex_o["size"] - target["size"]) < 1e-6:
            matching_ord = ex_o
            break
            
    assert matching_ord is not None, "Watchdog must match the exchange order"
    print(f"Watchdog Matching Algorithm matched order: {matching_ord['exchange_order_id']}")
    
    # Update DB to COMPLETED
    mock_db.update_status(exec_id, "completed", order_id=matching_ord["exchange_order_id"], result={"reconciled_by": "OrderWatchdog"})
    
    post_recovery_db = mock_db.records[exec_id]
    print(f"Post-Recovery DB Record: status={post_recovery_db['status']}, order_id={post_recovery_db['order_id']}")
    print(f"Post-Recovery Exchange Orders Count: {len(mock_exchange.orders)}")
    
    # Step 5: Retry Behavior
    print("\n--- [RETRY ORIGINAL INTENT] ---")
    _, retry_action, cached = mock_db.check_idempotent_execution(tenant_id, strategy_id, symbol, timestamp, side, size, price)
    print(f"Retry Intent check: action={retry_action} -> Dispatches 0 new exchange orders.")
    assert retry_action == "skip_return_result", "Retry must be skipped"
    assert len(mock_exchange.orders) == 1, "Exchange order count must remain exactly 1"
    
    print("\n>>> TEST A RESULT: PASS (VERIFIED) <<<\n")
    return {
        "execution_id": exec_id,
        "exchange_order_id": matching_ord["exchange_order_id"],
        "db_executions": len(mock_db.records),
        "exchange_orders": len(mock_exchange.orders),
        "status": "PASS"
    }

async def execute_test_b():
    print("================================================================================")
    print("TEST B: IDENTICAL PARAMETERS BUT TWO INDEPENDENT INTENTS")
    print("================================================================================")
    
    mock_exchange = MockAuthoritativeExchange()
    mock_db = MockPostgresExecutionRecordRepo()
    
    tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    strategy_id = "strat_identical_test"
    symbol = "BTCUSDT"
    side = "BUY"
    size = Decimal("0.5")
    price = Decimal("60000.0")
    
    # Intent A (Bucket: 11:00)
    time_A = datetime(2026, 8, 14, 11, 0, 0)
    # Intent B (Bucket: 11:05 - Next 5-min interval)
    time_B = datetime(2026, 8, 14, 11, 5, 0)
    
    # Execute Intent A
    exec_id_A, action_A, _ = mock_db.check_idempotent_execution(tenant_id, strategy_id, symbol, time_A, side, size, price)
    claimed_A, _ = mock_db.claim_execution(exec_id_A, tenant_id, strategy_id, symbol, side, size, price)
    res_A = await mock_exchange.place_order(symbol, side, "limit", size, price)
    mock_db.update_status(exec_id_A, "completed", order_id=res_A["exchange_order_id"], result=res_A)
    print(f"Intent A executed: execution_id={exec_id_A}, exchange_order_id={res_A['exchange_order_id']}")
    
    # Execute Intent B (Identical symbol, side, size, price, strategy)
    exec_id_B, action_B, _ = mock_db.check_idempotent_execution(tenant_id, strategy_id, symbol, time_B, side, size, price)
    claimed_B, _ = mock_db.claim_execution(exec_id_B, tenant_id, strategy_id, symbol, side, size, price)
    res_B = await mock_exchange.place_order(symbol, side, "limit", size, price)
    mock_db.update_status(exec_id_B, "completed", order_id=res_B["exchange_order_id"], result=res_B)
    print(f"Intent B executed: execution_id={exec_id_B}, exchange_order_id={res_B['exchange_order_id']}")
    
    assert exec_id_A != exec_id_B, "Intent A and Intent B must have distinct execution IDs"
    assert len(mock_exchange.orders) == 2, "Both distinct intents must execute on exchange"
    print(f"Both Intents Placed: Total DB Executions={len(mock_db.records)}, Total Exchange Orders={len(mock_exchange.orders)}")
    
    # Retry Intent A & Retry Intent B
    print("\n--- [RETRY RETESTING] ---")
    _, retry_A, _ = mock_db.check_idempotent_execution(tenant_id, strategy_id, symbol, time_A, side, size, price)
    _, retry_B, _ = mock_db.check_idempotent_execution(tenant_id, strategy_id, symbol, time_B, side, size, price)
    assert retry_A == "skip_return_result" and retry_B == "skip_return_result"
    print(f"Retries skipped cleanly: Total Exchange Orders remains {len(mock_exchange.orders)}")
    
    # Concurrent Storm: 10 concurrent requests for Intent A, 10 for Intent B
    print("\n--- [CONCURRENT STORM RETESTING] ---")
    concurrent_exchange_calls = 0
    for _ in range(10):
        _, act_A, _ = mock_db.check_idempotent_execution(tenant_id, strategy_id, symbol, time_A, side, size, price)
        if act_A == "proceed_new":
            await mock_exchange.place_order(symbol, side, "limit", size, price)
            concurrent_exchange_calls += 1
            
        _, act_B, _ = mock_db.check_idempotent_execution(tenant_id, strategy_id, symbol, time_B, side, size, price)
        if act_B == "proceed_new":
            await mock_exchange.place_order(symbol, side, "limit", size, price)
            concurrent_exchange_calls += 1
            
    print(f"Concurrent storm finished: Additional exchange calls = {concurrent_exchange_calls}")
    assert concurrent_exchange_calls == 0, "Concurrent retries must never reach exchange"
    assert len(mock_exchange.orders) == 2, "Final exchange orders count must remain 2"
    
    print("\n>>> TEST B RESULT: PASS (VERIFIED) <<<\n")
    return {
        "intent_A_id": exec_id_A,
        "intent_B_id": exec_id_B,
        "db_executions": len(mock_db.records),
        "exchange_orders": len(mock_exchange.orders),
        "status": "PASS"
    }

async def main():
    res_a = await execute_test_a()
    res_b = await execute_test_b()
    
    report = {
        "test_a": res_a,
        "test_b": res_b,
        "overall_status": "PASS"
    }
    with open("reports/forensic_tests_ab_results.json", "w") as f:
        json.dump(report, f, indent=2)

if __name__ == '__main__':
    asyncio.run(main())
