"""
FIN-CRITICAL-004 Regression Test: Atomic Idempotency Check-and-Set Fix

Tests that idempotency check and lock acquisition are atomic using Redis Lua scripts
to prevent race conditions that could cause duplicate order execution.

This test verifies the fix for the critical idempotency race condition issue.
"""

import pytest
import asyncio
from backend_app.core.distributed_idempotency import DistributedIdempotency
from backend_app.core.cache.redis_manager import redis_manager


@pytest.mark.asyncio
async def test_atomic_check_and_set():
    """
    FIN-CRITICAL-004: Verify that check-and-set is atomic.
    
    This test ensures that idempotency check and lock acquisition happen
    in a single atomic operation to prevent race conditions.
    """
    redis_client = await redis_manager.get_client()
    
    tenant_id = "test_tenant"
    client_order_id = "test_order_001"
    exchange_id = "binance"
    
    # Test the atomic check-and-set method
    idempotency = DistributedIdempotency()
    
    # First call should acquire lock
    owner_token_1 = "owner_1"
    cached_result_1, lock_acquired_1 = await idempotency._atomic_check_and_set(
        tenant_id, client_order_id, owner_token_1, exchange_id
    )
    
    assert cached_result_1 is None, "First call should not have cached result"
    assert lock_acquired_1 is True, "First call should acquire lock"
    
    # Second concurrent call should not acquire lock
    owner_token_2 = "owner_2"
    cached_result_2, lock_acquired_2 = await idempotency._atomic_check_and_set(
        tenant_id, client_order_id, owner_token_2, exchange_id
    )
    
    assert cached_result_2 is None, "Second call should not have cached result (still processing)"
    assert lock_acquired_2 is False, "Second call should not acquire lock"
    
    # Clean up
    await redis_client.delete(f"idempotency:{tenant_id}:{exchange_id}:{client_order_id}")
    
    print("✓ Atomic check-and-set prevents race conditions")


@pytest.mark.asyncio
async def test_lua_script_execution():
    """
    FIN-CRITICAL-004: Verify that Redis Lua scripts execute correctly.
    
    This test ensures that the Lua script for atomic check-and-set works
    as expected.
    """
    redis_client = await redis_manager.get_client()
    
    # Test Lua script execution
    lua_script = """
    local key = KEYS[1]
    local lock_payload = ARGV[1]
    local processing_ttl = ARGV[2]
    
    local current_value = redis.call('GET', key)
    
    if current_value and string.sub(current_value, 1, 7) ~= 'processing' then
        return {1, current_value}
    end
    
    if not current_value then
        redis.call('SET', key, lock_payload, 'EX', processing_ttl, 'NX')
        return {0, lock_payload}
    end
    
    return {0, false}
    """
    
    key = "test_lua_key"
    lock_payload = "processing:test_owner"
    
    # First call - should acquire lock
    result = await redis_client.eval(lua_script, 1, key, lock_payload, "60")
    status, value = result
    
    assert status == 0, "First call should return status 0 (lock acquired)"
    assert value == lock_payload, "First call should return lock payload"
    
    # Second call - should not acquire lock
    result = await redis_client.eval(lua_script, 1, key, "processing:other_owner", "60")
    status, value = result
    
    assert status == 0, "Second call should return status 0 (lock not acquired)"
    assert value is False, "Second call should return False (lock not acquired)"
    
    # Clean up
    await redis_client.delete(key)
    
    print("✓ Lua script execution works correctly")


