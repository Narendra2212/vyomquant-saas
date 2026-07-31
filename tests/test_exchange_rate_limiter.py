"""
tests/test_exchange_rate_limiter.py — EXCHANGE RATE LIMITER TESTS

STEP 6: Verify exchange rate limiting works correctly.
"""

import asyncio
import time

# Adjust path for imports
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend_app.core.exchange_rate_limiter import (
    ExchangeRateLimiter,
    ExchangeType,
    TokenBucket,
    exchange_limiter,
    exchange_limited,
    retry_handler,
)


class TestTokenBucket:
    """Test token bucket algorithm."""
    
    async def test_token_bucket_acquire(self):
        """Test basic token acquisition."""
        bucket = TokenBucket(rate=10, capacity=10)
        
        # First 10 should be immediate
        for i in range(10):
            result = await bucket.acquire()
            assert result is True, f"Token {i+1} should be acquired"
        
        print("[PASS] Token bucket: 10 tokens acquired immediately")
    
    async def test_token_bucket_wait(self):
        """Test waiting for token refill."""
        bucket = TokenBucket(rate=10, capacity=1)
        
        # Acquire only token
        await bucket.acquire()
        
        # Next should wait
        start = time.time()
        await bucket.acquire()
        elapsed = time.time() - start
        
        # Should wait ~0.1s for 1 token at 10/sec
        assert elapsed >= 0.08, f"Should wait for refill, got {elapsed:.3f}s"
        
        print(f"[PASS] Token bucket: Waited {elapsed:.3f}s for refill")
    
    async def test_token_bucket_wait_time(self):
        """Test wait time calculation."""
        bucket = TokenBucket(rate=10, capacity=1)
        
        # Empty bucket
        await bucket.acquire()
        
        # Check wait time
        wait_time = bucket.get_wait_time()
        assert wait_time > 0, "Wait time should be positive for empty bucket"
        assert wait_time <= 0.15, f"Wait time should be ~0.1s, got {wait_time:.3f}s"
        
        print(f"[PASS] Token bucket: Wait time {wait_time:.3f}s")


class TestExchangeRateLimiter:
    """Test exchange rate limiter."""
    
    async def test_acquire_context_manager(self):
        """Test acquire context manager."""
        limiter = ExchangeRateLimiter()
        
        async with limiter.acquire("binance"):
            print("[PASS] Acquire context: Binance request allowed")
    
    async def test_decorator(self):
        """Test exchange_limited decorator."""
        
        @exchange_limited("binance")
        async def mock_fetch():
            return {"price": 50000}
        
        result = await mock_fetch()
        assert result["price"] == 50000
        
        print("[PASS] Decorator: Rate limited function executed")
    
    async def test_exchange_limits(self):
        """Test different exchange limits."""
        
        # Test all supported exchanges
        exchanges = [
            ("binance", 100),
            ("coinbase", 10),
            ("kraken", 1),
            ("bybit", 50),
        ]
        
        for exchange, expected_rate in exchanges:
            bucket = exchange_limiter._get_bucket(exchange)
            assert bucket.rate == expected_rate, f"{exchange} should have {expected_rate} req/sec"
        
        print("[PASS] Exchange limits: All exchanges configured correctly")
    
    async def test_stats(self):
        """Test stats tracking."""
        limiter = ExchangeRateLimiter()
        
        # Make some requests
        for i in range(5):
            async with limiter.acquire("binance"):
                pass
        
        stats = limiter.get_stats("binance")
        assert stats["requests_total"] >= 5, "Should track total requests"
        
        print(f"[PASS] Stats: Tracked {stats['requests_total']} requests")
    
    async def test_reset(self):
        """Test reset functionality."""
        limiter = ExchangeRateLimiter()
        
        # Make requests
        async with limiter.acquire("binance"):
            pass
        
        # Reset stats
        await limiter.reset("binance")
        
        stats = limiter.get_stats("binance")
        assert stats["requests_total"] == 0, "Should reset stats"
        
        print("[PASS] Reset: Stats cleared successfully")


class TestRetryHandler:
    """Test retry handler."""
    
    async def test_retry_success(self):
        """Test successful execution without retry."""
        
        async def success_func():
            return "success"
        
        result = await retry_handler.execute_with_retry("binance", success_func)
        assert result == "success"
        
        print("[PASS] Retry handler: Success on first attempt")


async def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("EXCHANGE RATE LIMITER TESTS")
    print("=" * 60)
    
    # Token bucket tests
    print("\n--- Token Bucket Tests ---")
    bucket_tests = TestTokenBucket()
    await bucket_tests.test_token_bucket_acquire()
    await bucket_tests.test_token_bucket_wait()
    await bucket_tests.test_token_bucket_wait_time()
    
    # Exchange limiter tests
    print("\n--- Exchange Rate Limiter Tests ---")
    limiter_tests = TestExchangeRateLimiter()
    await limiter_tests.test_acquire_context_manager()
    await limiter_tests.test_decorator()
    await limiter_tests.test_exchange_limits()
    await limiter_tests.test_stats()
    await limiter_tests.test_reset()
    
    # Retry handler tests
    print("\n--- Retry Handler Tests ---")
    retry_tests = TestRetryHandler()
    await retry_tests.test_retry_success()
    
    print("\n" + "=" * 60)
    print("[SUCCESS] ALL TESTS PASSED")
    print("=" * 60)
    
    # Print stats
    print("\nExchange Stats:")
    stats = exchange_limiter.get_stats()
    for exchange, data in stats.items():
        if data["requests_total"] > 0:
            print(f"  {exchange}: {data['requests_total']} requests")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
