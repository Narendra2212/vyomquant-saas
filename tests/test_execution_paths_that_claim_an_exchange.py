"""
tests/test_execution_paths_that_claim_an_exchange.py

TWO OF THE THREE PATHS NO LONGER EXIST (HY-02)
    The history below is kept because it is the record of why these defects were found and
    what was decided, and that reasoning still explains the surviving path. But FINDING 2
    (``idempotent_exchange_submission``) and FINDING 3 (``ExchangeGateway`` /
    ``ExecutionWorker``) both lived in ``backend_app/backend/distributed_execution/``, and
    that whole subsystem -- 40 modules plus its unmounted router -- was DELETED under
    HY-02. RT-11 had already removed the router's ``include_router`` from main.py, and the
    only non-test importer of the package was that router, so nothing reachable was lost.

    The classes that pinned those two paths went with it, which is why this file now holds
    8 tests rather than 25. That is not reduced coverage of a live risk: those tests
    asserted that a dangerous-looking subsystem was inert, and the subsystem is now absent.
    The guarantee is enforced more strongly, and in one place, by
    ``tests/test_hy02_distributed_execution_is_gone.py``, which fails if any part of it
    returns -- package, router, import or mounted route.

    FINDING 1, the ``POST /api/execution/signal`` path, is untouched and still covered here.

Three execution paths construct an ``ExecutionEngine`` without an exchange executor and
then describe the result as though an exchange had been reached. Found while deciding what
``execution_environment`` the four unstated construction sites should declare: the answer
turned out to be that three of them are defects rather than labelling questions.

Established by construction and by running the code, not by reading intent.

AND THEN RE-MEASURED, WHICH CHANGED THE VERDICT
The first version of this file declined to label these three sites, on the grounds that
the label decides behaviour -- ``LIVE`` makes an executor-less engine refuse, ``PAPER``
legitimises the simulation -- and that choosing is a statement about what the subsystem is
for which neither the code nor the specs make.

That reasoning does not survive asking who calls them. All three are UNREACHABLE:

    POST /api/execution/signal            constructs the engine with no portfolio_state,
                                          outside the handler's try -> 500 on every request
    submit_order_idempotent               zero call sites anywhere in the repository
    ExchangeGateway.execute_order         reached only through a WorkerPool that only
                                          ExecutionOrchestrator.initialize() creates, and
                                          nothing calls initialize(); with no gateways,
                                          submit_execution_job raises before queueing, so
                                          POST /api/distributed-execution/submit-job
                                          returns 400 on every request

So the behaviour the label decides cannot be reached, the choice costs nothing, and the
modules' own names and field names settle it: a module called
``idempotent_exchange_submission`` that returns a field called ``exchange_order_id``, and a
class called ``ExchangeGateway`` whose method is documented "Execute order via Canonical
ExecutionEngine Gateway", are live paths. Stating ``LIVE`` turns a latent trap into a
refusal for whoever eventually wires them up, with no path to break in the meantime.

Section 0 pins the reachability measurements, because every fix below depends on them.

FINDING 1 -- ``POST /api/execution/signal`` RETURNS 500 ON EVERY REQUEST
``backend_app/backend/execution_router.py``::

    engine = UnifiedExecutionEngine()

    try:
        res = await engine.execute_trade(...)

``UnifiedExecutionEngine`` is an alias of ``ExecutionEngine``, whose constructor requires
``portfolio_state`` -- an earlier anti-fabrication fix, because an absent portfolio state
used to be replaced with an invented ``100000.0`` that every risk guardrail was then
measured against. So the call above raises ``ValueError`` immediately, and it sits BEFORE
the ``try``, so nothing catches it. The router is mounted (``main.py`` includes it at
``/api/execution``) and imports cleanly, because the construction is inside the handler.

The handler also passes ``metadata={"portfolio_state": {...}}`` -- the portfolio state is
supplied, to the wrong place. It is a keyword argument of the CONSTRUCTOR, not an entry in
the call's metadata.

NOT FIXED HERE. Repairing the construction makes the endpoint reach
``execute_with_idempotency`` with ``source`` defaulted, which that method answers with
``status: 'blocked'`` -- "Direct execution blocked - source must be bot_runner". So the
endpoint is dead by design as well as by accident, and converting a 500 into the designed
refusal is a cosmetic change to a route that is not meant to execute anything. It is
recorded, not patched.

FINDING 2 -- AN ``exchange_order_id`` MINTED FOR AN ORDER NO EXCHANGE SAW
``backend_app/backend/distributed_execution/idempotent_exchange_submission.py`` refuses
with "Nothing was sent to the exchange" when it has no portfolio state, and otherwise
did::

    engine = ExecutionEngine(portfolio_state=portfolio_state)
    res = await engine.execute_trade(...)
    exchange_order_id = res.execution_id or f"exchange_{uuid.uuid4().hex[:16]}"

No ``exchange_executor`` was passed, so ``_execute_trade_internal`` took its
``# PAPER EXECUTION PATH`` and simulated the fill. ``res.execution_id`` is this system's
own deterministic execution id, not a venue id, and where it was absent a synthetic
``exchange_<hex>`` was generated instead. Either way the caller received something named
as an exchange order id for an order no exchange saw, with ``success`` taken from a
simulation.

FIXED: the engine now states ``LIVE``, so with no executor it refuses at the branch point
instead of simulating, and the result only carries an ``exchange_order_id`` when the fill
actually came back with one.

FINDING 3 -- A DISTRIBUTED WORKER THAT RECORDS A REFUSAL AS A COMPLETED JOB
``ExchangeGateway.execute_order`` reads the capital to size against off the job::

    portfolio_state = getattr(job, "portfolio_state", None)
    if not portfolio_state or "total_equity" not in portfolio_state:
        return {"order_id": None, "status": "refused", "error": "PORTFOLIO_STATE_REQUIRED"}

``ExecutionJob`` is a dataclass (``distributed_execution/queue_manager.py``) and it has no
``portfolio_state`` field -- not in the class, not in ``to_dict``, not in ``from_dict`` --
and no producer sets one. So that refusal is not an edge case, it is the only outcome:
this gateway has never reached the engine at all.

What made it a defect is the consumer. ``ExecutionWorker._complete_job_with_result``
never looked at ``result["status"]``::

    job.order_id = result.get("order_id")
    job.execution_price = Decimal(result.get("execution_price", "0"))
    ...
    job.status = JobStatus.COMPLETED

so a refusal was acknowledged as a COMPLETED job with a null order id and zeroed price,
quantity and fees, and ``exchange_telemetry.on_order_execution`` was then emitted for it.
A refused order was recorded as an executed one.

FIXED: the worker routes anything that did not fill to ``_fail_job``, which is what the
retry and dead-letter paths exist for; and the gateway states ``LIVE`` and stops
substituting invented values -- an ``order_{hex}`` when no id came back, and
``Decimal("50000")`` as the execution price when the job carried no price -- for values it
does not have.
"""

