"""API-03 regression: the rate limiter refuses to run per-pod outside a local environment.

THE DEFECT
----------
``core/rate_limit.py`` chose its storage from::

    env_raw = os.getenv("ENV") or get_vyomquant_mode(default="safe")
    DEV_TEST_ENVS = {"testing", "test", "development", "dev", "local",
                     "safe", "paper", "ambiguous"}

``VYOMQUANT_MODE`` is a trading mode (``safe`` / ``paper`` / ``live``, per
``core/deployment_config.py``), not a deployment environment. Treating ``safe`` and
``paper`` as dev environments meant that with ``ENV`` unset -- and the documented
``VYOMQUANT_MODE=paper`` -- the limiter selected ``memory://``. In-memory storage is
per-process, so the effective limit became the configured limit times the replica count,
and the only trace was an ``info`` line.

The second branch made a genuine outage do the same thing: ``REDIS_URL`` set but Redis
unreachable fell back to memory, because ``"paper"`` was in the allowlist, instead of
failing closed.

And ``"ambiguous"`` was unreachable. ``get_vyomquant_mode`` always returns a non-empty
string, so ``env_raw`` was never falsey, so ``env`` was never set to ``"ambiguous"`` --
yet it was listed as a permitted dev environment, and the ``elif`` beneath it was
commented as the branch that would catch an ambiguous environment.

WHY THIS IS TESTABLE AT ALL
---------------------------
The original decision ran at import and left nothing behind but log lines, so it could
only be tested by re-importing the module under a doctored environment. The fix computes
a frozen :class:`RateLimitConfig` through :func:`resolve_rate_limit_config`, which takes
its Redis probe as an argument. The decision can therefore be asserted directly, for
every environment combination, without import games.

Section 5 re-imports the module anyway, because the import-time wiring is what actually
runs in production and a pure-function test would not prove it was connected.
"""

from __future__ import annotations

import dataclasses
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.rate_limit import (  # noqa: E402
    MEMORY_URI,
    RateLimitConfig,
    resolve_rate_limit_config,
)
from backend_app.core.runtime_env import (  # noqa: E402
    LOCAL_STATE_ENVS,
    local_state_is_acceptable,
)

REACHABLE = "redis://127.0.0.1:6379/0"


def _ok(_url: str) -> None:
    """A probe for a reachable Redis."""
    return None


