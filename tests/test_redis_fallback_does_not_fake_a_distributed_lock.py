"""An unavailable Redis must refuse, not hand back a per-process dict that says yes.

WHAT THIS GUARDS
----------------
``SharedRedisManager._get_active_cache`` ended with:

    from backend_app.core.safety_config import get_vyomquant_mode
    if get_vyomquant_mode() != "production":
        return self._mock_client
    return None

``get_vyomquant_mode()`` returns ``VYOMQUANT_MODE``, whose documented values are
``safe`` / ``paper`` / ``live``. ``"production"`` is a value of the *separate* ``ENV``
variable. The comparison was between two different vocabularies and **could never be
false**, so the ``return None`` was unreachable and the in-memory mock was returned in
every environment.

Reproduced with the exact ECS task-definition settings (``ENV=production``,
``VYOMQUANT_MODE=paper``, ``DEV_MODE=false``) and Redis unreachable:

    get_vyomquant_mode()                 = 'paper'
    get_vyomquant_mode() != "production" -> True
    _get_active_cache() returned         : MockRedisClient

WHY THIS IS A TRADING DEFECT, NOT A CACHING ONE
-----------------------------------------------
This manager backs two controls that must be global rather than per-process:

* ``master_executor``'s execution lock — ``set(lock_key, nx=True, ex=30)``
* ``IdempotentExchangeSubmission.claim_submission``'s ``SET NX`` claim

``MockRedisClient`` is backed by a plain ``dict`` on a module-global instance, so it is
per **process**. Every pod wins its own ``NX`` and both controls silently become no-ops
across pods — while reporting success.

That last clause is the sharp edge, and it is why this file exists alongside
``test_idempotent_submission_fails_closed.py`` rather than inside it: the fail-closed
handling added there defends against a store that **errors**. This one **lies
successfully**, so no exception is ever raised and that handling never fires. N pods
consuming one signal produce N real orders.
"""

import asyncio
import importlib

import pytest


@pytest.fixture
def rm(monkeypatch):
    """The shared manager with no reachable Redis, so the fallback branch is taken."""
    module = importlib.import_module("backend_app.core.cache.redis_manager")
    manager = module.redis_manager

    # Force "no real cache": this is what an outage looks like to this method.
    monkeypatch.setattr(module.SharedRedisManager, "_ensure_manager", _noop)
    monkeypatch.setattr(manager, "_redis_manager", None, raising=False)
    # DEV_MODE is a module constant read at import; neutralise it so the environment
    # variables below are what decide, which is the subject under test.
    monkeypatch.setattr(module, "DEV_MODE", False, raising=False)
    return module, manager


async def _noop(self):
    return None


# ─────────────────────────────────────────────────────────────────────────────
# The gate
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("env", ["production", "prod", "staging"])
def test_a_deployed_environment_gets_no_cache_rather_than_a_fake_one(rm, monkeypatch, env):
    """THE DEFECT."""
    module, manager = rm
    monkeypatch.setenv("ENV", env)
    monkeypatch.setenv("VYOMQUANT_MODE", "paper")
    monkeypatch.setenv("DEV_MODE", "false")

    client = asyncio.run(manager._get_active_cache())

    assert client is None, (
        f"with ENV={env!r} and Redis unavailable, the manager returned "
        f"{type(client).__name__}. A per-process dict standing in for a distributed "
        "lock lets every pod acquire the same lock simultaneously."
    )


def test_an_unset_env_gets_no_cache(rm, monkeypatch):
    """Fails closed. An unconfigured deployment must not be handed a fake lock."""
    module, manager = rm
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("DEV_MODE", "false")

    assert asyncio.run(manager._get_active_cache()) is None


def test_the_trading_mode_does_not_decide_whether_a_fake_cache_is_acceptable(rm, monkeypatch):
    """The root cause, pinned. ``VYOMQUANT_MODE`` is safe/paper/live — a trading mode,
    not a deployment environment. It must have no influence here, whatever its value."""
    module, manager = rm
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEV_MODE", "false")

    results = []
    for mode in ("safe", "paper", "live", "production", "anything"):
        monkeypatch.setenv("VYOMQUANT_MODE", mode)
        results.append(asyncio.run(manager._get_active_cache()))

    assert all(r is None for r in results), (
        "the trading mode changed whether a fake cache was substituted in production"
    )


