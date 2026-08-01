"""
routers/strategy_operations.py — Strategy Operations API

PHASE 2: Strategy Operations Router

Provides REST API endpoints for complete Strategy lifecycle management.
Replaces Bot Monitor with Strategy-centric architecture.

A deployed Strategy IS the running trading bot.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend_app.backend.strategy_service import get_strategy_service, StrategyStatus, StrategyEnvironment
from backend_app.backend.metrics_service import get_metrics_service
from backend_app.backend.backtest_service import get_backtest_service
from backend_app.backend.subscription_service import get_subscription_service
from backend_app.core.dependencies import get_current_user
from backend_app.core.rate_limit import limiter

router = APIRouter()
logger = logging.getLogger("StrategyOperationsRouter")


# ══════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════

class StrategyCreateRequest(BaseModel):
    """Request model for creating a Strategy."""
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., max_length=500)
    blueprint: dict = Field(..., description="Strategy DAG blueprint")
    exchange: str = Field(..., description="Exchange ID")
    symbol: str = Field(..., description="Trading pair")
    timeframe: str = Field(..., description="Timeframe")
    tags: List[str] = Field(default_factory=list)


class StrategyUpdateRequest(BaseModel):
    """Request model for updating a Strategy."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    blueprint: Optional[dict] = Field(None, description="Updated blueprint (creates new version)")
    tags: Optional[List[str]] = None


class StrategyDeployRequest(BaseModel):
    """Request model for deploying a Strategy."""
    version: Optional[str] = Field(None, description="Specific version to deploy (uses current if not specified)")
    environment: str = Field("paper", description="Deployment environment")
    exchange_id: Optional[str] = Field(None, description="Specific exchange ID")


class StrategyCloneRequest(BaseModel):
    """Request model for cloning a Strategy."""
    new_name: str = Field(..., min_length=1, max_length=100)


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY CRUD OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

@router.post("/strategies")
@limiter.limit("50/minute")
async def create_strategy(
    request: StrategyCreateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Create a new Strategy.
    
    Creates initial version v1.0 and marks as draft.
    """
    try:
        service = await get_strategy_service()
        
        result = await service.create_strategy(
            user_id=user["id"],
            name=request.name,
            description=request.description,
            blueprint=request.blueprint,
            exchange=request.exchange,
            symbol=request.symbol,
            timeframe=request.timeframe,
            tags=request.tags
        )
        
        return {
            "status": "created",
            "strategy": result["strategy"],
            "version": result["version"]
        }
    except Exception as e:
        logger.error(f"Error creating strategy for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_CREATE_FAILED", "message": str(e)}
        )


@router.get("/strategies")
@limiter.limit("200/minute")
async def list_strategies(
    status: Optional[str] = Query(None),
    environment: Optional[str] = Query(None),
    user: dict = Depends(get_current_user)
):
    """
    List all Strategies for user with optional filters.
    
    Returns strategies with deployment status and performance metrics.
    """
    try:
        service = await get_strategy_service()
        
        strategies = await service.list_strategies(
            user_id=user["id"],
            status_filter=status,
            environment_filter=environment
        )
        
        return {
            "strategies": strategies,
            "total": len(strategies)
        }
    except Exception as e:
        logger.error(f"Error listing strategies for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGIES_LIST_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}")
@limiter.limit("200/minute")
async def get_strategy(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get complete Strategy data.
    
    Returns strategy with current version, deployments, and backtests.
    """
    try:
        service = await get_strategy_service()
        
        strategy = await service.get_strategy(user_id, strategy_id)
        if not strategy:
            raise HTTPException(
                status_code=404,
                detail={"error": "STRATEGY_NOT_FOUND", "message": f"Strategy {strategy_id} not found"}
            )
        
        return strategy
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_GET_FAILED", "message": str(e)}
        )


