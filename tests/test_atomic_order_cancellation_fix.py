"""
FIN-CRITICAL-005 Regression Test: Atomic Order Cancellation Fix

Tests that order cancellation uses atomic UPDATE with WHERE clause including status check
to prevent race conditions where concurrent cancellation attempts could both proceed.

This test verifies the fix for the critical order cancellation race condition issue.

EVERY TEST IN THIS FILE SKIPS unless ``DATABASE_URL`` names a PostgreSQL server.

The SQL here is PostgreSQL throughout -- ``NOW()``, ``data->>'status'`` on a ``JSON``
column, and ``IS DISTINCT FROM`` -- and the module inserts into ``orders`` without
creating it, relying on a schema the ``database-tests`` job provisions. On the SQLite
fallback used by the ``unit-tests`` job in ``.github/workflows/01-pr-check.yml`` all four
tests failed with ``no such table: orders``. An atomic ``UPDATE`` predicate is the
property under test and SQLite cannot express it, so there is nothing to port.

This file is already named in the ``database-tests`` job, which is where it runs. Note
that the job ``--deselect``-s ``test_transaction_isolation_for_cancellation`` for a
documented design reason, so with this guard in place that one test runs in neither job;
``tests/test_sqlite_lane_is_greenable.py`` asserts that gap explicitly rather than
leaving it to be discovered.
"""

import os
import uuid

import pytest
import threading
import time
from sqlalchemy import text
from sqlalchemy.orm import Session
from backend_app.core.database import SessionLocal
from backend_app.backend.transactional_execution_manager import ReplaySafeTransaction

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith(("postgresql", "postgres://")),
    reason=(
        "needs a real PostgreSQL through DATABASE_URL; provided by the database-tests "
        "job in .github/workflows/01-pr-check.yml, skipped in the SQLite lane"
    ),
)

# ─── WHY THE TENANT ID IS NOW A REAL UUID WITH A REAL PROFILE BEHIND IT ──────
#
# These tests used the literal string 'test_tenant'. That worked only because
# `orders.tenant_id` was `varchar(36)` with no foreign key, so any string went in. Both
# halves of that were the defect BUG-033 describes: `tenant_id` is supposed to reference
# `profiles.id`, which is `uuid`, and PostgreSQL has no equality operator between uuid
# and varchar -- so `fk_orders_tenant_id` could not be created and nothing stopped a row
# from naming a tenant that did not exist.
#
# Alembic revision `a1b2c3d4e5f6` retypes the column and creates the constraint, after
# which 'test_tenant' is refused twice over:
#
#     psycopg2.errors.InvalidTextRepresentation:
#         invalid input syntax for type uuid: TEST_TENANT_ID
#
# So the fixture below seeds an actual tenant. Nothing about what these tests ASSERT has
# changed -- the atomic UPDATE predicate, the concurrency race and the idempotency check
# are all untouched. What changed is that the rows they operate on are now valid rows,
# which is what the schema always meant to require.
TEST_TENANT_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


@pytest.fixture(autouse=True)
def _seeded_tenant():
    """A real `profiles` row for TEST_TENANT_ID, removed afterwards.

    Autouse because every test in this file inserts into `orders`, and
    `fk_orders_tenant_id` now requires the parent to exist. `auth.users` is seeded first
    for the same reason one level up -- `profiles.id` references it.

    ON CONFLICT DO NOTHING so a leftover row from an interrupted run is tolerated, and
    the delete at the end is ordered child-first so the cascade is not relied upon.
    """
    session = SessionLocal()
    try:
        session.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:i, :e) "
                 "ON CONFLICT (id) DO NOTHING"),
            {"i": TEST_TENANT_ID, "e": "atomic-cancellation-test@example.test"},
        )
        session.execute(
            text("INSERT INTO public.profiles (id) VALUES (:i) "
                 "ON CONFLICT (id) DO NOTHING"),
            {"i": TEST_TENANT_ID},
        )
        session.commit()
    finally:
        session.close()

    yield TEST_TENANT_ID

    session = SessionLocal()
    try:
        session.execute(
            text("DELETE FROM orders WHERE tenant_id = :i"), {"i": TEST_TENANT_ID}
        )
        session.execute(
            text("DELETE FROM public.profiles WHERE id = :i"), {"i": TEST_TENANT_ID}
        )
        session.execute(
            text("DELETE FROM auth.users WHERE id = :i"), {"i": TEST_TENANT_ID}
        )
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


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
                "tenant_id": TEST_TENANT_ID,
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
                "tenant_id": TEST_TENANT_ID
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
                "tenant_id": TEST_TENANT_ID
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
                "tenant_id": TEST_TENANT_ID,
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
                        "tenant_id": TEST_TENANT_ID
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
                "tenant_id": TEST_TENANT_ID,
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
                "tenant_id": TEST_TENANT_ID
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
                "tenant_id": TEST_TENANT_ID
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