@pytest.mark.parametrize("env", ["development", "dev", "local", "test", "testing"])
def test_developer_and_test_environments_still_get_the_in_memory_cache(rm, monkeypatch, env):
    """The fix must not break local development or the suite, where there is genuinely
    no second process to coordinate with."""
    module, manager = rm
    monkeypatch.setenv("ENV", env)
    monkeypatch.setenv("DEV_MODE", "false")

    client = asyncio.run(manager._get_active_cache())

    assert client is manager._mock_client


def test_dev_mode_cannot_override_a_declared_production_environment(rm, monkeypatch):
    """INVERTED. This test previously asserted the opposite, and it was wrong.

    It was called ``test_explicit_dev_mode_still_gets_the_in_memory_cache`` and granted
    the mock cache for ``ENV=production`` plus ``DEV_MODE=true``, on the same reasoning
    its sibling in ``test_api03_rate_limit_storage_is_fail_closed.py`` used: that
    ``DEV_MODE=true`` is an explicit operator statement. But ``ENV=production`` is also an
    explicit operator statement, and when the two conflict a safety control must take the
    safe branch.

    The stake here is higher than for rate limiting. The mock cache is what backs the
    *distributed lock*: substituting it in production hands out a lock that reports
    success separately in every pod, which is the exact defect this file was written to
    prevent. One leftover ``DEV_MODE=true`` reinstated it.

    ``core/runtime_env.local_state_is_acceptable()`` returned ``True`` on ``DEV_MODE``
    before consulting ``ENV``. A declared non-local environment now wins, and because
    both this gate and the rate limiter ask that single function, the fix closes both at
    once -- which is the whole reason the judgement was centralised.
    """
    module, manager = rm
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEV_MODE", "true")

    assert asyncio.run(manager._get_active_cache()) is None, (
        "DEV_MODE=true still substitutes a fake cache -- and therefore a fake "
        "distributed lock -- in a declared production environment"
    )


def test_dev_mode_is_honoured_where_the_environment_is_silent(rm, monkeypatch):
    """POSITIVE CONTROL for the inversion above.

    Without this, the test above would also pass if ``DEV_MODE`` had simply been removed
    from the gate, and a developer who has not set ``ENV`` would get no cache at all.
    """
    module, manager = rm
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("DEV_MODE", "true")

    assert asyncio.run(manager._get_active_cache()) is manager._mock_client


def test_the_gate_does_not_consult_the_trading_mode_at_all(rm):
    """Parsed rather than grepped: the docstring explains the defect and therefore
    necessarily names ``get_vyomquant_mode``."""
    import ast
    import inspect
    import textwrap

    module, _manager = rm
    tree = ast.parse(textwrap.dedent(inspect.getsource(module._mock_cache_is_acceptable)))
    func = tree.body[0]
    body = func.body[1:] if ast.get_docstring(func) else func.body

    names = {
        node.id for stmt in body for node in ast.walk(stmt) if isinstance(node, ast.Name)
    } | {
        node.attr for stmt in body for node in ast.walk(stmt) if isinstance(node, ast.Attribute)
    }

    assert "get_vyomquant_mode" not in names, (
        "the cache fallback still consults the trading mode, which can never equal "
        "'production' and therefore always permits the mock"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The consequence, at the level that matters
# ─────────────────────────────────────────────────────────────────────────────

def test_a_lock_is_refused_rather_than_granted_when_redis_is_gone(rm, monkeypatch):
    """``master_executor`` takes its execution lock with ``set(..., nx=True)``. With no
    cache, that must come back falsey so the tick is skipped."""
    module, manager = rm
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEV_MODE", "false")

    acquired = asyncio.run(
        manager.set("execution:tenant-1:BTC/USDT:exec_demo", "1", ex=30, nx=True)
    )

    assert not acquired, (
        "an execution lock was reported as acquired while Redis was unavailable; every "
        "pod would acquire it and submit the same order"
    )


def test_the_mock_store_is_per_instance_which_is_why_it_cannot_be_a_lock(rm):
    """States the reason the substitution is unsafe, so the test survives a refactor
    that keeps the mock but changes where it is returned."""
    module, _manager = rm

    one = module.MockRedisClient()
    two = module.MockRedisClient()

    assert asyncio.run(one.set("k", "1", nx=True)) is True
    assert asyncio.run(two.set("k", "1", nx=True)) is True, (
        "two MockRedisClient instances share state, which would make this test "
        "meaningless rather than make the mock safe"
    )
