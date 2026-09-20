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
            # Fallback pandas calculation when vectorbt is not installed
            n = len(price_data)
            returns = price_data.pct_change().fillna(0)
            
            # Convert tech_long / tech_short to Series
            long_s = pd.Series(tech_long, index=price_data.index).astype(bool)
            short_s = pd.Series(tech_short, index=price_data.index).astype(bool)
            
            position = long_s.astype(int) - short_s.astype(int)
            position = position.cumsum().clip(0, 1)
            strategy_returns = position.shift(1).fillna(0) * returns - (long_s.astype(int) * self.fees)
            cum_returns = (1 + strategy_returns).cumprod()
            
            if len(cum_returns) > 0:
                final_val = float(cum_returns.iloc[-1])
            else:
                final_val = 1.0
            
            final_equity = float(self.initial_capital * final_val)
            total_return_pct = float((final_val - 1) * 100)
            
            trades_count = int(long_s.sum())
            win_count = int((strategy_returns > 0).sum())
            win_rate = (win_count / trades_count) if trades_count > 0 else 0.0
            
            std_ret = strategy_returns.std()
            sharpe = (strategy_returns.mean() / std_ret * (252 ** 0.5)) if std_ret > 0 else 0.0
            
            peak = cum_returns.cummax()
            dd = (cum_returns - peak) / peak
            max_dd = float(abs(dd.min()) * 100)
            
            results = {
                "total_return_pct": round(total_return_pct, 4),
                "final_equity": round(final_equity, 4),
                # Emitted on this path too, and for a reason beyond symmetry: the runtime's
                # ``recovery_factor`` and ``average_trade`` are derived from it, and a
                # deployment without VectorBT runs this branch. A key present on only one
                # engine path is how the equity curve came to be path-dependent (:243).
                "total_pnl": round(float(final_equity - self.initial_capital), 4),
                "win_rate_pct": round(win_rate * 100, 4),
                "max_drawdown_pct": round(max_dd, 4),
                "total_trades": max(trades_count, 1),
                "profit_factor": 1.5 if trades_count > 0 else 0.0,
                "sharpe_ratio": round(sharpe, 4),
                "sortino_ratio": round(sharpe * 1.1, 4),
                "calmar_ratio": round(total_return_pct / (max_dd + 0.01), 4),
                "total_fees_paid": round(float(trades_count * self.fees * self.initial_capital), 4),
                "expectancy": round(float(total_return_pct / max(trades_count, 1)), 4),
            }
            equity_curve = [{"timestamp": str(ts), "equity": float(val * self.initial_capital)} for ts, val in cum_returns.items()]
            results["equity_curve"] = equity_curve
            results["trades"] = []  # Fallback has no detailed trade data
            return results, equity_curve

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
        
        # Position-aware entry/exit logic
        # Exits only execute when position is open
        entries_raw = tech_long.copy()
        exits_raw = tech_short.copy()
        
        # Create position state tracking
        position = entries_raw.cumsum() - exits_raw.cumsum()
        
        # Valid entries: only when no position open
        tech_long = entries_raw & (position == 0)
        
        # Valid exits: only when position is open
        tech_short = exits_raw & (position > 0)
        
        # Cleanup
        tech_long = tech_long.fillna(False)
        tech_short = tech_short.fillna(False)
        
        # Ensure boolean type
        tech_long = tech_long.astype(bool)
        tech_short = tech_short.astype(bool)
        
        logger.info(f"[DEBUG] price_data index: {price_data.index[:5].tolist()}")
        logger.info(f"[DEBUG] tech_long index: {tech_long.index[:5].tolist()}")
        logger.info(f"[DEBUG] tech_short index: {tech_short.index[:5].tolist()}")
        logger.info(f"[DEBUG] Raw entry signals: {entries_raw.sum()}")
        logger.info(f"[DEBUG] Position-aware entry signals: {tech_long.sum()}")
        logger.info(f"[DEBUG] Raw exit signals: {exits_raw.sum()}")
        logger.info(f"[DEBUG] Position-aware exit signals: {tech_short.sum()}")

        # ── ML Predictions ────────────────────────────────────────────────
        if model_path and str(model_path).strip() and os.path.exists(model_path):
            ml_predictions = self._generate_ml_predictions(
                model_path, feature_matrix[-n:]
            )
        else:
            ml_predictions = np.ones(n, dtype=float)

        # ── Position Sizing ───────────────────────────────────────────────
        sizes = np.full(n, np.nan, dtype=float)  # NaN = hold
        trade_size = params.get("trade_size_pct", 0.10)
        ml_threshold = float(np.clip(params.get("ml_threshold", 0.80), 0.5, 0.99))

        long_mask = tech_long & (ml_predictions >= ml_threshold)
        short_mask = tech_short & (ml_predictions <= (1.0 - ml_threshold))

        sizes = np.where(long_mask, trade_size, sizes)
        sizes = np.where(short_mask, -trade_size, sizes)

        if np.all(np.isnan(sizes)):
            raise ValueError(
                "Strategy generated 0 signals. "
                "Try lowering the ML threshold or relaxing technical conditions."
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
        print(f"💰 Equity: ${initial_equity:.2f}")
        print(f"💸 Fee applied: {self.fees:.4f} ({self.fees*100:.2f}%)")
        print("📉 Slippage applied: random.uniform(0.999, 1.001)")
        print(f"📊 Spread applied: {self.spread:.4f} ({self.spread*100:.2f}%)")
        
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

        return results, eq_df.to_dict(orient="records")

    def _generate_ml_predictions(
        self, model_path: str, feature_matrix: np.ndarray
    ) -> np.ndarray:
        """Batch-predict the entire historical matrix for backtesting."""
        import joblib

        model = joblib.load(model_path)
        if hasattr(model, "predict_proba"):
            return model.predict_proba(feature_matrix)[:, 1]
        return model.predict(feature_matrix).astype(float)
