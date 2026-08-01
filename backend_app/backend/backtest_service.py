"""
backend/backtest_service.py — Backtest Service

PHASE 5: Complete Backtest History and Reporting

Every strategy owns complete backtest history.
Store forever.
Display comprehensive backtest reports.

Provides:
- Backtest execution and storage
- Backtest history retrieval
- Detailed backtest reporting
- Backtest comparison
- Backtest metrics calculation
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from backend_app.core.dependencies import create_request_supabase

logger = logging.getLogger("BacktestService")


class BacktestService:
    """
    Central service for all backtest operations.
    
    Every backtest is stored permanently with strategy.
    Complete history tracking and reporting.
    """
    
    def __init__(self):
        pass
    
    def _get_supabase(self, user: dict):
        """Get Supabase client for user."""
        return create_request_supabase(user.get("access_token"))
    
    async def create_backtest(
        self,
        user_id: str,
        strategy_id: str,
        version_id: str,
        version: str,
        blueprint: dict,
        dataset: str,
        start_date: str,
        end_date: str,
        initial_capital: float,
        commission: float,
        slippage: float
    ) -> Dict:
        """
        Create a new backtest record.
        
        Args:
            user_id: User ID
            strategy_id: Strategy ID
            version_id: Version ID
            version: Version string
            blueprint: Strategy blueprint
            dataset: Dataset used
            start_date: Backtest start date
            end_date: Backtest end date
            initial_capital: Initial capital
            commission: Commission rate
            slippage: Slippage rate
            
        Returns:
            Backtest record
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        backtest_id = str(uuid4())
        
        backtest_data = {
            "id": backtest_id,
            "strategy_id": strategy_id,
            "user_id": user_id,
            "version_id": version_id,
            "version": version,
            "blueprint": blueprint,
            "dataset": dataset,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            "commission": commission,
            "slippage": slippage,
            "status": "running",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        result = sb.table("strategy_backtests").insert(backtest_data).execute()
        
        logger.info(f"Created backtest {backtest_id} for strategy {strategy_id}")
        
        return result.data[0] if result.data else backtest_data
    
    async def update_backtest_results(
        self,
        user_id: str,
        backtest_id: str,
        results: Dict
    ) -> Dict:
        """
        Update backtest with execution results.
        
        Args:
            user_id: User ID
            backtest_id: Backtest ID
            results: Backtest results dictionary
            
        Returns:
            Updated backtest record
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        update_data = {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "total_return": results.get("total_return", 0),
            "total_return_pct": results.get("total_return_pct", 0),
            "win_rate": results.get("win_rate", 0),
            "max_drawdown": results.get("max_drawdown", 0),
            "sharpe_ratio": results.get("sharpe_ratio", 0),
            "sortino_ratio": results.get("sortino_ratio", 0),
            "profit_factor": results.get("profit_factor", 0),
            "total_trades": results.get("total_trades", 0),
            "winning_trades": results.get("winning_trades", 0),
            "losing_trades": results.get("losing_trades", 0),
            "equity_curve": results.get("equity_curve", []),
            "monthly_returns": results.get("monthly_returns", []),
            "daily_returns": results.get("daily_returns", []),
            "execution_time_seconds": results.get("execution_time_seconds", 0),
            "final_capital": results.get("final_capital", 0)
        }
        
        result = sb.table("strategy_backtests").update(update_data).eq("id", backtest_id).execute()
        
        logger.info(f"Updated backtest {backtest_id} with results")
        
        return result.data[0] if result.data else {}
    
    async def get_backtest(
        self,
        user_id: str,
        backtest_id: str
    ) -> Optional[Dict]:
        """
        Get complete backtest data.
        
        Args:
            user_id: User ID
            backtest_id: Backtest ID
            
        Returns:
            Backtest record with results
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        result = sb.table("strategy_backtests").select("*").eq("id", backtest_id).eq("user_id", user_id).execute()
        
        if not result.data:
            return None
        
        return result.data[0]
    
    async def list_backtests(
        self,
        user_id: str,
        strategy_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict]:
        """
        List backtests for user or strategy.
        
        Args:
            user_id: User ID
            strategy_id: Optional strategy filter
            limit: Maximum number of results
            
        Returns:
            List of backtest records
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        query = sb.table("strategy_backtests").select("*").eq("user_id", user_id)
        
        if strategy_id:
            query = query.eq("strategy_id", strategy_id)
        
        result = query.order("created_at", desc=True).limit(limit).execute()
        
        return result.data or []
    
    async def get_backtest_history(
        self,
        user_id: str,
        strategy_id: str
    ) -> List[Dict]:
        """
        Get complete backtest history for a strategy.
        
        Every strategy owns complete backtest history.
        Store forever.
        
        Args:
            user_id: User ID
            strategy_id: Strategy ID
            
        Returns:
            All backtests for strategy
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        result = (sb.table("strategy_backtests")
                 .select("*")
                 .eq("strategy_id", strategy_id)
                 .eq("user_id", user_id)
                 .order("created_at", desc=True)
                 .execute())
        
        return result.data or []
    
    async def compare_backtests(
        self,
        user_id: str,
        backtest_ids: List[str]
    ) -> Dict:
        """
        Compare multiple backtests.
        
        Args:
            user_id: User ID
            backtest_ids: List of backtest IDs to compare
            
        Returns:
            Comparison results
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        backtests = []
        for backtest_id in backtest_ids:
            result = sb.table("strategy_backtests").select("*").eq("id", backtest_id).eq("user_id", user_id).execute()
            if result.data:
                backtests.append(result.data[0])
        
        # Generate comparison
        comparison = {
            "backtest_ids": backtest_ids,
            "backtests": backtests,
            "comparison": {
                "total_return": {b["id"]: b.get("total_return_pct", 0) for b in backtests},
                "win_rate": {b["id"]: b.get("win_rate", 0) for b in backtests},
                "max_drawdown": {b["id"]: b.get("max_drawdown", 0) for b in backtests},
                "sharpe_ratio": {b["id"]: b.get("sharpe_ratio", 0) for b in backtests},
                "sortino_ratio": {b["id"]: b.get("sortino_ratio", 0) for b in backtests},
                "profit_factor": {b["id"]: b.get("profit_factor", 0) for b in backtests},
                "total_trades": {b["id"]: b.get("total_trades", 0) for b in backtests}
            }
        }
        
        return comparison
    
    async def delete_backtest(
        self,
        user_id: str,
        backtest_id: str
    ) -> bool:
        """
        Delete a backtest.
        
        Args:
            user_id: User ID
            backtest_id: Backtest ID
            
        Returns:
            Success status
        """
        sb = self._get_supabase({"id": user_id, "access_token": None})
        
        result = sb.table("strategy_backtests").delete().eq("id", backtest_id).eq("user_id", user_id).execute()
        
        logger.info(f"Deleted backtest {backtest_id}")
        return True
    
    async def get_backtest_report(
        self,
        user_id: str,
        backtest_id: str
    ) -> Dict:
        """
        Generate comprehensive backtest report.
        
        Returns:
            Complete backtest report with all metrics
        """
        backtest = await self.get_backtest(user_id, backtest_id)
        if not backtest:
            return {}
        
        report = {
            "backtest_id": backtest_id,
            "strategy_id": backtest["strategy_id"],
            "version": backtest["version"],
            "status": backtest["status"],
            "created_at": backtest["created_at"],
            "completed_at": backtest.get("completed_at"),
            
            # Parameters
            "parameters": {
                "dataset": backtest["dataset"],
                "start_date": backtest["start_date"],
                "end_date": backtest["end_date"],
                "initial_capital": backtest["initial_capital"],
                "commission": backtest["commission"],
                "slippage": backtest["slippage"]
            },
            
            # Performance Metrics
            "performance": {
                "total_return": backtest.get("total_return", 0),
                "total_return_pct": backtest.get("total_return_pct", 0),
                "final_capital": backtest.get("final_capital", 0),
                "win_rate": backtest.get("win_rate", 0),
                "max_drawdown": backtest.get("max_drawdown", 0),
                "sharpe_ratio": backtest.get("sharpe_ratio", 0),
                "sortino_ratio": backtest.get("sortino_ratio", 0),
                "profit_factor": backtest.get("profit_factor", 0)
            },
            
            # Trade Metrics
            "trades": {
                "total_trades": backtest.get("total_trades", 0),
                "winning_trades": backtest.get("winning_trades", 0),
                "losing_trades": backtest.get("losing_trades", 0)
            },
            
            # Execution
            "execution": {
                "execution_time_seconds": backtest.get("execution_time_seconds", 0)
            },
            
            # Data
            "equity_curve": backtest.get("equity_curve", []),
            "monthly_returns": backtest.get("monthly_returns", []),
            "daily_returns": backtest.get("daily_returns", [])
        }
        
        return report


# Singleton instance
_backtest_service = None

async def get_backtest_service() -> BacktestService:
    """Get singleton BacktestService instance."""
    global _backtest_service
    if _backtest_service is None:
        _backtest_service = BacktestService()
    return _backtest_service