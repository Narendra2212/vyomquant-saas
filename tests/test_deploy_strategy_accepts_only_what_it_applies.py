"""
tests/test_deploy_strategy_accepts_only_what_it_applies.py

``StrategyService.deploy_strategy`` accepts four risk and capital controls and stores none
of them, and the blueprint it hands the fleet cannot start a bot.

PART ONE -- FOUR PARAMETERS ACCEPTED AND DISCARDED
``StrategyDeployRequest`` declares them, the route forwards them, and the signature takes
them::

    async def deploy_strategy(
        self, user, strategy_id, version=None, environment="paper",
        exchange_id=None, exchange_account_id=None,
        capital: Optional[float] = None,
        trade_size_pct: Optional[float] = None,
        stop_loss_pct: Optional[float] = None,
        max_drawdown_pct: Optional[float] = None,
        **kwargs,
    )

The row it inserts contains none of the four::

    deployment_data = {
        "id": deployment_id, "strategy_id": strategy_id, "user_id": user["id"],
        "version_id": ..., "version": ..., "environment": environment,
        "exchange_id": ..., "exchange_symbol": ..., "status": "deploying",
        "worker_region": "us-east-1", "created_at": ...,
    }

A repo-wide search for ``trade_size_pct`` inside ``strategy_service.py`` returns exactly
one line -- the signature. ``max_drawdown_pct`` likewise appears nowhere in the live path;
its only other occurrences are in the backtest engine and in ``circuit_breaker``, where it
means something else.

AND THERE IS NOWHERE FOR THEM TO GO. ``004e_deployment_bindings.sql`` adds
``exchange_account_id``, ``risk_config_id``, ``execution_config``, ``mode`` and
``dag_hash`` -- that is all. ``deployment_binding.EXECUTION_CONFIG_FIELDS`` is
``("max_order_notional", "max_open_positions", "slippage_tolerance_bps",
"order_timeout_seconds", "retry_policy")`` and ``normalise_execution_config`` *refuses*
unknown keys, so the JSONB column will not take them either. Per-trade risk lives on
``strategies.risk`` (``position_size_pct`` / ``stop_loss_pct`` / ``take_profit_pct``),
which is what ``strategy_builder.StrategyEngine.run_logic`` reads on the live path.

So these cannot be persisted without a migration against a populated production table,
and the repository states it cannot provision its own database. They are therefore
REFUSED rather than stored or reported: a trader who sets a 2% stop and gets a deployment
with no stop at all is worse off than one who gets an error. That is the difference
between this and the backtest's ``configuration`` echo, which is a report of a
*simulation* and is left in place with its inert fields declared.

PART TWO -- THE BLUEPRINT CANNOT START A BOT
Measured, not inferred. ``deploy_strategy`` ends with::

    success, message = await app_state.fleet.start_bot(
        user_id=user["id"],
        symbol=strategy["strategy"]["symbol"],
        blueprint=version_data["blueprint"],
        paper_trading=(mode != db_binding.MODE_LIVE),
    )

``create_version`` writes ``blueprint = canonical_columns["graph_json"]`` whenever no
explicit blueprint is passed, and ``_persist_current_version`` -- the only live caller --
passes none. So for every version saved through the current path, ``blueprint`` is
``StrategyGraph.to_dict()``, whose keys are exactly::

    ['edges', 'metadata', 'name', 'nodes', 'schema_version', 'strategy_id',
     'validation_state', 'version']

``StrategyGraph``'s own docstring says "There is deliberately no ``exchange`` field
(SB-06)". And ``BotRunner.__init__`` does::

    self.exchange_id = strategy_blueprint.get("exchange_id")
    if not self.exchange_id:
        raise ValueError("exchange_id must be provided in strategy blueprint")

Run against that dict, it raises exactly that. ``fleet_manager.start_bot`` catches it and
returns ``(False, "Bot initialisation failed: ...")``, and ``deploy_strategy`` then writes
``strategy_deployments.status = 'failed'`` and ``strategies.status = 'failed'``. Even past
the constructor, ``run_logic`` requires ``buy_logic``, ``sell_logic``, ``risk`` and
``strategy_id``; that dict is missing the first three, so every tick would be skipped.

It fails CLOSED and it fails LOUDLY, which is why this is not a P0. But the route cannot
deploy, and today it discovers that only after inserting a ``strategy_deployments`` row
and calling the fleet. The refusal is moved ahead of the insert and told to name what is
missing.

WHAT IS DELIBERATELY NOT DONE HERE. Completing the blueprint -- adding ``exchange_id``,
``buy_logic``, ``sell_logic`` and ``risk`` off the strategy row -- would turn a path that
has never successfully started a bot into one that can, including with
``paper_trading=False``. That is enabling live execution on an untravelled route, and it
needs an owner and a plan, not a drive-by repair. ``routers/strategies.py``'s own deploy
endpoint already assembles a complete blueprint (``_lift_dag_fields`` plus an explicit
``blueprint["exchange_id"] = ...``), so the working shape exists to copy when someone
decides to.

A LEGACY ROW MAY STILL PASS. ``strategy_versions.blueprint`` has been ``JSONB NOT NULL``
since ``001_strategy_architecture.sql``, long before the canonical writer, so rows created
by older paths can hold a version-1 blueprint that does carry these keys. The gate below
is therefore a check on the blueprint's CONTENT, not a blanket refusal of the route: such
a row deploys exactly as it does today.
"""

