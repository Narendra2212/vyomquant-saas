"""
routers/dashboard.py — Dashboard Aggregation API

PHASE 4: Implement GET /api/dashboard endpoint

This router provides the single optimized endpoint that replaces multiple
frontend API calls. All dashboard data is aggregated by DashboardAggregationService.

Enterprise-grade aggregation:
- Single source of truth for all dashboard data
- All calculations performed in backend
- Frontend becomes presentation-only
- Parallel data fetching for optimal performance
- Comprehensive error handling and fallbacks
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException, Request

from backend_app.backend.dashboard_aggregation_service import get_dashboard_service
from backend_app.core.dependencies import get_current_user
from backend_app.core.rate_limit import limiter

router = APIRouter()
logger = logging.getLogger("DashboardRouter")


@router.get("/dashboard")
@limiter.limit("100/minute")
async def get_dashboard(
    request: Request,
    equity_days: int = Query(30, ge=1, le=365, description="Number of days for equity curve data"),
    user: dict = Depends(get_current_user)
):
    """
    Get complete dashboard data in one optimized call.
    
    This is the SINGLE SOURCE OF TRUTH for all Dashboard data.
    Replaces multiple frontend API calls with one aggregated request.
    
    Returns comprehensive dashboard data including:
    - overview: Portfolio summary (total_value, today_pnl, today_return_pct, unrealized_pnl, available_balance)
    - subscription: Current subscription tier and billing status
    - usage: Resource usage metrics (strategies, bots, ML training)
    - bots: Bot status (running, stopped, total)
    - strategies: Strategy information with calculated metrics
    - marketplace: Marketplace data and user publications
    - risk: Risk management metrics and circuit breaker status
    - notifications: Notification counts and recent notifications
    - referrals: Referral program data and earnings
    - health: System health metrics (latency, sync status)
    - exchange: Exchange connection status and metrics
    - recent_activity: Recent signals and trading insights
    - equity_curve: Historical equity performance data
    
    All calculations are performed in the backend. Frontend only renders data.
    
    Rate limited: 100 requests per minute per user.
    """
    try:
        dashboard_service = await get_dashboard_service()
        
        # Get complete dashboard data from aggregation service
        dashboard_data = await dashboard_service.get_dashboard_data(
            user=user,
            equity_days=equity_days
        )
        
        logger.info(f"Dashboard data fetched successfully for user {user['id']}")
        return dashboard_data
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        import inspect
        
        # Safe diagnostic logging - no sensitive data
        exc_type = type(e).__name__
        exc_module = type(e).__module__
        exc_message = str(e)
        
        # Get caller info
        frame = inspect.currentframe()
        caller_filename = frame.f_back.f_code.co_filename if frame.f_back else "unknown"
        caller_lineno = frame.f_back.f_lineno if frame.f_back else 0
        
        # Log comprehensive diagnostic info
        logger.error(
            f"[DASHBOARD_ENDPOINT] Exception details: "
            f"endpoint=/api/dashboard, "
            f"exception_type={exc_type}, "
            f"exception_module={exc_module}, "
            f"exception_message={exc_message}, "
            f"caller_file={caller_filename}, "
            f"caller_line={caller_lineno}, "
            f"equity_days={equity_days}, "
            f"user_id_truncated={user['id'][:8] if user.get('id') else 'missing'}..."
        )
        
        # Log full traceback for debugging
        logger.error(f"[DASHBOARD_ENDPOINT] Full traceback:\n{traceback.format_exc()}")
        
        raise HTTPException(
            status_code=503,
            detail={
                "error": "DASHBOARD_FETCH_FAILED",
                "message": "Failed to fetch dashboard data. Please try again later."
            }
        )


@router.get("/dashboard/overview")
@limiter.limit("200/minute")
async def get_dashboard_overview(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """
    Get dashboard overview data only (portfolio summary).
    
    Lightweight endpoint for quick overview updates.
    """
    try:
        dashboard_service = await get_dashboard_service()
        
        portfolio = await dashboard_service.get_portfolio_overview(user)
        
        return {
            "overview": {
                "total_value": float(portfolio.get("total_equity", 0)),
                "today_pnl": float(portfolio.get("total_pnl", 0)),
                "today_return_pct": float(portfolio.get("pnl_pct", 0)),
                "unrealized_pnl": float(portfolio.get("total_pnl", 0)),
                "available_balance": float(portfolio.get("available_balance", 0))
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching dashboard overview for user {user['id']}: {e}")
        raise HTTPException(
            status_code=503,
            detail={"error": "OVERVIEW_FETCH_FAILED", "message": str(e)}
        )


@router.get("/dashboard/strategies")
@limiter.limit("200/minute")
async def get_dashboard_strategies(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """
    Get dashboard strategies data only.
    
    Lightweight endpoint for strategy updates.
    """
    try:
        dashboard_service = await get_dashboard_service()
        
        strategies = await dashboard_service.get_strategies(user)
        insights = await dashboard_service.get_strategy_insights(user)
        
        active_strategies = [s for s in strategies if s["status"] == "active"]
        paused_strategies = [s for s in strategies if s["status"] == "paused"]
        
        return {
            "strategies": {
                "total": len(strategies),
                "active": len(active_strategies),
                "paused": len(paused_strategies),
                "items": strategies
            },
            "insights": insights
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching dashboard strategies for user {user['id']}: {e}")
        raise HTTPException(
            status_code=503,
            detail={"error": "STRATEGIES_FETCH_FAILED", "message": str(e)}
        )
