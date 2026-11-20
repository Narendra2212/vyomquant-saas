"""API-05 and ENV-06 — documentation exposure, and the two dimensions Sentry confused.

API-05: WHAT WAS WRONG
----------------------
``main._get_openapi_url()`` consulted no environment variable at all::

    enable_openapi = os.getenv("ENABLE_OPENAPI")
    if enable_openapi is not None:
        return "/openapi.json" if enable_openapi.lower() in ("true", "1", "yes") else None
    return "/openapi.json"

and no task definition in this repository sets ``ENABLE_OPENAPI`` (checked:
``ecs-task-definition-full.json``, ``new_task_def.json``, ``td15.json``,
``infra/rollback/taskdef-vyomquant-api-145-pre-domain.json``). So production hid ``/docs``
and ``/redoc`` — correctly, after the ENV-05 fix — and published the schema those pages are
rendered from, which enumerates every path, method, parameter and model in the application.
The hiding and the publishing withheld and disclosed the same surface.

THE POLICY, DETERMINED
----------------------
Two surfaces, named apart:

    /docs, /redoc      the INTERACTIVE, browser-executable consoles
    /openapi.json      the MACHINE-READABLE SCHEMA those consoles render from

They are governed by one rule — ``runtime_env.local_state_is_acceptable()`` — with an
explicit operator override per surface (``ENABLE_DOCS``, ``ENABLE_OPENAPI``) consulted
first, in both directions.

DOCUMENTATION IS NOT DISABLED EVERYWHERE. A declared development or test environment, and
any machine that says ``DEV_MODE=true``, serves all three exactly as before. The change is
production and staging, and only by default.

WHY IT WAS SAFE TO CHANGE NOW. The earlier pass deferred this because unmounting a schema
can break a generated client. Measured since: nothing in this repository fetches
``/openapi.json`` over HTTP — the frontend mentions it only in comments, no CI workflow
references it, and ``tests/test_route_contract.py`` reads ``app.openapi()`` in process.
CloudFront distribution EEOXECPHQ8SR0 does carry an explicit ``/openapi.json`` cache
behaviour and two middlewares list the path as public, but those are routing and auth facts
that predate the ENV-05 change: they exist because ``/docs`` used to be served in
production. The CDN behaviour is left in place and now forwards to a 404;
``ENABLE_OPENAPI=true`` restores the old behaviour with one variable.

ENV-06: WHAT WAS WRONG
----------------------
``main.lifespan`` called::

    initialize_sentry(dsn=..., environment=get_vyomquant_mode("production"), release=...)

A TRADING MODE supplying Sentry's ``environment`` tag. With the documented production
setting ``VYOMQUANT_MODE=paper``, every production event was filed under an environment
called ``paper`` — mixed in with developer machines in paper mode — and no environment
called ``production`` existed for an alert rule, a release-health query or an issue filter
to select. Same substitution as API-03 (``core/rate_limit``) and the ``redis_manager`` fix,
on the reporting side.

    deployment environment   development / testing / staging / production
    trading mode             safe / paper / live

Both are now recorded, independently: ``environment`` from
``runtime_env.deployment_environment()``, and the trading mode as its own global Sentry
tag. Nothing is lost by separating them, which is the point — an event in
``environment=production, trading_mode=paper`` is a different thing from one in
``environment=development, trading_mode=paper``, and one tag could not say which.
"""

import ast
import itertools
import json
import os
import pathlib

import pytest

import backend_app.main as main_module
from backend_app.backend.observability import sentry_config
from backend_app.core import runtime_env

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Deployment environments that are NOT a developer machine. ``prod`` and ``live`` are
#: included because an operator who types either has still declared a deployment, and
#: ``staging`` because a pre-production environment is a deployment like any other.
#: Matches ``tests/test_environment_precedence_is_centralised.py``'s list deliberately —
#: the same vocabulary, not a second one.
DECLARED_NON_LOCAL = ["production", "staging", "prod", "live"]

#: The positive controls. Without these, every assertion below could be satisfied by
#: withholding the documentation everywhere, which the brief explicitly forbids.
DECLARED_LOCAL = ["development", "dev", "local", "test", "testing"]

TRADING_MODES = ["safe", "paper", "live"]

#: Everything that must be cleared so a cell states only what it is about.
ENV_VARS = (
    "ENV",
    "ENVIRONMENT",
    "DEV_MODE",
    "VYOMQUANT_MODE",
    "AERORA_MODE",
    "ENABLE_DOCS",
    "ENABLE_OPENAPI",
)


