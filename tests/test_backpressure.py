"""
tests/test_backpressure.py — BACKPRESSURE SYSTEM TESTS

STEP 9: Verify backpressure system works correctly.
"""

import sys
import os
import time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend_app.core.backpressure import (
    BackpressureController,
    BackpressureConfig,
    CircuitBreaker,
    Priority,
    LoadLevel,
    backpressure,
    get_backpressure,
    BackpressureRejected,
)


def test_backpressure_creation():
    """Test backpressure controller creation."""
    config = BackpressureConfig()
    bp = BackpressureController(config)
    
    assert bp is not None
    assert bp.config.warning_threshold == 50
    print("[PASS] Backpressure controller created")


def test_load_levels():
    """Test load level determination."""
    bp = BackpressureController()
    
    # Test different queue sizes
    assert bp._get_load_level(30) == LoadLevel.NORMAL
    assert bp._get_load_level(75) == LoadLevel.WARNING
    assert bp._get_load_level(150) == LoadLevel.CRITICAL
    assert bp._get_load_level(250) == LoadLevel.EMERGENCY
    
    print("[PASS] Load levels correct")


def test_can_accept_normal():
    """Test acceptance in normal load."""
    bp = BackpressureController()
    bp.update_queue_size("dag", 30)  # NORMAL
    
    # All priorities should be accepted in NORMAL
    assert bp.can_accept(Priority.CRITICAL) is True
    assert bp.can_accept(Priority.HIGH) is True
    assert bp.can_accept(Priority.NORMAL) is True
    assert bp.can_accept(Priority.LOW) is True
    assert bp.can_accept(Priority.BACKGROUND) is True
    
    print("[PASS] Normal load accepts all priorities")


def test_can_accept_warning():
    """Test acceptance in warning load."""
    import random
    random.seed(42)
    bp = BackpressureController()
    bp.update_queue_size("dag", 75)  # WARNING
    
    # High priorities accepted
    assert bp.can_accept(Priority.CRITICAL) is True
    assert bp.can_accept(Priority.HIGH) is True
    
    # Low and background probabilistically rejected
    # We can't assert exact result, but should mostly reject
    low_accepted = sum(bp.can_accept(Priority.LOW) for _ in range(10))
    bg_accepted = sum(bp.can_accept(Priority.BACKGROUND) for _ in range(10))
    
    # Warning level: LOW 50%, BACKGROUND 20%
    assert low_accepted <= 8  # Should be around 5
    assert bg_accepted <= 4  # Should be around 2
    
    print(f"[PASS] Warning load: LOW accepted {low_accepted}/10, BG accepted {bg_accepted}/10")


def test_can_accept_critical():
    """Test acceptance in critical load."""
    bp = BackpressureController()
    bp.update_queue_size("dag", 150)  # CRITICAL
    
    # Critical and high mostly accepted
    assert bp.can_accept(Priority.CRITICAL) is True
    
    # Low and background rejected
    assert bp.can_accept(Priority.LOW) is False
    assert bp.can_accept(Priority.BACKGROUND) is False
    
    print("[PASS] Critical load rejects low priorities")


def test_can_accept_emergency():
    """Test acceptance in emergency load."""
    import random
    random.seed(42)
    bp = BackpressureController()
    bp.update_queue_size("dag", 250)  # EMERGENCY
    
    # Critical always accepted
    assert bp.can_accept(Priority.CRITICAL) is True
    
    # HIGH is 50% in emergency (probabilistic)
    high_accepted = sum(bp.can_accept(Priority.HIGH) for _ in range(10))
    assert high_accepted <= 7  # Should be around 5, allow some variance
    
    # NORMAL, LOW, BACKGROUND rejected
    assert bp.can_accept(Priority.NORMAL) is False
    assert bp.can_accept(Priority.LOW) is False
    assert bp.can_accept(Priority.BACKGROUND) is False
    
    print(f"[PASS] Emergency load: HIGH accepted {high_accepted}/10, others rejected")


def test_queue_size_tracking():
    """Test queue size tracking."""
    bp = BackpressureController()
    
    bp.update_queue_size("dag", 50)
    bp.update_queue_size("execution", 30)
    bp.update_queue_size("portfolio", 20)
    
    sizes = bp.get_queue_sizes()
    assert sizes["dag"] == 50
    assert sizes["execution"] == 30
    assert sizes["portfolio"] == 20
    
    print("[PASS] Queue sizes tracked correctly")


