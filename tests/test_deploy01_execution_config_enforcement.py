"""DEPLOY-01, SECOND FIX -- real enforcement of ``max_order_notional`` and
``max_open_positions`` at order-construction time.

THE FINDING THIS CLOSES
    ``deployment_binding.EXECUTION_CONFIG_FIELDS`` names five per-deployment execution
    limits. A prior pass (``tests/test_deploy01_execution_config_fields_not_enforced.py``)
    established that none of the five were read anywhere on the execution path and made
    ``normalise_execution_config`` refuse all five outright rather than silently discard
    them. That closed the SILENT danger but left the feature not working at all.

    This file is the real enforcement for two of the five:

    * ``max_order_notional`` -- refused (BLOCK, not clamped) at order-construction time
      when the signal's notional exceeds the deployment's own cap.
    * ``max_open_positions`` -- refused when opening a new position would exceed the
      deployment's own open-position count (which, for one ``BotRunner`` per
      ``(user, symbol, strategy)``, is 0 or 1 -- see
      ``master_executor.evaluate_deployment_execution_limits``'s own docstring for why that
      makes this cap bind only at ``0``).

    ``slippage_tolerance_bps``, ``order_timeout_seconds`` and ``retry_policy`` remain
    refused at deploy time (``UNENFORCED_EXECUTION_CONFIG_FIELDS``) -- this file does not
    touch them.

THE DESIGN: THE CONFIG TRAVELS IN THE BLUEPRINT, NOT A NEW LOOKUP
    ``BotRunner`` already receives one object per-deployment at construction
    (``strategy_blueprint``) carrying ``deployment_id``, ``exchange_id`` and ``mode``.
    ``deployment_runtime.build_runtime_blueprint`` now adds ``execution_config`` to that
    same object (sourced from ``deployment_binding.evaluate_binding``'s
    ``DeploymentBinding.execution_config``, and from the deployment row itself on resume
    after a restart -- ``rebuild_for_resume``). No new per-deployment cache or lookup was
    introduced; this is option (b) in the finding: the config threaded explicitly through
    the call chain that already carries every other per-deployment fact.

WHAT THIS FILE PROVES
    1. ``evaluate_deployment_execution_limits`` (the pure gate ``BotRunner._inner_loop``
       calls) refuses an oversized order against ``max_order_notional``, allows one within
       the cap, is a complete no-op for a deployment with no ``execution_config`` at all
       (no regression to the pre-existing default behaviour), refuses opening the
       deployment's one possible position at ``max_open_positions=0`` and allows it at any
       cap >= 1, and never blocks adding to an already-open position or a reduce-only
       order.
    2. ``deployment_runtime.build_runtime_blueprint`` carries ``execution_config`` into the
       blueprint, and ``rebuild_for_resume`` sources it from the deployment row.
    3. ``BotRunner.__init__`` reads it off the blueprint into ``self.execution_config``,
       defaulting to ``{}`` for any blueprint built before DEPLOY-01.
    4. End to end: a real paper ``BotRunner`` (the same fixture-driven boot
       ``tests/test_rt02_builder_strategy_runs_on_deployment_runtime.py`` uses) with a
       hostile ``max_order_notional`` produces NO order through the execution engine on a
       tick that, with no cap, DOES produce one.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from backend_app.backend import deployment_binding as db
from backend_app.backend import deployment_runtime as dr
from backend_app.backend.master_executor import evaluate_deployment_execution_limits

# Reused fixtures and helpers from the companion suite -- same real compiled plan, same
# fake-exchange boot sequence, no second implementation of either.
from tests.test_rt02_builder_strategy_runs_on_deployment_runtime import (  # noqa: F401
    USER_ID, _blueprint, _boot_paper_bot, execution_permitted, reg, version_row)


# ══════════════════════════════════════════════════════════════════════════
# 1. THE PURE GATE
# ══════════════════════════════════════════════════════════════════════════


class TestEvaluateDeploymentExecutionLimits:
    def test_an_empty_execution_config_is_a_complete_noop(self):
        """No regression: a deployment that never set execution_config behaves exactly as
        before this function existed, for every side/size/exposure combination."""
        for side in ("buy", "sell"):
            for exposure in (Decimal("0"), Decimal("5")):
                assert evaluate_deployment_execution_limits(
                    {}, side=side, qty=Decimal("1000"), price=Decimal("1000"),
                    current_exposure=exposure, is_reduce_only=False,
                ) is None

    def test_max_order_notional_refuses_an_oversized_order(self):
        reason = evaluate_deployment_execution_limits(
            {"max_order_notional": 100}, side="buy", qty=Decimal("1"),
            price=Decimal("1000"), current_exposure=Decimal("0"), is_reduce_only=False,
        )
        assert reason is not None
        assert "max_order_notional" in reason

    def test_max_order_notional_allows_an_order_within_the_cap(self):
        reason = evaluate_deployment_execution_limits(
            {"max_order_notional": 10_000}, side="buy", qty=Decimal("1"),
            price=Decimal("1000"), current_exposure=Decimal("0"), is_reduce_only=False,
        )
        assert reason is None

    def test_max_order_notional_is_skipped_for_a_reduce_only_order(self):
        """A position-reducing order is not the risk this cap bounds (see the function's
        own docstring); it must not block an exit."""
        reason = evaluate_deployment_execution_limits(
            {"max_order_notional": 1}, side="sell", qty=Decimal("1000"),
            price=Decimal("1000"), current_exposure=Decimal("1000"), is_reduce_only=True,
        )
        assert reason is None

    def test_max_open_positions_zero_refuses_opening_the_deployments_only_position(self):
        reason = evaluate_deployment_execution_limits(
            {"max_open_positions": 0}, side="buy", qty=Decimal("1"), price=Decimal("100"),
            current_exposure=Decimal("0"), is_reduce_only=False,
        )
        assert reason is not None
        assert "max_open_positions" in reason

    def test_max_open_positions_one_allows_the_first_and_only_possible_entry(self):
        """One BotRunner is one (user, symbol, strategy) and can hold at most one net
        position, so a cap >= 1 can never refuse the opening order -- documented, not
        hidden, in the function's own docstring."""
        reason = evaluate_deployment_execution_limits(
            {"max_open_positions": 1}, side="buy", qty=Decimal("1"), price=Decimal("100"),
            current_exposure=Decimal("0"), is_reduce_only=False,
        )
        assert reason is None

    def test_max_open_positions_does_not_block_adding_to_an_already_open_position(self):
        """Adding to an existing position is not OPENING a new one."""
        reason = evaluate_deployment_execution_limits(
            {"max_open_positions": 0}, side="buy", qty=Decimal("1"), price=Decimal("100"),
            current_exposure=Decimal("5"), is_reduce_only=False,
        )
        assert reason is None

    def test_max_open_positions_does_not_apply_to_a_sell(self):
        """The cap is about OPENING a position; a sell (even a naked one, which a different
        layer refuses on its own terms) is never the side this cap evaluates."""
        reason = evaluate_deployment_execution_limits(
            {"max_open_positions": 0}, side="sell", qty=Decimal("1"), price=Decimal("100"),
            current_exposure=Decimal("0"), is_reduce_only=False,
        )
        assert reason is None

    def test_both_caps_together_each_still_apply(self):
        reason = evaluate_deployment_execution_limits(
            {"max_order_notional": 100, "max_open_positions": 0}, side="buy",
            qty=Decimal("1"), price=Decimal("1000"), current_exposure=Decimal("0"),
            is_reduce_only=False,
        )
        assert reason is not None  # either cap alone would already refuse this


