"""
tests/test_production_runs_in_paper_mode.py

One environment variable separates simulated money from real money, and this pins it.

WHAT THIS IS, AND WHY IT IS A TEST RATHER THAN A NOTE
`backend_app/main.py` reads `VYOMQUANT_MODE` at module import, before any route is
registered, and resolves the process's entire execution posture from it::

    env_mode = get_vyomquant_mode("safe").lower()
    if env_mode == "live":
        ExecutionFlags.enable_live_trading()
    elif env_mode == "paper":
        ExecutionFlags.enable_paper_trading()
    else:
        SafetyMonitor.assert_safe_mode()   # crash if not an explicitly valid safe state

`ecs-task-definition-full.json` sets it to `paper`, so production runs
`enable_paper_trading()`, which sets `LIVE_TRADING_ENABLED = False`,
`BACKTEST_CAN_SUBMIT_ORDERS = False`, and `PAPER_TRADING_ENABLED`,
`STRATEGY_SIGNAL_EXECUTION` and `ALLOW_ML_INFERENCE` to True.

`SafetyMonitor.check_execution_allowed("live_order")` then refuses in paper mode by name,
and `ExecutionFlags.enable_live_trading()` refuses to run at all unless BOTH
`VYOMQUANT_MODE=live` AND `VYOMQUANT_ENABLE_LIVE_TRADING=true` are set - it raises
`RuntimeError` otherwise rather than degrading.

So this value is the outermost of several independent controls on real-money execution:

  1. `routers/strategies.py` starts bots with `paper_trading=True` hardcoded at both
     deploy sites, so the ordinary deploy route cannot start a live bot at all.
  2. `require_live_trading` gates the path that can, as a subscription entitlement.
  3. `VYOMQUANT_MODE=paper` sets `LIVE_TRADING_ENABLED = False` process-wide, which
     `check_execution_allowed("live_order")` reads.
  4. `ExecutionEngine.execute_with_idempotency`'s environment guard refuses any order
     whose `Execution_Environment` is not `LIVE`, and `_execute_trade_internal` refuses a
     branch that disagrees with what the engine was constructed for
     (`tests/test_simulated_orders_reach_the_simulator.py`).
  5. The global kill switch (`system:kill_switch`) is checked before any of it.

A reader deciding whether this platform can move real money needs to know all five, and
the first one anybody will reach for is this file.

THIS IS NOT A TEST THAT SAYS "NEVER GO LIVE"
Going live is a legitimate, intended operation. What it must not be is accidental, or a
one-character edit nobody reviewed. So the assertion is conditional: `paper` passes, and
`live` passes ONLY when `VYOMQUANT_ENABLE_LIVE_TRADING=true` is set beside it - which is
exactly what `enable_live_trading()` itself demands. Anything else fails, including the
`safe` default, because `main.py` answers `safe` with `assert_safe_mode()` and a crash: a
container that boots into safe mode is not a safe production deployment, it is an outage.

WHAT THIS FILE DOES NOT CHECK
The RUNNING task definition in AWS. This reads the checked-in JSON, which is what review
sees and what a deploy is built from; a value changed only in the console is invisible
here and to everybody else. Verifying the live service is task 13.4's job
(`.kiro/specs/production-launch-hardening/tasks.md`), and it needs credentials this test
does not have.
"""

from __future__ import annotations

import json
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The checked-in task definitions a deploy is built from. The rollback copy under
#: `infra/rollback/` is included deliberately: rolling back must not roll back INTO live
#: mode, and that file is one `git mv` away from being the deployed one.
TASK_DEFINITIONS = (
    "ecs-task-definition-full.json",
    "infra/rollback/taskdef-vyomquant-api-145-pre-domain.json",
)

MODE_VARIABLE = "VYOMQUANT_MODE"
LIVE_CONFIRMATION = "VYOMQUANT_ENABLE_LIVE_TRADING"


def _environment_of(relative: str) -> dict:
    """``{name: value}`` for the first container definition's environment block.

    TWO SHAPES, BOTH REAL, and the second one is why this is not a one-liner.
    `ecos-task-definition-full.json` is a register-task-definition INPUT, with
    `containerDefinitions` at the top level. The rollback copy under `infra/rollback/` is
    an `aws ecs describe-task-definition` OUTPUT, which wraps everything in a
    `taskDefinition` object alongside `tags`.

    Reading only the top level made this helper `pytest.skip` on the rollback file - so
    the parametrised cases for it reported as skipped rather than failing, and a guard
    that skips is not a guard. `test_both_task_definitions_were_actually_read` below
    asserts neither file is being skipped, so this cannot regress quietly.
    """
    path = REPO_ROOT / relative
    document = json.loads(path.read_text(encoding="utf-8"))
    if "containerDefinitions" not in document and "taskDefinition" in document:
        document = document["taskDefinition"]
    containers = document.get("containerDefinitions") or []
    if not containers:
        pytest.fail(
            f"{relative} declares no containerDefinitions under either the top level or "
            f"a `taskDefinition` wrapper, so its execution mode cannot be read"
        )
    return {
        entry.get("name"): entry.get("value")
        for entry in (containers[0].get("environment") or [])
    }


