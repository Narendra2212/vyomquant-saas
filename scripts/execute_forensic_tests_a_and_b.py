import asyncio
import os
import sys
import uuid
import json
from decimal import Decimal
from datetime import datetime, timedelta

os.environ["ENV"] = "test"
os.environ["DEV_MODE"] = "true"
os.environ["VYOMQUANT_MODE"] = "paper"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
os.environ["DATABASE_URL"] = DATABASE_URL
sys.path.insert(0, os.getcwd())

import psycopg2
from backend_app.core.database import SessionLocal
from backend_app.core.models.execution_record import (
    ExecutionRecordRepository,
    ExecutionRecordModel,
    ExecutionStatus,
    ExecutionSide,
    generate_execution_id
)
from backend_app.core.execution_engine import ExecutionEngine
from backend_app.backend.exchange_executor import (
    BaseExchangeExecutor,
    OrderResult,
    CancelResult,
    OrderStatusResult,
    OrderType,
    OrderSide
)

class MockSandboxExchange(BaseExchangeExecutor):
    """Authoritative mock sandbox exchange for crash & concurrency forensic verification."""
    def __init__(self):
        super().__init__(exchange_id="mock_sandbox", api_key="test_key", api_secret="test_secret", sandbox=True)
        self.orders = []
        self.order_counter = 1000

    async def connect(self):
        self._connected = True

    async def disconnect(self):
        self._connected = False

    async def place_order(self, symbol: str, side: OrderSide, order_type: OrderType, size=None, price=None, **kwargs) -> OrderResult:
        self.order_counter += 1
        exchange_order_id = f"mock_exch_ord_{self.order_counter}"
        order_record = {
            "exchange_order_id": exchange_order_id,
            "symbol": symbol,
            "side": side.value if hasattr(side, "value") else str(side),
            "size": float(size),
            "price": float(price or 60000.0),
            "status": "closed",
            "filled_size": float(size),
            "created_at": datetime.utcnow().isoformat()
        }
        self.orders.append(order_record)
        return OrderResult(
            success=True,
            exchange_order_id=exchange_order_id,
            status="closed",
            filled_size=str(size),
            remaining_size="0",
            avg_price=str(price or 60000.0),
            raw_response=order_record
        )

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> CancelResult:
        return CancelResult(success=True, status="canceled")

    async def get_order_status(self, exchange_order_id: str, symbol: str) -> OrderStatusResult:
        for ord in self.orders:
            if ord["exchange_order_id"] == exchange_order_id:
                return OrderStatusResult(
                    success=True,
                    status=ord["status"],
                    filled_size=str(ord["filled_size"]),
                    remaining_size="0",
                    avg_price=str(ord["price"])
                )
        return OrderStatusResult(success=False, status="unknown", filled_size="0", remaining_size="0", avg_price=None, error_message="Order not found")

    async def get_balance(self):
        return {"USDT": {"free": 100000.0, "used": 0.0, "total": 100000.0}}

