"""The live order path must normalize correctly, enforce venue minimums, and be
recognisable as a repeat by the venue.

WHAT THIS GUARDS — four defects on the single code path that reaches a real exchange
(``CCXTExchangeExecutor.place_order``), all found in the pre-launch audit:

1. **Tick-size precision crashed the order.** CCXT reports ``precision.price`` /
   ``precision.amount`` under two conventions: a count of decimal places, or the step
   itself. Binance, Kraken, Coinbase and Bitfinex are step ("TICK_SIZE") venues. The
   normalizer assumed a count and built its quantizer as ``"0." + "0" * precision``, so a
   value of ``0.001`` raised ``TypeError: can't multiply sequence by non-int of type
   'float'`` — on the live path, inside ``place_order``, after the idempotency claim had
   been taken.

2. **The market lookup never matched.** ``self._markets`` is keyed by CCXT's unified
   symbol (``"BTC/USDT"``) but ``place_order`` passed the raw symbol (``"BTCUSDT"``), so
   every lookup missed and both normalizers silently used their 8-decimal default instead
   of the venue's real precision.

3. **Venue minimums were never enforced.** ``limits.amount.min`` and ``limits.cost.min``
   were read from CCXT and compared against nothing, so an undersized order consumed its
   idempotency claim and execution record and was then rejected by the venue.

4. **No venue-side idempotency.** No ``clientOrderId`` was ever sent, so the exchange
   could not recognise a duplicate; a ccxt-internal retry or a lost response followed by
   a retry became a second real order.

Plus: ``create_order(**params)`` was called with a ``stopPrice`` key, but CCXT's signature
is ``create_order(symbol, type, side, amount, price, params)`` — so every stop and
take-profit order raised ``TypeError`` before reaching the venue.

No network: the ccxt client is replaced with a recorder, and the validation token is
minted the way the execution engine mints it.
"""

import asyncio
from decimal import Decimal

import pytest

from backend_app.core.live_execution_gate import (REQUIRED_CHECKS,  # noqa: E402
                                                  authorize_live_order)
from backend_app.backend.exchange_executor import (CCXTExchangeExecutor,
                                                   ExchangeNormalizer)
# NOTE: there are two OrderType enums in this codebase. `pydantic_models.OrderType` is
# market/limit/stop/take_profit and is what `ExecutionEngine._execute_trade_internal`
# passes; `exchange_executor.OrderType` is the richer set the executor itself switches on
# (STOP_LIMIT, TAKE_PROFIT_MARKET, ...). `place_order` re-coerces whatever it is given
# into its own enum, so both work as inputs — the stop-order test needs the executor's
# enum because only that one can express a stop-limit at all.
from backend_app.backend.exchange_executor import OrderType as ExecutorOrderType
from backend_app.core.global_safety import generate_validation_token
from backend_app.core.models.pydantic_models import OrderSide, OrderType

TICK_SIZE_MARKET = {
    "BTC/USDT": {
        "precision": {"price": 0.01, "amount": 0.00001},
        "limits": {"amount": {"min": 0.0001}, "cost": {"min": 10.0}},
    }
}
DECIMAL_PLACES_MARKET = {
    "BTC/USDT": {
        "precision": {"price": 2, "amount": 5},
        "limits": {"amount": {"min": 0.0001}, "cost": {"min": 10.0}},
    }
}


# ─────────────────────────────────────────────────────────────────────────────
# 1 + 2. Precision conventions and the lookup key
# ─────────────────────────────────────────────────────────────────────────────

def test_tick_size_precision_does_not_crash():
    """THE DEFECT. A step of 0.00001 used to raise TypeError."""
    n = ExchangeNormalizer("binance")
    n._markets = TICK_SIZE_MARKET

    assert n.normalize_size("BTC/USDT", Decimal("0.123456789")) == "0.12345"
    assert n.normalize_price("BTC/USDT", Decimal("50000.123456")) == "50000.12"


def test_both_ccxt_precision_conventions_give_the_same_answer():
    """A step of 0.00001 and a count of 5 describe the same market."""
    tick = ExchangeNormalizer("binance")
    tick._markets = TICK_SIZE_MARKET
    places = ExchangeNormalizer("binance")
    places._markets = DECIMAL_PLACES_MARKET

    assert tick.normalize_size("BTC/USDT", Decimal("0.123456789")) == places.normalize_size(
        "BTC/USDT", Decimal("0.123456789")
    )
    assert tick.normalize_price("BTC/USDT", Decimal("50000.987654")) == places.normalize_price(
        "BTC/USDT", Decimal("50000.987654")
    )


