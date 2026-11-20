"""BT-01 regression: a backtest must never run on invented data or report invented ratios."""
import math
import sys
import types

import numpy as np
import pytest

from backend_app.routers.strategies import (_equity_curve_metrics,
                                            _periods_per_year, backtest_internal)


class _FailingBinance:
    def __init__(self, *a, **k):
        pass

    def fetch_ohlcv(self, **k):
        raise ConnectionError("exchange unreachable")


def _install_fake_ccxt(monkeypatch, binance_cls):
    fake = types.ModuleType("ccxt")
    fake.binance = binance_cls
    monkeypatch.setitem(sys.modules, "ccxt", fake)


def test_fetch_failure_returns_an_error_and_no_fabricated_result(monkeypatch):
    _install_fake_ccxt(monkeypatch, _FailingBinance)
    res = backtest_internal({"strategies": ["rsi"], "symbols": ["BTCUSDT"], "timeframe": "1h"})
    assert res.get("error_type") == "MarketDataUnavailable"
    assert res.get("fabricated_data_used") is False
    for key in ("total_return_pct", "sharpe_ratio", "equity", "total_trades", "final_equity"):
        assert key not in res, f"{key} present on a run with no market data"
    assert "traceback" not in res


def test_empty_candle_response_is_also_an_error(monkeypatch):
    class Empty(_FailingBinance):
        def fetch_ohlcv(self, **k):
            return []

    _install_fake_ccxt(monkeypatch, Empty)
    res = backtest_internal({"strategies": ["rsi"], "symbols": ["BTCUSDT"], "timeframe": "1h"})
    assert res.get("error_type") == "MarketDataUnavailable"


def _ohlcv(n=200, seed=7):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    ts0 = 1_700_000_000_000
    return [[ts0 + i * 3_600_000, c, c * 1.002, c * 0.998, c, 1000.0] for i, c in enumerate(close)]


def test_successful_run_reports_measured_curve_and_ratios_from_it(monkeypatch):
    class Good(_FailingBinance):
        def fetch_ohlcv(self, **k):
            return _ohlcv()

    _install_fake_ccxt(monkeypatch, Good)
    res = backtest_internal({"strategies": ["rsi"], "symbols": ["BTCUSDT"], "timeframe": "1h"})
    assert "error" not in res, res
    curve = [p["equity"] for p in res["equity"]]
    assert len(curve) > 2, "a run over 200 bars must yield a per-bar curve, not two points"
    assert curve[0] == res["initial_capital"]
    assert curve[-1] == pytest.approx(res["final_equity"])
    expected = _equity_curve_metrics(curve, "1h")
    assert res["sharpe_ratio"] == expected["sharpe_ratio"]
    assert res["sortino_ratio"] == expected["sortino_ratio"]
    assert res["calmar_ratio"] == expected["calmar_ratio"]


def test_sortino_is_not_sharpe_times_a_constant():
    rng = np.random.default_rng(1)
    eq = list(10_000 * np.cumprod(1 + rng.normal(0.0005, 0.01, 500)))
    m = _equity_curve_metrics(eq, "1h")
    assert m["sharpe_ratio"] is not None and m["sortino_ratio"] is not None
    assert not math.isclose(m["sortino_ratio"], m["sharpe_ratio"] * 1.2, rel_tol=1e-3)


def test_flat_or_short_curves_have_no_ratios_not_zero():
    assert _equity_curve_metrics([10_000, 10_000], "1h") == {"sharpe_ratio": None, "sortino_ratio": None, "calmar_ratio": None}
    flat = _equity_curve_metrics([10_000] * 50, "1h")
    assert flat["sharpe_ratio"] is None and flat["calmar_ratio"] is None


def test_known_series_sharpe_matches_hand_calculation():
    eq = [100.0, 101.0, 100.5, 102.0, 103.0, 102.5]
    r = np.diff(eq) / np.array(eq[:-1])
    expected = round(float(np.mean(r) / np.std(r) * math.sqrt(_periods_per_year("1d"))), 2)
    assert _equity_curve_metrics(eq, "1d")["sharpe_ratio"] == expected


def test_unknown_timeframe_yields_no_ratios():
    assert _equity_curve_metrics([1, 2, 3, 4], "weird")["sharpe_ratio"] is None
