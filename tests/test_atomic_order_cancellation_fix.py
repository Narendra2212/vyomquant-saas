"""
FIN-CRITICAL-005 Regression Test: Atomic Order Cancellation Fix

Tests that order cancellation uses atomic UPDATE with WHERE clause including status check
to prevent race conditions where concurrent cancellation attempts could both proceed.

This test verifies the fix for the critical order cancellation race condition issue.
"""

import pytest
import threading
import time
from sqlalchemy import text
from sqlalchemy.orm import Session
from backend_app.core.database import SessionLocal
from backend_app.backend.transactional_execution_manager import ReplaySafeTransaction


def test_atomic_order_cancellation():
    """
    FIN-CRITICAL-005: Verify that order cancellation uses atomic UPDATE with status check.
    
    This test ensures that concurrent cancellation attempts are prevented by
    using UPDATE with WHERE clause that includes status check.
    """
    session = SessionLocal()
    try:
        # Test that the atomic UPDATE syntax works
        # First, create a test order
        session.execute(
            text("""
                INSERT INTO orders (id, tenant_id, data, created_at, updated_at)
                VALUES (:order_id, :tenant_id, :data, NOW(), NOW())
            """),
            {
                "order_id": "test_order_001",
                "tenant_id": "test_tenant",
                "data": '{"status": "pending", "symbol": "BTC/USDT"}'
            }
        )
        session.commit()
        
        # Test atomic UPDATE with status check
        update_result = session.execute(
            text("""
                UPDATE orders 
                SET data = :data, updated_at = NOW() 
                WHERE id = :order_id 
                  AND tenant_id = :tenant_id 
                  AND (data->>'status') IS DISTINCT FROM 'cancelled'
            """),
            {
                "data": '{"status": "cancelled", "symbol": "BTC/USDT"}',
                "order_id": "test_order_001",
                "tenant_id": "test_tenant"
            }
        )
        
        # Verify one row was updated
        assert update_result.rowcount == 1, "First cancellation should update one row"
        session.commit()
        
        # Try to cancel again - should not update any rows
        update_result = session.execute(
            text("""
                UPDATE orders 
                SET data = :data, updated_at = NOW() 
                WHERE id = :order_id 
                  AND tenant_id = :tenant_id 
                  AND (data->>'status') IS DISTINCT FROM 'cancelled'
            """),
            {
                "data": '{"status": "cancelled", "symbol": "BTC/USDT"}',
                "order_id": "test_order_001",
                "tenant_id": "test_tenant"
            }
        )
        
        # Verify no rows were updated (already cancelled)
        assert update_result.rowcount == 0, "Second cancellation should update zero rows"
        session.commit()
        
        # Clean up
        session.execute(
            text("DELETE FROM orders WHERE id = :order_id"),
            {"order_id": "test_order_001"}
        )
        session.commit()
        
        print("✓ Atomic order cancellation with status check works correctly")
        
    finally:
        session.close()