def test_the_raw_symbol_resolves_to_the_same_market_as_the_unified_one():
    """``place_order`` passes ``BTCUSDT``; the market map is keyed ``BTC/USDT``."""
    n = ExchangeNormalizer("binance")
    n._markets = TICK_SIZE_MARKET

    assert n.normalize_size("BTCUSDT", Decimal("0.123456789")) == n.normalize_size(
        "BTC/USDT", Decimal("0.123456789")
    )


def test_an_unknown_symbol_still_falls_back_rather_than_raising():
    """Pre-existing tolerance retained: an unlisted symbol gets the 8dp default."""
    n = ExchangeNormalizer("binance")
    n._markets = TICK_SIZE_MARKET
    assert n.normalize_size("DOGE/USDT", Decimal("1.123456789012")) == "1.12345678"


@pytest.mark.parametrize(
    "precision,expected",
    [
        (0, 0),          # integer-only, unambiguous under both conventions
        (0.01, 2),       # tick size
        (1e-08, 8),      # tick size in exponent form
        (5, 5),          # a count
        (1, None),       # AMBIGUOUS: one place, or no places? refuse.
        (-1, None),
        (None, None),
        ("nonsense", None),
    ],
)
def test_precision_resolution_refuses_to_guess_the_ambiguous_value(precision, expected):
    """``1`` means one decimal place under one convention and none under the other, and
    the two differ in which orders the venue accepts. Guessing either way would admit an
    order the venue rejects or refuse one it accepts, so neither is guessed — the same
    rule ``paper/paper_simulator._decimal_places`` already applies."""
    assert ExchangeNormalizer._decimal_places(precision) == expected


def test_size_is_snapped_down_to_a_whole_step():
    """Truncating to N decimals is not snapping to a step: with a step of 0.003,
    0.004 truncates to 0.004 and is then rejected by the venue."""
    n = ExchangeNormalizer("binance")
    n._markets = {"BTC/USDT": {"precision": {"amount": 0.003}, "limits": {}}}

    assert Decimal(n.normalize_size("BTC/USDT", Decimal("0.004"))) == Decimal("0.003")


def test_rounding_is_always_down():
    """A size rounded UP can exceed the balance and can breach a position limit that was
    checked against the pre-rounding figure."""
    n = ExchangeNormalizer("binance")
    n._markets = TICK_SIZE_MARKET

    assert Decimal(n.normalize_size("BTC/USDT", Decimal("0.999999999"))) <= Decimal("0.999999999")
    assert Decimal(n.normalize_price("BTC/USDT", Decimal("1.999"))) <= Decimal("1.999")


# ─────────────────────────────────────────────────────────────────────────────
# place_order: a recorder in place of ccxt
# ─────────────────────────────────────────────────────────────────────────────

class RecordingExchange:
    """Captures exactly how ``create_order`` was invoked."""

    def __init__(self):
        self.calls = []

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        self.calls.append(
            {"symbol": symbol, "type": type, "side": side, "amount": amount,
             "price": price, "params": params or {}}
        )
        return {"id": "VENUE-1", "filled": 0, "remaining": amount, "average": None,
                "status": "open"}


class _UnhaltedRedis:
    """The minimum Redis surface `GlobalKillSwitch.is_active()` asks for, answering
    "the platform is NOT halted".

    WHY THIS FIXTURE EXISTS (KILL-03/KILL-04). `place_order` now calls
    `enforce_kill_switch()` as its last statement before `create_order`, because the
    audit found the venue boundary was asserting the signed check NAME `"kill_switch"`
    rather than re-reading the state -- and the only genuine read was far upstream, before
    the idempotency claim, the capacity transaction and the rate limiter.

    With no Redis, `redis_manager` is an uninitialised `__getattr__` proxy that raises
    `AttributeError`, so `_redis_get` raises `RedisUnavailable` and `is_active()` **fails
    closed and returns True**. That is correct (it is KILL-01's fix), and it meant every
    test in this file that drives an order to the venue began failing with
    `ExecutionBlocked` -- for a reason that has nothing to do with normalization,
    minimums or client order ids.

    So the precondition is made EXPLICIT rather than the check being stubbed out, which is
    exactly the choice `_authorize` below records for the TRADE-04 gate: these tests are
    about normalization and idempotency, but they reach the real venue boundary, so they
    must arrive the way a legitimate order arrives -- and a legitimate order arrives when
    the platform is not halted. Stubbing `enforce_kill_switch` would mean this file no
    longer proved anything about the live path.

    The halt's own refusal behaviour is covered by
    `tests/test_kill03_authoritative_global_halt.py`, which asserts both directions
    against a `create_order` oracle.
    """

    async def get(self, key):
        return None          # Redis ANSWERED; the key is absent, so the switch is off.

    async def set(self, key, value, nx=False, ex=None):
        return True

    async def setex(self, key, ex, value):
        return True

    async def delete(self, key):
        return 1

    async def lpush(self, key, value):
        return 1

    async def lrange(self, key, start, end):
        return []

    async def expire(self, key, seconds):
        return True

    async def sadd(self, key, *values):
        return len(values)

    async def srem(self, key, *values):
        return len(values)

    async def smembers(self, key):
        return set()


