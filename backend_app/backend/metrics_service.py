"""
backend/metrics_service.py — Metrics Service

PHASE 4: Performance Metrics from Backend Only

All performance metrics calculated and served from backend.
Frontend only displays values, never calculates.

Provides:
- Strategy performance metrics (PnL, ROI, win rate, Sharpe, Sortino, etc.)
- Realtime metrics (CPU, memory, worker uptime, exchange latency)
- Historical metrics (equity curve, monthly returns, daily returns)
- Execution metrics (order count, signal count, latency)
- Risk metrics (drawdown, exposure, position limits)
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from backend_app.core.dependencies import get_telemetry

logger = logging.getLogger("MetricsService")


class MetricsService:
    """
    Central service for all performance metrics.
    
    All calculations performed in backend.
    Frontend only displays values.
    """
    
    def __init__(self):
        self._telemetry = None
    
    def _get_telemetry(self):
        """Get TelemetryEngine instance."""
        if self._telemetry is None:
            from backend_app.core.dependencies import get_telemetry
            self._telemetry = get_telemetry()
        return self._telemetry
    
    async def get_strategy_performance(
        self,
        user_id: str,
        strategy_id: str,
        time_range: str = "1d"
    ) -> Dict:
        """
        Get comprehensive Strategy performance metrics.
        
        All calculations performed in backend using QuestDB.
        
        Args:
            user_id: User ID
            strategy_id: Strategy ID
            time_range: Time range (1d, 1w, 1m, 3m, all)
            
        Returns:
            Performance metrics dictionary
        """
        try:
            telemetry = self._get_telemetry()
            
            # Calculate time range
            time_range_map = {
                "1d": timedelta(days=1),
                "1w": timedelta(weeks=1),
                "1m": timedelta(days=30),
                "3m": timedelta(days=90),
                "all": timedelta(days=3650)
            }
            delta = time_range_map.get(time_range, timedelta(days=1))
            start_time = datetime.now(timezone.utc) - delta
            
            # Query performance data from QuestDB
            query = f"""
            SELECT
                timestamp,
                strategy_id,
                total_equity,
                total_pnl,
                pnl_pct,
                available_balance,
                unrealized_pnl,
                position_count,
                total_trades,
                winning_trades,
                losing_trades,
                total_volume,
                avg_trade_size,
                max_drawdown,
                sharpe_ratio,
                sortino_ratio,
                profit_factor,
                avg_win,
                avg_loss,
                win_rate
            FROM strategy_performance
            WHERE strategy_id = '{strategy_id}'
            AND timestamp >= '{start_time.isoformat()}'
            ORDER BY timestamp DESC
            LIMIT 1000
            """
            
            result = await telemetry.execute_query(query)
            
            if not result or not result.get("dataset"):
                # Return empty metrics if no data
                return self._empty_performance_metrics()
            
            # Calculate metrics from data
            rows = result["dataset"]
            cols = [c["name"] for c in result["columns"]]
            
            if not rows:
                return self._empty_performance_metrics()
            
            # Get latest row
            latest = dict(zip(cols, rows[0]))
            
            # Calculate derived metrics
            metrics = {
                "strategy_id": strategy_id,
                "timestamp": latest.get("timestamp"),
                "time_range": time_range,
                
                # PnL Metrics
                "today_pnl": float(latest.get("total_pnl", 0)),
                "today_return_pct": float(latest.get("pnl_pct", 0)),
                "lifetime_pnl": float(latest.get("total_pnl", 0)),
                "unrealized_pnl": float(latest.get("unrealized_pnl", 0)),
                "available_balance": float(latest.get("available_balance", 0)),
                "total_equity": float(latest.get("total_equity", 0)),
                
                # Performance Metrics
                "roi_pct": float(latest.get("pnl_pct", 0)),
                "win_rate": float(latest.get("win_rate", 0) or 0),
                "sharpe_ratio": float(latest.get("sharpe_ratio", 0) or 0),
                "sortino_ratio": float(latest.get("sortino_ratio", 0) or 0),
                "profit_factor": float(latest.get("profit_factor", 0) or 0),
                "max_drawdown": float(latest.get("max_drawdown", 0) or 0),
                
                # Trade Metrics
                "total_trades": int(latest.get("total_trades", 0) or 0),
                "winning_trades": int(latest.get("winning_trades", 0) or 0),
                "losing_trades": int(latest.get("losing_trades", 0) or 0),
                "avg_trade": float(latest.get("avg_trade_size", 0) or 0),
                "avg_win": float(latest.get("avg_win", 0) or 0),
                "avg_loss": float(latest.get("avg_loss", 0) or 0),
                
                # Position Metrics
                "position_count": int(latest.get("position_count", 0) or 0),
                "total_volume": float(latest.get("total_volume", 0) or 0),
                
                # Realtime Metrics (if available)
                "realtime": await self._get_realtime_metrics(user_id, strategy_id)
            }
            
            return metrics
            
        except Exception as e:
            logger.error(f"Error fetching performance metrics for strategy {strategy_id}: {e}")
            return self._empty_performance_metrics()
    
    async def _get_realtime_metrics(self, user_id: str, strategy_id: str) -> Dict:
        """
        Get realtime metrics for a strategy.
        
        Returns:
            Realtime metrics (CPU, memory, worker uptime, exchange latency)
        """
        try:
            telemetry = self._get_telemetry()
            
            # Query realtime metrics
            query = f"""
            SELECT
                timestamp,
                cpu_usage_pct,
                memory_usage_mb,
                worker_uptime_seconds,
                exchange_latency_ms,
                websocket_connected,
                signal_count,
                execution_count,
                error_count
            FROM strategy_realtime
            WHERE strategy_id = '{strategy_id}'
            ORDER BY timestamp DESC
            LIMIT 1
            """
            
            result = await telemetry.execute_query(query)
            
            if not result or not result.get("dataset"):
                return {}
            
            row = result["dataset"][0]
            cols = [c["name"] for c in result["columns"]]
            
            return dict(zip(cols, row))
            
        except Exception as e:
            logger.error(f"Error fetching realtime metrics for strategy {strategy_id}: {e}")
            return {}
    
    def _empty_performance_metrics(self) -> Dict:
        """Return empty performance metrics structure."""
        return {
            "strategy_id": None,
            "timestamp": None,
            "time_range": "1d",
            "today_pnl": 0.0,
            "today_return_pct": 0.0,
            "lifetime_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "available_balance": 0.0,
            "total_equity": 0.0,
            "roi_pct": 0.0,
            "win_rate": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "avg_trade": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "position_count": 0,
            "total_volume": 0.0,
            "realtime": {}
        }
    
    async def get_equity_curve(
        self,
        user_id: str,
        strategy_id: str,
        days: int = 30
    ) -> List[Dict]:
        """
        Get equity curve data for a strategy.
        
        Args:
            user_id: User ID
            strategy_id: Strategy ID
            days: Number of days of data
            
        Returns:
            List of equity curve points
        """
        try:
            telemetry = self._get_telemetry()
            
            start_time = datetime.now(timezone.utc) - timedelta(days=days)
            
            query = f"""
            SELECT
                timestamp,
                total_equity,
                total_pnl
            FROM strategy_performance
            WHERE strategy_id = '{strategy_id}'
            AND timestamp >= '{start_time.isoformat()}'
            ORDER BY timestamp ASC
            """
            
            result = await telemetry.execute_query(query)
            
            if not result or not result.get("dataset"):
                return []
            
            rows = result["dataset"]
            cols = [c["name"] for c in result["columns"]]
            
            return [dict(zip(cols, row)) for row in rows]
            
        except Exception as e:
            logger.error(f"Error fetching equity curve for strategy {strategy_id}: {e}")
            return []
    
    async def get_monthly_returns(
        self,
        user_id: str,
        strategy_id: str
    ) -> List[Dict]:
        """
        Get monthly returns for a strategy.
        
        Args:
            user_id: User ID
            strategy_id: Strategy ID
            
        Returns:
            List of monthly returns
        """
        try:
            telemetry = self._get_telemetry()
            
            query = f"""
            SELECT
                to_char(timestamp, 'YYYY-MM') as month,
                first(total_equity) as start_equity,
                last(total_equity) as end_equity,
                (last(total_equity) - first(total_equity)) / first(total_equity) * 100 as return_pct
            FROM strategy_performance
            WHERE strategy_id = '{strategy_id}'
            SAMPLE BY 1 MONTH
            ORDER BY month ASC
            """
            
            result = await telemetry.execute_query(query)
            
            if not result or not result.get("dataset"):
                return []
            
            rows = result["dataset"]
            cols = [c["name"] for c in result["columns"]]
            
            return [dict(zip(cols, row)) for row in rows]
            
        except Exception as e:
            logger.error(f"Error fetching monthly returns for strategy {strategy_id}: {e}")
            return []
    
    async def get_daily_returns(
        self,
        user_id: str,
        strategy_id: str,
        days: int = 30
    ) -> List[Dict]:
        """
        Get daily returns for a strategy.
        
        Args:
            user_id: User ID
            strategy_id: Strategy ID
            days: Number of days
            
        Returns:
            List of daily returns
        """
        try:
            telemetry = self._get_telemetry()
            
            start_time = datetime.now(timezone.utc) - timedelta(days=days)
            
            query = f"""
            SELECT
                to_char(timestamp, 'YYYY-MM-DD') as day,
                first(total_equity) as start_equity,
                last(total_equity) as end_equity,
                (last(total_equity) - first(total_equity)) / first(total_equity) * 100 as return_pct
            FROM strategy_performance
            WHERE strategy_id = '{strategy_id}'
            AND timestamp >= '{start_time.isoformat()}'
            SAMPLE BY 1 DAY
            ORDER BY day ASC
            """
            
            result = await telemetry.execute_query(query)
            
            if not result or not result.get("dataset"):
                return []
            
            rows = result["dataset"]
            cols = [c["name"] for c in result["columns"]]
            
            return [dict(zip(cols, row)) for row in rows]
            
        except Exception as e:
            logger.error(f"Error fetching daily returns for strategy {strategy_id}: {e}")
            return []
    
    async def get_execution_metrics(
        self,
        user_id: str,
        strategy_id: str,
        time_range: str = "1d"
    ) -> Dict:
        """
        Get execution metrics for a strategy.
        
        Args:
            user_id: User ID
            strategy_id: Strategy ID
            time_range: Time range
            
        Returns:
            Execution metrics (order count, signal count, latency, slippage)
        """
        try:
            telemetry = self._get_telemetry()
            
            time_range_map = {
                "1d": timedelta(days=1),
                "1w": timedelta(weeks=1),
                "1m": timedelta(days=30),
                "3m": timedelta(days=90),
                "all": timedelta(days=3650)
            }
            delta = time_range_map.get(time_range, timedelta(days=1))
            start_time = datetime.now(timezone.utc) - delta
            
            query = f"""
            SELECT
                COUNT(*) as total_orders,
                COUNT(DISTINCT signal_id) as total_signals,
                AVG(latency_ms) as avg_latency_ms,
                AVG(slippage) as avg_slippage,
                SUM(fees) as total_fees,
                SUM(filled_amount) as total_filled
            FROM executions
            WHERE strategy_id = '{strategy_id}'
            AND timestamp >= '{start_time.isoformat()}'
            """
            
            result = await telemetry.execute_query(query)
            
            if not result or not result.get("dataset"):
                return {}
            
            row = result["dataset"][0]
            cols = [c["name"] for c in result["columns"]]
            
            return dict(zip(cols, row))
            
        except Exception as e:
            logger.error(f"Error fetching execution metrics for strategy {strategy_id}: {e}")
            return {}
    
    async def get_risk_metrics(
        self,
        user_id: str,
        strategy_id: str
    ) -> Dict:
        """
        Get risk metrics for a strategy.
        
        Args:
            user_id: User ID
            strategy_id: Strategy ID
            
        Returns:
            Risk metrics (drawdown, exposure, position limits, kill switch)
        """
        try:
            telemetry = self._get_telemetry()
            
            query = f"""
            SELECT
                max_drawdown,
                current_exposure,
                exposure_limit,
                position_limit,
                position_count,
                kill_switch_active,
                circuit_breaker_triggered,
                last_circuit_breaker_time
            FROM strategy_risk
            WHERE strategy_id = '{strategy_id}'
            ORDER BY timestamp DESC
            LIMIT 1
            """
            
            result = await telemetry.execute_query(query)
            
            if not result or not result.get("dataset"):
                return {}
            
            row = result["dataset"][0]
            cols = [c["name"] for c in result["columns"]]
            
            return dict(zip(cols, row))
            
        except Exception as e:
            logger.error(f"Error fetching risk metrics for strategy {strategy_id}: {e}")
            return {}


# Singleton instance
_metrics_service = None

async def get_metrics_service() -> MetricsService:
    """Get singleton MetricsService instance."""
    global _metrics_service
    if _metrics_service is None:
        _metrics_service = MetricsService()
    return _metrics_service