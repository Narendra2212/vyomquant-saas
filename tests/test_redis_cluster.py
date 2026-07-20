"""
tests/test_redis_cluster.py — REDIS CLUSTER TESTS

STEP 7: Verify Redis Cluster configuration and separate databases
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.redis_cluster import (
    RedisClusterConfig,
    RedisDatabase,
    RedisClusterManager,
    DATABASE_TTL,
    get_redis_cluster_manager,
)


def test_redis_databases():
    """Test Redis database enumeration."""
    assert RedisDatabase.CACHE.value == 0
    assert RedisDatabase.QUEUE.value == 1
    assert RedisDatabase.EVENTS.value == 2
    assert RedisDatabase.IDEMPOTENCY.value == 3
    assert RedisDatabase.METRICS.value == 4
    assert RedisDatabase.STATE.value == 5
    
    print("[PASS] Redis databases defined")


def test_database_ttls():
    """Test database TTL configuration."""
    assert DATABASE_TTL[RedisDatabase.CACHE] == 3600  # 1 hour
    assert DATABASE_TTL[RedisDatabase.QUEUE] == 86400  # 24 hours
    assert DATABASE_TTL[RedisDatabase.EVENTS] == 604800  # 7 days
    assert DATABASE_TTL[RedisDatabase.IDEMPOTENCY] == 86400  # 24 hours
    assert DATABASE_TTL[RedisDatabase.METRICS] == 2592000  # 30 days
    assert DATABASE_TTL[RedisDatabase.STATE] == 0  # No TTL
    
    print("[PASS] Database TTLs configured")


def test_cluster_config():
    """Test cluster configuration."""
    config = RedisClusterConfig(
        startup_nodes=[
            {"host": "node1", "port": 6379},
            {"host": "node2", "port": 6379},
            {"host": "node3", "port": 6379},
        ],
        password="secret",
        max_connections=100,
        socket_timeout=5.0,
    )
    
    assert len(config.startup_nodes) == 3
    assert config.startup_nodes[0]["host"] == "node1"
    assert config.password == "secret"
    assert config.max_connections == 100
    assert config.socket_timeout == 5.0
    
    print("[PASS] Cluster config working")


def test_config_defaults():
    """Test configuration defaults."""
    config = RedisClusterConfig()
    
    assert len(config.startup_nodes) == 3
    assert config.password is None
    assert config.max_connections == 100
    assert config.socket_timeout == 5.0
    assert config.socket_connect_timeout == 5.0
    assert config.retry_on_timeout is True
    assert config.decode_responses is True
    
    print("[PASS] Config defaults correct")


def test_key_prefixing():
    """Test key prefixing for database separation."""
    from unittest.mock import MagicMock
    
    manager = RedisClusterManager()
    
    # Test key prefixing
    key1 = manager._get_key_with_db("mykey", RedisDatabase.CACHE)
    assert key1 == "db0:mykey"
    
    key2 = manager._get_key_with_db("mykey", RedisDatabase.QUEUE)
    assert key2 == "db1:mykey"
    
    key3 = manager._get_key_with_db("mykey", RedisDatabase.IDEMPOTENCY)
    assert key3 == "db3:mykey"
    
    print("[PASS] Key prefixing working")


def test_manager_initialization():
    """Test manager initialization."""
    config = RedisClusterConfig()
    manager = RedisClusterManager(config)
    
    assert manager.config == config
    assert manager._cluster is None
    assert manager._healthy is False
    assert manager._stats["connections_created"] == 0
    
    print("[PASS] Manager initialization working")


def test_stats_structure():
    """Test stats structure."""
    manager = RedisClusterManager()
    stats = manager.get_stats()
    
    assert "connections_created" in stats
    assert "connections_failed" in stats
    assert "operations_successful" in stats
    assert "operations_failed" in stats
    assert "healthy" in stats
    assert "nodes_configured" in stats
    
    print("[PASS] Stats structure correct")


def test_database_separation():
    """Test that different databases are properly separated."""
    # Different databases should have different numeric values
    dbs = [db.value for db in RedisDatabase]
    assert len(dbs) == len(set(dbs)), "Database values should be unique"
    
    # Should be 0-5
    assert set(dbs) == {0, 1, 2, 3, 4, 5}
    
    print("[PASS] Database separation working")


def test_ttl_consistency():
    """Test TTL consistency across databases."""
    # Cache should have shortest TTL
    assert DATABASE_TTL[RedisDatabase.CACHE] < DATABASE_TTL[RedisDatabase.QUEUE]
    assert DATABASE_TTL[RedisDatabase.CACHE] < DATABASE_TTL[RedisDatabase.EVENTS]
    
    # Events should have longer TTL than queue
    assert DATABASE_TTL[RedisDatabase.EVENTS] > DATABASE_TTL[RedisDatabase.QUEUE]
    
    # Idempotency should have same TTL as queue
    assert DATABASE_TTL[RedisDatabase.IDEMPOTENCY] == DATABASE_TTL[RedisDatabase.QUEUE]
    
    # State should have no TTL
    assert DATABASE_TTL[RedisDatabase.STATE] == 0
    
    print("[PASS] TTL consistency correct")


def test_concern_separation():
    """Test that different concerns are mapped to correct databases."""
    # Cache: short-lived data
    assert RedisDatabase.CACHE.value == 0
    
    # Queue: task queues
    assert RedisDatabase.QUEUE.value == 1
    
    # Events: event streaming
    assert RedisDatabase.EVENTS.value == 2
    
    # Idempotency: deduplication
    assert RedisDatabase.IDEMPOTENCY.value == 3
    
    # Metrics: counters/gauges
    assert RedisDatabase.METRICS.value == 4
    
    # State: persistent
    assert RedisDatabase.STATE.value == 5
    
    print("[PASS] Concern separation correct")


def test_retry_configuration():
    """Test retry configuration."""
    config = RedisClusterConfig()
    
    assert config.retry_on_timeout is True
    assert len(config.retry_on_error) > 0
    assert ConnectionError in config.retry_on_error
    
    print("[PASS] Retry configuration correct")


def run_all_tests():
    """Run all Redis cluster tests."""
    print("=" * 60)
    print("REDIS CLUSTER TESTS")
    print("=" * 60)
    
    tests = [
        test_redis_databases,
        test_database_ttls,
        test_cluster_config,
        test_config_defaults,
        test_key_prefixing,
        test_manager_initialization,
        test_stats_structure,
        test_database_separation,
        test_ttl_consistency,
        test_concern_separation,
        test_retry_configuration,
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