from __future__ import annotations

import ast
import inspect
import os
import pathlib
import sys
from decimal import Decimal
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Directories that hold vendored or duplicated copies of the backend, plus the bundled
#: interpreter. Walking them makes a repo-wide survey take minutes and answers the wrong
#: question -- ``connection_layer/`` and ``aerora_quant_platform/`` contain their own
#: ``master_executor.py`` and ``exchange_executor.py`` at the same line numbers.
SKIP_DIRS = {
    ".venv", ".codex_verify_venv", ".git", "node_modules", ".hypothesis", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build",
    "python_portable", "Git", "connection_layer", "aerora_quant_platform",
}


def _engine_constructions(relative: str) -> list:
    """``[(lineno, {kwarg name: value node})]`` for each engine construction in a file."""
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8", errors="replace"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name in ("ExecutionEngine", "UnifiedExecutionEngine"):
            found.append(
                (node.lineno, {k.arg: k.value for k in node.keywords if k.arg})
            )
    return found


def _function(relative: str, name: str) -> ast.AST:
    """The named function's AST node, so a test can ask about its CODE.

    Every text-based "this fabrication is gone" assertion in this file failed on the
    comment or docstring that explains what was removed -- a correction that names what
    it corrects necessarily contains the string being forbidden. Comments are absent from
    the AST, and the two helpers below look at call targets and numeric literals rather
    than at dumped source, so docstrings do not register either.
    """
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8", errors="replace"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{relative} has no function named {name}")