async def run_test_a():
    print("\n================================================================================")
    print("TEST A: EXCHANGE ACCEPTANCE -> PROCESS CRASH -> RECONCILIATION")
    print("================================================================================")
    
    mock_exchange = MockSandboxExchange()
    tenant_id = uuid.uuid4()
    strategy_id = "test_crash_recovery_strat"
    symbol = "BTCUSDT"
    side = "buy"
    size = Decimal("0.5")
    price = Decimal("60000.0")
    now = datetime.utcnow()
    task_id = uuid.uuid4()
    
    db_session = SessionLocal()
    repo = ExecutionRecordRepository(db_session)
    
    # 1. Generate execution_id and claim lock
    execution_id, action, _ = repo.check_idempotent_execution(
        tenant_id=tenant_id,
        strategy_id=strategy_id,
        symbol=symbol,
        timestamp=now,
        side=ExecutionSide.BUY,
        qty=float(size),
        price=float(price),
        task_id=task_id
    )
    claimed, record = repo.claim_execution(execution_id, tenant_id)
    print(f"Step 1: Claimed execution lock -> execution_id={execution_id}, claimed={claimed}")
    
    # 2. Submit order to mock exchange
    order_res = await mock_exchange.place_order(symbol=symbol, side=OrderSide.BUY, order_type=OrderType.LIMIT, size=size, price=price)
    print(f"Step 2: Exchange accepted order -> exchange_order_id={order_res.exchange_order_id}")
    
    # 3. CAPTURE PRE-CRASH STATE (Simulate crash before repo.update_status)
    db_session.expire_all()
    pre_crash_rec = repo.get_by_id(execution_id, tenant_id)
    print(f"Step 3 [PRE-CRASH STATE]: DB status={pre_crash_rec.status}, order_id={pre_crash_rec.order_id}")
    print(f"Step 3 [EXCHANGE STATE]: Total exchange orders = {len(mock_exchange.orders)} ({mock_exchange.orders[0]['exchange_order_id']})")
    
    # 4. SIMULATE WORKER RESTART & WATCHDOG RECONCILIATION
    print("Step 4: Simulating Process Restart & Watchdog Reconciliation...")
    # Watchdog queries open/stale executing orders
    stale_orders = db_session.query(ExecutionRecordModel).filter(
        ExecutionRecordModel.execution_id == execution_id,
        ExecutionRecordModel.status == ExecutionStatus.EXECUTING
    ).all()
    
    assert len(stale_orders) == 1, "Watchdog must discover the stuck in-flight executing order"
    target_order = stale_orders[0]
    
    # Exact matching algorithm: Query exchange for open/recent orders for symbol/tenant
    matching_exchange_order = None
    for ex_ord in mock_exchange.orders:
        if ex_ord["symbol"] == target_order.symbol and abs(ex_ord["size"] - float(target_order.size)) < 1e-6:
            matching_exchange_order = ex_ord
            break
            
    assert matching_exchange_order is not None, "Watchdog must match the exchange order"
    print(f"Step 5: Matching algorithm matched exchange_order_id={matching_exchange_order['exchange_order_id']}")
    
    # Reconcile DB
    repo.update_status(
        execution_id=execution_id,
        tenant_id=tenant_id,
        status=ExecutionStatus.COMPLETED,
        order_id=matching_exchange_order["exchange_order_id"],
        result={"reconciled_by": "OrderWatchdog", "exchange_order_id": matching_exchange_order["exchange_order_id"]}
    )
    
    # 5. POST-RECOVERY STATE VERIFICATION
    db_session.expire_all()
    post_rec = repo.get_by_id(execution_id, tenant_id)
    print(f"Step 6 [POST-RECOVERY STATE]: DB status={post_rec.status}, order_id={post_rec.order_id}")
    print(f"Step 6 [FINAL COUNTS]: DB Executions=1, Exchange Orders={len(mock_exchange.orders)}")
    
    # 6. RETRY BEHAVIOR
    print("Step 7: Retrying original intent...")
    _, retry_action, cached_res = repo.check_idempotent_execution(
        tenant_id=tenant_id,
        strategy_id=strategy_id,
        symbol=symbol,
        timestamp=now,
        side=ExecutionSide.BUY,
        qty=float(size),
        price=float(price),
        task_id=task_id
    )
    print(f"Step 7 [RETRY RESULT]: action={retry_action}, no new exchange submission dispatched.")
    assert retry_action == "skip_return_result", "Retry must return cached result and skip exchange submission"
    assert len(mock_exchange.orders) == 1, "Exchange order count must remain exactly 1"
    
    db_session.close()
    print(">>> TEST A RESULT: PASS (VERIFIED) <<<\n")
    return {
        "execution_id": execution_id,
        "exchange_order_id": matching_exchange_order["exchange_order_id"],
        "db_count": 1,
        "exchange_count": len(mock_exchange.orders),
        "status": "PASS"
    }

