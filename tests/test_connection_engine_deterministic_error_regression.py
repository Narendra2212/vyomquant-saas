"""tests/test_connection_engine_deterministic_error_regression.py

A deterministic parse fault is not retried as though it were a flaky network.

THE DEFECT
----------
``ConnectionEngine.connect`` retried EVERY non-authentication exception from
``load_markets`` three times with exponential backoff. In production that included a
``TypeError`` raised inside the client library itself:

    ccxt/async_support/okx.py, parse_market:  symbol = base + / + quote
    TypeError: unsupported operand type(s) for +: NoneType and str

OKX lists a pre-open SPOT instrument, ``CT-USDT``, whose ``baseCcy``, ``quoteCcy``,
``settleCcy`` and ``uly`` are all empty strings. ccxt 4.3.92 maps an empty string to
``None`` through ``safe_currency_code`` and then concatenates, so the parse raises. The
result is identical on every attempt, so each container start spent three attempts and six
seconds of backoff before failing anyway - the CloudWatch record shows exactly that,
twice per start.

WHAT THIS CHANGE DOES AND DOES NOT DO
-------------------------------------
It does NOT make OKX load. The library defect is upstream and still present on the pinned
``ccxt[async]==4.3.92``; a later ccxt derives base and quote from ``instId`` instead (the
comment ``weird preopen markets``), and 4.4.1 and 4.4.50 do not carry it while 4.5.84 does.
What it does is stop classifying a deterministic library fault as transient: the failure is
reported once, with its traceback, and the raised exception type is unchanged so callers
such as ``AssetUniverse`` see what they always saw.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import ccxt as ccxt_base

from backend_app.backend import connection_engine as CE


class FakeExchange:
    """Stands in for a ccxt exchange class. Counts load_markets attempts."""

    def __init__(self, raises, config=None):
        self._raises = raises
        self.attempts = 0
        self.closed = 0
        self.symbols = ["BTC/USDT"]
        self.markets = {"BTC/USDT": {}}
        self.options = {}
        self.id = "fake"

    async def load_markets(self, *a, **kw):
        self.attempts += 1
        if self._raises is not None:
            raise self._raises
        return self.markets

    async def close(self):
        self.closed += 1

    def set_sandbox_mode(self, flag):
        pass


@pytest.fixture
def venue(monkeypatch):
    """Install a fake exchange class under a real-looking id, with no network."""
    built = {}

    def install(exc):
        def factory(config):
            ex = FakeExchange(exc, config)
            built["ex"] = ex
            return ex

        monkeypatch.setattr(CE.ccxt, "okx", factory, raising=False)
        return built

    return install


@pytest.fixture(autouse=True)
def not_dev_mode(monkeypatch):
    """DEV_MODE injects a mock interface instead of raising; these tests are the prod path."""
    import backend_app.core.dependencies as deps

    monkeypatch.setattr(deps, "DEV_MODE", False, raising=False)


def _connect(exchange_id="okx"):
    return asyncio.run(CE.ConnectionEngine(exchange_id).connect())


class TestADeterministicFaultIsNotRetried:
    def test_typeerror_is_attempted_exactly_once(self, venue):
        """The production case, reduced: one attempt, not three."""
        built = venue(TypeError("unsupported operand type(s) for +: NoneType and str"))

        with pytest.raises(ConnectionError):
            _connect()

        assert built["ex"].attempts == 1, (
            "a deterministic TypeError was retried %d times; waiting cannot change it"
            % built["ex"].attempts
        )

    @pytest.mark.parametrize(
        "exc",
        [
            TypeError("NoneType and str"),
            AttributeError("no attribute parse_market"),
            KeyError("instId"),
            IndexError("list index out of range"),
        ],
        ids=["TypeError", "AttributeError", "KeyError", "IndexError"],
    )
    def test_every_deterministic_type_fails_fast(self, venue, exc):
        built = venue(exc)

        with pytest.raises(ConnectionError):
            _connect()

        assert built["ex"].attempts == 1

    def test_the_raised_type_is_unchanged(self, venue):
        """Callers must see what they saw before: ConnectionError, message preserved."""
        venue(TypeError("unsupported operand type(s) for +: NoneType and str"))

        with pytest.raises(ConnectionError) as excinfo:
            _connect()

        assert "Exchange connection failed" in str(excinfo.value)
        assert "NoneType" in str(excinfo.value)


class TestTransientFailuresAreStillRetried:
    def test_a_network_error_is_retried_to_the_limit(self, venue):
        """The behaviour that must NOT regress: real flakiness still gets its retries."""
        built = venue(ccxt_base.NetworkError("connection reset"))

        with pytest.raises(ConnectionError):
            _connect()

        assert built["ex"].attempts == 3, (
            "a transient NetworkError got %d attempt(s); the backoff retry is the whole "
            "point of this loop" % built["ex"].attempts
        )

    def test_a_generic_exception_is_still_retried(self, venue):
        built = venue(RuntimeError("venue returned 502"))

        with pytest.raises(ConnectionError):
            _connect()

        assert built["ex"].attempts == 3

    def test_authentication_error_still_fails_fast_and_keeps_its_type(self, venue):
        """Pre-existing behaviour, asserted so the new branch cannot shadow it."""
        built = venue(ccxt_base.AuthenticationError("Invalid Api-Key ID"))

        with pytest.raises(ccxt_base.AuthenticationError):
            _connect()

        assert built["ex"].attempts == 1

    def test_a_healthy_venue_still_connects(self, venue):
        built = venue(None)

        ex = _connect()

        assert built["ex"].attempts == 1
        assert ex.symbols == ["BTC/USDT"]


def test_the_upstream_defect_is_still_present_on_the_pinned_ccxt():
    """The PREMISE, so this file cannot pass while asserting against a fixed library.

    ccxt 4.3.92 concatenates base and quote with no empty-string guard. When upstream is
    upgraded this test fails, which is the signal that the retry branch above is no longer
    carrying an OKX-shaped case and the comment there should be revisited.
    """
    import inspect

    import ccxt.async_support.okx as okx_mod

    src = inspect.getsource(okx_mod)
    assert "symbol = base + " in src, "ccxt okx no longer builds symbol by concatenation"
    assert "weird preopen markets" not in src, (
        "the pinned ccxt now carries the upstream pre-open-market guard; OKX should load, "
        "so re-read the comment on the deterministic-error branch in connection_engine.py"
    )


class TestAFailedConnectDoesNotLeakItsSession:
    """A connect() that raises must close the client it built.

    THE LEAK. On failure the caller never receives a reference, so it cannot close what
    it never got. ``get_or_create_exchange`` does::

        engine = ConnectionEngine(...)
        exchange = await engine.connect()      # raises here
        _exchange_pool[pool_key] = exchange    # never reached

    so the engine goes out of scope holding a live ccxt client with an open aiohttp
    session, and ``release_exchange`` cannot reach it because it was never pooled. In
    production this surfaced as ``ERROR:asyncio:Unclosed client session`` and ccxt own
    ``okx requires to release all resources with an explicit call to the .close()``.

    The AssetUniverse path escaped it only because that caller keeps the engine and calls
    ``disconnect()`` itself - the contract leaked regardless.
    """

    def test_a_deterministic_failure_closes_the_session(self, venue):
        built = venue(TypeError("NoneType and str"))

        with pytest.raises(ConnectionError):
            _connect()

        assert built["ex"].closed == 1, "the failed client was never closed"

    def test_an_auth_failure_closes_the_session(self, venue):
        built = venue(ccxt_base.AuthenticationError("Invalid Api-Key ID"))

        with pytest.raises(ccxt_base.AuthenticationError):
            _connect()

        assert built["ex"].closed == 1

    def test_retry_exhaustion_closes_the_session(self, venue):
        built = venue(ccxt_base.NetworkError("connection reset"))

        with pytest.raises(ConnectionError):
            _connect()

        assert built["ex"].attempts == 3
        assert built["ex"].closed == 1, (
            "the client was left open after every retry was exhausted"
        )

    def test_a_successful_connect_does_not_close(self, venue):
        """The obvious guard: closing on success would hand back a dead client."""
        built = venue(None)

        ex = _connect()

        assert built["ex"].closed == 0
        assert ex.symbols == ["BTC/USDT"]
