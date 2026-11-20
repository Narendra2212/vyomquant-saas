"""
tests/test_simulated_orders_reach_the_simulator.py

Labelling a paper bot's order ``PAPER`` at the funnel entry -- the P0-05 fix -- stopped
paper trading, because ``assert_live_environment`` refuses ``PAPER`` and every bot
deployed through ``POST /api/strategies/{id}/deploy`` is a paper bot.

LAUNCH BLOCKER, INTRODUCED BY THE FIX IT IS PART OF.

HOW THE REGRESSION WAS REACHED
``routers/strategies.py`` starts bots with ``paper_trading=True`` hardcoded, at two call
sites (the deploy route and the DAG deploy route). ``strategy_service.py`` starts them
with ``paper_trading=(mode != MODE_LIVE)``. So paper is the ordinary path, not an edge
case, and ``BotRunner`` is the runtime for both modes: ``_recover_live_state`` builds an
``ExecutionEngine`` on each branch and the tick loop in ``_inner_loop`` makes one shared
``execute_with_idempotency`` call.

Stating the order's environment at that call was correct and is kept. What was wrong was
the conclusion drawn from it -- that a paper bot must therefore stop executing until its
orders are routed to the marketplace Paper_Simulator. The tick loop's ``except Exception``
catches the refusal, rolls back exposure and continues, so the observable result is a bot
that logs an error on every signal and never records a fill.

WHAT REQUIREMENT 13 ACTUALLY BINDS
``.kiro/specs/marketplace-subscriptions-paper-trading/requirements.md``, Requirement 13:

    3. WHEN a Paper_Session emits an order intent, THE Paper_Session SHALL route that
       order intent to the Paper_Simulator, AND SHALL NOT enqueue, forward or copy that
       order intent onto the live order path.
    9. IF the live order path receives an order whose originating Execution_Environment
       is `PAPER`, THEN THE live order path SHALL reject the order before issuing any
       exchange call, SHALL return an error indicating an Execution_Environment mismatch,
       SHALL leave every Paper_Account balance and every real-money balance unchanged,
       and SHALL record the rejection in the Audit_Log.
    10. IF an order carries no Execution_Environment value or a value outside the 3
        defined values, THEN THE live order path SHALL reject the order before issuing
        any exchange call, SHALL NOT default that order to `LIVE`, and SHALL return an
        error indicating an unresolved Execution_Environment.

Three things follow, and together they decide this:

13.3 BINDS A Paper_Session, WHICH A BotRunner IS NOT. The Paper_Session, the
Paper_Simulator, the Paper_Account and the Paper_Accounting_Engine are the marketplace
paper-trading feature defined in the same document's glossary. ``BotRunner``'s paper mode
is a separate, pre-existing mechanism that simulates inside ``ExecutionEngine``. Reading
13.3 as a rule about every paper execution in the platform is what produced the refusal.

13.9's OPERATIVE CLAUSE IS "BEFORE ISSUING ANY EXCHANGE CALL". That is what the
requirement protects, and it is stated in the user story: "absolute certainty that no real
order can leave the platform." An ``ExecutionEngine`` constructed with
``exchange_executor=None`` issues no exchange call, and since
``tests/test_execution_engine_mode_matches_its_branch.py`` it cannot acquire one and keep
simulating either -- the branch point refuses a ``PAPER`` engine that holds an executor,
and refuses a ``LIVE`` engine that does not.

13.10 CONDEMNS THE PRE-FIX STATE DIRECTLY, so reverting is not available. An order
carrying no environment "SHALL NOT default that order to `LIVE`", and the parameter's
default is ``ExecutionEnvironment.LIVE``. Going back to BotRunner saying nothing trades one
violation for another.

``backend/execution_environment.py``'s own module docstring says what the guard is for:
``assert_live_environment`` is placed so that "there is no live order path that reaches an
exchange without passing it". The live order path is the path that can reach an exchange.

WHAT CHANGED
``execute_with_idempotency`` now asks which it is before choosing a guard:

    a DECLARED SIMULATOR -- the engine states PAPER or BACKTEST and holds no
    exchange_executor -- gets `assert_simulation_environment_matches`, which still refuses
    an unresolved environment (13.10) and refuses any order whose environment disagrees
    with the engine's, auditing the refusal the same way.

    everything else -- an engine that holds an executor, an engine that states LIVE, and
    an engine that states nothing at all -- gets `assert_live_environment` exactly as
    before.

The second clause is what keeps this from being a weakening. ``assert_live_environment``
is unchanged, and it still guards every engine that could reach an exchange and every
engine whose purpose was never stated.

THE MATRIX IS THE ARGUMENT, so it is enumerated below rather than asserted in prose. The
only combination that reaches ``place_order`` is: the engine states LIVE, the engine holds
an executor, and the order says LIVE. Every combination that was refused before this
change is still refused.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.execution_environment import (
    ExecutionEnvironment, ExecutionEnvironmentError,
    ExecutionEnvironmentMismatch, UnresolvedExecutionEnvironment)
from backend_app.core.execution_engine import ExecutionEngine

pytestmark = pytest.mark.usefixtures("live_platform_enabled")  # RT-15


PORTFOLIO = {"total_equity": "10000", "available_balance": "10000", "total_exposure": "0"}

PAPER = ExecutionEnvironment.PAPER
BACKTEST = ExecutionEnvironment.BACKTEST
LIVE = ExecutionEnvironment.LIVE

#: Sentinel for "the engine was never told what it exists for" -- 65 of the 71
#: construction sites in this repository. Distinct from stating a value.
UNSTATED = "<unstated>"


class _Executor:
    """Stands in for a real exchange executor. Raises if an order reaches it."""

    def __init__(self):
        self.orders = []
        self._current_execution_id = None
        self._current_validation_token = None

    async def place_order(self, **kwargs):
        self.orders.append(kwargs)
        raise AssertionError("place_order was reached")


def _engine(engine_environment, *, executor=None) -> ExecutionEngine:
    kwargs = {}
    if engine_environment is not UNSTATED:
        kwargs["execution_environment"] = engine_environment
    return ExecutionEngine(
        fee_rate=0.001,
        slippage=0.0,
        portfolio_state=dict(PORTFOLIO),
        exchange_executor=executor,
        **kwargs,
    )


async def _guard_verdict(engine, order_environment):
    """``"refused"`` or ``"admitted"``, plus whatever was raised.

    The guard is the first statement of ``execute_with_idempotency``, before any
    repository, session or Redis handle, so a refusal needs no infrastructure. An order
    the guard ADMITS goes on to fail somewhere else in this environment -- there is no
    database -- and that is the point: the only question here is whether STEP 0 let it
    past, which is why the outcome is classified by exception type rather than by result.
    """
    try:
        await engine.execute_with_idempotency(
            tenant_id=uuid4(),
            strategy_id="s-1",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.01"),
            price=Decimal("50000"),
            source="bot_runner",
            execution_environment=order_environment,
        )
    except ExecutionEnvironmentError as exc:
        return "refused", exc
    except BaseException as exc:  # noqa: BLE001 - anything else is past the guard
        return "admitted", exc
    return "admitted", None


# ---------------------------------------------------------------------------
# 1. The regression this file exists to stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAPaperBotCanStillTrade:
    async def test_a_paper_order_on_a_paper_engine_is_not_refused(self):
        """The launch blocker, stated as the one thing that must be true.

        ``BotRunner``'s paper fast-path builds exactly this engine: states ``PAPER``,
        ``exchange_executor=None``. Its orders say ``PAPER``. If the funnel refuses that
        combination, every paper bot on the platform stops executing.
        """
        verdict, raised = await _guard_verdict(_engine(PAPER), PAPER)

        assert verdict == "admitted", (
            f"a PAPER order was refused by a PAPER engine that cannot reach an "
            f"exchange, so paper trading does not work: {raised!r}"
        )

    async def test_the_paper_simulation_still_produces_a_fill(self):
        """Admitted is not enough; it has to simulate.

        Taken at ``_execute_trade_internal``, past the funnel's database work, because
        the question is whether the paper branch still runs for a correctly-labelled
        paper order.
        """
        engine = _engine(PAPER)

        success, detail = await engine._execute_trade_internal(
            execution_id="e-1",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.01"),
            price=Decimal("50000"),
        )

        assert success is True, f"the paper branch stopped simulating: {detail}"
        assert str(detail["order_id"]).startswith("paper_")

    async def test_a_backtest_order_on_a_backtest_engine_is_not_refused(self):
        verdict, raised = await _guard_verdict(_engine(BACKTEST), BACKTEST)

        assert verdict == "admitted", f"a BACKTEST order was refused: {raised!r}"


# ---------------------------------------------------------------------------
# 2. Everything that was refused before is still refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestNothingThatWasRefusedBecomesAdmitted:
    async def test_a_paper_order_on_an_engine_that_holds_an_executor_is_refused(self):
        """The engine could reach an exchange, so it is the live order path. 13.9."""
        engine = _engine(PAPER, executor=_Executor())

        verdict, raised = await _guard_verdict(engine, PAPER)

        assert verdict == "refused"
        assert isinstance(raised, ExecutionEnvironmentMismatch)

    async def test_a_paper_order_on_an_unstated_engine_is_refused(self):
        """An engine that never said what it exists for is not a declared simulator.

        This is the conservative half of the change and the reason it is not a
        weakening: 65 construction sites state nothing, and every one of them keeps the
        live-path guard.
        """
        verdict, raised = await _guard_verdict(_engine(UNSTATED), PAPER)

        assert verdict == "refused"
        assert isinstance(raised, ExecutionEnvironmentMismatch)

    async def test_a_paper_order_on_a_live_engine_is_refused(self):
        engine = _engine(LIVE, executor=_Executor())

        verdict, raised = await _guard_verdict(engine, PAPER)

        assert verdict == "refused"
        assert isinstance(raised, ExecutionEnvironmentMismatch)

    async def test_a_live_order_on_a_paper_engine_is_refused(self):
        """The mirror, and the more dangerous direction.

        A ``LIVE`` order admitted by a simulator would be simulated and returned as a
        successful fill with a ``paper_`` order id, and the trader would be told their
        live order executed. The branch point refuses this too; the funnel refusing it
        first means it never reaches the database either.
        """
        verdict, raised = await _guard_verdict(_engine(PAPER), LIVE)

        assert verdict == "refused", (
            f"a LIVE order was admitted by an engine built to simulate: {raised!r}"
        )
        assert isinstance(raised, ExecutionEnvironmentMismatch)

    async def test_a_backtest_order_on_a_paper_engine_is_refused(self):
        """Agreement, not merely "non-live". Two simulators are not interchangeable."""
        verdict, raised = await _guard_verdict(_engine(PAPER), BACKTEST)

        assert verdict == "refused"
        assert isinstance(raised, ExecutionEnvironmentMismatch)

    @pytest.mark.parametrize(
        "engine_environment", [UNSTATED, PAPER, BACKTEST, LIVE]
    )
    @pytest.mark.parametrize(
        "order_environment", [None, "", "live", "Live", "LIVE ", "PAPERX", 0, True]
    )
    async def test_an_unresolved_environment_is_refused_on_every_engine(
        self, engine_environment, order_environment
    ):
        """Requirement 13.10, which applies whichever guard is chosen.

        "SHALL NOT default that order to `LIVE`" -- so a simulator cannot be the loophole
        through which an unlabelled order becomes acceptable. Exact spelling only:
        ``"live"`` and ``"LIVE "`` are unresolved, not LIVE.
        """
        engine = _engine(
            engine_environment,
            executor=_Executor() if engine_environment is LIVE else None,
        )

        verdict, raised = await _guard_verdict(engine, order_environment)

        assert verdict == "refused", (
            f"order_environment={order_environment!r} was admitted by a "
            f"{engine_environment} engine: {raised!r}"
        )
        assert isinstance(raised, UnresolvedExecutionEnvironment)


# ---------------------------------------------------------------------------
# 3. The live path is untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTheLivePathIsUnchanged:
    async def test_a_live_order_on_a_live_engine_with_an_executor_is_admitted(self):
        """The only combination that may reach ``place_order``."""
        engine = _engine(LIVE, executor=_Executor())

        verdict, raised = await _guard_verdict(engine, LIVE)

        assert verdict == "admitted", f"live execution was refused: {raised!r}"

    async def test_a_live_order_on_an_unstated_engine_is_admitted(self):
        """Today's behaviour for the 65 sites that state nothing, preserved exactly."""
        verdict, raised = await _guard_verdict(_engine(UNSTATED), LIVE)

        assert verdict == "admitted", f"{raised!r}"

    async def test_a_live_engine_with_no_executor_cannot_silently_simulate(self):
        """Admitted by the funnel, refused at the branch point. Both are required.

        The funnel cannot refuse it -- the order and the engine agree, and they say LIVE
        -- so the branch point is what stops a live order being simulated and reported as
        filled.
        """
        engine = _engine(LIVE, executor=None)

        funnel, _raised = await _guard_verdict(engine, LIVE)
        assert funnel == "admitted"

        success, detail = await engine._execute_trade_internal(
            execution_id="e-2",
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.01"),
            price=Decimal("50000"),
        )

        assert success is False, (
            f"a LIVE engine with no executor simulated the fill: {detail}"
        )
        assert "paper_" not in str(detail)