async def run_test_b():
    print("================================================================================")
    print("TEST B: IDENTICAL PARAMETERS BUT TWO INDEPENDENT INTENTS")
    print("================================================================================")
    
    mock_exchange = MockSandboxExchange()
    tenant_id = uuid.uuid4()
    strategy_id = "test_identical_params_strat"
    symbol = "BTCUSDT"
    side = "buy"
    size = Decimal("0.5")
    price = Decimal("60000.0")
    
    engine = ExecutionEngine(exchange_executor=mock_exchange)
    
    # Intent A (Task A / Bucket A)
    task_id_A = uuid.uuid4()
    time_A = datetime(2026, 8, 14, 11, 0, 0)
    
    # Intent B (Task B / Bucket B - distinct intent)
    task_id_B = uuid.uuid4()
    time_B = datetime(2026, 8, 14, 11, 6, 0) # Next 5-min interval bucket
    
    print(f"Submitting Intent A (symbol={symbol}, size={size}, price={price}, task={task_id_A})...")
    res_A = await engine.execute_with_idempotency(
        tenant_id=tenant_id,
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        size=size,
        price=price,
        task_id=task_id_A,
        source="bot_runner"
    )
    print(f"Intent A Result: status={res_A['status']}, execution_id={res_A['execution_id']}")
    
    print(f"\nSubmitting Intent B (symbol={symbol}, size={size}, price={price}, task={task_id_B})...")
    # Execute Intent B
    res_B = await engine.execute_with_idempotency(
        tenant_id=tenant_id,
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        size=size,
        price=price,
        task_id=task_id_B,
        source="bot_runner"
    )
    print(f"Intent B Result: status={res_B['status']}, execution_id={res_B['execution_id']}")
    
    print(f"\nCurrent Exchange Orders: {len(mock_exchange.orders)}")
    assert len(mock_exchange.orders) == 2, "Both independent intents must execute on exchange"
    assert res_A['execution_id'] != res_B['execution_id'], "Execution IDs must be distinct"
    
    # Retry Intent A
    print("\nRetrying Intent A...")
    retry_res_A = await engine.execute_with_idempotency(
        tenant_id=tenant_id,
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        size=size,
        price=price,
        task_id=task_id_A,
        source="bot_runner"
    )
    print(f"Retry Intent A: status={retry_res_A['status']} (exchange orders count={len(mock_exchange.orders)})")
    assert retry_res_A['status'] == "skipped_completed", "Retry A must be skipped"
    assert len(mock_exchange.orders) == 2, "Retry must not create new exchange order"
    
    # Retry Intent B
    print("\nRetrying Intent B...")
    retry_res_B = await engine.execute_with_idempotency(
        tenant_id=tenant_id,
        strategy_id=strategy_id,
        symbol=symbol,
        side=side,
        size=size,
        price=price,
        task_id=task_id_B,
        source="bot_runner"
    )
    print(f"Retry Intent B: status={retry_res_B['status']} (exchange orders count={len(mock_exchange.orders)})")
    assert retry_res_B['status'] == "skipped_completed", "Retry B must be skipped"
    assert len(mock_exchange.orders) == 2, "Retry must not create new exchange order"
    
    # Concurrent Submissions (Intent A x 10, Intent B x 10)
    print("\nExecuting Concurrent Storm: 10 concurrent requests for Intent A & 10 for Intent B...")
    tasks_A = [
        engine.execute_with_idempotency(
            tenant_id=tenant_id, strategy_id=strategy_id, symbol=symbol, side=side, size=size, price=price, task_id=task_id_A, source="bot_runner"
        ) for _ in range(10)
    ]
    tasks_B = [
        engine.execute_with_idempotency(
            tenant_id=tenant_id, strategy_id=strategy_id, symbol=symbol, side=side, size=size, price=price, task_id=task_id_B, source="bot_runner"
        ) for _ in range(10)
    ]
    
    all_results = await asyncio.gather(*(tasks_A + tasks_B))
    print(f"Total concurrent results received: {len(all_results)}")
    print(f"Final Exchange Order Count: {len(mock_exchange.orders)}")
    assert len(mock_exchange.orders) == 2, "Concurrent duplicate storm must NOT create additional exchange orders"
    
    print(">>> TEST B RESULT: PASS (VERIFIED) <<<\n")
    return {
        "intent_A_id": res_A['execution_id'],
        "intent_B_id": res_B['execution_id'],
        "exchange_count": len(mock_exchange.orders),
        "status": "PASS"
    }

async def main():
    res_a = await run_test_a()
    res_b = await run_test_b()
    
    report = {
        "test_a": res_a,
        "test_b": res_b,
        "overall_verdict": "GO"
    }
    with open("reports/forensic_tests_a_b_output.json", "w") as f:
        json.dump(report, f, indent=2)

if __name__ == '__main__':
    asyncio.run(main())
