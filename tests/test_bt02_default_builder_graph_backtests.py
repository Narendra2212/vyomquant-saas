"""BT-02 end to end — the Builder's default graph, wired from the data node's `frame` handle.

`ohlcv_feed.frame -> sma.series`, `ohlcv_feed.frame -> ema.series`, `sma >= ema`, `buy`. This is
the graph a trader gets by dragging from the data node's first handle, and in production it failed
every backtest with "Node 'n-2': port 'series' holds 43200 values for 8640 bars of market data".
This drives it through the REAL compiler, the persisted-plan path the backtester uses, and the real
DAGEngine, over synthetic candles (no exchange, no database).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend_app.backend import strategy_compiler as SC
from backend_app.backend.strategy_dag import validator as V
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag.schema import EdgeSpec, NodeSpec, StrategyGraph

from tests.test_dag_runtime_golden_plan import version_row


@pytest.fixture(scope="module")
def reg():
    return registry_module.build_registry()


def _graph(reg, data_port: str) -> StrategyGraph:
    def node(node_id, block_id, **params):
        return NodeSpec(id=node_id, block_id=block_id, category=reg[block_id].category, params=dict(params))

    def edge(eid, src, sport, dst, dport):
        return EdgeSpec(id=eid, source=src, source_port=sport, target=dst, target_port=dport)

    return StrategyGraph(
        schema_version=2,
        strategy_id="bt02-default",
        version="1.0.0",
        name="Default builder graph",
        nodes=[
            node("n-1", "ohlcv_feed", symbol="ADA/USDC", timeframe="5m", market_type="spot", mode="streaming"),
            node("n-2", "sma", window=20, source="close"),
            node("n-3", "ema", window=50, source="close"),
            node("n-4", "gte"),
            node("n-5", "action_buy_market", quantity_type="percent_of_equity", quantity=0.25),
        ],
        edges=[
            edge("e1", "n-1", data_port, "n-2", "series"),
            edge("e2", "n-1", data_port, "n-3", "series"),
            edge("e3", "n-2", "value", "n-4", "left"),
            edge("e4", "n-3", "value", "n-4", "right"),
            edge("e5", "n-4", "out", "n-5", "signal"),
        ],
    )


def _candles(n: int = 8640) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    rng = np.random.default_rng(7)
    close = 100 + rng.normal(0, 0.5, n).cumsum()
    return pd.DataFrame(
        {"open": close, "high": close + 0.3, "low": close - 0.3, "close": close, "volume": np.full(n, 10.0)},
        index=idx,
    )


def _run(reg, data_port: str):
    from backend_app.backend.backtest_runtime import BacktestRuntime

    graph = _graph(reg, data_port)
    report = V.validate(graph, reg)
    assert report.valid, f"the graph must validate: {report.codes()}"
    plan = SC.compile_graph(graph, reg)
    loaded = BacktestRuntime.load_version_plan(version_row(graph, plan), registry=reg)
    nodes, edges = SC.plan_to_engine_graph(loaded.plan)
    return BacktestRuntime().dag_engine.execute(nodes=nodes, edges=edges, market_data=_candles())


def test_a_graph_wired_from_the_frame_handle_executes_over_8640_bars(reg):
    result = _run(reg, "frame")
    assert result is not None


def test_the_close_wired_graph_still_executes_and_is_equivalent(reg):
    """Wiring `close` and wiring `frame` must be the same strategy, so the same signals."""
    from_frame = _run(reg, "frame")
    from_close = _run(reg, "close")
    sig_frame = from_frame["signals"] if isinstance(from_frame, dict) else from_frame
    sig_close = from_close["signals"] if isinstance(from_close, dict) else from_close
    assert np.array_equal(np.asarray(sig_frame), np.asarray(sig_close))
