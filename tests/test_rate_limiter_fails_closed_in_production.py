"""
tests/test_rate_limiter_fails_closed_in_production.py

The rate limiter already fails closed in production. This file pins that, and records
that the audit finding which said otherwise was wrong.

WHAT THE AUDIT CLAIMED, AND WHY IT WAS WRONG
P1-11 was filed as "the rate limiter falls back to in-memory storage when Redis is
absent", and then left alone on the reasoning that tightening it "could stop a booting
deployment, since ``startup.sh`` treats Redis as explicitly non-fatal". Reading
``backend_app/core/rate_limit.py`` settles both halves against that:

* A non-dev environment with no ``REDIS_URL`` **raises ``RuntimeError`` at import**, with
  the message "In-memory rate-limiting fallback is prohibited in production/non-
  development environments". There is no silent fallback to tighten.
* A non-dev environment whose ``REDIS_URL`` is set but unreachable also raises. The ping
  is performed at import and the failure is fatal.
* What ``startup.sh`` does with Redis is irrelevant to this: the limiter makes its own
  decision at import time and refuses regardless.

So there was nothing to tighten and no boot to protect. The finding is withdrawn.

THE ONE RESIDUAL GAP, MEASURED RATHER THAN ASSUMED
``env`` is resolved as ``os.getenv("ENV") or get_vyomquant_mode(default="safe")``, and
``DEV_TEST_ENVS`` contains ``"paper"`` and ``"safe"``. ``startup.sh`` resolves
``VYOMQUANT_MODE`` to ``paper`` when nothing sets it. So a deployment that sets
``VYOMQUANT_MODE`` and NOT ``ENV`` resolves to ``paper``, lands in ``DEV_TEST_ENVS``, and
gets per-process in-memory limits -- which are not shared between workers, so the
effective limit is multiplied by the worker count.

That is not reachable in this deployment: all four ECS task definitions set
``ENV=production``, which is not in ``DEV_TEST_ENVS``, so Redis is required and its
absence is fatal. The gap is recorded because it depends on a task-definition variable
rather than on anything in the code, and a new task definition that omits ``ENV`` would
silently acquire it.

WHY THIS MATTERS AGAINST A WRITTEN REQUIREMENT
Rate limits here are not a nice-to-have.
``.kiro/specs/marketplace-subscriptions-paper-trading/requirements.md`` specifies exact
figures -- at most 120 requests per 60-second window per authenticated caller and 60 per
source address for an unauthenticated one on catalogue, search and detail; 30 per caller
per 60 seconds on Price_Range evaluation -- and states that a caller exceeding them SHALL
be rejected. A per-process limiter behind N workers does not deliver the stated figure, so
the distinction between Redis-backed and in-memory is the difference between meeting that
requirement and approximating it.

HOW THIS IS TESTED
``rate_limit.py`` decides at IMPORT, so each case reloads the module under a patched
environment. The import is the behaviour under test; there is no function to call.
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

MODULE = "backend_app.core.rate_limit"

#: The environments in which per-process limits are permitted.
#:
#: UPDATED BY API-03. This set was previously restated verbatim from ``rate_limit.py``'s
#: own ``DEV_TEST_ENVS``, which contained ``"safe"``, ``"paper"`` and ``"ambiguous"``.
#: Those first two are **trading modes**, not deployment environments, and their presence
#: here was the defect: ``startup.sh`` resolves ``VYOMQUANT_MODE`` to ``paper`` when
#: nothing sets it, so the documented production trading mode selected per-process rate
#: limiting. ``"ambiguous"`` was dead -- this file's own
#: ``test_nothing_set_resolves_to_safe_and_the_ambiguous_branch_is_dead`` proved the
#: branch that assigned it could never run.
#:
#: The allow-list now lives in ``core/runtime_env.LOCAL_STATE_ENVS`` and contains no
#: trading mode at all. Restated here, as before, so a change to either side is
#: deliberate: every entry is an environment permitted to run unlimited.
EXPECTED_DEV_TEST_ENVS = {
    "testing",
    "test",
    "development",
    "dev",
    "local",
}

#: Trading modes that must NEVER appear on the allow-list. Named explicitly rather than
#: left implied by the set above, because re-adding one is the exact regression API-03
#: fixed and it would otherwise read as a harmless addition.
FORBIDDEN_ON_ALLOW_LIST = {"safe", "paper", "live", "production", "staging", "prod"}


def _reload(monkeypatch, *, env=None, mode=None, redis_url=None, dev_mode=None):
    """Re-import the limiter under a specific environment.

    Every variable it reads is set or removed explicitly, so a value inherited from the
    developer's shell cannot decide the outcome.

    ``DEV_MODE`` was added to that list by API-03, which routed the limiter's environment
    decision through ``core/runtime_env.local_state_is_acceptable()`` -- a function that
    reads ``DEV_MODE``. Omitting it here was not harmless: ``tests/conftest.py`` sets
    ``DEV_MODE=true`` for the whole suite, so every case in this file silently ran with it
    set, and that is how the ``DEV_MODE``-overrides-``ENV`` fail-open went unnoticed by
    the API-03 suite while this file caught it.
    """
    for name in ("ENV", "VYOMQUANT_MODE", "AERORA_MODE", "REDIS_URL", "DEV_MODE"):
        monkeypatch.delenv(name, raising=False)
    if dev_mode is not None:
        monkeypatch.setenv("DEV_MODE", dev_mode)
    if env is not None:
        monkeypatch.setenv("ENV", env)
    if mode is not None:
        monkeypatch.setenv("VYOMQUANT_MODE", mode)
    if redis_url is not None:
        monkeypatch.setenv("REDIS_URL", redis_url)

    sys.modules.pop(MODULE, None)
    return importlib.import_module(MODULE)


# ---------------------------------------------------------------------------
# 1. Production refuses to come up without working Redis
# ---------------------------------------------------------------------------


class TestProductionFailsClosed:
    def test_production_without_redis_url_refuses_to_import(self, monkeypatch):
        """The property the audit finding claimed was missing."""
        with pytest.raises(RuntimeError) as caught:
            _reload(monkeypatch, env="production")

        message = str(caught.value)
        assert "REDIS_URL" in message
        assert "prohibited" in message.lower() or "fatal" in message.lower()

    def test_production_with_unreachable_redis_refuses_to_import(self, monkeypatch):
        """Set but dead is also fatal: the ping happens at import, not on first request."""
        with pytest.raises(RuntimeError) as caught:
            _reload(
                monkeypatch,
                env="production",
                # Port 1 is reserved and never listening; the socket timeout is 1s.
                redis_url="redis://127.0.0.1:1/0",
            )

        # API-03 rewrote the refusal message. Asserted on diagnostic content rather than
        # the old literal "rate limit storage": it must name the subsystem and say the
        # store is unreachable, so an operator reading one log line knows what to fix.
        message = str(caught.value).lower()
        assert "rate limit" in message
        assert "unreachable" in message

    def test_nothing_set_now_fails_closed_instead_of_resolving_to_safe(
        self, monkeypatch
    ):
        """UPDATED BY API-03. Silence used to mean ``safe``; it now means refuse.

        This test previously asserted ``module.env == "safe"`` and that ``"ambiguous"``
        was still a dead entry in ``DEV_TEST_ENVS``, recording as measured that::

            env_raw = os.getenv("ENV") or get_vyomquant_mode(default="safe")

        always produced a non-empty string, so the ``else: env = "ambiguous"`` branch
        could never run. Its own message said "the dead 'ambiguous' entry was removed
        from DEV_TEST_ENVS -- fine, but update this test".

        API-03 removed the ``get_vyomquant_mode`` read entirely: a trading mode says what
        the system may do with money and nothing about how many processes are running.
        With nothing set the environment is now the empty string, which is NOT on the
        allow-list, so the limiter refuses rather than silently choosing per-process
        counters. That is the whole point -- an unconfigured deployment is protected
        rather than guessed at.
        """
        with pytest.raises(RuntimeError) as caught:
            _reload(monkeypatch)

        message = str(caught.value)
        assert "REDIS_URL" in message
        assert "ENV=<unset>" in message, (
            f"the refusal should name the unset environment, got: {message}"
        )

    def test_an_unset_environment_is_not_called_safe_anywhere(self, monkeypatch):
        """The trading mode must not re-enter the environment decision by any route."""
        module = _reload(monkeypatch, env="development", mode="live")

        assert module.env == "development", (
            f"VYOMQUANT_MODE leaked back into the environment decision: {module.env!r}"
        )

    @pytest.mark.parametrize("env", ["production", "staging", "prod", "live"])
    def test_no_non_dev_environment_can_use_memory_storage(self, monkeypatch, env):
        if env in EXPECTED_DEV_TEST_ENVS:
            pytest.skip(f"{env!r} is a declared dev/test environment")

        with pytest.raises(RuntimeError):
            _reload(monkeypatch, env=env)

    @pytest.mark.parametrize("env", ["production", "staging", "prod", "live"])
    def test_dev_mode_cannot_override_a_declared_non_local_environment(
        self, monkeypatch, env
    ):
        """REGRESSION for a production fail-open found by the full suite.

        ``core/runtime_env.local_state_is_acceptable()`` read::

            if os.environ.get("DEV_MODE", "false") in _TRUTHY:
                return True
            return deployment_environment() in LOCAL_STATE_ENVS

        ``DEV_MODE`` short-circuited, so it was never compared against ``ENV``. A
        deployment with ``ENV=production`` and a leftover ``DEV_MODE=true`` therefore
        resolved to *local*: the limiter logged "explicitly local environment
        'production'" and used a per-process counter behind N replicas, with only a
        warning. One stale environment variable disabled the control.

        A declared non-local environment must win. The limiter must refuse here exactly
        as it does without ``DEV_MODE``.
        """
        with pytest.raises(RuntimeError) as caught:
            _reload(monkeypatch, env=env, dev_mode="true")

        assert "REDIS_URL" in str(caught.value)

    def test_dev_mode_still_works_where_the_environment_is_silent(self, monkeypatch):
        """POSITIVE CONTROL: the fix above must not make ``DEV_MODE`` useless.

        Without this, the previous test would also pass if ``DEV_MODE`` had simply been
        deleted from the implementation, and a developer with no ``ENV`` set could no
        longer start the application at all.
        """
        module = _reload(monkeypatch, dev_mode="true")

        assert module.limiter is not None
        assert module.config.local_allowed is True
        assert module.config.is_shared is False


# ---------------------------------------------------------------------------
# 2. The dev/test allow-list is exactly what it says
# ---------------------------------------------------------------------------


class TestTheDevTestAllowListIsDeclared:
    def test_the_allow_list_has_not_grown(self, monkeypatch):
        """A new entry here is a new environment permitted to run unlimited.

        Re-pointed by API-03: the allow-list moved from ``rate_limit.DEV_TEST_ENVS`` to
        ``core/runtime_env.LOCAL_STATE_ENVS``, so that the limiter and
        ``redis_manager`` -- which had answered the same question differently -- share
        one definition. The control is unchanged; only its address moved.
        """
        from backend_app.core import runtime_env

        assert set(runtime_env.LOCAL_STATE_ENVS) == EXPECTED_DEV_TEST_ENVS, (
            f"the local-state allow-list changed to "
            f"{sorted(runtime_env.LOCAL_STATE_ENVS)}; each entry is an environment "
            f"allowed to use per-process in-memory limits and a fake distributed lock"
        )

    def test_no_trading_mode_is_on_the_allow_list(self, monkeypatch):
        """The API-03 defect, pinned so it cannot return.

        ``safe`` and ``paper`` were on the old list. They are trading modes, and
        ``startup.sh`` resolves ``VYOMQUANT_MODE`` to ``paper`` when nothing sets it, so
        the documented production trading mode selected per-process rate limiting.
        """
        from backend_app.core import runtime_env

        overlap = set(runtime_env.LOCAL_STATE_ENVS) & FORBIDDEN_ON_ALLOW_LIST
        assert not overlap, (
            f"{sorted(overlap)} appear on the local-state allow-list. A trading mode is "
            f"not a deployment environment, and a production environment is not local."
        )

    def test_a_dev_environment_without_redis_uses_memory(self, monkeypatch):
        module = _reload(monkeypatch, env="test")

        assert module.limiter is not None

    def test_paper_mode_with_no_env_now_refuses(self, monkeypatch):
        """UPDATED BY API-03: the recorded gap this test pinned is CLOSED.

        It previously asserted ``module.env == "paper"`` and that a limiter was still
        initialised, recording that a deployment setting ``VYOMQUANT_MODE`` and not
        ``ENV`` would land on the allow-list via ``paper`` and get per-process limits.
        Its own message said: "if it now refuses, this recorded gap is closed and this
        test should go."

        It refuses. The test is kept rather than removed, inverted into the regression
        that the gap stays closed -- deleting it would leave nothing asserting that a
        trading mode cannot select the rate-limit backend.
        """
        with pytest.raises(RuntimeError) as caught:
            _reload(monkeypatch, mode="paper")

        message = str(caught.value)
        assert "ENV=<unset>" in message, (
            f"VYOMQUANT_MODE=paper still resolved to an environment: {message}"
        )


# ---------------------------------------------------------------------------
# 3. The requirement the storage choice is measured against
# ---------------------------------------------------------------------------


def test_the_spec_still_states_exact_rate_limits():
    """So "memory is fine" cannot be argued without re-reading the figures.

    A per-process limiter behind N workers admits roughly N times the configured rate,
    which is the difference between meeting these numbers and approximating them.
    """
    import pathlib

    spec = (
        pathlib.Path(__file__).resolve().parent.parent
        / ".kiro"
        / "specs"
        / "marketplace-subscriptions-paper-trading"
        / "requirements.md"
    )
    if not spec.is_file():
        pytest.skip("the spec is not present in this checkout")

    text = spec.read_text(encoding="utf-8", errors="replace")

    assert "at most 120 requests per 60-second window per authenticated caller" in text
    assert "SHALL rate-limit" in text


@pytest.fixture(autouse=True)
def _restore_module():
    """Leave the real module importable for everything that runs after this file.

    Each test reloads it under a patched environment; without this the last reload's
    version -- or no version at all, after a refusal -- would persist in
    ``sys.modules`` and every later importer would get it.
    """
    yield
    sys.modules.pop(MODULE, None)
    try:
        importlib.import_module(MODULE)
    except Exception:  # noqa: BLE001 - the ambient environment decides; not this file's business
        sys.modules.pop(MODULE, None)