@pytest.mark.parametrize("relative", TASK_DEFINITIONS)
def test_the_task_definition_declares_an_execution_mode_at_all(relative):
    """An absent `VYOMQUANT_MODE` means the container boots into `safe` and crashes.

    `main.py`'s `else` branch calls `SafetyMonitor.assert_safe_mode()`, so omitting the
    variable is not a conservative default - it is a container that will not serve.
    """
    if not (REPO_ROOT / relative).is_file():
        pytest.skip(f"{relative} is not present in this checkout")
    environment = _environment_of(relative)

    assert MODE_VARIABLE in environment, (
        f"{relative} does not set {MODE_VARIABLE}. main.py resolves an unset value to "
        f"`safe` and answers that with assert_safe_mode(), so this container boots into "
        f"a crash rather than into a safe posture."
    )


@pytest.mark.parametrize("relative", TASK_DEFINITIONS)
def test_live_mode_requires_its_explicit_confirmation(relative):
    """`paper` is fine. `live` is fine WITH the second switch. Nothing else is.

    This is the assertion that makes going live deliberate rather than accidental, and it
    mirrors `ExecutionFlags.enable_live_trading()`, which raises `RuntimeError` unless
    both variables are set. A task definition that says `live` without the confirmation
    describes a container that cannot start.
    """
    if not (REPO_ROOT / relative).is_file():
        pytest.skip(f"{relative} is not present in this checkout")
    environment = _environment_of(relative)
    mode = (environment.get(MODE_VARIABLE) or "").lower()

    if mode == "paper":
        return

    if mode == "live":
        confirmation = (environment.get(LIVE_CONFIRMATION) or "").lower()
        assert confirmation == "true", (
            f"{relative} sets {MODE_VARIABLE}=live without "
            f"{LIVE_CONFIRMATION}=true. ExecutionFlags.enable_live_trading() raises "
            f"RuntimeError in exactly that case, so this container would not start. If "
            f"going live is intended, set both - and read this file's docstring for the "
            f"other four controls that also have to agree."
        )
        return

    pytest.fail(
        f"{relative} sets {MODE_VARIABLE}={mode!r}, which main.py answers with "
        f"SafetyMonitor.assert_safe_mode() and a crash. The only serving values are "
        f"`paper` and `live`."
    )


def test_both_task_definitions_were_actually_read():
    """Non-vacuity: neither parametrised file may be silently skipped.

    The first version of this file read only top-level `containerDefinitions`, so the
    rollback task definition - an `aws ecs describe-task-definition` output, wrapped in a
    `taskDefinition` object - fell through to `pytest.skip`. Two cases reported as skipped
    and the file looked like it was guarding both. It was guarding one.
    """
    missing = [r for r in TASK_DEFINITIONS if not (REPO_ROOT / r).is_file()]
    assert not missing, f"task definitions absent from this checkout: {missing}"

    for relative in TASK_DEFINITIONS:
        environment = _environment_of(relative)
        assert MODE_VARIABLE in environment, (
            f"{relative} was read but declares no {MODE_VARIABLE}; if the file shape "
            f"changed again, fix _environment_of rather than letting it skip"
        )


def test_paper_mode_leaves_live_trading_disabled():
    """Read off `enable_paper_trading` itself, so the claim above cannot drift.

    The docstring asserts that `paper` disables live trading and backtest order
    submission while enabling signals and ML inference. That is a property of
    `safety_config`, not of this test's prose, so it is checked against the real
    classmethod's source rather than restated.
    """
    import inspect

    from backend_app.core.safety_config import ExecutionFlags

    source = inspect.getsource(ExecutionFlags.enable_paper_trading)

    assert "LIVE_TRADING_ENABLED = False" in source, (
        "enable_paper_trading no longer disables live trading, which is the property "
        "VYOMQUANT_MODE=paper is relied on for"
    )
    assert "BACKTEST_CAN_SUBMIT_ORDERS = False" in source, (
        "enable_paper_trading no longer stops a backtest submitting orders"
    )
    assert "PAPER_TRADING_ENABLED = True" in source


def test_live_mode_cannot_be_entered_without_both_switches():
    """The code half of the task-definition assertion above.

    `enable_live_trading` must keep demanding both variables. If it ever degraded to a
    warning, the task-definition check would be the only thing left, and a console edit
    would bypass it.
    """
    import inspect

    from backend_app.core.safety_config import ExecutionFlags

    source = inspect.getsource(ExecutionFlags.enable_live_trading)

    assert source.count("raise RuntimeError") >= 2, (
        "enable_live_trading no longer raises for both a wrong VYOMQUANT_MODE and a "
        "missing VYOMQUANT_ENABLE_LIVE_TRADING; going live would no longer need both "
        "switches"
    )
    assert LIVE_CONFIRMATION in source


def test_the_mode_decides_the_posture_before_any_route_is_registered():
    """Order matters: the flags are set at import, not inside a request.

    If this moved into a lifespan handler or a dependency, a request arriving during
    startup could be served by a process whose posture was not yet decided.
    """
    main = REPO_ROOT / "backend_app" / "main.py"
    if not main.is_file():
        pytest.skip("backend_app/main.py is not present in this checkout")
    source = main.read_text(encoding="utf-8", errors="replace")

    mode_at = source.find("enable_paper_trading()")
    first_route = source.find("app.include_router(")

    assert mode_at != -1, "main.py no longer enables paper trading from the mode"
    assert first_route != -1, "main.py no longer includes any router"
    assert mode_at < first_route, (
        "the execution posture is now decided after routers are registered, so a request "
        "could be served before VYOMQUANT_MODE has been applied"
    )
