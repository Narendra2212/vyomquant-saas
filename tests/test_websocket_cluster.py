"""
tests/test_websocket_cluster.py — WEBSOCKET CLUSTER TESTS

STEP 8: Verify WebSocket clustering and connection sharding
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend_app.backend.websocket_cluster import (
    ConnectionState,
    ConnectionInfo,
    ShardAssignment,
    ConnectionSharder,
    WebSocketServerInstance,
    WebSocketClusterManager,
)


def test_connection_states():
    """Test connection state enumeration."""
    assert ConnectionState.CONNECTING.value == "connecting"
    assert ConnectionState.CONNECTED.value == "connected"
    assert ConnectionState.DISCONNECTING.value == "disconnecting"
    assert ConnectionState.DISCONNECTED.value == "disconnected"
    
    print("[PASS] Connection states defined")


def test_connection_info():
    """Test connection info data model."""
    from datetime import datetime
    
    conn = ConnectionInfo(
        connection_id="conn_123",
        user_id="user_456",
        state=ConnectionState.CONNECTED,
        client_info={"browser": "chrome"}
    )
    
    assert conn.connection_id == "conn_123"
    assert conn.user_id == "user_456"
    assert conn.state == ConnectionState.CONNECTED
    assert conn.client_info["browser"] == "chrome"
    
    # Test is_alive
    assert conn.is_alive(timeout_seconds=60) is True
    
    print("[PASS] Connection info model working")


def test_shard_assignment():
    """Test shard assignment data model."""
    from datetime import datetime
    
    assignment = ShardAssignment(
        user_id="user_123",
        shard_id=2,
        instance_id="ws-server-3"
    )
    
    assert assignment.user_id == "user_123"
    assert assignment.shard_id == 2
    assert assignment.instance_id == "ws-server-3"
    
    print("[PASS] Shard assignment model working")


def test_connection_sharder():
    """Test connection sharding logic."""
    sharder = ConnectionSharder(num_shards=4)
    
    assert sharder.num_shards == 4
    
    # Test deterministic sharding
    user_id = "user_alice"
    shard_id_1 = sharder.get_shard_for_user(user_id)
    shard_id_2 = sharder.get_shard_for_user(user_id)
    
    assert shard_id_1 == shard_id_2, "Sharding must be deterministic"
    assert 0 <= shard_id_1 < 4
    
    # Test instance ID
    instance_id = sharder.get_instance_id(shard_id_1)
    assert instance_id == f"ws-server-{shard_id_1 + 1}"
    
    # Test user assignment
    assignment = sharder.assign_user("user_bob")
    assert assignment.user_id == "user_bob"
    assert 0 <= assignment.shard_id < 4
    
    # Test retrieval
    retrieved = sharder.get_assignment("user_bob")
    assert retrieved.user_id == "user_bob"
    
    print("[PASS] Connection sharder working")


def test_shard_distribution():
    """Test that sharding distributes users evenly."""
    sharder = ConnectionSharder(num_shards=4)
    
    # Test with many users
    shard_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    
    for i in range(1000):
        user_id = f"user_{i}"
        shard_id = sharder.get_shard_for_user(user_id)
        shard_counts[shard_id] += 1
    
    # Each shard should have roughly 250 users (±20%)
    for shard_id, count in shard_counts.items():
        assert 200 <= count <= 300, f"Shard {shard_id} has {count} users, expected ~250"
    
    print(f"[PASS] Shard distribution even: {shard_counts}")


def test_shard_consistency():
    """Test that same user always gets same shard."""
    sharder = ConnectionSharder(num_shards=4)
    
    test_users = ["alice", "bob", "charlie", "david", "eve"]
    
    for user in test_users:
        shard_ids = [sharder.get_shard_for_user(user) for _ in range(10)]
        assert len(set(shard_ids)) == 1, f"User {user} got different shards: {shard_ids}"
    
    print("[PASS] Shard consistency maintained")


def test_server_instance_initialization():
    """Test WebSocket server instance initialization."""
    instance = WebSocketServerInstance(
        instance_id="ws-server-1",
        shard_id=0,
        redis_url="redis://localhost:6379",
        max_connections=750,
        heartbeat_interval=30.0
    )
    
    assert instance.instance_id == "ws-server-1"
    assert instance.shard_id == 0
    assert instance.max_connections == 750
    assert instance.heartbeat_interval == 30.0
    assert len(instance._connections) == 0
    
    print("[PASS] Server instance initialization working")


def test_cluster_manager_initialization():
    """Test cluster manager initialization."""
    cluster = WebSocketClusterManager(
        num_shards=4,
        redis_url="redis://localhost:6379",
        max_connections_per_shard=750
    )
    
    assert cluster.num_shards == 4
    assert cluster.max_connections_per_shard == 750
    assert len(cluster._instances) == 0  # Not started yet
    
    print("[PASS] Cluster manager initialization working")


def test_cluster_capacity_calculation():
    """Test cluster capacity calculations."""
    # 4 shards × 750 = 3000 total capacity
    cluster = WebSocketClusterManager(
        num_shards=4,
        max_connections_per_shard=750
    )
    
    total_capacity = cluster.num_shards * cluster.max_connections_per_shard
    assert total_capacity == 3000
    
    # 6 shards × 750 = 4500
    cluster6 = WebSocketClusterManager(
        num_shards=6,
        max_connections_per_shard=750
    )
    assert cluster6.num_shards * cluster6.max_connections_per_shard == 4500
    
    print("[PASS] Cluster capacity calculation correct")


def test_sharder_with_special_characters():
    """Test sharding with various user ID formats."""
    sharder = ConnectionSharder(num_shards=4)
    
    test_users = [
        "user_123",
        "tenant_abc:user_456",
        "email@example.com",
        "uuid-1234-5678-9012",
        "very_long_user_id_with_many_characters_12345",
        "short",
        "12345",
    ]
    
    for user_id in test_users:
        shard_id = sharder.get_shard_for_user(user_id)
        assert 0 <= shard_id < 4, f"User {user_id} got invalid shard {shard_id}"
        
        # Should be consistent
        shard_id_2 = sharder.get_shard_for_user(user_id)
        assert shard_id == shard_id_2
    
    print("[PASS] Sharding works with various user ID formats")


def test_instance_stats_structure():
    """Test instance stats structure."""
    instance = WebSocketServerInstance(
        instance_id="ws-server-1",
        shard_id=0
    )
    
    stats = instance.get_stats()
    
    assert "instance_id" in stats
    assert "shard_id" in stats
    assert "total_connections" in stats
    assert "unique_users" in stats
    assert "max_connections" in stats
    assert "utilization" in stats
    
    assert stats["instance_id"] == "ws-server-1"
    assert stats["shard_id"] == 0
    
    print("[PASS] Instance stats structure correct")


def test_cluster_stats_structure():
    """Test cluster stats structure."""
    cluster = WebSocketClusterManager(num_shards=4)
    
    # Can't get full stats without starting, but structure should exist
    assert hasattr(cluster, 'get_cluster_stats')
    
    print("[PASS] Cluster stats structure exists")


def run_all_tests():
    """Run all WebSocket cluster tests."""
    print("=" * 60)
    print("WEBSOCKET CLUSTER TESTS")
    print("=" * 60)
    
    tests = [
        test_connection_states,
        test_connection_info,
        test_shard_assignment,
        test_connection_sharder,
        test_shard_distribution,
        test_shard_consistency,
        test_server_instance_initialization,
        test_cluster_manager_initialization,
        test_cluster_capacity_calculation,
        test_sharder_with_special_characters,
        test_instance_stats_structure,
        test_cluster_stats_structure,
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