@pytest.fixture
def clean_env(monkeypatch):
    """A blank slate for every variable in the matrix.

    ``tests/conftest.py`` sets ``ENV=testing`` and ``DEV_MODE=true`` process-wide, so
    without this each cell would inherit a local environment and every negative case would
    pass for the wrong reason.
    """
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _urls():
    """The three decisions, as a dict, read live."""
    return {
        "docs": main_module._get_docs_url(),
        "redoc": main_module._get_redoc_url(),
        "openapi": main_module._get_openapi_url(),
    }


# ══════════════════════════════════════════════════════════════════════════════════════════
#  1. THE COMPLETE ENVIRONMENT MATRIX
# ══════════════════════════════════════════════════════════════════════════════════════════

#: Every (ENV, DEV_MODE, trading mode) combination the brief's "complete environment
#: matrix" covers: 10 environments (9 declared + unset) × 2 DEV_MODE × 3 trading modes = 60
#: cells, each asserting all three URLs. The expected answer is computed from the one rule
#: rather than tabulated by hand, because a hand-written table of 60 rows is a second
#: implementation of the policy and would drift from it.
MATRIX = list(
    itertools.product(
        DECLARED_NON_LOCAL + DECLARED_LOCAL + [None],
        ["true", "false"],
        TRADING_MODES,
    )
)


@pytest.mark.parametrize("env,dev_mode,trading_mode", MATRIX)
def test_the_complete_environment_matrix(clean_env, env, dev_mode, trading_mode):
    """All three documentation URLs, across every environment and trading mode.

    The expected value is ``local_state_is_acceptable()`` — the single precedence rule
    (RUNTIME-01) — and the assertion is that all three surfaces agree with it. That is the
    whole policy: the schema follows the pages it feeds, and neither follows the trading
    mode.
    """
    if env is not None:
        clean_env.setenv("ENV", env)
    clean_env.setenv("DEV_MODE", dev_mode)
    clean_env.setenv("VYOMQUANT_MODE", trading_mode)

    local = runtime_env.local_state_is_acceptable()
    urls = _urls()

    if local:
        assert urls == {
            "docs": "/docs",
            "redoc": "/redoc",
            "openapi": "/openapi.json",
        }, f"ENV={env!r} DEV_MODE={dev_mode} VYOMQUANT_MODE={trading_mode}: {urls}"
    else:
        assert urls == {
            "docs": None,
            "redoc": None,
            "openapi": None,
        }, f"ENV={env!r} DEV_MODE={dev_mode} VYOMQUANT_MODE={trading_mode}: {urls}"


@pytest.mark.parametrize("env", DECLARED_NON_LOCAL)
@pytest.mark.parametrize("trading_mode", TRADING_MODES)
def test_the_schema_is_withheld_wherever_the_console_is(clean_env, env, trading_mode):
    """THE DEFECT, stated directly.

    Before this change the right-hand column was ``"/openapi.json"`` in every one of these
    twelve cells, while ``docs`` and ``redoc`` were already ``None``. ``DEV_MODE=true`` is
    left SET on purpose: a declared non-local environment must beat it (RUNTIME-01), and a
    test that cleared it would be asserting something else — which is exactly how RUNTIME-01
    survived 131 API-03 tests.
    """
    clean_env.setenv("ENV", env)
    clean_env.setenv("DEV_MODE", "true")
    clean_env.setenv("VYOMQUANT_MODE", trading_mode)

    assert os.environ["DEV_MODE"] == "true", "this cell must exercise the conflict"
    assert main_module._interactive_docs_allowed() is False
    assert main_module._get_openapi_url() is None, (
        "the schema the hidden /docs page renders from is still published"
    )


@pytest.mark.parametrize("env", DECLARED_LOCAL)
def test_documentation_is_not_disabled_everywhere(clean_env, env):
    """POSITIVE CONTROL, and the brief's explicit constraint.

    "Do not automatically disable documentation everywhere." A declared development or test
    environment is unchanged — all three surfaces served.
    """
    clean_env.setenv("ENV", env)
    assert _urls() == {
        "docs": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
    }


def test_dev_mode_alone_still_serves_everything(clean_env):
    """A machine that declares no environment but says ``DEV_MODE=true`` is a developer
    machine, and keeps its documentation. Unchanged from before."""
    clean_env.setenv("DEV_MODE", "true")
    assert _urls() == {
        "docs": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
    }


def test_an_undeclared_environment_fails_closed(clean_env):
    """``ENV`` unset and no ``DEV_MODE``: nothing is served.

    Fail-closed is the same direction ``runtime_env`` takes everywhere else. The fix for a
    developer machine is to SAY so, not to make silence mean local.
    """
    assert _urls() == {"docs": None, "redoc": None, "openapi": None}


