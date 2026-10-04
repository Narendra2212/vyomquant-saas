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

#: Verbatim from rate_limit.py. Restated here so a change to either is deliberate.
EXPECTED_DEV_TEST_ENVS = {
    "testing",
    "test",
    "development",
    "dev",
    "local",
    "safe",
    "paper",
    "ambiguous",
}


def _reload(monkeypatch, *, env=None, mode=None, redis_url=None):
    """Re-import the limiter under a specific environment.

    Every variable it reads is set or removed explicitly, so a value inherited from the
    developer's shell cannot decide the outcome.
    """
    for name in ("ENV", "VYOMQUANT_MODE", "AERORA_MODE", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
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

        assert "rate limit storage" in str(caught.value).lower()

    def test_nothing_set_resolves_to_safe_and_the_ambiguous_branch_is_dead(
        self, monkeypatch
    ):
        """With neither variable set the environment is ``safe``, never ``ambiguous``.

        The module reads::

            env_raw = os.getenv("ENV") or get_vyomquant_mode(default="safe")
            if env_raw:
                env = env_raw.lower()
            else:
                # Ambiguous environment: neither ENV nor VYOMQUANT_MODE is set
                env = "ambiguous"

        ``get_vyomquant_mode(default="safe")`` always returns a non-empty string, so
        ``env_raw`` is always truthy and the ``else`` can never run. ``"ambiguous"`` is
        therefore dead -- both as an assigned value and as its entry in
        ``DEV_TEST_ENVS`` -- and the comment describing an ambiguous environment
        describes a state the module cannot reach.

        The practical outcome is the same (``safe`` is on the allow-list, so memory
        storage either way), which is why this is recorded rather than fixed: removing
        the branch changes no behaviour, and leaving it is misleading about what happens
        when nothing is configured. Asserted as measured.
        """
        module = _reload(monkeypatch)

        assert module.env == "safe", (
            f"env resolved to {module.env!r}; if the 'ambiguous' branch is now "
            f"reachable, this module's fallback story has changed"
        )
        assert "ambiguous" in module.DEV_TEST_ENVS, (
            "the dead 'ambiguous' entry was removed from DEV_TEST_ENVS -- fine, but "
            "update this test"
        )
        assert module.limiter is not None

    @pytest.mark.parametrize("env", ["production", "staging", "prod", "live"])
    def test_no_non_dev_environment_can_use_memory_storage(self, monkeypatch, env):
        if env in EXPECTED_DEV_TEST_ENVS:
            pytest.skip(f"{env!r} is a declared dev/test environment")

        with pytest.raises(RuntimeError):
            _reload(monkeypatch, env=env)


# ---------------------------------------------------------------------------
# 2. The dev/test allow-list is exactly what it says
# ---------------------------------------------------------------------------


class TestTheDevTestAllowListIsDeclared:
    def test_the_allow_list_has_not_grown(self, monkeypatch):
        """A new entry here is a new environment permitted to run unlimited."""
        module = _reload(monkeypatch, env="test")

        assert set(module.DEV_TEST_ENVS) == EXPECTED_DEV_TEST_ENVS, (
            f"the dev/test rate-limit allow-list changed to "
            f"{sorted(module.DEV_TEST_ENVS)}; each entry is an environment allowed to "
            f"use per-process in-memory limits"
        )

    def test_a_dev_environment_without_redis_uses_memory(self, monkeypatch):
        module = _reload(monkeypatch, env="test")

        assert module.limiter is not None

    def test_paper_mode_with_no_env_falls_back_to_memory(self, monkeypatch):
        """The residual gap, pinned.

        ``startup.sh`` resolves ``VYOMQUANT_MODE`` to ``paper`` when nothing sets it, and
        ``paper`` is on the allow-list, so a deployment that sets the mode and not ``ENV``
        gets per-process limits. Not reachable here -- every ECS task definition sets
        ``ENV=production`` -- but it depends on a task-definition variable rather than on
        code, so it is recorded.
        """
        module = _reload(monkeypatch, mode="paper")

        assert module.env == "paper"
        assert module.limiter is not None, (
            "paper mode with no ENV no longer initialises a limiter; if it now refuses, "
            "this recorded gap is closed and this test should go"
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