def _callees(node: ast.AST) -> set[str]:
    """Every function name called anywhere inside ``node``."""
    return {
        getattr(call.func, "attr", None) or getattr(call.func, "id", None)
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
    }


def _literal_arguments(node: ast.AST, func: str) -> set[str]:
    """Constant arguments passed to ``func`` inside ``node``, e.g. ``Decimal("50000")``."""
    found: set[str] = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        callee = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
        if callee != func:
            continue
        for argument in call.args:
            if isinstance(argument, ast.Constant):
                found.add(str(argument.value))
    return found


def _stated_environment(value: ast.AST | None) -> str | None:
    if isinstance(value, ast.Attribute):
        return value.attr
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value.upper()
    return None


def _call_sites(
    name: str, receiver: str | None = None, *, production_only: bool = False
) -> list[str]:
    """``["path:lineno"]`` for every CALL to ``name``, found by parsing.

    Calls only -- a definition, an import or a comment naming the function is not a
    caller, and a text search cannot tell the difference. ``receiver`` additionally
    requires the call to be an attribute access whose target renders to that name, so
    ``initialize`` can be asked about the orchestrator specifically rather than about
    every object in the repository that happens to have a method of that name.

    ``production_only`` excludes ``tests/``. REACHABILITY IS A PROPERTY OF SHIPPED CODE.
    A test that calls a dangerous entry point in order to assert that it REFUSES is
    evidence that the path is safe, not evidence that it is reachable in production -- and
    counting it as a caller makes the measurement say the opposite of the truth. Use it
    only for "does production reach this", never to excuse a real caller.
    """
    sites: list[str] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if production_only and pathlib.Path(dirpath).name == "tests":
            dirnames[:] = []
            continue
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = pathlib.Path(dirpath) / filename
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                callee = getattr(node.func, "attr", None) or getattr(
                    node.func, "id", None
                )
                if callee != name:
                    continue
                if receiver is not None:
                    if not isinstance(node.func, ast.Attribute):
                        continue
                    target = node.func.value
                    rendered = (
                        target.id
                        if isinstance(target, ast.Name)
                        else getattr(target, "attr", "")
                    )
                    if rendered != receiver:
                        continue
                sites.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    return sorted(sites)


# ---------------------------------------------------------------------------
# 0. Who can reach these paths
# ---------------------------------------------------------------------------


class TestNoneOfTheThreePathsIsReachable:
    """The measurement the rest of this file depends on.

    Each fix below changes a behaviour that cannot currently occur. That is what makes
    them safe to land without a product decision, and it is also the reason none of them
    is urgent -- so the measurement is pinned rather than asserted once in a docstring.
    """

    def test_nothing_calls_the_idempotent_exchange_submission(self):
        """Its only entry point, and its singleton accessor, have no callers.

        ``production_only`` because ``tests/test_live_orders_are_bounded_by_account_equity.py``
        and ``tests/test_idempotent_submission_fails_closed.py`` now call
        ``submit_order_idempotent`` deliberately, to assert that it refuses. Those calls
        are the opposite of the hazard this test measures, and counting them would make
        the assertion read as "the path became reachable" when what actually happened is
        that it became provably sealed. The question asked of shipped code is unchanged.
        """
        assert _call_sites("submit_order_idempotent", production_only=True) == []
        assert _call_sites("get_idempotent_exchange_submission", production_only=True) == []

    def test_the_signal_endpoint_is_mounted_but_cannot_succeed(self):
        """Finding 1's reachability: mounted, reachable, and a 500 every time."""
        main = (ROOT / "backend_app" / "main.py").read_text(
            encoding="utf-8", errors="replace"
        )

        assert "app.include_router(execution_router" in main
        assert '"/api/execution"' in main