# ══════════════════════════════════════════════════════════════════════════════════════════
#  2. A TRADING MODE DECIDES NOTHING
# ══════════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mode", TRADING_MODES)
@pytest.mark.parametrize("variable", ["VYOMQUANT_MODE", "AERORA_MODE"])
def test_a_trading_mode_cannot_decide_what_is_published(clean_env, variable, mode):
    """With ``ENV`` unset, the pre-ENV-05 expression was
    ``os.getenv("ENV", get_vyomquant_mode("development"))``, so ``VYOMQUANT_MODE=paper`` —
    the value every production task definition sets — served the docs.

    Both the current name and the deprecated ``AERORA_MODE`` alias are covered, because
    ``get_vyomquant_mode`` still reads the alias.
    """
    clean_env.setenv(variable, mode)
    assert _urls() == {"docs": None, "redoc": None, "openapi": None}


def test_no_documentation_helper_reads_a_trading_mode():
    """Structural, so a future edit cannot reintroduce the substitution.

    None of the three URL helpers nor ``_interactive_docs_allowed`` may mention a trading
    mode variable or ``get_vyomquant_mode`` anywhere in their bodies.
    """
    tree = ast.parse(
        (REPO_ROOT / "backend_app" / "main.py").read_text(
            encoding="utf-8", errors="replace"
        )
    )
    forbidden = ("VYOMQUANT_MODE", "AERORA_MODE", "get_vyomquant_mode")
    targets = {
        "_interactive_docs_allowed",
        "_get_docs_url",
        "_get_redoc_url",
        "_get_openapi_url",
    }

    checked = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in targets:
            continue
        checked.add(node.name)
        # The docstrings discuss the defect by name, so only executable statements are read.
        body = [n for n in node.body if not isinstance(n, ast.Expr)]
        source = "\n".join(ast.unparse(n) for n in body)
        for name in forbidden:
            assert name not in source, (
                f"main.{node.name} consults {name}; a trading mode is not a deployment "
                f"environment"
            )

    assert checked == targets, f"not all helpers were found: missing {targets - checked}"


# ══════════════════════════════════════════════════════════════════════════════════════════
#  3. THE EXPLICIT OPERATOR OVERRIDES
# ══════════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("env", DECLARED_NON_LOCAL + DECLARED_LOCAL)
def test_enable_openapi_overrides_the_inference_in_both_directions(clean_env, env):
    """The escape hatch, and the reason this change is safe to make by default.

    A deployed environment that genuinely has a schema consumer — a generated client, a
    contract test, an API-gateway import — restores the previous behaviour with one
    variable. An operator who names the behaviour outranks an inference.
    """
    clean_env.setenv("ENV", env)

    clean_env.setenv("ENABLE_OPENAPI", "true")
    assert main_module._get_openapi_url() == "/openapi.json"

    clean_env.setenv("ENABLE_OPENAPI", "false")
    assert main_module._get_openapi_url() is None


@pytest.mark.parametrize("env", DECLARED_NON_LOCAL + DECLARED_LOCAL)
def test_the_two_overrides_are_independent(clean_env, env):
    """``ENABLE_DOCS`` governs the consoles and ``ENABLE_OPENAPI`` the schema.

    Publishing the schema for a machine consumer while keeping the browser console off is a
    legitimate configuration, and it has to be expressible. One combined switch could not
    say it.
    """
    clean_env.setenv("ENV", env)
    clean_env.setenv("ENABLE_DOCS", "false")
    clean_env.setenv("ENABLE_OPENAPI", "true")

    assert main_module._get_docs_url() is None
    assert main_module._get_redoc_url() is None
    assert main_module._get_openapi_url() == "/openapi.json"


def test_no_task_definition_sets_enable_openapi_so_the_default_is_what_ships(clean_env):
    """The default is the behaviour, because nothing overrides it.

    Recorded as a measurement rather than a claim: if a future task definition starts
    setting ``ENABLE_OPENAPI``, this fails and whoever added it has to decide deliberately.
    """
    task_definitions = [
        "ecs-task-definition-full.json",
        "new_task_def.json",
        "td15.json",
        "infra/rollback/taskdef-vyomquant-api-145-pre-domain.json",
    ]

    def load(path):
        raw = path.read_bytes()
        for bom, encoding in (
            (b"\xff\xfe", "utf-16-le"),
            (b"\xfe\xff", "utf-16-be"),
            (b"\xef\xbb\xbf", "utf-8-sig"),
        ):
            if raw.startswith(bom):
                return raw.decode(encoding).lstrip("\ufeff")
        return raw.decode("utf-8", errors="replace")

    seen = 0
    for relative in task_definitions:
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        seen += 1
        text = load(path)
        assert "ENABLE_OPENAPI" not in text, (
            f"{relative} now sets ENABLE_OPENAPI; the default this test documents is no "
            f"longer what ships"
        )
        # Sanity: the parser really read the file.
        assert json.loads(text), f"{relative} did not parse"

    assert seen >= 3, f"only {seen} task definitions were read"


