"""BT-02 — a candle frame wired to a price-series input must mean its close column.

Production: a strategy `ohlcv_feed -> sma / ema -> gte -> buy` (the Builder's default graph, wired
from the data node's first handle, `frame`) failed every backtest with
"Node 'n-2': port 'series' holds 43200 values for 8640 bars of market data" -- 8640 bars x 5
OHLCV columns flattened into one array.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend_app.backend.dag_engine import DAGExecutionError, IndicatorExecutor as DAGEngine


def _frame(n: int = 8640) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="5min")
    rng = np.random.default_rng(0)
    close = 100 + rng.normal(0, 1, n).cumsum()
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": np.ones(n)},
        index=idx,
    )


def test_a_frame_on_a_series_port_is_its_close_column():
    frame = _frame()
    out = DAGEngine._as_bars(frame, "n-2", "series", frame)
    assert out.shape == (8640,), "the frame was flattened to bars x columns"
    assert np.array_equal(out, frame["close"].to_numpy(dtype=float))


def test_a_plain_series_is_unchanged():
    frame = _frame()
    out = DAGEngine._as_bars(frame["high"], "n-2", "series", frame)
    assert np.array_equal(out, frame["high"].to_numpy(dtype=float))


def test_a_frame_without_close_is_refused_by_name_not_guessed():
    frame = _frame()
    with pytest.raises(DAGExecutionError, match="no 'close' column"):
        DAGEngine._as_bars(frame.drop(columns=["close"]), "n-2", "series", frame)


def test_a_wrong_length_series_is_still_refused():
    frame = _frame()
    with pytest.raises(DAGExecutionError, match="holds 10 values for 8640 bars"):
        DAGEngine._as_bars(frame["close"].iloc[:10], "n-2", "series", frame)
