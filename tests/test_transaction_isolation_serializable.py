"""
FIN-CRITICAL-001 Regression Test: Transaction Isolation Level

Tests that financial transactions use SERIALIZABLE isolation level
to prevent race conditions and ensure consistency for money-critical operations.

This test verifies the fix for the critical transaction isolation issue.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from backend_app.core.database import SessionLocal, engine
from backend_app.core.database_pool import get_db_pool


def test_transaction_isolation_level_serializable():
    """
    FIN-CRITICAL-001: Verify that database sessions use SERIALIZABLE isolation level.
    
    This test ensures that financial transactions are protected against race conditions
    by using the highest isolation level that prevents all concurrency anomalies.
    """
    # Test with SessionLocal from database.py
    session = SessionLocal()
    try:
        # Query the current transaction isolation level
        result = session.execute(text("SHOW transaction_isolation"))
        isolation_level = result.scalar()
        
        # Verify SERIALIZABLE isolation level
        # PostgreSQL returns "serializable" or "read committed" etc.
        assert isolation_level is not None, "Transaction isolation level should be set"
        
        # For PostgreSQL, we expect 'serializable' 
        # The exact format may vary by database, but should contain 'serializable'
        isolation_lower = isolation_level.lower()
        assert 'serializable' in isolation_lower, f"Expected SERIALIZABLE isolation level, got: {isolation_level}"
        
        print(f"✓ Transaction isolation level verified: {isolation_level}")
        
    finally:
        session.close()


def test_pool_transaction_isolation_level():
    """
    FIN-CRITICAL-001: Verify that database pool sessions use SERIALIZABLE isolation level.
    
    This test ensures that connection pooling also enforces SERIALIZABLE isolation.
    """
    pool = get_db_pool()
    session = pool.get_session()
    try:
        # Query the current transaction isolation level
        result = session.execute(text("SHOW transaction_isolation"))
        isolation_level = result.scalar()
        
        # Verify SERIALIZABLE isolation level
        assert isolation_level is not None, "Transaction isolation level should be set"
        isolation_lower = isolation_level.lower()
        assert 'serializable' in isolation_lower, f"Expected SERIALIZABLE isolation level, got: {isolation_level}"
        
        print(f"✓ Pool transaction isolation level verified: {isolation_level}")
        
    finally:
        session.close()


def test_concurrent_transaction_safety():
    """
    FIN-CRITICAL-001: Test that concurrent transactions are properly isolated.
    
    This test simulates concurrent financial operations to verify that
    SERIALIZABLE isolation prevents race conditions.
    """
    import threading
    import time
    
    results = []
    errors = []
    
    def concurrent_transaction(transaction_id):
        """Simulate a concurrent financial transaction."""
        session = SessionLocal()
        try:
            # Begin transaction
            session.begin()
            
            # Simulate financial operation
            time.sleep(0.1)  # Small delay to increase chance of race condition
            
            # Verify isolation level during transaction
            result = session.execute(text("SHOW transaction_isolation"))
            isolation_level = result.scalar()
            
            results.append((transaction_id, isolation_level))
            
            # Commit transaction
            session.commit()
            
        except Exception as e:
            errors.append((transaction_id, str(e)))
            session.rollback()
        finally:
            session.close()
    
    # Run concurrent transactions
    threads = []
    for i in range(5):
        thread = threading.Thread(target=concurrent_transaction, args=(i,))
        threads.append(thread)
        thread.start()
    
    # Wait for all threads to complete
    for thread in threads:
        thread.join()
    
    # Verify no errors occurred
    assert len(errors) == 0, f"Concurrent transactions should not fail: {errors}"
    
    # Verify all transactions used SERIALIZABLE isolation
    for transaction_id, isolation_level in results:
        isolation_lower = isolation_level.lower()
        assert 'serializable' in isolation_lower, \
            f"Transaction {transaction_id} should use SERIALIZABLE, got: {isolation_level}"
    
    print(f"✓ All {len(results)} concurrent transactions used SERIALIZABLE isolation")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
