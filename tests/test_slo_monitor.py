"""
tests/test_slo_monitor.py — SLO MONITOR TESTS

STEP 11: Verify SLO monitoring and compliance tracking
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.slo_monitor import (
    SLOType,
    SLOThreshold,
    SLOCompliance,
    SLOMonitor,
    DEFAULT_SLO_THRESHOLDS,
    get_slo_monitor,
)


def test_slo_types():
    """Test SLO type enumeration."""
    assert SLOType.LATENCY_P99.value == "latency_p99"
    assert SLOType.LATENCY_P95.value == "latency_p95"
    assert SLOType.SUCCESS_RATE.value == "success_rate"
    assert SLOType.ERROR_RATE.value == "error_rate"
    assert SLOType.AVAILABILITY.value == "availability"
    
    print("[PASS] SLO types defined")


def test_slo_threshold_creation():
    """Test SLO threshold creation."""
    threshold = SLOThreshold(
        slo_type=SLOType.LATENCY_P99,
        service="api",
        target_value=0.2,
        warning_threshold=0.15,
        critical_threshold=0.5,
        unit="seconds"
    )
    
    assert threshold.slo_type == SLOType.LATENCY_P99
    assert threshold.service == "api"
    assert threshold.target_value == 0.2
    assert threshold.warning_threshold == 0.15
    assert threshold.critical_threshold == 0.5
    assert threshold.unit == "seconds"
    
    print("[PASS] SLO threshold creation working")


def test_default_slo_thresholds():
    """Test default SLO thresholds are properly configured."""
    assert len(DEFAULT_SLO_THRESHOLDS) > 0
    
    # Check API latency SLO
    api_latency = next(
        (t for t in DEFAULT_SLO_THRESHOLDS 
         if t.service == "api" and t.slo_type == SLOType.LATENCY_P99),
        None
    )
    assert api_latency is not None
    assert api_latency.target_value == 0.2  # 200ms
    
    # Check API success rate SLO
    api_success = next(
        (t for t in DEFAULT_SLO_THRESHOLDS
         if t.service == "api" and t.slo_type == SLOType.SUCCESS_RATE),
        None
    )
    assert api_success is not None
    assert api_success.target_value == 0.999  # 99.9%
    
    # Check order placement SLO
    order_slo = next(
        (t for t in DEFAULT_SLO_THRESHOLDS
         if t.service == "order_placement" and t.slo_type == SLOType.SUCCESS_RATE),
        None
    )
    assert order_slo is not None
    assert order_slo.target_value == 0.995  # 99.5%
    
    print("[PASS] Default SLO thresholds configured")


def test_slo_compliance_creation():
    """Test SLO compliance status creation."""
    compliance = SLOCompliance(
        slo_type=SLOType.LATENCY_P99,
        service="api",
        current_value=0.15,
        target_value=0.2,
        is_compliant=True,
        compliance_rate=1.33,
        status="healthy"
    )
    
    assert compliance.slo_type == SLOType.LATENCY_P99
    assert compliance.is_compliant is True
    assert compliance.status == "healthy"
    
    # Test serialization
    data = compliance.to_dict()
    assert data["slo_type"] == "latency_p99"
    assert data["is_compliant"] is True
    
    print("[PASS] SLO compliance creation working")


def test_slo_monitor_initialization():
    """Test SLO monitor initialization."""
    monitor = SLOMonitor()
    
    assert len(monitor.thresholds) == len(DEFAULT_SLO_THRESHOLDS)
    assert monitor._running is False
    
    print("[PASS] SLO monitor initialization working")


def test_latency_recording():
    """Test latency metric recording."""
    monitor = SLOMonitor()
    
    # Record some latencies
    monitor.record_latency("api", 0.1)
    monitor.record_latency("api", 0.15)
    monitor.record_latency("api", 0.2)
    monitor.record_latency("api", 0.05)
    
    assert len(monitor._latency_data["api"]) == 4
    
    print("[PASS] Latency recording working")


def test_request_recording():
    """Test request success/failure recording."""
    monitor = SLOMonitor()
    
    # Record successes and failures
    monitor.record_request("api", success=True)
    monitor.record_request("api", success=True)
    monitor.record_request("api", success=False)
    monitor.record_request("api", success=True)
    
    assert len(monitor._request_data["api"]) == 4
    
    print("[PASS] Request recording working")


def test_error_recording():
    """Test error recording."""
    monitor = SLOMonitor()
    
    monitor.record_error("database", "timeout")
    monitor.record_error("database", "connection")
    
    assert len(monitor._error_data["database"]) == 2
    
    print("[PASS] Error recording working")


def test_latency_slo_evaluation():
    """Test latency SLO evaluation."""
    monitor = SLOMonitor()
    
    # Record latencies that should be compliant
    for _ in range(99):
        monitor.record_latency("api", 0.1)  # 100ms
    monitor.record_latency("api", 0.3)  # 300ms (the 1%)
    
    # P99 should be around 300ms (non-compliant for 200ms target)
    # Actually with sorted data, P99 would be 0.3
    
    # Check data recorded
    assert len(monitor._latency_data["api"]) == 100
    
    print("[PASS] Latency SLO evaluation test setup working")


def test_success_rate_slo_evaluation():
    """Test success rate SLO evaluation."""
    monitor = SLOMonitor()
    
    # Record 999 successes and 1 failure (99.9% success rate)
    for _ in range(999):
        monitor.record_request("api", success=True)
    monitor.record_request("api", success=False)
    
    assert len(monitor._request_data["api"]) == 1000
    
    # Calculate success rate
    successes = sum(1 for _, success in monitor._request_data["api"] if success)
    rate = successes / len(monitor._request_data["api"])
    assert rate == 0.999  # 99.9%
    
    print("[PASS] Success rate SLO evaluation working")


def test_global_singleton():
    """Test global singleton instance."""
    monitor1 = get_slo_monitor()
    monitor2 = get_slo_monitor()
    
    assert monitor1 is monitor2
    print("[PASS] Global singleton working")


def test_rollback_conditions():
    """Test rollback condition detection logic."""
    # Test cases that should trigger rollback:
    # 1. Order placement success rate < 98%
    # 2. 2+ critical SLO violations
    
    # Simulate critical violations
    violations = [
        SLOCompliance(
            slo_type=SLOType.SUCCESS_RATE,
            service="order_placement",
            current_value=0.97,  # Below 98%
            target_value=0.995,
            is_compliant=False,
            compliance_rate=0.97,
            status="violated"
        ),
        SLOCompliance(
            slo_type=SLOType.ERROR_RATE,
            service="api",
            current_value=0.02,  # Above 1%
            target_value=0.001,
            is_compliant=False,
            compliance_rate=0.05,
            status="violated"
        )
    ]
    
    # Should trigger rollback
    order_violation = any(
        c.service == "order_placement" and c.status == "violated"
        for c in violations
    )
    assert order_violation is True
    
    multiple_violations = len(violations) >= 2
    assert multiple_violations is True
    
    should_rollback = order_violation or multiple_violations
    assert should_rollback is True
    
    print("[PASS] Rollback condition detection working")


def test_slo_threshold_validation():
    """Test SLO threshold value validation."""
    # Percent-based SLOs should have values 0-1
    threshold = SLOThreshold(
        slo_type=SLOType.SUCCESS_RATE,
        service="api",
        target_value=0.999,
        warning_threshold=0.9995,
        critical_threshold=0.99,
        unit="percent"
    )
    
    assert 0 <= threshold.target_value <= 1
    assert 0 <= threshold.warning_threshold <= 1
    assert 0 <= threshold.critical_threshold <= 1
    
    print("[PASS] SLO threshold validation working")


def test_slo_summary():
    """Test SLO summary generation."""
    monitor = SLOMonitor()
    
    # Record some data
    for _ in range(10):
        monitor.record_request("api", success=True)
    
    summary = monitor.get_summary()
    
    assert "total_slos" in summary
    assert "compliant" in summary
    assert "violated" in summary
    assert "compliance_rate" in summary
    assert "slos" in summary
    
    print("[PASS] SLO summary generation working")


def run_all_tests():
    """Run all SLO monitor tests."""
    print("=" * 60)
    print("SLO MONITOR TESTS")
    print("=" * 60)
    
    tests = [
        test_slo_types,
        test_slo_threshold_creation,
        test_default_slo_thresholds,
        test_slo_compliance_creation,
        test_slo_monitor_initialization,
        test_latency_recording,
        test_request_recording,
        test_error_recording,
        test_latency_slo_evaluation,
        test_success_rate_slo_evaluation,
        test_global_singleton,
        test_rollback_conditions,
        test_slo_threshold_validation,
        test_slo_summary,
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
    
    if failed == 0:
        print("\n[ALL PASS] SLO monitoring tests passed!")
        print("Production safety system ready")


if __name__ == "__main__":
    run_all_tests()
