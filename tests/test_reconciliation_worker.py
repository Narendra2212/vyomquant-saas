"""
tests/test_reconciliation_worker.py — RECONCILIATION WORKER TESTS

STEP 4: Verify execution reconciliation loop
"""

import sys
import os
import asyncio
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend_app.backend.reconciliation_worker import (
    ReconciliationWorker,
    Mismatch,
    MismatchType,
    ReconciliationResult,
    get_reconciliation_worker,
)


def test_mismatch_creation():
    """Test mismatch data model."""
    mismatch = Mismatch(
        mismatch_type=MismatchType.ORDER_STATUS,
        user_id="user_123",
        exchange="binance",
        symbol="BTC-USD",
        local_state={"status": "pending", "order_id": "ord_123"},
        exchange_state={"status": "filled", "filled": 1.0},
        description="Order status mismatch",
        severity="critical"
    )
    
    assert mismatch.mismatch_type == MismatchType.ORDER_STATUS
    assert mismatch.user_id == "user_123"
    assert mismatch.severity == "critical"
    
    # Test serialization
    data = mismatch.to_dict()
    assert data["mismatch_type"] == "order_status"
    assert data["user_id"] == "user_123"
    
    print("[PASS] Mismatch model working")


def test_reconciliation_result():
    """Test reconciliation result model."""
    result = ReconciliationResult(
        user_id="user_123",
        exchange="binance",
        orders_checked=10,
        positions_checked=3,
        mismatches_found=2,
        mismatches_corrected=2,
        duration_seconds=0.5
    )
    
    assert result.user_id == "user_123"
    assert result.orders_checked == 10
    assert result.mismatches_found == 2
    
    # Test serialization
    data = result.to_dict()
    assert data["user_id"] == "user_123"
    assert data["orders_checked"] == 10
    
    print("[PASS] Reconciliation result model working")


def test_mismatch_types():
    """Test mismatch type enumeration."""
    assert MismatchType.ORDER_STATUS.value == "order_status"
    assert MismatchType.POSITION_SIZE.value == "position_size"
    assert MismatchType.ORDER_MISSING_LOCAL.value == "order_missing_local"
    assert MismatchType.ORDER_MISSING_EXCHANGE.value == "order_missing_exchange"
    
    print("[PASS] Mismatch types defined")


def test_worker_initialization():
    """Test reconciliation worker initialization."""
    worker = ReconciliationWorker(
        interval_seconds=5.0,
        auto_correct=True,
        alert_threshold=5
    )
    
    assert worker.interval_seconds == 5.0
    assert worker.auto_correct is True
    assert worker.alert_threshold == 5
    
    print("[PASS] Worker initialization working")


def test_worker_metrics():
    """Test worker metrics."""
    worker = ReconciliationWorker()
    
    metrics = worker.get_metrics()
    assert "reconciliation_runs_total" in metrics
    assert "mismatches_detected_total" in metrics
    
    print("[PASS] Worker metrics working")


def test_global_singleton():
    """Test global singleton instance."""
    worker1 = get_reconciliation_worker()
    worker2 = get_reconciliation_worker()
    
    assert worker1 is worker2
    print("[PASS] Global singleton working")


def test_compare_orders():
    """Test order comparison logic."""
    worker = ReconciliationWorker()
    
    # Test case: Order missing locally
    local_orders = {}
    exchange_orders = [{"id": "ord_123", "symbol": "BTC-USD", "status": "open"}]
    
    mismatches = worker._compare_orders("user_123", "binance", local_orders, exchange_orders)
    
    assert len(mismatches) == 1
    assert mismatches[0].mismatch_type == MismatchType.ORDER_MISSING_LOCAL
    
    # Test case: Ghost order (missing on exchange)
    from unittest.mock import MagicMock
    local_order = MagicMock()
    local_order.status = "open"  # Simple string instead of enum
    local_order.symbol = "BTC-USD"
    local_order.to_dict.return_value = {"order_id": "ord_123", "status": "open"}
    
    local_orders = {"ord_123": local_order}
    exchange_orders = []
    
    mismatches = worker._compare_orders("user_123", "binance", local_orders, exchange_orders)
    
    assert len(mismatches) == 1
    assert mismatches[0].mismatch_type == MismatchType.ORDER_MISSING_EXCHANGE
    
    print("[PASS] Order comparison working")


def test_compare_positions():
    """Test position comparison logic."""
    worker = ReconciliationWorker()
    
    # Test case: Position size mismatch
    from unittest.mock import MagicMock
    local_pos = MagicMock()
    local_pos.quantity = Decimal("1.0")
    local_pos.to_dict.return_value = {"quantity": "1.0"}
    
    local_positions = {"BTC-USD": local_pos}
    exchange_positions = [{"symbol": "BTC-USD", "contracts": 1.5}]
    
    mismatches = worker._compare_positions("user_123", "binance", local_positions, exchange_positions)
    
    assert len(mismatches) == 1
    assert mismatches[0].mismatch_type == MismatchType.POSITION_SIZE
    
    print("[PASS] Position comparison working")


def run_all_tests():
    """Run all reconciliation worker tests."""
    print("=" * 60)
    print("RECONCILIATION WORKER TESTS")
    print("=" * 60)
    
    tests = [
        test_mismatch_creation,
        test_reconciliation_result,
        test_mismatch_types,
        test_worker_initialization,
        test_worker_metrics,
        test_global_singleton,
        test_compare_orders,
        test_compare_positions,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            print(f"\n--- {test.__name__} ---")
            test()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)


if __name__ == "__main__":
    from decimal import Decimal
    run_all_tests()