# ══════════════════════════════════════════════════════════════════════════
# 2. THE BLUEPRINT CARRIES execution_config
# ══════════════════════════════════════════════════════════════════════════


class TestBuildRuntimeBlueprintCarriesExecutionConfig:
    def test_execution_config_is_included_verbatim(self, version_row):
        bp = _blueprint(version_row, execution_config={"max_order_notional": 500})
        assert bp["execution_config"] == {"max_order_notional": 500}

    def test_an_absent_execution_config_defaults_to_an_empty_dict(self, version_row):
        bp = _blueprint(version_row)
        assert bp["execution_config"] == {}

    def test_rebuild_for_resume_sources_execution_config_from_the_deployment_row(
        self, version_row
    ):
        """A resumed deployment (after a restart) must keep the SAME limits it was
        deployed with -- they live on the row, not only on the original request."""
        row = {
            "id": "dep-resume-1", "strategy_id": "strat-1", "version_id": "ver-1",
            "mode": "paper", "exchange_symbol": "ETH/USDT", "exchange_id": "binance",
            "execution_config": {"max_order_notional": 777},
        }

        async def load_version(_version_id):
            return version_row

        blueprint, paper_trading = asyncio.run(
            dr.rebuild_for_resume(row, load_version=load_version)
        )
        assert paper_trading is True
        assert blueprint["execution_config"] == {"max_order_notional": 777}

    def test_rebuild_for_resume_with_no_execution_config_column_defaults_to_empty(
        self, version_row
    ):
        """A database where 004e is unapplied has no such column at all; `.get` must not
        raise and the blueprint must still be startable with an empty, no-op config."""
        row = {
            "id": "dep-resume-2", "strategy_id": "strat-1", "version_id": "ver-1",
            "mode": "paper", "exchange_symbol": "ETH/USDT", "exchange_id": "binance",
        }

        async def load_version(_version_id):
            return version_row

        blueprint, _ = asyncio.run(dr.rebuild_for_resume(row, load_version=load_version))
        assert blueprint["execution_config"] == {}


# ══════════════════════════════════════════════════════════════════════════
# 3. BotRunner READS IT OFF THE BLUEPRINT
# ══════════════════════════════════════════════════════════════════════════


