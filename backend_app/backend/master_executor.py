"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — BACKEND: master_executor.py                                   ║
║                                                                          ║
║  BotRunner: orchestrates every engine into a live trading loop.          ║
║  One instance per (user, symbol). Managed by FleetManager.               ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  ME-1  CRITICAL: live_state only had Close+ML — all indicator values     ║
║         were missing, so every strategy condition evaluated to False      ║
║  ME-2  Exchange hardcoded to "binance" — ignores user's actual exchange  ║
║  ME-3  is_reduce_only = (side=='sell') — wrong for short-open orders    ║
║  ME-4  Exposure tracking assumed full fill — breaks on partial fills     ║
║  ME-5  No reconnection logic if WebSocket drops mid-session              ║
║  ME-6  No timeout on ML inference thread — hangs the tick loop           ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
import asyncio
import logging
import os
import time
from decimal import Decimal
from typing import Optional

import numpy as np

from backend_app.backend.data_processing_engine import DataProcessorEngine
from backend_app.backend.indicators_backend import (adx, atr,
                                                    awesome_oscillator,
                                                    bollinger_bands, cci,
                                                    choppiness_index, cmf,
                                                    donchian_channel, ema,
                                                    fisher_transform,
                                                    historical_volatility, hma,
                                                    ichimoku_cloud,
                                                    keltner_channels, macd,
                                                    mfi, momentum, obv,
                                                    pivot_standard, psar, roc,
                                                    rolling_vwap,
                                                    rolling_z_score, rsi, sma,
                                                    stochastic, supertrend,
                                                    trix, vortex_indicator,
                                                    williams_r, wma)
from backend_app.backend.deployment_runtime import (ClosedBar,
                                                    GraphSignalEvaluator,
                                                    is_graph_blueprint)
from backend_app.backend.execution_environment import ExecutionEnvironment
from backend_app.backend.ml_models import create_ml_block
from backend_app.core.risk_manager import RiskVerdict, TradeRequest
from backend_app.backend.strategy_builder import StrategyEngine

logger = logging.getLogger("BotRunner")

# All Numba indicators — import from the single indicators module

# Timeframe name → seconds
TF_SEC = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
}

# Maximum seconds to wait for ML inference before skipping
ML_INFERENCE_TIMEOUT = float(os.getenv("ML_INFERENCE_TIMEOUT", 2.0))

# Maximum reconnection attempts before giving up
MAX_RECONNECT_ATTEMPTS = int(os.getenv("MAX_RECONNECT_ATTEMPTS", 5))


def settle_exposure(*, side: str, qty: Decimal, previous_exposure: Decimal, result):
    """RT-06: the exposure consequence of one ``execute_with_idempotency`` result.

    Returns ``(filled_qty, new_exposure)``. ONLY a ``completed`` result is a fill; blocked,
    failed and ``skipped_*`` outcomes (a dedupe replay, a contention skip, an ambiguous
    prior submission, a refused live order) executed nothing in this call and leave
    exposure exactly as it was. This used to treat every status except two as a full fill
    of ``qty`` and counted a deduplicated ``skipped_completed`` replay a second time.
    """
    status = result.get("status") if result else None
    if status != "completed":
        return Decimal("0"), previous_exposure

    trade_result = result.get("result", {}) or {}
    filled_qty = Decimal(str((trade_result.get("trade_result") or {}).get("size", qty)))
    if filled_qty <= 0:
        return Decimal("0"), previous_exposure

    if side == "buy":
        new_exposure = (previous_exposure + filled_qty).quantize(Decimal("0.00000001"))
    else:
        new_exposure = max(Decimal("0"), (previous_exposure - filled_qty).quantize(Decimal("0.00000001")))
    return filled_qty, new_exposure


