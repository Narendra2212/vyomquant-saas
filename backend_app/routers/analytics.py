"""
routers/analytics.py — Performance analytics from QuestDB.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query

from backend_app.core.dependencies import get_current_user, get_telemetry

router = APIRouter()
logger = logging.getLogger("AnalyticsRouter")


def _safe_uid(uid: str) -> str:
    if re.match(r"^[a-zA-Z0-9\-_]{1,128}$", str(uid)):
        return str(uid)
    raise ValueError(f"Unsafe user_id: '{uid}'")


@router.get("/performance")
async def get_performance(
    days: int = Query(30, ge=1, le=365),
    user: dict = Depends(get_current_user),
    telemetry=Depends(get_telemetry),
):
    """
    Get performance metrics for the user over a time period.
    Returns win rate, total P&L, Sharpe ratio, and other key metrics.
    """
    try:
        safe_uid = _safe_uid(user["id"])
        
        # Query executions for the period
        query = (
            "SELECT "  # nosec: B608
            "COUNT(*) as total_trades, "
            "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades, "
            "SUM(pnl) as total_pnl, "
            "AVG(pnl) as avg_pnl, "
            "STDDEV(pnl) as pnl_stddev "
            "FROM executions "
            "WHERE user_id = '" + safe_uid + "' "
            "AND timestamp > dateadd('D', -" + str(int(days)) + ", now());"
        )  # nosec: B608
        
        result = await telemetry.execute_query(query)
        
        if result and result.get("dataset"):
            cols = [c["name"] for c in result["columns"]]
            row = dict(zip(cols, result["dataset"][0]))
            
            total_trades = row.get("total_trades", 0)
            winning_trades = row.get("winning_trades", 0)
            total_pnl = row.get("total_pnl", 0) or 0
            avg_pnl = row.get("avg_pnl", 0) or 0
            pnl_stddev = row.get("pnl_stddev", 0) or 0

            if total_trades <= 0:
                return {
                    "period_days": days,
                    "total_trades": 0,
                    "winning_trades": 0,
                    "win_rate": 0,
                    "total_pnl": 0,
                    "avg_pnl": 0,
                    "sharpe_ratio": 0,
                }
            
            # Calculate derived metrics
            win_rate = winning_trades / total_trades * 100
            sharpe_ratio = (avg_pnl / pnl_stddev) if pnl_stddev > 0 else 0
            
            return {
                "period_days": days,
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "win_rate": round(win_rate, 2),
                "total_pnl": round(total_pnl, 2),
                "avg_pnl": round(avg_pnl, 2),
                "sharpe_ratio": round(sharpe_ratio, 2),
            }
        
        return {
            "period_days": days,
            "total_trades": 0,
            "winning_trades": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "avg_pnl": 0,
            "sharpe_ratio": 0,
        }
        
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.error(f"Error fetching performance metrics: {e}")
        return {
            "period_days": days,
            "total_trades": 0,
            "winning_trades": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "avg_pnl": 0,
            "sharpe_ratio": 0,
        }
