"""
tests/test_rate_limiter.py — RATE LIMITER TESTS

STEP 5: Verify rate limiting works correctly.
"""

import asyncio
import pytest
from datetime import datetime

from core.rate_limiter import RateLimiter, RateLimitExceeded


@pytest.fixture(autouse=True)
async def reset_redis_singletons():
    """Ensure a completely fresh Redis connection pool and manager for each test."""
    from core.cache import redis_manager
    import backend.redis_manager
    redis_manager._redis_manager = None
    backend.redis_manager._redis_manager = None
    backend.redis_manager.RedisManager._instance = None
    yield
    try:
        await redis_manager.disconnect()
    except Exception:
        pass
    redis_manager._redis_manager = None
    backend.redis_manager._redis_manager = None
    backend.redis_manager.RedisManager._instance = None


@pytest.fixture
async def rate_limiter():
    """Create a fresh rate limiter for each test, clearing all rate limit keys."""
    limiter = RateLimiter()
    # Clean up any leftover keys from previous runs
    try:
        keys = await limiter.redis.keys("rate_limit:user:*")
        for k in keys:
            await limiter.redis.delete(k)
    except Exception:
        pass
    yield limiter
    # Cleanup after test
    try:
        keys = await limiter.redis.keys("rate_limit:user:*")
        for k in keys:
            await limiter.redis.delete(k)
    except Exception:
        pass


class TestTradeRateLimits:
    """Test trade per second/minute limits."""
    
    async def test_trades_per_second_limit(self, rate_limiter):
        """Test that 6th trade in 1 second is rejected."""
        user_id = "test-user-second"
        
        # First 5 trades should succeed
        for i in range(5):
            result = await rate_limiter.check_trade_allowed(user_id)
            assert result is True, f"Trade {i+1} should be allowed"
        
        # 6th trade should fail
        with pytest.raises(RateLimitExceeded) as exc_info:
            await rate_limiter.check_trade_allowed(user_id)
        
        assert exc_info.value.limit_type == "trades_per_second"
        assert exc_info.value.current == 5
        assert exc_info.value.max_allowed == 5
        
        # Cleanup
        await rate_limiter.reset_limits(user_id)
    
    async def test_trades_per_minute_limit(self, rate_limiter):
        """Test that 101st trade in 1 minute is rejected."""
        user_id = "test-user-minute"
        
        # Manually add 100 trades to minute window using unique member names
        import time
        now = time.time()
        for i in range(100):
            val = now - (i * 0.5)
            await rate_limiter.redis.zadd(
                f"rate_limit:user:{user_id}:trades:minute",
                {f"{val}:{i}": val}
            )
        
        # 101st trade should fail
        with pytest.raises(RateLimitExceeded) as exc_info:
            await rate_limiter.check_trade_allowed(user_id)
        
        assert exc_info.value.limit_type == "trades_per_minute"
        
        # Cleanup
        await rate_limiter.reset_limits(user_id)
    
    async def test_sliding_window_resets(self, rate_limiter):
        """Test that rate limit resets after window expires."""
        user_id = "test-user-window"
        
        # Use up all trades
        for i in range(5):
            await rate_limiter.check_trade_allowed(user_id)
        
        # Should fail now
        with pytest.raises(RateLimitExceeded):
            await rate_limiter.check_trade_allowed(user_id)
        
        # Wait for window to clear (simulate by resetting)
        await rate_limiter.reset_limits(user_id)
        
        # Should work again
        result = await rate_limiter.check_trade_allowed(user_id)
        assert result is True
        
        # Cleanup
        await rate_limiter.reset_limits(user_id)


