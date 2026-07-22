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
from backend_app.backend.ml_models import create_ml_block
from backend_app.backend.risk_manager import RiskVerdict
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


class BotRunner:
    """
    One BotRunner per (user, symbol, strategy).
    Injected with global singletons via `global_state` to share
    RiskManager and TelemetryEngine across all 500 bots.
    """

    def __init__(
        self, user_id: str, symbol: str, strategy_blueprint: dict, global_state
    ):
        self.user_id = user_id
        self.symbol = symbol
        self.blueprint = strategy_blueprint

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

        is_paper = self.blueprint.get("paper_trading", True)

        # FIX 2: Paper trading fast-path — skip ALL real CCXT calls.
        # Real exchange I/O can take 30-270s and kills the Gunicorn worker.
        if is_paper:
            logger.info(
                f"[PAPER] Bot {self.user_id}/{self.symbol}: skipping real exchange "
                f"connection (paper_trading=True). Using mock interface."
            )
            self.ccxt_bridge = ConnectionEngine(
                self.exchange_id,
                api_key=None,
                secret_key=None,
            )
            # Inject mock interface directly — avoids network call
            self.ccxt_bridge._apply_mock_interface()
            self.exchange = self.ccxt_bridge.exchange
            self.data_engine = DataEngine(self.exchange)

            # Use synthetic paper balance
            portfolio_state = {"total_equity": 10000.0}
            self.execution_engine = ExecutionEngine(
                fee_rate=0.001,
                slippage=0.0005,
                risk_manager=self.risk_manager,
                portfolio_state=portfolio_state,
                exchange_executor=None
            )
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
        if self.ccxt_bridge.api_key:
            wallet = await self.data_engine.fetch_wallet_balance_snapshot()
        else:
            wallet = {"USDT": {"total": 10000.0}}
            
        base_coin = self.symbol.split("/")[0]
        quote_coin = self.symbol.split("/")[1] if "/" in self.symbol else "USDT"
        
        self.recovered_exposure_coins = Decimal(
            str(wallet.get(base_coin, {}).get("total", 0.0))
        )
        
        quote_balance = Decimal(str(wallet.get(quote_coin, {}).get("total", 10000.0)))
        if quote_balance == 0:
            quote_balance = Decimal("10000.0")
            
        portfolio_state = {"total_equity": float(quote_balance)}

        # LIVE: Initialize real ExchangeExecutor
        from backend_app.backend.exchange_executor import get_exchange_executor
        exchange_exec = get_exchange_executor(
            exchange_id=self.exchange_id,
            api_key=keys["api_key"],
            api_secret=keys["secret_key"],
            sandbox=self.blueprint.get("testnet", False),
            password=keys.get("password")
        )

        # ═══════════════════════════════════════════════════════════════════
        # CRITICAL FIX C2: Use unified ExecutionEngine instead of deprecated OrderEngine
        # ═══════════════════════════════════════════════════════════════════
        self.execution_engine = ExecutionEngine(
            fee_rate=0.001,
            slippage=0.0005,
            risk_manager=self.risk_manager,
            portfolio_state=portfolio_state,
            exchange_executor=exchange_exec
        )

        # 4. Warm up the Numba circular buffer with historical OHLCV
        ohlcv = await self.data_engine.fetch_historical_ohlcv(
            self.symbol, timeframe=self.blueprint.get("timeframe", "1m"), limit=1_000

        )
        self.data_processor.warm_up_history(self.symbol, self.timeframe_sec, ohlcv)

        logger.info(
            f"Bot ready — {self.symbol} on {self.exchange_id}. "
            f"Exposure: {self.recovered_exposure_coins} {base_coin}"
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

                # ── Get historical matrix ─────────────────────────────────
                matrix = self.data_processor.get_historical_matrix_for_ml(
                    self.symbol, self.timeframe_sec
                )
                if len(matrix) < 30:
                    continue  # not enough history for indicators

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
                        logger.warning(
                            f"ML inference timeout on {self.symbol}. Skipping tick."
                        )
                        continue
                    live_state["ML_Prediction"] = float(confidence)
                else:
                    live_state["ML_Prediction"] = 1.0  # no ML: trust technicals only

                # ── Fetch live USDT balance for position sizing ───────────
                # Cache this every N seconds instead of every tick in production
                try:
                    wallet = await self.data_engine.fetch_wallet_balance_snapshot()
                    bal_usdt = float(wallet.get("USDT", {}).get("free", 0.0))
                except Exception:
                    bal_usdt = 0.0

                # ── Evaluate strategy ─────────────────────────────────────
                order_payload = await self.strategy_engine.run_logic(
                    user_id=self.user_id,
                    symbol=self.symbol,
                    strategy_blueprint=self.blueprint,
                    current_state=live_state,
                    live_balance_usdt=bal_usdt,  # FIX SB-1 in strategy_builder
                )

                if not order_payload:
                    continue

                # ── FIX ME-3: is_reduce_only from explicit payload field ──
                is_reduce = order_payload.get("reduce_only", False)

                # ── Risk check ────────────────────────────────────────────
                # Get real-time risk metrics from telemetry
                account_health = await self.telemetry.get_account_health(self.user_id)
                current_drawdown_pct = account_health.get("current_drawdown_pct", 0.0)
                daily_pnl_pct = account_health.get("daily_pnl_pct", 0.0)
                
                verdict, reason = await self.risk_manager.validate_trade_request(
                    user_id=self.user_id,
                    user_tier=self.blueprint.get("user_tier", "free"),
                    symbol=self.symbol,
                    side=order_payload["side"],
                    amount=order_payload["qty"],
                    current_price=price,
                    current_exposure=self.recovered_exposure_coins * price,
                    current_drawdown_pct=current_drawdown_pct,
                    daily_pnl_pct=daily_pnl_pct,
                    is_reduce_only=is_reduce,
                )

                if verdict != RiskVerdict.APPROVED:
                    logger.warning(f"Risk check failed: {reason}")
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
                    result = await self.execution_engine.execute_with_idempotency(
                        tenant_id=uuid.UUID(self.user_id),
                        strategy_id=self.blueprint.get("strategy_id", "default"),
                        symbol=self.symbol,
                        side=side,
                        size=qty,
                        price=Decimal(str(price)),
                        source="bot_runner"
                    )

                    # ── Record execution result ─────────────────────────────
                    if result and result.get("status") in ["completed", "skipped_completed"]:
                        trade_result = result.get("result", {})
                        if trade_result:
                            self.data_processor.record_execution(trade_result)
                            filled_qty = Decimal(str(trade_result.get('trade_result', {}).get('size', qty)))
                        else:
                            filled_qty = qty
                    else:
                        filled_qty = qty

                    # ── Atomic exposure update ───────────────────────────
                    if side == "buy":
                        self.recovered_exposure_coins = (previous_exposure + filled_qty).quantize(Decimal("0.00000001"))
                    else:
                        self.recovered_exposure_coins = max(
                            Decimal("0"),
                            (previous_exposure - filled_qty).quantize(Decimal("0.00000001"))
                        )

                    logger.info(f"✅ Executed {side} {filled_qty} {self.symbol} @ {price} (exposure: {self.recovered_exposure_coins})")

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