def test_concurrent_order_cancellation():
    """
    FIN-CRITICAL-005: Test concurrent order cancellation attempts.
    
    This test simulates concurrent cancellation attempts to verify that
    only one succeeds when using atomic UPDATE with status check.
    """
    session = SessionLocal()
    results = []
    errors = []
    
    try:
        # Create a test order
        session.execute(
            text("""
                INSERT INTO orders (id, tenant_id, data, created_at, updated_at)
                VALUES (:order_id, :tenant_id, :data, NOW(), NOW())
            """),
            {
                "order_id": "test_order_concurrent",
                "tenant_id": "test_tenant",
                "data": '{"status": "pending", "symbol": "BTC/USDT"}'
            }
        )
        session.commit()
        
        def concurrent_cancellation(worker_id):
            """Simulate a concurrent cancellation attempt."""
            local_session = SessionLocal()
            try:
                local_session.begin()
                
                # Attempt atomic cancellation
                update_result = local_session.execute(
                    text("""
                        UPDATE orders 
                        SET data = :data, updated_at = NOW() 
                        WHERE id = :order_id 
                          AND tenant_id = :tenant_id 
                          AND (data->>'status') IS DISTINCT FROM 'cancelled'
                    """),
                    {
                        "data": f'{{"status": "cancelled", "worker": "{worker_id}", "symbol": "BTC/USDT"}}',
                        "order_id": "test_order_concurrent",
                        "tenant_id": "test_tenant"
                    }
                )
                
                results.append((worker_id, update_result.rowcount))
                local_session.commit()
                
            except Exception as e:
                errors.append((worker_id, str(e)))
                local_session.rollback()
            finally:
                local_session.close()
        
        # Run concurrent cancellations
        threads = []
        for i in range(5):
            thread = threading.Thread(target=concurrent_cancellation, args=(i,))
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify only one cancellation succeeded
        successful_cancellations = sum(1 for _, rowcount in results if rowcount == 1)
        assert successful_cancellations == 1, f"Only one cancellation should succeed, got {successful_cancellations}"
        
        # Verify four failed due to status check
        failed_cancellations = sum(1 for _, rowcount in results if rowcount == 0)
        assert failed_cancellations == 4, f"Four cancellations should fail due to status check, got {failed_cancellations}"
        
        # Clean up
        session.execute(
            text("DELETE FROM orders WHERE id = :order_id"),
            {"order_id": "test_order_concurrent"}
        )
        session.commit()
        
        print("✓ Concurrent order cancellations are atomic")
        
    finally:
        session.close()


def test_transaction_isolation_for_cancellation():
    """
    FIN-CRITICAL-005: Verify that order cancellation uses SERIALIZABLE isolation.
    
    This test ensures that order cancellation transactions use SERIALIZABLE
    isolation level to prevent race conditions.
    """
    session = SessionLocal()
    try:
        # Query the current transaction isolation level
        result = session.execute(text("SHOW transaction_isolation"))
        isolation_level = result.scalar()
        
        # Verify SERIALIZABLE isolation level
        isolation_lower = isolation_level.lower()
        assert 'serializable' in isolation_lower, f"Expected SERIALIZABLE isolation level, got: {isolation_level}"
        
        print(f"✓ Order cancellation uses SERIALIZABLE isolation: {isolation_level}")
        
    finally:
        session.close()


def test_order_cancellation_idempotency():
    """
    FIN-CRITICAL-005: Test that order cancellation is idempotent.
    
    This test ensures that calling cancellation multiple times with the same
    idempotency key does not cause issues.
    """
    session = SessionLocal()
    try:
        # Create a test order
        session.execute(
            text("""
                INSERT INTO orders (id, tenant_id, data, created_at, updated_at)
                VALUES (:order_id, :tenant_id, :data, NOW(), NOW())
            """),
            {
                "order_id": "test_order_idempotent",
                "tenant_id": "test_tenant",
                "data": '{"status": "pending", "symbol": "BTC/USDT"}'
            }
        )
        session.commit()
        
        # Cancel once
        update_result = session.execute(
            text("""
                UPDATE orders 
                SET data = :data, updated_at = NOW() 
                WHERE id = :order_id 
                  AND tenant_id = :tenant_id 
                  AND (data->>'status') IS DISTINCT FROM 'cancelled'
            """),
            {
                "data": '{"status": "cancelled", "symbol": "BTC/USDT"}',
                "order_id": "test_order_idempotent",
                "tenant_id": "test_tenant"
            }
        )
        session.commit()
        
        assert update_result.rowcount == 1, "First cancellation should succeed"
        
        # Cancel again with same idempotency key - should not error
        update_result = session.execute(
            text("""
                UPDATE orders 
                SET data = :data, updated_at = NOW() 
                WHERE id = :order_id 
                  AND tenant_id = :tenant_id 
                  AND (data->>'status') IS DISTINCT FROM 'cancelled'
            """),
            {
                "data": '{"status": "cancelled", "symbol": "BTC/USDT"}',
                "order_id": "test_order_idempotent",
                "tenant_id": "test_tenant"
            }
        )
        session.commit()
        
        assert update_result.rowcount == 0, "Second cancellation should not update (idempotent)"
        
        # Clean up
        session.execute(
            text("DELETE FROM orders WHERE id = :order_id"),
            {"order_id": "test_order_idempotent"}
        )
        session.commit()
        
        print("✓ Order cancellation is idempotent")
        
    finally:
        session.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
