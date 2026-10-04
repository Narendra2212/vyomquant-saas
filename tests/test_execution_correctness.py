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
    """Mock Redis / state service / exchange client for testing.

    SEEDABLE, and that is the point.

    Every query method here used to ``return []`` unconditionally, so the replay,
    reconciliation and worker-restart engines could only ever be exercised on the EMPTY
    path: nothing to reconstruct, nothing to compare, nothing to restore. Combined with
    assertions that only checked ``result.success is not None``, the three tests below
    could not distinguish a working engine from one that returned an empty result and did
    nothing — which is why they asserted so little.

    The four seeds are keyword-only and default to empty, so every existing construction
    (``MockRedis()``) behaves exactly as before and the fill-deduplication and
    cancellation-idempotency tests are untouched.

    ``get_orders`` is called with ONE argument by ``ReconciliationEngine`` as the exchange
    client (``get_orders(user_id)``) and TWO as the state service
    (``get_orders(tenant_id, user_id)``), so the signatures stay ``*args, **kwargs``.
    """

    def __init__(self, *, events=None, orders=None, positions=None, fills=None):
        self.data = {}
        self._events = list(events or [])
        self._orders = list(orders or [])
        self._positions = list(positions or [])
        self._fills = list(fills or [])
        #: Every restore_* call the engines made, so a test can assert state was written
        #: back rather than merely computed.
        self.restored = {"positions": [], "orders": [], "executions": []}
    
    async def exists(self, key):
        result = key in self.data
        print(f"  MockRedis.exists({key}) = {result}")
        return result
    
    async def setex(self, key, ttl, value):
        import json
        self.data[key] = value
        print(f"  MockRedis.setex({key}, ttl={ttl})")
    
    async def set(self, key, value, ex=None, nx=False):
        import json
        if nx and key in self.data:
            return None
        self.data[key] = value
        print(f"  MockRedis.set({key}, ex={ex}, nx={nx})")
        return True
    
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
        print(f"  MockRedis.query_events({filters}) = {len(self._events)} events")
        # A copy: the engine sorts the list it is handed, and a double that let that
        # mutation reach the seed would make a second call observe a different order.
        return list(self._events)
        
    async def restore_position(self, *args, **kwargs):
        print(f"  MockRedis.restore_position()")
        self.restored["positions"].append((args, kwargs))
        
    async def restore_order(self, *args, **kwargs):
        print(f"  MockRedis.restore_order()")
        self.restored["orders"].append((args, kwargs))
        
    async def restore_execution(self, *args, **kwargs):
        print(f"  MockRedis.restore_execution()")
        self.restored["executions"].append((args, kwargs))

    async def get_orders(self, *args, **kwargs):
        print(f"  MockRedis.get_orders() = {len(self._orders)} orders")
        return list(self._orders)

    async def get_positions(self, *args, **kwargs):
        print(f"  MockRedis.get_positions() = {len(self._positions)} positions")
        return list(self._positions)

    async def get_fills(self, *args, **kwargs):
        print(f"  MockRedis.get_fills() = {len(self._fills)} fills")
        return list(self._fills)
        
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
    """Replay reconstruction must rebuild state from the journal, in sequence order.

    WHAT THIS USED TO ASSERT, AND WHY IT WAS NOTHING
    ------------------------------------------------
    ``assert result.success is not None``. ``success`` is a ``bool``, so that holds for
    ``True`` AND for ``False`` — the only way to fail it was to return ``None``. The engine
    was also handed a ``MockRedis`` whose ``query_events`` returned ``[]`` unconditionally,
    so there was nothing to reconstruct: a completely broken engine that returned an empty
    result passed, and so did one that returned ``success=False``.

    WHAT IT ASSERTS NOW
    -------------------
    Events are seeded and the reconstructed state is checked against them: the order exists,
    it carries the status the LAST event in sequence order gave it, and the position was
    rebuilt. Sequence ordering is exercised by seeding the events OUT of order — the engine
    sorts on ``sequence``, so an engine that replayed them as given would leave the order at
    ``"submitted"`` and fail.
    """
    async def _run():
        print("\n=== Test: Replay Reconstruction ===")

        # Seeded out of sequence order on purpose: ACCEPTED (2) before SUBMITTED (1). The
        # engine sorts by `sequence`, so correct replay ends at "accepted"; replaying in the
        # order given would drop ACCEPTED on the floor (its order does not exist yet) and
        # leave "submitted".
        events = [
            {
                "sequence": 2,
                "event_type": "ORDER_ACCEPTED",
                "payload": {"order_id": "ORD-1"},
            },
            {
                "sequence": 1,
                "event_type": "ORDER_SUBMITTED",
                "payload": {
                    "order_id": "ORD-1",
                    "tenant_id": "test_tenant",
                    "strategy_id": "test_strategy",
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "order_type": "limit",
                    "quantity": "0.5",
                    "price": "40000",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                },
            },
            {
                "sequence": 3,
                "event_type": "POSITION_OPENED",
                "payload": {
                    "position_id": "POS-1",
                    "tenant_id": "test_tenant",
                    "strategy_id": "test_strategy",
                    "symbol": "BTC/USDT",
                    "side": "long",
                    "quantity": "0.5",
                    "entry_price": "40000",
                    "current_price": "40000",
                    "timestamp": "2026-01-01T00:00:01+00:00",
                },
            },
        ]

        mock_redis = MockRedis(events=events)
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

        assert result.success is True, (
            f"reconstruction reported failure: errors={result.errors}"
        )
        assert result.errors == [], f"reconstruction recorded errors: {result.errors}"
        assert result.events_processed == len(events), (
            f"the engine processed {result.events_processed} of {len(events)} seeded "
            f"events, so some were silently skipped"
        )

        orders = {o.order_id: o for o in result.orders}
        assert "ORD-1" in orders, (
            f"the submitted order was not reconstructed at all: {result.orders!r}"
        )
        assert orders["ORD-1"].status == "accepted", (
            f"the order's status is {orders['ORD-1'].status!r}, not 'accepted'. The engine "
            f"must apply events in `sequence` order — these were seeded out of order, and "
            f"replaying them as given leaves the order at 'submitted' because ORDER_ACCEPTED "
            f"arrives before the order exists."
        )
        assert orders["ORD-1"].symbol == "BTC/USDT"
        assert orders["ORD-1"].quantity == Decimal("0.5"), (
            f"quantity was reconstructed as {orders['ORD-1'].quantity!r}, not Decimal('0.5')"
        )

        positions = {p.position_id for p in result.positions}
        assert "POS-1" in positions, (
            f"the opened position was not reconstructed: {result.positions!r}"
        )

        print("✅ Replay reconstruction test PASSED")
        return True
    return asyncio.run(_run())


