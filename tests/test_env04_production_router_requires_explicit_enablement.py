"""ENV-04 — omission must not mount an execution surface.

WHAT WAS WRONG
--------------
``PRODUCTION_ROUTER_ENABLED`` was computed by two identical lines in two modules::

    # core/safety_config.py:131   AND   core/feature_flags.py:39
    PRODUCTION_ROUTER_ENABLED: bool = os.getenv("PRODUCTION_ROUTER_ENABLED", "True").lower() == "true"

The default was ``"True"``, so the flag's ABSENCE mounted ``execution_router`` at
``/api/execution`` (``main.py``). That is the fail-OPEN direction for a switch that exists
to keep an execution surface off, and "absent" is the normal state of a new environment, a
rollback to an older task definition, or any local process.

Seven sources disagreed about the answer:

    core/feature_flags.py docstring ......... "PRODUCTION_ROUTER_ENABLED: False
                                               (unverified safety)"
    the code one line below it .............. default "True"
    ecs-task-definition-full.json ........... "False"
    new_task_def.json ....................... "False"
    td15.json ............................... "False"
    infra/rollback/taskdef-...-145-....json . "False"
    .env.example ............................ False
    deployment_go_no_go.md .................. True
    safety_config.validate_safety_config_integrity() — raised in production unless the
        router was ENABLED, i.e. demanded the exact opposite, and had zero call sites

Observed live in this audit's own process output:
``INFO:Algo22: Production execution router ENABLED (verify safety before production)``.

THE POLICY NOW
--------------
One definition — ``safety_config.production_router_enabled()``. Off unless
``PRODUCTION_ROUTER_ENABLED=true`` is set explicitly. ``ENV`` does not enter into it, so
there is one behaviour to reason about rather than three, and unset means off in
development, staging and production alike.

WHAT IS ACTUALLY BEHIND THE FLAG, STATED ACCURATELY
---------------------------------------------------
``execution_router`` exposes one endpoint, ``POST /api/execution/signal``, and it
constructs ``ExecutionEngine()`` with no ``portfolio_state`` on the line BEFORE its
``try`` — so it raises ``ValueError`` and returns 500 on every request without reaching a
venue (measured in ``tests/test_execution_paths_that_claim_an_exchange.py``). ENV-04 is
therefore a configuration-coherence and fail-open-direction defect, NOT a live-order
exposure, and this file does not claim otherwise. The direction still matters: it is an
order-submission surface by construction, and one repair to that constructor is all that
separates it from ``execute_trade``.
"""

import ast
import json
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

DEPLOYMENT_ENVIRONMENTS = ("production", "staging", "development")

#: The nine cells the brief requires: three environments × unset / false / true.
#: ``None`` means the variable is not set at all.
NINE_CELLS = [
    (env, flag)
    for env in DEPLOYMENT_ENVIRONMENTS
    for flag in (None, "false", "true")
]


@pytest.fixture
def router_enabled(monkeypatch):
    """Evaluate the authoritative predicate under a given (ENV, flag) pair."""
    from backend_app.core import safety_config

    def _evaluate(env, flag):
        monkeypatch.setenv("ENV", env)
        if flag is None:
            monkeypatch.delenv("PRODUCTION_ROUTER_ENABLED", raising=False)
        else:
            monkeypatch.setenv("PRODUCTION_ROUTER_ENABLED", flag)
        return safety_config.production_router_enabled()

    return _evaluate


# ══════════════════════════════════════════════════════════════════════════════════════════
#  1. THE NINE-CELL MATRIX
# ══════════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("env,flag", NINE_CELLS)
def test_the_full_environment_matrix(router_enabled, env, flag):
    """Three deployment environments × unset / false / true.

    The expected answer is the flag alone: ``true`` enables, everything else — including
    absence — does not. Stated as one assertion over all nine cells rather than nine
    separate expectations, because the property being proven is precisely that the
    environment does NOT change the answer.
    """
    expected = flag == "true"
    assert router_enabled(env, flag) is expected, (
        f"ENV={env!r} PRODUCTION_ROUTER_ENABLED={flag!r} gave the wrong answer"
    )


