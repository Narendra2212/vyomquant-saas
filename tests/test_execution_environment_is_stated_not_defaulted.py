"""
tests/test_execution_environment_is_stated_not_defaulted.py

A paper bot's order used to enter the live execution funnel labelled ``LIVE``, and the
guard built to stop exactly that waved it through. It no longer does.

P0-05. FIXED AT THE FUNNEL; ONE DOOR AND ONE ROUTING DECISION STILL OPEN.

WHAT THE DEFECT WAS
``ExecutionEngine.execute_with_idempotency`` opens with the control, before any repository
handle, any session, and the ``exchange_executor``/CCXT call in
``_execute_trade_internal``::

    await assert_live_environment(
        execution_environment,
        actor_id=str(tenant_id),
        strategy_id=str(strategy_id) if strategy_id else None,
        symbol=symbol,
    )

``assert_live_environment`` refuses ``PAPER``, ``BACKTEST``, ``None`` and any unrecognised
value, audits the refusal, and raises rather than returning a verdict a caller could drop.
It is well built and ``tests/test_execution_environment_guard.py`` exercises it directly.

The parameter it reads is declared ``execution_environment: Any =
ExecutionEnvironment.LIVE``, and **no caller anywhere in the repository passed it**. So
the default won on every call, ``assert_live_environment`` received ``LIVE``
unconditionally, and the one branch that could refuse was unreachable from production
code. A paper order was processed by the live funnel, passed the live-only assertion, and
was recorded with an audit trail that said ``LIVE``.

The parameter's docstring defended the default on the grounds that the engine had only
ever had one environment, so ``LIVE`` merely wrote down the pre-existing call convention.
That was not true of the caller that matters. ``BotRunner._recover_live_state`` constructs
an ``ExecutionEngine`` on BOTH paths -- the paper fast-path builds one with a synthetic
``{"total_equity": 10000.0}`` portfolio and ``exchange_executor=None`` before returning,
and the live path builds one with a real ``exchange_executor``. The tick loop is shared,
and it made the single ``execute_with_idempotency`` call for both modes with no
environment, while holding the answer in ``self.paper_trading`` -- a required keyword-only
constructor argument, there precisely so the mode is always established.

WHAT CHANGED
``BotRunner._inner_loop`` now states it, derived from ``self.paper_trading`` rather than
hardcoded::

    execution_environment=(
        ExecutionEnvironment.PAPER if self.paper_trading else ExecutionEnvironment.LIVE
    ),

Stating it is the whole fix. A live bot is unaffected -- it was already being labelled
``LIVE``, and now says so.

AND THEN THE CONSEQUENCE HAD TO BE DEALT WITH, BECAUSE IT WAS A LAUNCH BLOCKER
``assert_live_environment`` refuses ``PAPER`` by design, so labelling the order truthfully
made every paper bot's order refused -- and ``routers/strategies.py`` starts bots with
``paper_trading=True`` hardcoded at both deploy sites, so paper is the ordinary path. The
tick loop's ``except Exception`` swallowed the refusal, rolled back exposure and carried
on, which made the observable result a bot that logs an error per signal and never fills.

That was resolved by distinguishing the live order path from a declared simulator at the
funnel entry, NOT by removing the label.
``tests/test_simulated_orders_reach_the_simulator.py`` owns that change and enumerates the
whole (engine, executor, order) matrix. In short: an engine that states ``PAPER`` or
``BACKTEST`` and holds no ``exchange_executor`` is guarded for AGREEMENT -- 13.10 still
applies, ``LIVE`` is refused, only its own environment is admitted -- and every other
engine, including all 65 that state nothing, keeps ``assert_live_environment`` unchanged.

So a paper bot executes again, its orders are labelled ``PAPER``, and its audit trail says
``PAPER``. Requirement 13.3 -- route a Paper_Session's intents to the Paper_Simulator -- is
a rule about the marketplace Paper_Session feature, which a ``BotRunner`` is not; see
``TestRequirement133IsTheAnswerNotAnOpenQuestion`` below.

WHAT STILL ISN'T GUARDED
The default is still ``ExecutionEnvironment.LIVE``, so any caller that omits the argument
is still unguarded, and ``ExecutionEngine.execute_trade`` -- a second, thinner door into
the same funnel -- used to omit it even when the engine itself had been told what it
exists for. ``TestTheOtherDoorIntoTheFunnel`` covers that. The default was NOT changed to
an unstated sentinel: there are 70 funnel call sites in this repository and 1 states the
environment, so making omission fatal would break paths rather than protect them. The
sites are driven down one at a time instead.

WHAT ACTUALLY STOPS A PAPER ORDER REACHING AN EXCHANGE, BELT AND BRACES
``exchange_executor=None`` on the paper engine -- a null dependency, not a control --
plus, since ``tests/test_execution_engine_mode_matches_its_branch.py``, an assertion at
the branch point in ``_execute_trade_internal`` that the branch about to be taken agrees
with what the engine was constructed for. ``test_the_paper_engine_has_no_exchange_executor``
below asserts the null so it stops being accidental.
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

from backend_app.backend.execution_environment import ExecutionEnvironment
from backend_app.core import execution_engine as ee

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend_app"

#: The two methods on ``ExecutionEngine`` that reach ``assert_live_environment``:
#: ``execute_with_idempotency`` is the funnel itself, ``execute_trade`` delegates to it.
FUNNEL_METHODS = {"execute_with_idempotency", "execute_trade"}


def _tick_loop_source() -> str:
    """The per-tick body that holds the single execution call.

    ``BotRunner._inner_loop`` is where the call lives; ``_run_trading_loop`` is the
    wrapper ``fleet_manager._boot_and_run`` awaits. Resolved by name here rather than
    assumed, so a rename is a clear failure rather than a silently empty search.
    """
    from backend_app.backend import master_executor as me

    loop = getattr(me.BotRunner, "_inner_loop", None)
    assert loop is not None, (
        "BotRunner._inner_loop is gone; find where the single "
        "execute_with_idempotency call now lives before trusting this file"
    )
    return inspect.getsource(loop)


def _funnel_call_sites(root: pathlib.Path) -> list[tuple[str, int, ast.Call]]:
    """Every call to a funnel method under ``root``, found by parsing.

    A text scan would match the parameter's own declaration, this file's subject matter,
    and every comment explaining it. Parsing is the only way to ask about call sites, and
    getting that wrong is how the first version of this measurement went wrong.

    NOTE the name collision this deliberately does not try to resolve here:
    ``execute_with_idempotency`` is ALSO the name of the distributed idempotency layer's
    method, which takes ``client_order_id``/``operation`` and has nothing to do with the
    environment control. Callers of that layer are in ``exchange_executor.py`` and
    ``signal_service.py``; the tests below are scoped to specific modules, so they never
    see it.
    """
    sites: list[tuple[str, int, ast.Call]] = []
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if callee in FUNNEL_METHODS:
                sites.append((path.relative_to(root).as_posix(), node.lineno, node))
    return sites


def _bot_runner_funnel_call() -> ast.Call:
    """The single funnel call in ``BotRunner._inner_loop``, located by parsing.

    Not by slicing the source text. ``source.split("execute_with_idempotency(")[1]
    .split(")")[0]`` looks like it isolates the argument list and does not: the first
    ``)`` belongs to ``uuid.UUID(self.user_id)`` on the very first argument, so the slice
    ends four lines early and every argument after it is invisible. That is how the first
    version of this test reported the fix as missing.

    Scoped to ``_inner_loop`` specifically (RT-21), not to every funnel call anywhere in
    the module: ``BotRunner.close_position`` added a second one, for flattening a
    position on request rather than on a strategy signal. That call states its own
    environment the same way (``PAPER if self.paper_trading else LIVE`` --
    ``tests/test_rt21_close_position_flattens_exposure.py`` covers it), but it is a
    different call with a different purpose, and conflating the two here would make this
    test pass or fail on whichever one AST happens to visit first rather than on the tick
    funnel this file is about.
    """
    tree = ast.parse((BACKEND / "backend" / "master_executor.py").read_text(encoding="utf-8"))
    inner_loop = next(
        (
            node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_inner_loop"
        ),
        None,
    )
    assert inner_loop is not None, "BotRunner._inner_loop was not found by parsing"

    sites = [
        node for node in ast.walk(inner_loop)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None)) in FUNNEL_METHODS
    ]
    assert len(sites) == 1, (
        f"expected exactly one funnel call in _inner_loop, found {len(sites)}; the mode "
        f"may now be branched and this file's premise has changed"
    )
    return sites[0]


def _code_identifiers(module) -> set[str]:
    """Every identifier that appears in ``module``'s CODE, excluding prose.

    Comments are absent from the AST, and string constants are skipped, so an
    explanatory comment or docstring naming a symbol does not register as a reference to
    it. This matters: a text search of ``master_executor`` for ``"paper_simulator"`` now
    matches the comment that explains why it is not used, which made the absence test
    below fail on its own documentation.
    """
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.update(alias.name.split("."))
                if alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            names.update((node.module or "").split("."))
            for alias in node.names:
                names.update(alias.name.split("."))
                if alias.asname:
                    names.add(alias.asname)
    return names


# ---------------------------------------------------------------------------
# 1. The guard is armed at the call site that matters
# ---------------------------------------------------------------------------


class TestTheModeTravelsWithTheOrder:
    def test_the_guard_is_the_first_thing_the_funnel_does(self):
        """Order matters: it must precede the exchange call, not follow it.

        "First statement" used to be literal, and is not any more: the funnel now reads
        ``self.execution_environment`` and ``self.exchange_executor`` to decide WHICH
        guard applies -- the live-path guard, or the agreement guard a declared simulator
        gets (see ``tests/test_simulated_orders_reach_the_simulator.py``).

        So the property is restated rather than relaxed: everything before the guard must
        be a pure attribute read. No await, no call except ``getattr``, so nothing can
        open a session, touch a balance or reach an exchange before the verdict. Checked
        structurally, because "the guard is early enough" is not something a string search
        can answer.
        """
        source = inspect.getsource(ee.ExecutionEngine.execute_with_idempotency)
        tree = ast.parse(inspect.cleandoc(source).replace("async def", "def", 1))
        body = [
            node
            for node in tree.body[0].body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        ]

        def _is_pure_read(node) -> bool:
            if not isinstance(node, ast.Assign):
                return False
            for child in ast.walk(node.value):
                if isinstance(child, (ast.Await, ast.Yield, ast.YieldFrom)):
                    return False
                if isinstance(child, ast.Call):
                    callee = getattr(child.func, "id", None) or getattr(
                        child.func, "attr", None
                    )
                    if callee != "getattr":
                        return False
            return True

        prefix, remainder = [], list(body)
        while remainder and _is_pure_read(remainder[0]):
            prefix.append(remainder.pop(0))

        assert remainder, "execute_with_idempotency has no executable body"
        first_effect = ast.dump(remainder[0])

        assert "assert_live_environment" in first_effect, (
            "the first thing execute_with_idempotency DOES is no longer the environment "
            "guard, so an order could do work before being refused. Statements before "
            "it must be pure attribute reads."
        )
        assert "assert_simulation_environment_matches" in first_effect, (
            "the declared-simulator branch of the guard is gone from the funnel's first "
            "action; a correctly-labelled paper order would be refused by the live-path "
            "guard and paper trading would stop"
        )

    def test_the_bot_runner_states_the_environment_on_the_funnel(self):
        """The fix, measured where it was measured absent.

        NARROWED TO THE FUNNEL. ``ExecutionEngine.__init__`` has a parameter of the SAME
        NAME, and four construction sites supply it -- see
        ``tests/test_execution_engine_mode_matches_its_branch.py``. That is a different
        control: it tells the engine what it EXISTS FOR, and is enforced at the branch
        point inside ``_execute_trade_internal``, where the choice between placing a real
        order and simulating one is made. This is about the environment of an individual
        ORDER, read by ``assert_live_environment`` at the funnel entry.
        """
        call = _bot_runner_funnel_call()
        supplied = {keyword.arg for keyword in call.keywords if keyword.arg}

        assert "execution_environment" in supplied, (
            "BotRunner's tick loop no longer states the order's execution environment, "
            "so the ExecutionEnvironment.LIVE default decides it again and a paper "
            "bot's order is labelled LIVE"
        )

    def test_the_stated_environment_is_derived_from_the_bots_mode(self):
        """Stating it is not enough; stating the TRUTH is the fix.

        ``execution_environment=ExecutionEnvironment.LIVE`` would satisfy the test above
        while preserving the entire defect, so the value is inspected: it must branch on
        ``self.paper_trading`` and it must be able to produce PAPER.
        """
        call = _bot_runner_funnel_call()
        value = next(
            keyword.value
            for keyword in call.keywords
            if keyword.arg == "execution_environment"
        )

        assert isinstance(value, ast.IfExp), (
            f"the environment passed to the funnel is not conditional "
            f"({type(value).__name__}); a constant here cannot tell a paper bot from a "
            f"live one"
        )

        rendered = ast.dump(value)
        assert "paper_trading" in rendered, (
            "the environment is conditional, but not on the mode the bot was "
            "constructed with. self.paper_trading is the only established answer."
        )
        assert "'PAPER'" in rendered and "'LIVE'" in rendered, (
            f"the conditional does not choose between PAPER and LIVE: {rendered}"
        )

    def test_the_default_is_still_live_so_omitting_it_is_still_unguarded(self):
        """Recorded, not celebrated.

        "Said nothing" and "said live" remain indistinguishable at the guard. That is
        why the remaining unstated door in ``TestTheOtherDoorIntoTheFunnel`` matters, and
        why this is asserted rather than assumed: if the default is ever changed to an
        unstated sentinel, this fails and the 70 call sites have to be dealt with
        deliberately rather than discovered in production.
        """
        signature = inspect.signature(ee.ExecutionEngine.execute_with_idempotency)
        assert (
            signature.parameters["execution_environment"].default
            is ExecutionEnvironment.LIVE
        )


# ---------------------------------------------------------------------------
# 2. Both bot modes build an engine, so the funnel is shared
# ---------------------------------------------------------------------------


class TestBothBotModesBuildAnExecutionEngine:
    def _recover_source(self):
        from backend_app.backend import master_executor as me

        return inspect.getsource(me.BotRunner._recover_live_state)

    def test_the_paper_fast_path_constructs_an_execution_engine(self):
        """The fact that falsified the docstring's "every caller is live".

        The paper branch returns before the live path, and it builds its own engine on
        the way out, so the shared tick loop has one in both modes.
        """
        source = self._recover_source()
        paper_branch = source.split("if is_paper:", 1)
        assert len(paper_branch) == 2, "the paper fast-path is gone; re-check this file"
        before_return = paper_branch[1].split("return", 1)[0]

        assert "ExecutionEngine(" in before_return, (
            "the paper fast-path no longer builds an ExecutionEngine. If paper now has "
            "no engine at all, the funnel is live-only in fact and this file's premise "
            "has changed."
        )

    def test_the_paper_engine_has_no_exchange_executor(self):
        """The accidental property that was, for a while, all that protected a paper order.

        Asserted so it stops being accidental. It is no longer the only protection --
        the order is labelled PAPER and refused at the funnel, and the branch point
        asserts the engine's own environment -- but it is the cheapest of the three and
        the only one that holds even if the other two are bypassed.
        """
        source = self._recover_source()
        before_return = source.split("if is_paper:", 1)[1].split("return", 1)[0]
        construction = before_return.split("ExecutionEngine(", 1)[1].split(")", 1)[0]

        assert "exchange_executor=None" in construction.replace(" ", ""), (
            "the paper ExecutionEngine is no longer built with exchange_executor=None"
        )

    def test_the_tick_loop_has_exactly_one_execution_call_shared_by_both_modes(self):
        """``_inner_loop`` is the per-tick body; ``_run_trading_loop`` wraps it.

        One call for both modes is what made the missing environment argument a single
        point of failure rather than a per-branch oversight -- and is what makes the
        single conditional above sufficient.
        """
        source = _tick_loop_source()
        assert source.count("execute_with_idempotency(") == 1, (
            "the tick loop no longer has a single shared execution call; the mode may "
            "now be branched and this file's premise has changed"
        )

    def test_the_bot_knows_its_mode(self):
        """``paper_trading`` is required keyword-only, so the mode is always established.

        Which is what made the defect a failure to PASS ON information the caller
        already held, rather than information it lacked.
        """
        from backend_app.backend import master_executor as me

        parameter = inspect.signature(me.BotRunner.__init__).parameters["paper_trading"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# 3. What a paper bot's order says about itself
# ---------------------------------------------------------------------------


def test_a_paper_bots_order_states_paper():
    """Was ``xfail(strict=True)``. The marker is gone because the fix landed.

    Kept as a plain assertion on the call site, which is where this property lives: the
    environment of an order is decided entirely by what the caller supplies, so no
    exchange, database or Redis handle is needed to ask the question.

    The call is rendered back from the AST rather than sliced out of the source, and the
    tick loop is resolved by name first so that this cannot pass against some other
    module's call.
    """
    assert "execute_with_idempotency(" in _tick_loop_source(), (
        "the funnel call is no longer in BotRunner._inner_loop"
    )
    rendered = ast.dump(_bot_runner_funnel_call())

    assert "execution_environment" in rendered, (
        "the paper/live mode is not passed to the execution funnel, so the "
        "ExecutionEnvironment.LIVE default decides it and the guard cannot refuse a "
        "paper order"
    )
    assert "paper_trading" in rendered, (
        "the mode passed to the funnel is not derived from self.paper_trading"
    )
    assert "'PAPER'" in rendered, (
        "the funnel call can never be given PAPER, so a paper bot is still labelled live"
    )


def test_the_docstring_warns_that_the_default_disarms_the_guard():
    """The parameter's own documentation, held to the truth.

    It used to justify the LIVE default as merely writing down what every caller already
    meant, on the grounds that the engine had only ever had one environment.
    ``BotRunner`` was a caller with two modes that stated neither, so that justification
    was not available.

    Asserted POSITIVELY -- the warning must be present -- rather than as the absence of
    the old phrase. A correction that explains what it is correcting necessarily
    contains the claim it refutes, so an absence test would forbid the very thing it is
    asking for.
    """
    doc = inspect.getdoc(ee.ExecutionEngine.execute_with_idempotency) or ""

    assert "disarms that guard" in doc.lower(), (
        "the docstring does not warn that the default disarms the environment guard "
        "for any caller that omits it"
    )
    assert "exchange_executor=None" in doc, (
        "the docstring does not record that a null executor was what stopped a paper "
        "order reaching an exchange while this control was disarmed"
    )
    assert "paper" in doc.lower() and "botrunner" in doc.lower(), (
        "the docstring does not name the caller whose paper path reaches this funnel"
    )


# ---------------------------------------------------------------------------
# 4. The other door into the same funnel
# ---------------------------------------------------------------------------


PORTFOLIO = {"total_equity": "10000", "available_balance": "10000", "total_exposure": "0"}


def _engine(**kwargs) -> ee.ExecutionEngine:
    return ee.ExecutionEngine(
        fee_rate=0.001, slippage=0.0, portfolio_state=dict(PORTFOLIO), **kwargs
    )


class _FunnelSpy:
    """Replaces the funnel on one instance, recording what it was told.

    Nothing is executed and no exchange, database or Redis handle is needed: the only
    question is what the delegating wrapper passes down.
    """

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "completed", "execution_id": "spy", "result": {}}

    @property
    def only(self) -> dict:
        assert len(self.calls) == 1, f"expected one funnel call, got {len(self.calls)}"
        return self.calls[0]


async def _execute_trade(engine) -> _FunnelSpy:
    spy = _FunnelSpy()
    engine.execute_with_idempotency = spy
    await engine.execute_trade(
        tenant_id=uuid4(),
        strategy_id="s-1",
        symbol="BTC/USDT",
        side="buy",
        size=Decimal("0.01"),
        price=Decimal("50000"),
    )
    return spy


@pytest.mark.asyncio
class TestTheOtherDoorIntoTheFunnel:
    """``ExecutionEngine.execute_trade`` is a second entry point, and it dropped the mode.

    It is a thin wrapper -- "Unified trade execution interface. Delegates to
    execute_with_idempotency." -- with five production callers in ``backend_app``
    (``dag_risk_integration``, ``execution_router``, ``signal_service`` and both
    ``distributed_execution`` modules). It takes no environment of its own, and it did
    not pass on the engine's.

    That mattered because the engine IS told what it exists for at construction, and two
    of those sites state it: ``dag_risk_integration``'s fallback engine is built ``PAPER``
    and ``routers/strategies.py``'s is built ``BACKTEST``. Their orders then entered the
    funnel wearing the ``LIVE`` default -- the same defect as ``BotRunner``'s, through a
    different door.

    The fix is to forward what the engine was told, and only that. An engine constructed
    without an environment still sends nothing, so the 70 sites that omit it are
    unchanged; this cannot start refusing anything that was not already explicitly
    labelled non-live.
    """

    async def test_a_paper_engines_order_is_not_labelled_live(self):
        spy = await _execute_trade(_engine(execution_environment=ExecutionEnvironment.PAPER))

        assert spy.only.get("execution_environment") is ExecutionEnvironment.PAPER, (
            f"execute_trade sent {spy.only.get('execution_environment', '<nothing>')!r} "
            f"for an engine built for PAPER. The funnel's default then labels the order "
            f"LIVE and assert_live_environment - which exists to refuse PAPER - passes "
            f"it."
        )

    @pytest.mark.parametrize("environment", list(ExecutionEnvironment))
    async def test_whatever_the_engine_was_built_for_is_what_travels(self, environment):
        spy = await _execute_trade(_engine(execution_environment=environment))

        assert spy.only.get("execution_environment") is environment

    async def test_an_unstated_engine_still_sends_nothing(self):
        """The blast radius, bounded by a test rather than by hope.

        Forwarding ``None`` for an engine that was never told its environment would make
        ``assert_live_environment`` refuse, and 70 construction sites omit the argument.
        So omission has to stay silent here, and that is worth pinning: it is the
        difference between this fix and an outage.
        """
        spy = await _execute_trade(_engine())

        assert "execution_environment" not in spy.only, (
            f"execute_trade now sends "
            f"{spy.only['execution_environment']!r} for an engine that was never told "
            f"its environment; every omitting construction site starts being refused"
        )

    async def test_it_does_not_quietly_become_a_parameter_callers_can_override(self):
        """The engine's purpose is not a per-call option.

        If ``execute_trade`` grew an ``execution_environment`` argument, a caller could
        relabel a PAPER engine's order as LIVE, which is the defect with extra steps. The
        value comes from the engine or not at all.
        """
        parameters = inspect.signature(ee.ExecutionEngine.execute_trade).parameters

        assert "execution_environment" not in parameters, (
            "execute_trade now accepts an execution_environment from its caller; the "
            "engine's own environment is the only authority for what it exists for"
        )


# ---------------------------------------------------------------------------
# 5. What the spec says should happen, and what does
# ---------------------------------------------------------------------------


class TestRequirement133IsTheAnswerNotAnOpenQuestion:
    """Requirement 13.3 binds a Paper_Session, and a ``BotRunner`` is not one.

    ``.kiro/specs/marketplace-subscriptions-paper-trading/requirements.md``, Requirement
    13, criterion 3::

        WHEN a Paper_Session emits an order intent, THE Paper_Session SHALL route that
        order intent to the Paper_Simulator, AND SHALL NOT enqueue, forward or copy that
        order intent onto the live order path.

    and criterion 9::

        IF the live order path receives an order whose originating Execution_Environment
        is `PAPER`, THEN THE live order path SHALL reject the order before issuing any
        exchange call ...

    THIS CLASS PREVIOUSLY READ 13.3 AS A RULE ABOUT ALL PAPER EXECUTION, concluded that
    ``BotRunner``'s paper mode was therefore in violation, and treated the refusal that
    followed from labelling its orders as the correct interim state. That reading does
    not survive the same document's glossary: the Paper_Session, the Paper_Simulator, the
    Paper_Account and the Paper_Accounting_Engine are the marketplace paper-trading
    feature, each with its own lifecycle, orders, fills, positions and balances.
    ``BotRunner``'s paper mode is a separate, pre-existing mechanism that simulates inside
    ``ExecutionEngine``. 13.3 does not reach it.

    And 13.9 is satisfied for it on 13.9's own terms -- "reject the order before issuing
    any exchange call". A paper ``BotRunner``'s engine holds no ``exchange_executor`` and
    cannot acquire one and keep simulating, so no exchange call is possible. See
    ``tests/test_simulated_orders_reach_the_simulator.py``.

    So this class no longer records a violation. It records that the two mechanisms are
    still separate -- ``ExecutionEngine``'s internal ``# PAPER EXECUTION PATH`` is not the
    Paper_Simulator at ``backend_app/backend/paper/paper_simulator.py``, and ``BotRunner``
    does not reach it -- which is a consolidation worth doing and not a safety defect.
    """

    def test_the_paper_simulator_exists_and_exposes_an_intent_entry_point(self):
        """The routing target, confirmed present rather than assumed."""
        module = BACKEND / "backend" / "paper" / "paper_simulator.py"
        assert module.is_file(), "the Paper_Simulator module is gone"

        source = module.read_text(encoding="utf-8", errors="replace")
        assert "def submit_intent" in source, (
            "paper_simulator no longer exposes submit_intent; the routing target named "
            "in this class has moved"
        )

    def test_requirement_13_3_still_says_what_this_file_claims(self):
        """So the citation cannot rot while this file keeps quoting it.

        If the requirement is ever revised -- including a decision that the live funnel
        may serve paper after all -- this fails and the whole premise gets re-read
        instead of being inherited.
        """
        spec = (
            REPO_ROOT
            / ".kiro"
            / "specs"
            / "marketplace-subscriptions-paper-trading"
            / "requirements.md"
        )
        if not spec.is_file():
            pytest.skip("the spec is not present in this checkout")

        text = spec.read_text(encoding="utf-8", errors="replace")
        assert "SHALL route that order intent to the Paper_Simulator" in text, (
            "Requirement 13.3's routing rule has changed; re-read Requirement 13 before "
            "trusting this class"
        )
        assert (
            "SHALL NOT enqueue, forward or copy that order intent onto the live order path"
            in text
        )

    def test_botrunner_still_does_not_route_to_the_paper_simulator(self):
        """The remaining gap, stated as a fact about the code rather than a verdict.

        Not an xfail: this asserts what IS true today, so it documents the gap without
        pretending the gap is a failure of this test. When ``BotRunner``'s paper mode is
        bound to a Paper_Session, this fails and goes away with the change.

        MEASURED BY PARSING, NOT BY SEARCHING. ``master_executor`` now carries a comment
        explaining that the Paper_Simulator is where paper intents belong, and a text
        search for ``"paper_simulator"`` matched that comment -- so the test failed on
        the documentation of its own subject. ``_code_identifiers`` reads the AST, where
        comments do not exist.
        """
        from backend_app.backend import master_executor as me

        identifiers = _code_identifiers(me)

        assert "paper_simulator" not in identifiers, (
            "master_executor's CODE now references the Paper_Simulator. If BotRunner's "
            "paper mode has been routed to it, Requirement 13.3 is satisfied -- delete "
            "this test with the change."
        )
        assert "submit_intent" not in identifiers

    def test_the_live_path_guard_still_refuses_paper_outright(self):
        """The guard itself is unchanged, which is what the dispatch rests on.

        Nothing about ``assert_live_environment`` was relaxed to let a paper bot trade.
        What changed is which engines it is applied to. So the one property this file
        depends on -- that it refuses PAPER, rather than warning -- is asserted directly.
        ``tests/test_execution_environment_guard.py`` owns the guard's full contract.
        """
        import asyncio

        from backend_app.backend.execution_environment import assert_live_environment

        with pytest.raises(Exception) as caught:
            asyncio.run(
                assert_live_environment(
                    ExecutionEnvironment.PAPER,
                    actor_id=str(uuid4()),
                    strategy_id="s-1",
                    symbol="BTC/USDT",
                )
            )

        assert "paper" in str(caught.value).lower(), (
            f"the refusal does not name the environment it refused: {caught.value!r}"
        )