def test_reconciliation():
    """Reconciliation must DETECT a divergence between exchange and local state.

    WHAT THIS USED TO ASSERT, AND WHY IT WAS NOTHING
    ------------------------------------------------
    Two problems, either of which alone made it vacuous:

    1. ``assert result.success is not None`` — true for ``True`` and for ``False``.
    2. The SAME ``MockRedis`` instance was passed as both ``exchange_client`` and
       ``state_service``. Reconciliation's entire job is to compare those two sources, so
       with one object behind both of them a divergence was not merely absent, it was
       **unrepresentable**. The engine could not have failed this test.

    WHAT IT ASSERTS NOW
    -------------------
    Two distinct doubles, seeded to disagree in a way a real venue disagreement looks like:
    the exchange reports ``ORD-1`` filled, local state still has it ``open``, and the
    exchange knows an order local state has never heard of. Both mismatches must be found,
    classified, and turned into corrective actions.
    """
    async def _run():
        print("\n=== Test: Reconciliation ===")

        # The exchange's view: ORD-1 is filled, and ORD-2 exists.
        exchange = MockRedis(
            orders=[
                {"order_id": "ORD-1", "status": "filled", "quantity": "1", "symbol": "BTC/USDT"},
                {"order_id": "ORD-2", "status": "open", "quantity": "2", "symbol": "ETH/USDT"},
            ]
        )
        # Local state's view: ORD-1 is still open, and ORD-2 was never recorded.
        local = MockRedis(
            orders=[
                {"order_id": "ORD-1", "status": "open", "quantity": "1", "symbol": "BTC/USDT"},
            ]
        )

        reconciliation_engine = ReconciliationEngine(
            exchange_client=exchange,
            state_service=local,
            fill_deduplication_manager=local
        )
        result = await reconciliation_engine.reconcile(
            tenant_id="test_tenant",
            exchange_name="binance",
            user_id="test_user"
        )

        assert result.success is True, f"reconciliation reported failure: {result!r}"

        found = {m.mismatch_type for m in result.order_mismatches}
        assert "status_divergence" in found, (
            f"the exchange reports ORD-1 filled and local state reports it open; "
            f"reconciliation found {found or 'nothing'}. A status divergence on a live "
            f"order is the case this engine exists for."
        )
        assert "missing_order" in found, (
            f"ORD-2 exists on the exchange and not locally; reconciliation found "
            f"{found or 'nothing'}. An order the venue knows about and we do not is an "
            f"unrecorded position."
        )

        by_id = {m.order_id: m for m in result.order_mismatches}
        assert by_id["ORD-1"].local_status == "open"
        assert by_id["ORD-1"].exchange_status == "filled"

        assert result.reconciliation_actions, (
            "mismatches were detected but no corrective action was generated, so nothing "
            "would be done about them"
        )

        print("✅ Reconciliation test PASSED")
        return True
    return asyncio.run(_run())