# ---------------------------------------------------------------------------
# 4. No order reaches an exchange unless everything says LIVE
# ---------------------------------------------------------------------------


ADMITTED = "admitted"
REFUSED = "refused"

#: ``(engine environment, holds executor, order environment) -> funnel verdict``.
#:
#: Enumerated rather than summarised because this is the safety argument. The rule the
#: table encodes:
#:
#:   * engine stated PAPER or BACKTEST with NO executor -- a declared simulator -- admits
#:     exactly its own environment and refuses everything else, unresolved included.
#:   * every other engine keeps `assert_live_environment`: LIVE is admitted, PAPER and
#:     BACKTEST are refused, unresolved is refused.
#:
#: ONE ROW IS ADMITTED BY THE FUNNEL AND REFUSED LATER: `(PAPER, executor, LIVE)`. The
#: engine states it simulates yet holds an exchange executor, so it is not a declared
#: simulator and keeps the live-path guard -- which admits a LIVE order. The branch-point
#: assertion in `_execute_trade_internal` is what refuses it, and
#: `test_a_misconfigured_engine_is_refused_at_the_branch_point` asserts that. Tightening
#: the funnel for it would mean giving an executor-bearing engine something other than the
#: live-path guard, which is the one trade this change refuses to make.
MATRIX = [
    (UNSTATED, False, LIVE, ADMITTED),
    (UNSTATED, False, PAPER, REFUSED),
    (UNSTATED, False, BACKTEST, REFUSED),
    (UNSTATED, True, LIVE, ADMITTED),
    (UNSTATED, True, PAPER, REFUSED),
    (LIVE, True, LIVE, ADMITTED),
    (LIVE, True, PAPER, REFUSED),
    (LIVE, True, BACKTEST, REFUSED),
    (LIVE, False, LIVE, ADMITTED),
    (LIVE, False, PAPER, REFUSED),
    (PAPER, False, PAPER, ADMITTED),
    (PAPER, False, LIVE, REFUSED),
    (PAPER, False, BACKTEST, REFUSED),
    (PAPER, True, PAPER, REFUSED),
    (PAPER, True, LIVE, ADMITTED),
    (BACKTEST, False, BACKTEST, ADMITTED),
    (BACKTEST, False, LIVE, REFUSED),
    (BACKTEST, False, PAPER, REFUSED),
    (BACKTEST, True, BACKTEST, REFUSED),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_environment,holds_executor,order_environment,expected", MATRIX
)
async def test_the_whole_matrix(
    engine_environment, holds_executor, order_environment, expected
):
    engine = _engine(
        engine_environment, executor=_Executor() if holds_executor else None
    )

    verdict, raised = await _guard_verdict(engine, order_environment)

    assert verdict == expected, (
        f"engine={engine_environment} executor={holds_executor} "
        f"order={order_environment}: expected {expected}, got {verdict} ({raised!r})"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_environment,holds_executor",
    [(PAPER, True), (BACKTEST, True)],
)
async def test_a_misconfigured_engine_is_refused_at_the_branch_point(
    engine_environment, holds_executor
):
    """The second half of the argument, for the row the funnel admits.

    An engine that states it simulates while holding an exchange executor is
    misconfigured, and the branch point refuses it every order rather than placing one --
    including the LIVE order the funnel let through.
    """
    executor = _Executor()
    engine = _engine(engine_environment, executor=executor)

    success, detail = await engine._execute_trade_internal(
        execution_id="e-3",
        symbol="BTC/USDT",
        side="buy",
        size=Decimal("0.01"),
        price=Decimal("50000"),
    )

    assert success is False, (
        f"a {engine_environment} engine holding an executor executed: {detail}"
    )
    assert executor.orders == [], "place_order was reached"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine_environment,holds_executor,order_environment,expected", MATRIX
)
async def test_which_combinations_can_actually_place_a_real_order(
    engine_environment, holds_executor, order_environment, expected
):
    """Measured by whether ``place_order`` was called, not inferred from the verdict.

    AN EARLIER VERSION OF THIS FILE CLAIMED ONLY ONE COMBINATION COULD REACH AN EXCHANGE,
    AND THAT WAS FALSE. It filtered the table on ``engine is LIVE``, which quietly
    excluded ``(UNSTATED, executor, LIVE)`` -- an engine that never stated its purpose,
    holding a real executor, given a LIVE order. That is a genuine live execution path and
    it is how most of this repository's engines are built: 65 of 71 construction sites
    state nothing, so the branch-point assertion does not apply to them and nothing checks
    that the branch matches a purpose never declared.

    So there are TWO ways to reach an exchange, and the difference between them is the
    whole reason the unstated construction sites are being driven down one at a time:

        engine LIVE     + executor + order LIVE   -- checked at both the funnel and the
                                                     branch point
        engine UNSTATED + executor + order LIVE   -- checked at the funnel only

    Asserted per row so a change that opens a third way fails here, and so the list
    cannot be edited without the measurement being retaken.
    """
    may_reach_exchange = (
        holds_executor
        and order_environment is LIVE
        and engine_environment in (LIVE, UNSTATED)
    )

    executor = _Executor() if holds_executor else None
    engine = _engine(engine_environment, executor=executor)

    # TRADE-05. This row drives `_execute_trade_internal` directly, skipping
    # `execute_with_idempotency` where the risk verdict is recorded. The gateway now
    # refuses a live order whose `_live_risk_approved` is not True, so without this the
    # two ADMITTED live rows would report `reached=False` for the wrong reason and this
    # matrix would stop measuring what the environment guards do.
    #
    # Set for every row, not only the live ones, so the matrix keeps measuring exactly
    # one variable. The risk requirement itself is asserted from the public entry point in
    # tests/test_trade04_live_order_gate_at_venue_boundary.py.
    engine._live_risk_approved = True
    engine._live_risk_reason = "risk approved (test precondition)"
    engine._live_tenant_id = "00000000-0000-0000-0000-000000000001"
    engine._live_strategy_id = "s-1"

    verdict, _raised = await _guard_verdict(engine, order_environment)
    if verdict == REFUSED:
        assert not may_reach_exchange, (
            f"engine={engine_environment} executor={holds_executor} "
            f"order={order_environment} was expected to be able to trade but the funnel "
            f"refused it"
        )
        return

    # `_guard_verdict` goes through `execute_with_idempotency`, which overwrites the
    # verdict with its own (unset) default. Re-establish it for the direct call below.
    engine._live_risk_approved = True

    await engine._execute_trade_internal(
        execution_id="e-4",
        symbol="BTC/USDT",
        side="buy",
        size=Decimal("0.01"),
        price=Decimal("50000"),
    )
    reached = bool(executor and executor.orders)

    assert reached == may_reach_exchange, (
        f"engine={engine_environment} executor={holds_executor} "
        f"order={order_environment}: place_order reached={reached}, expected "
        f"{may_reach_exchange}"
    )
