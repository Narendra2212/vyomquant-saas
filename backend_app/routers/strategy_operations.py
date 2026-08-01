"""
routers/strategy_operations.py — Strategy Operations API

PHASE 2: Strategy Operations Router

Provides REST API endpoints for complete Strategy lifecycle management.
Replaces Bot Monitor with Strategy-centric architecture.

A deployed Strategy IS the running trading bot.
"""

import logging
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend_app.backend.strategy_service import get_strategy_service, StrategyStatus, StrategyEnvironment
from backend_app.backend.metrics_service import get_metrics_service
from backend_app.backend.backtest_service import get_backtest_service
# from backend_app.backend.subscription_service import get_subscription_service  # DEPRECATED: Use library.py for subscriptions
from backend_app.backend.strategy_compiler import get_compiler, CompilerError, ValidationError
from backend_app.backend.backtest_runtime import get_backtest_runtime
from backend_app.backend.optimization_engine import get_optimization_engine, OptimizationConfig, OptimizationMethod, ValidationMethod
from backend_app.backend.deployment_manager import get_deployment_manager, DeploymentConfig as DeployConfig, DeploymentEnvironment
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


class StrategyCompileRequest(BaseModel):
    """Request model for compiling a Strategy."""
    blueprint: dict = Field(..., description="Strategy DAG blueprint")
    version: str = Field(..., description="Version to compile")
    metadata: Optional[dict] = Field(None, description="Optional metadata")


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
    Compiles the blueprint into an execution graph.
    """
    try:
        service = await get_strategy_service()
        compiler = get_compiler()
        
        # Compile blueprint before creating strategy
        from backend_app.core.models.pydantic_models import DAGConfig
        dag_config = DAGConfig(
            nodes=request.blueprint.get("nodes", []),
            edges=request.blueprint.get("edges", []),
            symbols=request.blueprint.get("symbols", [request.symbol]),
            timeframe=request.timeframe
        )
        
        package = compiler.compile(
            dag_config=dag_config,
            strategy_id=str(uuid4()),  # Generate unique ID
            version="v1.0",
            metadata={"name": request.name}
        )
        
        result = await service.create_strategy(
            user_id=user["id"],
            name=request.name,
            description=request.description,
            blueprint=request.blueprint,
            execution_graph=package.execution_graph.to_dict(),
            exchange=request.exchange,
            symbol=request.symbol,
            timeframe=request.timeframe,
            tags=request.tags
        )
        
        return {
            "status": "created",
            "strategy": result["strategy"],
            "version": result["version"],
            "execution_graph": package.execution_graph.to_dict()
        }
    except ValidationError as e:
        logger.error(f"Validation failed for strategy creation: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": "STRATEGY_VALIDATION_FAILED", "message": str(e)}
        )
    except CompilerError as e:
        logger.error(f"Compilation failed for strategy creation: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": "STRATEGY_COMPILATION_FAILED", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error creating strategy for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_CREATE_FAILED", "message": str(e)}
        )


@router.post("/strategies/compile")
@limiter.limit("100/minute")
async def compile_strategy(
    request: StrategyCompileRequest,
    user: dict = Depends(get_current_user)
):
    """
    Compile a strategy blueprint into an execution graph.
    
    PHASE G: Compiler-based validation workflow.
    Does not create or update strategy - only validates and compiles.
    """
    try:
        compiler = get_compiler()
        
        from backend_app.core.models.pydantic_models import DAGConfig
        dag_config = DAGConfig(
            nodes=request.blueprint.get("nodes", []),
            edges=request.blueprint.get("edges", []),
            symbols=request.blueprint.get("symbols", []),
            timeframe=request.blueprint.get("timeframe")
        )
        
        package = compiler.compile(
            dag_config=dag_config,
            strategy_id=str(uuid4()),  # Generate unique ID
            version=request.version,
            metadata=request.metadata or {}
        )
        
        return {
            "status": "compiled",
            "execution_graph": package.execution_graph.to_dict(),
            "dependencies": package.dependencies,
            "compiled_at": package.compiled_at
        }
    except ValidationError as e:
        logger.error(f"Validation failed: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": "STRATEGY_VALIDATION_FAILED", "message": str(e)}
        )
    except CompilerError as e:
        logger.error(f"Compilation failed: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": "STRATEGY_COMPILATION_FAILED", "message": str(e)}
        )
    except Exception as e:
        logger.error(f"Error compiling strategy: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "COMPILE_FAILED", "message": str(e)}
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


class BacktestExecuteRequest(BaseModel):
    """Request model for executing a backtest using Strategy Package."""
    execution_graph: dict = Field(..., description="Execution graph from compiler")
    start_date: str = Field(..., description="Start date (ISO format)")
    end_date: str = Field(..., description="End date (ISO format)")
    initial_capital: float = Field(10000, ge=0)
    commission: float = Field(0.001, ge=0)
    slippage: float = Field(0.0005, ge=0)
    risk_per_trade: float = Field(0.01, ge=0, le=1)
    max_drawdown: float = Field(0.2, ge=0, le=1)
    daily_loss_limit: float = Field(0.05, ge=0, le=1)


class OptimizationRequest(BaseModel):
    """Request model for running optimization."""
    execution_graph: dict = Field(..., description="Execution graph from compiler")
    optimization_method: str = Field("grid_search", description="Optimization method")
    validation_method: str = Field("time_series_cv", description="Validation method")
    parameters: dict = Field(..., description="Parameter space to search")
    n_iterations: int = Field(50, ge=1, le=1000)
    n_trials: int = Field(10, ge=1, le=100)
    training_window_days: int = Field(180, ge=30)
    validation_window_days: int = Field(30, ge=7)
    test_window_days: int = Field(30, ge=7)
    start_date: str = Field(..., description="Start date (ISO format)")
    end_date: str = Field(..., description="End date (ISO format)")
    initial_capital: float = Field(10000, ge=0)
    commission: float = Field(0.001, ge=0)
    slippage: float = Field(0.0005, ge=0)
    risk_per_trade: float = Field(0.01, ge=0, le=1)
    max_drawdown: float = Field(0.2, ge=0, le=1)
    daily_loss_limit: float = Field(0.05, ge=0, le=1)
    run_walk_forward: bool = Field(True, description="Run walk-forward analysis")
    run_monte_carlo: bool = Field(True, description="Run Monte Carlo simulation")
    n_monte_carlo: int = Field(100, ge=10, le=1000)
    run_sensitivity: bool = Field(True, description="Run sensitivity analysis")
    run_benchmark: bool = Field(True, description="Run benchmark comparison")


class DeploymentRequest(BaseModel):
    """Request model for deploying a strategy."""
    execution_graph: dict = Field(..., description="Execution graph from compiler")
    environment: str = Field("paper", description="Deployment environment")
    exchange_id: Optional[str] = Field(None, description="Exchange ID")
    exchange_symbol: str = Field(..., description="Trading pair")
    worker_region: str = Field("us-east-1", description="Worker region")
    initial_capital: float = Field(10000, ge=0)
    risk_per_trade: float = Field(0.01, ge=0, le=1)
    max_drawdown: float = Field(0.2, ge=0, le=1)
    daily_loss_limit: float = Field(0.05, ge=0, le=1)


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


@router.post("/strategies/{strategy_id}/backtests/execute")
@limiter.limit("10/minute")
async def execute_backtest(
    strategy_id: str,
    request: BacktestExecuteRequest,
    user: dict = Depends(get_current_user)
):
    """
    Execute a backtest using Strategy Package from compiler.
    
    PHASE Backtesting Engine: Executes ONLY the Strategy Package produced
    by the Strategy Compiler. Uses the same DAG Engine execution pipeline
    as live trading.
    
    Workflow:
    1. Accepts Execution Graph from compiler
    2. Reconstructs Strategy Package
    3. Executes using DAG Engine (same as live trading)
    4. Simulates using VectorBT
    5. Applies risk limits from Risk Engine
    6. Stores results via BacktestService
    """
    try:
        # Get strategy to retrieve version info
        strategy_service = await get_strategy_service()
        strategy = await strategy_service.get_strategy(user["id"], strategy_id)
        
        if not strategy:
            raise HTTPException(
                status_code=404,
                detail={"error": "STRATEGY_NOT_FOUND", "message": f"Strategy {strategy_id} not found"}
            )
        
        # Reconstruct Strategy Package from execution graph
        from backend_app.backend.strategy_compiler import StrategyPackage, ExecutionGraph
        
        execution_graph = ExecutionGraph(
            id=request.execution_graph.get("id", str(uuid4())),
            version=request.execution_graph.get("version", "v1.0"),
            nodes=request.execution_graph.get("nodes", []),
            edges=request.execution_graph.get("edges", []),
            execution_order=request.execution_graph.get("execution_order", []),
            metadata=request.execution_graph.get("metadata", {})
        )
        
        strategy_package = StrategyPackage(
            id=str(uuid4()),
            strategy_id=strategy_id,
            version=strategy["version"]["version"] if strategy["version"] else "v1.0",
            execution_graph=execution_graph,
            metadata=execution_graph.metadata,
            dependencies=request.execution_graph.get("dependencies", {})
        )
        
        # Get backtest runtime
        backtest_runtime = get_backtest_runtime()
        
        # Inject exchange instance for data fetching
        # In production, this would come from the user's configured exchange
        import ccxt
        exchange_instance = ccxt.binance()  # Default to Binance for now
        
        # Execute backtest
        results = await backtest_runtime.run_backtest(
            strategy_package=strategy_package,
            user_id=user["id"],
            strategy_id=strategy_id,
            version_id=strategy["version"]["id"] if strategy["version"] else None,
            version=strategy["version"]["version"] if strategy["version"] else "v1.0",
            start_date=request.start_date,
            end_date=request.end_date,
            exchange_instance=exchange_instance
        )
        
        return results
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing backtest for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTEST_EXECUTE_FAILED", "message": str(e)}
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


# ══════════════════════════════════════════════════════════════════════════
# OPTIMIZATION OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

@router.post("/strategies/{strategy_id}/optimize")
@limiter.limit("10/minute")
async def run_optimization(
    strategy_id: str,
    request: OptimizationRequest,
    user: dict = Depends(get_current_user)
):
    """
    Run strategy optimization using Strategy Package from compiler.
    
    PHASE Research & Optimization: Executes comprehensive research including:
    - Parameter optimization (Grid Search, Random Search, Bayesian, Genetic)
    - Walk-forward analysis
    - Monte Carlo simulation
    - Sensitivity analysis
    - Benchmark comparison
    - Strategy quality scoring
    - Overfitting detection
    - Deployment gate validation
    
    Uses the exact same Strategy Package generated by the compiler.
    Reuses existing backtesting, risk, and portfolio engines.
    No duplicate execution runtime.
    """
    try:
        # Get strategy to retrieve version info
        strategy_service = await get_strategy_service()
        strategy = await strategy_service.get_strategy(user["id"], strategy_id)
        
        if not strategy:
            raise HTTPException(
                status_code=404,
                detail={"error": "STRATEGY_NOT_FOUND", "message": f"Strategy {strategy_id} not found"}
            )
        
        # Reconstruct Strategy Package from execution graph
        from backend_app.backend.strategy_compiler import StrategyPackage, ExecutionGraph
        
        execution_graph = ExecutionGraph(
            id=request.execution_graph.get("id", str(uuid4())),
            version=request.execution_graph.get("version", "v1.0"),
            nodes=request.execution_graph.get("nodes", []),
            edges=request.execution_graph.get("edges", []),
            execution_order=request.execution_graph.get("execution_order", []),
            metadata=request.execution_graph.get("metadata", {})
        )
        
        strategy_package = StrategyPackage(
            id=str(uuid4()),
            strategy_id=strategy_id,
            version=strategy["version"]["version"] if strategy["version"] else "v1.0",
            execution_graph=execution_graph,
            metadata=execution_graph.metadata,
            dependencies=request.execution_graph.get("dependencies", {})
        )
        
        # Get optimization engine
        optimization_engine = get_optimization_engine()
        backtest_runtime = get_backtest_runtime()
        
        # Inject backtest runtime
        optimization_engine.set_backtest_runtime(backtest_runtime)
        
        # Inject exchange instance
        import ccxt
        exchange_instance = ccxt.binance()
        
        # Create optimization config
        config = OptimizationConfig(
            method=OptimizationMethod(request.optimization_method),
            validation_method=ValidationMethod(request.validation_method),
            parameters=request.parameters,
            n_iterations=request.n_iterations,
            n_trials=request.n_trials,
            training_window_days=request.training_window_days,
            validation_window_days=request.validation_window_days,
            test_window_days=request.test_window_days,
            initial_capital=request.initial_capital,
            commission=request.commission,
            slippage=request.slippage,
            risk_per_trade=request.risk_per_trade,
            max_drawdown=request.max_drawdown,
            daily_loss_limit=request.daily_loss_limit
        )
        
        # Add date range to parameters
        config.parameters["start_date"] = request.start_date
        config.parameters["end_date"] = request.end_date
        
        # Generate research report
        research_report = await optimization_engine.generate_research_report(
            strategy_package=strategy_package,
            config=config,
            user_id=user["id"],
            strategy_id=strategy_id,
            version_id=strategy["version"]["id"] if strategy["version"] else None,
            version=strategy["version"]["version"] if strategy["version"] else "v1.0",
            exchange_instance=exchange_instance
        )
        
        # PHASE K: Deployment Gate validation
        deployment_approved = research_report.overall_quality_score >= 0.6 and len(research_report.warnings) == 0
        
        # Store research report in database
        sb = strategy_service._get_supabase({"id": user["id"], "access_token": None})
        
        report_data = {
            "id": research_report.report_id,
            "strategy_id": strategy_id,
            "user_id": user["id"],
            "version_id": strategy["version"]["id"] if strategy["version"] else None,
            "version": strategy["version"]["version"] if strategy["version"] else "v1.0",
            "optimization_method": request.optimization_method,
            "validation_method": request.validation_method,
            "n_iterations": request.n_iterations,
            "optimization_results": research_report.optimization_results,
            "best_parameters": research_report.best_parameters,
            "walk_forward_results": research_report.walk_forward_results,
            "monte_carlo_results": research_report.monte_carlo_results,
            "sensitivity_results": research_report.sensitivity_results,
            "benchmark_comparison": research_report.benchmark_comparison,
            "strategy_score": research_report.strategy_score,
            "overall_quality_score": research_report.overall_quality_score,
            "warnings": research_report.warnings,
            "deployment_approved": deployment_approved,
            "deployment_gate_reason": "Quality score >= 0.6 and no warnings" if deployment_approved else "Failed deployment gate",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        sb.table("strategy_research_reports").insert(report_data).execute()
        
        logger.info(f"[OPTIMIZATION] Research report {research_report.report_id} generated for strategy {strategy_id}")
        
        return {
            "status": "completed",
            "report_id": research_report.report_id,
            "research_report": research_report,
            "deployment_approved": deployment_approved
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error running optimization for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "OPTIMIZATION_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}/research")
@limiter.limit("200/minute")
async def list_research_reports(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    List all research reports for a Strategy.
    """
    try:
        from backend_app.core.dependencies import create_request_supabase
        
        sb = create_request_supabase(user.get("access_token"))
        
        result = (sb.table("strategy_research_reports")
                 .select("*")
                 .eq("strategy_id", strategy_id)
                 .eq("user_id", user["id"])
                 .order("created_at", desc=True)
                 .execute())
        
        return {
            "strategy_id": strategy_id,
            "research_reports": result.data or [],
            "total": len(result.data or [])
        }
    except Exception as e:
        logger.error(f"Error listing research reports for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "RESEARCH_LIST_FAILED", "message": str(e)}
        )


