"""
tests/test_sig_refuse01_refused_signals_are_recorded.py

SIG-REFUSE-01: a signal refused by the deployment's own execution limits left no trace.

Found during the live testnet run: a strategy whose buy block asked for 1 BTC (~$69k) under
a deployment capped at ``max_order_notional = 1000`` was refused, correctly, on every bar
its condition held. The refusal was a ``logger.warning`` and a ``continue`` - no signal
row, no risk decision, nothing in Signal Trace - so a correctly-refusing live bot was
indistinguishable from a dead one.

Asserted, through a real ``BotRunner`` tick (the DEPLOY-01 harness):
1. the over-cap order is still refused - nothing reaches the execution engine;
2. the refusal is now recorded as a signal with a FAILED risk decision whose reason names
   the execution limit;
3. a recording failure never affects trading (covered by the DEPLOY-01 tests, which run
   the same tick with the signal service raising).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tests.test_rt02_builder_strategy_runs_on_deployment_runtime import (  # noqa: F401
    USER_ID, _blueprint, execution_permitted, reg, version_row)


class _RecordingSignalService:
    def __init__(self):
        self.created = []
        self.risk_decisions = []

    async def create_signal(self, **kwargs):
        self.created.append(kwargs)
        return {"id": f"sig-{len(self.created)}"}

    async def update_risk_decision(self, **kwargs):
        self.risk_decisions.append(kwargs)
        return {}


def _run_one_tick(version_row, *, execution_config, signal_service):
    from backend_app.backend.master_executor import BotRunner
    from backend_app.core.cache.redis_manager import redis_manager
    from tests.fakes_market_data import (FakePublicExchange, make_closed_ohlcv,
                                         public_factory)
    from tests.test_rt02_builder_strategy_runs_on_deployment_runtime import (
        _ExplodingVault, _RecordingEngine, _Redis, _Risk, _Telemetry)

    bar_ms = 300_000
    probe = make_closed_ohlcv(90, "5m")
    forming_open = probe[-1][0] + bar_ms
    ticks = [
        {"timestamp": forming_open + 1_000, "price": 165.0, "amount": 1.0},
        {"timestamp": forming_open + bar_ms + 1_000, "price": 166.0, "amount": 1.0},
    ]
    bp = _blueprint(version_row, execution_config=execution_config)
    exchange = FakePublicExchange(make_closed_ohlcv(90, "5m"), trades=ticks)
    state = SimpleNamespace(
        vault=_ExplodingVault(), telemetry=_Telemetry(), risk=_Risk(), alert=MagicMock()
    )
    bot = BotRunner(
        USER_ID, "ETH/USDT", bp, state, paper_trading=True,
        public_market_data_factory=public_factory(exchange),
    )

    async def scenario():
        import unittest.mock as mock
        with mock.patch.object(
            redis_manager, "get_client", AsyncMock(return_value=_Redis())
        ), mock.patch(
            "backend_app.backend.signal_service.get_signal_service",
            AsyncMock(return_value=signal_service),
        ):
            await bot._recover_live_state()
            engine = _RecordingEngine()
            bot.execution_engine = engine
            try:
                await asyncio.wait_for(bot._inner_loop(), timeout=5)
            except asyncio.TimeoutError:
                pass
            # Let the fire-and-forget recorder finish.
            for _ in range(20):
                await asyncio.sleep(0)
            return engine

    return bot, asyncio.run(scenario())


def test_an_over_cap_order_is_refused_and_recorded_as_a_rejected_signal(
    version_row, execution_permitted
):
    svc = _RecordingSignalService()
    _bot, engine = _run_one_tick(
        version_row, execution_config={"max_order_notional": 1}, signal_service=svc
    )
    assert engine.orders == [], "the over-cap order must still be refused"
    assert svc.created, (
        "the refused signal left no record: a correctly-refusing bot looks dead in the UI"
    )
    assert svc.risk_decisions, "the refusal must be recorded as a risk decision"
    decision = svc.risk_decisions[-1]
    assert decision["risk_passed"] is False
    assert "max_order_notional" in decision["risk_reason"]
    assert "execution limits" in decision["risk_reason"]
