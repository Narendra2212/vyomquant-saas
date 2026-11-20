"""A declared deployment environment outranks every secondary flag. Phase 6, section 8.

THE INVARIANT
-------------
    An explicitly declared production environment must never be downgraded to local/test
    semantics by a secondary flag.

RUNTIME-01 was one instance: ``core/runtime_env.local_state_is_acceptable()`` consulted
``DEV_MODE`` first and returned before it ever looked at ``ENV``, so ``ENV=production``
plus a leftover ``DEV_MODE=true`` selected a per-process rate-limit counter and a mock
cache behind the distributed lock. It was fixed and
``tests/test_rate_limiter_fails_closed_in_production.py`` guards it.

This file is the generalisation. Two more sites were measured with the same shape and are
covered here; a third is reported but deliberately not changed; and the last test is an
AST sweep that fails when a NEW site appears, because the point is to retire the class
rather than the three instances.

    1. ``routers/library.py:_get_service_client``   measured: ENV=production + DEV_MODE=true
    2. ``routers/referral.py:_get_service_client``  returned None from a function whose
                                                    docstring says it raises
    3. ``main.py:_get_docs_url`` / ``_get_redoc_url``
                                                    defaulted ``ENV`` from a TRADING MODE,
                                                    so ENV unset + VYOMQUANT_MODE=paper
                                                    served the interactive docs

Evidence: ``staging/probe_lib01_service_client.py`` (output in
``staging/raw_P7L3.txt``), ``staging/probe_env_precedence_inventory.py``
(``staging/raw_P7Q7.txt``, 265 read sites over 119 variables),
``staging/ENVIRONMENT_PRECEDENCE_AUDIT.md``.

THE CONFLICTING VARIABLE IS NEVER CLEARED
-----------------------------------------
Every negative case below sets ``DEV_MODE=true`` **and leaves it set** while asserting the
refusal. That is the whole point: the API-03 suite missed RUNTIME-01 precisely because it
deleted ``DEV_MODE`` before exercising the detector, so the conflict it was meant to
resolve was never present. ``_assert_dev_mode_is_still_set`` is called inside each
negative case so this file cannot regress into the same mistake silently.

WHAT IS NOT ASSERTED HERE
-------------------------
That ``_get_service_client`` stops returning ``None`` in local environments. It still
does, and its callers still dereference the result without checking -- that is LIB-01,
reported in ``staging/FULL_REGRESSION_FAILURE_DISPOSITION.md`` and owned by the library
router, not a precedence question. ``ENV=testing`` is on the local allow-list, so the
behaviour the rest of the suite sees is unchanged by this file's subject.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re

import pytest

from backend_app.core import runtime_env
from backend_app.routers import library as library_router
from backend_app.routers import referral as referral_router

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND_APP = REPO_ROOT / "backend_app"

#: The four the brief names. ``prod`` and ``live`` are included because an operator who
#: types either of them has still declared a non-local deployment, and ``staging`` because
#: a pre-production environment is a deployment with replicas like any other.
DECLARED_NON_LOCAL = ["production", "staging", "prod", "live"]

#: The positive controls. Without these, every assertion in this file could be satisfied by
#: deleting the ``DEV_MODE`` feature outright.
DECLARED_LOCAL = ["development", "dev", "local", "test", "testing"]

ENV_VARS = ("ENV", "ENVIRONMENT", "DEV_MODE", "VYOMQUANT_MODE", "AERORA_MODE")


def _assert_dev_mode_is_still_set() -> None:
    """The guard against this file making the mistake it exists to catch."""
    assert os.environ.get("DEV_MODE", "").strip().lower() == "true", (
        "This negative case must exercise the CONFLICT: DEV_MODE has to still be set to "
        "true at the moment the assertion runs. A test that clears it is asserting "
        "something else -- that is exactly how RUNTIME-01 survived 131 API-03 tests."
    )


@pytest.fixture
def clean_env(monkeypatch):
    """A blank slate for the variables under test, and a fresh service-client cache.

    ``tests/conftest.py`` sets ``ENV=testing`` and ``DEV_MODE=true`` process-wide, and both
    routers memoise their client in a module global, so without this a case would inherit
    the previous one's answer.
    """
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(library_router, "_service_client", None, raising=False)
    monkeypatch.setattr(referral_router, "_service_client", None, raising=False)
    yield monkeypatch
    library_router._service_client = None
    referral_router._service_client = None


# ─────────────────────────────────────────────────────────────────────────────
# The centralised rule itself
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("env", DECLARED_NON_LOCAL)
def test_a_declared_non_local_environment_beats_dev_mode(clean_env, env):
    clean_env.setenv("ENV", env)
    clean_env.setenv("DEV_MODE", "true")

    _assert_dev_mode_is_still_set()
    assert runtime_env.deployment_environment() == env
    assert runtime_env.dev_mode_requested() is True, "the flag IS requested"
    assert runtime_env.local_state_is_acceptable() is False, "and it is NOT granted"
    assert runtime_env.local_fallbacks_are_acceptable() is False


@pytest.mark.parametrize("env", DECLARED_NON_LOCAL)
def test_the_alias_cannot_drift_from_the_rule_it_delegates_to(clean_env, env):
    """``local_fallbacks_are_acceptable`` is a second NAME, not a second IMPLEMENTATION.

    Asserted over both answers rather than by reading the source, so a future edit that
    gives it its own body is caught by behaviour.
    """
    for dev_mode in ("true", "false"):
        clean_env.setenv("ENV", env)
        clean_env.setenv("DEV_MODE", dev_mode)
        assert (
            runtime_env.local_fallbacks_are_acceptable()
            == runtime_env.local_state_is_acceptable()
        )


@pytest.mark.parametrize("env", DECLARED_LOCAL)
def test_a_declared_local_environment_still_gets_local_semantics(clean_env, env):
    """POSITIVE CONTROL. The refusals above must not be reachable by always refusing."""
    clean_env.setenv("ENV", env)
    clean_env.setenv("DEV_MODE", "true")

    assert runtime_env.local_state_is_acceptable() is True
    assert runtime_env.local_fallbacks_are_acceptable() is True


def test_an_unset_environment_with_dev_mode_is_still_local(clean_env):
    """POSITIVE CONTROL. ``DEV_MODE`` keeps the one job it was always meant for."""
    clean_env.setenv("DEV_MODE", "true")

    assert runtime_env.deployment_environment() == ""
    assert runtime_env.local_state_is_acceptable() is True
    assert runtime_env.local_fallbacks_are_acceptable() is True


def test_an_unset_environment_with_no_dev_mode_fails_closed(clean_env):
    assert runtime_env.deployment_environment() == ""
    assert runtime_env.local_state_is_acceptable() is False
    assert runtime_env.local_fallbacks_are_acceptable() is False


@pytest.mark.parametrize("mode", ["safe", "paper", "live"])
def test_no_trading_mode_can_supply_the_deployment_environment(clean_env, mode):
    """A trading mode says what may be done with money, not how many processes exist."""
    clean_env.setenv("VYOMQUANT_MODE", mode)
    clean_env.setenv("AERORA_MODE", mode)

    assert runtime_env.deployment_environment() == ""
    assert runtime_env.local_state_is_acceptable() is False
    assert runtime_env.local_fallbacks_are_acceptable() is False


# ─────────────────────────────────────────────────────────────────────────────
# Site 1 and 2 -- the service-role clients
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("env", DECLARED_NON_LOCAL)
@pytest.mark.parametrize(
    "router", [library_router, referral_router], ids=["library", "referral"]
)
def test_a_missing_service_role_key_is_refused_not_degraded(clean_env, env, router):
    """The branch the audit measured returning ``None`` under ``ENV=production``."""
    clean_env.setenv("ENV", env)
    clean_env.setenv("DEV_MODE", "true")
    clean_env.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    clean_env.setenv("SUPABASE_URL", "https://example.supabase.co")

    _assert_dev_mode_is_still_set()
    with pytest.raises(RuntimeError) as excinfo:
        router._get_service_client()
    assert "SUPABASE_SERVICE_ROLE_KEY" in str(excinfo.value)


@pytest.mark.parametrize("env", DECLARED_NON_LOCAL)
def test_an_unconstructable_client_is_refused_not_degraded(clean_env, env, monkeypatch):
    """``library``'s second ``DEV_MODE`` branch: the one around ``create_client``.

    Both credentials are present and ``create_client`` fails anyway -- a malformed URL, an
    invalid key, an unreachable project. That is the branch the real failure took: the
    placeholder credentials in ``backend_app/.env`` are truthy, so the function reached
    ``create_client`` and got ``SupabaseException: Invalid API key``.
    """
    clean_env.setenv("ENV", env)
    clean_env.setenv("DEV_MODE", "true")
    clean_env.setenv("SUPABASE_URL", "https://example.supabase.co")
    clean_env.setenv("SUPABASE_SERVICE_ROLE_KEY", "not-a-real-key")

    boom = ValueError("Invalid API key")

    def explode(*_args, **_kwargs):
        raise boom

    monkeypatch.setattr(library_router, "create_client", explode)

    _assert_dev_mode_is_still_set()
    with pytest.raises(ValueError) as excinfo:
        library_router._get_service_client()
    assert excinfo.value is boom, "the original cause must propagate, not a substitute"


@pytest.mark.parametrize("env", DECLARED_LOCAL)
def test_a_local_environment_still_degrades_to_none(clean_env, env):
    """POSITIVE CONTROL, and the reason the rest of the suite is unaffected.

    ``tests/conftest.py`` sets ``ENV=testing``, which is on this list, so every
    ``/api/library`` test keeps the behaviour it had before this change. If this test ever
    fails, the precedence fix has leaked into the test environment and the failure is in
    THIS change, not in the suite.
    """
    clean_env.setenv("ENV", env)
    clean_env.setenv("DEV_MODE", "true")
    clean_env.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    assert library_router._get_service_client() is None
    assert referral_router._get_service_client() is None


def test_the_documented_contract_of_build_service_client_now_holds(clean_env):
    """``_build_service_client`` must RAISE for an unconfigured deployment, not return ``None``.

    It returned ``None`` instead -- measured in ``staging/raw_P7L3.txt``. What the route was
    not entitled to receive was a ``None`` it would dereference.

    UPDATED FOR THE LIB-01 REPAIR, AND THE PROPERTY IS UNCHANGED. This used to assert an
    ``HTTPException`` with ``status_code == 500``, which is what the function's old (and
    false) docstring promised. The refusal is now
    ``MarketplaceError(MARKETPLACE_READ_FAILED)`` -- the code ``get_categories`` in the same
    module already raises for exactly this condition, mapped to **503** by
    ``backend/marketplace/errors.py``. 503 is also the more truthful status: a deployment
    whose service-role credentials are absent has a read that cannot complete and may
    succeed on retry after reconfiguration, which is not a server fault in the 500 sense.

    The thing this test guards -- that absence is RAISED rather than handed back as a
    dereferenceable ``None`` -- is asserted exactly as before. Only the exception type moved,
    and it moved onto the module's existing error catalogue instead of a bare
    ``HTTPException``. ``MarketplaceError`` is deliberately NOT an ``HTTPException``
    subclass, which is why the old assertion no longer catches it.
    """
    from backend_app.backend.marketplace.errors import (HTTP_STATUS_FOR_CODE,
                                                        MARKETPLACE_READ_FAILED,
                                                        MarketplaceError)

    clean_env.setenv("ENV", "production")
    clean_env.setenv("DEV_MODE", "true")
    clean_env.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    clean_env.setenv("SUPABASE_URL", "https://example.supabase.co")

    _assert_dev_mode_is_still_set()
    with pytest.raises(MarketplaceError) as excinfo:
        library_router._build_service_client()

    assert excinfo.value.code == MARKETPLACE_READ_FAILED
    assert HTTP_STATUS_FOR_CODE[MARKETPLACE_READ_FAILED] == 503


# ─────────────────────────────────────────────────────────────────────────────
# Site 3 -- the interactive documentation URLs
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("env", DECLARED_NON_LOCAL)
def test_the_interactive_docs_are_withheld_from_a_declared_deployment(clean_env, env):
    import backend_app.main as main_module

    clean_env.setenv("ENV", env)
    clean_env.setenv("DEV_MODE", "true")
    clean_env.setenv("VYOMQUANT_MODE", "paper")

    _assert_dev_mode_is_still_set()
    assert main_module._interactive_docs_allowed() is False
    assert main_module._get_docs_url() is None
    assert main_module._get_redoc_url() is None


@pytest.mark.parametrize("mode", ["safe", "paper", "live"])
def test_a_trading_mode_cannot_decide_whether_docs_are_served(clean_env, mode):
    """The exact pre-fix expression was ``os.getenv("ENV", get_vyomquant_mode(...))``.

    With ``ENV`` unset, ``VYOMQUANT_MODE=paper`` -- the value every production task
    definition in this repository sets -- made ``is_prod`` false and served ``/docs``.
    """
    import backend_app.main as main_module

    clean_env.setenv("VYOMQUANT_MODE", mode)

    assert main_module._get_docs_url() is None
    assert main_module._get_redoc_url() is None


def test_docs_are_still_served_on_a_declared_developer_machine(clean_env):
    """POSITIVE CONTROL. Withholding them everywhere would satisfy the tests above."""
    import backend_app.main as main_module

    clean_env.setenv("ENV", "development")

    assert main_module._interactive_docs_allowed() is True
    assert main_module._get_docs_url() == "/docs"
    assert main_module._get_redoc_url() == "/redoc"


@pytest.mark.parametrize("env", DECLARED_NON_LOCAL + DECLARED_LOCAL)
def test_an_explicit_operator_override_outranks_the_inference(clean_env, env):
    """``ENABLE_DOCS`` is consulted first, in both directions, by design.

    An operator who names the behaviour has made a decision; an inference has only made a
    guess. Kept from the original implementation deliberately -- the change was to the
    guess, not to the override.
    """
    import backend_app.main as main_module

    clean_env.setenv("ENV", env)

    clean_env.setenv("ENABLE_DOCS", "true")
    assert main_module._get_docs_url() == "/docs"

    clean_env.setenv("ENABLE_DOCS", "false")
    assert main_module._get_docs_url() is None

    clean_env.delenv("ENABLE_DOCS", raising=False)


# ─────────────────────────────────────────────────────────────────────────────
# The class, not the three instances
# ─────────────────────────────────────────────────────────────────────────────

#: ``DEV_MODE`` read sites that do NOT need to consult ``ENV``, each with the reason.
#: A site qualifies only if it cannot change what the process is ALLOWED to do.
_DEV_MODE_SITES_THAT_DECIDE_NOTHING = {
    # The one place the judgement lives. It reads DEV_MODE *in order to* compare it
    # against ENV, so requiring it to consult ENV in the same function is circular.
    "backend_app/core/runtime_env.py": "defines the rule",
    # Module-level captures into a constant. The constant's USERS are what matter, and
    # each of those is either env-aware or on this list in its own right.
    "backend_app/core/config.py": "module-level capture into settings",
    "backend_app/core/state.py": "module-level capture; _is_production_safe is env-aware",
    "backend_app/core/dependencies.py": "module-level capture; _is_production_safe is env-aware",
    "backend_app/core/cache/redis_manager.py": "module-level capture; the cache decision "
                                               "itself goes through runtime_env",
    # Reporting only. /health and /readiness echo the flag so an operator can see it; they
    # grant nothing.
    "backend_app/main.py": "reported in the health and readiness payloads, not acted on",
}


def _dev_mode_read_sites() -> list[tuple[str, int, str, bool]]:
    """Every ``DEV_MODE`` read under ``backend_app``, with its function's env-awareness."""
    found: list[tuple[str, int, str, bool]] = []

    for path in sorted(BACKEND_APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue

        functions = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

        for func in functions:
            source = ast.unparse(func)
            if "DEV_MODE" not in source:
                continue
            env_aware = bool(re.search(r"""['"](?:ENV|ENVIRONMENT)['"]""", source))
            for node in ast.walk(func):
                if isinstance(node, ast.Constant) and node.value == "DEV_MODE":
                    found.append(
                        (
                            path.relative_to(REPO_ROOT).as_posix(),
                            node.lineno,
                            func.name,
                            env_aware,
                        )
                    )

        # Module level: anything not inside one of the functions above.
        in_function = {id(n) for func in functions for n in ast.walk(func)}
        for node in ast.walk(tree):
            if id(node) in in_function:
                continue
            if isinstance(node, ast.Constant) and node.value == "DEV_MODE":
                found.append(
                    (
                        path.relative_to(REPO_ROOT).as_posix(),
                        node.lineno,
                        "<module>",
                        False,
                    )
                )

    return found


def test_no_site_decides_behaviour_from_dev_mode_without_consulting_the_environment():
    """The regression that retires the CLASS.

    A function may read ``DEV_MODE`` only if it also reads ``ENV``/``ENVIRONMENT`` -- so
    the declaration can win -- or if it appears on
    :data:`_DEV_MODE_SITES_THAT_DECIDE_NOTHING` with a stated reason. Anything else is a
    new flag that grants local semantics without knowing where it is running, which is
    RUNTIME-01 reopening under a different name.

    Deliberately an AST sweep rather than three fixed call sites: the two router fixes in
    this file were found by inventory, not by reasoning, and a fourth site would be found
    the same way or not at all.
    """
    sites = _dev_mode_read_sites()
    assert sites, "the sweep found nothing, so it is measuring nothing"

    offenders = [
        f"{path}:{lineno} ({func})"
        for path, lineno, func, env_aware in sites
        if not env_aware and path not in _DEV_MODE_SITES_THAT_DECIDE_NOTHING
    ]

    assert not offenders, (
        "These read DEV_MODE without consulting the declared environment:\n  "
        + "\n  ".join(offenders)
        + "\n\nRoute the decision through core/runtime_env.py "
        "(local_state_is_acceptable / local_fallbacks_are_acceptable) so a declared "
        "production environment cannot be downgraded by a leftover flag, or add the file "
        "to _DEV_MODE_SITES_THAT_DECIDE_NOTHING with the reason it grants nothing."
    )


def test_no_site_defaults_the_deployment_environment_from_a_trading_mode():
    """The API-03 defect shape, swept for across the whole package.

    ``os.getenv("ENV", get_vyomquant_mode(...))`` is the specific expression that let a
    trading mode stand in for a deployment environment in ``core/rate_limit.py`` and, until
    this change, in ``main.py``'s docs URLs. It is matched structurally: an ``ENV`` read
    whose DEFAULT argument mentions a trading-mode source.
    """
    trading_mode_sources = ("get_vyomquant_mode", "VYOMQUANT_MODE", "AERORA_MODE")
    offenders: list[str] = []

    for path in sorted(BACKEND_APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            callee = node.func
            is_env_read = (
                isinstance(callee, ast.Attribute)
                and callee.attr in {"getenv", "get"}
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in {"ENV", "ENVIRONMENT"}
            )
            if not is_env_read:
                continue
            default = ast.unparse(node.args[1])
            if any(source in default for source in trading_mode_sources):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno} "
                    f"-> default {default}"
                )

    assert not offenders, (
        "A trading mode is supplying the default for a deployment environment:\n  "
        + "\n  ".join(offenders)
        + "\n\nVYOMQUANT_MODE/AERORA_MODE are safe|paper|live -- what the system may do "
        "with money. They say nothing about which deployment this is. Use "
        "core/runtime_env.deployment_environment()."
    )
