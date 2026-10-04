"""
tests/test_execution_engine_mode_matches_its_branch.py

``ExecutionEngine`` chooses between placing a real order and simulating one by asking
whether it happens to hold an ``exchange_executor``, and nothing checks that the choice
matches what the engine was built for.

P0-05, the half that can be closed without a product decision.

WHAT THE ENGINE ACTUALLY DOES
``_execute_trade_internal`` is a two-branch funnel, and says so in its own comments::

    # LIVE EXECUTION PATH
    if hasattr(self, 'exchange_executor') and self.exchange_executor is not None:
        ...
        result = await self.exchange_executor.place_order(...)

    # PAPER EXECUTION PATH
    fee_rate_dec = Decimal(str(self.fee_rate))
    if side.lower() == 'buy':
        success, message = self.open_position(symbol, price, size, side='long')
    ...
        'order_id': f"paper_{uuid.uuid4().hex[:12]}",

So the paper path is real: it simulates the fill through ``open_position`` /
``close_position``, applies fees and slippage, and returns a ``paper_`` order id. This
matters for the rest of P0-05 -- it is why arming ``assert_live_environment`` at the
funnel entry would break a working paper simulation rather than tightening a dead path.

THE HOLE THAT IS NOT THE LABEL
The branch is selected by a null check on a constructor argument. There is no statement
anywhere that the selected branch is the one this engine was built for. Two leaks follow,
and the environment guard at the funnel entry could not see either of them: at the time
this was written no caller passed it an environment, so its ``ExecutionEnvironment.LIVE``
default meant it believed every order was live. Two doors now state it -- see the note at
the end of this docstring -- but every caller that omits it is still unguarded, so the
branch-point assertion below remains the control that does not depend on being told:

PAPER ORDER ON THE LIVE BRANCH
    ``BotRunner._recover_live_state`` builds the paper engine with
    ``exchange_executor=None``, and that null is the only thing keeping a paper bot's
    order off ``place_order``. Anything that later supplies an executor to that engine --
    a refactor hoisting the construction above the ``if is_paper:`` return, a default, a
    caller passing one for convenience -- turns simulated intent into a real order, with
    nothing left to object.

LIVE ORDER ON THE PAPER BRANCH
    The mirror, and arguably worse. An engine built for live trading that reaches
    ``_execute_trade_internal`` without an executor falls through to the paper branch,
    simulates the fill, and returns ``success`` with a ``paper_`` order id and a
    plausible price. The caller records a filled trade and the trader is told their live
    order executed, while nothing was ever sent to an exchange.

THE FIX, AND WHY IT NEEDS NO DECISION
The engine is told at construction which environment it exists for, and
``_execute_trade_internal`` asserts at the branch point that the branch it is about to
take agrees. Neither assertion can fire on any call site that exists today:

    master_executor.py paper branch   PAPER    + exchange_executor=None  -> paper branch
    master_executor.py live branch    LIVE     + real executor           -> live branch
    dag_risk_integration.py fallback  PAPER    + no executor             -> paper branch
    routers/strategies.py backtest    BACKTEST + no executor             -> paper branch

So nothing that works stops working, and the two leaks above become refusals at the exact
point where they would otherwise happen. This is the relocation the original P0-05 note
described -- the live-only assertion belongs at the exchange boundary, not at the entry to
a funnel both modes legitimately use.

WHAT THIS DOES NOT TOUCH. ``assert_live_environment`` at the funnel entry, its
``ExecutionEnvironment.LIVE`` default, and Requirement 13.9's rule that the funnel refuses
PAPER are all unchanged by THIS file.

They were changed afterwards, elsewhere, and
``tests/test_execution_environment_is_stated_not_defaulted.py`` owns that half:
``BotRunner._inner_loop`` now passes ``PAPER if self.paper_trading else LIVE``, and
``ExecutionEngine.execute_trade`` now forwards the engine's own
``execution_environment``, so a PAPER or BACKTEST engine's order is no longer relabelled
LIVE on the way into the funnel. Requirement 13.3 -- route a paper order intent to the
Paper_Simulator instead of refusing it on the live path -- is the part still open.

The two controls are independent on purpose. This one holds for the 70 call sites that
still say nothing, because it reads what the ENGINE was constructed for rather than what
a caller remembered to pass.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.execution_environment import ExecutionEnvironment
from backend_app.core.execution_engine import ExecutionEngine

PORTFOLIO = {"total_equity": "10000", "available_balance": "10000", "total_exposure": "0"}


class _Executor:
    """Stands in for a real exchange executor. Records whether it was reached."""

    def __init__(self):
        self.orders = []
        self._current_execution_id = None
        self._current_validation_token = None

    async def place_order(self, **kwargs):
        self.orders.append(kwargs)
        raise AssertionError(
            "place_order was reached; a non-live engine must be refused before this"
        )


def _engine(environment, executor=None) -> ExecutionEngine:
    return ExecutionEngine(
        fee_rate=0.001,
        slippage=0.0,
        portfolio_state=dict(PORTFOLIO),
        exchange_executor=executor,
        execution_environment=environment,
    )


# ---------------------------------------------------------------------------
# 1. The engine is told what it is for
# ---------------------------------------------------------------------------


class TestTheEngineKnowsItsEnvironment:
    def test_it_is_keyword_only(self):
        """So it cannot be supplied positionally by accident on a 5-argument signature."""
        parameter = inspect.signature(ExecutionEngine.__init__).parameters[
            "execution_environment"
        ]

        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    def test_omitting_it_is_allowed_and_leaves_the_engine_unstated(self):
        """STATED IS ENFORCED; omitted is not, and that is deliberate.

        This argument was written as required first. Backed out: there are 71
        construction sites in this repository and mandating it turns every one into a
        ``TypeError``, including four live paths in ``backend_app`` -- breaking execution
        to protect it. ``test_the_sites_that_do_not_state_their_environment`` keeps the
        remaining count visible instead.
        """
        engine = ExecutionEngine(portfolio_state=dict(PORTFOLIO))

        assert engine.execution_environment is None

    @pytest.mark.parametrize("member", list(ExecutionEnvironment))
    def test_every_member_is_accepted_and_recorded(self, member):
        engine = _engine(member)

        assert engine.execution_environment is member

    def test_an_unrecognised_value_is_refused_at_construction(self):
        """Refused where it is cheapest, and never coerced to unstated."""
        for value in ("", "live-ish", "PAPERX", 1, object()):
            with pytest.raises(ValueError):
                _engine(value)

    def test_an_explicit_none_is_refused_rather_than_treated_as_omitted(self):
        """The distinction a plain ``None`` default could not make.

        A caller passing something meant to be an environment and handing over nothing
        has a bug. Downgrading that to "unenforced" is precisely how the guard at
        ``execute_with_idempotency`` ended up disarmed, so the sentinel keeps the two
        cases apart.
        """
        with pytest.raises(ValueError):
            _engine(None)

    def test_the_exact_spelling_is_accepted(self):
        """The parse is the shared one, so the engine cannot invent its own vocabulary."""
        assert _engine("LIVE").execution_environment is ExecutionEnvironment.LIVE

    @pytest.mark.parametrize("spelling", ["live", "Live", "LIVE "])
    def test_a_mis_cased_or_padded_spelling_is_refused(self, spelling):
        """Deliberately strict, and not this engine's choice.

        ``parse_execution_environment`` accepts the member or its exact spelling and
        nothing else -- ``test_execution_environment_guard.py`` pins that with
        ``test_parse_refuses_anything_but_an_exact_spelling``. Reusing it means the engine
        cannot quietly become more permissive than the guard about what "LIVE" looks
        like, which is the right trade for a value that decides whether real money moves.
        """
        with pytest.raises(ValueError):
            _engine(spelling)


# ---------------------------------------------------------------------------
# 2. A non-live engine cannot take the live branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestANonLiveEngineCannotPlaceARealOrder:
    @pytest.mark.parametrize(
        "environment", [ExecutionEnvironment.PAPER, ExecutionEnvironment.BACKTEST]
    )
    async def test_it_is_refused_before_place_order(self, environment):
        """The leak: a paper engine that acquires an executor places a real order.

        ``_Executor.place_order`` raises if reached, so this cannot pass by the order
        merely failing downstream.
        """
        executor = _Executor()
        engine = _engine(environment, executor=executor)

        success, detail = await engine._execute_trade_internal(
            execution_id="e-1",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.01"),
            price=Decimal("50000"),
        )

        assert success is False, (
            f"a {environment.value} engine holding an exchange executor executed: {detail}"
        )
        assert executor.orders == [], "place_order was reached"

    async def test_the_refusal_names_the_mismatch(self):
        executor = _Executor()
        engine = _engine(ExecutionEnvironment.PAPER, executor=executor)

        _success, detail = await engine._execute_trade_internal(
            execution_id="e-2",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.01"),
            price=Decimal("50000"),
        )

        rendered = str(detail).lower()
        assert "paper" in rendered and (
            "live" in rendered or "exchange" in rendered
        ), f"the refusal does not say what disagreed: {detail!r}"


# ---------------------------------------------------------------------------
# 3. A live engine cannot silently simulate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestALiveEngineCannotSilentlySimulate:
    async def test_a_live_engine_with_no_executor_is_refused(self):
        """The mirror leak, and the more dangerous direction.

        Falling through to the paper branch returns ``success`` with a ``paper_`` order
        id and a plausible price, so the caller records a filled trade and the trader is
        told their live order executed while nothing reached an exchange.
        """
        engine = _engine(ExecutionEnvironment.LIVE, executor=None)

        success, detail = await engine._execute_trade_internal(
            execution_id="e-3",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.01"),
            price=Decimal("50000"),
        )

        assert success is False, (
            f"a LIVE engine with no exchange executor simulated the fill and reported "
            f"success: {detail}"
        )

    async def test_it_does_not_return_a_paper_order_id(self):
        engine = _engine(ExecutionEnvironment.LIVE, executor=None)

        _success, detail = await engine._execute_trade_internal(
            execution_id="e-4",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.01"),
            price=Decimal("50000"),
        )

        assert "paper_" not in str(detail), (
            f"a LIVE engine produced a simulated order id: {detail!r}"
        )


# ---------------------------------------------------------------------------
# 4. Both modes that exist today still work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheTwoCombinationsThatExistTodayStillWork:
    async def test_a_paper_engine_with_no_executor_still_simulates(self):
        """The combination `BotRunner`'s paper fast-path builds. It must keep working."""
        engine = _engine(ExecutionEnvironment.PAPER, executor=None)

        success, detail = await engine._execute_trade_internal(
            execution_id="e-5",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.01"),
            price=Decimal("50000"),
        )

        assert success is True, f"paper simulation stopped working: {detail}"
        assert str(detail["order_id"]).startswith("paper_")

    async def test_a_backtest_engine_with_no_executor_still_simulates(self):
        """The combination the legacy backtest loop builds."""
        engine = _engine(ExecutionEnvironment.BACKTEST, executor=None)

        success, _detail = await engine._execute_trade_internal(
            execution_id="e-6",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.01"),
            price=Decimal("50000"),
        )

        assert success is True


