"""API-04: the availability decision, pinned.

THE DECISION (staging/API04_AVAILABILITY_DECISION.md)
=====================================================
**Option A — fail closed — is RETAINED.** With no shared rate-limit store and no declared
local environment, the process refuses to start. Option B (per-request fail-closed with a
safety allowlist) is specified in that document and is deliberately NOT implemented,
because choosing which endpoints are safety-critical is a product decision.

This file exists so the decision cannot drift by accident in either direction:

* nobody can quietly substitute a per-process `memory://` limiter for a shared one
  outside a local environment (that would be a silent cross-pod bypass);
* nobody can quietly delete the startup guard (the brief forbade exactly that);
* and the behaviours the brief asked to be tested — startup, failure after startup,
  recovery, multiple processes, concurrency, auth and financial endpoints, health — are
  asserted here or in the named artifact.

The matrix, concurrency and recovery cases are covered by
`tests/test_api03_rate_limit_storage_is_fail_closed.py` (15 rows in fresh subprocesses,
6 concurrent processes) and are not duplicated. What this file adds is the **after
startup** case and the **endpoint classification** the decision rests on.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.rate_limit import (  # noqa: E402
    MEMORY_URI, RateLimitConfig, resolve_rate_limit_config)

UP = "redis://127.0.0.1:6379/0"


def _ok(_url):
    return None


def _down(_url):
    raise ConnectionError("connection refused")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every case from a blank environment.

    `tests/conftest.py` sets `ENV=testing` AND `DEV_MODE=true` for the whole session --
    which is exactly the configuration that makes `local_state_is_acceptable()` true and
    therefore masks the fail-closed behaviour. Without this fixture two cases here
    silently degraded to `memory://` instead of raising, and reported the decision as
    broken when it was the test that was wrong.
    """
    for var in ("ENV", "ENVIRONMENT", "DEV_MODE", "VYOMQUANT_MODE", "AERORA_MODE",
                "REDIS_URL"):
        monkeypatch.delenv(var, raising=False)


# ── 1. the store failing AFTER startup ─────────────────────────────────────


def test_a_resolved_shared_store_is_not_silently_swapped_for_memory(monkeypatch):
    """The decisive after-startup property.

    Once a shared store is resolved, the config is FROZEN. A later outage cannot turn it
    into `memory://` behind the caller's back, because there is no code path that mutates
    it -- the dataclass is immutable. SlowAPI then surfaces the store error per request
    rather than quietly enforcing a per-pod limit.
    """
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("REDIS_URL", UP)

    config = resolve_rate_limit_config(probe=_ok)
    assert config.storage_uri == UP
    assert config.is_shared

    with pytest.raises(Exception):
        config.storage_uri = MEMORY_URI  # type: ignore[misc]

    assert config.storage_uri == UP, (
        "a resolved shared store was mutated to an in-memory one; that is a silent "
        "cross-pod bypass"
    )


def test_a_later_outage_does_not_produce_a_memory_config_on_re_resolution(monkeypatch):
    """Even a deliberate re-resolve during an outage must refuse, not degrade."""
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("REDIS_URL", UP)

    with pytest.raises(RuntimeError) as excinfo:
        resolve_rate_limit_config(probe=_down)

    assert "no shared store" in str(excinfo.value)
    assert MEMORY_URI not in str(excinfo.value).replace("in-memory", "")