@router.get("/research/{report_id}")
@limiter.limit("200/minute")
async def get_research_report(
    report_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get complete research report.
    """
    try:
        from backend_app.core.dependencies import create_request_supabase
        
        sb = create_request_supabase(user.get("access_token"))
        
        result = sb.table("strategy_research_reports").select("*").eq("id", report_id).eq("user_id", user["id"]).execute()
        
        if not result.data:
            raise HTTPException(
                status_code=404,
                detail={"error": "RESEARCH_NOT_FOUND", "message": f"Research report {report_id} not found"}
            )
        
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting research report {report_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "RESEARCH_GET_FAILED", "message": str(e)}
        )


# ══════════════════════════════════════════════════════════════════════════
# DEPLOYMENT OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

@router.post("/strategies/{strategy_id}/deploy")
@limiter.limit("10/minute")
async def deploy_strategy(
    strategy_id: str,
    request: DeploymentRequest,
    user: dict = Depends(get_current_user)
):
    """
    Deploy a strategy to live trading.
    
    PHASE Deployment Engine: Deploys validated strategy using Strategy Package.
    
    Workflow:
    - Validates strategy has passed deployment gate (research approved)
    - Allocates worker
    - Initializes exchange connection
    - Initializes runtime (DAG Engine)
    - Performs health check
    - Starts execution loop
    
    Deployment executes ONLY the Strategy Package from compiler.
    """
    try:
        # Get strategy to retrieve version info
        strategy_service = await get_strategy_service()
        strategy = await strategy_service.get_strategy(user["id"], strategy_id)
        
        if not strategy:
            raise HTTPException(
                status_code=404,
                detail={"error": "STRATEGY_NOT_FOUND", "message": f"Strategy {strategy_id} not found"}
            )
        
        # PHASE K: Check deployment gate - strategy must be approved by research
        from backend_app.core.dependencies import create_request_supabase
        sb = create_request_supabase(user.get("access_token"))
        
        # Get latest research report
        research_result = (sb.table("strategy_research_reports")
                         .select("*")
                         .eq("strategy_id", strategy_id)
                         .eq("user_id", user["id"])
                         .order("created_at", desc=True)
                         .limit(1)
                         .execute())
        
        if not research_result.data or not research_result.data[0].get("deployment_approved"):
            raise HTTPException(
                status_code=400,
                detail={"error": "DEPLOYMENT_GATE_FAILED", "message": "Strategy must pass research deployment gate before deployment"}
            )
        
        # Reconstruct Strategy Package from execution graph
        from backend_app.backend.strategy_compiler import StrategyPackage, ExecutionGraph
        from backend_app.backend.deployment_manager import StrategyDeploymentConfig, DeploymentEnvironment
        
        execution_graph = ExecutionGraph(
            id=request.execution_graph.get("id", str(uuid4())),
            version=request.execution_graph.get("version", "v1.0"),
            nodes=request.execution_graph.get("nodes", []),
            edges=request.execution_graph.get("edges", []),
            execution_order=request.execution_graph.get("execution_order", []),
            metadata=request.execution_graph.get("metadata", {})
        )
        
        # Create deployment config
        deployment_config = StrategyDeploymentConfig(
            deployment_id=str(uuid4()),
            strategy_id=strategy_id,
            user_id=user["id"],
            version_id=strategy["version"]["id"] if strategy["version"] else None,
            version=strategy["version"]["version"] if strategy["version"] else "v1.0",
            execution_graph=execution_graph,
            environment=DeploymentEnvironment(request.environment),
            exchange_id=request.exchange_id,
            exchange_symbol=request.exchange_symbol,
            worker_region=request.worker_region,
            initial_capital=request.initial_capital,
            risk_per_trade=request.risk_per_trade,
            max_drawdown=request.max_drawdown,
            daily_loss_limit=request.daily_loss_limit
        )
        
        # Get deployment manager
        deployment_manager = get_deployment_manager()
        
        # Inject exchange instance
        import ccxt
        exchange_instance = ccxt.binance()  # Default to Binance
        
        # Deploy strategy
        deployment_state = await deployment_manager.deploy_strategy(
            deployment_config=deployment_config,
            exchange_instance=exchange_instance
        )
        
        # Store deployment in database
        deployment_data = {
            "id": deployment_config.deployment_id,
            "strategy_id": strategy_id,
            "user_id": user["id"],
            "version_id": deployment_config.version_id,
            "environment": request.environment,
            "status": deployment_state.status.value,
            "worker_id": deployment_state.worker.worker_id if deployment_state.worker else None,
            "exchange_id": request.exchange_id,
            "exchange_symbol": request.exchange_symbol,
            "worker_region": request.worker_region,
            "initial_capital": request.initial_capital,
            "started_at": deployment_state.started_at,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        sb.table("strategy_deployments").insert(deployment_data).execute()
        
        logger.info(f"[DEPLOYMENT] Strategy {strategy_id} deployed with ID {deployment_config.deployment_id}")
        
        return {
            "status": "deployed",
            "deployment_id": deployment_config.deployment_id,
            "deployment_state": deployment_state
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deploying strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOYMENT_FAILED", "message": str(e)}
        )


@router.post("/deployments/{deployment_id}/pause")
@limiter.limit("50/minute")
async def pause_deployment(
    deployment_id: str,
    user: dict = Depends(get_current_user)
):
    """Pause a running deployment."""
    try:
        deployment_manager = get_deployment_manager()
        deployment_state = await deployment_manager.pause_deployment(deployment_id)
        
        # Update database
        from backend_app.core.dependencies import create_request_supabase
        sb = create_request_supabase(user.get("access_token"))
        
        sb.table("strategy_deployments").update({"status": "paused"}).eq("id", deployment_id).execute()
        
        return {"status": "paused", "deployment_state": deployment_state}
    except Exception as e:
        logger.error(f"Error pausing deployment {deployment_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOYMENT_PAUSE_FAILED", "message": str(e)}
        )


@router.post("/deployments/{deployment_id}/resume")
@limiter.limit("50/minute")
async def resume_deployment(
    deployment_id: str,
    user: dict = Depends(get_current_user)
):
    """Resume a paused deployment."""
    try:
        deployment_manager = get_deployment_manager()
        deployment_state = await deployment_manager.resume_deployment(deployment_id)
        
        # Update database
        from backend_app.core.dependencies import create_request_supabase
        sb = create_request_supabase(user.get("access_token"))
        
        sb.table("strategy_deployments").update({"status": "running"}).eq("id", deployment_id).execute()
        
        return {"status": "resumed", "deployment_state": deployment_state}
    except Exception as e:
        logger.error(f"Error resuming deployment {deployment_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOYMENT_RESUME_FAILED", "message": str(e)}
        )


@router.post("/deployments/{deployment_id}/restart")
@limiter.limit("50/minute")
async def restart_deployment(
    deployment_id: str,
    user: dict = Depends(get_current_user)
):
    """Restart a deployment."""
    try:
        deployment_manager = get_deployment_manager()
        deployment_state = await deployment_manager.restart_deployment(deployment_id)
        
        # Update database
        from backend_app.core.dependencies import create_request_supabase
        sb = create_request_supabase(user.get("access_token"))
        
        sb.table("strategy_deployments").update({"status": "running"}).eq("id", deployment_id).execute()
        
        return {"status": "restarted", "deployment_state": deployment_state}
    except Exception as e:
        logger.error(f"Error restarting deployment {deployment_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOYMENT_RESTART_FAILED", "message": str(e)}
        )


@router.post("/deployments/{deployment_id}/stop")
@limiter.limit("50/minute")
async def stop_deployment(
    deployment_id: str,
    user: dict = Depends(get_current_user)
):
    """Stop a deployment."""
    try:
        deployment_manager = get_deployment_manager()
        deployment_state = await deployment_manager.stop_deployment(deployment_id)
        
        # Update database
        from backend_app.core.dependencies import create_request_supabase
        sb = create_request_supabase(user.get("access_token"))
        
        sb.table("strategy_deployments").update({"status": "stopped", "stopped_at": datetime.now(timezone.utc).isoformat()}).eq("id", deployment_id).execute()
        
        return {"status": "stopped", "deployment_state": deployment_state}
    except Exception as e:
        logger.error(f"Error stopping deployment {deployment_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOYMENT_STOP_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}/deployments")
@limiter.limit("200/minute")
async def list_deployments(
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """List all deployments for a strategy."""
    try:
        deployment_manager = get_deployment_manager()
        deployments = deployment_manager.list_deployments(user["id"])
        
        return {
            "strategy_id": strategy_id,
            "deployments": [
                {
                    "deployment_id": d.deployment_id,
                    "status": d.status.value,
                    "environment": d.config.environment.value,
                    "worker": d.worker.worker_id if d.worker else None,
                    "started_at": d.started_at,
                    "health": {
                        "cpu_percent": d.health.cpu_percent,
                        "memory_percent": d.health.memory_percent,
                        "latency_ms": d.health.latency_ms,
                        "uptime_seconds": d.health.uptime_seconds
                    }
                }
                for d in deployments if d.config.strategy_id == strategy_id
            ],
            "total": len([d for d in deployments if d.config.strategy_id == strategy_id])
        }
    except Exception as e:
        logger.error(f"Error listing deployments for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOYMENTS_LIST_FAILED", "message": str(e)}
        )


@router.get("/deployments/{deployment_id}")
@limiter.limit("200/minute")
async def get_deployment(
    deployment_id: str,
    user: dict = Depends(get_current_user)
):
    """Get deployment details."""
    try:
        deployment_manager = get_deployment_manager()
        deployment_state = deployment_manager.get_deployment(deployment_id)
        
        if not deployment_state:
            raise HTTPException(
                status_code=404,
                detail={"error": "DEPLOYMENT_NOT_FOUND", "message": f"Deployment {deployment_id} not found"}
            )
        
        return {
            "deployment_id": deployment_state.deployment_id,
            "status": deployment_state.status.value,
            "environment": deployment_state.config.environment.value,
            "worker": {
                "worker_id": deployment_state.worker.worker_id if deployment_state.worker else None,
                "status": deployment_state.worker.status if deployment_state.worker else None,
                "region": deployment_state.worker.region if deployment_state.worker else None,
                "cpu_usage": deployment_state.worker.cpu_usage if deployment_state.worker else 0,
                "memory_usage": deployment_state.worker.memory_usage if deployment_state.worker else 0,
                "latency_ms": deployment_state.worker.latency_ms if deployment_state.worker else 0,
                "restart_count": deployment_state.worker.restart_count if deployment_state.worker else 0,
                "uptime_seconds": deployment_state.worker.uptime_seconds if deployment_state.worker else 0
            },
            "health": {
                "cpu_percent": deployment_state.health.cpu_percent,
                "memory_percent": deployment_state.health.memory_percent,
                "latency_ms": deployment_state.health.latency_ms,
                "runtime_errors": deployment_state.health.runtime_errors,
                "exchange_errors": deployment_state.health.exchange_errors,
                "last_heartbeat": deployment_state.health.last_heartbeat,
                "uptime_seconds": deployment_state.health.uptime_seconds
            },
            "started_at": deployment_state.started_at,
            "stopped_at": deployment_state.stopped_at,
            "error_message": deployment_state.error_message,
            "runtime_metrics": deployment_state.runtime_metrics
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting deployment {deployment_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOYMENT_GET_FAILED", "message": str(e)}
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
# MARKETPLACE INTEGRATION - DEPRECATED
# ══════════════════════════════════════════════════════════════════════════
# Marketplace operations moved to /api/library/* router
# Use library.py endpoints for:
# - Publish/Unpublish strategies
# - Browse marketplace
# - Clone strategies
# - Rate strategies
# - Subscribe to strategies
# ══════════════════════════════════════════════════════════════════════════