class TestPositionLimits:
    """Test open position limits."""
    
    async def test_position_limit(self, rate_limiter):
        """Test max 20 open positions."""
        user_id = "test-user-positions"
        
        # Add 20 positions
        for i in range(20):
            await rate_limiter.increment_position_count(user_id)
        
        # Check count
        count = await rate_limiter.get_open_position_count(user_id)
        assert count == 20
        
        # Should not be able to open more
        can_open = await rate_limiter.can_open_position(user_id)
        assert can_open is False
        
        # Should raise when checking limit
        with pytest.raises(RateLimitExceeded) as exc_info:
            await rate_limiter.check_position_limit(user_id)
        
        assert exc_info.value.limit_type == "open_positions"
        
        # Cleanup
        await rate_limiter.reset_limits(user_id)
    
    async def test_position_decrement(self, rate_limiter):
        """Test decrementing position count."""
        user_id = "test-user-decrement"
        
        # Add 5 positions
        for i in range(5):
            await rate_limiter.increment_position_count(user_id)
        
        # Remove 2
        for i in range(2):
            await rate_limiter.decrement_position_count(user_id)
        
        # Should have 3
        count = await rate_limiter.get_open_position_count(user_id)
        assert count == 3
        
        # Cleanup
        await rate_limiter.reset_limits(user_id)
    
    async def test_position_count_never_negative(self, rate_limiter):
        """Test that position count can't go negative."""
        user_id = "test-user-negative"
        
        # Try to decrement when at 0
        await rate_limiter.decrement_position_count(user_id)
        
        # Should still be 0
        count = await rate_limiter.get_open_position_count(user_id)
        assert count == 0
        
        # Cleanup
        await rate_limiter.reset_limits(user_id)


class TestRateLimitStatus:
    """Test status reporting."""
    
    async def test_get_status(self, rate_limiter):
        """Test getting full rate limit status."""
        user_id = "test-user-status"
        
        # Add some trades
        for i in range(3):
            await rate_limiter.check_trade_allowed(user_id)
        
        # Add some positions
        for i in range(5):
            await rate_limiter.increment_position_count(user_id)
        
        # Get status
        status = await rate_limiter.get_status(user_id)
        
        assert status.user_id == user_id
        assert status.trades_per_second == 3
        assert status.trades_per_second_limit == 5
        assert status.open_positions == 5
        assert status.max_positions == 20
        assert status.allowed is True
        assert len(status.violations) == 0
        
        # Cleanup
        await rate_limiter.reset_limits(user_id)
    
    async def test_status_with_violations(self, rate_limiter):
        """Test status when limits are exceeded."""
        user_id = "test-user-violations"
        
        # Use up all limits
        for i in range(5):
            await rate_limiter.check_trade_allowed(user_id)
        
        for i in range(20):
            await rate_limiter.increment_position_count(user_id)
        
        # Get status
        status = await rate_limiter.get_status(user_id)
        
        assert status.allowed is False
        assert len(status.violations) >= 1
        assert any("trades_per_second" in v for v in status.violations)
        assert any("open_positions" in v for v in status.violations)
        
        # Cleanup
        await rate_limiter.reset_limits(user_id)


class TestReset:
    """Test limit reset functionality."""
    
    async def test_reset_limits(self, rate_limiter):
        """Test admin reset of limits."""
        user_id = "test-user-reset"
        
        # Use up limits
        for i in range(5):
            await rate_limiter.check_trade_allowed(user_id)
        
        for i in range(20):
            await rate_limiter.increment_position_count(user_id)
        
        # Verify limits hit
        with pytest.raises(RateLimitExceeded):
            await rate_limiter.check_trade_allowed(user_id)
        
        can_open = await rate_limiter.can_open_position(user_id)
        assert can_open is False
        
        # Reset
        await rate_limiter.reset_limits(user_id)
        
        # Should work now
        result = await rate_limiter.check_trade_allowed(user_id)
        assert result is True
        
        can_open = await rate_limiter.can_open_position(user_id)
        assert can_open is True
        
        # Cleanup
        await rate_limiter.reset_limits(user_id)


if __name__ == "__main__":
    # Run basic test
    async def basic_test():
        limiter = RateLimiter()
        user_id = "manual-test-user"
        
        print("Testing rate limiter...")
        
        # Test 1: 5 trades allowed
        print("\n1. Testing trades/second limit:")
        for i in range(6):
            try:
                await limiter.check_trade_allowed(user_id)
                print(f"   Trade {i+1}: ALLOWED")
            except RateLimitExceeded as e:
                print(f"   Trade {i+1}: DENIED - {e.message}")
        
        # Reset
        await limiter.reset_limits(user_id)
        
        # Test 2: Position limit
        print("\n2. Testing position limit:")
        for i in range(22):
            await limiter.increment_position_count(user_id)
        
        can_open = await limiter.can_open_position(user_id)
        print(f"   Can open position (21st): {can_open}")
        
        # Test 3: Status
        print("\n3. Testing status:")
        status = await limiter.get_status(user_id)
        print(f"   Positions: {status.open_positions}/{status.max_positions}")
        print(f"   Allowed: {status.allowed}")
        
        # Cleanup
        await limiter.reset_limits(user_id)
        print("\n✅ Tests completed!")
    
    asyncio.run(basic_test())
