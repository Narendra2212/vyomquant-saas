"""
Performance Engine for Trading Analytics

Computes comprehensive trading performance metrics from trade log and equity curve.
Supports both single-period and time-series analysis.
"""

import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Trade:
    """Represents a single trade"""
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    side: str  # "long" or "short"
    commission: float = 0.0
    slippage: float = 0.0


class PerformanceEngine:
    """
    PerformanceEngine computes trading performance metrics from trade log and equity curve.
    
    Key Metrics:
    - Total Return
    - Win Rate
    - Profit Factor
    - Sharpe Ratio
    - Maximum Drawdown
    - Calmar Ratio
    - Sortino Ratio
    - Average Trade Metrics
    """
    
    def __init__(self, trade_log: List[Dict], equity_curve: List[float], timestamps: Optional[List[datetime]] = None):
        """
        Initialize PerformanceEngine.
        
        Args:
            trade_log: List of trade dictionaries with keys like 'symbol', 'pnl', 'entry_price', etc.
            equity_curve: List of equity values over time
            timestamps: Optional list of datetime objects corresponding to equity curve points
        """
        self.trade_log = trade_log
        self.equity_curve = np.array(equity_curve) if equity_curve else np.array([])
        self.timestamps = timestamps or []
        
        # Validate inputs
        if len(self.equity_curve) < 2:
            raise ValueError("Equity curve must have at least 2 points")
        
        # Pre-compute returns
        if len(self.equity_curve) > 1:
            self.returns = np.diff(self.equity_curve) / self.equity_curve[:-1]
        else:
            self.returns = np.array([])
    
    # ----------------------------------
    # TOTAL RETURN
    # ----------------------------------
    def total_return(self) -> float:
        """
        Calculate total return percentage.
        
        Returns:
            Total return as decimal (e.g., 0.25 = 25%)
        """
        if len(self.equity_curve) < 2 or self.equity_curve[0] == 0:
            return 0.0
        return (self.equity_curve[-1] - self.equity_curve[0]) / self.equity_curve[0]
    
    # ----------------------------------
    # WIN RATE
    # ----------------------------------
    def win_rate(self) -> float:
        """
        Calculate win rate (percentage of winning trades).
        
        Returns:
            Win rate as decimal (e.g., 0.55 = 55%)
        """
        if not self.trade_log:
            return 0.0
        
        wins = [t for t in self.trade_log if t.get("pnl", 0) > 0]
        return len(wins) / len(self.trade_log)
    
    # ----------------------------------
    # LOSS RATE
    # ----------------------------------
    def loss_rate(self) -> float:
        """
        Calculate loss rate (percentage of losing trades).
        
        Returns:
            Loss rate as decimal
        """
        if not self.trade_log:
            return 0.0
        return 1.0 - self.win_rate()
    
    # ----------------------------------
    # PROFIT FACTOR
    # ----------------------------------
    def profit_factor(self) -> float:
        """
        Calculate profit factor (gross profit / gross loss).
        
        Returns:
            Profit factor (higher is better, >1.0 is profitable)
        """
        if not self.trade_log:
            return 0.0
        
        profit = sum(t.get("pnl", 0) for t in self.trade_log if t.get("pnl", 0) > 0)
        loss = abs(sum(t.get("pnl", 0) for t in self.trade_log if t.get("pnl", 0) < 0))
        
        if loss == 0:
            return float("inf") if profit > 0 else 0.0
        
        return profit / loss
    
    # ----------------------------------
    # SHARPE RATIO
    # ----------------------------------
    def sharpe_ratio(self, risk_free_rate: float = 0.0, periods_per_year: int = 252) -> float:
        """
        Calculate Sharpe Ratio (risk-adjusted return).
        
        Args:
            risk_free_rate: Risk-free rate per period (default: 0)
            periods_per_year: Number of periods per year (default: 252 for daily)
        
        Returns:
            Sharpe ratio (higher is better)
        """
        if len(self.returns) == 0:
            return 0.0
        
        # Annualize the risk-free rate to match return periods
        rf_per_period = risk_free_rate / periods_per_year if periods_per_year > 0 else risk_free_rate
        
        excess_returns = self.returns - rf_per_period
        std = np.std(excess_returns)
        
        if std == 0:
            return 0.0
        
        # Annualize Sharpe
        sharpe = np.mean(excess_returns) / std
        return sharpe * np.sqrt(periods_per_year)
    
    # ----------------------------------
    # SORTINO RATIO
    # ----------------------------------
    def sortino_ratio(self, risk_free_rate: float = 0.0, periods_per_year: int = 252) -> float:
        """
        Calculate Sortino Ratio (downside deviation only).
        
        Args:
            risk_free_rate: Risk-free rate per period (default: 0)
            periods_per_year: Number of periods per year (default: 252)
        
        Returns:
            Sortino ratio (higher is better)
        """
        if len(self.returns) == 0:
            return 0.0
        
        rf_per_period = risk_free_rate / periods_per_year if periods_per_year > 0 else risk_free_rate
        excess_returns = self.returns - rf_per_period
        
        # Downside deviation (only negative returns)
        negative_returns = excess_returns[excess_returns < 0]
        downside_std = np.std(negative_returns) if len(negative_returns) > 0 else 0.0
        
        if downside_std == 0:
            return float("inf") if np.mean(excess_returns) > 0 else 0.0
        
        sortino = np.mean(excess_returns) / downside_std
        return sortino * np.sqrt(periods_per_year)
    
    # ----------------------------------
    # MAX DRAWDOWN
    # ----------------------------------
    def max_drawdown(self) -> float:
        """
        Calculate maximum drawdown.
        
        Returns:
            Maximum drawdown as decimal (e.g., 0.20 = 20%)
        """
        if len(self.equity_curve) == 0:
            return 0.0
        
        peak = self.equity_curve[0]
        max_dd = 0.0
        
        for value in self.equity_curve:
            if value > peak:
                peak = value
            
            dd = (peak - value) / peak if peak > 0 else 0.0
            
            if dd > max_dd:
                max_dd = dd
        
        return max_dd
    
    # ----------------------------------
    # CALMAR RATIO
    # ----------------------------------
    def calmar_ratio(self, periods_per_year: int = 252) -> float:
        """
        Calculate Calmar Ratio (return / max drawdown).
        
        Args:
            periods_per_year: Number of periods per year
        
        Returns:
            Calmar ratio (higher is better)
        """
        total_ret = self.total_return()
        mdd = self.max_drawdown()
        
        if mdd == 0:
            return float("inf") if total_ret > 0 else 0.0
        
        # Annualize return
        if len(self.equity_curve) > 0:
            total_periods = len(self.returns) if len(self.returns) > 0 else 1
            periods_per_year_actual = min(periods_per_year, total_periods)
            annualized_return = ((1 + total_ret) ** (periods_per_year_actual / total_periods)) - 1
        else:
            annualized_return = total_ret
        
        return annualized_return / mdd
    
    # ----------------------------------
    # AVERAGE TRADE METRICS
    # ----------------------------------
    def average_trade_pnl(self) -> float:
        """Average PnL per trade"""
        if not self.trade_log:
            return 0.0
        return sum(t.get("pnl", 0) for t in self.trade_log) / len(self.trade_log)
    
    def average_win(self) -> float:
        """Average winning trade PnL"""
        wins = [t.get("pnl", 0) for t in self.trade_log if t.get("pnl", 0) > 0]
        return sum(wins) / len(wins) if wins else 0.0
    
    def average_loss(self) -> float:
        """Average losing trade PnL (absolute value)"""
        losses = [t.get("pnl", 0) for t in self.trade_log if t.get("pnl", 0) < 0]
        return abs(sum(losses) / len(losses)) if losses else 0.0
    
    def avg_trade_duration_minutes(self) -> float:
        """Average trade duration in minutes"""
        if not self.trade_log:
            return 0.0
        
        durations = []
        for trade in self.trade_log:
            entry = trade.get("entry_time")
            exit = trade.get("exit_time")
            if entry and exit:
                if isinstance(entry, str):
                    entry = datetime.fromisoformat(entry)
                if isinstance(exit, str):
                    exit = datetime.fromisoformat(exit)
                duration = (exit - entry).total_seconds() / 60
                durations.append(duration)
        
        return sum(durations) / len(durations) if durations else 0.0
    
    # ----------------------------------
    # CONSECUTIVE METRICS
    # ----------------------------------
    def max_consecutive_wins(self) -> int:
        """Maximum consecutive winning trades"""
        max_wins = 0
        current_wins = 0
        
        for trade in self.trade_log:
            if trade.get("pnl", 0) > 0:
                current_wins += 1
                max_wins = max(max_wins, current_wins)
            else:
                current_wins = 0
        
        return max_wins
    
    def max_consecutive_losses(self) -> int:
        """Maximum consecutive losing trades"""
        max_losses = 0
        current_losses = 0
        
        for trade in self.trade_log:
            if trade.get("pnl", 0) < 0:
                current_losses += 1
                max_losses = max(max_losses, current_losses)
            else:
                current_losses = 0
        
        return max_losses
    
    # ----------------------------------
    # VOLATILITY
    # ----------------------------------
    def volatility(self, periods_per_year: int = 252) -> float:
        """
        Calculate annualized volatility.
        
        Args:
            periods_per_year: Number of periods per year
        
        Returns:
            Annualized volatility as decimal
        """
        if len(self.returns) == 0:
            return 0.0
        return np.std(self.returns) * np.sqrt(periods_per_year)
    
    # ----------------------------------
    # SUMMARY
    # ----------------------------------
    def summary(self) -> Dict[str, Any]:
        """
        Get comprehensive performance summary.
        
        Returns:
            Dictionary with all key metrics
        """
        total_ret = self.total_return()
        mdd = self.max_drawdown()
        
        return {
            # Returns
            "total_return": total_ret,
            "total_return_pct": total_ret * 100,
            "equity_start": float(self.equity_curve[0]) if len(self.equity_curve) > 0 else 0.0,
            "equity_final": float(self.equity_curve[-1]) if len(self.equity_curve) > 0 else 0.0,
            
            # Trade Statistics
            "total_trades": len(self.trade_log),
            "win_rate": self.win_rate(),
            "win_rate_pct": self.win_rate() * 100,
            "loss_rate": self.loss_rate(),
            "profit_factor": self.profit_factor(),
            
            # Risk Metrics
            "max_drawdown": mdd,
            "max_drawdown_pct": mdd * 100,
            "sharpe_ratio": self.sharpe_ratio(),
            "sortino_ratio": self.sortino_ratio(),
            "calmar_ratio": self.calmar_ratio(),
            "volatility": self.volatility(),
            
            # Trade Details
            "avg_trade_pnl": self.average_trade_pnl(),
            "avg_win": self.average_win(),
            "avg_loss": self.average_loss(),
            "avg_trade_duration_min": self.avg_trade_duration_minutes(),
            "max_consecutive_wins": self.max_consecutive_wins(),
            "max_consecutive_losses": self.max_consecutive_losses(),
            
            # Expectancy
            "expectancy": self._calculate_expectancy()
        }
    
    def _calculate_expectancy(self) -> float:
        """
        Calculate expectancy: (Win Rate * Avg Win) - (Loss Rate * Avg Loss)
        
        Returns:
            Expected PnL per trade
        """
        win_rate = self.win_rate()
        loss_rate = self.loss_rate()
        avg_win = self.average_win()
        avg_loss = self.average_loss()
        
        return (win_rate * avg_win) - (loss_rate * avg_loss)
    
    # ----------------------------------
    # EQUITY CURVE ANALYSIS
    # ----------------------------------
    def drawdown_series(self) -> np.ndarray:
        """
        Calculate drawdown at each point in equity curve.
        
        Returns:
            Array of drawdown values
        """
        if len(self.equity_curve) == 0:
            return np.array([])
        
        peak = self.equity_curve[0]
        drawdowns = []
        
        for value in self.equity_curve:
            if value > peak:
                peak = value
            dd = (peak - value) / peak if peak > 0 else 0.0
            drawdowns.append(dd)
        
        return np.array(drawdowns)
    
    def underwater_chart_data(self) -> List[Dict]:
        """
        Generate data for underwater chart (drawdown over time).
        
        Returns:
            List of dicts with 'timestamp' and 'drawdown'
        """
        drawdowns = self.drawdown_series()
        
        data = []
        for i, dd in enumerate(drawdowns):
            point = {"drawdown": float(dd), "drawdown_pct": float(dd * 100)}
            if i < len(self.timestamps):
                point["timestamp"] = self.timestamps[i].isoformat()
            data.append(point)
        
        return data
    
    # ----------------------------------
    # BENCHMARK COMPARISON
    # ----------------------------------
    def compare_to_buy_and_hold(self, benchmark_returns: np.ndarray) -> Dict:
        """
        Compare strategy performance to buy-and-hold benchmark.
        
        Args:
            benchmark_returns: Array of benchmark returns per period
        
        Returns:
            Comparison metrics
        """
        if len(self.returns) == 0 or len(benchmark_returns) == 0:
            return {}
        
        strategy_total = self.total_return()
        benchmark_total = (benchmark_returns[-1] / benchmark_returns[0] - 1) if benchmark_returns[0] != 0 else 0.0
        
        # Beta calculation
        if len(self.returns) == len(benchmark_returns):
            covariance = np.cov(self.returns, np.diff(benchmark_returns) / benchmark_returns[:-1])[0, 1]
            benchmark_variance = np.var(np.diff(benchmark_returns) / benchmark_returns[:-1])
            beta = covariance / benchmark_variance if benchmark_variance != 0 else 0.0
        else:
            beta = 0.0
        
        return {
            "strategy_return": strategy_total,
            "benchmark_return": benchmark_total,
            "alpha": strategy_total - benchmark_total,
            "beta": beta,
            "outperformance": strategy_total > benchmark_total
        }