# ══════════════════════════════════════════════════════════════════════════════════════════
#  4. ENV-06 — TWO DIMENSIONS, RECORDED INDEPENDENTLY
# ══════════════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sentry_spy(monkeypatch):
    """Capture what ``initialize_sentry`` would send to the SDK, without initialising it.

    Replaces ``sentry_sdk.init`` and ``sentry_sdk.set_tag`` on the module under test, and
    forces ``SENTRY_AVAILABLE`` on, so the body runs on a machine where the SDK may be
    absent. Nothing is sent anywhere.
    """
    captured = {"init": None, "tags": {}}

    def fake_init(**kwargs):
        captured["init"] = kwargs

    def fake_set_tag(key, value):
        captured["tags"][key] = value

    if not hasattr(sentry_config, "sentry_sdk") or sentry_config.sentry_sdk is None:
        pytest.skip("sentry_sdk is not importable in this environment")

    monkeypatch.setattr(sentry_config, "SENTRY_AVAILABLE", True, raising=False)
    monkeypatch.setattr(sentry_config.sentry_sdk, "init", fake_init)
    monkeypatch.setattr(sentry_config.sentry_sdk, "set_tag", fake_set_tag)
    return captured


@pytest.mark.parametrize("trading_mode", TRADING_MODES)
@pytest.mark.parametrize("env", ["production", "staging", "development", "testing"])
def test_sentry_records_the_deployment_environment_and_the_trading_mode_separately(
    sentry_spy, env, trading_mode
):
    """THE DEFECT: ``environment`` was the trading mode.

    Both dimensions must survive, and they must not be the same field. Twelve cells, and in
    every one ``environment`` is the deployment environment and ``trading_mode`` is the
    trading mode.
    """
    result = sentry_config.initialize_sentry(
        dsn="https://examplepublickey@o0.ingest.sentry.io/0",
        environment=env,
        release="2.4.0",
        trading_mode=trading_mode,
    )

    assert result is True
    assert sentry_spy["init"]["environment"] == env
    assert sentry_spy["tags"]["trading_mode"] == trading_mode
    # And the environment is never a trading mode.
    assert sentry_spy["init"]["environment"] not in TRADING_MODES or env in TRADING_MODES


def test_an_undeclared_environment_is_reported_as_unknown_not_as_production(sentry_spy):
    """The default used to be ``"production"``.

    A caller that does not say where it runs should produce a visibly unlabelled stream
    rather than a convincing-looking production one. An alert rule cannot tell a
    mislabelled event from a real one; it can tell ``unknown``.
    """
    sentry_config.initialize_sentry(
        dsn="https://examplepublickey@o0.ingest.sentry.io/0",
        release="2.4.0",
    )
    assert sentry_spy["init"]["environment"] == "unknown"


def test_startup_passes_the_deployment_environment_not_the_trading_mode():
    """A correct function called with the wrong argument is still the defect.

    Parsed out of ``main.lifespan``: the ``initialize_sentry`` call's ``environment``
    keyword must be ``deployment_environment()``, and its ``trading_mode`` keyword must be
    where ``get_vyomquant_mode`` appears — not the other way round.
    """
    tree = ast.parse(
        (REPO_ROOT / "backend_app" / "main.py").read_text(
            encoding="utf-8", errors="replace"
        )
    )
    lifespan = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "lifespan"
    )

    call = next(
        n
        for n in ast.walk(lifespan)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "initialize_sentry"
    )
    keywords = {k.arg: ast.unparse(k.value) for k in call.keywords}

    assert "environment" in keywords, "startup does not name the Sentry environment"
    assert "get_vyomquant_mode" not in keywords["environment"], (
        "a trading mode is supplying Sentry's deployment environment again: "
        f"environment={keywords['environment']}"
    )
    assert "deployment_environment()" in keywords["environment"]

    assert "trading_mode" in keywords, (
        "the trading mode is no longer recorded at all; separating the dimensions must not "
        "lose one of them"
    )
    assert "get_vyomquant_mode" in keywords["trading_mode"]


def test_nothing_else_passes_a_trading_mode_as_a_deployment_environment():
    """The class of defect, not this one instance.

    Any ``environment=`` keyword anywhere under ``backend_app/`` whose value mentions
    ``get_vyomquant_mode`` is the same substitution in a different place.
    """
    offenders = []
    for path in sorted(REPO_ROOT.glob("backend_app/**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "environment":
                    continue
                value = ast.unparse(keyword.value)
                if "get_vyomquant_mode" in value or "VYOMQUANT_MODE" in value:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno} "
                        f"environment={value}"
                    )

    assert offenders == [], (
        "a trading mode is being passed as a deployment environment: " + ", ".join(offenders)
    )