@pytest.mark.asyncio
async def test_concurrent_idempotency_checks():
    """
    FIN-CRITICAL-004: Test concurrent idempotency checks.
    
    This test simulates concurrent requests to verify that only one
    request acquires the lock atomically.
    """
    idempotency = DistributedIdempotency()
    
    tenant_id = "test_tenant_concurrent"
    client_order_id = "test_order_concurrent"
    exchange_id = "binance"
    
    results = []
    
    async def concurrent_check(worker_id):
        """Simulate a concurrent idempotency check."""
        owner_token = f"owner_{worker_id}"
        cached_result, lock_acquired = await idempotency._atomic_check_and_set(
            tenant_id, client_order_id, owner_token, exchange_id
        )
        results.append((worker_id, cached_result, lock_acquired))
    
    # Run concurrent checks
    tasks = [concurrent_check(i) for i in range(5)]
    await asyncio.gather(*tasks)
    
    # Only one should acquire the lock
    lock_acquired_count = sum(1 for _, _, lock_acquired in results if lock_acquired)
    assert lock_acquired_count == 1, f"Only one should acquire lock, got {lock_acquired_count}"
    
    # All others should have lock_acquired = False
    not_acquired_count = sum(1 for _, _, lock_acquired in results if not lock_acquired)
    assert not_acquired_count == 4, f"Four should not acquire lock, got {not_acquired_count}"
    
    # Clean up
    redis_client = await redis_manager.get_client()
    await redis_client.delete(f"idempotency:{tenant_id}:{exchange_id}:{client_order_id}")
    
    print("✓ Concurrent idempotency checks are atomic")


@pytest.mark.asyncio
async def test_cached_result_return():
    """
    FIN-CRITICAL-004: Verify that cached results are returned atomically.
    
    This test ensures that when a completed result exists, it is returned
    immediately without attempting to acquire the lock.
    """
    redis_client = await redis_manager.get_client()
    
    tenant_id = "test_tenant_cached"
    client_order_id = "test_order_cached"
    exchange_id = "binance"
    key = f"idempotency:{tenant_id}:{exchange_id}:{client_order_id}"
    
    # Set a completed result (not processing)
    completed_result = '{"result": "success", "order_id": "12345"}'
    await redis_client.set(key, completed_result, ex=3600)
    
    # Try to acquire lock - should return cached result
    idempotency = DistributedIdempotency()
    cached_result, lock_acquired = await idempotency._atomic_check_and_set(
        tenant_id, client_order_id, "test_owner", exchange_id
    )
    
    assert cached_result is not None, "Should return cached result"
    assert cached_result.get("result") == "success", "Cached result should contain success"
    assert lock_acquired is False, "Should not acquire lock when cached result exists"
    
    # Clean up
    await redis_client.delete(key)
    
    print("✓ Cached results are returned atomically")


@pytest.mark.asyncio
async def test_idempotency_race_condition_prevention():
    """
    FIN-CRITICAL-004: Test that race conditions are prevented.
    
    This test simulates the exact race condition scenario where two workers
    check idempotency and try to acquire the lock concurrently.
    """
    idempotency = DistributedIdempotency()
    
    tenant_id = "test_tenant_race"
    client_order_id = "test_order_race"
    exchange_id = "binance"
    
    async def worker_simulation(worker_id):
        """Simulate a worker attempting to process an order."""
        owner_token = f"worker_{worker_id}"
        
        # Atomic check-and-set
        cached_result, lock_acquired = await idempotency._atomic_check_and_set(
            tenant_id, client_order_id, owner_token, exchange_id
        )
        
        if lock_acquired:
            # Simulate processing
            await asyncio.sleep(0.01)
            
            # Store result
            result_data = {"result": f"processed_by_{worker_id}", "order_id": client_order_id}
            await idempotency.store_result(tenant_id, client_order_id, result_data, exchange_id)
            
            return f"worker_{worker_id}_processed"
        else:
            return f"worker_{worker_id}_skipped"
    
    # Run workers concurrently
    tasks = [worker_simulation(i) for i in range(10)]
    results = await asyncio.gather(*tasks)
    
    # Only one should have processed
    processed_count = sum(1 for r in results if "_processed" in r)
    assert processed_count == 1, f"Only one should process, got {processed_count}"
    
    # Nine should have skipped
    skipped_count = sum(1 for r in results if "_skipped" in r)
    assert skipped_count == 9, f"Nine should skip, got {skipped_count}"
    
    # Clean up
    redis_client = await redis_manager.get_client()
    await redis_client.delete(f"idempotency:{tenant_id}:{exchange_id}:{client_order_id}")
    
    print("✓ Race conditions are prevented by atomic check-and-set")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