from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import strategy_service as ss
from backend_app.backend.strategy_dag.schema import StrategyGraph

#: The four the signature accepts and the insert drops.
UNAPPLIED_DEPLOY_PARAMS = ("capital", "trade_size_pct", "stop_loss_pct", "max_drawdown_pct")

#: What `BotRunner` and `StrategyEngine.run_logic` need off the blueprint.
BLUEPRINT_REQUIRED_KEYS = ("exchange_id", "buy_logic", "sell_logic", "risk")


def _canonical_blueprint() -> dict:
    """Exactly what the save path writes to ``strategy_versions.blueprint``."""
    return StrategyGraph(strategy_id="s-1", version="v1.0", name="probe").to_dict()


# ---------------------------------------------------------------------------
# 1. The unapplied parameters are declared, and refused
# ---------------------------------------------------------------------------


class TestTheUnappliedParametersAreDeclared:
    def test_the_service_declares_which_deploy_parameters_it_cannot_apply(self):
        """Named beside the code that would have to change for them to work.

        The same shape as ``backtest_runtime.CONFIGURATION_FIELDS_NOT_APPLIED``, so the
        two accepted-but-inert surfaces are described the same way.
        """
        declared = getattr(ss, "DEPLOY_PARAMS_NOT_APPLIED", None)
        assert declared is not None, (
            "strategy_service does not declare the deploy parameters it accepts and "
            "discards"
        )
        assert set(declared) == set(UNAPPLIED_DEPLOY_PARAMS), (
            f"expected {sorted(UNAPPLIED_DEPLOY_PARAMS)}, got {sorted(declared)}"
        )

    def test_each_declared_parameter_carries_a_reason(self):
        for name, reason in ss.DEPLOY_PARAMS_NOT_APPLIED.items():
            assert isinstance(reason, str) and len(reason) > 20, (
                f"{name} is declared unapplied with no usable explanation: {reason!r}"
            )

    def test_the_declared_set_matches_the_signature(self):
        """So a fifth parameter cannot be added to the signature and quietly dropped."""
        signature = inspect.signature(ss.StrategyService.deploy_strategy)
        for name in UNAPPLIED_DEPLOY_PARAMS:
            assert name in signature.parameters, (
                f"{name} is declared unapplied but is no longer a parameter; update "
                f"DEPLOY_PARAMS_NOT_APPLIED"
            )

    def test_none_of_them_is_written_to_the_deployment_row(self):
        """Source-level, because the defect is an absence and absences do not raise."""
        source = inspect.getsource(ss.StrategyService.deploy_strategy)
        body = source.split("deployment_data = {", 1)
        assert len(body) == 2, "could not locate the deployment_data literal"
        literal = body[1].split("}", 1)[0]
        for name in UNAPPLIED_DEPLOY_PARAMS:
            assert name not in literal, (
                f"{name} now appears in deployment_data. If it is genuinely persisted, "
                f"remove it from DEPLOY_PARAMS_NOT_APPLIED and from the refusal."
            )