def test_load_level_transition():
    """Test load level transitions."""
    bp = BackpressureController()
    
    # Start normal
    bp.update_queue_size("dag", 30)
    assert bp.get_current_level() == LoadLevel.NORMAL
    
    # Move to warning
    bp.update_queue_size("dag", 75)
    assert bp.get_current_level() == LoadLevel.WARNING
    
    # Move to critical
    bp.update_queue_size("dag", 150)
    assert bp.get_current_level() == LoadLevel.CRITICAL
    
    # Back to warning
    bp.update_queue_size("dag", 80)
    assert bp.get_current_level() == LoadLevel.WARNING
    
    print("[PASS] Load level transitions work")


def test_priority_mapping():
    """Test task type to priority mapping."""
    bp = BackpressureController()
    
    assert bp.get_priority_for_task("emergency_liquidate") == Priority.CRITICAL
    assert bp.get_priority_for_task("order_execution") == Priority.HIGH
    assert bp.get_priority_for_task("dag_task") == Priority.NORMAL
    assert bp.get_priority_for_task("portfolio_snapshot") == Priority.LOW
    assert bp.get_priority_for_task("log_cleanup") == Priority.BACKGROUND
    
    print("[PASS] Priority mapping correct")


def test_stats():
    """Test statistics collection."""
    bp = BackpressureController()
    
    bp.update_queue_size("dag", 75)
    
    # Simulate some acceptances and rejections
    for _ in range(10):
        if bp.can_accept(Priority.NORMAL):
            bp._record_acceptance()
        else:
            bp.record_rejection(Priority.NORMAL, "test")
    
    stats = bp.get_stats()
    assert "total_accepted" in stats
    assert "total_rejected" in stats
    assert "rejection_by_priority" in stats
    assert "queue_sizes" in stats
    assert "current_level" in stats
    
    print(f"[PASS] Stats collected: {stats['total_accepted']} accepted, {stats['total_rejected']} rejected")


async def test_admission_control():
    """Test admission control context manager."""
    import asyncio
    bp = BackpressureController()
    bp.update_queue_size("dag", 30)  # NORMAL
    
    # Should succeed in normal load
    async with bp.admission_control(Priority.NORMAL):
        pass  # Would do work here
    
    # Switch to emergency
    bp.update_queue_size("dag", 250)
    
    # Should raise in emergency
    try:
        async with bp.admission_control(Priority.NORMAL):
            pass
        assert False, "Should have raised BackpressureRejected"
    except BackpressureRejected:
        pass
    
    print("[PASS] Admission control works")


async def test_circuit_breaker():
    """Test circuit breaker pattern."""
    import asyncio
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.1)
    
    # Start closed
    assert cb.get_state() == "CLOSED"
    assert await cb.can_execute() is True
    
    # Record successes
    await cb.record_success()
    assert cb.get_state() == "CLOSED"
    
    # Record failures to open
    for _ in range(3):
        await cb.record_failure()
    
    assert cb.get_state() == "OPEN"
    assert await cb.can_execute() is False  # Should reject
    
    # Wait for recovery timeout
    await asyncio.sleep(0.15)
    
    # Should be half-open now
    assert await cb.can_execute() is True
    
    # Record success to close
    await cb.record_success()
    assert cb.get_state() == "CLOSED"
    
    print("[PASS] Circuit breaker works")


def test_should_shed_load():
    """Test load shedding detection."""
    bp = BackpressureController()
    
    # Normal - no shedding
    bp.update_queue_size("dag", 30)
    assert bp.should_shed_load() is False
    
    # Warning - no shedding
    bp.update_queue_size("dag", 75)
    assert bp.should_shed_load() is False
    
    # Critical - shed load
    bp.update_queue_size("dag", 150)
    assert bp.should_shed_load() is True
    
    # Emergency - shed load
    bp.update_queue_size("dag", 250)
    assert bp.should_shed_load() is True
    
    print("[PASS] Load shedding detection correct")


def test_global_singleton():
    """Test global singleton instance."""
    bp1 = get_backpressure()
    bp2 = get_backpressure()
    
    assert bp1 is bp2
    print("[PASS] Global singleton works")


def run_all_tests():
    """Run all backpressure tests."""
    print("=" * 60)
    print("BACKPRESSURE SYSTEM TESTS")
    print("=" * 60)
    
    tests = [
        test_backpressure_creation,
        test_load_levels,
        test_can_accept_normal,
        test_can_accept_warning,
        test_can_accept_critical,
        test_can_accept_emergency,
        test_queue_size_tracking,
        test_load_level_transition,
        test_priority_mapping,
        test_stats,
        test_should_shed_load,
        test_global_singleton,
    ]
    
    async_tests = [
        test_admission_control,
        test_circuit_breaker,
    ]
    
    passed = 0
    failed = 0
    
    # Run sync tests
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
    
    # Run async tests
    import asyncio
    for test in async_tests:
        try:
            print(f"\n--- {test.__name__} ---")
            asyncio.run(test())
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
