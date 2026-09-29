# -*- coding: utf-8 -*-
"""tests/test_validate_historical_data_feed_regression.py

The historical-window check reads a feed it was given, and says so when it wasn't.

THE DEFECT
----------
``BacktestService.validate_historical_data`` built its reader with
``DataEngine()``. ``DataEngine.__init__`` takes ``exchange_instance`` and every other
construction in the repo supplies one (``backtest_runtime.set_data_engine``,
``routers/market._engine_for``, ``master_executor``), so this one call site raised

    TypeError: DataEngine.__init__() missing 1 required positional argument:
               'exchange_instance'

before a single bar was fetched. The method's own ``except Exception`` turned that into
``{"code": "VALIDATION_ERROR", "valid": false}``, which the Backtester's Period panel
renders as "Error during data validation" — on *every* window, for every user, because
the failure is in the construction and not in the data.

Two further consequences of the same never-executed line, fixed with it: the engine has
no ``fetch_ohlcv`` (its REST history reader is ``fetch_historical_ohlcv``, counted in
bars rather than dated) and it returns CCXT bar lists, not the ``DataFrame`` the checks
below index by timestamp. Nothing downstream of the construction had ever run.

WHAT IS REAL AND WHAT IS SUPPLIED
---------------------------------
Real: ``BacktestService.validate_historical_data`` and every check in it, the real
``DataEngine`` (including its pagination and its bar-list shape), the FastAPI app and
its routing, the route's auth dependency and rate limit, and the router's own
``_backtest_exchange_instance`` seam.

Supplied: the CCXT exchange behind the seam — a deterministic in-process feed with a
known bar series. Connecting to Binance is not what these assertions are about; *which
feed reaches the engine, and that one reaches it at all*, is.
"""

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import data_seeking_engine as DSE
from backend_app.backend.backtest_service import BacktestService
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.routers import strategy_operations as SO

USER = {
    "id": "usr_validate_window",
    "email": "owner@example.com",
    "role": "authenticated",
    "access_token": "token_owner",
}

PATH = "/api/backtests/validate-data"

SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
#: 200 hourly bars from 2024-01-01T00:00Z, so the window below is fully covered and the
#: method's own 100-candle warmup floor is cleared. A clean series means any issue the
#: result carries came from the plumbing, not from the data.
BAR_COUNT = 200
WINDOW_START = "2024-01-01T00:00:00Z"
WINDOW_END = "2024-01-10T00:00:00Z"

HOUR_MS = 3_600_000
FIRST_TS = 1_704_067_200_000  # 2024-01-01T00:00:00Z


def _series(count=BAR_COUNT):
    """A gapless, ordered, positive OHLCV series in CCXT's list-of-lists shape."""
    return [
        [
            FIRST_TS + i * HOUR_MS,
            100.0 + i,          # open
            101.0 + i,          # high
            99.0 + i,           # low
            100.5 + i,          # close
            10.0 + i,           # volume
        ]
        for i in range(count)
    ]


class FakeExchange:
    """The one object the caller owns and the engine must be handed.

    Implements only what ``DataEngine.fetch_historical_ohlcv`` touches, and records each
    call, so "the engine read *this* feed" is observable rather than assumed.
    """

    def __init__(self, bars=None):
        self.bars = _series() if bars is None else bars
        self.calls = []
        self.has = {"fetchOHLCV": True}

    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit}
        )
        window = [b for b in self.bars if since is None or b[0] >= since]
        return window if limit is None else window[:limit]


@pytest.fixture
def feed(monkeypatch):
    """The router's venue seam, answering with an in-process feed."""
    exchange = FakeExchange()

    async def instance():
        return exchange

    monkeypatch.setattr(SO, "_backtest_exchange_instance", instance)
    return exchange


@pytest.fixture
def engines(monkeypatch):
    """Records every ``DataEngine`` the service constructs, then builds the real one."""
    built = []
    real = DSE.DataEngine

    def recording(exchange_instance):
        engine = real(exchange_instance)
        built.append(engine)
        return engine

    monkeypatch.setattr(DSE, "DataEngine", recording)
    return built