class TestSupplyingAnUnappliedParameterIsRefused:
    @pytest.mark.parametrize("name", UNAPPLIED_DEPLOY_PARAMS)
    def test_a_supplied_value_is_refused_by_name(self, name):
        """Refused, not reported. A stop-loss that does not exist is a money question."""
        with pytest.raises(ss.DeployParameterNotApplicable) as caught:
            ss.assert_deploy_params_applicable({name: 0.05})
        assert name in str(caught.value)

    def test_the_reason_points_at_where_per_trade_risk_actually_lives(self):
        with pytest.raises(ss.DeployParameterNotApplicable) as caught:
            ss.assert_deploy_params_applicable({"stop_loss_pct": 0.02})
        message = str(caught.value)
        assert "strategies.risk" in message or "risk" in message

    def test_omitting_them_is_fine(self):
        """They are all Optional with a None default; absence must stay the normal case."""
        ss.assert_deploy_params_applicable(
            {name: None for name in UNAPPLIED_DEPLOY_PARAMS}
        )
        ss.assert_deploy_params_applicable({})

    def test_every_supplied_value_is_named_not_just_the_first(self):
        with pytest.raises(ss.DeployParameterNotApplicable) as caught:
            ss.assert_deploy_params_applicable(
                {"trade_size_pct": 0.1, "stop_loss_pct": 0.02}
            )
        message = str(caught.value)
        assert "trade_size_pct" in message and "stop_loss_pct" in message

    def test_a_zero_counts_as_supplied(self):
        """``0.0`` is falsy and is a deliberate value; it must not slip through.

        A ``stop_loss_pct`` of 0 is exactly the kind of thing a truthiness check would
        accept and then discard.
        """
        with pytest.raises(ss.DeployParameterNotApplicable):
            ss.assert_deploy_params_applicable({"stop_loss_pct": 0.0})


# ---------------------------------------------------------------------------
# 2. The blueprint gate
# ---------------------------------------------------------------------------


class TestTheBlueprintIsCheckedBeforeAnythingIsWritten:
    def test_the_canonical_blueprint_is_missing_what_the_bot_needs(self):
        """The measurement this whole file rests on, asserted rather than described."""
        blueprint = _canonical_blueprint()

        missing = [k for k in BLUEPRINT_REQUIRED_KEYS if k not in blueprint]

        assert missing == list(BLUEPRINT_REQUIRED_KEYS), (
            f"StrategyGraph.to_dict() now carries some of {BLUEPRINT_REQUIRED_KEYS}: "
            f"missing only {missing}. If the save path changed, this file's premise has "
            f"changed with it."
        )

    def test_a_canonical_blueprint_is_refused_naming_every_missing_key(self):
        with pytest.raises(ss.DeployBlueprintIncomplete) as caught:
            ss.assert_blueprint_startable(_canonical_blueprint(), strategy_id="s-1")
        message = str(caught.value)
        for key in BLUEPRINT_REQUIRED_KEYS:
            assert key in message, f"the refusal does not name the missing {key!r}"

    def test_a_complete_legacy_blueprint_is_accepted(self):
        """A version-1 row that does carry these keys must deploy exactly as before."""
        ss.assert_blueprint_startable(
            {
                "exchange_id": "binance",
                "buy_logic": {},
                "sell_logic": {},
                "risk": {"position_size_pct": 0.1},
                "strategy_id": "s-1",
            },
            strategy_id="s-1",
        )

    def test_a_non_mapping_blueprint_is_refused(self):
        for candidate in (None, "", [], "a string"):
            with pytest.raises(ss.DeployBlueprintIncomplete):
                ss.assert_blueprint_startable(candidate, strategy_id="s-1")

    def test_the_refusal_names_the_constructor_that_would_have_raised(self):
        """So the reader is not left to rediscover why these four keys."""
        with pytest.raises(ss.DeployBlueprintIncomplete) as caught:
            ss.assert_blueprint_startable(_canonical_blueprint(), strategy_id="s-1")
        assert "exchange_id" in str(caught.value)

    def test_the_gate_runs_before_the_deployment_row_is_inserted(self):
        """Ordering is the point: a refusal must leave no ``strategy_deployments`` row.

        Today the route inserts the row, calls the fleet, fails, and then UPDATEs the row
        to ``failed`` -- so a route that cannot deploy still accumulates rows. Asserted on
        source order, the same way the archive and prerequisite gates above it are
        positioned.
        """
        source = inspect.getsource(ss.StrategyService.deploy_strategy)
        gate = source.find("assert_blueprint_startable")
        insert = source.find('table("strategy_deployments").insert')
        assert gate != -1, "deploy_strategy does not check the blueprint at all"
        assert insert != -1, "could not locate the strategy_deployments insert"
        assert gate < insert, (
            "the blueprint is checked after the deployment row is inserted, so a "
            "refusal still leaves a row behind"
        )

    def test_the_parameter_gate_also_runs_before_the_insert(self):
        source = inspect.getsource(ss.StrategyService.deploy_strategy)
        gate = source.find("assert_deploy_params_applicable")
        insert = source.find('table("strategy_deployments").insert')
        assert gate != -1, "deploy_strategy does not refuse the unapplied parameters"
        assert gate < insert


