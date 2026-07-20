"""
tests/test_scaling_metrics.py — SCALING METRICS TESTS

STEP 8: Verify scaling metrics work correctly.
"""

import sys
import os
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.scaling_metrics import (
    ScalingMetricsCollector,
    scaling_metrics,
    get_scaling_metrics,
)


def test_scaling_metrics_creation():
    """Test metrics collector creation."""
    metrics = ScalingMetricsCollector()
    assert metrics is not None
    print("[PASS] Scaling metrics collector created")


def test_set_active_users():
    """Test setting active users."""
    metrics = ScalingMetricsCollector()
    metrics.set_active_users(150)
    
    assert metrics.get_active_users() == 150
    print("[PASS] Active users set to 150")


def test_set_ws_connections():
    """Test setting WebSocket connections."""
    metrics = ScalingMetricsCollector()
    metrics.set_ws_connections(145)
    
    assert metrics.get_ws_connections() == 145
    print("[PASS] WS connections set to 145")


def test_set_queue_size():
    """Test setting queue sizes."""
    metrics = ScalingMetricsCollector()
    
    metrics.set_queue_size("dag", 25)
    metrics.set_queue_size("execution", 5)
    metrics.set_queue_size("portfolio", 10)
    
    assert metrics.get_queue_size("dag") == 25
    assert metrics.get_queue_size("execution") == 5
    assert metrics.get_queue_size("portfolio") == 10
    print("[PASS] Queue sizes set correctly")


def test_set_execution_latency():
    """Test setting execution latency."""
    metrics = ScalingMetricsCollector()
    
    metrics.set_execution_latency("dag", 0.25)
    metrics.set_execution_latency("order", 0.45)
    metrics.set_execution_latency("position", 0.85)
    
    assert metrics.get_execution_latency("dag") == 0.25
    assert metrics.get_execution_latency("order") == 0.45
    assert metrics.get_execution_latency("position") == 0.85
    print("[PASS] Execution latencies set correctly")


def test_prometheus_format():
    """Test Prometheus format export."""
    metrics = ScalingMetricsCollector()
    
    metrics.set_active_users(147)
    metrics.set_ws_connections(142)
    metrics.set_queue_size("dag", 23)
    metrics.set_execution_latency("order", 0.45)
    
    prometheus = metrics.get_prometheus_metrics()
    
    assert "active_users_total 147" in prometheus
    assert "ws_server_connections_total 142" in prometheus
    assert 'tasks_queue_size{queue_type="dag"} 23' in prometheus
    assert 'execution_latency_seconds{executor="order"} 0.45' in prometheus
    
    print("[PASS] Prometheus format correct")


def test_snapshot():
    """Test metrics snapshot."""
    metrics = ScalingMetricsCollector()
    
    metrics.set_active_users(150)
    metrics.set_ws_connections(145)
    
    snapshot = metrics.get_snapshot()
    
    assert "timestamp" in snapshot
    assert snapshot["active_users"] == 150
    assert snapshot["ws_connections"] == 145
    assert "queue_sizes" in snapshot
    assert "execution_latencies" in snapshot
    
    print("[PASS] Snapshot generated correctly")


def test_scaling_recommendation():
    """Test scaling recommendations."""
    metrics = ScalingMetricsCollector()
    
    # Set normal values
    metrics.set_active_users(100)
    rec = metrics.get_scaling_recommendation()
    assert rec["needs_scaling"] is False
    
    # Set critical values
    metrics.set_active_users(190)  # Above critical threshold (180)
    metrics.set_queue_size("dag", 120)  # Above critical threshold (100)
    
    rec = metrics.get_scaling_recommendation()
    assert rec["needs_scaling"] is True
    assert len(rec["recommendations"]) >= 2
    
    # Check recommendation details
    metrics_found = [r["metric"] for r in rec["recommendations"]]
    assert "active_users" in metrics_found
    assert "queue_dag" in metrics_found
    
    print("[PASS] Scaling recommendations working")


def test_history():
    """Test metric history."""
    metrics = ScalingMetricsCollector()
    
    # Set multiple values
    for i in range(5):
        metrics.set_active_users(100 + i * 10)
        time.sleep(0.01)  # Small delay
    
    # Get average
    avg = metrics.get_average_over_period("active_users", seconds=1)
    assert avg is not None
    assert 100 <= avg <= 140
    
    print(f"[PASS] History tracking working (avg: {avg:.1f})")


def test_trend():
    """Test trend detection."""
    metrics = ScalingMetricsCollector(history_minutes=1)
    
    # Simulate increasing trend
    for i in range(10):
        metrics.set_active_users(100 + i * 5)
        time.sleep(0.01)
    
    trend = metrics.get_trend("active_users", seconds=1)
    assert trend in ["increasing", "stable"]  # Should be increasing
    
    print(f"[PASS] Trend detection working: {trend}")


def test_global_singleton():
    """Test global singleton instance."""
    m1 = get_scaling_metrics()
    m2 = get_scaling_metrics()
    
    assert m1 is m2
    print("[PASS] Global singleton works")


def run_all_tests():
    """Run all scaling metrics tests."""
    print("=" * 60)
    print("SCALING METRICS TESTS")
    print("=" * 60)
    
    tests = [
        test_scaling_metrics_creation,
        test_set_active_users,
        test_set_ws_connections,
        test_set_queue_size,
        test_set_execution_latency,
        test_prometheus_format,
        test_snapshot,
        test_scaling_recommendation,
        test_history,
        test_trend,
        test_global_singleton,
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
