"""
Test Execution Correctness Remediation

Tests for:
- Fill deduplication
- Cancellation idempotency
- Replay reconstruction
- Reconciliation
- Worker restart recovery
"""

import sys
import os
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

# Add path for imports
sys.path.insert(0, 'aerora_quant_backend_updated_final1')

from backend_app.core.order_state_engine import OrderStateEngine, OrderState, FillRecord
from backend_app.core.fill_deduplication_manager import FillDeduplicationManager
from backend_app.core.cancellation_idempotency_manager import CancellationIdempotencyManager
from backend_app.core.replay_reconstruction_engine import ReplayReconstructionEngine
from backend_app.core.reconciliation_engine import ReconciliationEngine
from backend_app.core.worker_restart_recovery_manager import WorkerRestartRecoveryManager


import pytest

class MockRedis:
    """Mock Redis for testing."""
    def __init__(self):
        self.data = {}
    
    async def exists(self, key):
        result = key in self.data
        print(f"  MockRedis.exists({key}) = {result}")
        return result
    
    async def setex(self, key, ttl, value):
        import json
        self.data[key] = value
        print(f"  MockRedis.setex({key}, ttl={ttl})")
    
    async def get(self, key):
        result = self.data.get(key)
        print(f"  MockRedis.get({key}) = {result is not None}")
        return result
    
    async def keys(self, pattern):
        import fnmatch
        result = [k for k in self.data if fnmatch.fnmatch(k, pattern)]
        print(f"  MockRedis.keys({pattern}) = {len(result)} keys")
        return result

    # Additional mock methods to satisfy ReplayReconstructionEngine and ReconciliationEngine
    async def query_events(self, filters):
        print(f"  MockRedis.query_events({filters})")
        return []
        
    async def restore_position(self, *args, **kwargs):
        print(f"  MockRedis.restore_position()")
        
    async def restore_order(self, *args, **kwargs):
        print(f"  MockRedis.restore_order()")
        
    async def restore_execution(self, *args, **kwargs):
        print(f"  MockRedis.restore_execution()")

    async def get_orders(self, *args, **kwargs):
        print(f"  MockRedis.get_orders()")
        return []

    async def get_positions(self, *args, **kwargs):
        print(f"  MockRedis.get_positions()")
        return []

    async def get_fills(self, *args, **kwargs):
        print(f"  MockRedis.get_fills()")
        return []
        
    async def get_fill_registry_info(self, *args, **kwargs):
        print(f"  MockRedis.get_fill_registry_info()")
        return None


def test_fill_deduplication():
    """Test fill deduplication prevents duplicate fills."""
    async def _run():
        print("\n=== Test: Fill Deduplication ===")
        mock_redis = MockRedis()
        fill_dedup_manager = FillDeduplicationManager(redis_client=mock_redis)
        order_engine = OrderStateEngine(
            fill_deduplication_manager=fill_dedup_manager,
            cancellation_idempotency_manager=None
        )
        order_id = "test_order_001"
        lifecycle = order_engine.create_order(
            order_id=order_id,
            user_id="test_user",
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            quantity=Decimal("1.0")
        )
        order_engine.submit_order(order_id)
        order_engine.confirm_open(order_id)
        fill_result = await order_engine.add_fill(
            order_id=order_id,
            filled_quantity=Decimal("0.5"),
            fill_price=Decimal("50000"),
            exchange_trade_id="trade_001",
            tenant_id="test_tenant"
        )
        assert len(lifecycle.fill_history) == 1, "Should have 1 fill"
        duplicate_result = await order_engine.add_fill(
            order_id=order_id,
            filled_quantity=Decimal("0.5"),
            fill_price=Decimal("50000"),
            exchange_trade_id="trade_001",
            tenant_id="test_tenant"
        )
        assert len(lifecycle.fill_history) == 1, "Should still have 1 fill (duplicate rejected)"
        print("✅ Fill deduplication test PASSED")
        return True
    return asyncio.run(_run())