class TestBotRunnerReadsExecutionConfigFromTheBlueprint:
    def test_execution_config_is_copied_from_the_blueprint(self, version_row):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from backend_app.backend.master_executor import BotRunner

        state = SimpleNamespace(vault=None, telemetry=None, risk=None, alert=MagicMock())
        bp = _blueprint(version_row, execution_config={"max_open_positions": 2})
        bot = BotRunner(USER_ID, "ETH/USDT", bp, state, paper_trading=True)
        assert bot.execution_config == {"max_open_positions": 2}

    def test_execution_config_defaults_to_an_empty_dict_when_absent(self, version_row):
        """Every blueprint built before DEPLOY-01 (and every legacy, non-graph deploy that
        never threads one through) has no `execution_config` key at all."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from backend_app.backend.master_executor import BotRunner

        state = SimpleNamespace(vault=None, telemetry=None, risk=None, alert=MagicMock())
        bp = _blueprint(version_row)
        bp.pop("execution_config", None)
        bot = BotRunner(USER_ID, "ETH/USDT", bp, state, paper_trading=True)
        assert bot.execution_config == {}


# ══════════════════════════════════════════════════════════════════════════
# 4. END TO END: A REAL PAPER BotRunner, A REAL TICK
# ══════════════════════════════════════════════════════════════════════════


class TestEndToEndThroughARealBotRunner:
    """Same fixture-driven boot as
    tests/test_rt02_builder_strategy_runs_on_deployment_runtime.py -- a real compiled plan,
    a fake keyless exchange, a recording execution engine. The only difference is the
    deployment's own ``execution_config``.
    """

    @staticmethod
    def _run_one_tick(version_row, *, execution_config):
        from backend_app.core.cache.redis_manager import redis_manager
        from tests.fakes_market_data import make_closed_ohlcv
        from tests.test_rt02_builder_strategy_runs_on_deployment_runtime import \
            _RecordingEngine, _Redis

        bar_ms = 300_000
        probe = make_closed_ohlcv(90, "5m")
        forming_open = probe[-1][0] + bar_ms
        ticks = [
            {"timestamp": forming_open + 1_000, "price": 165.0, "amount": 1.0},
            {"timestamp": forming_open + bar_ms + 1_000, "price": 166.0, "amount": 1.0},
        ]
        bp = _blueprint(version_row, execution_config=execution_config)
        from backend_app.backend.master_executor import BotRunner
        from tests.fakes_market_data import FakePublicExchange, public_factory
        from tests.test_rt02_builder_strategy_runs_on_deployment_runtime import \
            _ExplodingVault, _Risk, _Telemetry
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        exchange = FakePublicExchange(make_closed_ohlcv(90, "5m"), trades=ticks)
        vault = _ExplodingVault()
        state = SimpleNamespace(
            vault=vault, telemetry=_Telemetry(), risk=_Risk(), alert=MagicMock()
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
                AsyncMock(side_effect=RuntimeError("no db in this test")),
            ):
                await bot._recover_live_state()
                engine = _RecordingEngine()
                bot.execution_engine = engine
                try:
                    await asyncio.wait_for(bot._inner_loop(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                return engine

        engine = asyncio.run(scenario())
        return bot, engine

    def test_a_tight_max_order_notional_refuses_the_order_the_same_tick_would_otherwise_place(
        self, version_row, execution_permitted
    ):
        # POSITIVE CONTROL first: with no cap at all, this exact tick places an order.
        _bot_unbounded, engine_unbounded = self._run_one_tick(
            version_row, execution_config={}
        )
        assert engine_unbounded.orders, (
            "positive control failed: this tick must place an order with no "
            "execution_config, or the refusal below proves nothing"
        )

        # Now with a cap no real order on this tick could possibly clear.
        bot, engine = self._run_one_tick(
            version_row, execution_config={"max_order_notional": 1}
        )
        assert engine.orders == [], (
            "a max_order_notional=1 deployment placed an order through the execution "
            "engine; the per-deployment cap was not consulted"
        )
        assert bot.recovered_exposure_coins == Decimal("0"), (
            "the refused order must not have moved the bot's exposure"
        )

    def test_max_open_positions_zero_refuses_the_same_opening_order(
        self, version_row, execution_permitted
    ):
        bot, engine = self._run_one_tick(
            version_row, execution_config={"max_open_positions": 0}
        )
        assert engine.orders == [], (
            "a max_open_positions=0 deployment opened a position through the execution "
            "engine; the per-deployment cap was not consulted"
        )
        assert bot.recovered_exposure_coins == Decimal("0")

    def test_max_open_positions_one_still_allows_the_tick_to_trade(
        self, version_row, execution_permitted
    ):
        """Negative control for the test above: a cap of 1 (the deployment's only
        possible position) must NOT be refused."""
        bot, engine = self._run_one_tick(
            version_row, execution_config={"max_open_positions": 1}
        )
        assert engine.orders, (
            "a max_open_positions=1 cap refused the deployment's first and only possible "
            "position; the cap must bind at 0, not at 1"
        )
        assert bot.recovered_exposure_coins > 0
