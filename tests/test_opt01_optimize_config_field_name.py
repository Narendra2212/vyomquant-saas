"""OPT-01 -- the /strategies/{id}/optimize research-report endpoint no longer crashes.

THE DEFECT
    ``run_optimization`` built ``OptimizationConfig(..., commission=body.commission, ...)``.
    ``OptimizationConfig`` is a plain ``@dataclass`` (``backend/optimization_engine.py``)
    whose field is ``fees``, not ``commission`` -- it takes no ``**kwargs`` to swallow an
    unrecognised one, so this raised ``TypeError: __init__() got an unexpected keyword
    argument 'commission'`` on every single call to this endpoint. The public request
    field stays named ``commission`` (API shape unchanged); only the keyword passed into
    the dataclass was wrong.

THE FIX
    ``fees=body.commission`` at the one call site that had this bug
    (``strategy_operations.py``'s ``run_optimization``). The SIBLING call site in
    ``strategies.py`` (walk-forward analysis) already used ``fees=`` correctly -- this
    file also pins that one, so a future refactor cannot silently reintroduce the mismatch
    in either place without a test failing.

WHAT THIS FILE PROVES
    * constructing ``OptimizationConfig`` with the real ``OptimizationRequest`` Pydantic
      model's fields, using the EXACT keyword set ``run_optimization`` passes, succeeds
      and the value flows through (``config.fees == body.commission``) -- this reconstructs
      the precise call that crashed every time, without needing to mock the endpoint's full
      dependency chain (strategy service, optimization engine, a real backtest run);
    * the source of ``run_optimization`` no longer contains the broken keyword;
    * ``OptimizationConfig`` genuinely has no ``commission`` field and no catch-all
      ``**kwargs`` (so the TypeError this bug produced is not a fluke of today's
      implementation -- any future reintroduction of the mismatch fails the same way).
"""
from __future__ import annotations

import dataclasses
import inspect

from backend_app.backend.optimization_engine import (OptimizationConfig,
                                                      OptimizationMethod,
                                                      ValidationMethod)
from backend_app.routers import strategies as strategies_router
from backend_app.routers import strategy_operations
from backend_app.routers.strategy_operations import OptimizationRequest


def _sample_request() -> OptimizationRequest:
    return OptimizationRequest(
        execution_graph={"id": "g1", "nodes": [], "edges": []},
        parameters={"param_a": [1, 2, 3]},
        start_date="2024-01-01",
        end_date="2024-06-01",
    )


class TestOptimizationConfigHasNoCommissionField:
    def test_the_dataclass_field_is_fees_not_commission(self):
        fields = {f.name for f in dataclasses.fields(OptimizationConfig)}
        assert "fees" in fields
        assert "commission" not in fields

    def test_the_dataclass_takes_no_kwargs_catch_all(self):
        """Confirms the bug really was a hard TypeError, not a silently-swallowed one."""
        import pytest

        with pytest.raises(TypeError, match="commission"):
            OptimizationConfig(
                method=OptimizationMethod.GRID_SEARCH,
                validation_method=ValidationMethod.WALK_FORWARD,
                parameters={},
                commission=0.001,
            )


class TestRunOptimizationBuildsTheRealConfig:
    def test_the_exact_call_shape_this_route_uses_succeeds_and_flows_through(self):
        body = _sample_request()

        # The exact construction run_optimization performs, after the fix.
        config = OptimizationConfig(
            method=OptimizationMethod(body.optimization_method),
            validation_method=ValidationMethod(body.validation_method),
            parameters=body.parameters,
            n_iterations=body.n_iterations,
            n_trials=body.n_trials,
            training_window_days=body.training_window_days,
            validation_window_days=body.validation_window_days,
            test_window_days=body.test_window_days,
            initial_capital=body.initial_capital,
            fees=body.commission,
            slippage=body.slippage,
            risk_per_trade=body.risk_per_trade,
            max_drawdown=body.max_drawdown,
            daily_loss_limit=body.daily_loss_limit,
        )

        assert config.fees == body.commission == 0.001

    def test_the_route_source_no_longer_passes_commission_as_a_keyword(self):
        src = inspect.getsource(strategy_operations.run_optimization)
        assert "commission=body.commission" not in src
        assert "fees=body.commission" in src

    def test_the_sibling_walk_forward_call_site_was_already_correct(self):
        """Pins the OTHER OptimizationConfig(...) call site too, so a future refactor of
        either cannot silently reintroduce the mismatch without this failing."""
        src = inspect.getsource(strategies_router)
        assert 'fees=payload.get("commission"' in src