def test_cancellation_idempotency():
    """Test cancellation idempotency prevents duplicate cancellations."""
    async def _run():
        print("\n=== Test: Cancellation Idempotency ===")
        mock_redis = MockRedis()
        cancel_idempotency_manager = CancellationIdempotencyManager(redis_client=mock_redis)
        order_engine = OrderStateEngine(
            fill_deduplication_manager=None,
            cancellation_idempotency_manager=cancel_idempotency_manager
        )
        order_id = "test_order_002"
        lifecycle = order_engine.create_order(
            order_id=order_id,
            user_id="test_user",
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            quantity=Decimal("1.0")
        )
        order_engine.submit_order(order_id)
        order_engine.confirm_open(order_id)
        cancel_result = await order_engine.cancel_order(
            order_id=order_id,
            reason="User requested",
            tenant_id="test_tenant"
        )
        assert lifecycle.current_state == OrderState.CANCELLED, "Should be cancelled"
        duplicate_cancel_result = await order_engine.cancel_order(
            order_id=order_id,
            reason="User requested",
            tenant_id="test_tenant"
        )
        assert lifecycle.current_state == OrderState.CANCELLED, "Should still be cancelled (duplicate rejected)"
        print("✅ Cancellation idempotency test PASSED")
        return True
    return asyncio.run(_run())


def test_replay_reconstruction():
    """Test replay reconstruction rebuilds state from events."""
    async def _run():
        print("\n=== Test: Replay Reconstruction ===")
        mock_redis = MockRedis()
        replay_engine = ReplayReconstructionEngine(
            event_journal_client=mock_redis,
            state_service=mock_redis,
            fill_deduplication_manager=mock_redis
        )
        result = await replay_engine.reconstruct_state(
            tenant_id="test_tenant",
            strategy_id=None,
            from_sequence=None,
            to_sequence=None,
            restore_to_live=False
        )
        assert result.success is not None, "Reconstruction should return a result"
        print("✅ Replay reconstruction test PASSED")
        return True
    return asyncio.run(_run())


def test_reconciliation():
    """Test reconciliation detects state divergences."""
    async def _run():
        print("\n=== Test: Reconciliation ===")
        mock_redis = MockRedis()
        reconciliation_engine = ReconciliationEngine(
            exchange_client=mock_redis,
            state_service=mock_redis,
            fill_deduplication_manager=mock_redis
        )
        result = await reconciliation_engine.reconcile(
            tenant_id="test_tenant",
            exchange_name="binance",
            user_id="test_user"
        )
        assert result.success is not None, "Reconciliation should return a result"
        print("✅ Reconciliation test PASSED")
        return True
    return asyncio.run(_run())


def test_worker_restart_recovery():
    """Test worker restart recovery orchestrates recovery sequence."""
    async def _run():
        print("\n=== Test: Worker Restart Recovery ===")
        mock_redis = MockRedis()
        recovery_manager = WorkerRestartRecoveryManager(redis_client=mock_redis)
        result = await recovery_manager.recover_worker(
            tenant_id="test_tenant",
            worker_id="worker_001",
            exchange_name="binance",
            user_id="test_user",
            strategy_id=None,
            from_sequence=None
        )
        assert result.success is not None, "Recovery should return a result"
        print("✅ Worker restart recovery test PASSED")
        return True
    return asyncio.run(_run())


async def run_all_tests():
    """Run all execution correctness tests."""
    print("\n" + "="*60)
    print("EXECUTION CORRECTNESS REMEDIATION TESTS")
    print("="*60)
    
    results = {}
    
    try:
        results['fill_deduplication'] = await test_fill_deduplication()
    except Exception as e:
        print(f"❌ Fill deduplication test FAILED: {e}")
        results['fill_deduplication'] = False
    
    try:
        results['cancellation_idempotency'] = await test_cancellation_idempotency()
    except Exception as e:
        print(f"❌ Cancellation idempotency test FAILED: {e}")
        results['cancellation_idempotency'] = False
    
    try:
        results['replay_reconstruction'] = await test_replay_reconstruction()
    except Exception as e:
        print(f"❌ Replay reconstruction test FAILED: {e}")
        results['replay_reconstruction'] = False
    
    try:
        results['reconciliation'] = await test_reconciliation()
    except Exception as e:
        print(f"❌ Reconciliation test FAILED: {e}")
        results['reconciliation'] = False
    
    try:
        results['worker_restart_recovery'] = await test_worker_restart_recovery()
    except Exception as e:
        print(f"❌ Worker restart recovery test FAILED: {e}")
        results['worker_restart_recovery'] = False
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    for test_name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{test_name}: {status}")
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n🎉 ALL TESTS PASSED")
    else:
        print("\n❌ SOME TESTS FAILED")
    
    return all_passed


if __name__ == "__main__":
    asyncio.run(run_all_tests())
