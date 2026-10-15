"""OPT-03 -- the optimization engine and its backtest runtime are no longer shared,
mutable, process-wide singletons.

THE DEFECT
    backend_app/backend/optimization_engine.py::get_optimization_engine() and
    backend_app/backend/backtest_runtime.py::get_backtest_runtime() are both
    module-level singletons (``global _x; if _x is None: _x = X(); return _x``).
    Both of this session's two call sites --
    backend_app/routers/strategy_operations.py's POST /strategies/{id}/optimize and
    backend_app/routers/strategies.py's POST /walk-forward -- called them directly,
    so every tenant's call shared the SAME ``OptimizationEngine`` instance and
    therefore the SAME ``_optimization_history`` list. Every one of
    run_walk_forward_analysis/run_sensitivity_analysis/run_monte_carlo_simulation/
    run_benchmark_comparison reads its "best parameters" back via
    ``self._get_best_parameters(self._optimization_history)`` -- so a tenant whose
    call overlapped or ran back to back with another tenant's in the same process
    could have THAT tenant's optimization results folded into their own.

    The shared ``backtest_runtime`` singleton compounded it: it is constructed once
    with the class defaults ($10,000 capital, 0.1% fees, 0.05% slippage), so every
    one of the capital/fee/slippage/risk fields both endpoints accept in their
    request body was accepted and then silently ignored -- the exact "parameter
    accepted but not applied" bug
    strategy_operations.py::_backtest_runtime_for's own docstring already names and
    fixes for the plain (non-optimization) backtest endpoint.

THE FIX
    Both call sites now construct a FRESH ``OptimizationEngine()`` and a FRESH
    ``BacktestRuntime(...)`` (built from the request's own capital/fee/slippage/risk
    fields) per request --
    strategy_operations.py::_optimization_engine_for(body) for /optimize, and the
    equivalent inline construction (from the already-built ``OptimizationConfig``) for
    /walk-forward. ``get_optimization_engine``/``get_backtest_runtime`` themselves are
    UNCHANGED (kept, not deleted -- tests/test_save_performs_no_backtest.py's tripwire
    still needs to patch ``get_backtest_runtime`` by name in both router modules).

WHAT THIS FILE PROVES
    1. Two calls to ``_optimization_engine_for`` return two DIFFERENT
       ``OptimizationEngine`` objects (not the shared singleton) with independent
       ``_optimization_history`` lists -- the cross-tenant leak this finding names is
       structurally impossible once each request owns its own list.
    2. The ``BacktestRuntime`` each one is given carries the SUBMITTED
       capital/fee/slippage/risk values, not the class defaults -- the "accepted but
       ignored" half.
    3. Two differently-configured requests produce two differently-configured
       runtimes (not just "non-default", but correctly request-specific).
    4. ``get_optimization_engine()``/``get_backtest_runtime()`` still exist and still
       work (negative control -- this is new per-request construction, not a removal
       of the underlying classes or their singleton accessors).
"""
from __future__ import annotations

import backend_app.routers.strategy_operations as SO


def _optimization_request(**overrides):
    body = {
        "execution_graph": {"nodes": [], "edges": [], "execution_order": []},
        "parameters": {"param_a": [1, 2, 3]},
        "start_date": "2023-01-01",
        "end_date": "2023-12-31",
    }
    body.update(overrides)
    return SO.OptimizationRequest(**body)


def test_two_calls_return_two_different_engine_instances_with_independent_history():
    engine_a, _ = SO._optimization_engine_for(_optimization_request())
    engine_b, _ = SO._optimization_engine_for(_optimization_request())

    assert engine_a is not engine_b, (
        "two requests must not share the same OptimizationEngine instance -- that is "
        "exactly the shared _optimization_history this finding is about"
    )
    assert engine_a._optimization_history is not engine_b._optimization_history

    # Populate A's history and confirm B cannot see it -- the concrete cross-tenant leak.
    engine_a._optimization_history.append("tenant-a-result")
    assert "tenant-a-result" not in engine_b._optimization_history


def test_neither_instance_is_the_shared_process_wide_singleton():
    from backend_app.backend.optimization_engine import get_optimization_engine
    from backend_app.backend.backtest_runtime import get_backtest_runtime

    engine, runtime = SO._optimization_engine_for(_optimization_request())

    assert engine is not get_optimization_engine()
    assert runtime is not get_backtest_runtime()


def test_the_runtime_carries_the_submitted_values_not_the_class_defaults():
    _, runtime = SO._optimization_engine_for(
        _optimization_request(
            initial_capital=54_321.0,
            commission=0.0025,
            slippage=0.0031,
            risk_per_trade=0.02,
            max_drawdown=0.33,
            daily_loss_limit=0.11,
        )
    )

    vb = runtime.vectorbt_engine
    assert vb.initial_capital == 54_321.0
    assert vb.fees == 0.0025
    assert vb.slippage == 0.0031

    risk = runtime.risk_engine
    from decimal import Decimal
    assert risk.risk_per_trade == Decimal("0.02")
    assert risk.max_drawdown == Decimal("0.33")
    assert risk.daily_loss_limit == Decimal("0.11")


def test_two_different_configurations_produce_two_differently_configured_runtimes():
    _, runtime_a = SO._optimization_engine_for(
        _optimization_request(initial_capital=1_000.0, commission=0.001)
    )
    _, runtime_b = SO._optimization_engine_for(
        _optimization_request(initial_capital=2_000.0, commission=0.009)
    )

    assert runtime_a.vectorbt_engine.initial_capital != runtime_b.vectorbt_engine.initial_capital
    assert runtime_a.vectorbt_engine.fees != runtime_b.vectorbt_engine.fees


def test_the_engine_this_request_gets_is_also_injected_with_this_requests_runtime():
    engine, runtime = SO._optimization_engine_for(_optimization_request(initial_capital=7_777.0))
    assert engine.backtest_runtime is runtime
    assert engine.backtest_runtime.vectorbt_engine.initial_capital == 7_777.0


def test_the_singleton_accessors_themselves_still_exist_and_work():
    """Negative control: this is new per-request construction alongside the
    singletons, not a removal of get_optimization_engine()/get_backtest_runtime()."""
    from backend_app.backend.optimization_engine import get_optimization_engine, OptimizationEngine
    from backend_app.backend.backtest_runtime import get_backtest_runtime, BacktestRuntime

    assert isinstance(get_optimization_engine(), OptimizationEngine)
    assert isinstance(get_backtest_runtime(), BacktestRuntime)
    # Still a singleton in its own right -- calling it twice returns the same object.
    assert get_optimization_engine() is get_optimization_engine()
    assert get_backtest_runtime() is get_backtest_runtime()