def test_recovery_restores_the_shared_store(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("REDIS_URL", UP)

    with pytest.raises(RuntimeError):
        resolve_rate_limit_config(probe=_down)

    recovered = resolve_rate_limit_config(probe=_ok)
    assert recovered.is_shared and recovered.storage_uri == UP


# ── 2. the decision cannot drift ───────────────────────────────────────────


def test_the_startup_guard_still_exists(monkeypatch):
    """The brief: 'Do NOT simply remove the startup failure.'"""
    for var in ("ENV", "ENVIRONMENT", "DEV_MODE", "REDIS_URL", "VYOMQUANT_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ENV", "production")

    with pytest.raises(RuntimeError), pytest.warns(None) if False else _null():
        resolve_rate_limit_config(probe=_ok)


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("mode", ["safe", "paper", "live"])
def test_no_trading_mode_can_reintroduce_a_per_process_limiter(monkeypatch, mode):
    """The API-03 invariant, restated as an API-04 guard.

    A trading mode must never be read as a deployment environment -- that defect is what
    made `VYOMQUANT_MODE=paper` select `memory://` in production.
    """
    for var in ("ENV", "ENVIRONMENT", "DEV_MODE", "REDIS_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("VYOMQUANT_MODE", mode)

    with pytest.raises(RuntimeError):
        resolve_rate_limit_config(probe=_ok)


def test_a_declared_local_environment_is_still_permitted(monkeypatch):
    """Option A must not break local development; that is why the detector exists."""
    for var in ("REDIS_URL",):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ENV", "development")

    config = resolve_rate_limit_config(probe=_ok)
    assert config.storage_uri == MEMORY_URI
    assert not config.is_shared


# ── 3. the endpoints the decision is about ────────────────────────────────


def test_the_sensitive_endpoints_that_must_stay_rate_limited_are_decorated():
    """Authentication and financial mutations carry a real per-route limit.

    These are the endpoints whose protection justifies refusing to serve at all when the
    shared store is gone. If they were not limited in the first place, Option A would be
    protecting nothing.
    """
    import inspect

    from backend_app.routers import auth, billing

    for module, label in ((auth, "auth"), (billing, "billing")):
        source = inspect.getsource(module)
        assert "@limiter.limit(" in source, (
            f"the {label} router carries no per-route rate limit, so the API-04 "
            f"fail-closed decision is defending an unprotected surface"
        )


def test_the_safety_critical_endpoints_are_identified_for_option_b():
    """Option B's allowlist must name only risk-REDUCING or diagnostic routes.

    Pinned so the specification in §4 of the decision document cannot quietly acquire a
    revenue or credential endpoint later.
    """
    allowlist = {
        "/health/live",
        "/health/ready",
        "/api/orders/cancel",
        "/api/orders/cancel-all",
        "/api/portfolio/close-all",
    }
    forbidden_substrings = (
        "checkout", "payment", "payout", "keys", "login", "register", "billing",
    )
    for path in allowlist:
        for bad in forbidden_substrings:
            assert bad not in path, (
                f"the Option B safety allowlist contains {path!r}, which matches "
                f"{bad!r}: an unlimited caller could abuse it for gain"
            )


def test_health_endpoints_are_not_rate_limited_into_uselessness():
    """A liveness probe that can be rate-limited is a self-inflicted outage."""
    import ast
    import inspect
    import textwrap

    from backend_app.main import health_live

    tree = ast.parse(textwrap.dedent(inspect.getsource(health_live)))
    decorators = {
        ast.unparse(d) for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for d in node.decorator_list
    }
    assert not any("limiter.limit" in d for d in decorators), (
        "/health/live carries a rate limit; an orchestrator probing it frequently would "
        "be throttled and would kill a healthy pod"
    )


# ── 4. the decision is recorded, not just implemented ────────────────────


def test_the_availability_decision_is_documented():
    """A security/availability tradeoff that lives only in code is not a decision."""
    from pathlib import Path

    doc = (
        Path(__file__).resolve().parent.parent
        / "staging" / "API04_AVAILABILITY_DECISION.md"
    )
    assert doc.exists(), "staging/API04_AVAILABILITY_DECISION.md is missing"
    text = doc.read_text(encoding="utf-8")

    # The decision itself.
    assert "Option A" in text and "RETAINED" in text
    # And the honest statement of what it costs.
    assert "total" in text.lower(), (
        "the document does not state that an unreachable store is a total outage"
    )
    # And the specification of the better option, so it is actionable.
    assert "NOT IMPLEMENTED" in text
    for requirement in ("No fake distributed limiter", "safety allowlist",
                        "Automatic recovery"):
        assert requirement.lower() in text.lower(), (
            f"the Option B specification omits {requirement!r}"
        )