@router.put("/strategies/{strategy_id}")
@limiter.limit("100/minute")
async def update_strategy(
    strategy_id: str,
    request: StrategyUpdateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Update a Strategy.
    
    If blueprint is updated, creates a new version.
    Otherwise updates metadata only.
    """
    try:
        service = await get_strategy_service()
        
        updates = request.dict(exclude_unset=True)
        result = await service.update_strategy(user_id, strategy_id, updates)
        
        return {
            "status": "updated",
            "strategy": result["strategy"],
            "new_version": result.get("new_version")
        }
    except Exception as e:
        logger.error(f"Error updating strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_UPDATE_FAILED", "message": str(e)}
        )


@router.delete("/strategies/{strategy_id}")
@limiter.limit("50/minute")
async def delete_strategy(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Delete a Strategy and all associated data.
    
    Stops all running deployments before deletion.
    """
    try:
        service = await get_strategy_service()
        
        success = await service.delete_strategy(user_id, strategy_id)
        if not success:
            raise HTTPException(
                status_code=404,
                detail={"error": "STRATEGY_DELETE_FAILED", "message": "Strategy not found"}
            )
        
        return {"status": "deleted", "strategy_id": strategy_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_DELETE_FAILED", "message": str(e)}
        )


@router.post("/strategies/{strategy_id}/clone")
@limiter.limit("50/minute")
async def clone_strategy(
    strategy_id: str,
    request: StrategyCloneRequest,
    user: dict = Depends(get_current_user)
):
    """
    Clone a Strategy.
    
    Creates new strategy with same blueprint and configuration.
    """
    try:
        service = await get_strategy_service()
        
        result = await service.clone_strategy(user_id, strategy_id, request.new_name)
        
        return {
            "status": "cloned",
            "strategy": result["strategy"],
            "version": result["version"]
        }
    except Exception as e:
        logger.error(f"Error cloning strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_CLONE_FAILED", "message": str(e)}
        )


# ══════════════════════════════════════════════════════════════════════════
# DEPLOYMENT OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

@router.post("/strategies/{strategy_id}/deploy")
@limiter.limit("50/minute")
async def deploy_strategy(
    strategy_id: str,
    request: StrategyDeployRequest,
    user: dict = Depends(get_current_user)
):
    """
    Deploy a Strategy (creates running bot instance).
    
    This is the equivalent of starting a bot.
    A deployed Strategy IS the running trading bot.
    """
    try:
        service = await get_strategy_service()
        
        result = await service.deploy_strategy(
            user_id=user["id"],
            strategy_id=strategy_id,
            version=request.version,
            environment=request.environment,
            exchange_id=request.exchange_id
        )
        
        return result
    except Exception as e:
        logger.error(f"Error deploying strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_DEPLOY_FAILED", "message": str(e)}
        )


