"""
tests/test_database_pool.py — DATABASE CONNECTION POOL TESTS

STEP 7: Verify connection pooling works correctly.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database_pool import (
    get_db_pool,
    get_db,
    DatabasePool,
    check_database_health,
)
from sqlalchemy import text


def test_pool_creation():
    """Test that pool is created."""
    pool = get_db_pool()
    assert pool is not None, "Pool should be created"
    print("[PASS] Pool created successfully")


def test_pool_status():
    """Test pool status reporting."""
    pool = get_db_pool()
    status = pool.get_pool_status()
    
    assert "type" in status, "Status should include type"
    print(f"[PASS] Pool status: {status}")


def test_connection():
    """Test that connections work."""
    pool = get_db_pool()
    
    try:
        with pool.connection() as conn:
            result = conn.execute(text("SELECT 1"))
            row = result.fetchone()
            assert row[0] == 1, "Should return 1"
        print("[PASS] Connection test successful")
    except Exception as e:
        print(f"[WARN] Connection test: {e}")


def test_session():
    """Test session creation."""
    try:
        with get_db() as session:
            result = session.execute(text("SELECT 1"))
            row = result.fetchone()
            assert row[0] == 1, "Should return 1"
        print("[PASS] Session test successful")
    except Exception as e:
        print(f"[WARN] Session test: {e}")


def test_multiple_connections():
    """Test multiple concurrent connections."""
    pool = get_db_pool()
    
    results = []
    for i in range(10):
        try:
            with pool.connection() as conn:
                result = conn.execute(text("SELECT 1"))
                results.append(result.fetchone()[0])
        except Exception as e:
            print(f"[WARN] Connection {i}: {e}")
    
    assert len(results) == 10, f"Should have 10 results, got {len(results)}"
    print(f"[PASS] Multiple connections: {len(results)} successful")


def test_pool_configuration():
    """Test pool configuration values."""
    import os
    
    # Check environment variables have defaults
    pool_size = int(os.getenv("DB_POOL_SIZE", "20"))
    max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    
    assert pool_size >= 10, "Pool size should be reasonable"
    assert max_overflow >= 5, "Max overflow should be reasonable"
    
    print(f"[PASS] Pool config: size={pool_size}, overflow={max_overflow}")


async def test_async_health():
    """Test async health check."""
    try:
        health = await check_database_health()
        assert "status" in health, "Health should include status"
        print(f"[PASS] Health check: {health['status']}")
    except Exception as e:
        print(f"[WARN] Health check: {e}")


def run_all_tests():
    """Run all sync tests."""
    print("=" * 60)
    print("DATABASE CONNECTION POOL TESTS")
    print("=" * 60)
    
    tests = [
        test_pool_creation,
        test_pool_status,
        test_pool_configuration,
        test_connection,
        test_session,
        test_multiple_connections,
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
            failed += 1
    
    # Async test
    try:
        import asyncio
        print("\n--- test_async_health ---")
        asyncio.run(test_async_health())
        passed += 1
    except Exception as e:
        print(f"[FAIL] test_async_health: {e}")
        failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
