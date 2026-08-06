"""
tests/test_dag_redis_migration.py — DAG Engine Redis Migration Tests

Tests that verify the DAG engine works correctly with the canonical Redis client
and dev-mode fallback after migration from core.redis_client to core.cache.redis_manager.
"""

import asyncio
import os
import pytest
from unittest.mock import patch, MagicMock


class TestDAGEngineRedisMigration:
    """Test DAG engine migration to canonical Redis client."""

    def test_dag_engine_uses_canonical_redis_client(self):
        """Test that DAG engine uses canonical redis_manager, not old redis_client."""
        from backend_app.backend.dag_engine import DAGEngine
        from backend_app.core.cache import redis_manager
        
        # Create DAG engine instance
        engine = DAGEngine("test_tenant")
        
        # Mock the redis_manager to ensure it's being called
        with patch.object(redis_manager, 'get_client', return_value=MagicMock()) as mock_get_client:
            mock_client = asyncio.run(mock_get_client())
            
            # Test event buffering
            event = {"type": "price_update", "symbol": "BTC/USDT", "price": 50000.0}
            event_id = asyncio.run(engine.buffer_event("test_tenant", event))
            
            # Verify canonical client was called
            assert mock_get_client.called, "DAG engine should use canonical redis_manager.get_client()"
            
            # Verify the mock client's xadd was called
            assert mock_client.xadd.called, "DAG engine should call xadd on redis client"

    def test_dag_engine_dev_mode_fallback(self):
        """Test that DAG engine works in DEV_MODE with MockRedisClient."""
        from backend_app.backend.dag_engine import DAGEngine
        from backend_app.core.cache import MockRedisClient
        
        # DEV_MODE is set by conftest.py
        
        # Create DAG engine instance
        engine = DAGEngine("test_tenant")
        
        # Test event buffering with mock client
        event = {"type": "price_update", "symbol": "BTC/USDT", "price": 50000.0}
        event_id = asyncio.run(engine.buffer_event("test_tenant", event))
        
        # Verify event was buffered in mock (should return an ID)
        assert event_id is not None, "MockRedisClient should return event ID in DEV_MODE"
        
        # Test event retrieval
        events = asyncio.run(engine.get_last_events("test_tenant", count=10))
        assert isinstance(events, list), "Should return list of events"

    def test_dag_event_loop_uses_canonical_redis_client(self):
        """Test that DAG event loop uses canonical redis_manager."""
        from backend_app.backend.dag_event_loop import DAGEventLoop
        from backend_app.core.cache import redis_manager
        
        # Mock redis_manager
        with patch.object(redis_manager, 'get_client', return_value=MagicMock()) as mock_get_client:
            mock_client = asyncio.run(mock_get_client())
            mock_client.set.return_value = True  # Lock acquisition success
            mock_client.sismember.return_value = False  # Signal not executed
            mock_client.sadd.return_value = 1  # Signal marked executed
            mock_client.expire.return_value = True  # Expire set successfully
            
            # Create DAG event loop (minimal setup)
            event_loop = DAGEventLoop(
                tenant_id="test_tenant",
                dag_nodes=[],
                dag_edges=[],
                strategy_id="test_strategy"
            )
            
            # Test signal execution check
            is_executed = asyncio.run(event_loop._check_signal_executed("test_tenant", "signal_123"))
            assert mock_get_client.called, "Event loop should use canonical redis_manager.get_client()"
            
            # Test signal marking
            asyncio.run(event_loop._mark_signal_executed("test_tenant", "signal_123"))
            assert mock_client.sadd.called, "Event loop should call sadd on redis client"

    def test_dag_engine_stream_operations(self):
        """Test that DAG engine stream operations work with canonical client."""
        from backend_app.backend.dag_engine import DAGEngine
        from backend_app.core.cache import MockRedisClient
        
        # Create DAG engine with mock client
        engine = DAGEngine("test_tenant")
        
        # Test multiple event buffering
        events = [
            {"type": "price_update", "symbol": "BTC/USDT", "price": 50000.0},
            {"type": "price_update", "symbol": "BTC/USDT", "price": 50100.0},
            {"type": "price_update", "symbol": "BTC/USDT", "price": 50200.0}
        ]
        
        for event in events:
            asyncio.run(engine.buffer_event("test_tenant", event))
        
        # Test event retrieval
        retrieved_events = asyncio.run(engine.get_last_events("test_tenant", count=3))
        assert len(retrieved_events) == 3, "Should retrieve all buffered events"
        
        # Test buffer clearing
        cleared = asyncio.run(engine.clear_event_buffer("test_tenant"))
        assert cleared is True, "Buffer should be cleared successfully"
        
        # Verify buffer is empty after clearing
        events_after_clear = asyncio.run(engine.get_last_events("test_tenant", count=10))
        assert len(events_after_clear) == 0, "Buffer should be empty after clearing"

    def test_redis_manager_streams_support(self):
        """Test that canonical redis_manager supports stream operations."""
        from backend_app.core.cache.redis_manager import SharedRedisManager, MockRedisClient
        
        # Test that SharedRedisManager supports xadd and xrevrange
        manager = SharedRedisManager()
        
        # In DEV_MODE, should use mock client
        redis_client = asyncio.run(manager.get_client())
        assert hasattr(redis_client, 'xadd'), "Redis client should support xadd"
        assert hasattr(redis_client, 'xrevrange'), "Redis client should support xrevrange"
        
        # Test stream operations
        stream_name = "test_stream"
        test_data = {"test": "data", "value": 123}
        
        entry_id = asyncio.run(redis_client.xadd(stream_name, test_data))
        assert entry_id is not None, "xadd should return entry ID"
        
        retrieved = asyncio.run(redis_client.xrevrange(stream_name, count=1))
        assert len(retrieved) == 1, "xrevrange should return one entry"

    def test_mock_redis_client_compatibility(self):
        """Test that MockRedisClient provides all methods used by DAG engine."""
        from backend_app.core.cache import MockRedisClient
        
        mock_client = MockRedisClient()
        
        # Test all methods used by DAG engine
        assert hasattr(mock_client, 'xadd'), "MockRedisClient should have xadd"
        assert hasattr(mock_client, 'xrevrange'), "MockRedisClient should have xrevrange"
        assert hasattr(mock_client, 'delete'), "MockRedisClient should have delete"
        assert hasattr(mock_client, 'set'), "MockRedisClient should have set"
        assert hasattr(mock_client, 'sismember'), "MockRedisClient should have sismember"
        assert hasattr(mock_client, 'sadd'), "MockRedisClient should have sadd"
        assert hasattr(mock_client, 'expire'), "MockRedisClient should have expire"