# ---------------------------------------------------------------------------
# 3. What the refusals must NOT become
# ---------------------------------------------------------------------------


class TestTheRefusalsAreRefusalsAndNotSubstitutions:
    def test_nothing_substitutes_a_default_exchange_id(self):
        """A default here would be the SB-06 defect again, on the deploy path.

        ``routers/strategies.py``'s deploy endpoint does
        ``blueprint["exchange_id"] = body.get("exchange_id", blueprint.get("exchange_id", "binance"))``
        -- a literal venue chosen when the caller names none. This gate must not copy
        that: a deployment whose venue nobody chose is not a deployment anyone asked for.
        """
        # The docstring names "binance" precisely to say it is NOT substituted, so the
        # scan has to be over executable code. Parsed and the docstring dropped, rather
        # than grepped - a prose mention is not a behaviour.
        import ast
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(ss.assert_blueprint_startable)))
        function = tree.body[0]
        body = function.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        code = "\n".join(ast.dump(statement) for statement in body)

        assert "binance" not in code, (
            "assert_blueprint_startable substitutes a venue instead of refusing"
        )
        assert "setdefault" not in code, (
            "assert_blueprint_startable mutates the blueprint instead of refusing"
        )

    def test_the_blueprint_gate_does_not_mutate_its_argument(self):
        blueprint = _canonical_blueprint()
        before = dict(blueprint)

        with pytest.raises(ss.DeployBlueprintIncomplete):
            ss.assert_blueprint_startable(blueprint, strategy_id="s-1")

        assert blueprint == before, "the gate modified the blueprint it was checking"

    def test_both_exceptions_are_distinguishable_from_a_generic_failure(self):
        """The route maps ``ValueError`` to a 400/403; these need to be tellable apart."""
        assert issubclass(ss.DeployBlueprintIncomplete, Exception)
        assert issubclass(ss.DeployParameterNotApplicable, Exception)
        assert not issubclass(
            ss.DeployBlueprintIncomplete, ss.DeployParameterNotApplicable
        )


# ---------------------------------------------------------------------------
# 4. The route answers a status a caller can act on
# ---------------------------------------------------------------------------