def _down(_url: str) -> None:
    raise ConnectionError("connection refused")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every case from a blank environment.

    tests/conftest.py sets ENV=testing and DEV_MODE=true for the whole session, which is
    exactly the configuration that would mask this defect, so each case states its own.
    """
    for var in ("ENV", "ENVIRONMENT", "DEV_MODE", "VYOMQUANT_MODE", "AERORA_MODE",
                "REDIS_URL"):
        monkeypatch.delenv(var, raising=False)


# ── 1. the production configurations that used to degrade silently ──────────


def test_the_documented_production_config_refuses_in_memory(monkeypatch):
    """THE API-03 ASSERTION.

    ``ENV=production``, ``VYOMQUANT_MODE=paper``, no ``REDIS_URL`` -- the real ECS
    shape. Pre-fix this returned a memory limiter; it must now raise.
    """
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("VYOMQUANT_MODE", "paper")

    with pytest.raises(RuntimeError) as excinfo:
        resolve_rate_limit_config(probe=_ok)

    assert "no shared store" in str(excinfo.value)


def test_paper_mode_with_env_unset_refuses_in_memory(monkeypatch):
    """The exact combination the old allowlist let through.

    ``ENV`` unset meant ``get_vyomquant_mode(default="safe")`` supplied ``"paper"``, and
    ``"paper"`` was listed as a dev environment.
    """
    monkeypatch.setenv("VYOMQUANT_MODE", "paper")

    with pytest.raises(RuntimeError):
        resolve_rate_limit_config(probe=_ok)


@pytest.mark.parametrize("mode", ["safe", "paper", "live", "ambiguous"])
def test_no_trading_mode_grants_in_memory_storage(monkeypatch, mode):
    """A trading mode must have no bearing on where counters live."""
    monkeypatch.setenv("VYOMQUANT_MODE", mode)

    with pytest.raises(RuntimeError):
        resolve_rate_limit_config(probe=_ok)


@pytest.mark.parametrize("mode", ["safe", "paper", "live"])
def test_the_legacy_mode_variable_does_not_grant_it_either(monkeypatch, mode):
    """``AERORA_MODE`` is the deprecated alias ``get_vyomquant_mode`` also reads."""
    monkeypatch.delenv("VYOMQUANT_MODE", raising=False)
    monkeypatch.setenv("AERORA_MODE", mode)

    with pytest.raises(RuntimeError):
        resolve_rate_limit_config(probe=_ok)


def test_an_unset_environment_fails_closed():
    """Silence must not mean "local". An unconfigured pod is still a pod."""
    with pytest.raises(RuntimeError):
        resolve_rate_limit_config(probe=_ok)


def test_an_unrecognised_environment_fails_closed(monkeypatch):
    monkeypatch.setenv("ENV", "staging-canary-7")

    with pytest.raises(RuntimeError):
        resolve_rate_limit_config(probe=_ok)


# ── 2. a genuine outage is not a licence to degrade ─────────────────────────


def test_an_unreachable_redis_in_production_raises_rather_than_degrading(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("REDIS_URL", REACHABLE)

    with pytest.raises(RuntimeError) as excinfo:
        resolve_rate_limit_config(probe=_down)

    assert "unreachable" in str(excinfo.value)


def test_the_refusal_explains_what_to_do(monkeypatch):
    """A fail-closed import that does not say why is an outage with extra steps."""
    monkeypatch.setenv("ENV", "production")

    with pytest.raises(RuntimeError) as excinfo:
        resolve_rate_limit_config(probe=_ok)

    message = str(excinfo.value)
    assert "REDIS_URL" in message
    assert "replica count" in message, "the message does not say why per-pod is wrong"
    assert "DEV_MODE=true" in message, "the message does not say how to opt in locally"
    assert "ENV=production" in message, "the message does not report what it observed"


# ── 3. the configurations that should still work ───────────────────────────


def test_a_reachable_redis_is_used_in_production(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("REDIS_URL", REACHABLE)

    config = resolve_rate_limit_config(probe=_ok)

    assert config.storage_uri == REACHABLE
    assert config.is_shared
    assert config.redis_reachable


@pytest.mark.parametrize("env", sorted(LOCAL_STATE_ENVS))
def test_a_declared_local_environment_may_use_memory(monkeypatch, env):
    monkeypatch.setenv("ENV", env)

    config = resolve_rate_limit_config(probe=_ok)

    assert config.storage_uri == MEMORY_URI
    assert not config.is_shared
    assert config.local_allowed


def test_dev_mode_cannot_override_a_declared_production_environment(monkeypatch):
    """INVERTED. This test previously asserted the opposite, and it was wrong.

    It was called ``test_explicit_dev_mode_may_use_memory_even_in_production`` and
    reasoned: "An operator saying DEV_MODE=true has stated the intent explicitly." The
    flaw is that an operator who set ``ENV=production`` has *also* stated their intent
    explicitly, and the two statements contradict each other. When the inputs to a safety
    control conflict, the control must take the safe branch -- and a leftover
    ``DEV_MODE=true`` in a deployment environment is far more likely than a deliberate
    decision to run production on per-process rate limits.

    The consequence of the original premise was a production fail-open:
    ``local_state_is_acceptable()`` returned ``True`` on ``DEV_MODE`` *before* consulting
    ``ENV``, so ``ENV=production`` logged "explicitly local environment 'production'" and
    used a per-process counter behind N replicas with only a warning. This test is why
    nothing caught it: the test and the implementation were written together and encoded
    the same mistaken premise, so the suite agreed with the defect.

    Surfaced by ``tests/test_rate_limiter_fails_closed_in_production.py``, which sets both
    variables and expected a refusal.
    """
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEV_MODE", "true")

    with pytest.raises(RuntimeError) as caught:
        resolve_rate_limit_config(probe=_ok)

    assert "REDIS_URL" in str(caught.value)
    assert not local_state_is_acceptable(), (
        "DEV_MODE=true still overrides a declared production environment"
    )


def test_dev_mode_is_honoured_where_the_environment_is_silent(monkeypatch):
    """POSITIVE CONTROL for the inversion above.

    Without this, the test above would also pass if ``DEV_MODE`` had simply been removed
    from the implementation, and a developer who has not set ``ENV`` could no longer run
    the application at all. ``DEV_MODE`` must still work; it just may not outrank an
    explicit declaration.
    """
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("DEV_MODE", "true")

    assert local_state_is_acceptable()
    assert resolve_rate_limit_config(probe=_ok).storage_uri == MEMORY_URI


def test_dev_mode_is_honoured_alongside_a_declared_local_environment(monkeypatch):
    """The suite's own configuration: ENV=testing plus DEV_MODE=true must stay local."""
    monkeypatch.setenv("ENV", "testing")
    monkeypatch.setenv("DEV_MODE", "true")

    assert local_state_is_acceptable()
    assert resolve_rate_limit_config(probe=_ok).storage_uri == MEMORY_URI