# ---------------------------------------------------------------------------
# 5. Every construction site states its environment
# ---------------------------------------------------------------------------


EXPECTED_SITES = {
    "backend_app/backend/master_executor.py": {"PAPER", "LIVE"},
    "backend_app/backend/dag_risk_integration.py": {"PAPER"},
    "backend_app/routers/strategies.py": {"BACKTEST"},
    # A validation harness that prints a markdown pass/fail report and overrides
    # `apply_slippage` for determinism. BACKTEST is readable from the code, which is why
    # it could be declared first.
    "backend_app/validate_financials.py": {"BACKTEST"},
    # LIVE, decided after measuring who can reach them: nobody. Both construct an engine
    # with no exchange executor on a path whose own name claims one -- a module called
    # `idempotent_exchange_submission` returning an `exchange_order_id`, and a class
    # called `ExchangeGateway` -- so the simulated fill was being reported as an exchange
    # result. The label was held back while it looked like a product decision; it is not,
    # because the behaviour it decides is unreachable (`submit_order_idempotent` has no
    # callers, and the gateway only exists if `ExecutionOrchestrator.initialize()` is
    # called, which nothing does). Stating LIVE turns a latent trap into a refusal for
    # whoever wires them up, with no live path to break.
    # Measured in tests/test_execution_paths_that_claim_an_exchange.py.
    "backend_app/backend/distributed_execution/execution_worker.py": {"LIVE"},
    "backend_app/backend/distributed_execution/idempotent_exchange_submission.py": {"LIVE"},
}