@pytest.mark.parametrize("env", DEPLOYMENT_ENVIRONMENTS)
def test_omission_never_enables_the_router(router_enabled, env):
    """THE DEFECT, stated as the property the brief asks for.

    With the old default of ``"True"``, every one of these three returned ``True``.
    """
    assert router_enabled(env, None) is False, (
        f"an unset PRODUCTION_ROUTER_ENABLED mounts the execution router in ENV={env}"
    )


@pytest.mark.parametrize("spelling", ["true", "True", "TRUE", " true ", "tRuE"])
def test_an_explicit_enablement_is_honoured_however_it_is_cased(router_enabled, spelling):
    """Explicit enablement still works. This change makes the flag required, not useless."""
    assert router_enabled("production", spelling) is True


@pytest.mark.parametrize("spelling", ["false", "False", "FALSE", "0", "no", "off", ""])
def test_the_unambiguous_disable_spellings_are_accepted_silently(router_enabled, spelling):
    assert router_enabled("production", spelling) is False


@pytest.mark.parametrize("typo", ["1", "yes", "on", "ture", "enabled", "y", "TRUE!"])
def test_an_unrecognised_value_disables_and_says_so(router_enabled, typo):
    """Fail closed AND visible.

    ``1`` / ``yes`` / ``on`` are deliberately NOT enabling spellings: widening the enabling
    vocabulary would be a permissiveness increase on the one switch whose purpose is
    explicit enablement. But silently ignoring a value an operator clearly meant as "on" is
    its own failure mode, so an unrecognised value is refused and logged by name.

    The records are captured with a handler attached directly to the module's own logger
    rather than through ``caplog``, so the assertion does not depend on propagation or on
    whatever log configuration an earlier test in the session left behind.
    """
    import logging

    captured = []

    class _Collect(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = _Collect(level=logging.WARNING)
    target = logging.getLogger("SafetyConfig")
    previous_level = target.level
    target.addHandler(handler)
    target.setLevel(logging.WARNING)
    try:
        result = router_enabled("production", typo)
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)

    assert result is False
    assert any("PRODUCTION_ROUTER_ENABLED" in message for message in captured), (
        f"an unrecognised value {typo!r} was ignored without a warning; captured {captured}"
    )