def test_a_local_environment_tolerates_an_unreachable_redis(monkeypatch):
    """A developer with REDIS_URL exported but no server running must not be blocked."""
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("REDIS_URL", REACHABLE)

    config = resolve_rate_limit_config(probe=_down)

    assert config.storage_uri == MEMORY_URI
    assert config.redis_reachable is False
    assert "unreachable" in config.reason


def test_a_reachable_redis_is_preferred_even_locally(monkeypatch):
    """Being allowed to use memory is not a reason to ignore a working shared store."""
    monkeypatch.setenv("ENV", "testing")
    monkeypatch.setenv("REDIS_URL", REACHABLE)

    config = resolve_rate_limit_config(probe=_ok)

    assert config.storage_uri == REACHABLE
    assert config.is_shared


def test_a_whitespace_only_redis_url_counts_as_unset(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("REDIS_URL", "   ")

    with pytest.raises(RuntimeError):
        resolve_rate_limit_config(probe=_ok)


# ── 4. the config object is honest ─────────────────────────────────────────


def test_the_config_records_the_deployment_environment_not_a_trading_mode(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("VYOMQUANT_MODE", "paper")
    monkeypatch.setenv("REDIS_URL", REACHABLE)

    config = resolve_rate_limit_config(probe=_ok)

    assert config.env == "production", (
        f"config.env is {config.env!r}; a trading mode has leaked into the field that "
        "names the deployment environment"
    )


def test_is_shared_is_the_property_that_matters():
    shared = RateLimitConfig(
        env="production", redis_url=REACHABLE, local_allowed=False,
        redis_reachable=True, storage_uri=REACHABLE, reason="x",
    )
    local = RateLimitConfig(
        env="testing", redis_url="", local_allowed=True, redis_reachable=False,
        storage_uri=MEMORY_URI, reason="x",
    )
    assert shared.is_shared and not local.is_shared


def test_the_config_is_frozen():
    config = RateLimitConfig(
        env="testing", redis_url="", local_allowed=True, redis_reachable=False,
        storage_uri=MEMORY_URI, reason="x",
    )
    with pytest.raises(Exception):
        config.storage_uri = "redis://elsewhere"  # type: ignore[misc]


def test_ENVIRONMENT_is_honoured_as_well_as_ENV(monkeypatch):
    """Both names appear in this project's deployment configs."""
    monkeypatch.setenv("ENVIRONMENT", "development")

    assert local_state_is_acceptable()
    assert resolve_rate_limit_config(probe=_ok).storage_uri == MEMORY_URI


# ── 5. the import-time wiring is actually connected ────────────────────────


def _assert_one_config_definition(module):
    """The module's own ``RateLimitConfig`` is the same DEFINITION this file imported.

    This replaces an `isinstance(rl.config, RateLimitConfig)` that was order-dependent.
    Three suites in this repository re-import `backend_app.core.rate_limit` under a
    doctored environment -- `test_rate_limiter_fails_closed_in_production.py` and this
    file (`sys.modules.pop` + `import_module`) and `test_rate_limit_backend.py`
    (`importlib.reload`). A re-import mints a NEW `RateLimitConfig` CLASS OBJECT, while
    this module still holds the class bound at its own import. Comparing `rl.config`
    against *this* file's class therefore measured collection order rather than the
    production wiring: run after the polluting file, `isinstance` returned False for a
    perfectly well-formed `RateLimitConfig(env='testing', ...)`.

    Recorded as TEST-04 in `staging/REGRESSION_ORDER_ISOLATION.md` and as the second
    half of P11-H15 in `staging/PHASE11_LAUNCH_BLOCKER_REGISTER.md`. It mattered because
    the neutralised assertion is the one proving the fail-closed storage decision is
    really wired to the limiter -- exactly the property it exists to guarantee.

    The identity comparison did carry one real property as a side effect: that there is
    only ONE definition of `RateLimitConfig`. That is not dropped -- it is asserted here
    structurally (name, defining module, field order, frozen-ness), which a reload cannot
    neutralise because it compares shape instead of object identity. Nothing is weakened:
    a genuinely divergent second definition still fails, and `storage_uri` and
    `local_allowed` keep their own assertions below.
    """
    live = module.RateLimitConfig

    assert live.__name__ == RateLimitConfig.__name__
    assert live.__module__ == RateLimitConfig.__module__ == "backend_app.core.rate_limit"
    assert [f.name for f in dataclasses.fields(live)] == [
        f.name for f in dataclasses.fields(RateLimitConfig)
    ], "a second, divergent RateLimitConfig definition exists"
    assert live.__dataclass_params__.frozen is True, (
        "the live RateLimitConfig is no longer frozen, so the decision is mutable"
    )
    return live


def test_the_module_exposes_the_decision_it_actually_applied():
    """Imported normally, under the suite's own environment.

    `clean_env` does not apply here by design -- this asserts the module as the test
    session imported it, which is the wiring production uses.
    """
    import backend_app.core.rate_limit as rl

    live_config_class = _assert_one_config_definition(rl)
    assert isinstance(rl.config, live_config_class)
    assert rl.limiter is not None
    # slowapi records the uri it was constructed with; the limiter must match the
    # decision rather than having been built from a separate code path.
    assert rl.config.storage_uri in (MEMORY_URI, rl.config.redis_url)


def test_the_wiring_assertion_is_not_neutralised_by_a_reload(monkeypatch):
    """Regression for the order dependence itself (P11-H15 / TEST-04).

    Does to the module exactly what `test_rate_limiter_fails_closed_in_production.py`
    does when it happens to be collected first -- drops it from `sys.modules` and imports
    it again -- then re-runs the section-5 assertions against the fresh module. That
    reproduces the polluting order INSIDE one test, so the property stops depending on
    which file pytest reached first. Before the fix this body failed on `isinstance`.

    `ENV=testing` is stated explicitly because the autouse `clean_env` blanks the
    environment for every case in this file, and an undeclared environment makes the
    re-import fail closed by design (that behaviour is section 1's subject, and is
    asserted there). The variable under test here is the RELOAD, so the environment is
    held at a declared local one -- the same thing the polluting file's `_reload()` does.
    """
    monkeypatch.setenv("ENV", "testing")

    import backend_app.core.rate_limit as before

    stale_config_class = before.RateLimitConfig
    original_module = before

    sys.modules.pop("backend_app.core.rate_limit", None)
    try:
        after = importlib.import_module("backend_app.core.rate_limit")

        assert after.RateLimitConfig is not stale_config_class, (
            "the re-import did not mint a new class, so this test is no longer "
            "reproducing the condition it was written for"
        )
        assert not isinstance(after.config, stale_config_class), (
            "the re-import no longer strands the old class, so the original assertion "
            "would not have failed and this regression test proves nothing"
        )

        live_config_class = _assert_one_config_definition(after)
        assert isinstance(after.config, live_config_class)
        assert after.limiter is not None
        assert after.config.storage_uri in (MEMORY_URI, after.config.redis_url)
    finally:
        # Leave a working module bound for the rest of the session, as section 5's
        # production-import test does. The conftest leak guard resets the orphaned
        # Limiter this re-import strands.
        # Rebind the ORIGINAL module object, not a fresh import: routers were decorated with
        # the original `limiter`, and a fresh module would leave the app holding one limiter
        # while the module exports another (breaks every later test that calls a decorated
        # handler directly).
        sys.modules["backend_app.core.rate_limit"] = original_module
        import backend_app.core as _core_pkg

        _core_pkg.rate_limit = original_module


def test_importing_under_a_production_environment_without_redis_raises(monkeypatch):
    """The fail-closed path must fire at import, not only when called directly.

    This is the one case that cannot be proven by calling the resolver: the whole defect
    was that the import-time branch chose memory. Re-imported in a subprocess-free way
    by clearing the module from sys.modules.
    """
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("DEV_MODE", raising=False)
    monkeypatch.setenv("REDIS_URL", "")
    import backend_app.core.rate_limit as _orig_rl

    original_module = _orig_rl
    monkeypatch.setitem(sys.modules, "backend_app.core.rate_limit", None)
    sys.modules.pop("backend_app.core.rate_limit", None)

    try:
        with pytest.raises(RuntimeError):
            importlib.import_module("backend_app.core.rate_limit")
    finally:
        # Restore a working module for the rest of the session.
        sys.modules.pop("backend_app.core.rate_limit", None)
        monkeypatch.undo()
        sys.modules["backend_app.core.rate_limit"] = original_module
        import backend_app.core as _core_pkg

        _core_pkg.rate_limit = original_module


# ── 6. negative control ────────────────────────────────────────────────────


def test_the_old_allowlist_would_have_permitted_memory_in_production():
    """Proof that section 1 can fail.

    Reconstructs the pre-fix decision. If this ever stops reporting "memory permitted",
    the defect as described never existed and the assertions above prove nothing.
    """
    old_dev_test_envs = {
        "testing", "test", "development", "dev", "local",
        "safe", "paper", "ambiguous",
    }
    # ENV unset, VYOMQUANT_MODE=paper -> get_vyomquant_mode(default="safe") -> "paper"
    env_raw = None or "paper"
    assert env_raw.lower() in old_dev_test_envs, (
        "the reconstruction is wrong; the old allowlist did not contain 'paper'"
    )

    # And the ambiguous branch was unreachable because the fallback always returns a value.
    from backend_app.core.safety_config import get_vyomquant_mode

    assert get_vyomquant_mode(default="safe"), (
        "get_vyomquant_mode can return a falsey value, so the `env = 'ambiguous'` "
        "branch was reachable after all"
    )


def test_the_new_decision_does_not_consult_the_trading_mode():
    """AST, not substring: this module's prose names get_vyomquant_mode repeatedly."""
    import ast
    import inspect
    import textwrap

    import backend_app.core.rate_limit as rl
    import backend_app.core.runtime_env as re_mod

    for func in (rl.resolve_rate_limit_config, re_mod.local_state_is_acceptable,
                 re_mod.deployment_environment):
        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        node = tree.body[0]
        body = node.body[1:] if ast.get_docstring(node) else node.body
        referenced = {
            n.id for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Name)
        } | {
            n.attr for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Attribute)
        }
        assert "get_vyomquant_mode" not in referenced, (
            f"{func.__name__} consults the trading mode to pick a storage backend"
        )


def test_the_cache_and_the_limiter_now_share_one_definition():
    """Two fail-closed rules for the same question are two things to drift.

    Reached via ``import_module`` rather than attribute access: ``core/cache/__init__.py``
    re-exports the *singleton* under the name ``redis_manager``, so
    ``backend_app.core.cache.redis_manager`` resolves to the instance and shadows the
    module of the same name.
    """
    rm = importlib.import_module("backend_app.core.cache.redis_manager")

    assert rm._mock_cache_is_acceptable() == local_state_is_acceptable(), (
        "the cache fallback and the rate limiter disagree about whether this process "
        "may substitute local state for shared state"
    )