@pytest.fixture(autouse=True)
def platform_is_not_halted(monkeypatch):
    """Every test in this module runs with a readable, un-engaged kill switch.

    A FRESH `GlobalKillSwitch` is installed as the module singleton too, because the
    latch is instance state: a previous test that engaged a switch would otherwise leave
    the process-wide singleton latched and block everything after it.
    """
    import backend_app.backend.redis_manager as redis_manager_module
    import backend_app.core.global_safety as global_safety

    fake = _UnhaltedRedis()
    monkeypatch.setattr(redis_manager_module, "redis_manager", fake)
    monkeypatch.setattr(redis_manager_module, "_redis_manager", fake, raising=False)
    monkeypatch.setattr(
        global_safety, "_global_kill_switch", global_safety.GlobalKillSwitch()
    )


def _executor(markets=None):
    ex = CCXTExchangeExecutor(
        exchange_id="binance", api_key="k", api_secret="s", sandbox=True
    )
    ex._exchange = RecordingExchange()
    ex.normalizer._markets = markets if markets is not None else TICK_SIZE_MARKET
    return ex


def _authorize(ex, symbol, size, execution_id="exec_abc1234567890",
               side="buy", exchange_id="binance"):
    """Arm the executor the way ``_execute_trade_internal`` does.

    TWO separate credentials are needed, and they guard different things:

    * ``_current_validation_token`` -- the pre-existing anti-bypass token, which binds a
      venue call to one ``(execution_id, symbol, size)``.
    * ``_current_live_authorization`` -- the TRADE-04 ``LiveOrderAuthorization``, added
      after this file was written. The audit found that ``place_order`` reached
      ``ccxt.create_order`` with no evidence that any risk, capital, kill-switch or
      live-enablement check had run; the venue boundary now refuses an order that
      presents no authorisation naming every one of ``REQUIRED_CHECKS``.

    Minting it here rather than stubbing the gate is deliberate. These tests are about
    normalization and idempotency, not about the gate, but they reach the real venue
    boundary -- so they must arrive the way a legitimate order arrives. Disabling the
    gate for them would mean this file no longer proved anything about the live path.
    The gate's own refusal behaviour is covered by
    ``tests/test_trade04_live_order_gate_at_venue_boundary.py``, including a test that
    each required check is individually load-bearing.
    """
    ex._current_execution_id = execution_id
    ex._current_validation_token = generate_validation_token(execution_id, symbol, size)
    ex._current_live_authorization = authorize_live_order(
        execution_id=execution_id,
        tenant_id="11111111-1111-1111-1111-111111111111",
        strategy_id="strategy_normalization_tests",
        symbol=symbol,
        side=side,
        size=size,
        exchange_id=exchange_id,
        checks=REQUIRED_CHECKS,
    )


def _place(ex, **kw):
    defaults = dict(
        symbol="BTC/USDT",
        side=OrderSide.buy,
        order_type=OrderType.limit,
        size=Decimal("0.01"),
        price=Decimal("50000"),
    )
    defaults.update(kw)
    return asyncio.run(ex.place_order(**defaults))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Venue minimums
# ─────────────────────────────────────────────────────────────────────────────

def test_an_order_below_the_minimum_quantity_is_refused_locally():
    """THE DEFECT: this used to be sent and rejected by the venue, after the idempotency
    claim and the execution record had already been consumed."""
    ex = _executor()
    _authorize(ex, "BTC/USDT", Decimal("0.00001"))

    result = _place(ex, size=Decimal("0.00001"))

    assert result.success is False
    assert result.error_code == "BELOW_MIN_QUANTITY"
    assert ex._exchange.calls == [], "an undersized order was sent to the venue"


def test_an_order_below_the_minimum_notional_is_refused_locally():
    """``limits.cost.min`` is 10 USDT here; 0.0001 BTC at 50,000 is 5."""
    ex = _executor()
    _authorize(ex, "BTC/USDT", Decimal("0.0001"))

    result = _place(ex, size=Decimal("0.0001"), price=Decimal("50000"))

    assert result.success is False
    assert result.error_code == "BELOW_MIN_NOTIONAL"
    assert ex._exchange.calls == []


def test_a_size_truncated_to_zero_is_refused_rather_than_sent_as_zero():
    """Truncation makes this reachable: a size below the step rounds DOWN to nothing."""
    ex = _executor()
    _authorize(ex, "BTC/USDT", Decimal("0.000000001"))

    result = _place(ex, size=Decimal("0.000000001"))

    assert result.success is False
    assert result.error_code == "BELOW_PRECISION"
    assert ex._exchange.calls == []


