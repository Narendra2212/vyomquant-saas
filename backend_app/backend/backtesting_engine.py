"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — BACKEND: backtesting_engine.py                                ║
║                                                                          ║
║  Pure computation — zero HTTP, WebSocket, or database code.              ║
║  Heavy VectorBT + ML computation runs in asyncio.to_thread().            ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  BT-1  stats.get('Profit Factor', 0) → np.isnan(0)=False → 0.0 wrong   ║
║  BT-2  freq param injected directly from user — no validation            ║
║  BT-3  No validation that price_data has a datetime index                ║
║  BT-4  No minimum data check before running VectorBT                     ║
║  BT-5  Equity curve column rename assumes exactly 2 columns              ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
import random

import numpy as np
import pandas as pd

logger = logging.getLogger("BacktestEngine")

# ── Valid VectorBT frequency strings ─────────────────────────────────────
# FIX BT-2: Whitelist prevents user from injecting arbitrary freq strings
VALID_FREQ_MAP = {
    "1m": "1T",
    "3m": "3T",
    "5m": "5T",
    "15m": "15T",
    "30m": "30T",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6H",
    "8h": "8H",
    "12h": "12H",
    "1d": "1D",
    "3d": "3D",
    "1w": "1W",
}


def _safe_stat(stats, key: str, default=None):
    """
    FIX BT-1: Safe stat extraction that correctly handles NaN vs missing key.
    Returns None (not 0) when stat is genuinely unavailable.
    """
    val = stats.get(key)
    if val is None:
        return default
    try:
        f = float(val)
        return None if np.isnan(f) or np.isinf(f) else round(f, 4)
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════════
#  THE PAYLOAD'S DECLARED KEY SET  (task 7.1, Requirements 2.7, 2.8)
#
#  Every key `run_backtest_async` puts on its `results` dict, on EITHER engine
#  path, hand-transcribed from the two `results = {...}` literals and the
#  `results["<key>"] = ...` statements that follow them:
#
#    primary (vectorbt) path   - the dict literal at :503-523, plus the three
#                                 keys added after it: :565 (`trades`),
#                                 :601-602 (`winning_trades`, `losing_trades`),
#                                 :632 (`equity_curve`)
#    fallback (no-vectorbt)     - the dict literal at :242-260, plus the two
#    path                        keys added after it: :262 (`equity_curve`),
#                                 :263 (`trades`)
#
#  Both literals were re-read line by line against a live run of each path
#  (see `tests/test_backtest_key_contract.py`'s `engine_run` fixture) rather
#  than guessed, and the two paths turn out to emit the IDENTICAL 16 keys -
#  task 7.6 gave the fallback path's `winning_trades`/`losing_trades` split a
#  matching producer on the primary path too, so there is no longer a
#  path-dependent gap here to declare separately. One frozenset covers both.
#
#  This is deliberately NOT derived by calling `.keys()` on a dict built at
#  import time: building one would mean re-running the compute path (or
#  faking one), which is a second implementation of the thing being
#  declared. A hand-transcription is instead kept honest by
#  `tests/test_backtest_key_contract.py`'s per-path `engine_run` fixture,
#  which asserts a REAL run's key set equals this frozenset exactly (not a
#  subset) - so a key added to, or removed from, either literal without a
#  matching edit here fails that test immediately.
BACKTEST_PAYLOAD_EMITTED_KEYS = frozenset(
    {
        "total_return_pct",
        "final_equity",
        "total_pnl",
        "win_rate_pct",
        "max_drawdown_pct",
        "total_trades",
        "winning_trades",
        "losing_trades",
        "profit_factor",
        "sharpe_ratio",
        "sortino_ratio",
        "calmar_ratio",
        "total_fees_paid",
        "expectancy",
        "equity_curve",
        "trades",
    }
)


class BacktestEngineUnavailable(RuntimeError):
    """The simulation could not be performed, so no result is returned.

    Distinct from ``ValueError``, which this engine already uses for inputs it refuses (too
    few bars, zero signals, an unrecognised timeframe) and which ``strategy_operations``
    maps onto 422 ``BACKTEST_NOT_RUNNABLE``. This one is not the caller's fault: the
    environment cannot run a backtest at all, so it is a ``RuntimeError`` and should surface
    as a 5xx rather than being reported to a user as a bad request.
    """


