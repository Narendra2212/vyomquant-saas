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


@pytest.fixture(autouse=True)
def restore_rate_limit_module():
    """Put `core.rate_limit` back the way it was found.

    Every test below reloads the module, and `importlib.reload` mutates the module object
    in place: `rate_limit.limiter` is rebound to a freshly constructed `Limiter` while
    `app.state.limiter` and every `@limiter.limit(...)` decorator closure in the routers
    keep the original object. Two consequences that outlive this file:

    * A later test that reaches for `rate_limit.limiter` gets an orphan no route consults.
      `tests/test_support_feature_e2e.py` calls `limiter.reset()` to clear its own
      rate-limit budget; against the orphan that reset silently does nothing, and its
      ninth `POST /api/support/tickets` answers 429 whenever the module happens to run
      fast enough to fit inside one 60-second window. That is exactly how a fixed baseline
      turns into "44 or 45 failures".
    * Two tests here reload under `ENV=production` and expect `RuntimeError`, which leaves
      the module half-initialised.

    Snapshotting and restoring `__dict__` keeps the damage inside this file: `monkeypatch`
    already restores the environment, and this restores the singleton the environment was
    read into.
    """
    module = importlib.import_module("backend_app.core.rate_limit")
    snapshot = dict(module.__dict__)
    try:
        yield
    finally:
        module.__dict__.clear()
        module.__dict__.update(snapshot)


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
        # API-03 rewrote this message. The assertion checks the DIAGNOSTIC CONTENT the
        # old literal was standing in for -- it names the variable, names the subsystem
        # and says the condition is fatal -- rather than the old exact sentence
        # "REDIS_URL environment variable is missing". The control (it raises) is
        # unchanged; only the wording moved.
        message = str(exc_info.value)
        assert "REDIS_URL" in message
        assert "rate limit" in message.lower()
        assert "fatal" in message.lower()

    def test_ambiguous_environment_without_redis_now_depends_on_dev_mode(
        self, monkeypatch
    ):
        """UPDATED BY API-03. 'safe' is a trading mode and is no longer an environment.

        This test used to read: "When no ENV/VYOMQUANT_MODE/AERORA_MODE is set,
        get_vyomquant_mode returns 'safe' which is in DEV_TEST_ENVS — so in-memory limiter
        is used without raising."

        That premise is gone. API-03 removed the ``get_vyomquant_mode`` read, because a
        trading mode says what the system may do with money and nothing about how many
        processes are running; ``safe`` and ``paper`` are no longer on the allow-list.

        With nothing set at all the limiter now REFUSES. It only falls back to memory when
        something declares the environment local -- here ``DEV_MODE``. Both halves are
        asserted, because the test would otherwise have kept passing for a reason it did
        not state: the suite-wide ``DEV_MODE=true`` from ``tests/conftest.py``.
        """
        for name in ("ENV", "ENVIRONMENT", "VYOMQUANT_MODE", "AERORA_MODE", "REDIS_URL",
                     "DEV_MODE"):
            monkeypatch.delenv(name, raising=False)

        rate_limit_module = importlib.import_module("backend_app.core.rate_limit")
        with pytest.raises(RuntimeError):
            importlib.reload(rate_limit_module)

        # ...and with the environment declared local, memory is permitted again.
        monkeypatch.setenv("DEV_MODE", "true")
        mod = importlib.reload(rate_limit_module)
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
        # API-03 rewrote this message; see the note on the sibling test above. The
        # diagnostic content asserted here is what the old literal "Failed to initialize
        # Redis rate limit storage" was standing in for.
        message = str(exc_info.value)
        assert "rate limit" in message.lower()
        assert "unreachable" in message.lower() or "fatal" in message.lower()