def test_an_order_meeting_every_minimum_is_sent():
    """The refusals must not block legitimate orders."""
    ex = _executor()
    _authorize(ex, "BTC/USDT", Decimal("0.01"))

    result = _place(ex, size=Decimal("0.01"))

    assert result.success is True
    assert len(ex._exchange.calls) == 1
    assert ex._exchange.calls[0]["amount"] == pytest.approx(0.01)


def test_minimums_are_read_as_absent_rather_than_zero_when_unpublished():
    """A venue that states no minimum must not have one invented for it."""
    n = ExchangeNormalizer("binance")
    n._markets = {"BTC/USDT": {"precision": {"amount": 0.001}, "limits": {}}}

    mins = n.market_minimums("BTC/USDT")
    assert mins["min_amount"] is None
    assert mins["min_cost"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Venue-side idempotency
# ─────────────────────────────────────────────────────────────────────────────

def test_the_execution_id_is_forwarded_as_a_client_order_id():
    """THE DEFECT. Without this the venue cannot reject a repeat, so a retry of a lost
    response places a second real order."""
    ex = _executor()
    _authorize(ex, "BTC/USDT", Decimal("0.01"), execution_id="exec_feedfacecafe0001")

    _place(ex, size=Decimal("0.01"))

    sent = ex._exchange.calls[0]["params"]
    assert sent.get("clientOrderId") == "exec_feedfacecafe0001", (
        "no clientOrderId reached the venue; venue-side duplicate rejection is unused"
    )


def test_the_same_execution_id_yields_the_same_client_order_id():
    """Idempotency requires the id to be deterministic, not merely present."""
    ids = []
    for _ in range(2):
        ex = _executor()
        _authorize(ex, "BTC/USDT", Decimal("0.01"), execution_id="exec_stable0000001")
        _place(ex, size=Decimal("0.01"))
        ids.append(ex._exchange.calls[0]["params"]["clientOrderId"])

    assert ids[0] == ids[1]


def test_the_validation_token_is_still_single_use():
    """The anti-bypass guard must not be weakened by retaining the execution id: the
    TOKEN is consumed, and a second order without a fresh one is refused."""
    ex = _executor()
    _authorize(ex, "BTC/USDT", Decimal("0.01"))

    first = _place(ex, size=Decimal("0.01"))
    assert first.success is True

    with pytest.raises(ValueError, match="Bypass attempt detected"):
        _place(ex, size=Decimal("0.01"))


def test_an_unauthorized_order_is_still_refused():
    """No token at all: the bypass guard is unchanged."""
    ex = _executor()

    with pytest.raises(ValueError, match="Bypass attempt detected"):
        _place(ex, size=Decimal("0.01"))


# ─────────────────────────────────────────────────────────────────────────────
# 5. The CCXT call shape
# ─────────────────────────────────────────────────────────────────────────────

def test_create_order_is_called_positionally_with_params_last():
    """``create_order(**params)`` with a ``stopPrice`` key raised TypeError, because CCXT
    has no such parameter — triggers belong inside ``params``."""
    ex = _executor()
    _authorize(ex, "BTC/USDT", Decimal("0.01"))

    _place(ex, size=Decimal("0.01"))

    call = ex._exchange.calls[0]
    assert call["symbol"] == "BTC/USDT"
    assert call["type"] == "limit"
    assert call["side"] == "buy"
    assert isinstance(call["params"], dict)


def test_a_stop_order_puts_its_trigger_in_params_and_does_not_raise():
    """THE DEFECT: every stop and take-profit order used to fail with TypeError."""
    ex = _executor()
    _authorize(ex, "BTC/USDT", Decimal("0.01"))

    result = _place(
        ex,
        order_type=ExecutorOrderType.STOP_LIMIT,
        size=Decimal("0.01"),
        price=Decimal("50000"),
        stop_price=Decimal("49000"),
    )

    assert result.success is True, f"stop order failed: {result.error_message}"
    params = ex._exchange.calls[0]["params"]
    assert params.get("stopPrice") == pytest.approx(49000.0)


def test_a_market_order_sends_no_price():
    ex = _executor()
    _authorize(ex, "BTC/USDT", Decimal("0.01"))

    _place(ex, order_type=OrderType.market, size=Decimal("0.01"), price=None)

    assert ex._exchange.calls[0]["price"] is None


def test_the_order_is_still_reported_pending_not_filled():
    """Pre-existing and important: a fill is confirmed by the event stream, never
    assumed from the placement response."""
    ex = _executor()
    _authorize(ex, "BTC/USDT", Decimal("0.01"))

    result = _place(ex, size=Decimal("0.01"))
    assert result.status == "pending"