class BacktestEngine:

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        fees: float = 0.001,
        slippage: float = 0.0005,
        spread: float = 0.0002,
        min_trade_size: float = 10.0,
    ):
        self.initial_capital = initial_capital
        self.fees = fees
        self.slippage = slippage
        self.spread = spread
        self.min_trade_size = min_trade_size
        logger.info(f"[BACKTEST] Initialized with init_cash={initial_capital:.2f}, fees={fees:.4f} ({fees*100:.2f}%), slippage={slippage:.4f} ({slippage*100:.2f}%), spread={spread:.4f} ({spread*100:.2f}%), min_trade_size={min_trade_size:.2f}")
    
    def _apply_realistic_slippage(self, price: float) -> float:
        """
        Apply random slippage to execution price.
        Slippage: price *= random.uniform(0.999, 1.001)
        """
        slippage_factor = random.uniform(0.999, 1.001)
        executed_price = price * slippage_factor
        logger.info(f"📉 Slippage applied: {price:.4f} → {executed_price:.4f} ({(slippage_factor-1)*100:.4f}%)")
        return executed_price
    
    def _apply_spread(self, price: float, side: str) -> float:
        """
        Apply spread to price (buy price higher, sell price lower).
        """
        if side == "buy":
            adjusted_price = price * (1 + self.spread)
        else:  # sell
            adjusted_price = price * (1 - self.spread)
        logger.info(f"📊 Spread applied ({side}): {price:.4f} → {adjusted_price:.4f}")
        return adjusted_price
    
    def _calculate_fee(self, trade_value: float) -> float:
        """
        Calculate fee based on trade value.
        Fee = 0.001 * trade_value (0.1%)
        """
        fee = trade_value * self.fees
        logger.info(f"💸 Fee applied: {fee:.4f} ({self.fees*100:.2f}% of {trade_value:.4f})")
        return fee
    
    def _validate_trade_size(self, size: float) -> bool:
        """
        Validate minimum trade size.
        """
        if size < self.min_trade_size:
            logger.warning(f"⚠️ Trade size {size:.4f} below minimum {self.min_trade_size:.4f}")
            return False
        return True

    # ══════════════════════════════════════════════════════════════════════
    #  PUBLIC ASYNC ENTRY POINT
    #  Wraps the heavy synchronous VectorBT call in a thread so the
    #  FastAPI event loop is never blocked. (FIX ML-4 pattern applied here)
    # ══════════════════════════════════════════════════════════════════════

    async def run_backtest_async(
        self,
        ohlcv_list: list,
        feature_matrix: np.ndarray,
        model_path: str,
        tech_long_condition: np.ndarray,
        tech_short_condition: np.ndarray,
        params: dict,
    ) -> tuple[dict, list, list]:
        """
        Async wrapper. Converts raw CCXT OHLCV list to a proper pandas
        Series with a DatetimeIndex, then dispatches to thread pool.

        ohlcv_list: [[timestamp_ms, open, high, low, close, volume], ...]
        Returns:    (stats_dict, equity_curve_list, trades_list)
        """
        # ── Build DatetimeIndex price series (FIX BT-3) ──────────────────
        df = pd.DataFrame(
            ohlcv_list, columns=["ts", "open", "high", "low", "close", "volume"]
        )
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df.set_index("ts").sort_index()
        price_data = df["close"].astype(float)

        # ── Validate freq param (FIX BT-2) ───────────────────────────────
        raw_tf = str(params.get("timeframe", "5m")).lower()
        vbt_freq = VALID_FREQ_MAP.get(raw_tf)
        if vbt_freq is None:
            raise ValueError(
                f"Invalid timeframe '{raw_tf}'. "
                f"Must be one of: {list(VALID_FREQ_MAP.keys())}"
            )
        params = {**params, "_vbt_freq": vbt_freq}  # inject validated freq

        # ── Dispatch to thread pool ───────────────────────────────────────
        return await asyncio.to_thread(
            self._compute_vectorbt_sync,
            price_data,
            feature_matrix,
            model_path,
            tech_long_condition,
            tech_short_condition,
            params,
        )

    # ══════════════════════════════════════════════════════════════════════
    #  SYNCHRONOUS HEAVY LIFTER — runs in thread, not event loop
    # ══════════════════════════════════════════════════════════════════════

    def _compute_vectorbt_sync(
        self,
        price_data: pd.Series,
        feature_matrix: np.ndarray,
        model_path: str,
        tech_long: np.ndarray,
        tech_short: np.ndarray,
        params: dict,
    ) -> tuple[dict, list, list]:
        try:
            import vectorbt as vbt
            has_vbt = True
        except (ImportError, ModuleNotFoundError):
            has_vbt = False

        # ── FIX BT-4: Minimum data guard ────────────────────────────────
        if len(price_data) < 50:
            raise ValueError(
                f"Backtest requires at least 50 bars. Got {len(price_data)}. "
                "Fetch more historical data."
            )

        if not has_vbt:
            # ── REFUSE. The fallback that used to live here FABRICATED a result. ──
            #
            # It returned a complete payload with the same keys and types as a real one, so
            # nothing downstream could tell them apart — and `backtest_runtime` persists
            # whatever it receives into `strategy_backtests`, whose `backtest_*` columns
            # `routers/library.py`'s publish path copies onto a `library_strategies` row,
            # which `listing_projection` then serves as a listing's PUBLIC
            # `performance_summary` and `risk_metrics`. So the invented figures did not stay
            # in the backtest; they were publishable as a strategy's measured track record.
            #
            # What it invented:
            #   profit_factor      the literal 1.5, derived from nothing
            #   sortino_ratio      sharpe * 1.1 — Sortino is not a multiple of Sharpe
            #   total_fees_paid    trades_count * fee_rate * initial_capital, an estimate
            #                      in the field the real path fills from portfolio.stats()
            #   trades             [], so winning/losing counts were back-derived from a
            #                      win rate rather than from per-trade net P&L
            #   win_rate_pct       winning BARS / trade COUNT. On a 120-bar series with one
            #                      trade and 70 up-bars this reported 7000.0 percent.
            # And it applied no stop-loss, no take-profit, no slippage and no spread, while
            # the real path passes sl_stop/tp_stop/slippage to vbt.Portfolio.from_signals.
            #
            # Refusing is safe: `vectorbt==0.26.2` is pinned in requirements-base.txt, so
            # this branch is unreachable in any correctly provisioned environment. One where
            # it IS reachable has broken dependencies — a case where an error is the useful
            # answer and an invented track record is the harmful one.
            # Regression: tests/test_backtest_engine_refuses_without_vectorbt.py
            raise BacktestEngineUnavailable(
                "vectorbt is not importable, so this backtest cannot be simulated. No "
                "result is returned: the previous fallback produced a plausible but "
                "fabricated one (a literal profit factor, a Sortino derived as Sharpe x "
                "1.1, an estimated fee total, a win rate computed over bars rather than "
                "trades, and no stop-loss, take-profit, slippage or spread), and those "
                "figures are persisted to strategy_backtests and republished as a "
                "marketplace listing's performance. Install the pinned dependency "
                "(vectorbt==0.26.2, requirements-base.txt) and re-run."
            )

        # ── Align arrays to price_data length ────────────────────────────
        n = len(price_data)
        
        # Ensure price_data is pandas Series with proper index
        if not isinstance(price_data, pd.Series):
            price_data = pd.Series(price_data)
        
        # Ensure signals are pandas Series with same index as price_data
        tech_long = pd.Series(tech_long, index=price_data.index)
        tech_short = pd.Series(tech_short, index=price_data.index)
        
        # Align to price_data length
        tech_long = tech_long.iloc[-n:] if len(tech_long) >= n else tech_long
        tech_short = tech_short.iloc[-n:] if len(tech_short) >= n else tech_short
        
        # Force index alignment
        tech_long = tech_long.reindex(price_data.index).fillna(False)
        tech_short = tech_short.reindex(price_data.index).fillna(False)
        
        # Ensure boolean type
        tech_long = tech_long.astype(bool)
        tech_short = tech_short.astype(bool)
        
        # ── POSITION-AWARE ENTRY/EXIT SELECTION ───────────────────────────
        # Act on an entry only when flat, and on an exit only when long. That was always
        # the stated intent -- the two comments inside the loop below are the originals --
        # but it was expressed as
        #
        #     position   = entries_raw.cumsum() - exits_raw.cumsum()
        #     tech_long  = entries_raw & (position == 0)
        #     tech_short = exits_raw   & (position > 0)
        #
        # and that is wrong twice over.
        #
        # FIRST, `cumsum` at bar i includes bar i. On the bar an entry fired the entry had
        # already been counted, so `position >= 1` and `position == 0` was false: EVERY
        # ENTRY WAS DISCARDED. On the bar an exit fired against one open position the exit
        # had been counted too, giving `position == 0`, so `position > 0` was false and
        # every exit was discarded as well. Nothing reached
        # `vbt.Portfolio.from_signals`, `np.all(np.isnan(sizes))` held, and the engine
        # raised "Strategy generated 0 signals" -- which `execute_backtest` answers as
        # 422 BACKTEST_NOT_RUNNABLE. The canonical backtest route therefore could not
        # return a result for any strategy that opens with an entry, which is every
        # ordinary long strategy. Measured over 240 bars with a long every 8th bar and a
        # short on the next: 30 entries and 30 exits in, 0 and 0 out.
        #
        # The one shape that got through was a series emitting an exit BEFORE its first
        # entry, because the leading exit made the two cumsums equal at the first entry
        # bar. `tests/test_backtest_key_contract.py::_conditions` is built that way and
        # its docstring records the ordering as "mandatory, not stylistic" -- it was
        # working around this line.
        #
        # SECOND, and the reason shifting the cumsum by one bar is not the fix either: the
        # counter is built from the RAW signals, so a signal that gets discarded still
        # moves it. A leading exit arriving while flat is dropped, yet still decrements the
        # running total, biasing it to -1 for the rest of the series so that `position == 0`
        # never holds again. Whether a signal is acted on depends on the position, and the
        # position depends on which signals were acted on; a cumulative sum over the raw
        # inputs cannot express that. It is sequential by nature, so it is written
        # sequentially here. At most 10,000 bars reach this point (`backtest_runtime`'s
        # fetch limit), which is microseconds next to the simulation that follows.
        # Regression: tests/test_backtest_signal_path_integrity.py
        entries_raw = tech_long.copy()
        exits_raw = tech_short.copy()

        entry_flags = entries_raw.to_numpy(dtype=bool, copy=True)
        exit_flags = exits_raw.to_numpy(dtype=bool, copy=True)
        kept_entries = np.zeros(n, dtype=bool)
        kept_exits = np.zeros(n, dtype=bool)
        holding = False
        for bar in range(n):
            # Valid entries: only when no position open
            if entry_flags[bar] and not holding:
                kept_entries[bar] = True
                holding = True
            # Valid exits: only when position is open
            #
            # `elif`, so a bar carrying BOTH an entry and an exit resolves to whichever
            # one the current position admits -- the entry when flat, the exit when long --
            # rather than to both. The DAG path cannot produce such a bar, because
            # `backtest_runtime` derives `entries = signals > 0` and `exits = signals < 0`
            # from one series, but a direct caller of this engine can, and silently
            # counting it as an open and a close on the same bar would be worse than
            # picking one and saying so.
            elif exit_flags[bar] and holding:
                kept_exits[bar] = True
                holding = False

        tech_long = pd.Series(kept_entries, index=entries_raw.index)
        tech_short = pd.Series(kept_exits, index=exits_raw.index)

        logger.info(
            "[BACKTEST] Position filter: entries %d -> %d, exits %d -> %d (%s at series end)",
            int(entry_flags.sum()),
            int(kept_entries.sum()),
            int(exit_flags.sum()),
            int(kept_exits.sum()),
            "holding" if holding else "flat",
        )
        
        logger.info(f"[DEBUG] price_data index: {price_data.index[:5].tolist()}")
        logger.info(f"[DEBUG] tech_long index: {tech_long.index[:5].tolist()}")
        logger.info(f"[DEBUG] tech_short index: {tech_short.index[:5].tolist()}")
        logger.info(f"[DEBUG] Raw entry signals: {entries_raw.sum()}")
        logger.info(f"[DEBUG] Position-aware entry signals: {tech_long.sum()}")
        logger.info(f"[DEBUG] Raw exit signals: {exits_raw.sum()}")
        logger.info(f"[DEBUG] Position-aware exit signals: {tech_short.sum()}")

        # ── ML Predictions ────────────────────────────────────────────────
        # `has_model` is carried rather than inferred from the array below, because the
        # absence of a model used to be encoded as `np.ones(n)` and that sentinel was
        # indistinguishable from a model that predicted 1.0 everywhere. The gate needs to
        # know which it is; see the next block.
        has_model = bool(
            model_path and str(model_path).strip() and os.path.exists(model_path)
        )
        if has_model:
            ml_predictions = self._generate_ml_predictions(
                model_path, feature_matrix[-n:]
            )
        else:
            ml_predictions = None

        # ── Position Sizing ───────────────────────────────────────────────
        sizes = np.full(n, np.nan, dtype=float)  # NaN = hold
        trade_size = params.get("trade_size_pct", 0.10)

        # ── THE ML GATE IS NOT IN FORCE WITHOUT A MODEL ───────────────────
        # It used to be applied unconditionally against `ml_predictions = np.ones(n)`,
        # the stand-in for "no ML opinion". The long leg passed (1.0 >= 0.80) but the
        # short leg read
        #
        #     short_mask = tech_short & (ml_predictions <= (1.0 - ml_threshold))
        #
        # which with predictions of 1.0 needs `ml_threshold <= 0`, and the threshold was
        # clipped to [0.5, 0.99]. So EVERY EXIT SIGNAL THE STRATEGY PRODUCED WAS
        # DISCARDED, at every permitted threshold, on every backtest -- `backtest_runtime`
        # passes `model_path=""` on its only call, so no backtest has ever had a model.
        # Positions could then only be closed by the stop-loss or the take-profit, which
        # `backtest_runtime` also hardcodes. A 240-bar run with 30 entry and 30 exit
        # signals reported 16 trades and +4.41%, and not one of those trades was closed by
        # the strategy's own sell logic.
        #
        # Worse than a wrong number: the payload is persisted to `strategy_backtests`, and
        # `routers/library.py`'s publish path copies those columns onto `library_strategies`
        # for `listing_projection` to serve as a listing's public track record. So the
        # figures for a strategy whose exits were thrown away were publishable as measured
        # performance.
        #
        # A sentinel for "no information" must not veto anything. With no model the
        # technical conditions stand on their own, which is what a backtest of a
        # non-ML strategy means. Nothing about the gate changes when a model IS loaded.
        # Regression: tests/test_backtest_signal_path_integrity.py
        if has_model:
            ml_threshold = float(np.clip(params.get("ml_threshold", 0.80), 0.5, 0.99))
            long_mask = np.asarray(tech_long & (ml_predictions >= ml_threshold), dtype=bool)
            short_mask = np.asarray(
                tech_short & (ml_predictions <= (1.0 - ml_threshold)), dtype=bool
            )
        else:
            long_mask = np.asarray(tech_long, dtype=bool)
            short_mask = np.asarray(tech_short, dtype=bool)

        logger.info(
            "[BACKTEST] ML gate %s: entries %d -> %d, exits %d -> %d",
            "applied" if has_model else "NOT APPLIED (no model supplied)",
            int(np.asarray(tech_long, dtype=bool).sum()),
            int(long_mask.sum()),
            int(np.asarray(tech_short, dtype=bool).sum()),
            int(short_mask.sum()),
        )

        sizes = np.where(long_mask, trade_size, sizes)
        sizes = np.where(short_mask, -trade_size, sizes)

        if np.all(np.isnan(sizes)):
            # The old message advised "try lowering the ML threshold", which was
            # unreachable advice on the path that produced this error: with no model no
            # threshold admits an exit, and the threshold is not consulted at all now.
            # Naming the counts instead, so the reader can see whether the strategy
            # produced nothing or whether something downstream removed it.
            raise ValueError(
                "Strategy generated 0 signals. The strategy produced "
                f"{int(np.asarray(tech_long, dtype=bool).sum())} entry and "
                f"{int(np.asarray(tech_short, dtype=bool).sum())} exit signals that were "
                "open/close-consistent"
                + (
                    f", and the ML gate at threshold {ml_threshold} admitted none of them."
                    if has_model
                    else " -- relax the technical conditions or widen the date range."
                )
            )

        # ── VectorBT Execution ────────────────────────────────────────────
        vbt_freq = params.get("_vbt_freq", "5T")  # validated by caller
        trade_size_pct = params.get("trade_size_pct", 0.10)
        
        # Fixed capital configuration
        initial_equity = self.initial_capital
        position_fraction = trade_size_pct  # Fraction of capital per trade (e.g., 10%)
        
        logger.info(f"[BACKTEST] Starting VectorBT execution with {len(price_data)} bars")
        logger.info(f"[BACKTEST] Initial Equity: ${initial_equity:.2f}")
        logger.info(f"[BACKTEST] Position Size: {position_fraction:.2%} of equity per trade")
        logger.info(f"[BACKTEST] Fee rate: {self.fees:.4f} ({self.fees*100:.2f}%)")
        logger.info(f"[BACKTEST] Slippage rate: {self.slippage:.4f} ({self.slippage*100:.2f}%)")
        logger.info(f"[BACKTEST] Spread: {self.spread:.4f} ({self.spread*100:.2f}%)")
        logger.info(f"[BACKTEST] Minimum trade size: ${self.min_trade_size:.2f}")
        
        # Log realistic execution parameters
        # These lines describe what is ACTUALLY passed to vbt.Portfolio.from_signals below.
        #
        # They used to claim two cost models that were not in force. "Slippage applied:
        # random.uniform(0.999, 1.001)" named `_apply_realistic_slippage`, which is defined
        # and never called — VectorBT applies the deterministic `slippage=` argument
        # instead. And "Spread applied: ..." named `_apply_spread`, likewise never called:
        # `self.spread` reaches no simulation at all, so no spread is modelled. A log line
        # asserting a cost model the run does not use is the same fabrication as an invented
        # metric, in a different output stream.
        # Regression: tests/test_backtest_engine_refuses_without_vectorbt.py
        print(f"💰 Equity: ${initial_equity:.2f}")
        print(f"💸 Fee applied: {self.fees:.4f} ({self.fees*100:.2f}%)")
        print(f"📉 Slippage applied by VectorBT: {self.slippage:.4f} ({self.slippage*100:.2f}%)")
        print("📊 Spread: NOT modelled on this path (self.spread reaches no simulation)")
        
        # Convert sizes to entry/exit signals for from_signals
        entries = sizes > 0
        exits = sizes < 0
        
        logger.info(f"[DEBUG] Total entry signals (sizes > 0): {entries.sum()}")
        logger.info(f"[DEBUG] Total exit signals (sizes < 0): {exits.sum()}")
        logger.info(f"[DEBUG] sizes array sample: {sizes[:10]}")
        
        sl_stop = params.get("stop_loss_pct", 0.05)
        tp_stop = params.get("take_profit_pct", 0.15)
        
        logger.info(f"[DEBUG] Stop Loss: {sl_stop * 100:.1f}%")
        logger.info(f"[DEBUG] Take Profit: {tp_stop * 100:.1f}%")
        
        logger.info("[VALIDATION] Running portfolio with costs...")
        portfolio = vbt.Portfolio.from_signals(
            close=price_data,
            entries=entries,
            exits=exits,
            sl_stop=sl_stop,
            tp_stop=tp_stop,
            fees=self.fees,
            slippage=self.slippage,
            init_cash=self.initial_capital,
            size=trade_size_pct,
            size_type="percent",
            freq=vbt_freq,
        )
        
        # ── VALIDATION: Extract metrics and validate fees impact ─────────
        trades_count = portfolio.trades.count()
        
        # Statistical significance validation
        if trades_count < 20:
            logger.warning(f"WARNING: Not enough trades for statistical significance (got {trades_count}, need >= 20)")
        
        # Extract total fees from stats API (correct VectorBT method)
        stats = portfolio.stats()
        total_fees = stats.get("Total Fees Paid", 0.0)
        
        # Safe fallback if key not present
        if total_fees is None:
            total_fees = 0.0
        
        # Get equity curve from portfolio
        equity_curve = portfolio.value()
        if len(equity_curve) > 0:
            final_equity = equity_curve.iloc[-1]
            initial_equity_logged = equity_curve.iloc[0]
        else:
            final_equity = self.initial_capital
            initial_equity_logged = self.initial_capital
        
        # Log equity progression
        print(f"💰 Initial Equity: ${initial_equity_logged:.2f}")
        print(f"💰 Final Equity: ${final_equity:.2f}")
        print(f"💰 Equity Change: ${final_equity - initial_equity_logged:.2f} ({((final_equity / initial_equity_logged) - 1) * 100:.2f}%)")
        
        # Verify capital logic
        total_pnl = final_equity - initial_equity_logged
        logger.info(f"[CAPITAL] Initial Equity: ${initial_equity_logged:.2f}")
        logger.info(f"[CAPITAL] Final Equity: ${final_equity:.2f}")
        logger.info(f"[CAPITAL] Total PnL: ${total_pnl:.2f}")
        logger.info(f"[CAPITAL] Total Fees: ${total_fees:.2f}")
        logger.info(f"[CAPITAL] Net Return: {((final_equity / initial_equity_logged) - 1) * 100:.2f}%")
        
        # ── PROFESSIONAL METRICS CALCULATION ───────────────────────────
        
        # Extract returns series
        returns = portfolio.returns()
        
        # Sharpe Ratio (annualized, assuming 252 trading days)
        # Normalize to 0 if not enough trades for statistical significance
        sharpe = 0.0
        if trades_count >= 20 and returns.std() != 0 and not pd.isna(returns.std()):
            sharpe = (returns.mean() / returns.std()) * (252 ** 0.5)
        
        # Max Drawdown from stats
        max_dd = stats.get("Max Drawdown [%]", 0.0)
        if max_dd is None:
            max_dd = 0.0
        
        # Profit Factor from trades
        trades = portfolio.trades
        if trades_count > 0:
            pnl = trades.pnl.values  # Convert to numpy array to avoid MappedArray indexing error
            logger.info(f"[DEBUG] pnl array: {pnl}")
            logger.info(f"[DEBUG] pnl mean: {pnl.mean():.4f}")
            logger.info(f"[DEBUG] pnl std: {pnl.std():.4f}")
            
            gross_profit = pnl[pnl > 0].sum()
            gross_loss = abs(pnl[pnl < 0].sum())
            
            # Fix edge case: if no losing trades, profit factor is infinity
            if gross_loss == 0:
                profit_factor = 9999.0  # UI-safe representation of infinity
            else:
                profit_factor = gross_profit / gross_loss
            
            logger.info(f"[DEBUG] Gross profit: {gross_profit:.4f}")
            logger.info(f"[DEBUG] Gross loss: {gross_loss:.4f}")
            logger.info(f"[DEBUG] Profit factor: {profit_factor:.4f}")
        else:
            profit_factor = 0.0
        
        # Expectancy calculation
        if trades_count > 0:
            win_rate = trades.win_rate()
            pnl = trades.pnl.values  # Convert to numpy array
            wins = pnl[pnl > 0]
            losses = pnl[pnl < 0]
            
            avg_win = wins.mean() if len(wins) > 0 else 0.0
            avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
            
            expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        else:
            win_rate = 0.0
            expectancy = 0.0
        
        logger.info(f"[DEBUG] Stats keys: {list(stats.keys())[:10]}")
        logger.info(f"[DEBUG] Initial Cash: ${self.initial_capital:.2f}")
        logger.info(f"[DEBUG] Final Equity: ${final_equity:.2f}")
        logger.info(f"[DEBUG] Total Fees from stats: ${total_fees:.4f}")
        logger.info(f"[DEBUG] Trades Count: {trades_count}")
        logger.info(f"[DEBUG] Sharpe Ratio: {sharpe:.4f}")
        logger.info(f"[DEBUG] Max Drawdown: {max_dd:.2f}%")
        logger.info(f"[DEBUG] Profit Factor: {profit_factor:.4f}")
        logger.info(f"[DEBUG] Expectancy: ${expectancy:.4f}")
        
        if total_fees == 0.0 and trades_count > 0:
            logger.warning("Fees not reflected in stats - may need manual calculation")
        
        logger.info(f"[BACKTEST] Total fees accumulated: ${total_fees:.4f}")
        
        # ── FIX BT-1: Safe stat extraction ───────────────────────────────
        results = {
            "total_return_pct": _safe_stat(stats, "Total Return [%]", 0.0),
            "final_equity": round(float(final_equity), 4),
            # ``total_pnl`` was computed at :405 and spent entirely on the ``[CAPITAL]`` log
            # line below it, so the Net P&L field on the Backtester results screen had no
            # producer on any code path - not this dict, not ``performance_metrics``, not the
            # literals ``backtest_runtime`` adds. Requirement 2.10 accepts either emitting it
            # or removing the field; emitting is the smaller change, because the value already
            # exists and is already correct. It is declared here, beside the ``final_equity``
            # it is derived from, so the two cannot disagree.
            "total_pnl": round(float(total_pnl), 4),
            "win_rate_pct": round(float(win_rate * 100), 4) if not pd.isna(win_rate) else 0.0,
            "max_drawdown_pct": _safe_stat(stats, "Max Drawdown [%]", 0.0),
            "total_trades": int(stats.get("Total Trades", 0) or 0),
            "profit_factor": round(float(profit_factor), 4) if not pd.isna(profit_factor) else 0.0,
            "sharpe_ratio": round(float(sharpe), 4) if not pd.isna(sharpe) else 0.0,
            "sortino_ratio": _safe_stat(stats, "Sortino Ratio", None),
            "calmar_ratio": _safe_stat(stats, "Calmar Ratio", None),
            "total_fees_paid": round(float(total_fees), 4),
            "expectancy": round(float(expectancy), 4) if not pd.isna(expectancy) else 0.0,
        }

        # ── FIX BT-5: Robust equity curve extraction ──────────────────────
        equity_series = portfolio.value()
        eq_df = equity_series.reset_index()
        # Rename whatever the two columns are to standard names
        eq_df.columns = ["timestamp", "equity"]
        
        # ── EXTENDED TRADE DETAIL EXTRACTION ───────────────────────────
        # Extract detailed trade-by-trade data from VectorBT portfolio
        trades_list = []
        if trades_count > 0:
            # ``portfolio.trades`` is a VectorBT ``ExitTrades`` RECORD ACCESSOR, not a
            # DataFrame: it has no ``iterrows``, so this block raised
            # ``AttributeError: 'ExitTrades' object has no attribute 'iterrows'`` on every
            # backtest that produced at least one trade - which made
            # ``BacktestRuntime.run_backtest`` answer 500 for every strategy that traded and
            # complete only for ones that did not. ``records_readable`` is the DataFrame
            # this loop was written against: every column name the body reaches for
            # ('Entry Timestamp', 'Exit Timestamp', 'Size', 'PnL', ...) is one of its
            # columns. Found by the Requirement 26.4 end-to-end sandbox suite
            # (``tests/sandbox_lifecycle/``), which is the first test to run a real
            # simulation through this method end to end.
            readable = portfolio.trades.records_readable
            # VectorBT reports the two prices as averages and splits fees across the two
            # legs. Aliased rather than left to the ``else`` branches below, which would
            # have recorded 0.0 - a fabricated figure, which the specification's
            # non-functional constraints forbid outright.
            if "Entry Price" not in readable.columns and "Avg Entry Price" in readable.columns:
                readable = readable.assign(**{"Entry Price": readable["Avg Entry Price"]})
            if "Exit Price" not in readable.columns and "Avg Exit Price" in readable.columns:
                readable = readable.assign(**{"Exit Price": readable["Avg Exit Price"]})
            if "Fees" not in readable.columns and {"Entry Fees", "Exit Fees"} <= set(
                readable.columns
            ):
                readable = readable.assign(
                    **{"Fees": readable["Entry Fees"] + readable["Exit Fees"]}
                )
            for idx, trade in readable.iterrows():
                try:
                    # Extract trade details from VectorBT trade object
                    trade_detail = {
                        "trade_id": int(idx),
                        "entry_time": str(trade['Entry Timestamp']) if 'Entry Timestamp' in trade else str(trade.index[idx]),
                        "exit_time": str(trade['Exit Timestamp']) if 'Exit Timestamp' in trade else None,
                        "entry_price": float(trade['Entry Price']) if 'Entry Price' in trade else float(trade.get('entry_price', 0)),
                        "exit_price": float(trade['Exit Price']) if 'Exit Price' in trade else float(trade.get('exit_price', 0)),
                        "side": "BUY" if trade.get('Size', 0) > 0 else "SELL",
                        "quantity": float(abs(trade.get('Size', 0))),
                        "gross_pnl": float(trade['PnL']) if 'PnL' in trade else float(trade.get('pnl', 0)),
                        "fees": float(trade['Fees']) if 'Fees' in trade else float(trade.get('fees', 0)),
                        "net_pnl": float(trade['PnL'] - trade.get('Fees', 0)) if 'PnL' in trade else float(trade.get('pnl', 0)),
                        "return_pct": float((trade['PnL'] / trade['Entry Price']) * 100) if 'PnL' in trade and 'Entry Price' in trade else 0.0,
                        "duration": 0,  # Will be calculated if timestamps available
                        "status": "CLOSED"
                    }
                    
                    # Calculate duration if timestamps available
                    if 'Entry Timestamp' in trade and 'Exit Timestamp' in trade:
                        try:
                            entry_ts = pd.to_datetime(trade['Entry Timestamp'])
                            exit_ts = pd.to_datetime(trade['Exit Timestamp'])
                            duration_seconds = (exit_ts - entry_ts).total_seconds()
                            trade_detail["duration"] = duration_seconds
                        except:
                            pass
                    
                    trades_list.append(trade_detail)
                except Exception as e:
                    logger.warning(f"Failed to extract trade {idx} details: {e}")
        
        logger.info(f"[BACKTEST] Extracted {len(trades_list)} detailed trade records")
        eq_df["timestamp"] = eq_df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        eq_df["equity"] = eq_df["equity"].round(4)
        
        # Add detailed trades to results
        results["trades"] = trades_list

        # ── The win/loss split, derived from the per-trade NET P&L ─────────
        # ``winning_trades`` and ``losing_trades`` had NO producer anywhere - not this dict,
        # not ``backtest_runtime``, not the fallback branch above - so
        # ``update_backtest_results``'s ``results.get("winning_trades", 0)`` default won on
        # every run and ``strategy_backtests`` carried ``0`` and ``0`` next to a real
        # ``total_trades``. They are not renames: repointing a key cannot fix a column
        # nothing computes.
        #
        # Derived here rather than in the runtime because this is the layer that owns the
        # trade records, and derived from ``net_pnl`` rather than the gross ``PnL``: a trade
        # whose fees exceed its gross profit is a loss, and counting it as a win is how a
        # fee-heavy strategy comes to read as profitable. The split therefore agrees with
        # the ``trades`` rows a trader can expand and check.
        trade_net_pnls = [
            float(trade["net_pnl"])
            for trade in trades_list
            if trade.get("net_pnl") is not None
        ]
        results["winning_trades"] = sum(1 for pnl in trade_net_pnls if pnl > 0)
        results["losing_trades"] = sum(1 for pnl in trade_net_pnls if pnl < 0)

        # ── The curve travels IN BAND, on both engine paths ────────────────
        # This path used to return the curve as the second tuple element only, while the
        # fallback at :243 also set it as a payload key. So the curve's fate was
        # path-dependent: a deployment without VectorBT persisted a curve and a deployment
        # with it did not, which is the more damaging half and the only half that reproduces
        # in production. A value carried out of band has to be re-inserted by hand at every
        # hop, and ``backtest_runtime.run_backtest`` forgot - it unpacked the curve, read it
        # twice, and assembled its payload without it. Setting the key here removes the drop
        # site rather than patching it. The tuple return is unchanged, so every existing
        # caller that unpacks two values keeps working (Requirement 3.4).
        equity_curve = eq_df.to_dict(orient="records")
        results["equity_curve"] = equity_curve

        return results, equity_curve

    def _generate_ml_predictions(
        self, model_path: str, feature_matrix: np.ndarray
    ) -> np.ndarray:
        """Batch-predict the entire historical matrix for backtesting."""
        import joblib

        model = joblib.load(model_path)
        if hasattr(model, "predict_proba"):
            return model.predict_proba(feature_matrix)[:, 1]
        return model.predict(feature_matrix).astype(float)