def test_reconciliation_aborts_rather_than_mass_ghost_cancelling():
    """A transient exchange failure must not be read as "every order vanished".

    ``_reconcile_orders`` carries a guard (BUG-FIX UK-05): when the exchange returns ZERO
    orders while local state holds non-terminal ones, that is far more likely a timeout or a
    429 that answered with an empty list than it is every open order disappearing. Treating
    them as ghosts would emit a ``cancel_order`` action for each and destroy the open-order
    book.

    The guard was untested — the existing reconciliation test passed one empty double as
    both sources, so the "exchange empty, local populated" asymmetry it protects against
    could not arise. This is the single highest-consequence branch in the module, so it gets
    its own test.
    """
    async def _run():
        print("\n=== Test: Reconciliation Ghost-Cancel Guard ===")

        exchange = MockRedis(orders=[])  # the transient failure: empty, not an exception
        local = MockRedis(
            orders=[
                {"order_id": "ORD-1", "status": "open", "quantity": "1", "symbol": "BTC/USDT"},
                {"order_id": "ORD-2", "status": "accepted", "quantity": "2", "symbol": "ETH/USDT"},
            ]
        )

        result = await ReconciliationEngine(
            exchange_client=exchange,
            state_service=local,
            fill_deduplication_manager=local,
        ).reconcile(
            tenant_id="test_tenant", exchange_name="binance", user_id="test_user"
        )

        ghosts = [
            m for m in result.order_mismatches if m.mismatch_type == "ghost_order"
        ]
        assert not ghosts, (
            f"the exchange returned zero orders — almost certainly a transient API failure "
            f"— and reconciliation classified {len(ghosts)} live local order(s) as ghosts: "
            f"{[m.order_id for m in ghosts]}. The next step is a cancel action per ghost, "
            f"i.e. cancelling the entire open-order book on a timeout."
        )
        cancels = [
            a for a in result.reconciliation_actions if a.action_type == "cancel_order"
        ]
        assert not cancels, (
            f"{len(cancels)} cancel_order action(s) were generated from an empty exchange "
            f"response: {[a.target_id for a in cancels]}"
        )

        print("✅ Ghost-cancel guard test PASSED")
        return True
    return asyncio.run(_run())