# ══════════════════════════════════════════════════════════════════════════════════════════
#  2. ONE DEFINITION
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_there_is_exactly_one_place_the_default_is_decided():
    """Two identical ``os.getenv("PRODUCTION_ROUTER_ENABLED", "True")`` lines existed, in
    ``core/safety_config.py`` and ``core/feature_flags.py``, so changing the default
    required changing it twice — and the docstring next to one of them had already drifted.

    Asserted with ``ast`` rather than a text scan: a ``Call`` to ``os.getenv`` whose first
    argument is this variable name, anywhere under ``backend_app/``, must appear only inside
    ``production_router_enabled``.
    """
    offenders = []

    for path in sorted(REPO_ROOT.glob("backend_app/**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))

        # Map every node to the function it sits in, so a reader of the failure knows where.
        enclosing = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    enclosing.setdefault(child, node.name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_getenv = (
                isinstance(func, ast.Attribute) and func.attr == "getenv"
            ) or (isinstance(func, ast.Name) and func.id == "getenv")
            if not is_getenv or not node.args:
                continue
            first = node.args[0]
            if not (
                isinstance(first, ast.Constant)
                and first.value == "PRODUCTION_ROUTER_ENABLED"
            ):
                continue
            owner = enclosing.get(node, "<module level>")
            if owner != "production_router_enabled":
                rel = path.relative_to(REPO_ROOT).as_posix()
                offenders.append(f"{rel}:{node.lineno} in {owner}")

    assert offenders == [], (
        "PRODUCTION_ROUTER_ENABLED is read outside production_router_enabled(), so the "
        "default is decided in more than one place: " + ", ".join(offenders)
    )


def test_both_execution_flags_classes_read_the_shared_definition():
    """``safety_config.ExecutionFlags`` and ``feature_flags.ExecutionFlags`` are two
    different classes with the same name and the same attribute. ``main.py`` imports the
    first; ``execution_router`` imports ``ExecutionContext`` from the second. They must not
    be able to disagree.

    Asserted structurally AND by value. Structurally, because both resolve the attribute in
    their class body at import time, so the only way to prove they cannot drift is to show
    each assigns from the same call — reloading the modules to vary the input would mint new
    ``SystemMode`` / ``ExecutionFlags`` class objects mid-session and break identity
    comparisons in unrelated tests.
    """
    from backend_app.core import feature_flags, safety_config

    for module in (safety_config, feature_flags):
        tree = ast.parse(
            pathlib.Path(module.__file__).read_text(encoding="utf-8", errors="replace")
        )
        assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and "PRODUCTION_ROUTER_ENABLED"
            in ast.unparse(node.targets[0] if isinstance(node, ast.Assign) else node.target)
        ]
        assert assignments, f"{module.__name__} does not define the flag at all"
        for node in assignments:
            value = ast.unparse(node.value)
            assert value == "production_router_enabled()", (
                f"{module.__name__} computes PRODUCTION_ROUTER_ENABLED as {value!r} "
                f"instead of reading the shared definition"
            )

    # And they agree right now, under whatever this session is configured with.
    expected = safety_config.production_router_enabled()
    assert safety_config.ExecutionFlags.PRODUCTION_ROUTER_ENABLED is expected
    assert feature_flags.ExecutionFlags.PRODUCTION_ROUTER_ENABLED is expected


def test_the_contradictory_assertion_is_gone():
    """``validate_safety_config_integrity`` raised in production unless the router was
    ENABLED — the opposite of every other source, and with zero call sites, so it had never
    run. Had it ever been wired, it would have refused to boot all four shipped task
    definitions.

    Asserted on the source rather than by calling it, because the function is deliberately
    still uncalled and still carries the ``VYOMQUANT_MODE == "live_trading"`` comparison
    that cannot fire (TRADE-06).
    """
    from backend_app.core import safety_config

    source = pathlib.Path(safety_config.__file__).read_text(
        encoding="utf-8", errors="replace"
    )
    tree = ast.parse(source)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "validate_safety_config_integrity"
    )

    raises = [
        n for n in ast.walk(fn) if isinstance(n, ast.Raise)
    ]
    for node in raises:
        text = ast.unparse(node)
        assert "PRODUCTION_ROUTER_ENABLED" not in text, (
            "validate_safety_config_integrity still refuses a DISABLED execution router"
        )

    doc = ast.get_docstring(fn) or ""
    assert "NO CALL SITES" in doc, (
        "the record that this validator is uncalled was removed from its docstring"
    )


# ══════════════════════════════════════════════════════════════════════════════════════════
#  3. THE MOUNT DECISION IS THE FLAG, AND NOTHING ELSE
# ══════════════════════════════════════════════════════════════════════════════════════════

def test_the_router_is_mounted_only_under_the_flag():
    """Structural: every ``include_router(execution_router, ...)`` in ``main.py`` must sit
    inside an ``if`` whose test is ``ExecutionFlags.PRODUCTION_ROUTER_ENABLED``.

    The nine-cell matrix above proves what the predicate answers; this proves the mount is
    the thing that consumes it. Without both, a correct predicate could sit next to an
    unconditional mount.
    """
    tree = ast.parse(
        (REPO_ROOT / "backend_app" / "main.py").read_text(
            encoding="utf-8", errors="replace"
        )
    )

    guarded, unguarded = 0, []

    def mounts_execution_router(node):
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "include_router"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "execution_router"
        )

    for branch in ast.walk(tree):
        if not isinstance(branch, ast.If):
            continue
        test_text = ast.unparse(branch.test)
        if "PRODUCTION_ROUTER_ENABLED" not in test_text:
            continue
        for node in ast.walk(branch):
            if mounts_execution_router(node):
                guarded += 1

    for node in ast.walk(tree):
        if mounts_execution_router(node):
            unguarded.append(node.lineno)

    assert guarded >= 1, "main.py does not mount execution_router under the flag at all"
    assert len(unguarded) == guarded, (
        f"execution_router is mounted {len(unguarded)} times but only {guarded} of those "
        f"are under PRODUCTION_ROUTER_ENABLED (lines {unguarded})"
    )


