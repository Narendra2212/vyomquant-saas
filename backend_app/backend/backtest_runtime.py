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

from backend_app.backend.strategy_compiler import StrategyPackage, ExecutionGraph
from backend_app.backend.dag_engine import DAGEngine
from backend_app.backend.backtesting_engine import BacktestEngine
from backend_app.core.risk_engine import RiskEngine
from backend_app.backend.data_seeking_engine import DataEngine
from backend_app.backend.backtest_service import BacktestService

logger = logging.getLogger("BacktestRuntime")


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
            stats, equity_curve = await self.vectorbt_engine.run_backtest_async(
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
            
            # Prepare results
            results = {
                **stats,
                **performance_metrics,
                "charts": charts,
                "execution_time_seconds": execution_time,
                "trades_count": stats.get("Total Trades", 0),
                "final_capital": stats.get("Final Equity", self.vectorbt_engine.initial_capital)
            }
            
            # PHASE K: Update backtest with results
            await self.backtest_service.update_backtest_results(
                user=user,
                backtest_id=backtest_id,
                results=results
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
            stats: VectorBT stats dictionary
            equity_curve: Equity curve data
            price_data: Price data DataFrame
            
        Returns:
            Dictionary of performance metrics
        """
        if isinstance(equity_curve, list) and len(equity_curve) > 0 and isinstance(equity_curve[0], dict):
            equity_series = pd.Series([float(e.get("equity", 0.0)) for e in equity_curve])
        else:
            equity_series = pd.Series(equity_curve, dtype=float)
        
        if len(equity_series) == 0:
            return {"sortino_ratio": 0.0, "calmar_ratio": 0.0, "recovery_factor": 0.0}
            
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
        max_dd = stats.get("Max Drawdown [%]", 0) / 100
        if max_dd > 0:
            metrics["calmar_ratio"] = annual_return / max_dd
        else:
            metrics["calmar_ratio"] = 0.0
        
        # Recovery Factor (net profit / max drawdown)
        net_profit = stats.get("Net Profit", 0)
        if max_dd > 0:
            metrics["recovery_factor"] = net_profit / max_dd
        else:
            metrics["recovery_factor"] = 0.0
        
        # Average Trade
        total_trades = stats.get("Total Trades", 0)
        if total_trades > 0:
            metrics["average_trade"] = net_profit / total_trades
        else:
            metrics["average_trade"] = 0.0
        
        # Largest Win/Loss
        metrics["largest_win"] = stats.get("Best Trade", 0)
        metrics["largest_loss"] = stats.get("Worst Trade", 0)
        
        # Consecutive Wins/Losses
        metrics["consecutive_wins"] = stats.get("Win Streak", 0)
        metrics["consecutive_losses"] = stats.get("Loss Streak", 0)
        
        # Expectancy
        win_rate = stats.get("Win Rate [%]", 0) / 100
        avg_win = stats.get("Avg Winning Trade", 0)
        avg_loss = stats.get("Avg Losing Trade", 0)
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
        
        # Monthly Returns
        equity_series.index = pd.to_datetime(equity_series.index)
        monthly_returns = equity_series.resample('M').last().pct_change().dropna()
        metrics["monthly_returns"] = monthly_returns.tolist()
        
        # Daily Returns
        daily_returns = equity_series.resample('D').last().pct_change().dropna()
        metrics["daily_returns"] = daily_returns.tolist()
        
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
        equity_series = pd.Series(equity_curve)
        equity_series.index = pd.to_datetime(equity_series.index)
        
        charts = {
            "equity_curve": {
                "timestamps": equity_series.index.astype(int).tolist(),
                "values": equity_series.tolist()
            },
            "drawdown_curve": self._calculate_drawdown_curve(equity_series),
            "monthly_returns": metrics.get("monthly_returns", []),
            "daily_returns": metrics.get("daily_returns", []),
            "price_chart": {
                "timestamps": price_data.index.astype(int).tolist(),
                "close": price_data["close"].tolist()
            }
        }
        
        return charts
    
    def _calculate_drawdown_curve(self, equity_series: pd.Series) -> Dict:
        """Calculate drawdown curve for charting."""
        running_max = equity_series.cummax()
        drawdown = (equity_series - running_max) / running_max * 100
        
        return {
            "timestamps": equity_series.index.astype(int).tolist(),
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