@pytest.fixture(autouse=True)
def clear_rate_limit():
    """The route's 100/minute limit is real and shared; no test inherits another's count."""
    from backend_app.core.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _codes(result):
    return [issue["code"] for issue in result.get("issues", [])]


# ---------------------------------------------------------------------------
# 1. The service can run at all, and the engine reads the caller's feed
# ---------------------------------------------------------------------------


class TestServiceBuildsItsEngineOnTheGivenFeed:
    @pytest.mark.asyncio
    async def test_validation_runs_without_a_typeerror(self, engines):
        """The construction defect, asserted where it was raised.

        Before the fix this returned ``VALIDATION_ERROR`` carrying the ``TypeError``
        text; the data never mattered.
        """
        result = await BacktestService().validate_historical_data(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            start_date=WINDOW_START,
            end_date=WINDOW_END,
            exchange_instance=FakeExchange(),
        )

        assert "VALIDATION_ERROR" not in _codes(result), (
            "the validation raised instead of validating: "
            f"{result.get('issues')}"
        )
        assert result["valid"] is True
        assert result["issues"] == []
        assert result["data_info"]["total_candles"] == BAR_COUNT

    @pytest.mark.asyncio
    async def test_engine_receives_the_exchange_instance_it_was_given(self, engines):
        """Not *an* engine on *a* feed — the engine, on the caller's own object."""
        exchange = FakeExchange()

        await BacktestService().validate_historical_data(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            start_date=WINDOW_START,
            end_date=WINDOW_END,
            exchange_instance=exchange,
        )

        assert len(engines) == 1, "the validation built no data engine"
        assert engines[0].exchange is exchange
        # And it was read through, so the instance is load-bearing rather than stored.
        assert exchange.calls, "the engine never fetched from the supplied feed"
        assert exchange.calls[0]["symbol"] == SYMBOL
        assert exchange.calls[0]["timeframe"] == TIMEFRAME

    @pytest.mark.asyncio
    async def test_absent_feed_is_reported_not_invented(self, engines):
        """No connection means the check could not run, and says which check.

        The alternative — constructing a stand-in exchange — would report some other
        venue's data quality as though it were the one the backtest will read.
        """
        result = await BacktestService().validate_historical_data(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            start_date=WINDOW_START,
            end_date=WINDOW_END,
            exchange_instance=None,
        )

        assert result["valid"] is False
        assert _codes(result) == ["NO_MARKET_DATA_FEED"]
        assert engines == [], "an engine was built with no feed to build it on"

    def test_the_feed_is_a_required_argument(self):
        """A future call site cannot omit it the way this one did."""
        parameter = inspect.signature(
            BacktestService.validate_historical_data
        ).parameters["exchange_instance"]

        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# 2. The route the Backtester actually calls
# ---------------------------------------------------------------------------


class TestRouteSuppliesTheFeed:
    def test_window_check_succeeds_over_http(self, client, feed, engines):
        """The user-visible symptom: every window answered "Error during data validation"."""
        response = client.post(
            PATH,
            json={
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "start_date": WINDOW_START,
                "end_date": WINDOW_END,
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert "VALIDATION_ERROR" not in _codes(body), body.get("issues")
        assert body["valid"] is True
        assert body["data_info"]["total_candles"] == BAR_COUNT

    def test_route_hands_the_service_the_venue_seam_instance(self, client, feed, engines):
        """The feed is the module's one seam, not a second venue decided here."""
        response = client.post(
            PATH,
            json={
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "start_date": WINDOW_START,
                "end_date": WINDOW_END,
            },
        )

        assert response.status_code == 200, response.text
        assert len(engines) == 1
        assert engines[0].exchange is feed
        assert feed.calls, "the seam's instance was passed but never read"

    def test_handler_names_no_exchange_of_its_own(self):
        """Structural: the fix threaded the seam through, it did not import ccxt here."""
        source = inspect.getsource(SO.validate_historical_data)

        assert "_backtest_exchange_instance" in source
        assert "ccxt" not in source
        assert "binance" not in source