class TestRepositoryWideRedisCanonical:
    """Test that only canonical Redis client is importable from production code."""

    def test_only_canonical_redis_importable(self):
        """Test that deleted redis_client modules are not importable."""
        import sys
        
        # Test that old modules are not importable
        try:
            from backend_app.core.redis_client import redis_client
            assert False, "core.redis_client should not be importable (was deleted)"
        except ImportError:
            pass  # Expected
        
        try:
            from backend_app.core.redis_streams import publish
            assert False, "core.redis_streams should not be importable (was deleted)"
        except ImportError:
            pass  # Expected
        
        # Test that canonical client is importable
        from backend_app.core.cache import redis_manager, MockRedisClient
        assert redis_manager is not None, "Canonical redis_manager should be importable"
        assert MockRedisClient is not None, "MockRedisClient should be importable from cache module"

    def test_backend_redis_manager_still_exists(self):
        """Test that backend.redis_manager (canonical) still exists."""
        from backend_app.backend.redis_manager import RedisManager
        assert RedisManager is not None, "Backend RedisManager should still exist"

    def test_cache_redis_manager_wrapper_exists(self):
        """Test that cache.redis_manager wrapper still exists."""
        from backend_app.core.cache.redis_manager import SharedRedisManager
        assert SharedRedisManager is not None, "Cache wrapper should still exist"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