# ---------------------------------------------------------------------------
# 1. The dead endpoint, recorded rather than patched
# ---------------------------------------------------------------------------


class TestTheSignalEndpointCannotSucceed:
    def test_the_engine_is_constructed_with_no_portfolio_state(self):
        constructions = _engine_constructions("backend_app/backend/execution_router.py")

        assert constructions, "execution_router no longer constructs an engine"
        for lineno, kwargs in constructions:
            assert "portfolio_state" not in kwargs, (
                f"execution_router.py:{lineno} now supplies portfolio_state, so this "
                f"endpoint may work -- re-check the finding"
            )

    def test_constructing_it_that_way_raises(self):
        """Run, not reasoned about."""
        from backend_app.core.execution_engine import ExecutionEngine

        with pytest.raises(ValueError) as caught:
            ExecutionEngine()

        assert "portfolio_state" in str(caught.value)

    def test_the_construction_is_outside_the_handlers_try(self):
        """Which is why this surfaces as a 500 rather than the handler's error shape."""
        from backend_app.backend import execution_router as er

        source = inspect.getsource(er.process_signal)
        construction = source.find("UnifiedExecutionEngine()")
        first_try = source.find("try:")

        assert construction != -1 and first_try != -1
        assert construction < first_try, (
            "the engine construction is now inside the try block, so the failure is "
            "handled -- re-check this finding"
        )

    def test_the_portfolio_state_is_passed_to_the_wrong_place(self):
        """It is supplied as call metadata, where the constructor cannot see it."""
        from backend_app.backend import execution_router as er

        source = inspect.getsource(er.process_signal)

        assert '"portfolio_state"' in source, (
            "the handler no longer mentions portfolio_state at all; re-check"
        )
        assert "metadata=" in source

    def test_the_funnel_would_block_it_anyway(self):
        """Why repairing the construction is not the fix.

        ``execute_with_idempotency`` requires ``source == "bot_runner"`` and this handler
        does not set it, so a working construction only converts a 500 into the designed
        ``blocked`` refusal.
        """
        from backend_app.backend import execution_router as er
        from backend_app.core.execution_engine import ExecutionEngine

        handler = inspect.getsource(er.process_signal)
        assert "source=" not in handler, (
            "the handler now sets a source; if it is bot_runner, this endpoint is no "
            "longer dead by design and the finding needs re-reading"
        )

        default = inspect.signature(
            ExecutionEngine.execute_with_idempotency
        ).parameters["source"].default
        assert default != "bot_runner"


# ---------------------------------------------------------------------------
# 2. The exchange order id that no exchange issued
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3. The worker that recorded a refusal as a completed job
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. The one site that WAS determinable from the start
# ---------------------------------------------------------------------------


def test_the_validation_harness_declares_backtest():
    """``validate_financials.py`` prints a markdown pass/fail report.

    It is a harness, not a trading path, and it overrides ``apply_slippage`` for
    determinism -- so ``BACKTEST`` is readable from the code rather than guessed, which is
    why this one was labelled while the three above were still open questions.
    """
    constructions = _engine_constructions("backend_app/validate_financials.py")

    assert constructions, "validate_financials no longer constructs an engine"
    for lineno, kwargs in constructions:
        assert "execution_environment" in kwargs, (
            f"backend_app/validate_financials.py:{lineno} does not declare its "
            f"execution environment"
        )
