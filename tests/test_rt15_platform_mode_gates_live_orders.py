"""RT-15 regression: a LIVE engine must not reach the venue unless the PLATFORM is in
live mode, live trading is explicitly enabled and the tenant is operator-allowlisted.

Reuses the real-``place_order`` harness from the TRADE-04 test (a recording venue, every
other prerequisite satisfied) so that a refusal here is attributable to platform mode alone.
"""
from __future__ import annotations

import pytest

from backend_app.core.live_execution_gate import (LiveExecutionRefused,
                                                  assert_platform_live_trading_enabled,
                                                  authorize_live_order, REQUIRED_CHECKS)
from tests.test_trade04_live_order_gate_at_venue_boundary import (  # noqa: F401
    TENANT, harness, place)


def _set(monkeypatch, mode="live", enable="true", allow="*"):
    for k in ("VYOMQUANT_MODE", "AERORA_MODE", "VYOMQUANT_ENABLE_LIVE_TRADING",
              "AERORA_ENABLE_LIVE_TRADING", "VYOMQUANT_LIVE_TENANT_ALLOWLIST"):
        monkeypatch.delenv(k, raising=False)
    if mode is not None:
        monkeypatch.setenv("VYOMQUANT_MODE", mode)
    if enable is not None:
        monkeypatch.setenv("VYOMQUANT_ENABLE_LIVE_TRADING", enable)
    if allow is not None:
        monkeypatch.setenv("VYOMQUANT_LIVE_TENANT_ALLOWLIST", allow)


def test_positive_control_live_platform_reaches_the_venue(harness):
    engine, exchange, mp = harness
    _set(mp)
    place(engine)
    assert len(exchange.calls) == 1


@pytest.mark.parametrize(
    "kwargs,code",
    [
        (dict(mode="paper"), "LIVE_TRADING_DISABLED_BY_PLATFORM_MODE"),
        (dict(mode="safe"), "LIVE_TRADING_DISABLED_BY_PLATFORM_MODE"),
        (dict(mode=None), "LIVE_TRADING_DISABLED_BY_PLATFORM_MODE"),
        (dict(enable="false"), "LIVE_TRADING_NOT_ENABLED"),
        (dict(enable=None), "LIVE_TRADING_NOT_ENABLED"),
        (dict(allow=None), "LIVE_TENANT_NOT_ALLOWLISTED"),
        (dict(allow="someone-else"), "LIVE_TENANT_NOT_ALLOWLISTED"),
    ],
)
def test_live_engine_is_refused_before_the_venue(harness, kwargs, code):
    engine, exchange, mp = harness
    _set(mp, **kwargs)
    result = place(engine)
    assert exchange.calls == [], "an order reached the venue while the platform was not live"
    assert result["status"] in ("blocked", "failed")  # refused, never "filled"/"submitted"
    assert code in str(result), f"refusal should name {code}: {result}"


def test_tenant_in_allowlist_is_admitted(harness):
    engine, exchange, mp = harness
    _set(mp, allow=f"other, {TENANT}")
    place(engine)
    assert len(exchange.calls) == 1


def test_platform_flipped_to_paper_after_authorization_still_blocks_the_boundary(harness):
    """The boundary re-reads the mode per order: an authorisation minted while live must
    not survive the operator switching the platform back to paper."""
    from backend_app.core.live_execution_gate import require_live_authorization

    _, _, mp = harness
    _set(mp)
    auth = authorize_live_order(
        execution_id="e1", tenant_id=str(TENANT), strategy_id="s", symbol="BTC/USDT",
        side="buy", size="0.01", exchange_id="binance", checks=REQUIRED_CHECKS,
    )
    _set(mp, mode="paper")
    with pytest.raises(LiveExecutionRefused) as ei:
        require_live_authorization(auth, symbol="BTC/USDT", side="buy", size="0.01",
                                   exchange_id="binance")
    assert ei.value.code == "LIVE_TRADING_DISABLED_BY_PLATFORM_MODE"


def test_authorization_cannot_be_minted_while_platform_is_not_live(monkeypatch):
    _set(monkeypatch, mode="paper")
    with pytest.raises(LiveExecutionRefused):
        authorize_live_order(
            execution_id="e1", tenant_id="t", strategy_id="s", symbol="BTC/USDT",
            side="buy", size="0.01", exchange_id="binance", checks=REQUIRED_CHECKS,
        )


def test_helper_accepts_live_trading_alias(monkeypatch):
    _set(monkeypatch, mode="live_trading")
    assert_platform_live_trading_enabled("anyone")