class BotRunner:
    """
    One BotRunner per (user, symbol, strategy).
    Injected with global singletons via `global_state` to share
    RiskManager and TelemetryEngine across all 500 bots.
    """

    def __init__(
        self,
        user_id: str,
        symbol: str,
        strategy_blueprint: dict,
        global_state,
        *,
        paper_trading: bool,
        public_market_data_factory=None,
    ):
        self.user_id = user_id
        self.symbol = symbol
        self.blueprint = strategy_blueprint

        # RT-01: how a PAPER bot obtains market data. ``None`` = the venue's public, keyless
        # ccxt endpoints. Tests inject a fake. It is NEVER the DEV_MODE mock: that streams a
        # constant 50,000 for every symbol.
        self._public_market_data_factory = public_market_data_factory
        # RT-01/RT-08: why the runtime stopped, for the fleet to write to the deployment row.
        self.exit_reason: Optional[str] = None
        # RT-08: set by the fleet from the deployment row. A paused bot keeps consuming market
        # data (so indicators stay current) but opens no new orders.
        self.paused: bool = False

        # ── THE MODE. REQUIRED, KEYWORD-ONLY, AND NOT READ FROM THE BLUEPRINT ──
        #
        # This used to be `self.blueprint.get("paper_trading", True)`, evaluated inside
        # `_recover_live_state`. NOTHING IN THE CODEBASE EVER WROTE THAT KEY — a repo-wide
        # search found it at that one read site and nowhere else, and nowhere in the
        # frontend as a payload field. So the default won on every deployment, and the
        # entire live path was recorded without being run: `deploy_version` passed every
        # Requirement 13 gate, checked the LIVE_TRADING entitlement, required and resolved
        # an exchange account, persisted `strategy_deployments.mode = 'live'`, moved the row
        # and the version lifecycle to RUNNING — and the runtime returned at the top of the
        # paper branch before the credentials were even decrypted.
        #
        # It failed SAFE, which is why nothing caught fire. But a trader who believes they
        # are live is not safe, and a platform that reports a live deployment it did not
        # perform is making a claim it cannot support.
        #
        # REQUIRED rather than defaulted, for the reason the defect demonstrates: a default
        # is what let an absent value decide a money-critical mode with nothing saying so.
        # `ExecutionEngine.__init__` already refuses an absent `portfolio_state` on the same
        # grounds. A caller that forgets the mode is now a TypeError at the call site.
        #
        # Regression: tests/test_live_deployment_mode_propagation.py
        if not isinstance(paper_trading, bool):
            raise ValueError(
                f"BotRunner requires paper_trading as an explicit bool; got "
                f"{paper_trading!r}. The mode decides whether this bot can place real "
                f"orders, so it is resolved by the caller that evaluated the deployment "
                f"binding and is never inferred here."
            )
        self.paper_trading = paper_trading

        # ── Global singletons (shared across all bots) ───────────────────
        self.vault = global_state.vault
        self.telemetry = global_state.telemetry
        self.risk_manager = global_state.risk
        self.alert = global_state.alert

        # ── Instance-specific engines (created per bot) ──────────────────
        self.ccxt_bridge = None
        self.exchange = None
        self.data_engine = None
        self.execution_engine = None  # Unified execution engine (replaces order_engine)

        # FIX ME-2: Read exchange from blueprint - MUST be provided
        self.exchange_id = strategy_blueprint.get("exchange_id")
        if not self.exchange_id:
            raise ValueError("exchange_id must be provided in strategy blueprint")

        # ── ML block ─────────────────────────────────────────────────────
        self.ml_model_path = strategy_blueprint.get("ml_model_path")
        self.ml_block = create_ml_block(self.ml_model_path)

        # ── Numba data processor ─────────────────────────────────────────
        tf_str = strategy_blueprint.get("timeframe", "1m")
        tf_sec = TF_SEC.get(tf_str, 60)
        self.timeframe_sec = tf_sec
        self.data_processor = DataProcessorEngine(
            symbols_list=[symbol],
            timeframes_sec=[tf_sec],
            max_history=1_000,
        )

        # ── Strategy evaluation engine ───────────────────────────────────
        self.strategy_engine = StrategyEngine(self.risk_manager, self.telemetry)

        # RT-02/RT-05: a deployment of a builder-saved strategy is evaluated by the canonical
        # DAG runtime (DAGEngine.execute_plan), not by the legacy buy_logic/sell_logic engine.
        # Constructed here so an uncompilable graph fails the START, not the first tick.
        self.graph_eval: Optional[GraphSignalEvaluator] = (
            GraphSignalEvaluator(strategy_blueprint)
            if is_graph_blueprint(strategy_blueprint)
            else None
        )

        # ── Exposure tracking (coins held) ───────────────────────────────
        self.recovered_exposure_coins = Decimal("0")

    # ══════════════════════════════════════════════════════════════════════
    #  BOOT SEQUENCE
    # ══════════════════════════════════════════════════════════════════════

    async def _recover_live_state(self):
        """
        Connects to exchange, loads ML model, warms up Numba buffers.
        FIX ME-2: Uses self.exchange_id instead of hardcoded 'binance'.
        """
        logger.info(
            f"Booting bot: user={self.user_id} symbol={self.symbol} "
            f"exchange={self.exchange_id}"
        )

        # FIX 3: Use absolute imports — bare imports break in command_worker.py
        from backend_app.backend.connection_engine import ConnectionEngine
        from backend_app.backend.data_seeking_engine import DataEngine
        from backend_app.core.execution_engine import ExecutionEngine

        # The mode this bot was CONSTRUCTED with, not one derived from the blueprint. See
        # the constructor for what reading it from the blueprint did.
        is_paper = self.paper_trading

        if self.ccxt_bridge is not None:   # a reconnect: release the old client first
            try:
                await self.ccxt_bridge.disconnect()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"previous market-data client did not close cleanly: {exc}")

        # PAPER bots hold NO credentials and place NO real orders, but they need REAL market
        # data: a paper result computed on invented prices is worse than no result.
        #
        # RT-01. This branch used to build a ``ConnectionEngine`` with no keys, call
        # ``_apply_mock_interface()`` -- a no-op, because ``exchange`` is None until
        # ``connect()`` -- and hand ``DataEngine(None)`` to the loop, which died on its first
        # attribute read, retried five times and exited "cleanly" while the deployment row still
        # said ``running``. Even with a client the mock streams a constant 50,000 for every
        # symbol. It now connects KEYLESSLY to the venue's public market data, with the DEV_MODE
        # mock disabled, so a failure to reach the venue is a failed start the fleet records
        # rather than fabricated prices.
        if is_paper:
            logger.info(
                f"[PAPER] Bot {self.user_id}/{self.symbol}: connecting to PUBLIC market data "
                f"on {self.exchange_id} (no credentials; no real orders)."
            )
            self.ccxt_bridge, self.exchange = await self._connect_public_market_data()
            self.data_engine = DataEngine(self.exchange)

            # ── PAPER-01: THE CANONICAL PAPER LEDGER ──────────────────────
            # This module used to have NO import edge to `backend_app/backend/paper/`
            # at all, so a bot's paper fills were simulated inside `ExecutionEngine`
            # against the synthetic balance below and written to no `paper_*` table.
            # A trader who deployed in paper mode got real simulated executions that
            # `GET /api/paper/account`, `/positions`, `/orders`, `/trades` and
            # `/summary` -- and therefore `PaperTrading.jsx` -- did not show, and that
            # did not survive a restart.
            #
            # The ledger routes each fill through `PaperTradingService.place_order`,
            # which is the same canonical path `POST /api/paper/orders` uses: the
            # arithmetic is `paper/paper_accounting`, the writes are
            # `paper/paper_repository`, and the DEFAULT account needs no `session_id`
            # -- which is what makes it usable here, since a BotRunner bot has no
            # Paper_Session. Migration 013 relaxed `session_id` on the seven child
            # tables for exactly this case.
            #
            # The engine's in-process cash and positions become a COPY of the stored
            # rows (`ExecutionEngine._reconcile_from_paper_ledger`), so there is one
            # answer to "what is my cash" rather than two.
            from backend_app.backend.paper.bot_paper_ledger import BotPaperLedger

            paper_ledger = BotPaperLedger(self.user_id)

            # Use synthetic paper balance
            portfolio_state = {"total_equity": 10000.0}
            self.execution_engine = ExecutionEngine(
                fee_rate=0.001,
                slippage=0.0005,
                risk_manager=self.risk_manager,
                portfolio_state=portfolio_state,
                exchange_executor=None,
                # PAPER-01. Present only on the paper branch: a LIVE engine must never
                # hold one, and `tests/test_paper01_botrunner_fills_reach_the_ledger_postgres.py`
                # asserts the live construction below passes none.
                paper_ledger=paper_ledger,
                # PAPER, stated rather than implied by the `exchange_executor=None`
                # above. That null used to be the only thing keeping this engine's
                # orders off a real exchange; the engine now refuses the live branch
                # outright when it was built for paper, so hoisting this construction
                # or passing an executor by mistake is a refusal instead of a real
                # order. Regression:
                # tests/test_execution_engine_mode_matches_its_branch.py
                execution_environment=ExecutionEnvironment.PAPER,
            )
            await self._warm_up_from_history()
            logger.info(f"[PAPER] Bot {self.user_id}/{self.symbol} ready (paper mode).")
            return

        # LIVE path — real keys, real exchange, real OHLCV warmup
        keys = self.vault.load_decrypted_keys(self.user_id, self.exchange_id)
        self.ccxt_bridge = ConnectionEngine(
            self.exchange_id,
            api_key=keys["api_key"],
            secret_key=keys["secret_key"],
            password=keys.get("password"),
        )
        self.exchange = await self.ccxt_bridge.connect()
        self.data_engine = DataEngine(self.exchange)
        
        # 2. Load ML model into RAM (zero disk I/O during trading)
        if self.ml_block and self.ml_model_path:
            self.ml_block.load_model_to_memory(self.ml_model_path)

        # 3. Recover live balance for position sizing
        #
        # RT-07: a live bot is sized from the account's MEASURED quote balance. There is
        # no substitute figure: this used to fall back to 10,000 when the wallet showed
        # a zero (or no) quote balance, and to a fabricated {"USDT": 10000} wallet when
        # no API key was present, and that number became the engine's `total_equity` -
        # i.e. the capital ceiling real orders are checked against.
        if not self.ccxt_bridge.api_key:
            raise RuntimeError(
                f"Refusing to start live bot {self.user_id}/{self.symbol}: no exchange API "
                "key is available, so the account balance cannot be measured."
            )
        wallet = await self.data_engine.fetch_wallet_balance_snapshot()

        base_coin = self.symbol.split("/")[0]
        quote_coin = self.symbol.split("/")[1] if "/" in self.symbol else "USDT"

        self.recovered_exposure_coins = Decimal(
            str((wallet.get(base_coin) or {}).get("total", 0.0))
        )

        quote_balance = Decimal(str((wallet.get(quote_coin) or {}).get("total", 0)))
        if quote_balance <= 0:
            raise RuntimeError(
                f"Refusing to start live bot {self.user_id}/{self.symbol}: the measured "
                f"{quote_coin} balance is {quote_balance}, not a positive amount."
            )

        portfolio_state = {"total_equity": float(quote_balance)}

        # LIVE: Initialize real ExchangeExecutor
        #
        # `user_id` IS THE POINT OF THIS CALL, NOT DECORATION. `get_exchange_executor`
        # memoises executors in a process-global dict, and its key used to be
        # `f"{exchange_id}:{sandbox}"` — which does not mention the account. The keys
        # three lines below were just decrypted out of the vault for THIS user, so in a
        # worker that had already started a binance bot for somebody else, this call
        # returned THAT trader's executor and every order this strategy produced would
        # have been signed with their credentials and filled against their balance.
        # Regression: tests/test_exchange_executor_cache_is_per_tenant.py
        from backend_app.backend.exchange_executor import get_exchange_executor
        exchange_exec = get_exchange_executor(
            exchange_id=self.exchange_id,
            api_key=keys["api_key"],
            api_secret=keys["secret_key"],
            sandbox=self.blueprint.get("testnet", False),
            password=keys.get("password"),
            user_id=self.user_id,
        )

        # ═══════════════════════════════════════════════════════════════════
        # CRITICAL FIX C2: Use unified ExecutionEngine instead of deprecated OrderEngine
        # ═══════════════════════════════════════════════════════════════════
        self.execution_engine = ExecutionEngine(
            fee_rate=0.001,
            slippage=0.0005,
            risk_manager=self.risk_manager,
            portfolio_state=portfolio_state,
            exchange_executor=exchange_exec,
            # LIVE. This branch has already decrypted the account's keys and built a
            # real exchange executor, so the engine is told so - and the engine now
            # refuses to fall through to its paper branch if the executor is ever
            # missing, rather than simulating the fill and reporting it as executed.
            # Regression: tests/test_execution_engine_mode_matches_its_branch.py
            execution_environment=ExecutionEnvironment.LIVE,
        )

        # 4. Warm up the Numba circular buffer (and the DAG window) with historical OHLCV
        await self._warm_up_from_history()

        logger.info(
            f"Bot ready — {self.symbol} on {self.exchange_id}. "
            f"Exposure: {self.recovered_exposure_coins} {base_coin}"
        )

    async def _connect_public_market_data(self):
        """``(bridge, exchange)`` for the venue's PUBLIC data. Never carries credentials."""
        if self._public_market_data_factory is not None:
            return await self._public_market_data_factory(self.exchange_id)
        from backend_app.backend.connection_engine import ConnectionEngine

        bridge = ConnectionEngine(self.exchange_id, api_key=None, secret_key=None)
        exchange = await bridge.connect(allow_mock=False)
        return bridge, exchange

    async def _warm_up_from_history(self) -> None:
        """Fill the Numba buffer and the DAG window from CLOSED historical bars.

        The newest row an exchange returns is usually the still-FORMING candle. Treating it as
        closed would hand every indicator a partial bar and lose that bar's real close, so it is
        dropped when its period has not ended.
        """
        timeframe = self.blueprint.get("timeframe", "1m")
        ohlcv = await self.data_engine.fetch_historical_ohlcv(
            self.symbol, timeframe=timeframe, limit=1_000
        )
        now_ms = int(time.time() * 1000)
        tf_ms = self.timeframe_sec * 1000
        closed = [row for row in (ohlcv or []) if int(row[0]) + tf_ms <= now_ms]
        self.data_processor.warm_up_history(self.symbol, self.timeframe_sec, closed)
        if self.graph_eval is not None and closed:
            admitted = self.graph_eval.warm_up([ClosedBar.from_ohlcv_row(r) for r in closed])
            logger.info(
                f"Bot {self.user_id}/{self.symbol}: DAG window warmed with {admitted} closed bars."
            )

    # ══════════════════════════════════════════════════════════════════════
    #  LIVE TRADING LOOP
    # ══════════════════════════════════════════════════════════════════════

    async def _run_trading_loop(self):
        """
        Main async loop. Reconnects on transient network failures.
        FIX ME-5: Outer retry loop with exponential backoff.
        """
        reconnect_attempts = 0

        while reconnect_attempts < MAX_RECONNECT_ATTEMPTS:
            try:
                await self._inner_loop()
                break  # clean exit via CancelledError

            except asyncio.CancelledError:
                logger.info(
                    f"Bot {self.user_id}/{self.symbol} received shutdown signal."
                )
                break

            except Exception as e:
                reconnect_attempts += 1
                self._last_error = f"{type(e).__name__}: {e}"
                wait = min(2**reconnect_attempts, 60)
                logger.error(
                    f"Bot {self.user_id}/{self.symbol} loop crashed: {e}. "
                    f"Reconnecting in {wait}s (attempt {reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS})."
                )
                asyncio.create_task(
                    self.alert.send_critical(
                        system=f"BotRunner {self.symbol}",
                        message=f"Loop error (attempt {reconnect_attempts}): {e}",
                    )
                )
                await asyncio.sleep(wait)

                try:
                    # Reconnect CCXT before retrying the inner loop
                    await self._recover_live_state()
                except Exception as reconnect_err:
                    logger.critical(f"Reconnection failed: {reconnect_err}")

        if reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            self.exit_reason = (
                f"gave up after {MAX_RECONNECT_ATTEMPTS} reconnection attempts"
                + (f": {self._last_error}" if getattr(self, "_last_error", None) else "")
            )
            logger.critical(
                f"Bot {self.user_id}/{self.symbol} gave up after "
                f"{MAX_RECONNECT_ATTEMPTS} reconnection attempts."
            )

        await self.shutdown()

    async def _inner_loop(self):
        """
        Core tick processing loop.
        FIX ME-1: Computes ALL indicator values from blueprint and adds
                  them to live_state before strategy evaluation.
        FIX ME-3: Sets reduce_only based on explicit 'reduce_only' field.
        FIX ME-4: Updates exposure from actual filled amount in the order.
        FIX ME-6: Wraps ML inference in asyncio.wait_for() with timeout.
        """
        logger.info(f"Bot {self.user_id}/{self.symbol} inner loop started.")

        async for ticks in self.data_engine.stream_live_ticks(self.symbol):
            for tick in (ticks if isinstance(ticks, list) else [ticks]):
                timestamp_ms = tick.get("timestamp", int(time.time() * 1000))
                price = float(tick.get("price", tick.get("last", 0)))
                volume = float(tick.get("amount", tick.get("baseVolume", 0)))

                if price <= 0:
                    continue

                # ── Update Numba matrix ───────────────────────────────────
                new_candles = self.data_processor.process_live_tick(
                    self.symbol, timestamp_ms, price, volume
                )

                if not new_candles:
                    continue  # no closed candle yet — keep accumulating ticks

                # ── Per-tick state for the strategy engine in use ─────────
                if self.graph_eval is None:
                    live_state = await self._legacy_live_state(price)
                    if live_state is None:
                        continue
                else:
                    # The DAG window carries its own history and indicators; this dict only
                    # feeds the signal-trace audit record below.
                    live_state = {"Close": price, "ML_Prediction": 1.0}

                # ── Fetch live USDT balance for position sizing ───────────
                # Cache this every N seconds instead of every tick in production
                if self.paper_trading:
                    # A paper bot's capital is its ledger cash. Its market-data connection is
                    # keyless, so there is no wallet to read (and a read would only fail).
                    bal_usdt = float(self.execution_engine.cash)
                else:
                    try:
                        wallet = await self.data_engine.fetch_wallet_balance_snapshot()
                        bal_usdt = float(wallet.get("USDT", {}).get("free", 0.0))
                    except Exception:
                        bal_usdt = 0.0

                # ── Evaluate strategy ─────────────────────────────────────
                if self.graph_eval is not None:
                    # RT-02/RT-05: the canonical DAG evaluator (DAGEngine.execute_plan).
                    order_payload = await asyncio.to_thread(
                        self.graph_eval.on_closed_bars,
                        [ClosedBar.from_candle(c) for c in new_candles],
                        price=price,
                        equity=float(self.execution_engine.current_equity),
                        free_quote=bal_usdt,
                        position_base=float(self.recovered_exposure_coins),
                    )
                    if order_payload:
                        order_payload["user_id"] = self.user_id
                else:
                    order_payload = await self.strategy_engine.run_logic(
                        user_id=self.user_id,
                        symbol=self.symbol,
                        strategy_blueprint=self.blueprint,
                        current_state=live_state,
                        live_balance_usdt=bal_usdt,  # FIX SB-1 in strategy_builder
                    )

                if not order_payload:
                    continue

                if self.paused:
                    logger.info(f"Bot {self.user_id}/{self.symbol} is paused; signal not executed.")
                    continue

                # ── FIX ME-3: is_reduce_only from explicit payload field ──
                is_reduce = order_payload.get("reduce_only", False)

                # ── Risk check ────────────────────────────────────────────
                # Get real-time risk metrics from telemetry
                account_health = await self.telemetry.get_account_health(self.user_id)
                current_drawdown_pct = account_health.get("current_drawdown_pct", 0.0)
                daily_pnl_pct = account_health.get("daily_pnl_pct", 0.0)
                
                verdict, reason = await self.risk_manager.validate_trade_request(
                    TradeRequest(
                        user_id=self.user_id,
                        user_tier=self.blueprint.get("user_tier", "free"),
                        symbol=self.symbol,
                        side=order_payload["side"],
                        amount=order_payload["qty"],
                        current_price=price,
                        current_exposure=float(self.recovered_exposure_coins) * price,  # Decimal * float is a TypeError
                        current_drawdown_pct=current_drawdown_pct,
                        daily_pnl_pct=daily_pnl_pct,
                        is_reduce_only=is_reduce,
                    )
                )

                # ── Signal Trace Audit Trail Logging (Non-blocking) ───────
                decision = str(order_payload.get("side", "BUY")).upper()
                async def record_audit_signal(v_verdict, v_reason, v_exec_id=None, v_status="pending", v_fill=0.0, v_result=None):
                    try:
                        from backend_app.backend.signal_service import get_signal_service
                        sig_svc = await get_signal_service()
                        sig = await sig_svc.create_signal(
                            user={"id": self.user_id},
                            strategy_id=str(self.blueprint.get("id") or self.blueprint.get("strategy_id", "default")),
                            strategy_version=str(self.blueprint.get("current_version", "1.0")),
                            deployment_id=str(self.blueprint.get("deployment_id", self.blueprint.get("id", "default"))),
                            exchange_id=self.exchange_id,
                            symbol=self.symbol,
                            timeframe=str(self.blueprint.get("timeframe", "1m")),
                            worker_id=f"worker_{self.user_id}_{self.symbol.replace('/', '_')}",
                            decision=decision,
                            indicators=live_state,
                            market_info={"price": price, "volume": volume, "timestamp": timestamp_ms},
                            ml_info={"confidence": live_state.get("ML_Prediction")} if "ML_Prediction" in live_state else None
                        )
                        sig_id = sig.get("id")
                        if not sig_id:
                            return

                        # Risk evaluation audit
                        await sig_svc.update_risk_decision(
                            user={"id": self.user_id},
                            signal_id=sig_id,
                            risk_passed=(v_verdict == RiskVerdict.APPROVED),
                            risk_reason=str(v_reason or "Risk Approved"),
                            position_size=float(order_payload["qty"]),
                            capital=bal_usdt,
                            exposure=float(self.recovered_exposure_coins * Decimal(str(price))),
                            drawdown_check=bool(current_drawdown_pct < 0.2)
                        )

                        if v_verdict != RiskVerdict.APPROVED or not v_exec_id:
                            return

                        # Order & Execution update
                        order_st = "FILLED" if v_status in ["completed", "skipped_completed"] else "FAILED"
                        await sig_svc.update_order(
                            user={"id": self.user_id},
                            signal_id=sig_id,
                            order_id=v_exec_id,
                            exchange_order_id=None,
                            order_status=order_st,
                            quantity=float(order_payload["qty"]),
                            filled=float(v_fill),
                            remaining=0.0 if order_st == "FILLED" else float(order_payload["qty"]),
                            average_price=float(price),
                            fees=float(Decimal(str(v_fill)) * Decimal(str(price)) * Decimal("0.001")),
                            slippage=0.0005,
                            latency_ms=10.0
                        )

                        if order_st == "FILLED":
                            await sig_svc.update_execution(
                                user={"id": self.user_id},
                                signal_id=sig_id,
                                trade_id=f"tr_{v_exec_id[:12]}",
                                pnl=0.0,
                                realized_pnl=None
                            )
                    except Exception as sig_err:
                        logger.debug(f"Signal trace audit logging exception: {sig_err}")

                if verdict != RiskVerdict.APPROVED:
                    logger.warning(f"Risk check failed: {reason}")
                    asyncio.create_task(record_audit_signal(verdict, reason))
                    continue

                # ── ATOMIC EXECUTION + EXPOSURE UPDATE ─────────────────────
                # STEP 1: Generate execution_id for idempotency and locking using the deterministic logic
                import uuid
                from datetime import datetime

                from backend_app.core.models.execution_record import \
                    generate_execution_id
                execution_id = generate_execution_id(
                    tenant_id=uuid.UUID(self.user_id),
                    strategy_id=self.blueprint.get("strategy_id", "default"),
                    symbol=self.symbol,
                    timestamp=datetime.utcnow(),
                    side=order_payload["side"],
                    qty=float(order_payload["qty"]),
                    price=float(price)
                )

                # STEP 2: Acquire Redis lock for atomic execution + exposure update
                from backend_app.core.cache.redis_manager import redis_manager
                redis_client = await redis_manager.get_client()
                lock_key = f"execution:{self.user_id}:{self.symbol}:{execution_id}"
                lock_acquired = await redis_client.set(lock_key, "1", nx=True, ex=30)

                if not lock_acquired:
                    logger.warning(f"🔒 Could not acquire execution lock for {self.symbol} - skipping tick")
                    continue

                # Store pre-execution exposure for rollback
                previous_exposure = Decimal(str(self.recovered_exposure_coins))
                side = order_payload["side"]
                qty = Decimal(str(order_payload["qty"]))

                try:
                    import uuid

                    # ── Execute trade through unified engine ────────────────
                    # execution_engine.execute_with_idempotency is now async
                    #
                    # THE MODE TRAVELS WITH THE ORDER (P0-05).
                    # This call used to omit `execution_environment`, so the parameter's
                    # `ExecutionEnvironment.LIVE` default applied and
                    # `assert_live_environment` - which exists to refuse PAPER - passed
                    # every order through, including a paper bot's. The audit trail then
                    # recorded a paper order as LIVE, and the only thing keeping it off a
                    # real exchange was the `exchange_executor=None` on the paper engine.
                    #
                    # `self.paper_trading` is a required keyword-only constructor
                    # argument precisely so this is always established, and it was not
                    # being passed on. It is now.
                    #
                    # WHAT THIS MEANS FOR A PAPER BOT. This comment used to say the order
                    # is REFUSED here, and that is NO LONGER TRUE -- it described an
                    # interim state that has since been fixed, and leaving it would tell
                    # the next reader that paper trading is broken when it is not.
                    #
                    # `execute_with_idempotency` now chooses its guard by what the engine
                    # IS rather than applying the live-path guard to everything
                    # (`core/execution_engine.py`, first statement of the method). This
                    # engine states PAPER and holds no `exchange_executor`, so it is a
                    # DECLARED SIMULATOR and is guarded by
                    # `assert_simulation_environment_matches`, which admits exactly one
                    # value -- its own. PAPER on PAPER passes; a LIVE order on this engine
                    # is still refused, which the live-path guard could not do for it.
                    # The order then reaches the paper simulation branch and produces a
                    # `paper_`-prefixed fill.
                    # Verified by execution: tests/test_simulated_orders_reach_the_simulator.py
                    # (129 tests across that file and its two companions).
                    #
                    # WHAT IS STILL OUTSTANDING, AND IT IS NOT A REFUSAL. The fill is
                    # simulated INSIDE `ExecutionEngine`, against the synthetic
                    # `{"total_equity": 10000.0}` set in `_recover_live_state`. This
                    # module has NO import edge to `backend_app/backend/paper/` at all --
                    # no `paper_simulator`, no `paper_repository` -- so the fill is never
                    # written to `paper_accounts` / `paper_orders` / `paper_fills` /
                    # `paper_positions`, which is where `PaperTrading.jsx` and the paper
                    # dashboard read from.
                    #
                    # So the platform has two paper engines sharing one word: the
                    # Paper_Session path (`POST /api/paper/sessions` ->
                    # `paper_session_service` -> `paper_simulator`, fully persisted) and
                    # this one (deploy-in-paper -> BotRunner, persisted only to
                    # execution/telemetry records). A trader who deploys a strategy in
                    # paper mode gets real simulated executions that their paper account
                    # does not show. Requirement 13.3 wants this loop's intents routed to
                    # the Paper_Simulator; the seam is this single call site plus the
                    # engine construction in `_recover_live_state`, and the missing piece
                    # is a `session_id`, which a BotRunner bot does not have.
                    # Recorded as an open item rather than attempted here.
                    # Regression: tests/test_execution_environment_is_stated_not_defaulted.py
                    result = await self.execution_engine.execute_with_idempotency(
                        tenant_id=uuid.UUID(self.user_id),
                        strategy_id=self.blueprint.get("strategy_id", "default"),
                        symbol=self.symbol,
                        side=side,
                        size=qty,
                        price=Decimal(str(price)),
                        source="bot_runner",
                        execution_environment=(
                            ExecutionEnvironment.PAPER
                            if self.paper_trading
                            else ExecutionEnvironment.LIVE
                        ),
                        # TRADE-05. The risk verdict this loop just obtained, carried to
                        # the gateway as evidence. The gateway refuses a LIVE order whose
                        # `risk_approved` is not True, so risk is now enforced AT the
                        # gateway instead of being a convention this caller happens to
                        # follow. We only reach this line when `verdict` is APPROVED --
                        # the non-approved branch `continue`s above -- so passing the
                        # comparison rather than a literal `True` keeps the evidence tied
                        # to the actual evaluation.
                        risk_approved=(verdict == RiskVerdict.APPROVED),
                        risk_reason=str(reason or "risk approved"),
                    )

                    # ── Record execution result ─────────────────────────────
                    # RT-06: see `settle_exposure` - only a `completed` result is a fill.
                    _status = result.get("status") if result else None
                    # (This block used to call `self.data_processor.record_execution(_tr)`.
                    # No such method exists on DataProcessorEngine -- or anywhere -- so every
                    # COMPLETED order raised AttributeError here, the except below "rolled back"
                    # the exposure the fill had just created, and the bot believed it held
                    # nothing after buying: it could never size a sell and would buy again.
                    # Found by tests/test_rt02_*; the exposure is settled by `settle_exposure`.)
                    filled_qty, self.recovered_exposure_coins = settle_exposure(
                        side=side,
                        qty=qty,
                        previous_exposure=previous_exposure,
                        result=result,
                    )
                    if filled_qty > 0:
                        logger.info(f"✅ Executed {side} {filled_qty} {self.symbol} @ {price} (exposure: {self.recovered_exposure_coins})")
                    else:
                        logger.warning(
                            f"⚠️ Order {side} {qty} {self.symbol} was NOT executed "
                            f"(status={_status!r}); exposure unchanged at {previous_exposure}"
                        )

                    # Record successful trace in background
                    asyncio.create_task(record_audit_signal(
                        verdict,
                        reason,
                        v_exec_id=execution_id,
                        v_status=_status if _status else "no_result",
                        v_fill=float(filled_qty),
                        v_result=result
                    ))

                    # Fire-and-forget telemetry log with error handling
                    async def safe_log():
                        try:
                            await self.telemetry.log_execution(
                                user_id=self.user_id,
                                symbol=self.symbol,
                                side=side,
                                amount=float(filled_qty),
                                price=price,
                            )
                        except Exception as e:
                            logger.error(f"Telemetry failed: {e}")

                    if filled_qty > 0:
                        asyncio.create_task(safe_log())

                except Exception as e:
                    # ── ROLLBACK: Restore previous exposure state ───────────
                    logger.error(f"🔴 Order execution failed on {self.symbol}: {e}")
                    logger.info(f"🔄 Rolling back exposure: {self.recovered_exposure_coins} -> {previous_exposure}")
                    self.recovered_exposure_coins = previous_exposure

                finally:
                    # ── Release Redis lock ────────────────────────────────
                    await redis_client.delete(lock_key)
                    logger.debug(f"🔓 Released execution lock for {execution_id}")

        # RT-01: `stream_live_ticks` RETURNS (it does not raise) when the venue offers no
        # watchTrades. Falling off the end used to be reported upstream as a clean exit: the
        # bot was gone, the deployment row still said running, and nothing was logged. A market
        # data stream that ends is a failure the reconnect/give-up logic must see.
        raise RuntimeError(
            f"market data stream for {self.symbol} on {self.exchange_id} ended unexpectedly"
        )

    async def _legacy_live_state(self, price: float):
        """The legacy engine's per-tick indicator state, or ``None`` to skip the tick."""
        matrix = self.data_processor.get_historical_matrix_for_ml(
            self.symbol, self.timeframe_sec
        )
        if len(matrix) < 30:
            return None  # not enough history for indicators

        close = matrix[:, 4].astype("float64")
        high = matrix[:, 2].astype("float64")
        low = matrix[:, 3].astype("float64")
        volume_arr = matrix[:, 5].astype("float64")

        # ── FIX ME-1: Compute ALL indicator values ─────────────────
        live_state = self._build_live_state(price, close, high, low, volume_arr)

        # ── ML inference (FIX ME-6: timeout guard) ───────────────
        if self.ml_block:
            try:
                confidence = await asyncio.wait_for(
                    asyncio.to_thread(self.ml_block.live_inference, matrix),
                    timeout=ML_INFERENCE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(f"ML inference timeout on {self.symbol}. Skipping tick.")
                return None
            live_state["ML_Prediction"] = float(confidence)
        else:
            live_state["ML_Prediction"] = 1.0  # no ML: trust technicals only
        return live_state

    # ══════════════════════════════════════════════════════════════════════
    #  FIX ME-1: BUILD COMPLETE live_state WITH ALL INDICATORS
    # ══════════════════════════════════════════════════════════════════════

    def _build_live_state(
        self,
        current_price: float,
        close: "np.ndarray",
        high: "np.ndarray",
        low: "np.ndarray",
        volume: "np.ndarray",
    ) -> dict:
        """
        Computes every indicator declared in the strategy blueprint and
        packages the latest value (last element) into live_state.

        FIX ME-1: Original live_state = {"Close": price, "ML_Prediction": ...}
        contained only 2 keys. Any blueprint condition referencing RSI_14,
        MACD, etc. would silently evaluate to False because left_val was None.
        Now ALL configured indicators are computed and their latest values
        added to live_state under their canonical keys.
        """

        state: dict = {"Close": current_price}

        # Indicator configurations from the blueprint
        indicator_configs = self.blueprint.get("indicators", [])

        for cfg in indicator_configs:
            name = cfg.get("name", "")
            params = cfg.get("params", {})
            key = cfg.get("key", name)  # key in live_state, e.g. "RSI_14"

            try:
                value = self._compute_indicator(name, params, close, high, low, volume)
                if value is not None:
                    state[key] = value
            except Exception as e:
                logger.debug(f"Indicator '{name}' computation error: {e}")

        return state

    def _compute_indicator(
        self, name: str, params: dict, close, high, low, volume
    ) -> Optional[float]:
        """
        Dispatches indicator name to the correct Numba function.
        Returns the LAST valid (non-NaN) value, or None during warmup.
        """
        import numpy as np

        def last_valid(arr):
            """Return last non-NaN element, or None if all NaN."""
            if arr is None:
                return None
            vals = arr[~np.isnan(arr)]
            return float(vals[-1]) if len(vals) > 0 else None

        name = name.lower()

        if name == "sma":
            return last_valid(sma(close, **params))
        if name == "ema":
            return last_valid(ema(close, **params))
        if name == "wma":
            return last_valid(wma(close, **params))
        if name == "hma":
            return last_valid(hma(close, **params))
        if name == "rsi":
            return last_valid(rsi(close, **params))
        if name == "atr":
            return last_valid(atr(high, low, close, **params))
        if name == "cci":
            return last_valid(cci(high, low, close, **params))
        if name == "williams_r":
            return last_valid(williams_r(high, low, close, **params))
        if name == "roc":
            return last_valid(roc(close, **params))
        if name == "momentum":
            return last_valid(momentum(close, **params))
        if name == "obv":
            return last_valid(obv(close, volume))
        if name == "mfi":
            return last_valid(mfi(high, low, close, volume, **params))
        if name == "cmf":
            return last_valid(cmf(high, low, close, volume, **params))
        if name == "rolling_vwap":
            return last_valid(rolling_vwap(high, low, close, volume, **params))
        if name == "z_score":
            return last_valid(rolling_z_score(close, **params))
        if name == "hv":
            return last_valid(historical_volatility(close, **params))
        if name == "fisher":
            return last_valid(fisher_transform(high, low, **params))
        if name == "chop":
            return last_valid(choppiness_index(high, low, close, **params))
        if name == "ao":
            return last_valid(awesome_oscillator(high, low, **params))
        if name == "trix":
            return last_valid(trix(close, **params))
        if name == "psar":
            return last_valid(psar(high, low, close, **params))

        # Multi-output indicators — return the primary line
        if name == "macd":
            ml, _, _ = macd(close, **params)
            return last_valid(ml)

        if name == "stochastic":
            k, _ = stochastic(high, low, close, **params)
            return last_valid(k)

        if name == "bb":
            _, _, upper, _, pb = bollinger_bands(close, **params)
            return last_valid(pb)  # %B as primary signal value

        if name == "adx":
            adx_arr, pdi, mdi = adx(high, low, close, **params)
            return last_valid(adx_arr)

        if name == "supertrend":
            trend, direction = supertrend(high, low, close, **params)
            return last_valid(direction)  # 1=up, -1=down

        if name == "vortex":
            plus_vi, _ = vortex_indicator(high, low, close, **params)
            return last_valid(plus_vi)

        if name == "donchian":
            hh, ll, mid = donchian_channel(high, low, **params)
            return last_valid(mid)

        if name == "keltner":
            mid, _, _ = keltner_channels(high, low, close, **params)
            return last_valid(mid)

        if name == "ichimoku":
            tenkan, kijun, _, _, _ = ichimoku_cloud(high, low, close, **params)
            return last_valid(tenkan)

        if name == "pivot":
            pp, _, _, _, _, _, _ = pivot_standard(high, low, close)
            return last_valid(pp)

        logger.warning(f"Unknown indicator name in blueprint: '{name}'")
        return None

    # ══════════════════════════════════════════════════════════════════════
    #  SHUTDOWN
    # ══════════════════════════════════════════════════════════════════════

    async def shutdown(self):
        """Cleanly closes all connections."""
        if self.ccxt_bridge:
            try:
                await self.ccxt_bridge.disconnect()
            except Exception as e:
                logger.warning(f"CCXT disconnect error: {e}")
        logger.info(f"Bot {self.user_id}/{self.symbol} safely powered down.")