@pytest.mark.xfail(
    strict=True,
    reason=(
        "backend_app/core/worker_restart_recovery_manager.py is a stub: recover_worker "
        "ignores its redis_client and returns success=True with every restored count at 0. "
        "Worker restart recovery is not implemented. Delete this marker when it is."
    ),
)
def test_worker_restart_recovery():
    """Worker restart recovery must actually restore the state in the journal.

    XFAIL(strict): ``WorkerRestartRecoveryManager`` IS NOT IMPLEMENTED.

    ``backend_app/core/worker_restart_recovery_manager.py`` is a stub in full. It accepts a
    ``redis_client``, never reads it, and returns ``WorkerRestartRecoveryResult(success=True,
    positions_restored=0, orders_restored=0, ...)`` unconditionally. Its own comment says
    the stub exists "for testing compatibility", and the assertion it was written against —
    ``assert result.success is not None`` — is one a hardcoded success cannot fail. The two
    engines it is supposed to orchestrate (``ReplayReconstructionEngine``,
    ``ReconciliationEngine``) are real and are exercised above; only the orchestrator is
    absent.

    This test is kept at full strength and marked ``xfail(strict=True)`` rather than
    weakened, following the pattern ``tests/test_subscriber_restricted_operations.py``
    documents: a known defect must keep failing until the fix lands, and the moment it
    starts passing the strict marker reds the suite as an XPASS — which is the signal to
    DELETE the marker, not to loosen the assertion.

    The companion test below asserts the part that CAN be fixed without implementing the
    feature: that the stub stops reporting a success it did not achieve.
    """
    async def _run():
        print("\n=== Test: Worker Restart Recovery ===")

        events = [
            {
                "sequence": 1,
                "event_type": "ORDER_SUBMITTED",
                "payload": {
                    "order_id": "ORD-R1",
                    "tenant_id": "test_tenant",
                    "strategy_id": "test_strategy",
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "order_type": "limit",
                    "quantity": "0.25",
                    "price": "39000",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                },
            },
            {
                "sequence": 2,
                "event_type": "POSITION_OPENED",
                "payload": {
                    "position_id": "POS-R1",
                    "tenant_id": "test_tenant",
                    "strategy_id": "test_strategy",
                    "symbol": "BTC/USDT",
                    "side": "long",
                    "quantity": "0.25",
                    "entry_price": "39000",
                    "current_price": "39000",
                    "timestamp": "2026-01-01T00:00:01+00:00",
                },
            },
        ]

        mock_redis = MockRedis(events=events)
        recovery_manager = WorkerRestartRecoveryManager(redis_client=mock_redis)
        result = await recovery_manager.recover_worker(
            tenant_id="test_tenant",
            worker_id="worker_001",
            exchange_name="binance",
            user_id="test_user",
            strategy_id=None,
            from_sequence=None
        )

        assert result.success is True, (
            f"worker recovery reported failure: errors={result.errors}"
        )
        assert result.errors == [], f"recovery recorded errors: {result.errors}"
        assert result.orders_restored == 1, (
            f"recovery restored {result.orders_restored} order(s) from a journal holding "
            f"one. A crashed worker that comes back having restored nothing has not "
            f"recovered."
        )
        assert result.positions_restored == 1, (
            f"recovery restored {result.positions_restored} position(s) from a journal "
            f"holding one. An unrestored position is an untracked exposure."
        )

        print("✅ Worker restart recovery test PASSED")
        return True
    return asyncio.run(_run())


def test_worker_restart_recovery_does_not_report_a_success_it_did_not_achieve():
    """An unimplemented recovery must say so, not report success.

    This is the half of the defect that is fixable without building the feature, and it is
    the half that misleads an operator. ``ExecutionWorker.start`` branches on
    ``recovery_result.success`` and, on the true arm, logs::

        Worker <id> restart recovery successful: 0 positions, 0 orders, 0 executions,
        0 fills, 0 mismatches

    and increments ``stats["restart_recovery_count"]``. A restarted worker that recovered
    NOTHING because no recovery exists therefore reports, in its own logs and its own
    counters, that recovery ran and found nothing to do. "Nothing to recover" and "recovery
    is not implemented" are the two readings that must never be the same line, and this is
    the same substitution — an absence reported as an established figure — that this
    codebase refuses everywhere else.

    The caller already handles the false arm: it logs a warning and continues startup
    (fail-safe), which is the correct behaviour for a worker whose recovery is unavailable.
    Nothing in the repository constructs this manager — ``ExecutionWorker`` takes it as an
    optional injection defaulting to ``None`` — so making it honest changes no live path.
    """
    async def _run():
        print("\n=== Test: Worker Restart Recovery Honesty ===")

        result = await WorkerRestartRecoveryManager(
            redis_client=MockRedis()
        ).recover_worker(
            tenant_id="test_tenant",
            worker_id="worker_001",
            exchange_name="binance",
            user_id="test_user",
            strategy_id=None,
            from_sequence=None,
        )

        assert result.success is False, (
            "recover_worker reported success=True while restoring nothing. ExecutionWorker "
            "logs that as 'restart recovery successful: 0 positions, 0 orders, ...' and "
            "counts it in restart_recovery_count, so a worker with no recovery at all looks "
            "like a worker that had nothing to recover."
        )
        assert result.errors, (
            "recover_worker reported failure with no error explaining it, so an operator "
            "reading the warning has nothing to act on"
        )
        assert any(
            "not implemented" in str(e).lower() or "stub" in str(e).lower()
            for e in result.errors
        ), (
            f"the error does not say that recovery is unimplemented, which is the one fact "
            f"an operator needs: {result.errors!r}"
        )

        print("✅ Worker restart recovery honesty test PASSED")
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