@router.post("/deployments/{deployment_id}/stop")
@limiter.limit("100/minute")
async def stop_deployment(
    deployment_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Stop a running Strategy deployment.
    
    This is the equivalent of stopping a bot.
    """
    try:
        service = await get_strategy_service()
        
        success = await service.stop_deployment(user_id, deployment_id)
        if not success:
            raise HTTPException(
                status_code=404,
                detail={"error": "DEPLOYMENT_STOP_FAILED", "message": "Deployment not found"}
            )
        
        return {"status": "stopped", "deployment_id": deployment_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping deployment {deployment_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOYMENT_STOP_FAILED", "message": str(e)}
        )


@router.post("/strategies/{strategy_id}/pause")
@limiter.limit("50/minute")
async def pause_strategy(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Pause a running Strategy.
    
    Stops all deployments and marks strategy as paused.
    """
    try:
        service = await get_strategy_service()
        
        success = await service.pause_strategy(user_id, strategy_id)
        if not success:
            raise HTTPException(
                status_code=404,
                detail={"error": "STRATEGY_PAUSE_FAILED", "message": "Strategy not found"}
            )
        
        return {"status": "paused", "strategy_id": strategy_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error pausing strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_PAUSE_FAILED", "message": str(e)}
        )


@router.post("/strategies/{strategy_id}/resume")
@limiter.limit("50/minute")
async def resume_strategy(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Resume a paused Strategy.
    
    Re-deploys strategy with current version.
    """
    try:
        service = await get_strategy_service()
        
        result = await service.resume_strategy(user_id, strategy_id)
        
        return result
    except Exception as e:
        logger.error(f"Error resuming strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_RESUME_FAILED", "message": str(e)}
        )


# ══════════════════════════════════════════════════════════════════════════
# METRICS AND MONITORING
# ══════════════════════════════════════════════════════════════════════════

@router.get("/strategies/{strategy_id}/metrics")
@limiter.limit("200/minute")
async def get_strategy_metrics(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get comprehensive Strategy metrics.
    
    All calculations performed in backend.
    Returns performance, deployment, and health metrics.
    """
    try:
        service = await get_strategy_service()
        
        metrics = await service.get_strategy_metrics(user_id, strategy_id)
        
        if not metrics:
            raise HTTPException(
                status_code=404,
                detail={"error": "STRATEGY_NOT_FOUND", "message": f"Strategy {strategy_id} not found"}
            )
        
        return metrics
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting metrics for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_METRICS_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}/performance")
@limiter.limit("200/minute")
async def get_strategy_performance(
    strategy_id: str,
    time_range: str = Query("1d", description="Time range: 1d, 1w, 1m, 3m, all"),
    user: dict = Depends(get_current_user)
):
    """
    Get Strategy performance metrics from MetricsService.
    
    All calculations performed in backend.
    Returns PnL, ROI, win rate, Sharpe, Sortino, profit factor, etc.
    """
    try:
        metrics_service = await get_metrics_service()
        
        performance = await metrics_service.get_strategy_performance(
            user_id=user["id"],
            strategy_id=strategy_id,
            time_range=time_range
        )
        
        return performance
    except Exception as e:
        logger.error(f"Error getting performance for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "PERFORMANCE_FETCH_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}/equity-curve")
@limiter.limit("200/minute")
async def get_strategy_equity_curve(
    strategy_id: str,
    days: int = Query(30, ge=1, le=365, description="Number of days"),
    user: dict = Depends(get_current_user)
):
    """
    Get equity curve data for a Strategy.
    
    All calculations performed in backend.
    Returns historical equity points.
    """
    try:
        metrics_service = await get_metrics_service()
        
        equity_curve = await metrics_service.get_equity_curve(
            user_id=user["id"],
            strategy_id=strategy_id,
            days=days
        )
        
        return {
            "strategy_id": strategy_id,
            "days": days,
            "equity_curve": equity_curve
        }
    except Exception as e:
        logger.error(f"Error getting equity curve for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "EQUITY_CURVE_FETCH_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}/monthly-returns")
@limiter.limit("200/minute")
async def get_strategy_monthly_returns(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get monthly returns for a Strategy.
    
    All calculations performed in backend.
    Returns monthly return percentages.
    """
    try:
        metrics_service = await get_metrics_service()
        
        monthly_returns = await metrics_service.get_monthly_returns(
            user_id=user["id"],
            strategy_id=strategy_id
        )
        
        return {
            "strategy_id": strategy_id,
            "monthly_returns": monthly_returns
        }
    except Exception as e:
        logger.error(f"Error getting monthly returns for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "MONTHLY_RETURNS_FETCH_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}/daily-returns")
@limiter.limit("200/minute")
async def get_strategy_daily_returns(
    strategy_id: str,
    days: int = Query(30, ge=1, le=365, description="Number of days"),
    user: dict = Depends(get_current_user)
):
    """
    Get daily returns for a Strategy.
    
    All calculations performed in backend.
    Returns daily return percentages.
    """
    try:
        metrics_service = await get_metrics_service()
        
        daily_returns = await metrics_service.get_daily_returns(
            user_id=user["id"],
            strategy_id=strategy_id,
            days=days
        )
        
        return {
            "strategy_id": strategy_id,
            "days": days,
            "daily_returns": daily_returns
        }
    except Exception as e:
        logger.error(f"Error getting daily returns for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DAILY_RETURNS_FETCH_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}/execution-metrics")
@limiter.limit("200/minute")
async def get_strategy_execution_metrics(
    strategy_id: str,
    time_range: str = Query("1d", description="Time range: 1d, 1w, 1m, 3m, all"),
    user: dict = Depends(get_current_user)
):
    """
    Get execution metrics for a Strategy.
    
    All calculations performed in backend.
    Returns order count, signal count, latency, slippage, fees.
    """
    try:
        metrics_service = await get_metrics_service()
        
        execution_metrics = await metrics_service.get_execution_metrics(
            user_id=user["id"],
            strategy_id=strategy_id,
            time_range=time_range
        )
        
        return {
            "strategy_id": strategy_id,
            "time_range": time_range,
            "execution_metrics": execution_metrics
        }
    except Exception as e:
        logger.error(f"Error getting execution metrics for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "EXECUTION_METRICS_FETCH_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}/risk-metrics")
@limiter.limit("200/minute")
async def get_strategy_risk_metrics(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get risk metrics for a Strategy.
    
    All calculations performed in backend.
    Returns drawdown, exposure, position limits, kill switch status.
    """
    try:
        metrics_service = await get_metrics_service()
        
        risk_metrics = await metrics_service.get_risk_metrics(
            user_id=user["id"],
            strategy_id=strategy_id
        )
        
        return {
            "strategy_id": strategy_id,
            "risk_metrics": risk_metrics
        }
    except Exception as e:
        logger.error(f"Error getting risk metrics for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "RISK_METRICS_FETCH_FAILED", "message": str(e)}
        )


# ══════════════════════════════════════════════════════════════════════════
# BACKTEST OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

class BacktestCreateRequest(BaseModel):
    """Request model for creating a backtest."""
    version_id: str = Field(..., description="Version ID to backtest")
    version: str = Field(..., description="Version string")
    blueprint: dict = Field(..., description="Strategy blueprint")
    dataset: str = Field(..., description="Dataset to use")
    start_date: str = Field(..., description="Start date (ISO format)")
    end_date: str = Field(..., description="End date (ISO format)")
    initial_capital: float = Field(10000, ge=0)
    commission: float = Field(0.001, ge=0)
    slippage: float = Field(0.0005, ge=0)


@router.post("/strategies/{strategy_id}/backtests")
@limiter.limit("50/minute")
async def create_backtest(
    strategy_id: str,
    request: BacktestCreateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Create a new backtest for a Strategy.
    
    Every backtest is stored permanently with strategy.
    """
    try:
        backtest_service = await get_backtest_service()
        
        backtest = await backtest_service.create_backtest(
            user_id=user["id"],
            strategy_id=strategy_id,
            version_id=request.version_id,
            version=request.version,
            blueprint=request.blueprint,
            dataset=request.dataset,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_capital=request.initial_capital,
            commission=request.commission,
            slippage=request.slippage
        )
        
        return {
            "status": "created",
            "backtest": backtest
        }
    except Exception as e:
        logger.error(f"Error creating backtest for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTEST_CREATE_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}/backtests")
@limiter.limit("200/minute")
async def list_backtests(
    strategy_id: str,
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(get_current_user)
):
    """
    List all backtests for a Strategy.
    
    Every strategy owns complete backtest history.
    """
    try:
        backtest_service = await get_backtest_service()
        
        backtests = await backtest_service.list_backtests(
            user_id=user["id"],
            strategy_id=strategy_id,
            limit=limit
        )
        
        return {
            "strategy_id": strategy_id,
            "backtests": backtests,
            "total": len(backtests)
        }
    except Exception as e:
        logger.error(f"Error listing backtests for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTESTS_LIST_FAILED", "message": str(e)}
        )


@router.get("/backtests/{backtest_id}")
@limiter.limit("200/minute")
async def get_backtest(
    backtest_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get complete backtest data.
    """
    try:
        backtest_service = await get_backtest_service()
        
        backtest = await backtest_service.get_backtest(user_id, backtest_id)
        if not backtest:
            raise HTTPException(
                status_code=404,
                detail={"error": "BACKTEST_NOT_FOUND", "message": f"Backtest {backtest_id} not found"}
            )
        
        return backtest
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting backtest {backtest_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTEST_GET_FAILED", "message": str(e)}
        )


@router.get("/backtests/{backtest_id}/report")
@limiter.limit("200/minute")
async def get_backtest_report(
    backtest_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get comprehensive backtest report.
    
    Returns complete report with all metrics, equity curve, and analysis.
    """
    try:
        backtest_service = await get_backtest_service()
        
        report = await backtest_service.get_backtest_report(user_id, backtest_id)
        if not report:
            raise HTTPException(
                status_code=404,
                detail={"error": "BACKTEST_NOT_FOUND", "message": f"Backtest {backtest_id} not found"}
            )
        
        return report
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting backtest report {backtest_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTEST_REPORT_FAILED", "message": str(e)}
        )


@router.post("/backtests/compare")
@limiter.limit("50/minute")
async def compare_backtests(
    backtest_ids: List[str],
    user: dict = Depends(get_current_user)
):
    """
    Compare multiple backtests.
    
    Returns side-by-side comparison of metrics.
    """
    try:
        backtest_service = await get_backtest_service()
        
        comparison = await backtest_service.compare_backtests(user_id, backtest_ids)
        
        return comparison
    except Exception as e:
        logger.error(f"Error comparing backtests for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTEST_COMPARE_FAILED", "message": str(e)}
        )


@router.delete("/backtests/{backtest_id}")
@limiter.limit("50/minute")
async def delete_backtest(
    backtest_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Delete a backtest.
    """
    try:
        backtest_service = await get_backtest_service()
        
        success = await backtest_service.delete_backtest(user_id, backtest_id)
        if not success:
            raise HTTPException(
                status_code=404,
                detail={"error": "BACKTEST_DELETE_FAILED", "message": "Backtest not found"}
            )
        
        return {"status": "deleted", "backtest_id": backtest_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting backtest {backtest_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTEST_DELETE_FAILED", "message": str(e)}
        )


# ══════════════════════════════════════════════════════════════════════════
# VERSION CONTROL OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

@router.get("/strategies/{strategy_id}/versions")
@limiter.limit("200/minute")
async def get_version_history(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get complete version history for a Strategy.
    
    Returns all versions with metadata.
    """
    try:
        service = await get_strategy_service()
        
        versions = await service.get_version_history(user_id, strategy_id)
        
        return {
            "strategy_id": strategy_id,
            "versions": versions,
            "total": len(versions)
        }
    except Exception as e:
        logger.error(f"Error getting version history for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "VERSION_HISTORY_FAILED", "message": str(e)}
        )


@router.post("/strategies/{strategy_id}/versions/compare")
@limiter.limit("100/minute")
async def compare_versions(
    strategy_id: str,
    version_a: str = Query(..., description="First version to compare"),
    version_b: str = Query(..., description="Second version to compare"),
    user: dict = Depends(get_current_user)
):
    """
    Compare two strategy versions.
    
    Returns comparison of blueprints and metadata.
    """
    try:
        service = await get_strategy_service()
        
        comparison = await service.compare_versions(user_id, strategy_id, version_a, version_b)
        
        return comparison
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={"error": "VERSION_NOT_FOUND", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error comparing versions for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "VERSION_COMPARE_FAILED", "message": str(e)}
        )


@router.post("/strategies/{strategy_id}/versions/restore")
@limiter.limit("50/minute")
async def restore_version(
    strategy_id: str,
    version: str = Query(..., description="Version to restore"),
    user: dict = Depends(get_current_user)
):
    """
    Restore a previous version as current.
    
    Creates a new version based on the restored version.
    """
    try:
        service = await get_strategy_service()
        
        new_version = await service.restore_version(user_id, strategy_id, version)
        
        return {
            "status": "restored",
            "restored_from": version,
            "new_version": new_version
        }
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={"error": "VERSION_NOT_FOUND", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error restoring version {version} for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "VERSION_RESTORE_FAILED", "message": str(e)}
        )


@router.post("/strategies/{strategy_id}/versions/{version}/deploy")
@limiter.limit("50/minute")
async def deploy_version(
    strategy_id: str,
    version: str,
    environment: str = Query("paper", description="Deployment environment"),
    user: dict = Depends(get_current_user)
):
    """
    Deploy a specific version of a Strategy.
    
    Deployment always references an immutable version.
    """
    try:
        service = await get_strategy_service()
        
        deployment = await service.deploy_version(user_id, strategy_id, version, environment)
        
        return deployment
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={"error": "VERSION_NOT_FOUND", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error deploying version {version} for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "VERSION_DEPLOY_FAILED", "message": str(e)}
        )


# ══════════════════════════════════════════════════════════════════════════
# MARKETPLACE INTEGRATION
# ══════════════════════════════════════════════════════════════════════════

class MarketplacePublishRequest(BaseModel):
    """Request model for publishing to marketplace."""
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., max_length=500)
    pricing: dict = Field(default_factory=dict)
    category: str = Field("custom")
    tags: List[str] = Field(default_factory=list)
    visibility: str = Field("public")


@router.post("/strategies/{strategy_id}/marketplace/publish")
@limiter.limit("50/minute")
async def publish_to_marketplace(
    strategy_id: str,
    request: MarketplacePublishRequest,
    user: dict = Depends(get_current_user)
):
    """
    Publish a Strategy to Marketplace.
    
    Users publish directly from Strategies.
    """
    try:
        service = await get_strategy_service()
        
        result = await service.publish_to_marketplace(
            user_id=user["id"],
            strategy_id=strategy_id,
            marketplace_data=request.dict()
        )
        
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "PUBLISH_FAILED", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error publishing strategy {strategy_id} to marketplace for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "PUBLISH_FAILED", "message": str(e)}
        )


@router.put("/strategies/{strategy_id}/marketplace")
@limiter.limit("50/minute")
async def update_marketplace_listing(
    strategy_id: str,
    request: MarketplacePublishRequest,
    user: dict = Depends(get_current_user)
):
    """
    Update Marketplace listing for a Strategy.
    """
    try:
        service = await get_strategy_service()
        
        result = await service.update_marketplace_listing(
            user_id=user["id"],
            strategy_id=strategy_id,
            marketplace_data=request.dict()
        )
        
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "UPDATE_FAILED", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error updating marketplace listing for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "UPDATE_FAILED", "message": str(e)}
        )


@router.delete("/strategies/{strategy_id}/marketplace")
@limiter.limit("50/minute")
async def unpublish_from_marketplace(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Unpublish a Strategy from Marketplace.
    """
    try:
        service = await get_strategy_service()
        
        success = await service.unpublish_from_marketplace(user_id, strategy_id)
        if not success:
            raise HTTPException(
                status_code=404,
                detail={"error": "UNPUBLISH_FAILED", "message": "Strategy not published"}
            )
        
        return {"status": "unpublished", "strategy_id": strategy_id}
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "UNPUBLISH_FAILED", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error unpublishing strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "UNPUBLISH_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}/marketplace/status")
@limiter.limit("200/minute")
async def get_marketplace_status(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get Marketplace status for a Strategy.
    
    Returns publication status and listing data.
    """
    try:
        service = await get_strategy_service()
        
        status = await service.get_marketplace_status(user_id, strategy_id)
        
        return status
    except Exception as e:
        logger.error(f"Error getting marketplace status for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STATUS_FETCH_FAILED", "message": str(e)}
        )


# ══════════════════════════════════════════════════════════════════════════
# SUBSCRIPTION OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

class SubscriptionCreateRequest(BaseModel):
    """Request model for subscribing to marketplace strategy."""
    marketplace_listing_id: str = Field(..., description="Marketplace listing ID")
    configuration: Optional[dict] = Field(default_factory=dict, description="User configuration")


class SubscriptionUpdateRequest(BaseModel):
    """Request model for updating subscription configuration."""
    configuration: dict = Field(..., description="Updated configuration")


@router.post("/marketplace/subscribe")
@limiter.limit("50/minute")
async def subscribe_to_strategy(
    request: SubscriptionCreateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Subscribe to a marketplace strategy.
    
    Creates a read-only copy of the strategy in user's strategies.
    """
    try:
        subscription_service = await get_subscription_service()
        
        result = await subscription_service.subscribe_to_strategy(
            user_id=user["id"],
            marketplace_listing_id=request.marketplace_listing_id,
            configuration=request.configuration
        )
        
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "SUBSCRIBE_FAILED", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error subscribing for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SUBSCRIBE_FAILED", "message": str(e)}
        )


@router.delete("/subscriptions/{subscription_id}")
@limiter.limit("50/minute")
async def unsubscribe_from_strategy(
    subscription_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Unsubscribe from a marketplace strategy.
    """
    try:
        subscription_service = await get_subscription_service()
        
        success = await subscription_service.unsubscribe_from_strategy(user_id, subscription_id)
        if not success:
            raise HTTPException(
                status_code=404,
                detail={"error": "UNSUBSCRIBE_FAILED", "message": "Subscription not found"}
            )
        
        return {"status": "unsubscribed", "subscription_id": subscription_id}
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "UNSUBSCRIBE_FAILED", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error unsubscribing for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "UNSUBSCRIBE_FAILED", "message": str(e)}
        )


@router.get("/subscriptions")
@limiter.limit("200/minute")
async def list_subscriptions(
    user: dict = Depends(get_current_user)
):
    """
    List all user's marketplace subscriptions.
    """
    try:
        subscription_service = await get_subscription_service()
        
        subscriptions = await subscription_service.list_subscriptions(user_id)
        
        return {
            "subscriptions": subscriptions,
            "total": len(subscriptions)
        }
    except Exception as e:
        logger.error(f"Error listing subscriptions for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SUBSCRIPTIONS_LIST_FAILED", "message": str(e)}
        )


@router.get("/subscriptions/{subscription_id}")
@limiter.limit("200/minute")
async def get_subscription(
    subscription_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get subscription details.
    """
    try:
        subscription_service = await get_subscription_service()
        
        subscription = await subscription_service.get_subscription(user_id, subscription_id)
        if not subscription:
            raise HTTPException(
                status_code=404,
                detail={"error": "SUBSCRIPTION_NOT_FOUND", "message": "Subscription not found"}
            )
        
        return subscription
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting subscription {subscription_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SUBSCRIPTION_GET_FAILED", "message": str(e)}
        )


@router.put("/subscriptions/{subscription_id}/configuration")
@limiter.limit("100/minute")
async def update_subscription_configuration(
    subscription_id: str,
    request: SubscriptionUpdateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Update subscription configuration.
    
    Users can configure parameters and exchange settings
    without modifying the protected strategy logic.
    """
    try:
        subscription_service = await get_subscription_service()
        
        result = await subscription_service.update_subscription_configuration(
            user_id,
            subscription_id,
            request.configuration
        )
        
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "CONFIG_UPDATE_FAILED", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error updating configuration for subscription {subscription_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "CONFIG_UPDATE_FAILED", "message": str(e)}
        )


@router.post("/subscriptions/{subscription_id}/clone")
@limiter.limit("50/minute")
async def clone_subscribed_strategy(
    subscription_id: str,
    new_name: str = Query(..., description="Name for cloned strategy"),
    user: dict = Depends(get_current_user)
):
    """
    Clone a subscribed strategy (if publisher allows).
    
    Creates a new owned strategy based on the subscribed one.
    """
    try:
        subscription_service = await get_subscription_service()
        
        result = await subscription_service.clone_subscribed_strategy(user_id, subscription_id, new_name)
        
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "CLONE_FAILED", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error cloning subscription {subscription_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "CLONE_FAILED", "message": str(e)}
        )