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

import inspect
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
    environment: str = Query("live", regex="^(live|paper)$", description="Trading environment (live or paper)"),
    equity_days: int = Query(30, ge=1, le=365, description="Number of days for equity curve data"),
    user: dict = Depends(get_current_user)
):
    """
    Get complete dashboard data in one optimized call.
    
    This is the SINGLE SOURCE OF TRUTH for all Dashboard data.
    Replaces multiple frontend API calls with one aggregated request.
    
    Supports strict environment isolation:
    - environment=live: Aggregates real exchange balances, positions, executions, and QuestDB telemetry.
    - environment=paper: Aggregates deterministic virtual paper account, paper positions, and paper trades.
    
    Returns comprehensive dashboard data including:
    - environment: Current environment ('live' or 'paper')
    - overview: Normalized portfolio summary (total_value, total_equity, available_balance, free_balance, used_balance, today_pnl, today_realized_pnl, today_return_pct, unrealized_pnl, cumulative_pnl)
    - positions: Normalized open positions array
    - executions: Normalized recent trade executions / fills
    - subscription: Current subscription tier and billing status
    - usage: Resource usage metrics (strategies, bots, ML training)
    - strategies: Strategy information with calculated metrics
    - marketplace: Marketplace data and user publications
    - risk: Risk management metrics (risk_score, risk_level, daily loss, drawdown, circuit breaker)
    - notifications: Notification counts and recent notifications
    - referrals: Referral program data and earnings
    - health: System health metrics (real measured latency or null, sync status)
    - exchange: Exchange connection status and metrics
    - recent_activity: Recent signals, insights, and executions
    - equity_curve: Historical equity performance data
    - degraded: null when every read succeeded, else a block naming what could not be read
    
    All calculations are performed in the backend. Frontend only renders data.
    
    Rate limited: 100 requests per minute per user.

    HOW TO TELL AN EMPTY ACCOUNT FROM A BROKEN READ (BC-2, Requirement 14.5)
        `positions` is `[]` both when the account holds none and when the read failed, so the
        list alone cannot be trusted to mean either. `degraded` is the discriminator:

        * `degraded == null` — `positions` is the truth. `[]` means no open positions; render
          the empty state.
        * `degraded == {"positions": "unreadable", "environment": …, "reason": …}` — `positions`
          is `[]` because it could not be read. Render the ERROR state. Never an empty table:
          the account may well be holding positions, and this response cannot say.

        `risk.open_positions_count` carries the same distinction as a value — an `int` when
        counted, `null` when unreadable, never `0` as a stand-in — and `risk.degraded` mirrors
        the top-level block for a consumer reading only that section.

        A degraded response is not cached, so a client polling this endpoint sees the marker
        clear on the first request after the read recovers.
    """
    try:
        norm_env = environment.lower() if environment in ("live", "paper") else "live"
        # Check fast Redis cache (10 second TTL for instant sub-50ms repeat response)
        cache_key = f"dashboard:{user['id']}:{norm_env}:{equity_days}"
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            cached = await redis_manager.get(cache_key)
            if cached:
                import json
                return json.loads(cached)
        except Exception as cache_err:
            logger.debug(f"Dashboard cache read error: {cache_err}")

        dashboard_service = await get_dashboard_service()
        
        # Get complete dashboard data from aggregation service with explicit environment
        dashboard_data = await dashboard_service.get_dashboard_data(
            user=user,
            equity_days=equity_days,
            environment=norm_env
        )
        
        # Write to fast cache — but never a degraded one (BC-2). A cached `degraded` block
        # outlives the outage that produced it: the read recovers, and every request for the
        # next 10 seconds is still told the positions are unreadable. Claiming an outage that
        # has ended is the same class of defect as hiding one that has not, so a degraded
        # response is served once, to the request that observed it, and not stored.
        if dashboard_data.get("degraded") is None:
            try:
                from backend_app.core.cache.redis_manager import redis_manager
                import json
                await redis_manager.set(cache_key, json.dumps(dashboard_data), ex=10)
            except Exception as cache_write_err:
                logger.debug(f"Dashboard cache write error: {cache_write_err}")

        logger.info(f"Dashboard data fetched successfully for user {user['id']} in {norm_env} mode")
        return dashboard_data
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        
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

    Returns
        **200** ``{overview: {total_value, today_pnl, today_return_pct, unrealized_pnl,
        available_balance}}`` — every figure a real reading of the portfolio.
        **503** ``DASHBOARD_FETCH_FAILED`` when the portfolio read failed.

    WHY THIS ONE REFUSES RATHER THAN DEGRADING (BC-2, design.md §1.6, Requirement 14.5)
        This handler used to catch every exception and return ``total_value: 0.0, today_pnl:
        0.0, …``. A trader with a broken read saw a zeroed portfolio and could act on it —
        a flat account and an unreachable one are not remotely the same statement, and
        ``0.0`` asserts the first.

        BC-2 offers two dispositions and this site takes the refusal, not the ``degraded``
        marker that ``GET /api/dashboard`` takes for its positions list. The difference is
        what survives the failure: on ``/api/dashboard`` the balances, equity curve and
        executions are separate reads, so a response carrying those plus an honest marker is
        more useful than a blanket refusal. Here there is exactly ONE read, and all five
        figures on the response are headline money figures derived from it. Nothing survives
        it, so there is no partial truth for a marker to qualify — a 200 whose entire body is
        unreadable should not be a 200.

        The status and error code are ``GET /api/dashboard``'s own, deliberately: it is the
        same failure of the same read, and a client that already handles
        ``DASHBOARD_FETCH_FAILED`` needs no second branch for it.
    """
    try:
        dashboard_service = await get_dashboard_service()
        
        portfolio = await dashboard_service.get_portfolio_overview(user)
        
        return {
            "overview": {
                "total_value": float(portfolio.get("total_equity", 0)),
                "today_pnl": float(portfolio.get("daily_pnl", portfolio.get("today_pnl", 0))),
                "today_return_pct": float(portfolio.get("pnl_pct", 0)),
                "unrealized_pnl": float(portfolio.get("unrealized_pnl", 0)),
                "available_balance": float(portfolio.get("available_balance", 0))
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error fetching dashboard overview for user {user['id']}: {e}"
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "DASHBOARD_FETCH_FAILED",
                "message": "Failed to fetch dashboard data. Please try again later."
            }
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