#: Construction sites that do NOT state their environment, so are unenforced.
#:
#: Listed rather than summarised so the set can be driven down one at a time and cannot
#: quietly grow. One left:
#:
#:   execution_router.py
#:       `POST /api/execution/signal` constructs the engine with no `portfolio_state` at
#:       all, so it raises before reaching any branch - the endpoint returns 500 on every
#:       request. And repairing that only reaches `execute_with_idempotency` with `source`
#:       defaulted, which answers `status: 'blocked'`. The route is dead by design as well
#:       as by accident, so a label changes nothing about it.
#:
#: Pinned with its measurement in tests/test_execution_paths_that_claim_an_exchange.py.
UNSTATED_PRODUCTION_SITES = {
    "backend_app/backend/execution_router.py",
}


def _construction_sites(base: str) -> dict:
    """``{relative path: {stated environments}}`` for every engine construction."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    found: dict = {}
    for path in (root / base).rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name not in ("ExecutionEngine", "UnifiedExecutionEngine"):
                continue
            relative = path.relative_to(root).as_posix()
            stated = found.setdefault(relative, set())
            for keyword in node.keywords:
                if keyword.arg != "execution_environment":
                    continue
                value = keyword.value
                if isinstance(value, ast.Attribute):
                    stated.add(value.attr)
                elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                    stated.add(value.value.upper())
    return found


def test_the_sites_that_do_not_state_their_environment():
    """A recorded, shrinkable gap -- not a pass/fail on the whole repository.

    The protection is opt-in by construction site, so the useful assertion is that the
    unenforced set has not GROWN. A new engine built on a live path without saying what
    it is for is the thing to catch.
    """
    sites = _construction_sites("backend_app")
    unstated = {relative for relative, stated in sites.items() if not stated}

    assert unstated <= UNSTATED_PRODUCTION_SITES, (
        f"new production ExecutionEngine construction site(s) that do not state their "
        f"environment: {sorted(unstated - UNSTATED_PRODUCTION_SITES)}. State it, or add "
        f"it to UNSTATED_PRODUCTION_SITES with a reason."
    )


def test_the_sites_that_do_state_it_are_the_ones_expected():
    sites = _construction_sites("backend_app")
    stated = {relative for relative, values in sites.items() if values}

    assert stated == set(EXPECTED_SITES), (
        f"the set of sites stating their environment changed: {sorted(stated)}"
    )


@pytest.mark.parametrize("relative", sorted(EXPECTED_SITES))
def test_each_construction_site_states_its_environment(relative):
    """Parsed, so a site that constructs an engine without saying what for is caught.

    The required keyword-only argument already makes omission a ``TypeError`` at runtime,
    but these are code paths with no test coverage of their own -- this is what notices
    before a deployment does.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    tree = ast.parse((root / relative).read_text(encoding="utf-8", errors="replace"))

    stated = set()
    constructions = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "ExecutionEngine":
            continue
        constructions += 1
        for keyword in node.keywords:
            if keyword.arg != "execution_environment":
                continue
            value = keyword.value
            if isinstance(value, ast.Attribute):
                stated.add(value.attr)
            elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                stated.add(value.value.upper())

    assert constructions, f"{relative} no longer constructs an ExecutionEngine"
    assert stated == EXPECTED_SITES[relative], (
        f"{relative} states {sorted(stated)}, expected "
        f"{sorted(EXPECTED_SITES[relative])}"
    )
