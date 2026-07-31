"""
tests/test_exchange_rate_limit_engine.py — EXCHANGE RATE LIMIT ENGINE TESTS

STEP 9: Verify token bucket, priority queue, and batching
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend_app.core.exchange_rate_limit_engine import (
    RequestPriority,
    TokenBucketConfig,
    TokenBucket,
    PendingRequest,
    RequestBatcher,
    ExchangeRateConfig,
    ExchangeRateLimitEngine,
    EXCHANGE_CONFIGS,
)


def test_request_priorities():
    """Test request priority enumeration."""
    assert RequestPriority.CRITICAL.value == 0
    assert RequestPriority.HIGH.value == 1
    assert RequestPriority.MEDIUM.value == 2
    assert RequestPriority.LOW.value == 3
    
    # Critical is highest priority (lowest number)
    assert RequestPriority.CRITICAL.value < RequestPriority.HIGH.value
    assert RequestPriority.HIGH.value < RequestPriority.MEDIUM.value
    
    print("[PASS] Request priorities defined")


def test_token_bucket_config():
    """Test token bucket configuration."""
    config = TokenBucketConfig(
        requests_per_second=10.0,
        requests_per_minute=600.0,
        burst_size=20
    )
    
    assert config.requests_per_second == 10.0
    assert config.requests_per_minute == 600.0
    assert config.burst_size == 20
    
    print("[PASS] Token bucket config working")


def test_token_bucket_initialization():
    """Test token bucket initialization."""
    config = TokenBucketConfig(
        requests_per_second=10.0,
        burst_size=20
    )
    
    bucket = TokenBucket(config)
    
    assert bucket.config == config
    assert bucket._tokens == 20  # Initial burst
    assert bucket._max_tokens == 20
    
    print("[PASS] Token bucket initialization working")


def test_exchange_configs():
    """Test pre-configured exchange limits."""
    # Check all expected exchanges exist
    expected_exchanges = ["binance", "coinbase", "kraken", "okx", "bybit"]
    
    for exchange in expected_exchanges:
        assert exchange in EXCHANGE_CONFIGS, f"Missing config for {exchange}"
        config = EXCHANGE_CONFIGS[exchange]
        assert isinstance(config, ExchangeRateConfig)
        assert config.name == exchange
        assert config.requests_per_second > 0
        assert config.requests_per_minute > 0
    
    print("[PASS] Exchange configs defined")


def test_exchange_rate_config():
    """Test exchange rate configuration."""
    config = ExchangeRateConfig(
        name="test_exchange",
        requests_per_second=5.0,
        requests_per_minute=300.0,
        burst_size=10,
        adaptive_throttling=True,
        throttle_reduction=0.8,
        cooldown_seconds=60.0,
        enable_batching=True,
        batch_window_ms=100.0,
        max_queue_size=1000,
        queue_timeout_seconds=30.0
    )
    
    assert config.name == "test_exchange"
    assert config.requests_per_second == 5.0
    assert config.requests_per_minute == 300.0
    assert config.burst_size == 10
    assert config.adaptive_throttling is True
    assert config.throttle_reduction == 0.8
    assert config.cooldown_seconds == 60.0
    assert config.enable_batching is True
    assert config.batch_window_ms == 100.0
    assert config.max_queue_size == 1000
    assert config.queue_timeout_seconds == 30.0
    
    print("[PASS] Exchange rate config working")


def test_pending_request():
    """Test pending request data model."""
    from datetime import datetime
    from unittest.mock import MagicMock
    
    future = MagicMock()
    
    request = PendingRequest(
        request_id="req_123",
        exchange="binance",
        method="fetch_balance",
        params={"user_id": "user_456"},
        priority=RequestPriority.MEDIUM,
        timestamp=datetime.utcnow(),
        future=future,
        batchable=True,
        batch_key="binance:fetch_balance"
    )
    
    assert request.request_id == "req_123"
    assert request.exchange == "binance"
    assert request.method == "fetch_balance"
    assert request.priority == RequestPriority.MEDIUM
    assert request.batchable is True
    assert request.batch_key == "binance:fetch_balance"
    
    print("[PASS] Pending request model working")


def test_request_batcher():
    """Test request batcher."""
    batcher = RequestBatcher(batch_window_ms=100.0)
    
    assert batcher.batch_window_ms == 100.0
    assert len(batcher._pending_batches) == 0
    
    print("[PASS] Request batcher initialization working")


def test_batch_key_generation():
    """Test batch key generation."""
    from datetime import datetime
    from unittest.mock import MagicMock
    
    batcher = RequestBatcher()
    
    # Batchable request
    request1 = PendingRequest(
        request_id="req_1",
        exchange="binance",
        method="fetch_balance",
        params={},
        priority=RequestPriority.LOW,
        timestamp=datetime.utcnow(),
        future=MagicMock(),
        batchable=True
    )
    
    key1 = batcher._get_batch_key(request1)
    assert key1 == "binance:fetch_balance"
    
    # Non-batchable request
    request2 = PendingRequest(
        request_id="req_2",
        exchange="binance",
        method="create_order",  # Not in batchable list
        params={},
        priority=RequestPriority.HIGH,
        timestamp=datetime.utcnow(),
        future=MagicMock(),
        batchable=False
    )
    
    key2 = batcher._get_batch_key(request2)
    assert key2 == ""  # Empty for non-batchable
    
    print("[PASS] Batch key generation working")


def test_engine_initialization():
    """Test rate limit engine initialization."""
    engine = ExchangeRateLimitEngine()
    
    assert len(engine._configs) == 5  # 5 pre-configured exchanges
    assert len(engine._buckets) == 0  # Not started yet
    assert len(engine._queues) == 0  # Not started yet
    assert engine._running is False
    
    print("[PASS] Engine initialization working")


def test_exchange_specific_limits():
    """Test that each exchange has appropriate limits."""
    # Binance should have highest limits
    binance = EXCHANGE_CONFIGS["binance"]
    assert binance.requests_per_second == 10.0
    assert binance.requests_per_minute == 1200.0
    assert binance.burst_size == 20
    
    # Coinbase has lower limits
    coinbase = EXCHANGE_CONFIGS["coinbase"]
    assert coinbase.requests_per_second == 5.0
    assert coinbase.requests_per_minute == 300.0
    assert coinbase.burst_size == 10
    
    # Kraken has lowest per-second rate
    kraken = EXCHANGE_CONFIGS["kraken"]
    assert kraken.requests_per_second == 3.0
    
    print("[PASS] Exchange-specific limits correct")


def test_adaptive_throttling_config():
    """Test adaptive throttling configuration."""
    for exchange, config in EXCHANGE_CONFIGS.items():
        assert config.adaptive_throttling is True, f"{exchange} should have adaptive throttling"
        assert 0 < config.throttle_reduction < 1, f"{exchange} should have valid throttle reduction"
        assert config.cooldown_seconds > 0, f"{exchange} should have cooldown period"
    
    print("[PASS] Adaptive throttling config correct")


def test_batching_config():
    """Test batching configuration."""
    for exchange, config in EXCHANGE_CONFIGS.items():
        assert config.enable_batching is True, f"{exchange} should have batching enabled"
        assert config.batch_window_ms > 0, f"{exchange} should have batch window"
    
    print("[PASS] Batching config correct")


def test_queue_limits():
    """Test queue size limits."""
    for exchange, config in EXCHANGE_CONFIGS.items():
        assert config.max_queue_size > 0, f"{exchange} should have max queue size"
        assert config.queue_timeout_seconds > 0, f"{exchange} should have queue timeout"
    
    print("[PASS] Queue limits configured")


def test_priority_ordering():
    """Test that priorities order correctly."""
    priorities = [
        RequestPriority.LOW,
        RequestPriority.MEDIUM,
        RequestPriority.HIGH,
        RequestPriority.CRITICAL,
    ]
    
    # Sort by value (lower = higher priority)
    sorted_priorities = sorted(priorities, key=lambda p: p.value)
    
    assert sorted_priorities[0] == RequestPriority.CRITICAL
    assert sorted_priorities[1] == RequestPriority.HIGH
    assert sorted_priorities[2] == RequestPriority.MEDIUM
    assert sorted_priorities[3] == RequestPriority.LOW
    
    print("[PASS] Priority ordering correct")


def run_all_tests():
    """Run all exchange rate limit engine tests."""
    print("=" * 60)
    print("EXCHANGE RATE LIMIT ENGINE TESTS")
    print("=" * 60)
    
    tests = [
        test_request_priorities,
        test_token_bucket_config,
        test_token_bucket_initialization,
        test_exchange_configs,
        test_exchange_rate_config,
        test_pending_request,
        test_request_batcher,
        test_batch_key_generation,
        test_engine_initialization,
        test_exchange_specific_limits,
        test_adaptive_throttling_config,
        test_batching_config,
        test_queue_limits,
        test_priority_ordering,
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
