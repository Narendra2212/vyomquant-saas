"""
tests/test_obs01_bot_reports_its_runtime_status.py

OBS-01: a running bot recorded nothing about its own progress.

Found during the live testnet run: a live bot whose condition held on every bar for eleven
minutes produced no signal, no refusal and no order. Its deployment said ``running`` and its
claim heartbeat said the process was alive; nothing said whether market data was arriving,
whether candles were closing, or what the stream's errors were. Those were WARNING log
lines in a container nobody could read from the product.

Asserted:
1. a real ``BotRunner`` tick persists a self-report naming its phase, ticks, closed bars and
   last evaluation, under its deployment id;
2. a stream failure is reported as ``phase='stream_error'`` with the error, which is the
   case that used to be invisible (``DataEngine`` retries network errors forever);
3. the store writes ``runtime_status`` on the deployment row and never raises;
4. migration 066 adds the column.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_rt02_builder_strategy_runs_on_deployment_runtime import (  # noqa: F401
    USER_ID, _blueprint, execution_permitted, reg, version_row)

REPO = Path(__file__).resolve().parents[1]


def _run(version_row, *, ticks, exchange_kwargs=None, deployment_id="dep-obs01"):
    from backend_app.backend.master_executor import BotRunner
    from backend_app.core.cache.redis_manager import redis_manager
    from tests.fakes_market_data import (FakePublicExchange, make_closed_ohlcv,
                                         public_factory)
    from tests.test_rt02_builder_strategy_runs_on_deployment_runtime import (
        _ExplodingVault, _RecordingEngine, _Redis, _Risk, _Telemetry)

    bp = dict(_blueprint(version_row, execution_config={}))
    bp["deployment_id"] = deployment_id
    exchange = FakePublicExchange(make_closed_ohlcv(90, "5m"), trades=ticks, **(exchange_kwargs or {}))
    state = SimpleNamespace(
        vault=_ExplodingVault(), telemetry=_Telemetry(), risk=_Risk(), alert=MagicMock()
    )
    bot = BotRunner(
        USER_ID, "ETH/USDT", bp, state, paper_trading=True,
        public_market_data_factory=public_factory(exchange),
    )
    written = []

    async def _capture(_self, dep_id, status):
        written.append((dep_id, dict(status)))
        return True

    async def scenario():
        with patch.object(redis_manager, "get_client", AsyncMock(return_value=_Redis())), \
             patch("backend_app.backend.signal_service.get_signal_service",
                   AsyncMock(side_effect=RuntimeError("no db in this test"))), \
             patch("backend_app.backend.deployment_state.DeploymentStateStore.write_runtime_status",
                   _capture), \
             patch("backend_app.backend.master_executor.RUNTIME_REPORT_INTERVAL_S", 0.0):
            await bot._recover_live_state()
            bot.execution_engine = _RecordingEngine()
            try:
                await asyncio.wait_for(bot._inner_loop(), timeout=5)
            except asyncio.TimeoutError:
                pass
            for _ in range(20):
                await asyncio.sleep(0)

    asyncio.run(scenario())
    return bot, written


def _ticks():
    from tests.fakes_market_data import make_closed_ohlcv

    bar_ms = 300_000
    forming_open = make_closed_ohlcv(90, "5m")[-1][0] + bar_ms
    return [
        {"timestamp": forming_open + 1_000, "price": 165.0, "amount": 1.0},
        {"timestamp": forming_open + bar_ms + 1_000, "price": 166.0, "amount": 1.0},
    ]


def test_a_running_bot_reports_ticks_closed_bars_and_its_last_evaluation(
    version_row, execution_permitted
):
    _bot, written = _run(version_row, ticks=_ticks())
    assert written, "the bot persisted no runtime status at all"
    assert {dep for dep, _ in written} == {"dep-obs01"}
    first, last = written[0][1], written[-1][1]
    assert first["phase"] == "waiting_for_market_data"
    assert first["mode"] == "paper"
    assert last["phase"] == "streaming"
    assert last["ticks"] >= 2
    assert last["bars_closed"] >= 1
    assert last.get("last_bar_closed_at")
    assert last.get("last_evaluation", "").startswith(("signal", "no_signal", "refused"))
    assert "updated_at" in last


def test_a_stream_error_is_reported_with_the_error():
    from backend_app.backend.master_executor import BotRunner

    bot = BotRunner.__new__(BotRunner)
    bot.blueprint = {"deployment_id": "dep-x"}
    bot.graph_eval = None
    written = []

    async def scenario():
        async def _capture(_self, dep_id, status):
            written.append((dep_id, dict(status)))
            return True

        with patch("backend_app.backend.deployment_state.DeploymentStateStore.write_runtime_status",
                   _capture):
            bot._on_stream_error("Tick Stream", ConnectionError("wss refused"))
            for _ in range(10):
                await asyncio.sleep(0)

    asyncio.run(scenario())
    assert written, "a stream error was not reported"
    _dep, status = written[-1]
    assert status["phase"] == "stream_error"
    assert "Tick Stream" in status["last_error"] and "wss refused" in status["last_error"]


def test_the_data_engine_hands_stream_errors_to_its_owner():
    from backend_app.backend.data_seeking_engine import DataEngine

    seen = []
    engine = DataEngine(SimpleNamespace(has={"watchTrades": True}))
    engine.on_stream_error = lambda name, err: seen.append((name, str(err)))

    async def scenario():
        with patch("asyncio.sleep", AsyncMock()):
            await engine._handle_stream_error("Tick Stream", ValueError("boom"))

    asyncio.run(scenario())
    assert seen == [("Tick Stream", "boom")]


@pytest.mark.asyncio
async def test_the_store_writes_runtime_status_and_never_raises():
    from backend_app.backend.deployment_state import DeploymentStateStore

    rows = [{"id": "d1", "status": "running"}]

    class _Q:
        def __init__(self):
            self.payload, self.filters = None, []

        def update(self, payload):
            self.payload = payload
            return self

        def eq(self, col, val):
            self.filters.append((col, val))
            return self

        def execute(self):
            hit = [r for r in rows if all(r.get(c) == v for c, v in self.filters)]
            for r in hit:
                r.update(self.payload)
            return SimpleNamespace(data=hit)

    db = SimpleNamespace(table=lambda name: _Q())
    assert await DeploymentStateStore(lambda: db).write_runtime_status("d1", {"phase": "streaming"})
    assert rows[0]["runtime_status"] == {"phase": "streaming"}

    def _boom():
        raise RuntimeError("no database")

    assert await DeploymentStateStore(_boom).write_runtime_status("d1", {"phase": "x"}) is False


def test_migration_066_adds_the_column():
    sql = (REPO / "backend_app" / "migrations" / "066_obs01_deployment_runtime_status.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS runtime_status jsonb" in sql