class TestTheRouteMapsBothRefusals:
    """Both new exceptions are plain ``Exception`` subclasses.

    Without an explicit branch they fall through to a generic handler, which would
    report a caller-correctable request as a server fault. They must not fall into the
    ``except ValueError`` branch either: that one answers 403/404, and does not describe
    what happened.

    HISTORY. ``strategy_operations.deploy_strategy`` -- the route these tests used to
    inspect by source -- was deleted by API-01 (commit 396d210) as a permanently shadowed
    duplicate: ``routers/strategies.py`` was mounted first and always won the same path,
    so the handler had been dead code since the day both were added. That deletion was
    correct. But tracing where a deploy request actually goes today --
    ``routers.strategies.deploy_bot`` / ``resume_strategy`` ->
    ``_deploy_current_version_paper`` -> ``StrategyService.deploy_version`` -- found that
    the parameter refusal had not travelled with it: ``deploy_version``'s own request
    model has no field for ``capital``/``trade_size_pct``/``stop_loss_pct``/
    ``max_drawdown_pct`` at all, and nothing read them out of ``deploy_bot``'s raw
    ``body`` dict either, so a caller setting a stop-loss on deploy had it silently
    dropped -- the exact defect this file exists to catch, just one layer up from where
    it used to live. Fixed by wiring ``assert_deploy_params_applicable`` into
    ``_deploy_current_version_paper`` (called before anything else, same as the deleted
    route) and having ``deploy_bot`` forward its body to it. These tests now exercise
    that function directly instead of grepping a route's source, because the behaviour
    that matters is what actually happens when it runs, not what literal exception name
    the code mentions.

    The blueprint-incomplete half never had this problem: ``deploy_version`` already
    calls ``assert_blueprint_startable`` (``strategy_service.py:1858``) and converts a
    ``DeployBlueprintIncomplete`` into ``DeployRejected(..., http_status=422)``, which
    ``_deploy_current_version_paper`` already maps through ``e.http_status`` -- a
    different wrapper and status (422, not the original route's 409) but the same
    guarantee: no silent deploy of a blueprint that cannot start a bot, and no 500.
    """

    def test_an_unapplicable_parameter_is_a_422(self):
        with pytest.raises(HTTPException) as caught:
            _run(_deploy_route()({"id": "u1"}, "strat-1", supplied_params={"stop_loss_pct": 0.02}))
        assert caught.value.status_code == 422
        assert caught.value.detail["error"] == "DEPLOY_PARAMETER_NOT_APPLICABLE"
        assert "stop_loss_pct" in caught.value.detail["message"]

    def test_the_refusal_runs_before_the_service_is_even_reached(self):
        """Ordering is the point: a refused parameter must not cost a service call."""
        with patch(
            "backend_app.backend.strategy_service.get_strategy_service", new=AsyncMock()
        ) as mock_get_service:
            with pytest.raises(HTTPException):
                _run(_deploy_route()({"id": "u1"}, "strat-1", supplied_params={"capital": 500}))
            assert mock_get_service.call_count == 0, (
                "get_strategy_service was called before the parameter refusal ran"
            )

    def test_an_incomplete_blueprint_is_refused_not_a_500(self):
        """The service-level guarantee this route relies on, exercised at its own seam.

        ``_deploy_current_version_paper`` does not itself inspect the blueprint --
        ``deploy_version`` does, and already turns the refusal into a ``DeployRejected``
        this route maps through ``e.http_status``. This proves that mapping holds for
        the status code the service actually uses (422), not the original route's 409.
        """
        from backend_app.backend.deployment_binding import DeployRejected

        mock_service = AsyncMock()
        mock_service.get_strategy = AsyncMock(
            return_value={"version": {"version": "v1.0"}}
        )
        mock_service.deploy_version = AsyncMock(
            side_effect=DeployRejected(
                "RUNTIME_BLUEPRINT_NOT_STARTABLE",
                "missing ['exchange_id', 'buy_logic', 'sell_logic', 'risk']",
                http_status=422,
            )
        )
        with patch(
            "backend_app.backend.strategy_service.get_strategy_service",
            new=AsyncMock(return_value=mock_service),
        ):
            with pytest.raises(HTTPException) as caught:
                _run(_deploy_route()({"id": "u1"}, "strat-1"))
        assert caught.value.status_code == 422
        assert caught.value.status_code != 500

    def test_omitting_the_parameters_is_fine(self):
        """The normal case: no risk/capital controls supplied, nothing refused."""
        mock_service = AsyncMock()
        mock_service.get_strategy = AsyncMock(return_value=None)
        with patch(
            "backend_app.backend.strategy_service.get_strategy_service",
            new=AsyncMock(return_value=mock_service),
        ):
            with pytest.raises(HTTPException) as caught:
                _run(_deploy_route()({"id": "u1"}, "strat-1", supplied_params=None))
        # Reaches the NO_SAVED_VERSION 409, proving the refusal did not fire and the
        # call actually reached the service.
        assert caught.value.status_code == 409
        assert caught.value.detail["error"] == "NO_SAVED_VERSION"

    def test_the_deploy_bot_route_forwards_its_body_for_validation(self):
        """The route must keep passing the body through, or the refusal above is inert."""
        source = inspect.getsource(_live_deploy_bot_route())
        assert "supplied_params=body" in source.replace(" ", ""), (
            "deploy_bot no longer forwards its body to _deploy_current_version_paper, "
            "so a supplied risk/capital parameter can no longer be refused"
        )


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _deploy_route():
    from backend_app.routers.strategies import _deploy_current_version_paper

    return _deploy_current_version_paper


def _live_deploy_bot_route():
    from backend_app.routers.strategies import deploy_bot

    return deploy_bot
