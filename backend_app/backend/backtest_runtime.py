"""
backend/backtest_runtime.py — Integrated Backtesting Runtime

PHASE Backtesting Engine: Institutional-grade backtesting that executes
ONLY the Strategy Package produced by the Strategy Compiler.

Architecture:
Builder → Compiler → Execution Graph → Strategy Package → Backtesting Runtime
                                                ↓
                                            DAG Engine
                                                ↓
                                        VectorBT Simulation
                                                ↓
                                            Risk Engine
                                                ↓
                                        Portfolio Engine
                                                ↓
                                        Performance Analytics

IMPORTANT: Never duplicate execution logic. Backtesting and Live Trading
use the same DAG Engine execution pipeline.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import numpy as np
import pandas as pd

from backend_app.backend.strategy_compiler import (
    ExecutionGraph,
    StrategyPackage,
    load_plan,
    plan_to_engine_graph,
)
from backend_app.backend.dag_engine import DAGEngine
from backend_app.backend.backtesting_engine import BacktestEngine
from backend_app.core.risk_engine import RiskEngine
from backend_app.backend.data_seeking_engine import DataEngine
from backend_app.backend.backtest_service import BacktestService

logger = logging.getLogger("BacktestRuntime")


def _engine_metric(stats: Dict, key: str, default: float = 0.0) -> float:
    """One of ``backtesting_engine``'s own snake_case payload keys, as a usable float.

    ``_safe_stat`` in the engine deliberately answers ``None`` when a VectorBT stat is
    genuinely unavailable rather than faking a ``0`` - ``max_drawdown_pct``, ``calmar_ratio``
    and ``sortino_ratio`` can all arrive as ``None``. Every arithmetic read of those keys has
    to survive it, so the coercion lives in one place instead of at each call site.

    ``default`` is returned for a missing key, a ``None``, a non-numeric value and a
    NaN/inf - not because those are equivalent, but because none of them can be divided by.
    """
    value = stats.get(key, default)
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):  # NaN / ±inf
        return default
    return number


def _closed_trade_net_pnls(stats: Dict) -> List[float]:
    """The per-trade net P&L the engine already puts on ``stats["trades"]``.

    ``net_pnl`` and not the gross ``gross_pnl``: a trade whose fees exceed its gross profit
    is a loss, and counting it as a win is how a fee-heavy strategy comes to read as
    profitable. This is the one source the win/loss aggregates in
    :meth:`BacktestRuntime._calculate_performance_metrics` are derived from, so they cannot
    disagree with the ``trades`` rows a trader can expand and check.
    """
    pnls: List[float] = []
    for trade in stats.get("trades") or []:
        if not isinstance(trade, dict):
            continue
        value = trade.get("net_pnl")
        if value is None:
            continue
        try:
            pnls.append(float(value))
        except (TypeError, ValueError):
            continue
    return pnls


def _longest_streak(trade_pnls: List[float], winning: bool) -> int:
    """The longest run of consecutive winning (or losing) trades, in execution order.

    ``stats.get("Win Streak", 0)`` and ``stats.get("Loss Streak", 0)`` read display names
    VectorBT does not publish under those spellings and the engine never emitted, so both
    columns were ``0`` on every run. Break-even trades end a streak without starting one.
    """
    longest = 0
    current = 0
    for pnl in trade_pnls:
        if (pnl > 0) if winning else (pnl < 0):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


class BacktestRuntime:
    """
    Integrated backtesting runtime that executes Strategy Packages.
    
    This is the ONLY entry point for backtesting. It:
    1. Accepts Strategy Package from compiler
    2. Executes using DAG Engine (same as live trading)
    3. Simulates using VectorBT
    4. Applies risk limits from Risk Engine
    5. Tracks portfolio state
    6. Stores results via BacktestService
    """
    
    def __init__(
        self,
        initial_capital: float = 10_000.0,
        fees: float = 0.001,
        slippage: float = 0.0005,
        spread: float = 0.0002,
        risk_per_trade: float = 0.01,
        max_drawdown: float = 0.2,
        daily_loss_limit: float = 0.05
    ):
        """
        Initialize backtesting runtime with simulation and risk parameters.
        
        Args:
            initial_capital: Starting capital
            fees: Commission rate (0.001 = 0.1%)
            slippage: Slippage rate (0.0005 = 0.05%)
            spread: Spread rate (0.0002 = 0.02%)
            risk_per_trade: Risk per trade as fraction of equity (0.01 = 1%)
            max_drawdown: Maximum drawdown limit (0.2 = 20%)
            daily_loss_limit: Daily loss limit (0.05 = 5%)
        """
        # PHASE D: Market Simulator (VectorBT)
        self.vectorbt_engine = BacktestEngine(
            initial_capital=initial_capital,
            fees=fees,
            slippage=slippage,
            spread=spread
        )
        
        # Store backtest results for UI
        self.last_backtest_results = None
        
        # PHASE E: Risk Engine (same as live trading)
        self.risk_engine = RiskEngine(
            initial_capital=initial_capital,
            risk_per_trade=risk_per_trade,
            max_drawdown=max_drawdown,
            daily_loss_limit=daily_loss_limit
        )
        
        # PHASE C: Execution Runtime (DAG Engine - same as live trading)
        self.dag_engine = DAGEngine(enable_tracing=True)
        
        # PHASE B: Historical Data Engine
        self.data_engine = None  # Injected at runtime with exchange instance
        
        # PHASE K: Backtest Service for storage
        self.backtest_service = BacktestService()
        
        logger.info(f"[BACKTEST_RUNTIME] Initialized with ${initial_capital:.2f} capital")
    
    def set_data_engine(self, exchange_instance):
        """Inject exchange instance for historical data fetching."""
        self.data_engine = DataEngine(exchange_instance)

    # ══════════════════════════════════════════════════════════════════════
    #  THE VERSION LOAD PATH  (strategy-builder task 2.4, Requirements 22.3, 22.5)
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def load_version_plan(version_row: Any, *, registry: Any = None):
        """The ``LoadedPlan`` for a stored version row.

        This is the backtester's half of Requirement 22.5. It used to reconstruct the
        strategy from the ``blueprint`` column, which meant the backtester executed a
        graph it had re-derived rather than the artifact the save path actually produced -
        so a backtest and a live run could disagree about the same version.

        Now the persisted ``compiled_plan`` is read and used **as-is** when
        ``CompiledPlan.from_dict(row["compiled_plan"]).matches_graph(graph)``. It is
        recompiled only on a hash mismatch, and only compiled from the graph when no plan
        was stored at all - which is every existing row until migration 004 part 1 is
        applied, so a missing or NULL ``compiled_plan`` is a normal case here, not a
        crash.

        Returns
            ``LoadedPlan`` carrying ``plan``, ``graph``, ``reused`` and ``reason``, so the
            caller can log or assert which branch was taken rather than assume.
        """
        loaded = load_plan(version_row, registry)
        logger.info(
            "[BACKTEST] Version plan %s: dag_hash=%s reused=%s reason=%s",
            "reused" if loaded.reused else "recompiled",
            loaded.dag_hash,
            loaded.reused,
            loaded.reason,
        )
        return loaded

    async def run_version_backtest(
        self,
        version_row: Any,
        user: dict,
        strategy_id: str,
        version_id: str,
        version: str,
        start_date: str,
        end_date: str,
        exchange_instance,
        *,
        registry: Any = None,
    ) -> Dict:
        """Backtest a stored version from its persisted plan.

        The plan-shaped entry point for :meth:`run_backtest`: it resolves the version's
        ``CompiledPlan`` through :meth:`load_version_plan`, adapts it to the node/edge
        lists the DAG engine consumes, and then reuses the existing simulation path
        unchanged - the market simulator, the risk engine and the portfolio engine are
        untouched by task 2.4.

        Market identity comes from the graph's DATA nodes, so nothing here falls back to
        ``BTC/USDT`` / ``15m`` (SB-06).
        """
        from backend_app.backend.strategy_dag.schema import BlockCategory

        loaded = self.load_version_plan(version_row, registry=registry)
        nodes, edges = plan_to_engine_graph(loaded.plan, registry)

        symbols: List[str] = []
        timeframes: List[str] = []
        for node in loaded.graph.nodes:
            if node.category is not BlockCategory.DATA:
                continue
            symbol = node.params.get("symbol")
            frame = node.params.get("timeframe")
            if isinstance(symbol, str) and symbol and symbol not in symbols:
                symbols.append(symbol)
            if isinstance(frame, str) and frame and frame not in timeframes:
                timeframes.append(frame)

        if not symbols:
            # SB-06: nothing here substitutes a market. A version whose DATA nodes carry
            # no symbol cannot be backtested against anything in particular, and saying
            # so is better than quietly backtesting BTC/USDT.
            raise ValueError(
                f"Version {version} declares no symbol on any DATA node; there is "
                "nothing to backtest against. Set the DATA block's symbol."
            )

        metadata: Dict[str, Any] = {
            "strategy_name": loaded.graph.name or strategy_id,
            "symbols": symbols,
            "dag_hash": loaded.dag_hash,
            "warmup_bars": loaded.plan.warmup_bars,
            "plan_reused": loaded.reused,
        }
        if timeframes:
            metadata["timeframe"] = timeframes[0]

        execution_graph = ExecutionGraph(
            id=str(uuid4()),
            version=version,
            nodes=nodes,
            edges=edges,
            execution_order=list(loaded.plan.execution_order),
            metadata=metadata,
        )
        package = StrategyPackage(
            id=str(uuid4()),
            strategy_id=strategy_id,
            version=version,
            execution_graph=execution_graph,
            metadata=execution_graph.metadata,
            dependencies={},
        )

        return await self.run_backtest(
            strategy_package=package,
            user=user,
            strategy_id=strategy_id,
            version_id=version_id,
            version=version,
            start_date=start_date,
            end_date=end_date,
            exchange_instance=exchange_instance,
        )
    
    async def run_backtest(
        self,
        strategy_package: StrategyPackage,
        user: dict,
        strategy_id: str,
        version_id: str,
        version: str,
        start_date: str,
        end_date: str,
        exchange_instance
    ) -> Dict:
        """
        Execute backtest using Strategy Package.
        
        Args:
            strategy_package: Compiled Strategy Package from compiler
            user: User dictionary
            strategy_id: Strategy ID
            version_id: Version ID
            version: Version string
            start_date: Backtest start date
            end_date: Backtest end date
            exchange_instance: CCXT exchange instance for data fetching
            
        Returns:
            Complete backtest results with metrics, trades, charts
        """
        start_time = datetime.now(timezone.utc)
        
        # Set data engine
        self.set_data_engine(exchange_instance)
        
        # PHASE K: Create backtest record
        backtest_record = await self.backtest_service.create_backtest(
            user=user,
            strategy_id=strategy_id,
            version_id=version_id,
            version=version,
            blueprint=strategy_package.execution_graph.to_dict(),
            dataset=f"{strategy_package.metadata.get('symbols', ['BTC/USDT'])[0]}",
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.vectorbt_engine.initial_capital,
            commission=self.vectorbt_engine.fees,
            slippage=self.vectorbt_engine.slippage
        )
        
        backtest_id = backtest_record["id"]
        
        try:
            # PHASE B: Fetch historical data
            logger.info(f"[BACKTEST] Fetching historical data from {start_date} to {end_date}")
            symbol = strategy_package.metadata.get("symbols", ["BTC/USDT"])[0]
            timeframe = strategy_package.metadata.get("timeframe", "15m")
            
            ohlcv_data = await self.data_engine.fetch_historical_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                limit=10_000,  # Fetch up to 10k bars
                max_retries=3
            )
            
            if not ohlcv_data or len(ohlcv_data) < 50:
                raise ValueError(f"Insufficient historical data: {len(ohlcv_data) if ohlcv_data else 0} bars")
            
            logger.info(f"[BACKTEST] Fetched {len(ohlcv_data)} OHLCV bars")
            
            # Convert to DataFrame
            df = pd.DataFrame(
                ohlcv_data,
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.set_index("timestamp").sort_index()
            
            # PHASE C: Execute using DAG Engine (same as live trading)
            logger.info(f"[BACKTEST] Executing strategy using DAG Engine")
            execution_graph = strategy_package.execution_graph
            
            # Convert execution graph to DAG Engine format
            dag_results = self.dag_engine.execute(
                nodes=execution_graph.nodes,
                edges=execution_graph.edges,
                market_data=df
            )
            
            # Extract signals from DAG execution
            signals = dag_results.get("signals", pd.Series())
            
            if signals.empty or len(signals) == 0:
                logger.warning("[BACKTEST] Strategy generated no signals - using placeholder")
                signals = pd.Series(0, index=df.index)
            
            logger.info(f"[BACKTEST] Generated {len(signals)} signals")
            
            # Align signals with price data
            signals = signals.reindex(df.index).fillna(0)
            
            # PHASE D: Run VectorBT simulation
            logger.info(f"[BACKTEST] Running VectorBT simulation")
            
            # Convert signals to entry/exit format for VectorBT
            # Positive signals = long entries, negative signals = exits
            entries = signals > 0
            exits = signals < 0
            
            # PHASE E: Apply risk engine constraints
            # Filter signals that violate risk limits
            if not self.risk_engine.can_trade():
                logger.warning("[BACKTEST] Risk limits exceeded - no trades executed")
                entries = pd.Series(False, index=df.index)
                exits = pd.Series(False, index=df.index)
            
            # Run VectorBT backtest
            stats, returned_equity_curve = await self.vectorbt_engine.run_backtest_async(
                ohlcv_list=ohlcv_data,
                feature_matrix=np.zeros((len(df), 1)),  # Placeholder for ML features
                model_path="",  # No ML model by default
                tech_long_condition=entries.values,
                tech_short_condition=exits.values,
                params={
                    "timeframe": timeframe,
                    "trade_size_pct": 0.10,
                    "ml_threshold": 0.80,
                    "stop_loss_pct": 0.05,
                    "take_profit_pct": 0.15
                }
            )

            # ── The curve is read from the PAYLOAD, not from the tuple ─────────
            # It used to be unpacked into a local, read twice (by
            # ``_calculate_performance_metrics`` and ``_generate_charts``) and then left out
            # of the ``results`` literal below, so ``strategy_backtests.equity_curve``
            # persisted as ``[]`` for a run that produced one point per bar and the chart
            # rendered empty. The engine now carries the curve as a payload key on both of
            # its paths, so the ``{**stats}`` spread below carries it through on its own -
            # there is no longer a hand-off to forget.
            #
            # ``setdefault`` rather than ``get``: an engine that still answers with the
            # tuple alone (a caller's own subclass, or a double) has its curve adopted into
            # the payload here, so from this line on there is exactly one place the curve
            # lives regardless of which engine produced it.
            equity_curve = stats.setdefault("equity_curve", returned_equity_curve)

            # PHASE G: Performance Analytics
            performance_metrics = self._calculate_performance_metrics(
                stats, equity_curve, df
            )
            
            # PHASE H: Generate charts
            charts = self._generate_charts(
                equity_curve, df, performance_metrics
            )
            
            # Calculate execution time
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()

            # ── The engine's key names, not VectorBT's display names ───────────
            # ``stats.get("Final Equity", self.vectorbt_engine.initial_capital)`` is the
            # single most damaging read in this module. ``Final Equity`` is a VectorBT
            # *display* name; ``stats`` is the engine's own ``results`` dict, which
            # normalised it to ``final_equity`` before returning. So the lookup missed on
            # every run and the default won unconditionally: **every completed backtest
            # reported its starting capital as its final capital**. Worse than a zero - a
            # zero looks like a bug and gets questioned, whereas the starting capital reads
            # as "this strategy broke exactly even", and a profitable run and a ruinous one
            # render identically.
            #
            # The fallback is kept for an engine that emits no ``final_equity`` at all, but
            # it no longer does so in silence: substituting the starting capital for a
            # measured result is a claim worth a log line.
            final_equity = stats.get("final_equity")
            if final_equity is None:
                logger.warning(
                    "[BACKTEST] engine payload carried no 'final_equity'; reporting the "
                    "starting capital as final_capital. Payload keys: %s", sorted(stats)
                )
                final_equity = self.vectorbt_engine.initial_capital

            # Prepare results
            results = {
                **stats,
                **performance_metrics,
                "charts": charts,
                "execution_time_seconds": execution_time,
                # ``stats.get("Total Trades", 0)`` missed the same way, and published
                # ``trades_count: 0`` beside the engine's real ``total_trades`` in this very
                # literal - a payload that contradicted itself about how many trades the run
                # made. Repointed rather than dropped because the results surface declares
                # it as the fallback input for the trade count (``Backtester.jsx``
                # ``tradeCount``, ``pageFields.js`` ``inputs``), so it is a live key, not a
                # dead one. It now agrees with ``total_trades`` instead of contradicting it.
                "trades_count": stats.get("total_trades", 0),
                "final_capital": final_equity,
            }
            
            # PHASE K: Update backtest with results
            #
            # ``executed_bar_count`` (marketplace-subscriptions-paper-trading task 12.1,
            # Requirements 3.8, 3.4, 25.1): ``len(ohlcv_data)`` is the number of bars this
            # simulation actually ran over - the same value the guard above tests against
            # 50 and the same one the log line reports - and it was computed here and
            # thrown away, so ``strategy_backtests`` carried no bar count at all and the
            # marketplace Evidence_Validator's "non-null, at least 50" criterion could
            # never pass. It is recorded now, in the same UPDATE as the metrics it
            # describes. Nothing re-judges it here: the 50-bar guard above and the
            # 20-trade significance warning in ``backtesting_engine`` are unchanged.
            await self.backtest_service.update_backtest_results(
                user=user,
                backtest_id=backtest_id,
                results=results,
                executed_bar_count=len(ohlcv_data),
            )
            
            logger.info(f"[BACKTEST] Completed backtest {backtest_id} in {execution_time:.2f}s")
            
            return {
                "backtest_id": backtest_id,
                "status": "completed",
                "results": results
            }
            
        except Exception as e:
            logger.error(f"[BACKTEST] Failed: {e}")
            # Update backtest with error status
            await self.backtest_service.update_backtest_results(
                user=user,
                backtest_id=backtest_id,
                results={
                    "status": "failed",
                    "error": str(e),
                    "execution_time_seconds": (datetime.now(timezone.utc) - start_time).total_seconds()
                }
            )
            raise
    
    def _calculate_performance_metrics(
        self,
        stats: Dict,
        equity_curve: List,
        price_data: pd.DataFrame
    ) -> Dict:
        """
        PHASE G: Calculate comprehensive performance analytics.
        
        Args:
            stats: the ENGINE'S ``results`` payload, whose keys are snake_case. It is *not*
                ``portfolio.stats()`` and it does not carry VectorBT's display names.
            equity_curve: Equity curve data
            price_data: Price data DataFrame

        Returns:
            Dictionary of performance metrics

        WHY EVERY ``stats`` READ BELOW IS snake_case
            This method used to read ten VectorBT *display* names out of ``stats`` -
            ``Max Drawdown [%]``, ``Net Profit``, ``Total Trades``, ``Best Trade``,
            ``Worst Trade``, ``Win Streak``, ``Loss Streak``, ``Win Rate [%]``,
            ``Avg Winning Trade`` and ``Avg Losing Trade`` - and ``run_backtest`` read two
            more. ``stats`` is the engine's already-normalised ``results`` dict, so all
            twelve missed on **every single run** and each ``, 0)`` default won, fabricating
            twelve columns out of arithmetic on zeros: ``calmar_ratio``,
            ``recovery_factor``, ``average_trade``, ``largest_win``, ``largest_loss``,
            ``consecutive_wins``, ``consecutive_losses``, ``expectancy``, ``kelly``,
            ``sqn``, ``trades_count`` and ``final_capital``. Every one of them was rendered
            to a trader as a measured result.

            Six had a snake_case counterpart to point at. The other six - best and worst
            trade, the two streaks, the two averages - have no engine key at all, so there
            was nothing to repoint them at; they are derived below from the per-trade
            ``net_pnl`` values the engine already puts on ``stats["trades"]``.
        """
        # ── The equity series, on the index the run actually ran on ────────
        # DEFECT 49. This used to build the series with a default RangeIndex and then, at
        # the bottom of the method, do ``equity_series.index = pd.to_datetime(index)`` on
        # it - which reads ``0..239`` as NANOSECONDS since the epoch. All 240 points landed
        # inside one 240ns window in January 1970, so ``resample('M')`` and ``resample('D')``
        # each produced a single bucket and ``pct_change().dropna()`` returned nothing:
        # ``monthly_returns`` and ``daily_returns`` persisted as ``[]`` on every run, for
        # runs that carried a timestamp on every point. Both columns are in the writer's
        # read-key set and both were written, so the key-set contract reported them present
        # and correct while the values were empty - a flat result is indistinguishable from
        # a destroyed index once the list is stored.
        #
        # The timestamps are taken from the curve itself, where they already are, normalised
        # the same way :meth:`_generate_charts` normalises this identical argument.
        timestamps = None
        if isinstance(equity_curve, list) and len(equity_curve) > 0 and isinstance(equity_curve[0], dict):
            equity_series = pd.Series([float(e.get("equity", 0.0)) for e in equity_curve])
            stamps = [e.get("timestamp") for e in equity_curve]
            if all(stamp is not None for stamp in stamps):
                timestamps = pd.to_datetime(pd.Index(stamps), errors="coerce", utc=True)
        else:
            equity_series = pd.Series(equity_curve, dtype=float)

        if len(equity_series) == 0:
            return {"sortino_ratio": 0.0, "calmar_ratio": 0.0, "recovery_factor": 0.0}

        # A curve whose own timestamps are unusable falls back to the bar index the run
        # executed over - the same instants, read off the other end of the same simulation -
        # and only then to leaving the positional index alone. What it must never do again is
        # reinterpret ``0..n-1`` as epoch nanoseconds.
        if timestamps is not None and timestamps.isna().any():
            timestamps = None
        if timestamps is None and isinstance(price_data.index, pd.DatetimeIndex) \
                and len(price_data.index) == len(equity_series):
            timestamps = price_data.index
        if timestamps is not None:
            equity_series.index = timestamps

        returns = equity_series.pct_change().dropna()

        # Calculate additional metrics
        metrics = {}
        
        # Sortino Ratio (downside risk only)
        downside_returns = returns[returns < 0]
        if len(downside_returns) > 0 and downside_returns.std() > 0:
            metrics["sortino_ratio"] = (returns.mean() / downside_returns.std()) * (252 ** 0.5)
        else:
            metrics["sortino_ratio"] = 0.0
        
        # Calmar Ratio (annual return / max drawdown)
        if len(equity_series) > 0 and equity_series.iloc[0] != 0:
            annual_return = (equity_series.iloc[-1] / equity_series.iloc[0] - 1) * (252 / len(equity_series))
        else:
            annual_return = 0.0
        # ``max_drawdown_pct``, not ``Max Drawdown [%]``.
        max_dd = _engine_metric(stats, "max_drawdown_pct") / 100
        if max_dd > 0:
            metrics["calmar_ratio"] = annual_return / max_dd
        else:
            metrics["calmar_ratio"] = 0.0
        
        # Recovery Factor (net profit / max drawdown)
        # ``total_pnl``, not ``Net Profit``. The engine computed this figure all along and
        # spent it on a log line; it is a payload key now, which is what gives this read
        # something to point at.
        net_profit = _engine_metric(stats, "total_pnl")
        if max_dd > 0:
            metrics["recovery_factor"] = net_profit / max_dd
        else:
            metrics["recovery_factor"] = 0.0
        
        # Average Trade
        # ``total_trades``, not ``Total Trades``.
        total_trades = int(_engine_metric(stats, "total_trades"))
        if total_trades > 0:
            metrics["average_trade"] = net_profit / total_trades
        else:
            metrics["average_trade"] = 0.0

        # ── Per-trade aggregates, from the engine's own trade rows ─────────
        # ``Best Trade``, ``Worst Trade``, ``Win Streak``, ``Loss Streak``,
        # ``Avg Winning Trade`` and ``Avg Losing Trade`` have no counterpart in the engine's
        # payload, so there is no key to repoint them at. What they wanted is on
        # ``stats["trades"]``: one ``net_pnl`` per closed trade, emitted all along. Deriving
        # all six from that one list also means one definition of "winning" across the
        # method, rather than six lookups that could disagree.
        trade_pnls = _closed_trade_net_pnls(stats)
        wins = [pnl for pnl in trade_pnls if pnl > 0]
        losses = [pnl for pnl in trade_pnls if pnl < 0]

        # Largest Win/Loss
        metrics["largest_win"] = max(wins) if wins else 0.0
        metrics["largest_loss"] = min(losses) if losses else 0.0
        
        # Consecutive Wins/Losses
        metrics["consecutive_wins"] = _longest_streak(trade_pnls, winning=True)
        metrics["consecutive_losses"] = _longest_streak(trade_pnls, winning=False)
        
        # Expectancy
        # ``win_rate_pct``, not ``Win Rate [%]``; the two averages from the trade rows.
        win_rate = _engine_metric(stats, "win_rate_pct") / 100
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        if avg_loss != 0:
            metrics["expectancy"] = (win_rate * avg_win) - ((1 - win_rate) * abs(avg_loss))
        else:
            metrics["expectancy"] = 0.0
        
        # SQN (System Quality Number)
        if total_trades > 0 and returns.std() > 0:
            metrics["sqn"] = (np.sqrt(total_trades) * returns.mean()) / returns.std()
        else:
            metrics["sqn"] = 0.0
        
        # Kelly Criterion
        if avg_loss != 0:
            metrics["kelly"] = win_rate - ((1 - win_rate) / (avg_win / abs(avg_loss)))
        else:
            metrics["kelly"] = 0.0
        
        # ── Monthly and Daily Returns (DEFECT 49) ──────────────────────────
        # Resampled on the DatetimeIndex established at the top of this method. The
        # ``equity_series.index = pd.to_datetime(equity_series.index)`` that used to sit here
        # is gone: by this point the index is already the run's real instants, and re-reading
        # a RangeIndex as epoch nanoseconds is the whole defect.
        #
        # A run shorter than the bucket legitimately yields no rows - a four-hour backtest
        # has no month-over-month return - which is a different thing from the empty lists
        # the nanosecond index produced for every run regardless of span.
        if isinstance(equity_series.index, pd.DatetimeIndex):
            monthly_returns = equity_series.resample('M').last().pct_change().dropna()
            daily_returns = equity_series.resample('D').last().pct_change().dropna()
            metrics["monthly_returns"] = monthly_returns.tolist()
            metrics["daily_returns"] = daily_returns.tolist()
        else:
            # No timestamps anywhere - neither on the curve nor on the bars. An empty list
            # is the honest answer; a positional index resampled as time is not.
            logger.warning(
                "[BACKTEST] equity curve carried no usable timestamps; monthly and daily "
                "returns are not derivable for this run"
            )
            metrics["monthly_returns"] = []
            metrics["daily_returns"] = []
        
        return metrics
    
    def _generate_charts(
        self,
        equity_curve: List,
        price_data: pd.DataFrame,
        metrics: Dict
    ) -> Dict:
        """
        PHASE H: Generate chart data for visualization.
        
        Args:
            equity_curve: Equity curve data
            price_data: Price data DataFrame
            metrics: Performance metrics
            
        Returns:
            Dictionary of chart data
        """
        # ``equity_curve`` arrives as ``eq_df.to_dict(orient="records")`` - a list of
        # ``{"timestamp", "equity"}`` mappings - so ``pd.Series(equity_curve)`` produced a
        # Series OF DICTS with a RangeIndex, and the first arithmetic on it
        # (``cummax()`` in :meth:`_calculate_drawdown_curve`) raised
        # "'>=' not supported between instances of 'dict' and 'dict'". Every backtest that
        # reached charting therefore answered 500.
        #
        # Normalised the same way :meth:`_calculate_performance_metrics` already normalises
        # the identical argument, so the two readers of one value agree about its shape:
        # the values are the equities and the index is the timestamps.
        if (
            isinstance(equity_curve, list)
            and equity_curve
            and isinstance(equity_curve[0], dict)
        ):
            equity_series = pd.Series(
                [float(point.get("equity", 0.0)) for point in equity_curve],
                index=pd.to_datetime([point.get("timestamp") for point in equity_curve]),
            )
        else:
            equity_series = pd.Series(equity_curve, dtype=float)
            equity_series.index = pd.to_datetime(equity_series.index)
        
        # ``astype("int64")``, never ``astype(int)``: a DatetimeIndex converts to epoch
        # NANOSECONDS, which needs 64 bits, and ``int`` is platform-dependent - it is
        # ``int32`` on Windows, where pandas 2.x then refuses the cast outright with
        # "Converting from datetime64[ns] to int32 is not supported". So this raised on
        # every backtest that got as far as charting, on one of the platforms this backend
        # is developed on, and silently truncated nothing on the other. Found by the
        # Requirement 26.4 end-to-end sandbox suite (``tests/sandbox_lifecycle/``).
        charts = {
            "equity_curve": {
                "timestamps": equity_series.index.astype("int64").tolist(),
                "values": equity_series.tolist()
            },
            "drawdown_curve": self._calculate_drawdown_curve(equity_series),
            "monthly_returns": metrics.get("monthly_returns", []),
            "daily_returns": metrics.get("daily_returns", []),
            "price_chart": {
                "timestamps": price_data.index.astype("int64").tolist(),
                "close": price_data["close"].tolist()
            }
        }
        
        return charts
    
    def _calculate_drawdown_curve(self, equity_series: pd.Series) -> Dict:
        """Calculate drawdown curve for charting."""
        running_max = equity_series.cummax()
        drawdown = (equity_series - running_max) / running_max * 100
        
        return {
            # ``int64`` for the same reason as :meth:`_generate_charts` above.
            "timestamps": equity_series.index.astype("int64").tolist(),
            "values": drawdown.tolist()
        }


# Singleton instance
_backtest_runtime = None

def get_backtest_runtime() -> BacktestRuntime:
    """Get singleton BacktestRuntime instance."""
    global _backtest_runtime
    if _backtest_runtime is None:
        _backtest_runtime = BacktestRuntime()
    return _backtest_runtime