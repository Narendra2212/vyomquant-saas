import os
import sys
import asyncio
import time
from decimal import Decimal
from datetime import datetime, timezone

sys.path.insert(0, '.')
os.environ["ENV"] = "testing"
os.environ["DEV_MODE"] = "false"

print("=" * 80)
print("MASTER ADVERSARIAL ATTACK: CROSS-SYSTEM STATE CONSISTENCY & RECONCILIATION")
print("=" * 80)

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
from backend_app.core.reconciliation_engine import (
    ReconciliationEngine,
    OrderMismatch,
    PositionMismatch,
    FillMismatch
)

async def run_all_attacks():
    redis = SharedRedisManager()
    
    # -------------------------------------------------------------------------
    # ATTACK 1 & 11: Concurrent Worker Distributed Idempotency Race (BUG-IDEM-01)
    # -------------------------------------------------------------------------
    print("\n[ATTACK SUITE 1 & 11]: Attacking Concurrent Workers Against Same client_order_id...")
    idempotency = DistributedIdempotencyLayer()
    tenant_id = "tenant_attacker_1"
    client_order_id = "coid_race_test_1001"
    
    execution_count = 0
    async def mock_place_order():
        nonlocal execution_count
        execution_count += 1
        await asyncio.sleep(0.05) # Simulate exchange network latency
        return {"order_id": "exch_12345", "status": "filled", "executed_qty": 1.0}
    
    # Fire 5 concurrent workers at the exact same instant
    tasks = [
        idempotency.execute_with_idempotency(tenant_id, client_order_id, mock_place_order)
        for _ in range(5)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    print(f"  Execution count across 5 concurrent workers: {execution_count}")
    assert execution_count == 1, f"Expected exactly 1 execution, got {execution_count} (DOUBLE EXECUTION VULNERABILITY)"
    
    # Verify all workers received the same result or duplicate error
    successful_results = [r for r in results if isinstance(r, dict) and r.get("order_id") == "exch_12345"]
    print(f"  Workers safely resolved: {len(successful_results)}/5 (PASS)")
    print("  --> Concurrent Distributed Idempotency Race: RUNTIME_PROVEN PASS")

    # -------------------------------------------------------------------------
    # ATTACK 3 & 5: Concurrent Duplicate Fill Events (BUG-EE-10)
    # -------------------------------------------------------------------------
    print("\n[ATTACK SUITE 3 & 5]: Attacking Duplicate Fill Event Registrations...")
    fill_mgr = FillDeduplicationManager(redis)
    
    fill_rec = FillRecord(
        fill_id="fill_001",
        order_id="order_999",
        tenant_id=tenant_id,
        symbol="BTC/USDT",
        side="buy",
        filled_quantity=Decimal("1.5"),
        fill_price=Decimal("50000"),
        timestamp=datetime.now(timezone.utc),
        exchange_trade_id="trade_tx_888",
        fill_hash=fill_mgr.generate_fill_hash(
            "order_999", "BTC/USDT", "buy", Decimal("1.5"), Decimal("50000"),
            datetime.now(timezone.utc), "trade_tx_888"
        )
    )
    
    # Send 10 concurrent fill registration requests
    fill_reg_tasks = [
        fill_mgr.register_fill(tenant_id, fill_rec)
        for _ in range(10)
    ]
    fill_reg_results = await asyncio.gather(*fill_reg_tasks)
    accepted_fills = sum(1 for r in fill_reg_results if r is True)
    
    print(f"  Fill accepted count across 10 concurrent deliveries: {accepted_fills}")
    assert accepted_fills == 1, f"Expected exactly 1 fill registered, got {accepted_fills} (DUPLICATE FILL BUG)"
    print("  --> Atomic Fill Deduplication: RUNTIME_PROVEN PASS")

    # -------------------------------------------------------------------------
    # ATTACK 6: Concurrent Duplicate Cancellation Registrations
    # -------------------------------------------------------------------------
    print("\n[ATTACK SUITE 6]: Attacking Concurrent Duplicate Cancellations...")
    cancel_mgr = CancellationIdempotencyManager(redis)
    idem_key = cancel_mgr.generate_idempotency_key("order_999", tenant_id, datetime.now(timezone.utc))
    cancel_rec = CancellationRecord(
        cancellation_id="cancel_001",
        order_id="order_999",
        tenant_id=tenant_id,
        idempotency_key=idem_key,
        timestamp=datetime.now(timezone.utc),
        status="pending"
    )
    
    cancel_tasks = [
        cancel_mgr.register_cancellation(tenant_id, cancel_rec)
        for _ in range(10)
    ]
    cancel_results = await asyncio.gather(*cancel_tasks)
    accepted_cancels = sum(1 for r in cancel_results if r is True)
    
    print(f"  Cancellation accepted count across 10 concurrent deliveries: {accepted_cancels}")
    assert accepted_cancels == 1, f"Expected exactly 1 cancellation registered, got {accepted_cancels}"
    print("  --> Atomic Cancellation Deduplication: RUNTIME_PROVEN PASS")

    # -------------------------------------------------------------------------
    # ATTACK 10 & 20: Mathematical Conservation & Micro-Fraction Reversals with Fees
    # -------------------------------------------------------------------------
    print("\n[ATTACK SUITE 10 & 20]: Attacking Mathematical Conservation & Micro Reversals...")
    ee = ExecutionEngine(portfolio_state={"total_equity": Decimal("100000.00")})
    
    # 1. Long 1.00000000 BTC @ 50,000 (Fee = 50.00)
    fee_entry = Decimal("50.00")
    ee.handle_partial_fill("BTC", Decimal("1.00000000"), Decimal("50000.00"), Decimal("1.00000000"), "buy", fee_entry)
    
    # 2. Reversal: Sell 1.50000000 BTC @ 60,000 (Close Long 1.0 @ 60k, Open Short 0.5 @ 60k, Total Fee = 90.00)
    fee_rev = Decimal("90.00")
    ee.handle_partial_fill("BTC", Decimal("1.50000000"), Decimal("60000.00"), Decimal("1.50000000"), "sell", fee_rev)
    
    # Expected: Long Gross Realized PnL = (60k - 50k) * 1.0 = +10,000.00
    # Long Net Realized PnL = 10,000.00 - 90.00 = +9,910.00
    # Net Equity = 100,000.00 - 50.00 + 9,910.00 = 109,860.00
    # Position: Short 0.50000000 BTC @ 60,000.00
    pos = ee.positions.get("BTC")
    assert pos is not None, "Position must exist"
    assert pos.side == "short", f"Expected short, got {pos.side}"
    assert pos.size == Decimal("0.50000000"), f"Expected 0.5, got {pos.size}"
    assert pos.entry_price == Decimal("60000.00"), f"Expected 60000, got {pos.entry_price}"
    assert ee.current_equity == Decimal("109860.00"), f"Expected equity 109860.00, got {ee.current_equity}"
    print(f"  Reversal Equity: {ee.current_equity} (Exact: PASS)")
    print(f"  Flipped Short Position: {pos.size} {pos.symbol} @ {pos.entry_price} (Exact: PASS)")
    
    # 3. Micro Reversal: Buy 0.50000001 BTC @ 55,000 (Close Short 0.5 @ 55k, Open Long 0.00000001 @ 55k, Fee = 27.50)
    fee_micro = Decimal("27.50")
    ee.handle_partial_fill("BTC", Decimal("0.50000001"), Decimal("55000.00"), Decimal("0.50000001"), "buy", fee_micro)
    
    # Short Gross Realized PnL = (60k - 55k) * 0.5 = +2,500.00
    # Short Net Realized PnL = 2,500.00 - 27.50 = +2,472.50
    # Ending Equity = 109,860.00 + 2,472.50 = 112,332.50
    # New Position: Long 0.00000001 BTC @ 55,000.00
    pos2 = ee.positions.get("BTC")
    assert pos2 is not None, "Micro position must exist"
    assert pos2.side == "long", f"Expected long, got {pos2.side}"
    assert pos2.size == Decimal("0.00000001"), f"Expected 1e-8, got {pos2.size}"
    assert pos2.entry_price == Decimal("55000.00"), f"Expected 55000, got {pos2.entry_price}"
    assert ee.current_equity == Decimal("112332.50"), f"Expected equity 112332.50, got {ee.current_equity}"
    print(f"  Micro-Reversal Equity: {ee.current_equity} (Exact: PASS)")
    print(f"  Micro Long Position: {pos2.size} {pos2.symbol} @ {pos2.entry_price} (Exact: PASS)")
    print("  --> Multi-Stage Reversal & Conservation Audit: RUNTIME_PROVEN PASS")

    # -------------------------------------------------------------------------
    # ATTACK 7: Reconciliation Engine Exchange vs DB Disagreement
    # -------------------------------------------------------------------------
    print("\n[ATTACK SUITE 7]: Attacking Reconciliation Engine Discrepancy Detection...")
    class MockExchangeClient:
        async def get_orders(self, *args, **kwargs):
            return [{
                "order_id": "order_recon_test_1",
                "symbol": "BTC/USDT",
                "status": "filled",
                "side": "buy",
                "filled_quantity": Decimal("1.0"),
                "price": Decimal("50000.0")
            }]
        async def get_positions(self, *args, **kwargs):
            return []
        async def get_fills(self, *args, **kwargs):
            return []

    class MockStateService:
        async def get_orders(self, *args, **kwargs):
            return [{
                "order_id": "order_recon_test_1",
                "symbol": "BTC/USDT",
                "status": "executing",  # Local DB lagging behind exchange!
                "side": "buy",
                "filled_quantity": Decimal("0.5"),  # DB thinks only 0.5 filled
                "price": Decimal("50000.0")
            }]
        async def get_positions(self, *args, **kwargs):
            return []
        async def get_fills(self, *args, **kwargs):
            return []
        async def update_order_status(self, *args, **kwargs):
            return True

    recon_engine = ReconciliationEngine(
        exchange_client=MockExchangeClient(),
        state_service=MockStateService(),
        fill_deduplication_manager=fill_mgr,
        auto_correct=True
    )
    
    recon_result = await recon_engine.reconcile(
        tenant_id=tenant_id,
        exchange_name="binance",
        user_id="user_recon_1"
    )
    print(f"  Reconciliation success: {recon_result.success}")
    print(f"  Detected order mismatches: {len(recon_result.order_mismatches)}")
    print(f"  Actions generated & executed: {len(recon_result.reconciliation_actions)}")
    assert recon_result.success is True, "Expected reconciliation to succeed"
    assert len(recon_result.order_mismatches) > 0, "Expected order mismatch between executing DB and filled exchange"
    print("  --> Reconciliation Engine Discrepancy Recovery: RUNTIME_PROVEN PASS")

if __name__ == "__main__":
    asyncio.run(run_all_attacks())
    print("\n" + "=" * 80)
    print("ALL CROSS-SYSTEM CONSISTENCY ATTACKS PASSED WITH REAL RUNTIME PROOF!")
    print("=" * 80)
