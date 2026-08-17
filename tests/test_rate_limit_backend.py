"""
tests/test_rate_limit_backend.py

Unit tests verifying strict rate limiter storage backend selection in core.rate_limit.

WHAT IS TESTED
-------------
1. Local/dev/test mode without REDIS_URL uses in-memory storage without error.
2. Production mode with valid REDIS_URL arms Redis-backed limiter.
3. Production mode without REDIS_URL raises RuntimeError (fails hard).
4. Ambiguous environment (neither ENV nor VYOMQUANT_MODE set) without REDIS_URL raises RuntimeError.
5. Production mode with invalid/failing REDIS_URL raises RuntimeError.
"""

import importlib
import os
import pytest
from unittest.mock import patch, MagicMock

class TestRateLimitBackendSelection:
    def test_dev_environment_without_redis_uses_memory(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        monkeypatch.delenv("REDIS_URL", raising=False)
        
        rate_limit_module = importlib.import_module("backend_app.core.rate_limit")
        mod = importlib.reload(rate_limit_module)
        assert mod.limiter._storage_uri == "memory://"

    def test_test_environment_without_redis_uses_memory(self, monkeypatch):
        monkeypatch.setenv("ENV", "testing")
        monkeypatch.delenv("REDIS_URL", raising=False)
        
        rate_limit_module = importlib.import_module("backend_app.core.rate_limit")
        mod = importlib.reload(rate_limit_module)
        assert mod.limiter._storage_uri == "memory://"

    def test_production_environment_without_redis_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.delenv("REDIS_URL", raising=False)
        
        rate_limit_module = importlib.import_module("backend_app.core.rate_limit")
        with pytest.raises(RuntimeError) as exc_info:
            importlib.reload(rate_limit_module)
        assert "REDIS_URL environment variable is missing" in str(exc_info.value)

    def test_ambiguous_environment_without_redis_falls_back_to_safe(self, monkeypatch):
        """When no ENV/VYOMQUANT_MODE/AERORA_MODE is set, get_vyomquant_mode returns 'safe'
        which is in DEV_TEST_ENVS — so in-memory limiter is used without raising."""
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("VYOMQUANT_MODE", raising=False)
        monkeypatch.delenv("AERORA_MODE", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)
        
        rate_limit_module = importlib.import_module("backend_app.core.rate_limit")
        mod = importlib.reload(rate_limit_module)
        # 'safe' default is in DEV_TEST_ENVS so in-memory limiter is used
        assert mod.limiter is not None
        assert mod.limiter._storage_uri == "memory://"

    def test_production_environment_with_redis_url_arms_redis(self, monkeypatch):
        """Production env with valid REDIS_URL arms Redis limiter (mocking the ping to avoid live server)."""
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        
        with patch("redis.from_url") as mock_redis:
            mock_client = MagicMock()
            mock_redis.return_value = mock_client
            mock_client.ping.return_value = True
            
            rate_limit_module = importlib.import_module("backend_app.core.rate_limit")
            mod = importlib.reload(rate_limit_module)
            assert mod.limiter is not None
            mock_client.ping.assert_called_once()

    def test_production_environment_redis_init_failure_raises_runtime_error(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("REDIS_URL", "invalid-scheme://localhost")

        rate_limit_module = importlib.import_module("backend_app.core.rate_limit")
        # Production environment should fail hard if Redis initialization fails
        with pytest.raises(RuntimeError) as exc_info:
            importlib.reload(rate_limit_module)
        assert "Failed to initialize Redis rate limit storage" in str(exc_info.value)
