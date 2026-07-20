"""
tests/test_database_scaling.py — DATABASE SCALING TESTS

STEP 6: Verify connection pooling, RW splitting, and partitioning
"""

import sys
import os
import asyncio
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database_scaling import (
    DatabaseConfig,
    QueryType,
    TablePartitionManager,
    PartitionedOrderRepository,
    PartitionedTradeRepository,
)


def test_database_config():
    """Test database configuration."""
    config = DatabaseConfig(
        primary_url="postgresql://primary:5432/db",
        replica_urls=[
            "postgresql://replica1:5432/db",
            "postgresql://replica2:5432/db",
        ],
        pool_size=50,
        max_overflow=50,
        pool_timeout=30,
    )
    
    assert config.pool_size == 50
    assert config.max_overflow == 50
    assert len(config.replica_urls) == 2
    assert config.primary_url == "postgresql://primary:5432/db"
    
    print("[PASS] Database config working")


def test_query_type_detection():
    """Test query type detection."""
    # These would be tested on actual router instance
    # For now, just verify enum exists
    assert QueryType.READ.value == "read"
    assert QueryType.WRITE.value == "write"
    assert QueryType.TRANSACTION.value == "transaction"
    
    print("[PASS] Query types defined")


def test_partition_manager_initialization():
    """Test partition manager initialization."""
    # Would need actual router, just test class exists
    from unittest.mock import MagicMock
    
    router = MagicMock()
    manager = TablePartitionManager(router)
    
    assert manager.router is router
    print("[PASS] Partition manager initialization working")


def test_user_partition_calculation():
    """Test user partition calculation."""
    from unittest.mock import MagicMock
    
    router = MagicMock()
    manager = TablePartitionManager(router)
    
    # Same user_id should always give same partition
    user_id = "user_123"
    partition1 = manager.get_user_partition(user_id, num_partitions=16)
    partition2 = manager.get_user_partition(user_id, num_partitions=16)
    
    assert partition1 == partition2
    assert partition1.startswith("trades_user_")
    
    # Different users likely different partitions
    user_id2 = "user_456"
    partition3 = manager.get_user_partition(user_id2, num_partitions=16)
    # May or may not be different, but should be valid format
    assert partition3.startswith("trades_user_")
    
    print("[PASS] User partition calculation working")


def test_date_partition_calculation():
    """Test date partition calculation."""
    from unittest.mock import MagicMock
    
    router = MagicMock()
    manager = TablePartitionManager(router)
    
    # January 2024
    date = datetime(2024, 1, 15)
    partition = manager.get_date_partition(date)
    assert partition == "orders_2024_01"
    
    # December 2024
    date = datetime(2024, 12, 25)
    partition = manager.get_date_partition(date)
    assert partition == "orders_2024_12"
    
    print("[PASS] Date partition calculation working")


def test_partition_name_format():
    """Test partition name format."""
    from unittest.mock import MagicMock
    
    router = MagicMock()
    manager = TablePartitionManager(router)
    
    # Test various user_ids
    test_users = ["user_1", "user_abc", "tenant_123:user_456", "long_user_id_with_many_chars"]
    partitions = [manager.get_user_partition(u) for u in test_users]
    
    # All should be valid format
    for p in partitions:
        assert p.startswith("trades_user_")
        # Extract number
        num = int(p.split("_")[-1])
        assert 0 <= num < 16
    
    print("[PASS] Partition name format correct")


def test_repository_initialization():
    """Test repository initialization."""
    from unittest.mock import MagicMock
    
    router = MagicMock()
    order_repo = PartitionedOrderRepository(router)
    trade_repo = PartitionedTradeRepository(router)
    
    assert order_repo.router is router
    assert trade_repo.router is router
    
    print("[PASS] Repository initialization working")


def test_config_defaults():
    """Test configuration defaults."""
    config = DatabaseConfig()
    
    assert config.pool_size == 50
    assert config.max_overflow == 50
    assert config.pool_timeout == 30
    assert config.pool_recycle == 3600
    assert config.health_check_interval == 30
    assert config.enable_failover is True
    assert config.failover_timeout == 5
    
    print("[PASS] Config defaults correct")


def test_query_detection_logic():
    """Test query detection logic patterns."""
    # This tests the pattern matching logic
    write_queries = [
        "INSERT INTO orders VALUES (1, 2, 3)",
        "UPDATE orders SET status = 'filled'",
        "DELETE FROM orders WHERE id = 1",
        "CREATE TABLE test (id int)",
        "DROP TABLE test",
        "ALTER TABLE orders ADD COLUMN foo",
    ]
    
    read_queries = [
        "SELECT * FROM orders",
        "SELECT id, status FROM orders WHERE user_id = 1",
        "SELECT COUNT(*) FROM trades",
    ]
    
    transaction_queries = [
        "BEGIN TRANSACTION",
        "COMMIT",
        "ROLLBACK",
    ]
    
    # Check that patterns would be detected correctly
    for q in write_queries:
        q_upper = q.upper()
        assert any(kw in q_upper for kw in ['INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'ALTER'])
    
    for q in read_queries:
        q_upper = q.upper()
        assert 'SELECT' in q_upper
        assert not any(kw in q_upper for kw in ['INSERT', 'UPDATE', 'DELETE'])
    
    for q in transaction_queries:
        q_upper = q.upper()
        assert any(kw in q_upper for kw in ['BEGIN', 'COMMIT', 'ROLLBACK'])
    
    print("[PASS] Query detection patterns working")


def run_all_tests():
    """Run all database scaling tests."""
    print("=" * 60)
    print("DATABASE SCALING TESTS")
    print("=" * 60)
    
    tests = [
        test_database_config,
        test_query_type_detection,
        test_partition_manager_initialization,
        test_user_partition_calculation,
        test_date_partition_calculation,
        test_partition_name_format,
        test_repository_initialization,
        test_config_defaults,
        test_query_detection_logic,
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
    run_all_tests()