@pytest.mark.parametrize(
    "flag,expect_mounted",
    [("false", False), ("true", True)],
    ids=["false", "true"],
)
def test_the_app_really_does_or_does_not_expose_the_route(flag, expect_mounted):
    """End to end: boot the real application and ask the router table what is reachable.

    The structural test above reads the source. This one measures the composition, because
    a correct predicate and a correct guard can still be wired to the wrong thing. A
    subprocess is used because the flag is read at class-body time in two modules and
    consumed at import time in ``main.py``, so it cannot be varied inside one interpreter
    without reloading the whole application graph.

    TWO CELLS, NOT THREE, AND WHY. The ``unset`` cell is not measurable end to end in this
    repository, and the reason is itself a finding. ``core/config.py`` runs
    ``load_dotenv()`` at import, which injects ``backend_app/.env`` into ``os.environ``
    BEFORE ``safety_config`` reads the flag — so removing the variable from the subprocess
    environment does not produce an unset flag, it produces whatever that git-ignored file
    says. Measured directly::

        SETTING=unset  PREDICATE=True  EXECUTION_SIGNAL_MOUNTED=True

    with ``PRODUCTION_ROUTER_ENABLED`` popped from the environment, because the file said
    ``True``. (That file has been aligned to ``False``; it is the reason every local and
    non-containerised CI boot logged "Production execution router ENABLED".)

    ``unset -> off`` is proven instead by the nine-cell predicate matrix at the top of this
    file, which monkeypatches the variable away inside the process after dotenv has already
    run, and by the structural test above showing the mount consumes nothing else.

    NOT ``ENV=production``, and also not a choice. The application cannot be imported under
    ``ENV=production`` on a machine without a reachable Redis, because
    ``core/rate_limit`` refuses to start outside an explicitly local environment when it has
    no shared store — in-memory rate limiting is per-process and would multiply the
    effective limit by the replica count (API-03). That is a control working correctly, and
    it is measured::

        RuntimeError: FATAL: rate limiting has no shared store. REDIS_URL is set but
        unreachable (Timeout connecting to server). (ENV=production, REDIS_URL=set)

    Environment-independence of the mount decision is established by the predicate matrix
    rather than assumed here.
    """
    script = (
        "import json\n"
        "from backend_app.main import app\n"
        "paths = sorted({r.path for r in app.routes if hasattr(r, 'path')})\n"
        "print('ROUTES_JSON=' + json.dumps(paths))\n"
    )

    import os

    env = dict(os.environ)
    env["PRODUCTION_ROUTER_ENABLED"] = flag
    env.update(
        {
            "ENV": "development",
            "DEV_MODE": "true",
            "VYOMQUANT_MODE": "safe",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert completed.returncode == 0, (
        f"the application failed to import with PRODUCTION_ROUTER_ENABLED={flag!r}; "
        f"stderr tail: {completed.stderr[-1500:]}"
    )

    line = next(
        (l for l in completed.stdout.splitlines() if l.startswith("ROUTES_JSON=")), None
    )
    assert line is not None, f"no route table was printed; stdout: {completed.stdout[-600:]}"
    paths = json.loads(line[len("ROUTES_JSON=") :])

    mounted = "/api/execution/signal" in paths
    assert mounted is expect_mounted, (
        f"PRODUCTION_ROUTER_ENABLED={flag!r}: /api/execution/signal mounted={mounted}, "
        f"expected {expect_mounted}"
    )


def test_the_local_dotenv_file_does_not_enable_the_router():
    """The file that actually made the local lane permissive.

    ``backend_app/.env`` is git-ignored, is loaded by ``core/config.py`` before the flag is
    read, and said ``PRODUCTION_ROUTER_ENABLED=True``. It is therefore what mounted
    ``/api/execution`` on every local and non-containerised CI boot, independently of the
    code default — which is why flipping the default alone would not have changed what this
    machine does. Skipped where the file does not exist (CI), because it is not committed.
    """
    path = REPO_ROOT / "backend_app" / ".env"
    if not path.exists():
        pytest.skip("backend_app/.env is not present in this working tree")

    for number, raw in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        line = raw.strip()
        if not line.startswith("PRODUCTION_ROUTER_ENABLED"):
            continue
        _, _, value = line.partition("=")
        assert value.strip().lower() != "true", (
            f"backend_app/.env:{number} enables the production execution router for every "
            f"local boot"
        )


# ══════════════════════════════════════════════════════════════════════════════════════════
#  4. EVERY SOURCE THAT STATES A VALUE NOW STATES THE SAME ONE
# ══════════════════════════════════════════════════════════════════════════════════════════

TASK_DEFINITIONS = (
    "ecs-task-definition-full.json",
    "new_task_def.json",
    "td15.json",
    "infra/rollback/taskdef-vyomquant-api-145-pre-domain.json",
)


def _load_json_whatever_the_encoding(path: pathlib.Path):
    """Decode a task definition without assuming how it was written to disk.

    ``td15.json`` is UTF-16LE with a byte-order mark — it came out of a PowerShell
    redirection, which is the default on this platform — so a plain
    ``read_text(encoding="utf-8")`` fails at character 0. The other three are UTF-8.
    Sniffing the BOM is the difference between a test that reads the shipped files and a
    test that reads three of the four.
    """
    raw = path.read_bytes()
    for bom, encoding in (
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
        (b"\xef\xbb\xbf", "utf-8-sig"),
    ):
        if raw.startswith(bom):
            return json.loads(raw.decode(encoding).lstrip("\ufeff"))
    return json.loads(raw.decode("utf-8", errors="replace"))


def _environment_values(document, name):
    """Every value assigned to ``name`` anywhere in an ECS JSON document.

    Walks the structure instead of indexing a fixed path. ``aws ecs register-task-definition``
    input and ``describe-task-definition`` output differ — two of these four files are
    wrapped in ``{"taskDefinition": {...}}`` and two are not — and a parser that assumed one
    shape silently found nothing in the others, which reads as "the variable is not set"
    rather than "the parser is wrong".
    """
    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("name") == name and "value" in node:
                found.append(str(node["value"]))
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(document)
    return found


@pytest.mark.parametrize("relative", TASK_DEFINITIONS)
def test_no_shipped_task_definition_enables_the_router(relative):
    """These four already said ``"False"``; the code default disagreed with them. Pinned so
    the agreement is a tested fact rather than a coincidence, and so a future task
    definition cannot enable an execution surface without this failing."""
    path = REPO_ROOT / relative
    if not path.exists():
        pytest.skip(f"{relative} is not present in this working tree")

    found = _environment_values(
        _load_json_whatever_the_encoding(path), "PRODUCTION_ROUTER_ENABLED"
    )

    assert found, f"{relative} does not set PRODUCTION_ROUTER_ENABLED at all"
    for value in found:
        assert value.strip().lower() != "true", (
            f"{relative} sets PRODUCTION_ROUTER_ENABLED={value!r}, which mounts "
            f"/api/execution"
        )


@pytest.mark.parametrize("relative", [".env.example", "deployment_go_no_go.md"])
def test_no_document_tells_an_operator_to_enable_it(relative):
    """``deployment_go_no_go.md`` said ``PRODUCTION_ROUTER_ENABLED=True`` — a runbook
    instructing an operator to mount the surface the flag exists to keep off. Corrected
    with this change; pinned so it cannot drift back.
    """
    path = REPO_ROOT / relative
    if not path.exists():
        pytest.skip(f"{relative} is not present in this working tree")

    for number, raw in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        line = raw.strip()
        if not line.startswith("PRODUCTION_ROUTER_ENABLED"):
            continue
        _, _, value = line.partition("=")
        assert value.strip().lower() != "true", (
            f"{relative}:{number} tells an operator to set "
            f"PRODUCTION_ROUTER_ENABLED=True"
        )


def test_the_feature_flag_docstring_no_longer_contradicts_the_code():
    """The docstring said ``False`` and the line below it defaulted to ``"True"``. Whatever
    the docstring says now, it must not claim a bare default of True."""
    from backend_app.core import feature_flags

    doc = feature_flags.ExecutionFlags.__doc__ or ""
    assert "PRODUCTION_ROUTER_ENABLED" in doc
    assert "PRODUCTION_ROUTER_ENABLED: True" not in doc
    assert "ENV-04" in doc, "the reconciliation is not recorded next to the flag"
