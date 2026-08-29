"""
routers/strategy_operations.py — Strategy Operations API

PHASE 2: Strategy Operations Router

Provides REST API endpoints for complete Strategy lifecycle management.
Replaces Bot Monitor with Strategy-centric architecture.

A deployed Strategy IS the running trading bot.
"""

from datetime import datetime, timezone
import gzip
import inspect
import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set
from uuid import uuid4

from fastapi import Request, APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend_app.backend.strategy_service import get_strategy_service, StrategyStatus, StrategyEnvironment, DeployPrerequisiteError
from backend_app.backend.metrics_service import get_metrics_service
from backend_app.backend.backtest_service import get_backtest_service
# from backend_app.backend.subscription_service import get_subscription_service  # DEPRECATED: Use library.py for subscriptions
from backend_app.backend.strategy_compiler import get_compiler, CompilerError, ValidationError
from backend_app.backend.backtest_runtime import BacktestRuntime, get_backtest_runtime
from backend_app.backend.optimization_engine import get_optimization_engine, OptimizationConfig, OptimizationMethod, ValidationMethod
from backend_app.backend.deployment_manager import get_deployment_manager, DeploymentConfig as DeployConfig, DeploymentEnvironment
from backend_app.core.dependencies import get_current_user
from backend_app.core.rate_limit import limiter
# Task 6.3: the existing ML entitlement and quota controls, kept in force on the
# training endpoints as declared dependencies. The caps from task 6.2 are additive to
# these, never a replacement (Requirement 16.7).
from backend_app.core.subscription_dependencies import check_ml_quota, require_ml_training
from backend_app.core.performance_monitor import (
    monitor_performance,
    log_performance_summary,
    verify_database_indexes,
    verify_cache_configuration,
    verify_query_optimization
)
from backend_app.core.crash_recovery import (
    crash_recovery_manager,
    verify_state_consistency,
    periodic_crash_check
)

router = APIRouter()
logger = logging.getLogger("StrategyOperationsRouter")


# ══════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════

class StrategyCreateRequest(BaseModel):
    """Request model for creating a Strategy."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field("", max_length=500)
    blueprint: dict = Field(default_factory=dict, description="Strategy DAG blueprint")
    exchange: Optional[str] = Field("binance", description="Exchange ID")
    symbol: Optional[str] = Field("BTCUSDT", description="Trading pair")
    timeframe: Optional[str] = Field("1h", description="Timeframe")
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
    exchange_id: Optional[str] = Field(None, description="Exchange name / slug (e.g. 'binance')")
    exchange_account_id: Optional[str] = Field(None, description="Connected exchange account UUID from user's vault")
    capital: Optional[float] = Field(None, description="Initial capital for this deployment")
    trade_size_pct: Optional[float] = Field(None, description="Trade size as decimal fraction (0.1 = 10%)")
    stop_loss_pct: Optional[float] = Field(None, description="Stop-loss threshold as decimal fraction")
    max_drawdown_pct: Optional[float] = Field(None, description="Maximum drawdown threshold as decimal fraction")


class StrategyCloneRequest(BaseModel):
    """Request model for cloning a Strategy."""
    new_name: str = Field(..., min_length=1, max_length=100)


class StrategyCompileRequest(BaseModel):
    """Request model for compiling a Strategy.

    ``blueprint`` accepts a canonical ``schema_version: 2`` ``StrategyGraph`` envelope
    (task 2.4). A version 1 blueprint is still accepted and migrated at read time, so an
    existing client keeps working while the frontend is re-pointed.
    """
    blueprint: dict = Field(..., description="Canonical StrategyGraph (schema_version 2)")
    version: str = Field(..., description="Version to compile")
    metadata: Optional[dict] = Field(None, description="Optional metadata")


# ══════════════════════════════════════════════════════════════════════════
# STRATEGY COMPILE AND OPERATIONS (Canonical routing handled by strategies.py)
# ══════════════════════════════════════════════════════════════════════════


@router.post("/strategies/compile")
@limiter.limit("100/minute")
async def compile_strategy(request: Request,
    body: StrategyCompileRequest,
    user: dict = Depends(get_current_user)
):
    """
    Compile a strategy graph into a Compiled_Plan. Persists nothing.

    Task 2.4: the input is a canonical ``StrategyGraph``. This endpoint used to build a
    ``DAGConfig`` and go down the legacy ``StrategyPackage`` branch, which carried a
    different rule set from the router-embedded compiler and from the canonical one -
    three answers to one question (SB-01). It now goes through
    ``StrategyCompiler.compile_plan``, the same entry point the save path, the clone
    path, the worker and the backtester use, so the verdict is identical everywhere
    (Requirement 3.1, 3.2).

    A version 1 blueprint is migrated at read time by ``schema.load_graph``, so an
    un-migrated client still gets a canonical answer instead of a legacy one.

    Response: ``compiled_plan`` is ``CompiledPlan.to_dict()`` - the one serialization
    path (Requirement 2.5) and byte-identical to what the version row stores, so a
    client can compare hashes without a translation table. ``execution_graph`` and
    ``dependencies`` are retained in their existing shapes for the current client.
    """
    from backend_app.backend.strategy_compiler import ExecutionGraph, get_compiler
    from backend_app.backend.strategy_dag.schema import (
        GraphParseError,
        UnsupportedSchemaVersion,
        load_graph,
    )

    try:
        graph = load_graph(body.blueprint)
    except (GraphParseError, UnsupportedSchemaVersion) as e:
        logger.info("Compile payload is not a readable strategy graph: %s", e)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "STRATEGY_GRAPH_UNREADABLE",
                "message": str(e),
                "hint": "Send a canonical schema_version 2 graph with 'nodes' and 'edges'.",
            },
        )

    try:
        plan = get_compiler().compile_plan(graph)
    except ValidationError as e:
        # The complete structured report, so every problem is fixable in one round
        # (Requirement 3.5, 8.x). `.report` is None only for a legacy string failure, so
        # it is checked before it is dereferenced.
        report = getattr(e, "report", None)
        logger.info(
            "Compile refused: %s", e.codes() if hasattr(e, "codes") else e
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "STRATEGY_VALIDATION_FAILED",
                "message": str(e),
                "report": report.to_dict() if report is not None else None,
                "errors": list(getattr(e, "errors", []) or []),
                "warnings": list(getattr(e, "warnings", []) or []),
                "codes": list(e.codes()) if hasattr(e, "codes") else [],
            },
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

    canonical = graph.to_dict()
    execution_graph = ExecutionGraph(
        id=str(uuid4()),
        version=body.version,
        nodes=canonical["nodes"],
        edges=canonical["edges"],
        execution_order=list(plan.execution_order),
        metadata={
            "node_count": len(canonical["nodes"]),
            "edge_count": len(canonical["edges"]),
            "warmup_bars": plan.warmup_bars,
            **(body.metadata or {}),
        },
    )

    return {
        "status": "compiled",
        "compiled_plan": plan.to_dict(),
        "dag_hash": plan.dag_hash,
        "schema_version": plan.schema_version,
        "compiler_version": plan.compiler_version,
        "warmup_bars": plan.warmup_bars,
        "execution_graph": execution_graph.to_dict(),
        "dependencies": get_compiler().extract_resource_dependencies(graph),
        "compiled_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/strategies")
@limiter.limit("200/minute")
async def list_strategies(request: Request, 
    status: Optional[str] = Query(None),
    environment: Optional[str] = Query(None),
    user: dict = Depends(get_current_user)
):
    """
    List all Strategies for user with optional filters.
    
    Returns strategies with deployment status and performance metrics.
    """
    try:
        cache_key = f"strategies:{user['id']}:{status}:{environment}"
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            cached = await redis_manager.get(cache_key)
            if cached:
                import json
                return json.loads(cached)
        except Exception as cache_err:
            logger.debug(f"Strategies cache read error: {cache_err}")

        service = await get_strategy_service()
        
        strategies = await service.list_strategies(
            user=user,
            status_filter=status,
            environment_filter=environment
        )
        
        resp_data = {
            "strategies": strategies,
            "total": len(strategies)
        }

        try:
            from backend_app.core.cache.redis_manager import redis_manager
            import json
            await redis_manager.set(cache_key, json.dumps(resp_data), ex=10)
        except Exception as cache_write_err:
            logger.debug(f"Strategies cache write error: {cache_write_err}")

        return resp_data
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
            f"[STRATEGIES_LIST_ENDPOINT] Exception details: "
            f"endpoint=/api/strategies, "
            f"exception_type={exc_type}, "
            f"exception_module={exc_module}, "
            f"exception_message={exc_message}, "
            f"caller_file={caller_filename}, "
            f"caller_line={caller_lineno}, "
            f"status_filter={status}, "
            f"environment_filter={environment}, "
            f"user_id_truncated={user['id'][:8] if user.get('id') else 'missing'}..."
        )
        
        # Log full traceback for debugging
        logger.error(f"[STRATEGIES_LIST_ENDPOINT] Full traceback:\n{traceback.format_exc()}")
        
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGIES_LIST_FAILED", "message": str(e)}
        )


@router.get("/strategies/{strategy_id}")
@limiter.limit("200/minute")
async def get_strategy(request: Request, 
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get complete Strategy data.
    
    Returns strategy with current version, deployments, and backtests.
    """
    try:
        service = await get_strategy_service()
        
        strategy = await service.get_strategy(user=user, strategy_id=strategy_id)
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
async def update_strategy(request: Request, 
    strategy_id: str,
    body: StrategyUpdateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Update a Strategy.
    
    If blueprint is updated, creates a new version.
    Otherwise updates metadata only.
    """
    try:
        service = await get_strategy_service()
        
        updates = body.dict(exclude_unset=True)
        result = await service.update_strategy(user=user, strategy_id=strategy_id, updates=updates)
        if not result:
            raise HTTPException(
                status_code=404,
                detail={"error": "STRATEGY_NOT_FOUND", "message": f"Strategy {strategy_id} not found"}
            )
        
        return {
            "status": "updated",
            "strategy": result.get("strategy", result),
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
async def delete_strategy(request: Request, 
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Delete a Strategy and all associated data.
    
    Stops all running deployments before deletion.
    """
    try:
        service = await get_strategy_service()
        
        success = await service.delete_strategy(user=user, strategy_id=strategy_id)
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
async def clone_strategy(request: Request, 
    strategy_id: str,
    body: StrategyCloneRequest,
    user: dict = Depends(get_current_user)
):
    """
    Clone a Strategy.
    
    Creates new strategy with same blueprint and configuration.
    """
    try:
        service = await get_strategy_service()
        
        result = await service.clone_strategy(user=user, strategy_id=strategy_id, new_name=body.new_name)
        
        return {
            "status": "cloned",
            "strategy": result["strategy"],
            "version": result["version"],
            "warnings": result.get("warnings", [])
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
async def deploy_strategy(request: Request, 
    strategy_id: str,
    body: StrategyDeployRequest,
    user: dict = Depends(get_current_user)
):
    """
    Deploy a Strategy (creates running bot instance).
    
    This is the equivalent of starting a bot.
    A deployed Strategy IS the running trading bot.

    Task 8.2 note: this is the **strategy-level legacy** deploy surface, not the versioned
    binding surface `design.md` names (``POST .../versions/{v}/deploy``). It gained exactly
    two things from task 8.2 and deliberately nothing else: the deployment row now records
    ``mode`` - without which ``chk_sd_live_needs_account`` guards nothing, because it
    constrains ``mode`` and this writer only ever set the legacy ``environment`` column -
    and Requirement 13.6 is enforced here, so a live deploy with no exchange account is a
    422 naming the missing account rather than a 23514 surfacing as a 500. The lifecycle
    and market-compatibility gates belong to the versioned route.
    """
    from backend_app.backend.deployment_binding import DeployRejected

    try:
        service = await get_strategy_service()
        
        result = await service.deploy_strategy(
            user=user,
            strategy_id=strategy_id,
            version=body.version,
            environment=body.environment,
            exchange_id=body.exchange_id,
            exchange_account_id=body.exchange_account_id,
            capital=body.capital,
            trade_size_pct=body.trade_size_pct,
            stop_loss_pct=body.stop_loss_pct,
            max_drawdown_pct=body.max_drawdown_pct,
        )
        
        return result
    except DeployRejected as e:
        raise _deploy_rejected_http(e)
    except DeployPrerequisiteError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "DEPLOY_PREREQUISITE_NOT_MET",
                "message": str(e),
                "missing_prerequisite": e.missing_prerequisite,
            }
        )
    except ValueError as e:
        err_msg = str(e)
        logger.warning(f"Validation/quota error deploying strategy {strategy_id}: {err_msg}")
        status_code = 403 if "quota" in err_msg.lower() or "subscription" in err_msg.lower() else 400
        raise HTTPException(
            status_code=status_code,
            detail={"error": "STRATEGY_DEPLOY_FAILED", "message": err_msg}
        )
    except Exception as e:
        logger.error(f"Error deploying strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_DEPLOY_FAILED", "message": str(e)}
        )


class DeploymentTransitionRequest(BaseModel):
    """The optional body of a pause, resume or stop request (task 8.3).

    One field, and only one: the author's reason. It is preserved on the deployment row
    and in the audit record (Requirements 9.8, 20.9), so "why is this stopped?" has an
    answer that is not "somebody pressed stop". Nothing else may be sent - a transition
    is not an opportunity to re-bind an account or change a limit, and
    ``extra="forbid"`` says so rather than dropping the field silently.
    """

    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(
        None,
        max_length=500,
        description="Why this transition was requested. Recorded on the deployment and in the audit trail.",
    )


async def _transition_deployment_endpoint(
    action: str,
    deployment_id: str,
    user: dict,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """The shared body of pause, resume and stop (Requirements 13.8, 13.10, 9.7, 9.8).

    Three endpoints, one implementation, because the only thing that differs between them
    is the action name - and three copies is how one of them ends up without the
    ownership filter. Every refusal arrives as a ``LifecycleRejected`` carrying its own
    status (409 for a state conflict naming the current state, 422 for an unknown action),
    and a deployment that is not this user's is a 404 rather than a 403 so a deployment id
    cannot be probed for existence across tenants.

    The legacy ``status`` key is kept on the response next to the canonical
    ``binding_state``: existing clients read the lowercase string, and Requirement 13.10
    asks for one of the five uppercase states. Removing the old key would be a breaking
    change nothing in this task requires.
    """
    from backend_app.backend.deployment_binding import DeployRejected

    try:
        service = await get_strategy_service()
        result = await service.transition_deployment(
            user=user, deployment_id=deployment_id, action=action, reason=reason
        )
    except DeployRejected as e:  # includes LifecycleRejected
        raise _deploy_rejected_http(e)
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={"error": "DEPLOYMENT_NOT_FOUND", "message": str(e)},
        )
    except Exception as e:
        logger.error(
            f"Error applying {action} to deployment {deployment_id} for user {user['id']}: {e}"
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": f"DEPLOYMENT_{action.upper()}_FAILED",
                "message": str(e),
            },
        )

    return {
        "status": str(result.get("binding_state", "")).lower(),
        "deployment_id": deployment_id,
        **result,
    }


@router.post("/deployments/{deployment_id}/stop")
@limiter.limit("100/minute")
async def stop_deployment(request: Request, 
    deployment_id: str,
    body: Optional[DeploymentTransitionRequest] = None,
    user: dict = Depends(get_current_user)
):
    """Stop a running Strategy deployment (Requirement 13.8).

    The deployment moves to ``STOPPED``, the reason is preserved on the row and in the
    audit trail, and the version it runs moves ``RUNNING``/``PAUSED`` -> ``STOPPED`` where
    the lifecycle allows it. Stopping an already-stopped deployment is idempotent and
    answers 200 with ``idempotent: true``.

    Responses
        **200** stopped, or already stopped.
        **404** no such deployment belongs to this user.
        **409** the deployment cannot be stopped from its current state, which the
        response names.
        **429** from the existing limiter.
    """
    return await _transition_deployment_endpoint(
        "stop", deployment_id, user, body.reason if body is not None else None
    )


@router.post("/strategies/{strategy_id}/pause")
@router.post("/strategies/{strategy_id}/stop")
@limiter.limit("50/minute")
async def pause_strategy(request: Request, 
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Pause or stop a running Strategy.
    
    Stops all deployments and marks strategy as paused.
    """
    try:
        service = await get_strategy_service()
        
        success = await service.pause_strategy(user=user, strategy_id=strategy_id)
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
async def resume_strategy(request: Request, 
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Resume a paused Strategy.
    
    Re-deploys strategy with current version.
    """
    try:
        service = await get_strategy_service()
        
        result = await service.resume_strategy(user=user, strategy_id=strategy_id)
        
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
async def get_strategy_metrics(request: Request, 
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
        
        metrics = await service.get_strategy_metrics(user=user, strategy_id=strategy_id)
        
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
async def get_strategy_performance(request: Request, 
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
            user=user,
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
async def get_strategy_equity_curve(request: Request, 
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
            user=user,
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
async def get_strategy_monthly_returns(request: Request, 
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
            user=user,
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
async def get_strategy_daily_returns(request: Request, 
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
            user=user,
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
async def get_strategy_execution_metrics(request: Request, 
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
            user=user,
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
async def get_strategy_risk_metrics(request: Request, 
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
            user=user,
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
    version: int = Field(1, ge=1, description="Version number (integer)")
    blueprint: dict = Field(..., description="Strategy blueprint")
    dataset: str = Field(..., description="Dataset to use")
    start_date: str = Field(..., description="Start date (ISO format)")
    end_date: str = Field(..., description="End date (ISO format)")
    initial_capital: float = Field(10000, ge=0)
    commission: float = Field(0.001, ge=0)
    slippage: float = Field(0.0005, ge=0)


class BacktestExecuteRequest(BaseModel):
    """One backtest of one stored version. ``design.md``: ``{version_id, start_date, end_date, initial_capital, ...}``.

    Trading-lifecycle-integration task 6.1, Requirements 5.1-5.7, 22.3.

    WHAT MOVED OFF THIS MODEL
        ``execution_graph`` used to be **required**, which made the caller reconstruct the
        graph the backtester was about to run — the shape Requirement 5.7 forbids, because
        a client-assembled graph is by definition not the artifact the live runtime
        consumes. The version's own persisted ``compiled_plan`` is executed instead
        (Requirement 5.2, 22.3), so the field is no longer read. It is kept declared, and
        named in the response's ``ignored_fields``, rather than deleted: a caller that
        still sends it gets an explicit statement that it had no effect instead of either a
        422 for a field that used to be mandatory or the silent ignore Requirement 6.2
        rules out.

    WHAT IS NOT ACCEPTED HERE, AND WHY
        No symbol, timeframe, market type, data feed or exchange identifier. Market
        identity is the version's own DATA block (SB-06, Requirement 12.1) and the venue is
        the server's public feed, resolved by :func:`_backtest_exchange_instance` — exactly
        the ``ConnectionEngine`` path ``preview_node`` already uses. ``extra="forbid"`` so
        an ``exchange``, ``symbol`` or ``api_key`` key is a 422 rather than a field a client
        author believes is doing something.

    Every field below is read: each one reaches the ``BacktestRuntime`` constructed for
    this request (see :func:`_backtest_runtime_for`), so varying it changes the simulated
    result — the "implements" test Requirement 6.1 sets. The bounds are the ones this model
    already carried; configuration sanity bounds beyond them are task 6.3's subject.
    """

    model_config = ConfigDict(extra="forbid")

    version_id: Optional[str] = Field(
        None,
        description=(
            "The immutable strategy version to backtest. Omitted: the strategy's current "
            "version is used (Requirement 4.3 lets the caller name any other one)."
        ),
    )
    execution_graph: Optional[dict] = Field(
        None,
        description=(
            "LEGACY, NO LONGER READ. The version's own persisted compiled plan is "
            "executed (Requirements 5.2, 5.7). Supplying it is reported back in "
            "'ignored_fields' and changes nothing about the run."
        ),
    )
    start_date: str = Field(..., description="Start date (ISO format)")
    end_date: str = Field(..., description="End date (ISO format)")
    initial_capital: float = Field(10000, ge=0)
    commission: float = Field(0.001, ge=0, description="Trading fee rate; the runtime's 'fees'")
    slippage: float = Field(0.0005, ge=0)
    spread: float = Field(0.0002, ge=0, description="Spread rate applied by the simulator")
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
async def create_backtest(request: Request, 
    strategy_id: str,
    body: BacktestCreateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Create a new backtest for a Strategy.

    Every backtest is stored permanently with strategy.

    OWNERSHIP, BEFORE THE ROW IS BUILT (Requirement 20.1)
    -----------------------------------------------------
    ``strategy_id`` arrives as a path parameter and used to travel straight into
    ``BacktestService.create_backtest``, which pairs it with the CALLER'S OWN ``user_id``
    and inserts the row. Nothing read ``strategies``, so nothing ever learned whose
    strategy that was: a non-owner could persist a ``strategy_backtests`` row referencing
    another tenant's ``strategy_id`` - Requirement 20.1's "reference ... another user's
    resource, including by naming it as a related entity in a request body", which that
    criterion requires the application layer to refuse *on top of* row-level security.

    The read below is the ownership-scoped one ``StrategyService.get_strategy`` performs
    (``.eq("id", strategy_id).eq("user_id", user["id"])``) - the same seam the sibling
    ``execute_backtest`` handler uses for the same purpose, so there is one spelling of
    "is this strategy mine" on the backtest surface and not two.

    The refusal is the established 404 ``STRATEGY_NOT_FOUND`` shape (``execute_backtest``,
    ``strategy_archive.archive_strategy``), and it is the SAME response a strategy id that
    names no row at all receives - a strategy that is not the caller's and a strategy that
    does not exist are indistinguishable here, per Requirement 20.2. No new error code is
    introduced, precisely because a distinct one would itself be the disclosure.
    """
    try:
        strategy_service = await get_strategy_service()
        owned = await strategy_service.get_strategy(user=user, strategy_id=strategy_id)
        if not owned:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "STRATEGY_NOT_FOUND",
                    "message": f"Strategy {strategy_id} not found",
                },
            )

        backtest_service = await get_backtest_service()
        
        backtest = await backtest_service.create_backtest(
            user=user,
            strategy_id=strategy_id,
            version=body.version,
            blueprint=body.blueprint,
            dataset=body.dataset,
            start_date=body.start_date,
            end_date=body.end_date,
            initial_capital=body.initial_capital,
            commission=body.commission,
            slippage=body.slippage
        )
        
        return {
            "status": "created",
            "backtest": backtest
        }
    except HTTPException:
        # The 404 above is an ANSWER about the request, not a failure of it. Without this
        # re-raise the generic handler below would rewrite it as a 500
        # ``BACKTEST_CREATE_FAILED``, and a 500 whose message quoted the strategy id would
        # be a different answer from the one a nonexistent id gets (Requirement 20.2).
        raise
    except Exception as e:
        logger.error(f"Error creating backtest for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTEST_CREATE_FAILED", "message": str(e)}
        )


async def _backtest_exchange_instance():
    """The public feed a backtest reads its history through.

    Trading-lifecycle-integration task 6.1. Replaces the ``import ccxt;
    ccxt.binance()`` this handler used to construct, which pinned every backtest on the
    platform to one venue regardless of the strategy or the server's configuration, and
    did so from inside a router.

    The path is the one the Builder surface already uses for public market data:
    ``ConnectionEngine`` opens an unauthenticated connection to the feed named by the
    server's ``DEFAULT_EXCHANGE`` (see :func:`_fetch_preview_bars`, which resolves it the
    same way for the same reason). A backtest reads no user's vault and accepts no venue
    from the request, so the venue stays a deployment fact (SB-06).

    Seam note: module-level, so a test can hand the handler a deterministic feed without
    replacing the version load, the compiler, the DAG engine or the simulator — the four
    things a backtest test is actually about.
    """
    import os

    from backend_app.backend.connection_engine import ConnectionEngine

    venue = os.getenv("DEFAULT_EXCHANGE")
    if not venue:
        logger.error("Backtest unavailable: DEFAULT_EXCHANGE is not configured")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "BACKTEST_FEED_UNCONFIGURED",
                "message": (
                    "The platform market data feed is not configured, so no historical "
                    "window can be read and no backtest can be run."
                ),
            },
        )
    return await ConnectionEngine(exchange_id=venue).connect()


def _backtest_runtime_for(body: "BacktestExecuteRequest"):
    """A :class:`BacktestRuntime` carrying **this request's** configuration.

    Deliberately not ``get_backtest_runtime()``. That singleton is constructed once with
    the class defaults, so every configuration field this endpoint accepts —
    ``initial_capital``, ``commission``, ``slippage``, ``spread``, ``risk_per_trade``,
    ``max_drawdown``, ``daily_loss_limit`` — was accepted and then never read: the
    simulator and the risk engine ran on $10,000 at 0.1% fees no matter what was sent.
    That is exactly the "parameter the engine accepts but does not apply" Requirement 6.2
    forbids offering a control for.

    ``BacktestRuntime`` takes all seven as constructor arguments and hands them to the
    ``BacktestEngine`` (VectorBT) and the ``RiskEngine`` it builds, so constructing one per
    request is what makes the submitted value observable in the result. Nothing inside the
    runtime is modified — it is REUSED AS-IS per ``design.md``'s component disposition; only
    the choice of instance changes.

    A per-request instance is also the safer one: the singleton carries mutable per-run
    state (``last_backtest_results``, and a ``data_engine`` bound to whichever exchange
    connected last), which two concurrent backtests would share.

    Seam note: module-level for the same reason as :func:`_backtest_exchange_instance`.
    ``BacktestRuntime`` is imported at module scope alongside ``get_backtest_runtime``, so
    this file's backtest-import footprint (pinned by
    ``tests/test_save_performs_no_backtest.py``) does not grow a second entry.
    """
    return BacktestRuntime(
        initial_capital=body.initial_capital,
        fees=body.commission,
        slippage=body.slippage,
        spread=body.spread,
        risk_per_trade=body.risk_per_trade,
        max_drawdown=body.max_drawdown,
        daily_loss_limit=body.daily_loss_limit,
    )


async def _load_backtest_version(
    service: Any, user: dict, strategy_id: str, owned: Mapping[str, Any], version_id: Optional[str]
) -> Dict[str, Any]:
    """The ``strategy_versions`` row this backtest will execute.

    ``version_id`` names any persisted version of this strategy (Requirement 4.3);
    omitting it selects the current one, which is what the Strategies_Page preselection
    path sends (Requirement 4.1). Either way the row is returned whole, because
    :meth:`BacktestRuntime.load_version_plan` reads the persisted ``compiled_plan`` off it
    (Requirements 5.2, 22.3).

    ``select("*")`` and never a projection: the same rule the archive read follows, so a
    column a migration has not added yet is an absent key rather than a 42703.

    Ownership: the caller has already established that ``strategy_id`` belongs to this
    user; this read adds ``strategy_id`` as a filter on top of the request-scoped client's
    RLS, so another strategy's version id cannot be backtested under this strategy.

    Raises
        :class:`HTTPException` 404 ``BACKTEST_VERSION_UNAVAILABLE`` when no such version
        belongs to this strategy — the same answer a non-existent id gets (Requirement
        20.2) — and 422 ``BACKTEST_VERSION_UNAVAILABLE`` when the strategy simply has no
        version to backtest yet (Requirement 5.4).
    """
    if not version_id:
        current = owned.get("version")
        if not current:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "BACKTEST_VERSION_UNAVAILABLE",
                    "message": (
                        "This strategy has no saved version, so there is nothing to "
                        "backtest. Save a version first."
                    ),
                    "strategy_id": strategy_id,
                },
            )
        return dict(current)

    sb_res = service._get_supabase(user)  # noqa: SLF001 - the router's established access
    sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
    rows: List[Dict[str, Any]] = []
    if sb is not None:
        query = (
            sb.table("strategy_versions")
            .select("*")
            .eq("id", str(version_id))
            .eq("strategy_id", str(strategy_id))
            .execute()
        )
        result = await query if inspect.isawaitable(query) else query
        rows = list(getattr(result, "data", None) or [])

    if not rows:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "BACKTEST_VERSION_UNAVAILABLE",
                "message": (
                    f"Version {version_id} is not a saved version of this strategy, so it "
                    "cannot be backtested."
                ),
                "strategy_id": strategy_id,
                "version_id": str(version_id),
            },
        )
    return dict(rows[0])


@router.post("/strategy-operations/strategies/{strategy_id}/backtests/execute")
@router.post("/strategies/{strategy_id}/backtests/execute")
@limiter.limit("10/minute")
async def execute_backtest(request: Request, 
    strategy_id: str,
    body: BacktestExecuteRequest,
    user: dict = Depends(get_current_user)
):
    """Backtest one immutable version through the canonical runtime, and persist the result.

    Trading-lifecycle-integration task 6.1. Requirements 5.1-5.7, 22.3, and Requirement
    3.3's archived refusal (deferred to here by task 5.1).

    WHAT THIS ENDPOINT USED TO DO, AND WHY IT CHANGED
    -------------------------------------------------
    It required the caller to post an ``execution_graph``, rebuilt a ``StrategyPackage``
    from it, and ran that. Three things were wrong with it, and all three are what
    Requirement 5 is about:

    * The graph came from the client, so the backtest executed an artifact assembled in
      the browser rather than the version's own persisted ``compiled_plan`` — the one
      thing Requirements 5.2/5.7 and 22.3 exist to prevent, because a backtest and a live
      run could then disagree about the same version.
    * The venue was ``ccxt.binance()``, hardcoded in the router.
    * Every configuration field the request model declared was discarded: the shared
      singleton runtime ran on the class defaults (Requirement 6.2's silently-ignored
      parameter, in its purest form).

    Now: the version row is loaded, :meth:`BacktestRuntime.run_version_backtest` resolves
    its plan through :meth:`~BacktestRuntime.load_version_plan` — reusing the persisted
    plan when its hash matches the graph and recompiling **for this run only** when it does
    not (Requirements 5.2, 5.3) — takes market identity from the version's own DATA nodes
    (SB-06), and runs the same ``DAG_Engine`` → ``BacktestEngine`` (VectorBT) → ``RiskEngine``
    pipeline the live path uses (Requirements 5.1, 5.5).

    ORDER, AND WHY
    --------------
    1. Ownership, through ``StrategyService.get_strategy``'s ``.eq("user_id", …)`` on top of
       the request-scoped client's RLS. Another tenant's strategy is a **404**, identical to
       a missing one (Requirement 20.2).
    2. Requirement 3.3's archived refusal, read off the row step 1 already loaded, so it
       costs no extra query. Raised as an ``ArchiveRejected`` — a ``DeployRejected``
       subclass — so this route's existing refusal mapping answers it without a second
       mapping that could drift from the deploy route's.
    3. The version row. Nothing has been written at this point, so a refusal at 1, 2 or 3
       leaves no ``strategy_backtests`` row behind.
    4. The run. ``run_version_backtest`` creates the ``strategy_backtests`` row, executes,
       and writes the metrics back through ``BacktestService`` — so persistence happens
       server-side in one call and the caller needs no second request to save it
       (Requirement 10.1; the client-side two-step glue task 17.2 removes).

    SYNCHRONOUS, AND RETURNED IN ONE RESPONSE
    -----------------------------------------
    No job id, no polling: the response carries ``backtest_id``, ``status`` and the full
    ``results`` — what ``BacktestRuntime.run_backtest``'s own contract already describes —
    plus the version that was run and the configuration that was actually applied, so a
    client can show what produced the numbers rather than what it hoped it sent.

    Responses
        **200** ``{backtest_id, status, results, strategy_id, version_id, version,
        configuration, ignored_fields}``.
        **404** ``STRATEGY_NOT_FOUND``, or ``BACKTEST_VERSION_UNAVAILABLE`` for a version
        that is not this strategy's.
        **409** ``STRATEGY_ARCHIVED`` (Requirement 3.3).
        **422** ``BACKTEST_VERSION_UNAVAILABLE`` when the version carries no runnable graph
        or plan (Requirement 5.4), or ``BACKTEST_NOT_RUNNABLE`` when the version declares no
        market or the requested window holds too little data to simulate.
        **503** ``BACKTEST_FEED_UNCONFIGURED``.
        **429** from the existing limiter.

    The legacy job-queue path (``POST /api/strategies/backtest`` in ``routers/strategies.py``)
    is untouched by this handler and stays reachable, per ``design.md``'s "RETAINED, NOT
    EXTENDED".
    """
    from backend_app.backend.deployment_binding import DeployRejected
    from backend_app.backend.strategy_archive import (
        OPERATION_BACKTEST,
        assert_strategy_not_archived,
    )

    # ── 1. Ownership, before anything is read, compiled or written ─────────
    try:
        strategy_service = await get_strategy_service()
        owned = await strategy_service.get_strategy(user=user, strategy_id=strategy_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Backtest could not resolve strategy %s for user %s: %s",
            strategy_id,
            user.get("id"),
            e,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_GET_FAILED", "message": str(e)},
        )

    if not owned:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "STRATEGY_NOT_FOUND",
                "message": f"Strategy {strategy_id} not found",
            },
        )

    # ── 2. Requirement 3.3: an archived strategy is not backtestable ───────
    try:
        assert_strategy_not_archived(
            owned.get("strategy") or {},
            operation=OPERATION_BACKTEST,
            strategy_id=strategy_id,
        )
    except DeployRejected as e:  # includes ArchiveRejected
        logger.info(
            "Backtest refused for strategy %s, user %s: %s",
            strategy_id,
            user.get("id"),
            e.code,
        )
        raise _deploy_rejected_http(e)

    # ── 3. The version whose persisted plan will be executed ───────────────
    version_row = await _load_backtest_version(
        strategy_service, user, strategy_id, owned, body.version_id
    )
    version_id = str(version_row.get("id") or "")
    version_label = str(version_row.get("version") or "v1.0")

    ignored_fields = ["execution_graph"] if body.execution_graph is not None else []
    if ignored_fields:
        logger.info(
            "Backtest for strategy %s ignored the legacy execution_graph field: version "
            "%s's own persisted plan is executed (Requirements 5.2, 5.7).",
            strategy_id,
            version_label,
        )

    configuration = {
        "initial_capital": body.initial_capital,
        "commission": body.commission,
        "slippage": body.slippage,
        "spread": body.spread,
        "risk_per_trade": body.risk_per_trade,
        "max_drawdown": body.max_drawdown,
        "daily_loss_limit": body.daily_loss_limit,
        "start_date": body.start_date,
        "end_date": body.end_date,
    }

    # ── 4. The one runtime, carrying this request's configuration ──────────
    runtime = _backtest_runtime_for(body)
    exchange_instance = await _backtest_exchange_instance()

    try:
        results = await runtime.run_version_backtest(
            version_row=version_row,
            user=user,
            strategy_id=strategy_id,
            version_id=version_id,
            version=version_label,
            start_date=body.start_date,
            end_date=body.end_date,
            exchange_instance=exchange_instance,
        )
    except HTTPException:
        raise
    except (CompilerError, ValidationError) as e:
        # Requirement 5.4: the version is named as unavailable for backtesting rather than
        # reported as a generic failure. A stored version that no longer compiles is a
        # statement about the version, not about the backtester.
        report = getattr(e, "report", None)
        logger.info(
            "Version %s of strategy %s could not be loaded for backtesting: %s",
            version_label,
            strategy_id,
            e,
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "BACKTEST_VERSION_UNAVAILABLE",
                "message": (
                    f"Version {version_label} could not be loaded for backtesting: {e}"
                ),
                "strategy_id": strategy_id,
                "version_id": version_id,
                "report": report.to_dict() if report is not None else None,
            },
        )
    except ValueError as e:
        # The runtime's own refusals: a version whose DATA nodes name no symbol (SB-06 -
        # nothing here substitutes BTC/USDT), and a window that returned too few bars to
        # simulate. Both are answers about the request, not server faults.
        logger.info(
            "Backtest of version %s for strategy %s was refused: %s",
            version_label,
            strategy_id,
            e,
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "BACKTEST_NOT_RUNNABLE",
                "message": str(e),
                "strategy_id": strategy_id,
                "version_id": version_id,
            },
        )
    except Exception as e:
        logger.error(
            "Error executing backtest for strategy %s for user %s: %s",
            strategy_id,
            user.get("id"),
            e,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTEST_EXECUTE_FAILED", "message": str(e)},
        )

    # The persisted result, returned directly. ``run_version_backtest`` already created the
    # ``strategy_backtests`` row and wrote the metrics back to it, so ``backtest_id`` names
    # a row that exists.
    payload = dict(results or {})
    payload.update(
        {
            "strategy_id": strategy_id,
            "version_id": version_id,
            "version": version_label,
            "configuration": configuration,
            "ignored_fields": ignored_fields,
        }
    )
    return payload


@router.get("/strategies/{strategy_id}/backtests")
@limiter.limit("200/minute")
async def list_backtests(request: Request, 
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
            user=user,
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
async def get_backtest(request: Request, 
    backtest_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get complete backtest data.
    """
    try:
        backtest_service = await get_backtest_service()
        
        backtest = await backtest_service.get_backtest(user=user, backtest_id=backtest_id)
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
async def get_backtest_report(request: Request, 
    backtest_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get comprehensive backtest report.
    
    Returns complete report with all metrics, equity curve, and analysis.
    """
    try:
        backtest_service = await get_backtest_service()
        
        report = await backtest_service.get_backtest_report(user=user, backtest_id=backtest_id)
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


@router.get("/backtests")
@limiter.limit("200/minute")
async def list_backtests(request: Request,
    strategy_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(get_current_user)
):
    """
    List backtests for user or strategy.
    
    Returns list of backtest records with summary information.
    """
    try:
        backtest_service = await get_backtest_service()
        
        backtests = await backtest_service.list_backtests(
            user=user,
            strategy_id=strategy_id,
            limit=limit
        )
        
        return {
            "backtests": backtests,
            "total": len(backtests)
        }
    except Exception as e:
        logger.error(f"Error listing backtests for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTESTS_LIST_FAILED", "message": str(e)}
        )


@router.put("/backtests/{backtest_id}/results")
@limiter.limit("100/minute")
async def update_backtest_results(request: Request,
    backtest_id: str,
    body: Dict[str, Any],
    user: dict = Depends(get_current_user)
):
    """
    Update backtest with execution results.

    Called after backtest execution completes to store metrics and equity curve.

    OWNERSHIP (Requirements 20.1, 20.2)
    -----------------------------------
    ``BacktestService.update_backtest_results`` scopes the UPDATE by ``user_id`` as well as
    by ``id``, so a caller who does not own ``backtest_id`` matches no row and the service
    returns ``{}``. Before that predicate existed this handler wrote a non-owner's metrics
    onto the owner's row and then echoed the row back, which was both the cross-tenant
    write Requirement 20.1 forbids and an existence oracle.

    An empty answer is returned for BOTH "not yours" and "no such backtest", so this
    handler cannot tell them apart either - and it answers the established
    404 ``BACKTEST_NOT_FOUND`` that ``get_backtest`` and ``get_backtest_report`` already
    give the same fact, identically for both cases (Requirement 20.2).
    """
    try:
        backtest_service = await get_backtest_service()
        
        results = body.get("results", {})
        updated_backtest = await backtest_service.update_backtest_results(
            user=user,
            backtest_id=backtest_id,
            results=results
        )

        if not updated_backtest:
            raise HTTPException(
                status_code=404,
                detail={"error": "BACKTEST_NOT_FOUND", "message": f"Backtest {backtest_id} not found"}
            )

        return updated_backtest
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating backtest results for {backtest_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTEST_RESULTS_UPDATE_FAILED", "message": str(e)}
        )


@router.delete("/backtests/{backtest_id}")
@limiter.limit("50/minute")
async def delete_backtest(request: Request,
    backtest_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Delete a backtest.

    Permanently removes backtest record and results.

    WHY THE RESPONSE IS A CONSTANT (Requirements 20.1, 20.2)
    --------------------------------------------------------
    ``BacktestService.delete_backtest`` now reports truthfully whether its
    ownership-scoped DELETE (``id`` AND ``user_id``) matched a row; it used to
    ``return True`` regardless. This handler deliberately does not forward that answer.

    Requirement 20.1 is discharged by the predicate on the DELETE statement - a non-owner's
    request removes nothing, which ``tests/sandbox_lifecycle/`` asserts on the rows
    themselves. Requirement 20.2 then requires that "this backtest is not yours" and "there
    is no such backtest" be indistinguishable. Forwarding ``deleted`` would answer
    ``{"success": false}`` in both of those cases and so would not, by itself, leak; but the
    pair (owner -> ``true``, everyone else -> ``false``) makes the response a probe for
    whether the CALLER owns the named row, and an existing client that treats ``false`` as
    an error would begin reporting a failure for an already-absent backtest. So the wire
    answer stays the one it has always been, for every caller, and the truthful value stays
    in-process where its consumers are.
    """
    try:
        backtest_service = await get_backtest_service()
        
        await backtest_service.delete_backtest(
            user=user,
            backtest_id=backtest_id
        )
        
        return {"success": True}
    except Exception as e:
        logger.error(f"Error deleting backtest {backtest_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTEST_DELETE_FAILED", "message": str(e)}
        )


@router.post("/backtests/validate-data")
@limiter.limit("100/minute")
async def validate_historical_data(request: Request,
    body: Dict[str, Any],
    user: dict = Depends(get_current_user)
):
    """
    Validate historical data availability and quality before backtest.
    
    Checks for:
    - Data availability
    - Data gaps
    - Duplicate timestamps
    - Invalid OHLCV values
    - Timestamp ordering
    - Sufficient warmup period
    - OHLCV consistency
    """
    try:
        backtest_service = await get_backtest_service()
        
        validation_result = await backtest_service.validate_historical_data(
            symbol=body.get("symbol", "BTC/USDT"),
            timeframe=body.get("timeframe", "1h"),
            start_date=body.get("start_date"),
            end_date=body.get("end_date")
        )
        
        return validation_result
    except Exception as e:
        logger.error(f"Error validating historical data for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DATA_VALIDATION_FAILED", "message": str(e)}
        )


# ══════════════════════════════════════════════════════════════════════════
# OPTIMIZATION OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

@router.post("/strategies/{strategy_id}/optimize")
@limiter.limit("10/minute")
async def run_optimization(request: Request, 
    strategy_id: str,
    body: OptimizationRequest,
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
        strategy = await strategy_service.get_strategy(user=user, strategy_id=strategy_id)
        
        if not strategy:
            raise HTTPException(
                status_code=404,
                detail={"error": "STRATEGY_NOT_FOUND", "message": f"Strategy {strategy_id} not found"}
            )
        
        # Reconstruct Strategy Package from execution graph
        from backend_app.backend.strategy_compiler import StrategyPackage, ExecutionGraph
        
        execution_graph = ExecutionGraph(
            id=body.execution_graph.get("id", str(uuid4())),
            version=body.execution_graph.get("version", "v1.0"),
            nodes=body.execution_graph.get("nodes", []),
            edges=body.execution_graph.get("edges", []),
            execution_order=body.execution_graph.get("execution_order", []),
            metadata=body.execution_graph.get("metadata", {})
        )
        
        strategy_package = StrategyPackage(
            id=str(uuid4()),
            strategy_id=strategy_id,
            version=strategy["version"]["version"] if strategy["version"] else "v1.0",
            execution_graph=execution_graph,
            metadata=execution_graph.metadata,
            dependencies=body.execution_graph.get("dependencies", {})
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
            method=OptimizationMethod(body.optimization_method),
            validation_method=ValidationMethod(body.validation_method),
            parameters=body.parameters,
            n_iterations=body.n_iterations,
            n_trials=body.n_trials,
            training_window_days=body.training_window_days,
            validation_window_days=body.validation_window_days,
            test_window_days=body.test_window_days,
            initial_capital=body.initial_capital,
            commission=body.commission,
            slippage=body.slippage,
            risk_per_trade=body.risk_per_trade,
            max_drawdown=body.max_drawdown,
            daily_loss_limit=body.daily_loss_limit
        )
        
        # Add date range to parameters
        config.parameters["start_date"] = body.start_date
        config.parameters["end_date"] = body.end_date
        
        # Generate research report
        research_report = await optimization_engine.generate_research_report(
            strategy_package=strategy_package,
            config=config,
            user=user,
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
            "optimization_method": body.optimization_method,
            "validation_method": body.validation_method,
            "n_iterations": body.n_iterations,
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
async def list_research_reports(request: Request, 
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """
    List all research reports for a Strategy.
    """
    try:
        from backend_app.core.dependencies import create_request_supabase_async
        
        sb_res = create_request_supabase_async(user.get("access_token"))
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        q = (sb.table("strategy_research_reports")
                 .select("*")
                 .eq("strategy_id", strategy_id)
                 .eq("user_id", user["id"])
                 .order("created_at", desc=True)
                 .execute())
        result = await q if inspect.isawaitable(q) else q
        
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
async def get_research_report(request: Request, 
    report_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get complete research report.
    """
    try:
        from backend_app.core.dependencies import create_request_supabase_async
        
        sb_res = create_request_supabase_async(user.get("access_token"))
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        q = sb.table("strategy_research_reports").select("*").eq("id", report_id).eq("user_id", user["id"]).execute()
        result = await q if inspect.isawaitable(q) else q
        
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
# DEPLOYMENT OPERATIONS (PIPELINE ENGINE)
# ══════════════════════════════════════════════════════════════════════════

@router.post("/strategies/{strategy_id}/deploy-pipeline")
@limiter.limit("10/minute")
async def deploy_strategy_pipeline(request: Request, 
    strategy_id: str,
    body: DeploymentRequest,
    user: dict = Depends(get_current_user)
):
    """
    Deploy a strategy to live trading pipeline.
    
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
        strategy = await strategy_service.get_strategy(user=user, strategy_id=strategy_id)
        
        if not strategy:
            raise HTTPException(
                status_code=404,
                detail={"error": "STRATEGY_NOT_FOUND", "message": f"Strategy {strategy_id} not found"}
            )
        
        # PHASE K: Check deployment gate - strategy must be approved by research
        from backend_app.core.dependencies import create_request_supabase_async
        sb_res = create_request_supabase_async(user.get("access_token"))
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        # Get latest research report
        q1 = (sb.table("strategy_research_reports")
                         .select("*")
                         .eq("strategy_id", strategy_id)
                         .eq("user_id", user["id"])
                         .order("created_at", desc=True)
                         .limit(1)
                         .execute())
        research_result = await q1 if inspect.isawaitable(q1) else q1
        
        if not research_result.data or not research_result.data[0].get("deployment_approved"):
            raise HTTPException(
                status_code=400,
                detail={"error": "DEPLOYMENT_GATE_FAILED", "message": "Strategy must pass research deployment gate before deployment"}
            )
        
        # Reconstruct Strategy Package from execution graph
        from backend_app.backend.strategy_compiler import StrategyPackage, ExecutionGraph
        from backend_app.backend.deployment_manager import StrategyDeploymentConfig, DeploymentEnvironment
        
        execution_graph = ExecutionGraph(
            id=body.execution_graph.get("id", str(uuid4())),
            version=body.execution_graph.get("version", "v1.0"),
            nodes=body.execution_graph.get("nodes", []),
            edges=body.execution_graph.get("edges", []),
            execution_order=body.execution_graph.get("execution_order", []),
            metadata=body.execution_graph.get("metadata", {})
        )
        
        # Create deployment config
        deployment_config = StrategyDeploymentConfig(
            deployment_id=str(uuid4()),
            strategy_id=strategy_id,
            user_id=user["id"],
            version_id=strategy["version"]["id"] if strategy["version"] else None,
            version=strategy["version"]["version"] if strategy["version"] else "v1.0",
            execution_graph=execution_graph,
            environment=DeploymentEnvironment(body.environment),
            exchange_id=body.exchange_id,
            exchange_symbol=body.exchange_symbol,
            worker_region=body.worker_region,
            initial_capital=body.initial_capital,
            risk_per_trade=body.risk_per_trade,
            max_drawdown=body.max_drawdown,
            daily_loss_limit=body.daily_loss_limit
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
            "environment": body.environment,
            "status": deployment_state.status.value,
            "worker_id": deployment_state.worker.worker_id if deployment_state.worker else None,
            "exchange_id": body.exchange_id,
            "exchange_symbol": body.exchange_symbol,
            "worker_region": body.worker_region,
            "initial_capital": body.initial_capital,
            "started_at": deployment_state.started_at,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        q2 = sb.table("strategy_deployments").insert(deployment_data).execute()
        if inspect.isawaitable(q2):
            await q2
        
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
async def pause_deployment(request: Request, 
    deployment_id: str,
    body: Optional[DeploymentTransitionRequest] = None,
    user: dict = Depends(get_current_user)
):
    """Pause a running deployment (Requirement 13.8).

    Now goes through the lifecycle state machine rather than straight to the in-process
    runtime, which changes three things and is the point of task 8.3:

    * **Ownership is enforced.** The deployment is read with ``.eq("user_id", ...)`` and
      the write carries the same filter. The previous implementation passed the path
      parameter to the runtime and then updated the row by id alone, with no tenant
      filter anywhere - so one user could pause another user's deployment. That is fixed
      here, not weakened.
    * **The transition is gated.** Pausing a stopped deployment is a **409** naming the
      current state instead of a 500 from a runtime that has never heard of it; pausing an
      already-paused one is idempotent.
    * **The version moves with it** (``RUNNING -> PAUSED``) and the act is audited with
      actor, timestamp and reason.

    Responses
        **200** paused, or already paused. **404** not this user's. **409** not pausable
        from the current state. **429** from the existing limiter.
    """
    return await _transition_deployment_endpoint(
        "pause", deployment_id, user, body.reason if body is not None else None
    )


@router.post("/deployments/{deployment_id}/resume")
@limiter.limit("50/minute")
async def resume_deployment(request: Request, 
    deployment_id: str,
    body: Optional[DeploymentTransitionRequest] = None,
    user: dict = Depends(get_current_user)
):
    """Resume a paused deployment (Requirement 13.8).

    ``PAUSED -> RUNNING`` on the binding and on the version, gated, tenant-scoped and
    audited - see :func:`pause_deployment` for what that changed. A deployment that is
    already running is idempotent; one that is stopped or failed is a **409**, because a
    stopped deployment is not resumed, a new one is deployed: execution references an
    immutable ``version_id`` and reusing the row would lose which plan ran when.
    """
    return await _transition_deployment_endpoint(
        "resume", deployment_id, user, body.reason if body is not None else None
    )


@router.post("/deployments/{deployment_id}/restart")
@limiter.limit("50/minute")
async def restart_deployment(request: Request, 
    deployment_id: str,
    user: dict = Depends(get_current_user)
):
    """Restart a deployment.

    **Ownership is enforced, and was not** (strategy-builder task 8.7, Requirements 21.2
    and 21.4). This handler passed the path parameter straight to the in-process
    ``deployment_manager`` - which keys its registry by deployment id and knows nothing
    about tenants - and then updated ``strategy_deployments`` filtered by ``id`` alone.
    Any authenticated user who knew (or guessed) another user's deployment id could
    restart their live strategy and flip their row to ``running``. The row is now read
    first, scoped to the caller, and a deployment that is not theirs is a **404** rather
    than a 403 so an id cannot be probed for existence across tenants - the same
    convention :func:`pause_deployment`, :func:`resume_deployment` and
    :func:`stop_deployment` adopted in task 8.3. The follow-up write carries the same
    filter, so the statement is scoped even if this read is ever moved.

    What is deliberately NOT changed here: the in-process registry losing a deployment
    still surfaces as a 500. That is pre-existing behaviour and not an isolation defect -
    ``restart`` is not one of Requirement 13.8's four transitions and has no entry in the
    lifecycle state machine, so giving it a state-aware answer belongs with whichever task
    adopts it, not with a security fix.
    """
    from backend_app.core.dependencies import create_request_supabase_async

    sb_res = create_request_supabase_async(user.get("access_token"))
    sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
    if sb is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "DEPLOYMENT_RESTART_UNAVAILABLE",
                "message": "No database client is available, so ownership of this "
                           "deployment could not be verified and nothing was restarted.",
            },
        )

    try:
        owned_q = (
            sb.table("strategy_deployments")
            .select("id")
            .eq("id", deployment_id)
            .eq("user_id", user["id"])
            .limit(1)
            .execute()
        )
        owned = await owned_q if inspect.isawaitable(owned_q) else owned_q
    except Exception as e:
        logger.error(
            "Ownership of deployment %s could not be verified for user %s: %s",
            deployment_id,
            user["id"],
            e,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "DEPLOYMENT_RESTART_UNAVAILABLE",
                "message": "Ownership of this deployment could not be verified, so "
                           "nothing was restarted.",
            },
        )

    if not owned or not getattr(owned, "data", None):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "DEPLOYMENT_NOT_FOUND",
                "message": f"Deployment {deployment_id} not found",
            },
        )

    try:
        deployment_manager = get_deployment_manager()
        deployment_state = await deployment_manager.restart_deployment(deployment_id)

        q = (
            sb.table("strategy_deployments")
            .update({"status": "running"})
            .eq("id", deployment_id)
            .eq("user_id", user["id"])
            .execute()
        )
        if inspect.isawaitable(q):
            await q
        
        return {"status": "restarted", "deployment_state": deployment_state}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error restarting deployment {deployment_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOYMENT_RESTART_FAILED", "message": str(e)}
        )


@router.post("/deployments/{deployment_id}/stop", include_in_schema=False)
@limiter.limit("50/minute")
async def stop_deployment_alias(request: Request, 
    deployment_id: str,
    body: Optional[DeploymentTransitionRequest] = None,
    user: dict = Depends(get_current_user)
):
    """The second registration of ``POST /deployments/{id}/stop``, kept behaving identically.

    This path is declared **twice** in this router - once above (the 100/minute handler)
    and once here - which pre-dates this task. FastAPI matches the first registration, so
    this one was already unreachable; it is left in place because deleting a route is a
    larger blast radius than this task's scope, and pointed at the same implementation so
    that if the ordering ever changes the behaviour does not. ``include_in_schema=False``
    keeps the duplicate out of the OpenAPI document, where two entries for one path is a
    contract nobody can honour.
    """
    return await _transition_deployment_endpoint(
        "stop", deployment_id, user, body.reason if body is not None else None
    )


@router.get("/strategies/{strategy_id}/deployments")
@limiter.limit("200/minute")
async def list_deployments(request: Request, 
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
async def get_deployment(request: Request, 
    deployment_id: str,
    user: dict = Depends(get_current_user)
):
    """Get deployment details.

    **Ownership is enforced, and was not** (strategy-builder task 8.7, Requirements 21.2
    and 21.4). This handler asked ``deployment_manager.get_deployment(deployment_id)`` -
    an in-process registry keyed by deployment id with no tenant concept whatsoever - and
    returned the whole state: worker id, region, host metrics, heartbeat, error message and
    every runtime metric. Any authenticated user holding another user's deployment id read
    all of it. The authoritative record is the ``strategy_deployments`` row, so that is
    read **first**, scoped to the caller with an explicit ``user_id`` filter on top of the
    request-scoped client's RLS; a deployment that is not theirs is a **404**, identical to
    one that does not exist, so an id cannot be probed across tenants.

    The in-process runtime is consulted **second, and only for a deployment the caller
    already owns**. A deployment created by ``deploy_version`` is not in that registry at
    all (that path starts a fleet bot), so its absence is not an error: the row's own
    state is reported and the worker and health blocks come back zeroed, exactly as they
    did for a deployment whose worker had not attached. That is why this is a 200 with
    ``runtime_attached: false`` rather than the 404 the old handler gave for every
    fleet-started deployment - a deployment that demonstrably exists was being reported as
    missing to its own owner.

    Responses
        **200** the deployment. **404** no such deployment belongs to this user.
        **429** from the existing limiter.
    """
    from backend_app.backend import strategy_lifecycle as lifecycle
    from backend_app.core.dependencies import create_request_supabase_async

    # ── 1. Ownership, from the authoritative row, before anything is read ──
    sb_res = create_request_supabase_async(user.get("access_token"))
    sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
    row: Dict[str, Any] = {}
    if sb is not None:
        try:
            owned_q = (
                sb.table("strategy_deployments")
                .select("*")
                .eq("id", deployment_id)
                .eq("user_id", user["id"])
                .limit(1)
                .execute()
            )
            owned = await owned_q if inspect.isawaitable(owned_q) else owned_q
            rows = (getattr(owned, "data", None) or []) if owned else []
            row = dict(rows[0]) if rows else {}
        except Exception as e:
            logger.error(
                "Ownership of deployment %s could not be verified for user %s: %s",
                deployment_id,
                user["id"],
                e,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "DEPLOYMENT_LOOKUP_UNAVAILABLE",
                    "message": "Ownership of this deployment could not be verified, so "
                               "no deployment was returned.",
                },
            )

    if not row:
        # Not this user's, or not there at all. One answer for both (Requirement 21.4).
        raise HTTPException(
            status_code=404,
            detail={
                "error": "DEPLOYMENT_NOT_FOUND",
                "message": f"Deployment {deployment_id} not found",
            },
        )

    try:
        deployment_manager = get_deployment_manager()
        deployment_state = deployment_manager.get_deployment(deployment_id)

        if not deployment_state:
            # Owned, but not held by this process's registry. Report the row.
            return {
                **lifecycle.binding_report(row),
                "status": str(row.get("status") or ""),
                "environment": row.get("environment") or row.get("mode"),
                "runtime_attached": False,
                "worker": {
                    "worker_id": None,
                    "status": None,
                    "region": None,
                    "cpu_usage": 0,
                    "memory_usage": 0,
                    "latency_ms": 0,
                    "restart_count": 0,
                    "uptime_seconds": 0,
                },
                "health": {},
                "error_message": row.get("error_message"),
                "runtime_metrics": {},
            }

        return {
            "deployment_id": deployment_state.deployment_id,
            "runtime_attached": True,
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
async def compare_backtests(request: Request, 
    backtest_ids: List[str],
    user: dict = Depends(get_current_user)
):
    """
    Compare multiple backtests.
    
    Returns side-by-side comparison of metrics.
    """
    try:
        backtest_service = await get_backtest_service()
        
        comparison = await backtest_service.compare_backtests(user=user, backtest_ids=backtest_ids)
        
        return comparison
    except Exception as e:
        logger.error(f"Error comparing backtests for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "BACKTEST_COMPARE_FAILED", "message": str(e)}
        )


@router.delete("/backtests/{backtest_id}")
@limiter.limit("50/minute")
async def delete_backtest(request: Request, 
    backtest_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Delete a backtest.

    UNREACHABLE, AND NOT TO BE UNSHADOWED AS IT STANDS
    --------------------------------------------------
    This module declares ``DELETE /backtests/{backtest_id}`` twice. FastAPI matches the
    FIRST registration, which is the handler ~800 lines above; nothing routes here, and
    ``tests/sandbox_lifecycle/test_cross_tenant_ownership_matrix.py`` pins that fact
    (``backtests.archive`` asserts the registration that answers is the earlier one).

    It is left in place rather than deleted because removing a route declaration is a
    surface change this remediation was not scoped to make. But note what it would do if it
    ever became the one that answers: ``delete_backtest`` is now truthful about whether the
    ownership-scoped DELETE matched, so ``if not success -> 404`` here would answer the
    OWNER 200 and everybody else 404 - i.e. it would become exactly the existence oracle
    Requirement 20.2 forbids. Whoever resolves the duplication must make this branch
    unconditional first, the way the live handler does.
    """
    try:
        backtest_service = await get_backtest_service()
        
        success = await backtest_service.delete_backtest(user=user, backtest_id=backtest_id)
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
async def get_version_history(request: Request, 
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """Get complete version history for a Strategy.

    Each version carries a ``canvas`` block (task 8.3, Requirement 9.9): the backend's own
    verdict on whether that version may be edited, which of its fields are frozen, what
    the legal next lifecycle transitions are, and a sentence explaining it. The Builder
    renders the canvas read-only from this rather than re-deriving "is it deployed?" from
    a state string, so the UI cannot offer an edit that migration 004c's immutability
    trigger would then reject. The version rows themselves are unchanged - the block is
    added alongside them, so no existing reader breaks.
    """
    from backend_app.backend import strategy_lifecycle as lifecycle

    try:
        service = await get_strategy_service()
        
        versions = await service.get_version_history(user=user, strategy_id=strategy_id)

        annotated = [
            {**version, "canvas": lifecycle.canvas_state(version)}
            if isinstance(version, dict)
            else version
            for version in (versions or [])
        ]
        
        return {
            "strategy_id": strategy_id,
            "versions": annotated,
            "total": len(annotated),
            "read_only_states": list(lifecycle.READ_ONLY_LIFECYCLE_STATES),
        }
    except Exception as e:
        logger.error(f"Error getting version history for strategy {strategy_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "VERSION_HISTORY_FAILED", "message": str(e)}
        )


@router.post("/strategies/{strategy_id}/versions/compare")
@limiter.limit("100/minute")
async def compare_versions(request: Request, 
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
        
        comparison = await service.compare_versions(user=user, strategy_id=strategy_id, version_a=version_a, version_b=version_b)
        
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
async def restore_version(request: Request, 
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
        
        new_version = await service.restore_version(user=user, strategy_id=strategy_id, version=version)
        
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


class DeploymentBindingRequest(BaseModel):
    """The extended deploy body (task 8.2, Requirement 13.1).

    ``design.md`` -> ``POST .../versions/{v}/deploy`` "existing path, **extended body**".
    Every field is optional at the schema level; Requirement 13.6 is what makes
    ``exchange_account_id`` mandatory for ``mode = 'live'``, and it is enforced in the
    Deployment_Service so the refusal names the missing account.

    **This model declares no api key, secret or passphrase, and it never will.** An
    exchange account is named by id; its credentials are resolved by
    ``load_decrypted_keys(user_id, exchange_id)`` inside the execution process and travel
    on no request and no response (Requirements 13.9, 21.7). ``extra="forbid"`` is
    deliberate: a client that invents a field is told so, rather than having a limit it
    believes it set silently dropped.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Optional[str] = Field(
        None, description="Deployment binding mode: 'paper' or 'live' (chk_sd_mode)"
    )
    exchange_account_id: Optional[str] = Field(
        None,
        description=(
            "Connected exchange account id from the user's vault. A reference, never a "
            "credential. Required when mode is 'live'."
        ),
    )
    risk_config_id: Optional[str] = Field(
        None, description="Risk configuration id owned by the requesting user"
    )
    execution_config: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "max_order_notional, max_open_positions, slippage_tolerance_bps, "
            "order_timeout_seconds, retry_policy"
        ),
    )
    gap_strategy: Optional[str] = Field(
        None,
        description=(
            "Requested market-data gap handling. Synthetic and interpolating fills are "
            "refused; a live binding may not fill gaps at all (Requirement 13.7)."
        ),
    )


def _deploy_rejected_http(exc: Any) -> HTTPException:
    """One ``DeployRejected`` as its HTTP answer.

    The status lives on the exception, decided once in ``deployment_binding``, so the two
    deploy surfaces cannot disagree about whether a non-READY version is a 409 or a 422.
    A refusal about a resource the caller does not own arrives here already carrying 404,
    never 403.
    """
    return HTTPException(status_code=exc.http_status, detail=exc.to_detail())


@router.post("/strategies/{strategy_id}/versions/{version}/deploy")
@limiter.limit("50/minute")
async def deploy_version(request: Request, 
    strategy_id: str,
    version: str,
    environment: str = Query("paper", description="Deployment environment"),
    body: Optional[DeploymentBindingRequest] = None,
    user: dict = Depends(get_current_user)
):
    """Bind one immutable version to an account, a risk profile, an execution config and a mode.

    Requirement 13.1: the version identifier, the exchange account identifier, the risk
    configuration identifier, the execution configuration and the mode are recorded as one
    Deployment_Binding. Deployment always references an immutable version.

    Gates, all of them refusing before anything is written (Requirements 13.2-13.7):

    * the version, the exchange account and the risk configuration must each belong to the
      requesting user - another tenant's resource is a **404**, never a 403;
    * the version's lifecycle state must be ``READY``, and a refusal names the state and
      the outstanding prerequisite (**409**);
    * the symbol and the timeframe come from the version's own DATA node and must be ones
      the target account's venue lists and serves (**422**, naming the symbol or timeframe
      and the account);
    * ``mode = 'live'`` requires an exchange account (**422**) and forbids synthetic gap
      filling (**422**).

    Credentials are never in this exchange. The body names an account; the keys are read
    by the execution process (Requirement 13.9).

    Responses
        **200** ``{"deployment": row, "binding": {...}, "success", "message"}``.
        ``binding.binding_stored`` is ``false`` when
        ``004e_deployment_bindings.sql`` has not been applied - the binding columns were
        not written and the response says so rather than reporting a bound deployment.
        **404** no such version, account or risk config belongs to this user.
        **409** the version is not deployable yet.
        **422** a binding the platform refuses to create.
        **503** the market universe is not loaded, or a live binding cannot be recorded.
        **429** from the existing limiter.
    """
    from backend_app.backend.deployment_binding import BindingRequest, DeployRejected

    try:
        service = await get_strategy_service()

        binding_request = BindingRequest.from_payload(
            body.model_dump(exclude_none=True) if body is not None else None
        )
        deployment = await service.deploy_version(
            user=user,
            strategy_id=strategy_id,
            version=version,
            environment=environment,
            binding_request=binding_request,
        )
        
        return deployment
    except DeployRejected as e:
        raise _deploy_rejected_http(e)
    except DeployPrerequisiteError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "DEPLOY_PREREQUISITE_NOT_MET",
                "message": str(e),
                "missing_prerequisite": e.missing_prerequisite,
            }
        )
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


# ── GET .../versions/{version}/deploy/preflight (task 8.2) ───────────────────
# Requirements 13.3-13.6. `design.md` -> API surface: "Calls `evaluate_binding_summary`
# (collect-all, read-only)", response shape `{deployable, conditions:[...]}`.
#
# BOTH PATHS, DELIBERATELY - and why this is not a new route pattern
# -----------------------------------------------------------------
# `design.md` documents the path as `/api/strategy-operations/strategies/{id}/versions/
# {version}/deploy/preflight`, while the POST it belongs to is registered here as
# `/strategies/{id}/versions/{version}/deploy` (so, `/api/strategies/...`). Registering
# both is exactly what `execute_backtest` above already does for the same reason: the
# documented `strategy-operations` path has to resolve, and the sibling path the existing
# deploy uses has to stay consistent with it. Both decorators name **one** handler, so the
# two paths cannot answer differently.
#
# THE ROUTING HAZARD THIS ROUTE HAD TO CLEAR
# ------------------------------------------
# `routers/strategies.py` is mounted at `/api/strategies` *before* this router is mounted
# at `/api`, and first-match-wins: a path that router also declares is served by it, not by
# this one. It declares `GET /{strategy_id}` (one segment) and no `/{id}/versions/...` GET
# at all, so neither variant of this path is shadowed - but that is a fact about the other
# file, so `tests/test_task_8_2_deploy_preflight.py` asserts the resolution against the
# real `backend_app.main.app`, not against a router assembled for the test.
#
# WHY A NON-DEPLOYABLE ANSWER IS 200
# ----------------------------------
# The summary's conditions each carry an `http_status` the write path would use; this
# endpoint does not. A preflight is a 200 answer *about* a deployment, so
# `{"deployable": false}` with every failed condition named is a successful preflight, not a
# refused request. Only a question this endpoint cannot answer at all is a non-200: 404 for
# a version that is not this caller's, 409 for an archived strategy (Requirement 3.3) -
# both mapped through the deploy route's own `_deploy_rejected_http`, so the two surfaces
# cannot disagree.
#
# 200/minute, matching `GET /strategies/{strategy_id}` - the comparable existing endpoint
# under Requirement 22.2 (the same "get" action on the closest equivalent resource). The
# 2-second poll task 18.1 runs while the modal is open is 30 requests a minute, so the
# convention is also the right ceiling; the deploy POST's 50/minute would not be.
_PREFLIGHT_PATHS = (
    "/strategy-operations/strategies/{strategy_id}/versions/{version}/deploy/preflight",
    "/strategies/{strategy_id}/versions/{version}/deploy/preflight",
)


@router.get(_PREFLIGHT_PATHS[0])
@router.get(_PREFLIGHT_PATHS[1])
@limiter.limit("200/minute")
async def preflight_deploy_version(request: Request,
    strategy_id: str,
    version: str,
    environment: str = Query("paper", description="Legacy environment fallback for mode"),
    mode: Optional[str] = Query(None, description="'paper' or 'live' (chk_sd_mode)"),
    exchange_account_id: Optional[str] = Query(
        None,
        description=(
            "Connected exchange account id from the user's vault. A reference, never a "
            "credential. Required when mode is 'live'."
        ),
    ),
    risk_config_id: Optional[str] = Query(
        None, description="Risk configuration id owned by the requesting user"
    ),
    gap_strategy: Optional[str] = Query(
        None, description="Requested market-data gap handling (Requirement 13.7)"
    ),
    execution_config: Optional[str] = Query(
        None,
        description=(
            "The execution_config document the deploy would carry, as a JSON object. "
            "Validated by the same normaliser the write path uses, so a field the deploy "
            "would refuse is reported as a failed condition here rather than discovered "
            "on submit."
        ),
    ),
    user: dict = Depends(get_current_user)
):
    """Every mandatory deployment condition for one version, each with its own verdict.

    Trading-lifecycle-integration task 8.2. Requirements 13.3, 13.4, 13.5, 13.6.

    Read-only and side-effect-free: no deployment row, no quota reservation, no runtime
    call, no exchange order, nothing written anywhere. That is a contract, not an
    incidental property - the deployment configuration workflow polls this every two
    seconds while it is open (13.6), so a version of it that wrote or reserved anything
    would consume a user's bot allowance by having a modal open.

    The gates are :func:`evaluate_binding`'s own, run through
    ``evaluate_binding_summary``'s collect-all convention (task 8.1): a refusal becomes a
    reported condition instead of a raised exception, so 13.2's "report every failed
    condition" holds and the workflow can show the author all of them at once. A condition
    whose input a failed condition was supposed to produce is ``pending``, never omitted
    and never ``passed``.

    ``deployable`` is what 13.4/13.5 bind the Deploy button to: false while any condition
    is ``failed`` **or** ``pending``, and true only when every one has passed. Because the
    answer is recomputed from scratch on each poll, 13.6's "a condition that had passed
    subsequently fails" needs nothing extra here - the next poll simply reports it.

    Responses
        **200** ``{"deployable": bool, "conditions": [{"name", "status", "detail"?,
        "code"?, "message"?, "reason"?}]}``. ``deployable: false`` is a successful
        preflight.
        **404** ``VERSION_NOT_FOUND`` - no such version belongs to this caller, which is
        also the answer for another tenant's version (Requirement 20.2).
        **409** ``STRATEGY_ARCHIVED`` (Requirement 3.3).
        **422** ``PREFLIGHT_REQUEST_INVALID`` - ``execution_config`` is not a JSON object.
        **429** from the existing limiter.
    """
    import json as _json

    from backend_app.backend.deployment_binding import BindingRequest, DeployRejected

    parsed_execution_config: Optional[Dict[str, Any]] = None
    if execution_config is not None:
        try:
            parsed_execution_config = _json.loads(execution_config)
        except (TypeError, ValueError) as e:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "PREFLIGHT_REQUEST_INVALID",
                    "message": (
                        "execution_config must be a JSON object naming the execution "
                        f"limits this deployment would run under: {e}"
                    ),
                },
            )
        if not isinstance(parsed_execution_config, dict):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "PREFLIGHT_REQUEST_INVALID",
                    "message": "execution_config must be a JSON object.",
                },
            )

    try:
        service = await get_strategy_service()

        binding_request = BindingRequest.from_payload(
            {
                key: value
                for key, value in {
                    "mode": mode,
                    "exchange_account_id": exchange_account_id,
                    "risk_config_id": risk_config_id,
                    "gap_strategy": gap_strategy,
                    "execution_config": parsed_execution_config,
                }.items()
                if value is not None
            }
        )
        summary = await service.preflight_version_deployment(
            user=user,
            strategy_id=strategy_id,
            version=version,
            environment=environment,
            binding_request=binding_request,
        )

        # `design.md`'s shape, produced by the summary itself. Not rebuilt here: a second
        # serialisation is a second thing to keep in step with the gates.
        return summary.to_dict()
    except DeployRejected as e:
        # Only a refusal *of the preflight request* reaches here - an archived strategy.
        # Every refusal about the deployment is already a reported condition.
        raise _deploy_rejected_http(e)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={"error": "VERSION_NOT_FOUND", "message": str(e)}
        )
    except Exception as e:
        logger.error(
            "Error running deploy preflight for version %s of strategy %s for user %s: %s",
            version,
            strategy_id,
            user.get("id"),
            e,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "VERSION_DEPLOY_PREFLIGHT_FAILED", "message": str(e)}
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

# ══════════════════════════════════════════════════════════════════════════
# BLOCK REGISTRY (task 3.1) — the palette contract, backend-authoritative
# ══════════════════════════════════════════════════════════════════════════
# One assembled registry, served five ways. `design.md` -> Registry response shape is
# produced by `BlockRegistry.to_dict()`; this section transports it and adds nothing to
# it. The four narrower endpoints project the *same assembled payload*, so a block can
# never appear in `/registry/indicators` and be missing from `/registry/blocks`
# (Requirements 4.1, 4.11, 5.1, 6.9).
#
# Path note: this router is mounted at `prefix="/api"` (`backend_app/main.py`), so the
# `strategy-operations` segment is carried in the route paths here. That is what makes the
# live paths `/api/strategy-operations/registry/*`, which is what `design.md` -> API
# surface documents and what `strategies.BLOCKS_REPLACEMENT_ENDPOINT` advertises as the
# successor to the deprecated `GET /api/strategies/blocks` alias.
# ══════════════════════════════════════════════════════════════════════════

#: The registry is per-user-agnostic but auth-gated, so it must not land in a shared
#: cache; revalidation is cheap because the ETag is a content hash (Requirement 4.15).
_REGISTRY_CACHE_CONTROL = "private, max-age=0, must-revalidate"

#: ``Content-Encoding`` applied when the caller advertises it (task 9.5, Requirement
#: 4.15: "SHALL keep the gzipped response at 250 KB or smaller"; `design.md` -> Performance:
#: "Registry payload | < 250 KB gzipped, cached by ``ETag`` | fetched once per session").
#:
#: WHY THIS LIVES IN `_registry_response` AND NOT IN A MIDDLEWARE
#: ------------------------------------------------------------
#: `GZipMiddleware` would compress every response the application serves - order fills,
#: risk decisions, deployment mutations - to close a budget stated for one read-only
#: reference payload. That is a change of blast radius, not of scope, and it would also
#: strip the deterministic `Content-Length` from streaming responses elsewhere. The budget
#: belongs to the registry, so the compression does too, and it goes in the one function
#: every registry endpoint already returns through rather than beside it.
#:
#: LEVEL 6, NOT 9
#: --------------
#: Measured on the assembled registry (100 blocks, 172 400 bytes of compact JSON):
#: level 1 -> 20 571 bytes in 2.6 ms, level 6 -> 15 863 bytes in 4.1 ms, level 9 ->
#: 15 640 bytes in 7.4 ms. Level 9 buys 223 bytes for 3.3 ms against a ceiling that is
#: already met sixteen times over, so 6 is the balance point. All three are far below
#: 250 KB; the figure is asserted from the served bytes in
#: `tests/test_task_9_5_registry_budget.py`, not from this comment.
#:
#: NO SERVER-SIDE CACHE OF THE COMPRESSED BYTES, DELIBERATELY
#: ---------------------------------------------------------
#: The second and every later request in a session carries `If-None-Match` and is answered
#: `304` with no body, so nothing is compressed on the hot path at all - the `ETag` cache
#: task 3.1 built *is* the answer to the repeat cost. A byte cache would only shorten cold
#: requests, at the price of a second cache to keep coherent with the payload; the
#: `registry_version` hash guarantees only that the *blocks* changed, and
#: `/registry/timeframes` mixes in pipeline vocabularies it does not cover, so a cache
#: keyed on the tag would be subtly wrong for that projection. Not worth 4 ms.
_REGISTRY_GZIP_LEVEL = 6

#: Below this many bytes the ~18-byte gzip envelope plus the CPU is not repaid. Starlette's
#: own `GZipMiddleware` uses the same 500-byte floor for the same reason. Only
#: `/registry/timeframes` is ever near it.
_REGISTRY_GZIP_MIN_BYTES = 500


def _accepts_gzip(request: Request) -> bool:
    """Whether the caller advertised gzip in ``Accept-Encoding`` (RFC 9110 12.5.3).

    Negotiated rather than assumed: a client that asked for no encoding, or that
    explicitly forbade this one with ``gzip;q=0``, gets the identity representation. The
    two bodies are byte-identical once decoded - the compressed branch compresses the very
    bytes the identity branch would have served - so the choice changes transfer size and
    nothing else.
    """
    header = request.headers.get("accept-encoding")
    if not header:
        return False
    for token in header.split(","):
        name, _, parameters = token.strip().partition(";")
        if name.strip().lower() not in ("gzip", "x-gzip"):
            continue
        for parameter in parameters.split(";"):
            key, _, value = parameter.partition("=")
            if key.strip().lower() != "q":
                continue
            try:
                # `gzip;q=0` is a refusal, not a preference (RFC 9110 12.5.3).
                if float(value.strip()) == 0.0:
                    return False
            except ValueError:
                return False
        return True
    return False


def _builder_metrics():
    """``backend/metrics.py``'s collector, or ``None``. Lazy and guarded (task 9.1).

    Requirement 24.1's registry and asset-universe counters are recorded through the
    platform's existing collector. Guarded because a metrics failure must not turn a served
    registry into a 500.
    """
    try:
        from backend_app.backend.metrics import metrics_collector

        return metrics_collector
    except Exception:  # noqa: BLE001 - instrumentation never breaks its caller
        return None


def _builder_alert_module():
    """``backend/builder_alerts.py``, or ``None``. Lazy and guarded (task 9.2).

    Requirements 24.4/24.5. Separate from :func:`_builder_metrics` because it is a separate
    failure - the alert module reaches the platform's dispatcher, and therefore ``aiohttp``
    and Redis - and guarded for the same reason: an alert must never turn a served response
    into a 500.
    """
    try:
        from backend_app.backend import builder_alerts

        return builder_alerts
    except Exception:  # noqa: BLE001 - an alert never breaks the act it observes
        return None


#: Where the timeframe vocabulary comes from. There is no single canonical timeframe
#: constant in this codebase, and a hand-written list here would be the same defect class as
#: the hardcoded symbol universe task 7.2 retired. So the served set is the **intersection**
#: of the pipeline's own vocabularies - `design.md` -> "derived from the intersection of
#: timeframes the platform's data pipeline supports" (Requirement 11.8).
#:
#: A source belongs here when a missing label changes what the pipeline *does* to the data:
#:
#:   ``backtesting_engine.VALID_FREQ_MAP``   the resampling whitelist. An absent label
#:                                           raises ``ValueError`` - the backtest is refused.
#:   ``master_executor.TF_SEC``              the live loop's bar-interval table. An absent
#:                                           label falls back to 60s, so the executor would
#:                                           aggregate the wrong bars silently.
#:   ``market_data_validation.TIMEFRAME_MINUTES``
#:                                           the row-coverage gate. An absent label is
#:                                           measured as if it were an hour, which makes the
#:                                           completeness check unfailable - a disarmed
#:                                           control, not a cosmetic gap.
#:
#: Considered and deliberately **not** included, recorded so the omissions are decisions
#: rather than oversights (task 7.2):
#:
#: * The DATA descriptor's ``timeframe`` param (``block_specs._timeframe_param``) publishes
#:   **no** ``options`` tuple - it is ``required`` with ``default=None`` by SB-06 - so it
#:   states no vocabulary and can contribute no constraint. This endpoint is what the
#:   selector reads instead (Requirement 11.8).
#: * ``data_seeking_engine.DataEngine`` holds no whitelist: it forwards the timeframe string
#:   to CCXT, so it accepts whatever the venue accepts. An empty constraint intersected in
#:   would be a no-op, and pretending it were a vocabulary would be inventing one.
#: * ``backtest_service._timeframe_to_minutes`` is method-local and feeds a *gap warning*
#:   only. A missing label there produces a visible advisory, gates nothing, and its narrower
#:   key set would drop 2h/6h/12h - intervals the resampler, the executor and the coverage
#:   gate all handle - so intersecting it would understate what the platform supports.
#: * The ML/dataset admission constraints (minimum rows, sequence length, warmup) bound the
#:   *quantity* of data at a given interval, not the set of intervals, so they do not bear on
#:   this set. They are enforced by the training admission gate, where they belong.
_TIMEFRAME_SOURCES = (
    ("backend_app.backend.backtesting_engine", "VALID_FREQ_MAP"),
    ("backend_app.backend.master_executor", "TF_SEC"),
    ("backend_app.backend.market_data_validation", "TIMEFRAME_MINUTES"),
)

#: Bar-interval suffix -> seconds. Used only to *order* and annotate labels that the
#: pipeline already accepts; it never adds a timeframe of its own.
_TIMEFRAME_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def _registry_payload() -> dict:
    """The assembled registry in its one wire shape, or a 5xx naming the failure.

    Assembly failures are the SB-03 guard reaching the HTTP layer: an empty category or a
    block whose runtime does not resolve must surface as a named error, never as a 200
    carrying an empty ``blocks`` list, because a silently blank palette is the defect this
    endpoint exists to close (Requirements 4.8, 4.9, 4.12).

    503 rather than 500: the palette is unavailable, the client's correct response is the
    retryable error state, and the message names the offending category or block so an
    operator does not have to reproduce it to find out what broke.
    """
    from backend_app.backend.strategy_dag.registry import (
        RegistryAssemblyError,
        get_registry,
    )

    try:
        return get_registry().to_dict()
    except RegistryAssemblyError as e:
        logger.error("Block registry assembly failed (%s): %s", type(e).__name__, e)
        raise HTTPException(
            status_code=503,
            detail={
                "error": getattr(e, "code", "REGISTRY_ASSEMBLY_FAILED"),
                "failure": type(e).__name__,
                "message": str(e),
            },
        )
    except Exception as e:
        logger.error("Block registry unavailable: %s", e)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "REGISTRY_UNAVAILABLE",
                "failure": type(e).__name__,
                "message": str(e),
            },
        )


def _registry_etag(registry_version: str, resource: str) -> str:
    """A quoted strong entity-tag for one registry resource.

    ``registry_version`` is already the deterministic hash of the assembled descriptor
    set, so an unchanged registry yields an unchanged tag and a changed one cannot reuse
    it (Requirement 4.15). ``resource`` keeps the projections' tags distinct: a client
    holding the ``/registry/blocks`` tag must not be told its ``/registry/indicators``
    copy is fresh.
    """
    return f'"{registry_version}-{resource}"'


def _if_none_match_matches(header: Optional[str], entity_tag: str) -> bool:
    """RFC 9110 ``If-None-Match`` evaluation: ``*``, a list, and weak prefixes."""
    if not header:
        return False
    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate == entity_tag:
            return True
    return False


def _registry_response(request: Request, payload: dict, resource: str):
    """200 with the payload, or 304 with no body when the client's copy is current.

    Also the one seam ``builder.registry.requests`` and ``builder.registry.cache_hits`` are
    recorded at (task 9.1, Requirement 24.1). One place, because every registry endpoint
    returns through here, so a projection added later is counted without being remembered.
    The label is the ``resource`` name already used for the entity-tag, which keeps the
    hit ratio readable per projection; no user, tenant or venue appears in it.

    Transfer coding (task 9.5, Requirement 4.15). A ``200`` is served gzipped when the
    caller advertised gzip, which is what keeps the payload inside the 250 KB budget the
    design states. Three details that are easy to get wrong and are therefore explicit:

    * The compressed bytes are produced from ``JSONResponse.render``'s own output, not from
      a second ``json.dumps`` call, so the gzip and identity representations cannot drift
      in whitespace, key order or float formatting - decoding one yields the other byte for
      byte.
    * ``Vary: Accept-Encoding`` travels on **both** the ``200`` and the ``304``, because the
      response body now depends on a request header and a shared cache that missed that
      would hand gzipped bytes to a client that cannot decode them.
    * The ``ETag`` is *not* varied by the encoding. It identifies the registry
      representation - the JSON - and both branches carry the identical JSON; a client that
      revalidates after switching encodings should still be told its copy is current.
      ``mtime=0`` keeps the compressed bytes reproducible so the served ``Content-Length``
      is a function of the payload alone.
    """
    entity_tag = _registry_etag(payload["registry_version"], resource)
    headers = {
        "ETag": entity_tag,
        "Cache-Control": _REGISTRY_CACHE_CONTROL,
        "Vary": "Accept-Encoding",
        "X-Registry-Version": payload["registry_version"],
    }
    cache_hit = _if_none_match_matches(request.headers.get("if-none-match"), entity_tag)
    collector = _builder_metrics()
    if collector is not None:
        collector.record_builder_registry_request(resource, cache_hit=cache_hit)
    if cache_hit:
        # 304 carries the validators, never a body (RFC 9110 15.4.5) - and therefore never
        # a `Content-Encoding` either.
        return Response(status_code=304, headers=headers)

    response = JSONResponse(content=payload, headers=headers)
    if not _accepts_gzip(request) or len(response.body) < _REGISTRY_GZIP_MIN_BYTES:
        return response
    return Response(
        content=gzip.compress(
            response.body, compresslevel=_REGISTRY_GZIP_LEVEL, mtime=0
        ),
        media_type="application/json",
        headers={**headers, "Content-Encoding": "gzip"},
    )


def _category_projection(category: str) -> dict:
    """One category's blocks, taken from the same assembled payload ``/blocks`` serves.

    Filtered from ``payload["blocks"]`` rather than re-read from the registry, so the
    block objects here are the identical objects - a projection cannot drift from its
    source (Requirement 4.1).
    """
    payload = _registry_payload()
    blocks = [block for block in payload["blocks"] if block["category"] == category]
    return {
        "registry_version": payload["registry_version"],
        "registry_schema_version": payload["registry_schema_version"],
        "category": category,
        "categories": [c for c in payload["categories"] if c["id"] == category],
        "port_types": payload["port_types"],
        "compatibility_matrix": payload["compatibility_matrix"],
        "blocks": blocks,
        "total": len(blocks),
    }


def _timeframe_seconds(label: str) -> Optional[int]:
    """Seconds in one bar of ``label``, or ``None`` when the label is not an interval."""
    text = str(label).strip().lower()
    if len(text) < 2:
        return None
    unit = _TIMEFRAME_UNIT_SECONDS.get(text[-1])
    if unit is None:
        return None
    try:
        count = int(text[:-1])
    except ValueError:
        return None
    return count * unit if count > 0 else None


# ══════════════════════════════════════════════════════════════════════════
# PERFORMANCE MONITORING ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@router.get("/performance/summary")
@limiter.limit("200/minute")
async def get_performance_summary(request: Request,
    user: dict = Depends(get_current_user)
):
    """
    Get performance monitoring summary.
    
    Returns query statistics, cache hit rates, and optimization recommendations.
    """
    try:
        summary = performance_monitor.get_summary()
        log_performance_summary()
        return summary
    except Exception as e:
        logger.error(f"Error getting performance summary: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "PERFORMANCE_SUMMARY_FAILED", "message": str(e)}
        )


@router.get("/performance/verify")
@limiter.limit("10/minute")
async def verify_performance(request: Request,
    user: dict = Depends(get_current_user)
):
    """
    Verify performance optimizations.
    
    Checks:
    - Database indexes
    - Cache configuration
    - Query optimization (N+1 detection, slow queries)
    """
    try:
        db_verification = verify_database_indexes()
        cache_verification = verify_cache_configuration()
        query_verification = verify_query_optimization()
        
        return {
            "database": db_verification,
            "cache": cache_verification,
            "queries": query_verification,
            "overall_status": "ok" if all([
                db_verification.get("status") == "verified",
                cache_verification.get("status") in ["ok", "degraded"],
                query_verification.get("status") == "ok"
            ]) else "issues_detected"
        }
    except Exception as e:
        logger.error(f"Error verifying performance: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "PERFORMANCE_VERIFICATION_FAILED", "message": str(e)}
        )


# ══════════════════════════════════════════════════════════════════════════
# CRASH RECOVERY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@router.post("/crash-recovery/recover-deployment")
@limiter.limit("10/minute")
async def recover_deployment(request: Request,
    body: Dict[str, Any],
    user: dict = Depends(get_current_user)
):
    """
    Recover a crashed deployment.
    
    Initiates recovery process for a deployment that has crashed.
    """
    try:
        deployment_id = body.get("deployment_id")
        if not deployment_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "MISSING_DEPLOYMENT_ID", "message": "deployment_id is required"}
            )
        
        recovery_result = await crash_recovery_manager.recover_deployment(
            deployment_id=deployment_id,
            user_id=user["id"],
            strategy_id=body.get("strategy_id", "")
        )
        
        return recovery_result
    except Exception as e:
        logger.error(f"Error recovering deployment for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "DEPLOYMENT_RECOVERY_FAILED", "message": str(e)}
        )


@router.post("/crash-recovery/recover-signals")
@limiter.limit("10/minute")
async def recover_signals(request: Request,
    body: Dict[str, Any],
    user: dict = Depends(get_current_user)
):
    """
    Recover signals that may have been lost during crash.
    """
    try:
        strategy_id = body.get("strategy_id")
        if not strategy_id:
            raise HTTPException(
                status_code=400,
                detail={"error": "MISSING_STRATEGY_ID", "message": "strategy_id is required"}
            )
        
        crash_time = datetime.fromisoformat(body.get("crash_time", datetime.now(timezone.utc).isoformat()))
        
        recovery_result = await crash_recovery_manager.recover_signals(
            strategy_id=strategy_id,
            user_id=user["id"],
            crash_time=crash_time
        )
        
        return recovery_result
    except Exception as e:
        logger.error(f"Error recovering signals for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SIGNAL_RECOVERY_FAILED", "message": str(e)}
        )


@router.get("/crash-recovery/verify-state")
@limiter.limit("10/minute")
async def verify_system_state(request: Request,
    user: dict = Depends(get_current_user)
):
    """
    Verify overall system state consistency.
    
    Checks deployment-worker, signal, and portfolio consistency.
    """
    try:
        consistency_result = await verify_state_consistency()
        return consistency_result
    except Exception as e:
        logger.error(f"Error verifying system state: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "STATE_VERIFICATION_FAILED", "message": str(e)}
        )


@router.get("/crash-recovery/log")
@limiter.limit("50/minute")
async def get_recovery_log(request: Request,
    user: dict = Depends(get_current_user)
):
    """
    Get crash recovery operation log.
    """
    try:
        recovery_log = crash_recovery_manager.get_recovery_log()
        active_recoveries = crash_recovery_manager.get_active_recoveries()
        
        return {
            "recovery_log": recovery_log,
            "active_recoveries": active_recoveries
        }
    except Exception as e:
        logger.error(f"Error getting recovery log: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "RECOVERY_LOG_FAILED", "message": str(e)}
        )


def _pipeline_timeframes() -> tuple:
    """``(timeframes, sources)`` for the intervals every pipeline stage can process.

    The intersection of the vocabularies in :data:`_TIMEFRAME_SOURCES`, ordered by bar
    duration. A source that cannot be imported is logged and skipped rather than silently
    narrowing the answer to nothing; if *no* source is readable, or the intersection is
    empty, this raises instead of serving an empty selector - an empty timeframe list is
    the same class of lie as an empty palette (Requirement 11.8).
    """
    import importlib

    vocabularies = []
    sources = []
    for module_name, attribute in _TIMEFRAME_SOURCES:
        try:
            module = importlib.import_module(module_name)
            vocabulary = {str(key) for key in getattr(module, attribute)}
        except Exception as e:
            logger.warning(
                "Timeframe vocabulary %s.%s unreadable: %s", module_name, attribute, e
            )
            continue
        if not vocabulary:
            logger.warning("Timeframe vocabulary %s.%s is empty", module_name, attribute)
            continue
        vocabularies.append(vocabulary)
        sources.append(f"{module_name}.{attribute}")

    if not vocabularies:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "TIMEFRAME_VOCABULARY_UNAVAILABLE",
                "message": (
                    "No data-pipeline timeframe vocabulary could be read from "
                    + ", ".join(f"{m}.{a}" for m, a in _TIMEFRAME_SOURCES)
                    + "; refusing to serve an empty timeframe set."
                ),
            },
        )

    supported = set(vocabularies[0])
    for vocabulary in vocabularies[1:]:
        supported &= vocabulary

    timeframes = []
    for label in supported:
        seconds = _timeframe_seconds(label)
        if seconds is None:
            logger.warning("Pipeline timeframe %r is not a bar interval; omitted", label)
            continue
        timeframes.append({"id": label, "label": label, "seconds": seconds})
    timeframes.sort(key=lambda entry: entry["seconds"])

    if not timeframes:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "TIMEFRAME_VOCABULARY_EMPTY",
                "message": (
                    "The data pipeline vocabularies "
                    + ", ".join(sources)
                    + " have no bar interval in common; refusing to serve an empty "
                    "timeframe set."
                ),
            },
        )
    return timeframes, sources


@router.get("/strategy-operations/registry/blocks")
@limiter.limit("200/minute")
async def get_registry_blocks(request: Request,
    user: dict = Depends(get_current_user)
):
    """
    The full block registry: all seven categories, in palette order.

    The single source of truth for what a block is (Requirement 4.1). The body is
    ``BlockRegistry.to_dict()`` verbatim - ``registry_version``,
    ``registry_schema_version``, ``port_types``, ordered ``categories``, ``blocks[]`` and
    the ``compatibility_matrix``. The matrix is shipped so a connect-time check in the
    client applies the identical rule the backend validator enforces (Requirement 6.9),
    and each block carries its real ``params`` so the inspector form is generated from the
    same specs the validator evaluates (Requirement 5.1).

    Supersedes the deprecated ``GET /api/strategies/blocks``, which published three of the
    seven categories and one generic ``window`` parameter for every indicator (SB-03,
    SB-04).

    Caching: ``ETag`` derived from ``registry_version``; an ``If-None-Match`` that matches
    gets ``304`` with no body (Requirements 4.15, 11.8 mechanics).
    """
    return _registry_response(request, _registry_payload(), "blocks")


@router.get("/strategy-operations/registry/indicators")
@limiter.limit("200/minute")
async def get_registry_indicators(request: Request,
    user: dict = Depends(get_current_user)
):
    """
    INDICATOR descriptors only - a convenience projection of ``/registry/blocks``.

    Every indicator the platform's indicator library actually implements, each with its
    own parameter ranges and warmup behaviour and one output port per distinct output
    (Requirements 4.4, 5.9, 5.10).
    """
    from backend_app.backend.strategy_dag.schema import BlockCategory

    payload = _category_projection(BlockCategory.INDICATOR.value)
    return _registry_response(request, payload, "indicators")


@router.get("/strategy-operations/registry/features")
@limiter.limit("200/minute")
async def get_registry_features(request: Request,
    user: dict = Depends(get_current_user)
):
    """
    FEATURE_ENGINEERING descriptors only - a convenience projection of ``/registry/blocks``.

    This category being empty *was* SB-03: the engine could compute features the palette
    never offered. Each descriptor carries its ``leakage_risk`` so a REVIEW_REQUIRED
    transform is visible where it is chosen (Requirements 4.3, 18.x).
    """
    from backend_app.backend.strategy_dag.schema import BlockCategory

    payload = _category_projection(BlockCategory.FEATURE_ENGINEERING.value)
    return _registry_response(request, payload, "features")


@router.get("/strategy-operations/registry/models")
@limiter.limit("200/minute")
async def get_registry_models(request: Request,
    user: dict = Depends(get_current_user)
):
    """
    ML_DL descriptors only - a convenience projection of ``/registry/blocks``.

    Already filtered by real availability: ``build_registry`` omits a model whose library
    is not importable in this environment rather than advertising a block that would fail
    at train time (Requirements 4.5, 4.6). Each descriptor's ``metadata.model`` carries
    the minimum-data and resource-cap figures the training admission gate applies, so the
    UI can state the requirement before the author submits a job (Requirement 14.1).
    """
    from backend_app.backend.strategy_dag.schema import BlockCategory

    payload = _category_projection(BlockCategory.ML_DL.value)
    return _registry_response(request, payload, "models")


@router.get("/strategy-operations/registry/timeframes")
@limiter.limit("200/minute")
async def get_registry_timeframes(request: Request,
    user: dict = Depends(get_current_user)
):
    """
    The bar intervals the platform's data pipeline supports (Requirement 11.8).

    The intersection of the pipeline's own vocabularies - see :data:`_TIMEFRAME_SOURCES` -
    ordered by bar duration, each entry carrying its ``seconds`` so the client can compute
    an expected interval without a second table. ``sources`` names where the answer came
    from, so a shrinking list is traceable to the stage that shrank it.

    The DATA block's ``timeframe`` parameter is required with no default (SB-06), and this
    is the set the author picks from.
    """
    timeframes, sources = _pipeline_timeframes()
    payload = _registry_payload()
    return _registry_response(
        request,
        {
            "registry_version": payload["registry_version"],
            "registry_schema_version": payload["registry_schema_version"],
            "timeframes": timeframes,
            "sources": sources,
            "total": len(timeframes),
        },
        "timeframes",
    )


# ══════════════════════════════════════════════════════════════════════════
# ASSET DISCOVERY (task 7.1) — the live universe, served from cache
# ══════════════════════════════════════════════════════════════════════════
# `design.md` -> DATA blocks and exchange-agnostic asset discovery, and its API table's
# `GET /api/strategy-operations/assets`. Requirements 11.1-11.6 and 25.5.
#
# WHAT AUTHENTICATES THIS ENDPOINT
# --------------------------------
# `Depends(get_current_user)` — the platform's own bearer-token dependency, the same one
# every other read on this router carries — plus the existing `slowapi` limit. An
# unauthenticated caller gets 401 from the dependency and never reaches the cache.
# `design.md` -> security surface: "Asset discovery | authenticated and rate-limited; cache
# is global and contains no user data".
#
# WHY THERE IS NO PER-TENANT SCOPING, RECORDED AS A DECISION
# ---------------------------------------------------------
# The asset universe is reference data: the markets the platform's own executor can trade,
# identical for every caller, derived from public exchange metadata and holding no user
# row, no exchange account, no key and no strategy. There is nothing here to scope, so
# there is no RLS-scoped client and no `user_id` filter on this path — deliberately, and
# not by omission. Authentication is still required, because block and market availability
# is not something to publish anonymously and because the limiter needs an identity to
# hold. Every tenant-scoped read on this router keeps its double filter untouched.
#
# WHAT THIS ENDPOINT DOES NOT DO
# ------------------------------
# It does not call `load_markets()`. It cannot: `discover_assets` reads a cache and, on a
# miss, *schedules* a refresh and answers 503. The exchange round-trip lives in
# `asset_universe.refresh_universe`, driven by the startup warm and the scheduled loop
# (Requirement 11.5). And it does not carry a fallback list — an empty cache with a failed
# refresh is `503 ASSET_UNIVERSE_UNAVAILABLE` (Requirement 11.6), which is the whole point
# of the endpoint existing.
# ══════════════════════════════════════════════════════════════════════════


@router.get("/strategy-operations/assets")
@limiter.limit("120/minute")
async def discover_assets_endpoint(request: Request,
    response: Response,
    search: Optional[str] = Query(
        None, max_length=64, description="Free-text match over symbol, base and quote."
    ),
    base: Optional[str] = Query(None, max_length=32, description="Base currency, exact."),
    quote: Optional[str] = Query(None, max_length=32, description="Quote currency, exact."),
    market_type: Optional[str] = Query(
        None, description="One of 'spot', 'swap', 'future'."
    ),
    active_only: bool = Query(
        True,
        description=(
            "Keep only markets the venue reports as active. Default true so a selector "
            "does not offer a delisted market; pass false to see the whole universe."
        ),
    ),
    limit: int = Query(50, ge=1, le=500, description="Page size."),
    cursor: Optional[str] = Query(
        None, description="Continuation cursor from a previous page's `next_cursor`."
    ),
    user: dict = Depends(get_current_user),
):
    """The tradeable market universe, filtered, paginated, served from cache.

    Each record is an ``AssetRef``: canonical ``symbol``, ``base``, ``quote``,
    ``market_type``, ``active``, ``price_precision``, ``amount_precision``,
    ``min_notional``, ``min_amount`` (Requirement 11.4), plus ``available_on`` and the
    ``precision_source`` naming which of those venues the precision and limit figures
    belong to. Those figures are the exchange's own, unrounded, and ``null`` where the
    venue stated none — a later order-size check needs to know the difference between "no
    minimum published" and "a minimum of zero".

    ``total`` counts everything matching the filters, not the page. ``next_cursor`` is a
    **keyset** cursor over the served order, not an offset: a refresh between pages cannot
    shift every later row into a duplicate. It is pinned to the filter set, so continuing
    it under different filters is a 422 rather than a silent walk through a different
    result set, and it records the universe it was cut against so a page served after a
    refresh reports ``universe_changed: true`` instead of pretending nothing moved.

    ``source_meta`` carries the provenance: when the universe was built, its age, the 6 h
    TTL, whether it is stale, the whole universe size, the contributing exchanges, the ones
    that failed, and whether a refresh is in flight.

    Status codes
        **200** a page, possibly empty (no market matched the filters).
        **422** the cursor is unreadable or belongs to a different query.
        **503** ``ASSET_UNIVERSE_UNAVAILABLE`` — the cache holds nothing and a refresh has
        not produced a universe. A refresh is scheduled and ``Retry-After`` is set.
        **No substitute list is returned, ever** (Requirement 11.6).
        **429** from the existing limiter.
    """
    from backend_app.backend.asset_universe import (
        AssetQuery,
        AssetUniverseUnavailable,
        InvalidAssetCursor,
        RETRY_AFTER_SECONDS,
        discover_assets,
    )

    query = AssetQuery(
        search=search,
        base=base,
        quote=quote,
        market_type=market_type,
        active_only=active_only,
        limit=limit,
        cursor=cursor,
    )

    try:
        page = await discover_assets(query)
    except InvalidAssetCursor as e:
        raise HTTPException(
            status_code=422,
            detail={
                "error": getattr(e, "code", "ASSET_CURSOR_INVALID"),
                "message": str(e),
            },
        )
    except AssetUniverseUnavailable as e:
        logger.warning("Asset discovery has no universe to serve: %s", e)
        raise HTTPException(
            status_code=503,
            detail={
                "error": getattr(e, "code", "ASSET_UNIVERSE_UNAVAILABLE"),
                "message": str(e),
                "last_refresh_error": e.last_error,
                "refresh_in_flight": e.refresh_in_flight,
                "retry_after_seconds": RETRY_AFTER_SECONDS,
            },
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )

    # The universe is global reference data with a server-side TTL, but it is served behind
    # authentication, so it must not land in a shared cache.
    response.headers["Cache-Control"] = _REGISTRY_CACHE_CONTROL
    response.headers["X-Asset-Universe-Hash"] = page["source_meta"]["universe_hash"]

    # Requirement 24.1's asset universe age, taken from the age `source_meta` already
    # reports rather than recomputed from `generated_at` - one arithmetic, one answer, and
    # the gauge cannot disagree with the response body. Task 9.2's 12 h alert reads this.
    collector = _builder_metrics()
    if collector is not None:
        collector.set_asset_universe_age(page["source_meta"]["age_seconds"])
    # Task 9.2's 12 h alert, from the same figure, at the same seam. 12 h is two of task
    # 7.1's 6 h TTLs, and `builder_alerts` derives it from `UNIVERSE_TTL_SECONDS` rather
    # than hardcoding 43200, so an operator who moves the TTL moves the alert with it.
    # Synchronous, total and non-blocking: the page above has already been built and is
    # returned whatever the alert does.
    alerts = _builder_alert_module()
    if alerts is not None:
        try:
            alerts.notice_asset_universe_age(page["source_meta"]["age_seconds"])
        except Exception:  # noqa: BLE001 - an alert must not turn a served page into a 500
            logger.debug("The asset-universe age alert was not raised.", exc_info=True)
    return page


# ══════════════════════════════════════════════════════════════════════════
# NODE PREVIEW (task 5.7) — the runtime, over a bounded window
# ══════════════════════════════════════════════════════════════════════════
# `design.md` -> Data preview and honesty: "Preview computation runs on a bounded
# historical window through the same executors, so previews cannot disagree with runtime"
# (Requirements 24.7, 24.8).
#
# THE DEFECT CLASS THIS SECTION IS SHAPED TO AVOID
# ------------------------------------------------
# A preview computed by a second evaluator is SB-01 in a new location: two code paths
# answering "what does this block produce?", free to drift, with the wrong one on screen at
# the moment the author decides whether to deploy. So nothing here evaluates a block. This
# endpoint is a thin transport over four objects that already exist and are already what
# runtime uses:
#
#   `load_graph`                       the one parser (v1 migrated at read time)
#   `get_compiler().compile_plan`      the one validator + compiler (Requirement 3.1)
#   `plan_to_engine_graph`             the one canonical -> engine adaptation (task 2.4)
#   `DAGEngine.execute_dag`            the one execution pipeline, the same call
#                                      `dag_event_loop`, `dag_risk_integration` and
#                                      `backtest_runtime` make
#
# There is no preview-specific indicator, feature, math or logic code below, and
# `tests/test_node_preview_endpoint.py` asserts that structurally rather than trusting this
# comment.
#
# What is bounded, and by whom
# ----------------------------
# The window is capped by :data:`PREVIEW_MAX_BARS` **server-side**. `bars` in the request is
# a hint that is clamped, never a count that is honoured: an unbounded preview is a backtest
# nobody accounted for (Requirement 25.x, and `design.md`'s own open question 3). The applied
# figure and the cap are both reported, so a clamped window is visible rather than silent.
#
# What never appears here
# -----------------------
# No exchange identifier, API key, secret or passphrase is accepted in the request or
# emitted in the response (SB-06, Requirement 12.1). The request model forbids unknown
# fields, so an `exchange` key is a 422 rather than something quietly ignored. A smuggled
# `exchange` *param* on a DATA node is reported by the real validator as `PARAM_UNKNOWN`,
# because the DATA descriptor publishes no such parameter (Requirement 12.2), and is not
# honoured by anything on this path: the venue comes from the server's own setting, so the
# param reaches no fetch and appears in no response field. Market data is resolved
# through the platform's own public feed - the same `ConnectionEngine` +
# `data_seeking_engine.DataEngine` path `api_ws` uses, keyed off the server's
# `DEFAULT_EXCHANGE` - so the venue is a deployment fact, never a request field and never a
# response field. What the response *does* carry is the market: symbol and timeframe, which
# are the DATA block's own parameters.
#
# What cannot happen
# ------------------
# A preview places no order and starts no training job. `plan_to_engine_graph` deliberately
# omits ACTION's `runtime_ref` (that reference is `CCXTExchangeExecutor.place_order`), so an
# ACTION node adapted for the engine contributes a signal series and nothing else, and
# `execute_dag` returns signals rather than intents. An ML node predicts from an already
# trained, already bound model or raises; there is no train-on-demand path through here.
# ══════════════════════════════════════════════════════════════════════════

#: The hard server-side ceiling on a preview window, in bars. A preview is a diagnostic
#: glance at the tail of a series, not a backtest: one bounded fetch and one bounded
#: execution of one node's upstream closure.
#:
#: Why 1200 rather than a rounder, smaller number: see
#: :data:`PREVIEW_WARMUP_HEADROOM`. The window has to be more than twice the previewed
#: node's composed warmup or the *runtime* refuses the series, and a 1200-bar ceiling is
#: what covers a composed warmup of ~575 bars - an SMA(480), an EMA(190), or a realistic
#: chain of two or three ordinary blocks. Past that the preview refuses and says why,
#: rather than growing without limit.
PREVIEW_MAX_BARS = 1200

#: The window used when the request names none and the previewed node's warmup is small.
PREVIEW_DEFAULT_BARS = 200

#: The floor. Below this the answer is warmup with nothing after it.
PREVIEW_MIN_BARS = 50

#: Multiple of the previewed node's composed warmup the window must cover.
#:
#: This is **derived, not chosen**. ``dag_engine``'s indicator path calls
#: ``core.pipeline_guard.guard_indicators``, which refuses a produced series that is more
#: than 50% NaN - a real runtime control, and the reason a preview cannot simply render a
#: mostly-warmup window as a column of nulls. Asking for ``2 * warmup`` plus a sample's
#: worth of bars keeps the warmup region a minority, so a legitimate slow block previews
#: instead of arriving as a generic execution failure. Weakening the guard for previews
#: would be worse than useless: it would make the preview disagree with runtime, which is
#: the one thing Requirement 24.7 forbids.
PREVIEW_WARMUP_HEADROOM = 2

#: Rows returned per output: "the last N values" (Requirement 24.7). The window is what is
#: *computed*; this is what is *transported*.
PREVIEW_SAMPLE_ROWS = 50

#: Columns of a FEATURE_MATRIX whose values are sampled. Every produced column *name* is
#: returned (Requirement 24.8 asks for the names); a wide matrix - `feat_lag` with ten lags,
#: fanned into `feat_concat` - would otherwise turn a preview into a payload. Which columns
#: were sampled is stated, so the truncation is visible rather than silent.
PREVIEW_SAMPLE_COLUMNS = 12


class NodePreviewRequest(BaseModel):
    """The preview request. Deliberately small, and closed to unknown fields.

    ``extra="forbid"``: an ``exchange``, ``api_key`` or ``exchange_account_id`` field here
    would be a 422 rather than a silently ignored key that a client author believes is
    doing something (SB-06). A preview names a market through the graph's DATA block and
    nothing else.
    """

    model_config = ConfigDict(extra="forbid")

    blueprint: Optional[dict] = Field(
        None,
        description=(
            "The canonical StrategyGraph to preview - the canvas as it stands, which is "
            "usually ahead of the saved version. Omitted: the strategy's current version "
            "is previewed instead."
        ),
    )
    bars: Optional[int] = Field(
        None,
        description=(
            f"Requested window in bars. A hint: it is clamped server-side to at most "
            f"{PREVIEW_MAX_BARS}."
        ),
    )


def _preview_window(requested: Optional[int], warmup_bars: int) -> Dict[str, Any]:
    """The window that will actually be computed, and an account of how it was reached.

    Preconditions
        ``warmup_bars`` is the **previewed node's** composed warmup - not the plan's, which
        is the figure for the ACTION path and would make an EMA(20) preview fetch a window
        sized for the SMA(300) elsewhere in the graph.

    Postconditions
        ``bars`` is in ``[PREVIEW_MIN_BARS, PREVIEW_MAX_BARS]`` for every input, including
        a hostile one. It grows past the request only to reach ``needed_bars``, and never
        past the cap. ``needed_bars`` is what the window would have to be for the runtime
        to accept the series at all (see :data:`PREVIEW_WARMUP_HEADROOM`); when it exceeds
        the cap the caller refuses rather than fetching a window that will be rejected
        downstream with a generic message. ``clamped`` says whether the request was reduced
        and ``max_bars`` states the ceiling either way, so a client cannot mistake a capped
        answer for the one it asked for.
    """
    asked = None
    if requested is not None:
        try:
            asked = int(requested)
        except (TypeError, ValueError):
            asked = None

    warmup = max(0, int(warmup_bars))
    # The window this node needs before the runtime will accept its own output: warmup as a
    # minority of the series, plus one sample's worth of bars to actually look at.
    needed = PREVIEW_WARMUP_HEADROOM * warmup + PREVIEW_SAMPLE_ROWS

    bars = PREVIEW_DEFAULT_BARS if asked is None or asked <= 0 else asked
    bars = max(bars, min(PREVIEW_MAX_BARS, needed))
    bars = max(PREVIEW_MIN_BARS, min(PREVIEW_MAX_BARS, bars))
    return {
        "requested": asked,
        "bars": bars,
        "max_bars": PREVIEW_MAX_BARS,
        "min_bars": PREVIEW_MIN_BARS,
        "sample_rows": PREVIEW_SAMPLE_ROWS,
        "node_warmup_bars": warmup,
        "needed_bars": needed,
        "clamped": asked is not None and asked != bars,
    }


def _preview_market(plan: Any) -> Dict[str, str]:
    """The one market this plan reads, from its own DATA nodes.

    ``DAGEngine.execute_dag`` evaluates one graph against one OHLCV frame, exactly as the
    live loop does per symbol. A graph whose DATA nodes name two different markets
    therefore has no single frame to preview against, and this refuses rather than picking
    one: a preview computed against the wrong feed is the ``ohlcv_feed.volume`` defect with
    a different disguise.

    Raises
        :class:`HTTPException` 422 when the plan declares no market, or more than one.
    """
    # One scan, shared with the training path (task 6.3): `scan_plan_markets` is what
    # `strategy_service.resolve_training_data_source` reads too, so a preview and a
    # training job cannot disagree about which market a graph declares. The error
    # contracts stay separate, because a refused preview and a blocked training run are
    # different answers to different questions.
    from backend_app.backend.strategy_service import scan_plan_markets

    symbols, timeframes, _data_node = scan_plan_markets(plan)

    if not symbols or not timeframes:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "PREVIEW_MARKET_UNRESOLVED",
                "message": (
                    "This graph's Market Data block declares no symbol and timeframe, so "
                    "there is no bounded window to compute the preview over."
                ),
                "hint": "Set the symbol and timeframe on the Market Data block.",
            },
        )
    if len(symbols) > 1 or len(timeframes) > 1:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "PREVIEW_MULTIPLE_MARKETS",
                "message": (
                    "This graph reads more than one market, and a preview is computed "
                    "against one bar series. Previewing it would mean choosing a feed on "
                    "the author's behalf."
                ),
                "symbols": symbols,
                "timeframes": timeframes,
            },
        )
    return {"symbol": symbols[0], "timeframe": timeframes[0]}


async def _fetch_preview_bars(symbol: str, timeframe: str, bars: int) -> List[List[Any]]:
    """``bars`` most recent OHLCV rows for ``symbol`` at ``timeframe``.

    The platform's existing public market-data path, unchanged: ``ConnectionEngine`` opens
    an unauthenticated connection to the feed named by the server's ``DEFAULT_EXCHANGE``
    and ``data_seeking_engine.DataEngine.fetch_historical_ohlcv`` paginates it. That is the
    same resolution ``api_ws/ws_routes._get_public_exchange`` performs for public feeds; a
    preview neither reads a user's vault nor accepts a venue from the request (SB-06).

    Seam note: this is the one I/O boundary of the preview path, and it is a module-level
    function so a test can supply a deterministic frame without replacing any part of the
    validator, the compiler, the adapter or the engine - the four things a preview test is
    actually about.
    """
    import os

    from backend_app.backend.connection_engine import ConnectionEngine
    from backend_app.backend.data_seeking_engine import DataEngine

    venue = os.getenv("DEFAULT_EXCHANGE")
    if not venue:
        # Named without naming a venue: the operator needs to know which setting is missing,
        # the caller must not learn which venue would have been used.
        logger.error("Node preview unavailable: DEFAULT_EXCHANGE is not configured")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "PREVIEW_FEED_UNCONFIGURED",
                "message": (
                    "The platform market data feed is not configured, so no historical "
                    "window can be read."
                ),
            },
        )

    engine = DataEngine(await ConnectionEngine(exchange_id=venue).connect())
    return await engine.fetch_historical_ohlcv(symbol, timeframe, limit=int(bars))


#: The market data mode a preview reads under (task 7.5, Requirement 13.7).
#:
#: A preview is a bounded historical read with no order router downstream of it, so it is
#: `backtest`, not `paper` and not `live`. Naming it rather than leaving it implicit is the
#: point: `market_data_contract.resolve_gap_policy` pins what a gap may be filled with per
#: mode and refuses an unrecognised one, so this constant is what decides whether a
#: `FORWARD_FILL` configuration is permitted here at all - and it is checked before any
#: candle is fetched.
PREVIEW_DATA_MODE = "backtest"


def _preview_gap_policy() -> "Any":
    """The gap strategy this path may use, resolved before a single candle is read.

    Requirement 13.7 and task 7.5. The strategy is the platform validator's own configured
    ``ValidationConfig.gap_strategy`` - this path chooses nothing and overrides nothing -
    and :func:`market_data_contract.resolve_gap_policy` decides whether ``backtest`` mode
    may use it. A deployment configured to synthesise or interpolate candles is refused
    here, with the reason on the wire, rather than serving an author a preview built partly
    from prices no exchange quoted.

    503 rather than 422: the request is fine, the *server's* data configuration is not, and
    the author cannot fix it by editing the graph.
    """
    from backend_app.backend.market_data_contract import (
        MarketDataContractError,
        resolve_gap_policy,
    )
    from backend_app.backend.market_data_validation import get_validator

    try:
        return resolve_gap_policy(
            PREVIEW_DATA_MODE, get_validator().config.gap_strategy
        )
    except MarketDataContractError as exc:
        logger.error("Preview refused the configured gap strategy: %s", exc)
        raise HTTPException(status_code=503, detail=exc.to_dict()) from exc


def _preview_frame(rows: Any, bars: int, timeframe: str) -> "Any":
    """CCXT OHLCV rows as the CLOSED-BAR frame the engine executes against.

    ``(frame, counters)``. The five columns the executors read, indexed by bar timestamp,
    tail-limited to ``bars`` - and every bar in it is a **closed** bar.

    Why the closed-bar gate is here and not left out (task 7.5, Requirement 19.2)
        A CCXT ``fetch_ohlcv`` window ends with the bar that is still forming. Previewing
        against it means the same bar timestamp produces one value now and a different one
        thirty seconds later, which is two contradictory answers to "what does this block
        produce?" - the exact defect this endpoint's design forbids. The frame is therefore
        built by ``market_data_contract.closed_bar_frame``, which drops a forming bar, keeps
        the **first** candle for a duplicated timestamp and counts what it dropped.

    ``drop_late`` is left at its default of ``False``: this is a historical read, so an
    out-of-order row is counted and sorted into place rather than discarded
    (``design.md``: "re-sort historical").

    This function still computes nothing about a block. It arranges bars; the counters it
    returns are counts of dropped candles, not values.
    """
    from backend_app.backend.market_data_contract import (
        MarketDataContractError,
        closed_bar_frame,
    )

    try:
        frame, counters = closed_bar_frame(rows, timeframe)
    except MarketDataContractError as exc:
        # An interval the pipeline cannot measure. The graph named it, so this is a 422.
        raise HTTPException(status_code=422, detail=exc.to_dict()) from exc

    if frame.empty:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "PREVIEW_DATA_UNAVAILABLE",
                "message": (
                    "The market data feed returned no closed bars for this symbol and "
                    "timeframe, so there is nothing to compute a preview from."
                ),
                # Which is it: nothing arrived, or everything that arrived was still
                # forming? An author looking at a 503 deserves to know.
                "counters": counters.to_dict(),
            },
        )
    return frame.tail(int(bars)), counters


async def _preview_data_quality(frame: Any, symbol: str, timeframe: str) -> "Any":
    """The platform validator's ``DataQualityReport`` for the window just built.

    Requirement 19.1 - every candle batch is validated "before the DAG_Runtime receives
    that batch". ``market_data_contract.quality_report`` calls the existing
    ``MarketDataValidator``; nothing here grades, scores or re-counts anything.

    A window the validator rejects outright becomes a classified 422 carrying the
    validator's own message - the same posture ``strategy_service`` takes for a training
    window. Its thresholds are strict (a close beyond three standard deviations, or a gap
    wider than 1.5 bars, refuses the window) and are deliberately not loosened for a
    preview: a preview computed under looser data rules than the runtime uses would
    disagree with the runtime, which is the one thing this endpoint may not do.
    """
    from backend_app.backend.market_data_contract import (
        MarketDataContractError,
        quality_report,
    )

    try:
        return await quality_report(frame, symbol, timeframe)
    except MarketDataContractError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                **exc.to_dict(),
                "hint": (
                    "The platform refuses to compute on a window it cannot vouch for. Try "
                    "a different symbol, timeframe or window length."
                ),
            },
        ) from exc


def _json_number(value: Any) -> Optional[float]:
    """A float that survives strict JSON, or ``None``.

    NaN and ±Inf are not JSON numbers. ``None`` is the honest wire form for "this bar has no
    value", and it is what the warmup region genuinely is. The count of them is reported
    alongside, so an author looking at an empty bar is told how many there are rather than
    left to count nulls (Requirement 20.4's spirit, and the reason a preview exists at all).
    """
    import math

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(number) else number


def _timestamp_labels(index: Any) -> List[str]:
    """An index as ISO-8601 strings, whatever it is made of."""
    import pandas as pd

    labels: List[str] = []
    for stamp in list(index):
        try:
            labels.append(pd.Timestamp(stamp).isoformat())
        except (TypeError, ValueError):
            labels.append(str(stamp))
    return labels


def _series_sample(value: Any, rows: int) -> Dict[str, Any]:
    """The last ``rows`` values of a produced series."""
    tail = value.tail(int(rows))
    values = [_json_number(item) for item in tail.to_list()]
    return {
        "kind": "series",
        "length": int(len(value)),
        "index": _timestamp_labels(tail.index),
        "values": values,
        "empty_values": sum(1 for item in values if item is None),
    }


def _matrix_sample(matrix: Any, rows: int) -> Dict[str, Any]:
    """A ``FeatureMatrix`` as the produced column names plus a sample of values.

    Requirement 24.8, read straight off the contract in
    ``strategy_dag/feature_matrix.py``: every produced column name, its per-column warmup,
    its provenance node, and the tail of the matrix. Nothing is recomputed and nothing is
    renamed - the names in the response are the names the block published, which is the
    point of showing them (a `lag_1` / `lag_2` / `lag_3` triple is how an author checks
    which lag is which).
    """
    columns = list(matrix.columns)
    sampled = columns[:PREVIEW_SAMPLE_COLUMNS]
    take = min(int(rows), int(matrix.n_rows))
    start = int(matrix.n_rows) - take
    values = [
        [_json_number(matrix.values[row, columns.index(name)]) for name in sampled]
        for row in range(start, int(matrix.n_rows))
    ]
    return {
        "kind": "feature_matrix",
        "length": int(matrix.n_rows),
        "columns": columns,
        "column_count": len(columns),
        "sampled_columns": sampled,
        "sample_truncated": len(sampled) < len(columns),
        "column_warmup": {name: int(offset) for name, offset in matrix.column_warmup.items()},
        "provenance": dict(matrix.provenance),
        "warmup_offset": int(matrix.warmup_offset),
        "index": _timestamp_labels(matrix.index[start:]),
        "values": values,
        "empty_values": sum(1 for row in values for item in row if item is None),
    }


def _output_sample(value: Any, rows: int) -> Dict[str, Any]:
    """One produced port value, in the shape the inspector renders.

    Dispatch is on what the port actually produced, not on the node's category: a port
    declares its ``PortType`` and the engine validated the value against it before it
    entered the value map (task 5.2), so this only has to transport what is there.
    """
    import numpy as np
    import pandas as pd

    from backend_app.backend.strategy_dag.feature_matrix import FeatureMatrix

    if isinstance(value, FeatureMatrix):
        return _matrix_sample(value, rows)
    if isinstance(value, pd.Series):
        return _series_sample(value, rows)
    if isinstance(value, pd.DataFrame):
        return _matrix_sample(
            FeatureMatrix(
                index=np.arange(len(value.index)),
                columns=[str(name) for name in value.columns],
                values=value.to_numpy(dtype=float),
            ),
            rows,
        )
    if isinstance(value, np.ndarray) and value.ndim == 1:
        return _series_sample(pd.Series(value), rows)
    number = _json_number(value)
    if number is not None or isinstance(value, (int, float)):
        return {"kind": "scalar", "length": 1, "value": number}
    # Not a shape this endpoint knows how to transport. Said plainly rather than rendered
    # as an empty series, which would read as "this block produced nothing".
    return {
        "kind": "unrenderable",
        "python_type": type(value).__name__,
        "message": "This port produced a value the preview cannot sample.",
    }


def _preview_closure(plan: Any, node_id: str) -> List[str]:
    """``node_id`` and every node upstream of it, in ``plan.execution_order``.

    Why the preview executes a closure rather than the whole graph
    -------------------------------------------------------------
    A node's value in a DAG is a function of its ancestors and nothing else, so the values
    computed here are **identical** to the values the same node takes in a full run - that is
    the property the "same executors" requirement is about, and restricting the node set
    does not touch it. What it does remove is the collateral: previewing an EMA in a graph
    that also holds an untrained ML node would otherwise fail on the model binding, and
    previewing anything at all would evaluate every ACTION node in the graph. Neither is
    part of the question the author asked.

    Order comes from ``plan.execution_order``, so the subgraph is executed in the compiler's
    deterministic topological order, exactly as the worker executes the full one.
    """
    needed = {node_id}
    stack = [node_id]
    while stack:
        current = stack.pop()
        for source in plan.predecessors(current):
            if source not in needed:
                needed.add(source)
                stack.append(source)
    return [nid for nid in plan.execution_order if nid in needed]


def _node_outputs_payload(
    engine: Any, descriptor: Any, node_id: str, rows: int
) -> List[Dict[str, Any]]:
    """Every declared output port of ``node_id``, with what it produced.

    Read from ``engine.node_outputs[(node_id, port)]`` - the port-addressed map (task 5.2) -
    so a `macd` preview shows `macd`, `signal` and `histogram` as three different series
    rather than whichever one happened to be the node's primary. A port that produced
    nothing is reported as such, never omitted: a missing port and an empty port are
    different facts.
    """
    declared = [
        {"name": port.name, "port_type": getattr(port.type, "value", str(port.type))}
        for port in (getattr(descriptor, "outputs", ()) or ())
    ]
    payload: List[Dict[str, Any]] = []
    for port in declared:
        key = (node_id, port["name"])
        if key in engine.node_outputs:
            value = engine.node_outputs[key]
        elif len(declared) == 1 and node_id in engine.node_results:
            # A legacy-shaped executor that returned a bare value rather than publishing
            # ports. Its single declared port IS that value; see `_publish_node_outputs`.
            value = engine.node_results[node_id]
        else:
            payload.append({**port, "produced": False})
            continue
        payload.append({**port, "produced": True, **_output_sample(value, rows)})
    return payload


# ══════════════════════════════════════════════════════════════════════════
#  THE EXECUTION TRACE FOR ONE NODE
#  (strategy-builder task 9.3 — Requirement 24.6, design.md § Observability)
#
#  "`dag_engine.ExecutionTracer` already records per-node inputs, outputs,
#  duration and failures, and `signal_trace_engine.py` records signal
#  provenance. Both are reused" — so nothing below records anything. There is no
#  third trace store: these functions are projections over the two that exist,
#  and `DAGEngine.get_node_issues`'s own docstring already names this task as
#  where its records get rendered.
#
#  WHY THE TRACE RIDES THE PREVIEW RESPONSE
#  ----------------------------------------
#  An `ExecutionTracer` is populated by a run and lives as long as the engine
#  that ran. The one place the Builder surface runs a DAG is `preview_node`
#  above, which already constructs its engine with `enable_tracing=True` and
#  then discarded everything the tracer recorded. Publishing it from the same
#  response is what makes the trace *the trace of the run the author just asked
#  for*: a separate endpoint would have to re-execute to have anything to
#  report, and two executions is two answers to one question — SB-01's shape.
#  It also costs no new authorisation path, no new rate limit and no new
#  ownership resolution, all three of which would be new places to get an
#  isolation rule wrong.
# ══════════════════════════════════════════════════════════════════════════

#: Signal-trace records read per node. `signal_trace_engine` retains an hour, and the
#: inspector answers "why did nothing happen *just now*"; an unbounded read would put a
#: retention window on the wire.
SIGNAL_TRACE_RECORD_LIMIT = 5

#: Node executions republished per signal-trace record. One live bar produces one, so this
#: only bounds a pathological record.
SIGNAL_TRACE_EXECUTIONS_PER_RECORD = 20


def _enum_value(value: Any) -> Any:
    """An enum member as its value, anything else unchanged. Total by construction."""
    return getattr(value, "value", value)


def _trace_entry_payload(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """One recorded entry, reshaped for rendering. Nothing is added and nothing computed.

    `ExecutionTracer` records inputs as two parallel maps - `input_shape` and `input_types`,
    both keyed by the **upstream node id** the value came from, because that is how
    `PortInputs` addresses a value and what every executor reads - plus `input_ports`, the
    port view (`input port -> upstream node ids`). A renderer wants one row per *port*,
    because "which of this block's inputs was that" is the question an author has and a
    ULID is not an answer to it. So this zips the three: one row per binding, carrying both
    the port and the source it came from.

    A source that fed no declared port still gets a row, with `port` null. Dropping it would
    lose an input the run genuinely bound, and a legacy plain-dict caller records no port
    view at all - that reads as "port unknown", never as "no inputs".

    The shape dict keeps its own `type` key ("Series", "scalar") *inside* `shape`, and the
    declared type sits beside it as `type`: the two are different facts (`Series[float64]`
    versus `{"type": "Series", "length": 300}`) and flattening them together would let one
    overwrite the other.
    """
    shapes = entry.get("input_shape") or {}
    types = entry.get("input_types") or {}
    port_view = entry.get("input_ports") or {}

    def row(port: Optional[str], source: Any) -> Dict[str, Any]:
        return {
            "port": None if port is None else str(port),
            "source": str(source),
            "type": types.get(source),
            "shape": shapes.get(source),
        }

    rows: List[Dict[str, Any]] = []
    bound: Set[str] = set()
    for port in sorted(port_view, key=str):
        for source in port_view.get(port) or ():
            bound.add(str(source))
            rows.append(row(port, source))
    for source in sorted(set(shapes) | set(types), key=str):
        if str(source) not in bound:
            rows.append(row(None, source))

    return {
        "node_id": entry.get("node_id"),
        "node_type": entry.get("node_type"),
        "status": entry.get("status"),
        "duration_ms": entry.get("execution_time_ms"),
        "error_message": entry.get("error_message"),
        "recorded_at": entry.get("timestamp"),
        # Requirement 24.6's "inputs": one row per binding the run bound, as recorded.
        "inputs": rows,
        # ... and its "outputs". One entry, because the tracer records the node's primary
        # value; the per-port sample lives in `outputs` on the response, which is read from
        # the port-addressed map instead.
        "output": {
            "type": entry.get("output_type"),
            "shape": entry.get("output_shape"),
        },
    }


def _condition_display(issue: Mapping[str, Any]) -> str:
    """One recorded numeric condition as a sentence, authored here and rendered verbatim.

    `NodeIssue.to_dict` carries `code`, `bar`, `bars_affected` and `detail` and no sentence,
    and it is written to by kernels that must not compose English. The client renders
    whatever text arrives (the `NodePreview` / `ParameterForm` rule), so the sentence is
    composed on this side - once - rather than reassembled from four fields in the browser,
    which is where it would drift from the vocabulary the engine records.
    """
    code = str(issue.get("code") or "UNSPECIFIED")
    bars = issue.get("bars_affected")
    bar = issue.get("bar")
    parts = [code]
    try:
        affected = int(bars)
    except (TypeError, ValueError):
        affected = 0
    if affected > 0:
        parts.append(f"on {affected} bar{'s' if affected != 1 else ''}")
    if bar is not None:
        parts.append(f"from bar {bar}")
    sentence = " ".join(parts)
    detail = str(issue.get("detail") or "").strip()
    return f"{sentence}: {detail}" if detail else sentence


def _trace_summary(
    node_id: str,
    entries: Sequence[Mapping[str, Any]],
    blocking: Optional[Mapping[str, Any]],
    conditions: Sequence[Mapping[str, Any]],
) -> str:
    """"Why did nothing happen?", answered in one sentence.

    Composed here and rendered verbatim, the convention `ParameterForm` (`fix_hint`),
    `NodePreview` (`detail.message`) and task 7.11's feed strip (`display`) already follow.
    The reason it belongs on this side is that every fact in it - which node the runtime
    stopped at, which condition it recorded, whether this node was reached at all - is a
    fact about a run only the server observed.
    """
    if not entries:
        if blocking is not None:
            return (
                f"This block did not execute. The run stopped at "
                f"'{blocking.get('node_id')}': {blocking.get('error_message') or 'no reason recorded'}"
            )
        return (
            "This block did not execute, and the run recorded no failure. Nothing "
            "upstream of it produced a value to work from."
        )

    last = entries[-1]
    status = str(last.get("status") or "")
    if status == "fail":
        return (
            f"This block failed: {last.get('error_message') or 'no reason recorded'}"
        )
    if status != "success":
        return (
            "This block started and recorded no outcome, so the run ended before it "
            "finished."
        )

    duration = last.get("duration_ms")
    ran = (
        f"This block executed in {duration} ms"
        if duration is not None
        else "This block executed"
    )
    if conditions:
        first = conditions[0]
        return (
            f"{ran} and recorded {len(conditions)} numeric "
            f"condition{'s' if len(conditions) != 1 else ''}, the first of which is "
            f"{first.get('display')}. That is why bars are empty rather than wrong."
        )
    return f"{ran} and recorded no failure."


def _node_trace_payload(
    engine: Any,
    node_id: str,
    executed_nodes: Sequence[str],
    signal_provenance: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Requirement 24.6: the recorded trace for one node - inputs, outputs, duration,
    failures.

    Preconditions
        ``engine`` has been through one ``execute_dag`` (or has failed inside one) with
        tracing enabled. ``executed_nodes`` is the node set that run was given, in the
        compiler's order - :func:`_preview_closure`'s return - which is what lets an
        upstream failure be reported as upstream.

    Postconditions
        Every field is read from :class:`~backend_app.backend.dag_engine.ExecutionTracer`
        or :class:`~backend_app.backend.dag_engine.NodeIssueLog`. ``status`` is
        ``not_executed`` when the tracer holds no entry for the node, which is a different
        fact from a node that ran and produced nothing, and the two must not render alike.
        ``blocking_failure`` is the **earliest** recorded failure at or upstream of this
        node: the first thing that went wrong is the one an author can act on, whereas
        ``get_last_failure`` reports the newest.
    """
    order = [str(nid) for nid in executed_nodes]
    position = {nid: index for index, nid in enumerate(order)}

    entries = [_trace_entry_payload(entry) for entry in engine.get_node_execution_trace(node_id)]
    failures = [_trace_entry_payload(entry) for entry in engine.get_execution_failures()]
    conditions = [
        {**dict(issue), "display": _condition_display(issue)}
        for issue in engine.get_node_issues().get(node_id, [])
    ]

    # The earliest failure the previewed node depends on. Everything in `executed_nodes` is
    # this node or an ancestor of it (that is what a closure is), so a failure anywhere in
    # that set is a failure this node depended on - no second graph walk needed, and none
    # that could disagree with the compiler's order.
    selected_position = position.get(str(node_id), len(order))
    blocking = None
    for failure in failures:
        failed_at = position.get(str(failure.get("node_id")))
        if failed_at is None or failed_at > selected_position:
            continue
        if blocking is None or failed_at < position.get(str(blocking.get("node_id")), len(order)):
            blocking = failure

    statuses = [str(entry.get("status") or "") for entry in entries]
    if not entries:
        status = "not_executed"
    elif "fail" in statuses:
        status = "fail"
    elif all(item == "success" for item in statuses):
        status = "success"
    else:
        status = statuses[-1] or "pending"

    durations = [
        entry["duration_ms"] for entry in entries if entry.get("duration_ms") is not None
    ]

    return {
        "node_id": str(node_id),
        "status": status,
        "recorded": bool(entries),
        # Summed rather than "the last one": a node started twice in a run spent both.
        "duration_ms": round(sum(durations), 3) if durations else None,
        "executions": entries,
        # Requirement 24.6's "recorded failures" - the whole run's, so an upstream one is
        # visible from the node that has nothing to show because of it.
        "failures": failures,
        "blocking_failure": blocking,
        # Requirement 20.4's records, each with the sentence that names the bar.
        "conditions": conditions,
        # What was executed, in the compiler's order. The trace is only ever a statement
        # about this set.
        "executed_nodes": order,
        "summary": _trace_summary(str(node_id), entries, blocking, conditions),
        "signal_provenance": dict(signal_provenance)
        if signal_provenance is not None
        else _signal_provenance_unavailable(
            "Signal provenance was not read for this preview."
        ),
    }


def _signal_provenance_unavailable(message: str) -> Dict[str, Any]:
    """The honest empty answer. Never an absent key, and never a 500."""
    return {"available": False, "records": [], "traces_seen": 0, "message": message}


def _signal_trace_execution(trace: Any) -> Dict[str, Any]:
    """One ``DAGNodeTrace``, as an allow-list.

    An allow-list rather than a filtered dump, because the record this reads from
    (``SignalTraceRecord``) carries an ``exchange``, a ``bot_id`` and a free-form
    ``metadata`` bag, and Requirement 12.1 / SB-06 forbid the first reaching a Builder
    payload at all. Naming the fields that may travel is the only version of this that
    stays correct when a field is added to the record upstream.
    """
    return {
        "node_type": _enum_value(getattr(trace, "node_type", None)),
        "label": getattr(trace, "node_label", None),
        "duration_ms": getattr(trace, "execution_ms", None),
        "status": _enum_value(getattr(trace, "status", None)),
        "error_message": getattr(trace, "error_message", None),
        "cache_hit": bool(getattr(trace, "cache_hit", False)),
        "inputs": [
            {"key": io.key, "value": str(io.value), "dtype": io.dtype}
            for io in (getattr(trace, "inputs", ()) or ())
        ],
        "outputs": [
            {"key": io.key, "value": str(io.value), "dtype": io.dtype}
            for io in (getattr(trace, "outputs", ()) or ())
        ],
    }


async def _signal_provenance(strategy_id: str, node_id: str) -> Dict[str, Any]:
    """What ``signal_trace_engine`` recorded for this node on live signals.

    Reused, not rebuilt: ``signal_trace_engine.trace_engine`` is the process-local store
    ``design.md`` names for signal provenance, and this is a read of it through its own
    ``get_recent_traces``. It is keyed by ``strategy_id`` and holds no owner column, which
    is exactly why it is only ever read **after** ``StrategyService.get_strategy`` has
    resolved this ``strategy_id`` against ``user["id"]``: the ownership decision stays
    where it already is rather than being re-derived from a trace.

    Total. A store that has not been started, an unimportable module, a record shaped
    differently from the dataclass - all of them report "not available" with a sentence.
    A preview must not 500 because a diagnostic could not be read.
    """
    try:
        from backend_app.backend.signal_trace_engine import trace_engine

        records = await trace_engine.get_recent_traces(
            strategy_id=strategy_id, limit=SIGNAL_TRACE_RECORD_LIMIT
        )
    except Exception as e:  # pragma: no cover - defensive; the store is in-process
        logger.info("Signal provenance unavailable for strategy %s: %s", strategy_id, e)
        return _signal_provenance_unavailable(
            "The signal trace store could not be read, so no live provenance is shown."
        )

    wanted = str(node_id)
    payload: List[Dict[str, Any]] = []
    for record in records or ():
        try:
            executions = [
                _signal_trace_execution(trace)
                for trace in (getattr(record, "node_traces", ()) or ())
                if str(getattr(trace, "node_id", "")) == wanted
            ][:SIGNAL_TRACE_EXECUTIONS_PER_RECORD]
            if not executions:
                continue
            created = getattr(record, "created_at", None)
            payload.append(
                {
                    "trace_id": getattr(record, "trace_id", None),
                    "recorded_at": created.isoformat() if created is not None else None,
                    "status": _enum_value(getattr(record, "status", None)),
                    "final_decision": getattr(record, "final_decision", None),
                    # The market, from the record. No venue: `record.exchange` exists and
                    # is deliberately not read here (SB-06).
                    "symbol": getattr(record, "symbol", None),
                    "latency_ms": getattr(record, "total_latency_ms", None),
                    "executions": executions,
                }
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.info("A signal trace record could not be projected: %s", e)
            continue

    return {
        "available": True,
        "traces_seen": len(records or ()),
        "records": payload,
        "message": (
            f"{len(payload)} live signal trace(s) recorded for this block."
            if payload
            else (
                "No live signal has been traced for this block. Signal provenance is "
                "recorded while a deployment runs; a preview is not a deployment."
            )
        ),
    }


@router.post("/strategy-operations/strategies/{strategy_id}/nodes/{node_id}/preview")
@limiter.limit("30/minute")
async def preview_node(request: Request,
    strategy_id: str,
    node_id: str,
    body: NodePreviewRequest,
    user: dict = Depends(get_current_user)
):
    """The last N values a node produces, computed by the executors that trade it.

    Requirement 24.7: the preview runs over a **bounded historical window** through the
    **same executors** the DAG runtime uses. Requirement 24.8: a FEATURE_ENGINEERING node's
    preview carries the produced column names and a sample of the produced values.

    The path, in order, with nothing of its own in between
    -----------------------------------------------------
    1. ``strategy_id`` is resolved through ``StrategyService.get_strategy``, whose
       ``.eq("user_id", user["id"])`` filter runs on top of the request-scoped client's RLS.
       Another tenant's strategy is reported as **404**, identically to a missing one, so
       existence does not leak (Requirement 21.x).
    2. The graph is ``body.blueprint`` when supplied - the canvas as it stands, which is
       what an author is actually looking at - else the strategy's current version. Either
       way it is parsed by ``schema.load_graph``, so a version 1 payload is migrated at read
       time rather than interpreted.
    3. ``get_compiler().compile_plan`` validates and compiles it: the same entry point the
       save path, the clone path, the worker and the backtester use (Requirement 3.1). An
       invalid graph gets the complete structured report and **no preview** - a preview of a
       graph that cannot run would be a number with nothing behind it.
    4. ``plan_to_engine_graph`` adapts the plan, and ``DAGEngine.execute_dag`` executes it -
       over the previewed node's upstream closure, in ``plan.execution_order``. That is the
       same call ``dag_event_loop``, ``dag_risk_integration`` and ``backtest_runtime`` make,
       so this response cannot disagree with what trades; see :func:`_preview_closure` for
       why restricting the node set cannot change a value.
    5. The requested node's ports are read out of the port-addressed value map, and the
       numeric conditions this run recorded against that node are read out of
       ``get_node_issues`` (Requirement 20.4) - which is what turns "this bar is empty" from
       a mystery into ``DIVISION_BY_ZERO at bar 34``.

    Bounded, and honestly so
    ------------------------
    ``bars`` is clamped to :data:`PREVIEW_MAX_BARS`; the response states the figure applied,
    the ceiling, and whether the request was reduced. When the plan's warmup exceeds the
    window the response says so, because that - not a broken block - is why the values are
    null.

    Nothing about an exchange is accepted or returned. The response's ``market`` is the DATA
    block's own symbol and timeframe (SB-06, Requirement 12.1).

    Nothing is persisted, no order is placed and no training job is created.
    """
    import asyncio

    from backend_app.backend.strategy_compiler import (
        get_compiler,
        plan_to_engine_graph,
    )
    from backend_app.backend.strategy_dag.plan import compute_warmup
    from backend_app.backend.strategy_dag.registry import get_registry
    from backend_app.backend.strategy_dag.schema import (
        GraphParseError,
        UnsupportedSchemaVersion,
        load_graph,
    )

    # ── 1. Ownership, before anything is read or computed ──────────────────
    try:
        service = await get_strategy_service()
        owned = await service.get_strategy(user=user, strategy_id=strategy_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Preview could not resolve strategy %s: %s", strategy_id, e)
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_GET_FAILED", "message": str(e)},
        )
    if not owned:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "STRATEGY_NOT_FOUND",
                "message": f"Strategy {strategy_id} not found",
            },
        )

    # ── 2. The graph: the canvas when given one, else the saved version ────
    source = "request"
    payload = body.blueprint
    if payload is None:
        source = "current_version"
        version = owned.get("version") or {}
        payload = version.get("graph_json") or version.get("blueprint")
        if not payload:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "PREVIEW_GRAPH_UNAVAILABLE",
                    "message": (
                        "This strategy's current version carries no readable graph, and no "
                        "graph was supplied with the request."
                    ),
                },
            )

    try:
        graph = load_graph(payload)
    except (GraphParseError, UnsupportedSchemaVersion) as e:
        logger.info("Preview payload is not a readable strategy graph: %s", e)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "STRATEGY_GRAPH_UNREADABLE",
                "message": str(e),
                "hint": "Send a canonical schema_version 2 graph with 'nodes' and 'edges'.",
            },
        )

    if not any(node.id == node_id for node in graph.nodes):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "PREVIEW_NODE_NOT_FOUND",
                "message": f"This graph holds no node '{node_id}'.",
            },
        )

    # ── 3. The one validator and the one compiler ─────────────────────────
    registry = get_registry()
    try:
        plan = get_compiler().compile_plan(graph, registry)
    except ValidationError as e:
        report = getattr(e, "report", None)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "STRATEGY_VALIDATION_FAILED",
                "message": str(e),
                "report": report.to_dict() if report is not None else None,
                "errors": list(getattr(e, "errors", []) or []),
                "warnings": list(getattr(e, "warnings", []) or []),
                "codes": list(e.codes()) if hasattr(e, "codes") else [],
                "hint": (
                    "A preview is computed by the runtime, so a graph the runtime would "
                    "refuse has no preview."
                ),
            },
        )
    except CompilerError as e:
        logger.error("Preview compilation failed: %s", e)
        raise HTTPException(
            status_code=400,
            detail={"error": "STRATEGY_COMPILATION_FAILED", "message": str(e)},
        )

    descriptor = registry.get(plan.node(node_id).block_id)
    if descriptor is None:  # pragma: no cover - compile_plan already refused this
        raise HTTPException(
            status_code=422,
            detail={
                "error": "PREVIEW_BLOCK_UNPUBLISHED",
                "message": (
                    f"The registry publishes no descriptor for "
                    f"'{plan.node(node_id).block_id}', so its output ports are unknown."
                ),
            },
        )

    # ── 4. The bounded window, and the market it is read for ──────────────
    market = _preview_market(plan)
    # The gap policy, before a single candle is fetched (Requirement 13.7). A server
    # configured to fabricate candles is refused while the refusal is still free.
    gap_policy = _preview_gap_policy()
    # The previewed node's own composed warmup, from the compiler's one warmup rule
    # (`plan.compute_warmup`'s `node_ids` form). `plan.warmup_bars` is the ACTION path's
    # figure and would oversize the window for a fast node in a slow graph.
    node_warmup = compute_warmup(graph, registry, node_ids=[node_id])
    window = _preview_window(body.bars, node_warmup)
    if window["needed_bars"] > PREVIEW_MAX_BARS:
        # Said here, before a fetch, rather than left to arrive as `guard_indicators`
        # refusing a 90%-NaN series with a message about NaN percentages. The block is not
        # broken and neither is the graph: this node needs more history than a preview is
        # allowed to read, and both figures are on the wire so the author can act.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "PREVIEW_WARMUP_EXCEEDS_WINDOW",
                "message": (
                    f"This node needs {node_warmup} warmup bars before its first "
                    f"trustworthy value, which needs a window of {window['needed_bars']} "
                    f"bars. A preview reads at most {PREVIEW_MAX_BARS}."
                ),
                "node_id": node_id,
                "node_warmup_bars": node_warmup,
                "needed_bars": window["needed_bars"],
                "max_bars": PREVIEW_MAX_BARS,
                "hint": (
                    "Reduce the lookback on this block or on one feeding it, or run a "
                    "backtest, which reads the full history this needs."
                ),
            },
        )
    try:
        rows = await _fetch_preview_bars(
            market["symbol"], market["timeframe"], window["bars"]
        )
    except HTTPException:
        raise
    except Exception as e:
        # The venue is never named to the caller, and never logged as part of a message the
        # caller sees (SB-06).
        logger.warning(
            "Preview market data fetch failed for %s %s: %s",
            market["symbol"],
            market["timeframe"],
            e,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "PREVIEW_DATA_UNAVAILABLE",
                "message": (
                    "The platform market data feed could not supply a window for "
                    f"{market['symbol']} at {market['timeframe']}."
                ),
            },
        )
    # Closed bars only: a forming bar never reaches an executor, so one timestamp cannot
    # produce two contradictory values (Requirement 19.2). First-wins on duplicates and the
    # dropped-candle counts come from the same gate (19.3, 19.4).
    frame, ingest_counters = _preview_frame(rows, window["bars"], market["timeframe"])
    # The existing validator, on the frame that will actually be executed (19.1, 19.5).
    quality = await _preview_data_quality(frame, market["symbol"], market["timeframe"])

    # ── 5. The same execution pipeline that trades ─────────────────────────
    from backend_app.backend.dag_engine import DAGEngine

    engine_nodes, engine_edges = plan_to_engine_graph(plan, registry)
    closure = set(_preview_closure(plan, node_id))
    preview_nodes = [node for node in engine_nodes if node["id"] in closure]
    preview_edges = [
        edge
        for edge in engine_edges
        if edge["source"] in closure and edge["target"] in closure
    ]
    engine = DAGEngine(enable_tracing=True, enable_event_buffer=False)
    executed_ids = [node["id"] for node in preview_nodes]
    try:
        await asyncio.to_thread(engine.execute_dag, preview_nodes, preview_edges, frame)
    except Exception as e:
        # A node that cannot execute is reported as such, with whatever the run managed to
        # record against it. An empty 200 would read as "this block produces nothing".
        logger.info("Preview execution failed on strategy %s: %s", strategy_id, e)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "PREVIEW_EXECUTION_FAILED",
                "message": str(e),
                "failure": type(e).__name__,
                "node_id": node_id,
                "issues": engine.get_node_issues().get(node_id, []),
                # Task 9.3 / Requirement 24.6. This is the branch the trace matters most
                # in: the run that produced nothing is exactly the run whose recorded
                # inputs, duration and failure answer "why did nothing happen?". The
                # tracer holds them whether or not `execute_dag` returned, so a refusal
                # carries the same trace a success would.
                "trace": _node_trace_payload(engine, node_id, executed_ids),
            },
        )

    outputs = _node_outputs_payload(engine, descriptor, node_id, window["sample_rows"])
    index_labels = _timestamp_labels(frame.index)

    return {
        "strategy_id": strategy_id,
        "node_id": node_id,
        "block_id": plan.node(node_id).block_id,
        "category": plan.node(node_id).category.value,
        "graph_source": source,
        "dag_hash": plan.dag_hash,
        "compiler_version": plan.compiler_version,
        "market": market,
        "window": {
            **window,
            "available_bars": int(len(frame.index)),
            # This node's own composed warmup - what makes *its* leading bars null. The
            # plan's figure is reported separately rather than conflated with it.
            "warmup_bars": int(node_warmup),
            "plan_warmup_bars": int(plan.warmup_bars),
            # The reason a preview can be all nulls without anything being broken.
            "warmup_exceeds_window": int(node_warmup) >= int(len(frame.index)),
            "first_timestamp": index_labels[0] if index_labels else None,
            "last_timestamp": index_labels[-1] if index_labels else None,
        },
        # What was actually executed: the previewed node and its ancestors, in the
        # compiler's order. Stated so a client is not left guessing which part of the graph
        # the numbers came from.
        "executed_nodes": [node["id"] for node in preview_nodes],
        # Task 7.5: how this window was produced, and what the platform's own validator
        # said about it. `counters` are the arrival-seam drops (forming bars, duplicates
        # resolved first-wins, out-of-order arrivals); `quality` is
        # `DataQualityReport.to_dict()` republished verbatim - the two are never summed,
        # because they count different things at different layers. `gap_policy.disclosure`
        # is how a permitted fill reaches the caller instead of being logged and forgotten.
        "market_data": {
            "counters": ingest_counters.to_dict(),
            "gap_policy": gap_policy.to_dict(),
            "quality": quality.to_dict(),
            "closed_bars_only": True,
        },
        "outputs": outputs,
        # Requirement 20.4: the numeric conditions this run recorded against this node.
        "issues": engine.get_node_issues().get(node_id, []),
        # Task 9.3, Requirement 24.6: the trace this run *already recorded* - the node's
        # bound inputs, its output shape, its duration and every failure - read out of the
        # `ExecutionTracer` the engine above was constructed with and which was previously
        # discarded. Plus whatever `signal_trace_engine` holds for this node from live
        # signals, which is a different question about the same block and is answered
        # honestly (including "nothing was recorded") rather than omitted.
        "trace": _node_trace_payload(
            engine,
            node_id,
            executed_ids,
            await _signal_provenance(strategy_id, node_id),
        ),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════
#  THE SAVE PATH AND THE TRAINING JOB ENDPOINTS
#  (strategy-builder task 6.3 — design.md § API surface, § Training workflow)
#
#  Three endpoints, all additive, all on the existing conventions this router
#  already uses: `Depends(get_current_user)`, a `slowapi` limit, `extra="forbid"`
#  request models, and 422 for a validation report / 409 for a state conflict /
#  404 for anything that is not this tenant's.
#
#  WHAT THE ROUTER DOES AND DOES NOT DO
#  ------------------------------------
#  It maps. Every decision - the graph verdict, the data quality grade, the
#  feature schema check, the minimum-data gate, the caps, the idempotency - is
#  made in `strategy_service` and, through it, in the components that already
#  owned those rules. This file translates the outcomes into status codes. That
#  is the same division `preview_node` above follows and the reason SB-01 cannot
#  come back through a router.
#
#  ENTITLEMENTS (Requirement 16.7)
#  -------------------------------
#  `require_ml_training` and `check_ml_quota` stay in force on the explicit
#  training endpoint, unchanged, as declared dependencies. The caps task 6.2
#  added are ADDITIVE to them. The save endpoint cannot carry them as
#  dependencies - a dependency cannot be conditional, and a FREE user saving an
#  indicator-only strategy must not be refused - so it applies the same two
#  functions inside the training branch only
#  (`StrategyService._assert_ml_training_entitled`). Neither control is removed
#  or loosened anywhere.
# ══════════════════════════════════════════════════════════════════════════


class VersionSaveRequest(BaseModel):
    """The save payload: a canonical graph plus an optional training configuration.

    ``extra="forbid"``: an ``exchange``, ``exchange_id``, ``api_key`` or
    ``exchange_account_id`` field here is a 422 rather than a silently ignored key a
    client author believes is doing something (SB-06). The market a version trades
    comes from its DATA block, and the venue is chosen at deploy time.
    """

    model_config = ConfigDict(extra="forbid")

    blueprint: dict = Field(
        ..., description="Canonical StrategyGraph (schema_version 2)"
    )
    version: Optional[str] = Field(
        None, description="Version label. Defaults to the next major."
    )
    make_current: bool = Field(True, description="Point the strategy at this version.")
    is_draft: bool = Field(True, description="Mark the version a draft.")
    training: Optional["TrainingConfigRequest"] = Field(
        None,
        description=(
            "Training configuration for the graph's model nodes. Ignored when the "
            "graph declares none."
        ),
    )


class TrainingConfigRequest(BaseModel):
    """What an author may choose about a training run.

    Every field is optional and every omitted one resolves to the model spec's own
    declared figure, which is then RECORDED in ``training_jobs.config`` - so a run is
    reproducible from the row rather than from whatever a constant happens to be
    later (Requirement 15.14).

    ``extra="forbid"`` again, and for the same reason: there is deliberately no
    ``exchange``, ``exchange_id``, ``data_source`` or credential field here. The data
    source is RESOLVED from the graph's DATA block (Requirement 12.6), and a client
    that tries to name a venue gets a 422 rather than having it quietly dropped.

    ``epochs`` is the one field a client genuinely chooses, and it is checked against
    ``min(model.max_safe_epochs, tier_cap)`` server-side. It is never clamped
    silently: an over-cap request is refused, naming the requested and permitted
    values (Requirement 16.3).
    """

    model_config = ConfigDict(extra="forbid")

    epochs: Optional[int] = Field(None, ge=1, description="Training epochs / rounds.")
    batch_size: Optional[int] = Field(None, ge=1, description="Batch size.")
    bars: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Requested history window in bars. A floor: the server raises it to what "
            "the model's minimum row count needs, and caps it."
        ),
    )
    label_horizon: Optional[int] = Field(
        None, ge=1, description="Bars ahead the label looks."
    )
    label_mode: Optional[str] = Field(
        None, description="'classification' or 'regression'."
    )
    label_threshold: Optional[float] = Field(
        None, description="Classification band around zero."
    )
    val_fraction: Optional[float] = Field(None, ge=0.0, lt=1.0)
    test_fraction: Optional[float] = Field(None, ge=0.0, lt=1.0)
    embargo_bars: Optional[int] = Field(
        None,
        ge=0,
        description=(
            "Embargo between splits. A floor only: the server raises it to the "
            "model's own minimum and to the feature lookback plus the label horizon, "
            "and never lowers it."
        ),
    )
    seed: Optional[int] = Field(None, description="Deterministic seed.")


class TrainingJobCreateRequest(BaseModel):
    """Queue training explicitly for a version that already exists."""

    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(..., description="The immutable version to train against.")
    node_id: Optional[str] = Field(
        None,
        description=(
            "One model node. Omitted: every model node in the graph is queued."
        ),
    )
    training: Optional[TrainingConfigRequest] = None


VersionSaveRequest.model_rebuild()


def _training_blocked_response(blocked: Any) -> HTTPException:
    """A :class:`TrainingBlocked` as the 422 the requirement asks for.

    The body carries the reason code and the whole structured detail - the data
    quality report, the failing feature issues, or the gate's ``required`` and
    ``available`` for BOTH dimensions - because Requirement 14.9 renders both
    quantities and a UI that has to ask a second time for the other half renders
    "insufficient data" instead.
    """
    payload = blocked.to_dict()
    return HTTPException(
        status_code=422,
        detail={
            "error": "TRAINING_BLOCKED",
            "reason": payload["reason"],
            "message": payload["message"],
            "detail": payload["detail"],
            "job_created": False,
        },
    )


def _validation_failed_response(exc: Any) -> HTTPException:
    """A ``ValidationError`` as the 422 carrying the complete report."""
    report = getattr(exc, "report", None)
    return HTTPException(
        status_code=422,
        detail={
            "error": "STRATEGY_VALIDATION_FAILED",
            "message": str(exc),
            "report": report.to_dict() if report is not None else None,
            "errors": list(getattr(exc, "errors", []) or []),
            "warnings": list(getattr(exc, "warnings", []) or []),
            "codes": list(exc.codes()) if hasattr(exc, "codes") else [],
        },
    )


@router.post("/strategy-operations/strategies/{strategy_id}/versions")
@limiter.limit("30/minute")
async def save_strategy_version(request: Request,
    strategy_id: str,
    body: VersionSaveRequest,
    user: dict = Depends(get_current_user)
):
    """Validate, compile, persist an immutable version, and maybe queue training.

    ``design.md`` -> API surface calls this "the single save path". It is one call to
    ``StrategyService.create_version_and_maybe_train``, which runs the workflow in the
    design's order; this function only maps the outcome.

    The response's ``training`` field, and what each value means
    -----------------------------------------------------------
    ============== ==============================================================
    ``NOT_REQUIRED`` The graph declares no model node. The version is ``READY``
                     and deployable (Requirement 14.10). Not "skipped", and no
                     empty job was created.
    ``QUEUED``       One ``training_jobs`` row per model node exists in ``QUEUED``,
                     ``job_id`` is on the wire, and the version is ``TRAINING``
                     (Requirements 15.1, 15.12). **Nothing was trained during this
                     request** - the work happens in the worker.
    ``BLOCKED``      An admission gate refused. The version was still saved, and
                     **no ``training_jobs`` row was created** (Requirements 14.7,
                     14.8, 14.3, 14.4). ``reason`` and ``detail`` carry the quality
                     report, the failing feature issues, the gate's required-versus-
                     available figures, or the exceeded cap's requested-versus-
                     permitted values.
    ``UNAVAILABLE``  Training was admitted but ``training_jobs`` does not exist,
                     because ``004d_training_and_models.sql`` has not been applied.
                     A warning naming that file was logged, and **no job id was
                     invented**.
    ============== ==============================================================

    Why a blocked training half is a 200 and not a 4xx
    -------------------------------------------------
    The save succeeded: the graph compiled, and an immutable version now exists with
    its hash, plan and report. Refusing the whole request because a history range was
    short would throw that away and force the author to re-submit an identical graph.
    The requirement is that training is *blocked* and *reported*, which is what the
    ``training`` field does. The explicit ``POST /training/jobs`` endpoint, whose only
    purpose is to create a job, answers 422 for the same block.

    Status codes
        **200** saved (see ``training`` for the training half).
        **422** the graph is invalid - the full ``ValidationReport`` is in ``detail``,
        and nothing was persisted.
        **404** ``strategy_id`` is not this user's. Identical to a missing one, so
        existence does not leak across tenants.
        **429** from the existing limiter.
    """
    from backend_app.backend.strategy_compiler import CompilerError, ValidationError
    from backend_app.backend.strategy_dag.schema import (
        GraphParseError,
        UnsupportedSchemaVersion,
    )

    try:
        service = await get_strategy_service()
        result = await service.create_version_and_maybe_train(
            user,
            strategy_id,
            body.blueprint,
            training_cfg=(
                None if body.training is None else body.training.model_dump(exclude_none=True)
            ),
            version=body.version,
            make_current=body.make_current,
            is_draft=body.is_draft,
        )
    except ValidationError as e:
        logger.info("Version save refused: %s", e.codes() if hasattr(e, "codes") else e)
        raise _validation_failed_response(e)
    except (GraphParseError, UnsupportedSchemaVersion) as e:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "STRATEGY_GRAPH_UNREADABLE",
                "message": str(e),
                "hint": "Send a canonical schema_version 2 graph with 'nodes' and 'edges'.",
            },
        )
    except CompilerError as e:
        logger.error("Version save could not be compiled: %s", e)
        raise HTTPException(
            status_code=400,
            detail={"error": "STRATEGY_COMPILATION_FAILED", "message": str(e)},
        )
    except ValueError as e:
        # `create_version` reports a strategy that is not this user's as not found.
        raise HTTPException(
            status_code=404,
            detail={"error": "STRATEGY_NOT_FOUND", "message": str(e)},
        )

    version_row = result.get("version") or {}
    return {
        "status": "saved",
        "strategy_id": strategy_id,
        "version_id": version_row.get("id"),
        "version": version_row.get("version"),
        "dag_hash": result.get("dag_hash"),
        "validation_state": result.get("validation_state"),
        "lifecycle_state": result.get("lifecycle_state"),
        "warmup_bars": result.get("warmup_bars"),
        "registry_version": result.get("registry_version"),
        "canonical_persisted": result.get("canonical_persisted"),
        "warnings": result.get("warnings", []),
        "report": (
            result["report"].to_dict() if result.get("report") is not None else None
        ),
        # Requirement 15.12: the save response STATES what happened to training and
        # carries the job identifier. There is no implicit background training.
        "training": result.get("training"),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/strategy-operations/training/jobs")
@limiter.limit("20/minute")
async def create_training_job(request: Request,
    body: TrainingJobCreateRequest,
    user: dict = Depends(get_current_user),
    _feature=Depends(require_ml_training),
    _quota=Depends(check_ml_quota),
):
    """Queue training for an existing version. Idempotent per (version, node).

    Requirement 15.13 and "idempotent per (version, node)" are held by
    ``uq_tj_active_per_node``, a partial unique index over
    ``(version_id, node_id) WHERE status IN ('QUEUED','RUNNING')`` - not by a
    read-then-write check, which would let two concurrent requests both read "no live
    job" and both insert. A second request has its INSERT rejected by PostgreSQL, and
    the service translates that rejection into the existing job.

    So a repeat answers **200** with that job and ``idempotent_nodes`` naming the
    nodes that already had one, rather than 409. ``design.md``'s status-code table
    lists 409 for a duplicate training job; the idempotent response is chosen here
    because the task specifies idempotency and because a retry after a dropped
    connection - or a double-click - must not read as an error when the outcome the
    client asked for (one live job for this node) holds either way. The invariant is
    identical under both answers; only the reporting differs.

    ``require_ml_training`` and ``check_ml_quota`` remain declared dependencies,
    unchanged. The caps from task 6.2 are applied on top of them, inside the service.

    Status codes
        **200** queued, or already live.
        **422** an admission gate refused - ``detail`` carries the reason and, for the
        minimum-data gate, ``required`` and ``available`` for both dimensions. **No
        job row was created.**
        **404** the version is not this user's, or does not exist.
        **429** from the existing limiter.
    """
    from backend_app.backend.strategy_compiler import CompilerError, ValidationError
    from backend_app.backend.strategy_dag.schema import (
        GraphParseError,
        UnsupportedSchemaVersion,
    )
    from backend_app.backend.strategy_service import TrainingBlocked

    try:
        service = await get_strategy_service()
        outcome = await service.create_training_job(
            user,
            body.version_id,
            node_id=body.node_id,
            training_cfg=(
                None if body.training is None else body.training.model_dump(exclude_none=True)
            ),
        )
    except TrainingBlocked as blocked:
        logger.info(
            "Training job refused for version %s (%s); no job row was created.",
            body.version_id,
            blocked.reason,
        )
        raise _training_blocked_response(blocked)
    except ValidationError as e:
        raise _validation_failed_response(e)
    except (GraphParseError, UnsupportedSchemaVersion) as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "STRATEGY_GRAPH_UNREADABLE", "message": str(e)},
        )
    except CompilerError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "STRATEGY_COMPILATION_FAILED", "message": str(e)},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={"error": "TRAINING_TARGET_NOT_FOUND", "message": str(e)},
        )

    return {"status": outcome.get("state"), **outcome}


# ══════════════════════════════════════════════════════════════════════════
# TRUTHFUL TRAINING STATUS (task 6.6)
#
# Two reads, on the two paths ``design.md``'s API table names. Both answer with
# ``training_status.public_training_job``, which is the only projection of a
# ``training_jobs`` row anything returns to a client, and which carries Requirement
# 15.3's whole list plus the nullable ``eta_seconds``.
#
# Three things these endpoints deliberately do NOT do:
#
#   * they never write. A reporting surface that repaired a row it thought was
#     wrong would be a reporting surface racing the worker that owns it - and 004d
#     attaches no ``updated_at`` trigger to this table, so every write here would
#     be one more place to forget that column.
#   * they never compute progress from a clock. ``progress`` is derived from
#     completed epochs and ``eta_seconds`` is absent until the requirement's own
#     two conditions hold (Requirements 15.4, 15.5, 15.6).
#   * they never return an artifact reference. A completed job's bound model is
#     reported as ``model_versioning.public_model_version``, which has no
#     ``artifact_uri`` by construction; the bytes stay behind task 6.5's signed,
#     ownership-checked exchange.
#
# Ownership is the same double filter every other Strategy Builder read uses - the
# caller's RLS-scoped client plus an explicit ``user_id`` - and another tenant's job
# is a 404, so existence does not leak.
# ══════════════════════════════════════════════════════════════════════════


@router.get("/strategy-operations/training/jobs")
@limiter.limit("200/minute")
async def list_training_jobs(request: Request,
    version_id: str = Query(..., min_length=1, description="The strategy version to report on."),
    user: dict = Depends(get_current_user)
):
    """Every training job this user owns for one version.

    Declared BEFORE the ``{job_id}`` route below so ``version_id`` cannot be read as a
    path segment, and mounted on the same path as ``POST /training/jobs`` with a
    different method.

    ``version_id`` is required rather than optional. A caller asking "what is happening
    to this version" is the question the builder asks; an unfiltered list of every job a
    user has ever run is a broader read than anything in this spec needs, so it is not
    offered.

    An empty ``jobs`` list is the answer for a version with no jobs, for another
    tenant's version and for an unapplied ``004d_training_and_models.sql`` - the same
    answer for all three, because telling them apart would confirm whether a version
    identifier the caller does not own exists.

    Neither this endpoint nor ``GET /training/jobs/{job_id}`` wraps its body in a
    synthetic ``{"status": "ok"}``, unlike some of this router's older responses:
    ``status`` on a training job means one of ``QUEUED | RUNNING | COMPLETED | FAILED |
    CANCELLED`` (Requirement 15.2), and one key with two meanings on sibling endpoints is
    worse than no envelope at all.

    Status codes
        **200** the jobs, possibly none.
        **429** from the existing limiter.
    """
    from backend_app.backend import training_status as TS

    return await TS.list_training_job_reports(user, version_id)


@router.get("/strategy-operations/training/jobs/{job_id}")
@limiter.limit("200/minute")
async def get_training_job(request: Request,
    job_id: str,
    user: dict = Depends(get_current_user)
):
    """The truthful status of one training job.

    Reports ``status``, ``dataset_rows``, ``usable_rows``, ``feature_columns``,
    ``feature_names``, ``split_sizes``, ``model``, ``epochs_total``, ``epoch_current``,
    ``loss``, ``val_loss``, ``progress``, ``cancellable`` and ``failure_reason``
    (Requirement 15.3), plus ``eta_seconds`` - which is **null** until at least three
    epochs have completed and their measured durations have a coefficient of variation
    below 0.35 (Requirements 15.5, 15.6). ``eta_state`` says which of those conditions
    is holding the estimate back, so the builder can render "estimating…" and say why
    rather than showing a number nobody should act on.

    ``progress`` is derived from completed epochs here, not echoed from the stored
    column: the stored value is a cache of the same arithmetic, and where the two
    disagree the derived figure is reported and the disagreement is logged.

    Status codes
        **200** the job's status.
        **404** no such job belongs to this user - the same answer for a job that does
        not exist, one that belongs to another tenant, and an unapplied
        ``004d_training_and_models.sql``.
        **429** from the existing limiter.
    """
    from backend_app.backend import training_status as TS

    try:
        return await TS.training_job_report(user, job_id)
    except TS.TrainingJobNotFound as e:
        raise HTTPException(
            status_code=404,
            detail={"error": "TRAINING_JOB_NOT_FOUND", "message": str(e)},
        )


@router.post("/strategy-operations/training/jobs/{job_id}/cancel")
@limiter.limit("60/minute")
async def cancel_training_job(request: Request,
    job_id: str,
    user: dict = Depends(get_current_user)
):
    """Ask a running or queued training job to stop. Sets ``cancel_requested``.

    Cooperative, per Requirement 15.7 and ``design.md``'s edge-case table: this sets
    the flag and the **worker** stops at the next epoch boundary, sets ``CANCELLED``
    and binds no model version. Setting the status here would mark a job cancelled
    while a process was still writing epochs against it.

    Ownership is enforced by an explicit ``user_id`` filter on top of the RLS-scoped
    client's ``tj_owner_update`` policy, and another user's job is reported as **404**
    - the same answer as a job that does not exist, so existence does not leak.

    Deliberately NOT gated behind ``check_ml_quota``: a quota check refuses an action
    that CONSUMES capacity, and cancelling releases it. A user whose monthly ML
    allowance had run out would otherwise be unable to stop a run they had started,
    which is a control that harms rather than protects. Authentication and ownership
    are the controls that belong here, and both are enforced.

    Status codes
        **200** ``cancel_requested`` is set (idempotent: a second request reports
        ``already_requested``).
        **409** the job has already finished, failed or been cancelled - naming the
        current state.
        **404** no such job belongs to this user.
        **429** from the existing limiter.
    """
    try:
        service = await get_strategy_service()
        return await service.request_job_cancellation(user, job_id)
    except DeployPrerequisiteError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "TRAINING_JOB_NOT_CANCELLABLE",
                "message": str(e),
                "missing_prerequisite": e.missing_prerequisite,
            },
        )
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={"error": "TRAINING_JOB_NOT_FOUND", "message": str(e)},
        )


# ══════════════════════════════════════════════════════════════════════════
# MODEL ARTIFACTS (task 6.5)
#
# Requirement 17.8 asks for two things in one sentence - "serve model artifacts through
# an ownership-checked endpoint" and "sanitize artifact file names before use" - and
# ``design.md``'s security surface adds a third: "``artifact_uri`` is server-side only and
# never returned raw to the client; download goes through a signed, ownership-checked
# endpoint". That is a two-step exchange, and both steps are below.
#
#   1. ``GET /models/{model_version_id}/download`` MINTS a link. Authenticated with the
#      platform's ``get_current_user``, ownership enforced by ``mv_owner_select`` on the
#      caller's own RLS-scoped client PLUS an explicit ``user_id`` filter. Returns the
#      model version's client projection - which has no ``artifact_uri`` - and a
#      five-minute signed token.
#
#   2. ``GET /models/artifact?token=...`` REDEEMS it and streams the bytes. This request
#      carries no JWT, which is the whole reason the token exists: an artifact download is
#      a browser navigation or a file save, and such a request cannot carry a Bearer
#      header. WHAT AUTHENTICATES IT is the HMAC-SHA256 signature over
#      ``(purpose, model_version_id, user_id, exp)``, verified in constant time against
#      the platform's existing ``SUPABASE_JWT_SECRET``/``JWT_SECRET``; the row is then
#      re-read with an explicit ``user_id`` filter and compared against the token's owner,
#      so ownership is checked at redemption as well as at minting rather than carried
#      across on trust. No secret configured means no link is issued at all (fail-closed),
#      and the artifact's checksum is verified before a byte is served.
#
# Both endpoints keep the existing rate limits. Neither returns ``artifact_uri``.
# ══════════════════════════════════════════════════════════════════════════


@router.get("/strategy-operations/models/artifact")
@limiter.limit("60/minute")
async def download_model_artifact(request: Request, token: str = Query(..., min_length=16)):
    """Stream one model artifact against a signed, ownership-bound token.

    Declared BEFORE the ``{model_version_id}`` route below so that ``artifact`` is read as
    a literal segment and never as a model version id.

    Authentication: the token, and only the token - see the section comment above for why
    and for what that token proves. Every failure is the same **404** with no detail beyond
    "not found": a bad signature, an expired token, a token for another purpose, another
    tenant's model version, an unapplied ``004d_training_and_models.sql`` and an artifact
    whose checksum no longer matches its row are indistinguishable to the caller, because
    telling them apart is telling a token holder something about a resource they do not own.

    The file name in ``Content-Disposition`` is built from the row's own identifiers and
    sanitised with ``ml_models._safe_filename`` - the ML-5 path-traversal fix - so it can
    carry no path, quote or newline whatever is in the database.

    Status codes
        **200** the artifact, as ``application/octet-stream``.
        **404** anything else at all.
        **429** from the existing limiter.
    """
    from backend_app.backend import model_versioning as MV

    not_found = HTTPException(
        status_code=404,
        detail={
            "error": "MODEL_ARTIFACT_NOT_FOUND",
            "message": "No model artifact is available for this link.",
        },
    )

    try:
        payload = MV.verify_artifact_token(token)
    except (MV.InvalidArtifactToken, MV.SignedLinkUnavailable) as e:
        logger.info("A model artifact download was refused: %s", e)
        raise not_found

    try:
        row = await MV.load_model_version_for_token(payload)
        body = MV.read_artifact_bytes(row)
    except MV.ModelVersionNotFound as e:
        logger.info("A model artifact download found nothing: %s", e)
        raise not_found
    except MV.ArtifactChecksumMismatch as e:
        # Requirement 17.6's condition on the serving path. Loud in the log, opaque to the
        # caller.
        logger.error("A model artifact was NOT served because its checksum moved: %s", e)
        raise not_found
    except MV.ArtifactStoreError as e:
        logger.error("A model artifact could not be read from the store: %s", e)
        raise not_found

    filename = MV.artifact_filename(row)
    return Response(
        content=body,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(body)),
            "X-Artifact-Checksum": str(row.get("artifact_checksum") or ""),
            # A signed link is a credential for one object; it must not be cached by a
            # proxy on the way back.
            "Cache-Control": "no-store",
        },
    )


@router.get("/strategy-operations/models/{model_version_id}/download")
@limiter.limit("60/minute")
async def create_model_artifact_link(request: Request,
    model_version_id: str,
    user: dict = Depends(get_current_user)
):
    """Mint a short-lived signed link to one of this user's model artifacts.

    Ownership is enforced twice, exactly as the version read is: ``mv_owner_select``
    (``user_id = auth.uid()``) on the caller's request-scoped client, and an explicit
    ``.eq("user_id", ...)`` on the query. Another user's model version is a **404** - the
    same answer a non-existent id gets, so existence does not leak (Requirement 21.4).

    The response carries the model version's client projection (Requirement 21.2's
    filtered read), which by construction has no ``artifact_uri``, plus ``download_url``,
    ``expires_at`` and the checksum and byte count a client needs to verify what it
    downloads.

    Status codes
        **200** a signed link, valid for five minutes.
        **404** no such model version belongs to this user - including when
        ``004d_training_and_models.sql`` has not been applied.
        **503** no signing secret is configured, so no link can be signed. Deliberately
        not a 200 with an unsigned URL.
        **429** from the existing limiter.
    """
    from backend_app.backend import model_versioning as MV

    try:
        descriptor = await MV.artifact_download_descriptor(user, model_version_id)
    except MV.ModelVersionNotFound as e:
        raise HTTPException(
            status_code=404,
            detail={"error": "MODEL_VERSION_NOT_FOUND", "message": str(e)},
        )
    except MV.SignedLinkUnavailable as e:
        logger.error("A model artifact link could not be signed: %s", e)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "ARTIFACT_LINK_UNAVAILABLE",
                "message": "Artifact downloads are unavailable: no signing secret is "
                           "configured.",
            },
        )
    except MV.ModelVersioningError as e:
        logger.error("A model artifact link could not be issued: %s", e)
        raise HTTPException(
            status_code=404,
            detail={"error": "MODEL_VERSION_NOT_FOUND", "message": str(e)},
        )

    return {"status": "signed", **descriptor}


# ══════════════════════════════════════════════════════════════════════════
# FEED STATE AND THE DATA QUALITY REPORT (task 7.4)
#
# `design.md` -> API surface: `GET /api/strategy-operations/strategies/{id}/data-quality`,
# "Feed state + quality report for the version's data config | reads `DataQualityReport`".
# `design.md` -> Data preview and honesty: "Feed state is reported literally... Stale data is
# never labelled `LIVE`, and the panel shows the concrete age". Requirements 19.6-19.10 and
# 19.14.
#
# WHAT AUTHENTICATES THIS ENDPOINT
# --------------------------------
# It is a NEW network-exposed endpoint, and it is authenticated by the platform's own
# `Depends(get_current_user)` bearer dependency - the same one every other tenant-scoped read
# on this router carries - plus the existing `slowapi` limiter (`@limiter.limit`). An
# unauthenticated caller is refused by the dependency and reaches neither a strategy row nor
# a market-data fetch. Ownership is then enforced TWICE, exactly as the preview and the model
# version reads do: `StrategyService.get_strategy` queries the caller's own RLS-scoped
# client AND filters `.eq("user_id", user["id"])`. Another tenant's strategy is a **404**,
# identical to a missing one, so existence does not leak (Requirement 21.4).
#
# WHAT IT COMPUTES: NOTHING
# -------------------------
# The quality report is `market_data_validation.MarketDataValidator`'s, reached through task
# 7.5's `market_data_contract.validated_window` - which calls that module's own
# `get_validator()` singleton, the same instance and the same `ValidationConfig` the training
# admission gate reads (`strategy_service.quality_check_training_frame`). So a report served
# here cannot disagree with the verdict that blocks a training run, and it cannot disagree
# with the window the preview computes indicators on either: one closed-bar rule, one
# validator call, one set of ingest counters, read rather than re-derived. No threshold is
# redefined, no validator is subclassed and no rule is relaxed for a read path.
# `market_data_validation.py` is NOT modified by this task.
#
# THE STRICT OUTLIER POSTURE IS PRESERVED, AND REPORTED HONESTLY
# --------------------------------------------------------------
# `OutlierDetector._z_score_filter` rejects a whole window containing any close beyond 3σ,
# and `GapHandler.handle` rejects a window with any gap. Earlier phases mapped that to a
# classified `DATA_QUALITY` block rather than loosening the threshold, and this endpoint
# keeps that posture: a `DataValidationError` becomes
# `data_quality.status = "REJECTED"` carrying `strategy_service.REASON_DATA_QUALITY` and the
# validator's own message. A rejected window is REPORTED as rejected - it is not retried
# against looser settings, not downgraded to a warning, and not turned into a 500.
#
# WHY A POOR REPORT IS STILL A 200
# --------------------------------
# The training gate BLOCKS on `POOR`/`UNUSABLE` (`BLOCKING_QUALITY_LEVELS`); this endpoint
# does not, because its whole job is to say how good the data is. Refusing to serve a bad
# report would hide exactly the case the report exists for. The grade is on the wire and the
# gate stays authoritative where it belongs.
#
# WHY FEED STATE IS A 200 EVEN WITH NO FEED AT ALL
# ------------------------------------------------
# `DISCONNECTED` is a legitimate, useful answer, and it is the truthful one in an environment
# with no running feed. A 503 here would leave a status strip with nothing to render, which
# is how a UI ends up showing the last colour it had - a stale reading presented as current.
# So: feed state is always served, `data_quality.available` says whether a report could be
# produced, and every unmeasured figure is an explicit `null`. Never a 500.
# ══════════════════════════════════════════════════════════════════════════

#: Ceiling on the quality window, in bars. The same figure and the same reasoning as
#: :data:`PREVIEW_MAX_BARS`: one bounded fetch, one bounded validation, and a cost a read
#: endpoint can carry (Requirement 25.x).
DATA_QUALITY_MAX_BARS = PREVIEW_MAX_BARS

#: Floor on the quality window. Below a couple of hundred bars the validator's own outlier
#: statistics are being computed over too little history to mean much.
DATA_QUALITY_MIN_BARS = 200

#: Bars read beyond the plan's warmup, so that "are there enough bars for warmup?" is
#: answered by a window that could actually contain the answer.
DATA_QUALITY_WARMUP_HEADROOM_BARS = 50


def _data_quality_window(warmup_bars: int) -> Dict[str, Any]:
    """The bounded window the report is computed over, and what it can conclude.

    ``covers_warmup`` is the part that matters for honesty. Requirement 19.10 compares
    available bars against the compiled warmup, and this endpoint's only bar count is the
    window it read - so when the warmup exceeds the ceiling, that count is a *truncation*,
    not an availability figure, and comparing it would report ``INSUFFICIENT_DATA`` for a
    market that may hold plenty of history. In that case the count is still reported (with
    ``window_limited: true``) but it is NOT passed to the classifier, so the state says
    nothing it cannot support.
    """
    warmup = max(0, int(warmup_bars or 0))
    bars = min(
        DATA_QUALITY_MAX_BARS,
        max(DATA_QUALITY_MIN_BARS, warmup + DATA_QUALITY_WARMUP_HEADROOM_BARS),
    )
    return {
        "bars": bars,
        "max_bars": DATA_QUALITY_MAX_BARS,
        "min_bars": DATA_QUALITY_MIN_BARS,
        "warmup_bars": warmup,
        "covers_warmup": bars >= warmup,
        "window_limited": bars < warmup + DATA_QUALITY_WARMUP_HEADROOM_BARS,
    }


def _data_quality_market(plan: Any) -> Dict[str, str]:
    """The one market this version reads, from its own DATA nodes.

    The scan is ``strategy_service.scan_plan_markets`` - the same one the preview and the
    training path use, so those three cannot disagree about which market a graph declares.
    Only the error contract is this endpoint's own: a report that cannot be attributed to a
    single bar series is a different answer from a refused preview or a blocked training run.
    """
    from backend_app.backend.strategy_service import scan_plan_markets

    symbols, timeframes, _data_node = scan_plan_markets(plan)
    if not symbols or not timeframes:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "DATA_QUALITY_MARKET_UNRESOLVED",
                "message": (
                    "This version's Market Data block declares no symbol and timeframe, "
                    "so there is no data source to report quality for."
                ),
                "hint": "Set the symbol and timeframe on the Market Data block.",
            },
        )
    if len(symbols) > 1 or len(timeframes) > 1:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "DATA_QUALITY_MULTIPLE_MARKETS",
                "message": (
                    "This version reads more than one market. A quality report describes "
                    "one bar series, so reporting on one of them would mean choosing a "
                    "feed on the author's behalf."
                ),
                "symbols": symbols,
                "timeframes": timeframes,
            },
        )
    return {"symbol": symbols[0], "timeframe": timeframes[0]}


async def _data_quality_payload(
    symbol: str, timeframe: str, bars: int
) -> Dict[str, Any]:
    """``market_data_validation``'s report for a bounded window, or an honest absence.

    Nothing here validates, grades, counts or repairs anything. The window goes through
    ``market_data_contract.validated_window`` - task 7.5's one ingest entry point - which
    resolves the gap policy, applies the closed-bar gate and then calls the existing
    ``MarketDataValidator``. This function translates the outcome into a report body.

    Why this seam and not a second fetch-and-validate sequence
        7.5 owns the ingest side and 7.4 owns the reporting side, so the counters and the
        report published here are **read** from that contract rather than re-derived: one
        closed-bar rule, one validator call, one set of counters. A private
        fetch-then-validate here would be a second ingest path free to drift from the one
        that computes indicators - which is the ``DataQualityReport`` equivalent of SB-01.
        ``mode`` is ``MODE_BACKTEST`` because this is exactly what that mode names: a
        bounded historical read with no order router downstream of it.

    Three outcomes, none of them a 500 and none of them silent:

    ``REPORTED``    the validator produced a :class:`DataQualityReport`. Served whole,
                    whatever the grade, plus ``quality_level_name`` because
                    ``DataQualityLevel.GOOD.value`` is the integer 85 and an integer read as
                    a label is a report nobody can act on.
    ``REJECTED``    the ingest contract refused the window with ``DATA_QUALITY`` - the strict
                    outlier / gap / integrity posture. Reported as refused, carrying the
                    validator's own message, under the platform's existing
                    ``strategy_service.REASON_DATA_QUALITY`` vocabulary. Never retried under
                    looser thresholds.
    ``UNAVAILABLE`` no window could be read at all: the feed is unconfigured, returned
                    nothing, held no closed bar, or the timeframe is not one the pipeline
                    can measure. ``available: false`` and every figure ``null`` - an
                    unavailable report is never rendered as a good one.
    """
    from backend_app.backend.market_data_contract import (
        MODE_BACKTEST,
        MarketDataContractError,
        validated_window,
    )
    from backend_app.backend.strategy_service import (
        REASON_DATA_QUALITY,
        REASON_DATA_UNAVAILABLE,
    )

    source = (
        "market_data_validation.MarketDataValidator "
        "via market_data_contract.validated_window"
    )
    unavailable = {
        "available": False,
        "status": "UNAVAILABLE",
        "reason": REASON_DATA_UNAVAILABLE,
        "source": source,
        "report": None,
        "quality_score": None,
        "quality_level": None,
        "quality_level_name": None,
        "available_bars": None,
        "counters": None,
        "gap_policy": None,
    }

    try:
        rows = await _fetch_preview_bars(symbol, timeframe, bars)
    except HTTPException as e:
        # The preview path's own 503 for an unconfigured feed. Here it is not a failure of
        # the request: "no window could be read" is part of the answer this endpoint gives.
        detail = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
        return {
            **unavailable,
            "message": detail.get("message"),
            "detail_code": detail.get("error"),
        }
    except Exception as e:
        # The venue is never named to the caller (SB-06); the operator gets the log.
        logger.warning(
            "Data quality window unavailable for %s %s: %s", symbol, timeframe, e
        )
        return {
            **unavailable,
            "message": (
                f"The platform market data feed could not supply a window for {symbol} "
                f"at {timeframe}."
            ),
            "detail_code": type(e).__name__,
        }

    try:
        result = await validated_window(
            rows, symbol, timeframe, mode=MODE_BACKTEST, bars=bars
        )
    except MarketDataContractError as e:
        if e.code == "DATA_QUALITY":
            logger.info(
                "The data validator rejected the quality window for %s %s: %s",
                symbol,
                timeframe,
                e,
            )
            return {
                "available": True,
                "status": "REJECTED",
                "reason": REASON_DATA_QUALITY,
                "source": source,
                "report": None,
                "quality_score": None,
                "quality_level": None,
                "quality_level_name": "REJECTED",
                # A refused window has no *validated* bar count, so none is claimed; the
                # feed state therefore does not assert a warmup shortfall from it.
                "available_bars": None,
                "counters": None,
                "gap_policy": None,
                "validator_error": e.details.get("validator_error") or e.message,
                "message": e.message,
            }
        logger.info(
            "No quality report could be produced for %s %s: %s", symbol, timeframe, e
        )
        return {
            **unavailable,
            "reason": e.code,
            "message": e.message,
            "detail_code": e.code,
            "counters": e.details.get("counters"),
        }
    except Exception as e:
        logger.error(
            "The data validator could not report on %s %s: %s", symbol, timeframe, e
        )
        return {
            **unavailable,
            "message": (
                "The platform's data validator could not produce a report for this "
                "window."
            ),
            "detail_code": type(e).__name__,
        }

    # `IngestResult.to_dict()` verbatim: `mode`, `closed_bars_only`, `counters`,
    # `gap_policy`, `quality`. Read from 7.5's contract, not recomputed here.
    ingest = result.to_dict()
    body = ingest["quality"]
    return {
        "available": True,
        "status": "REPORTED",
        "reason": None,
        "source": source,
        "report": body,
        "quality_score": body.get("quality_score"),
        # `to_dict()` publishes the enum's *value*, which is a threshold integer. The name is
        # what a reader needs, so both are here and neither is guessed from the other.
        "quality_level": body.get("quality_level"),
        "quality_level_name": getattr(
            result.report.quality_level, "name", str(result.report.quality_level)
        ),
        # Closed bars only, so this is the count the warmup comparison may use.
        "available_bars": int(len(result.frame.index)),
        "mode": ingest["mode"],
        "closed_bars_only": ingest["closed_bars_only"],
        "counters": ingest["counters"],
        "gap_policy": ingest["gap_policy"],
    }


@router.get("/strategy-operations/strategies/{strategy_id}/data-quality")
@limiter.limit("60/minute")
async def get_strategy_data_quality(request: Request,
    response: Response,
    strategy_id: str,
    user: dict = Depends(get_current_user)
):
    """Feed state and the data quality report for this version's configured data source.

    Requirement 19.14 asks the API to serve the quality report for a version's configured
    data source; Requirements 19.6-19.10 ask for a feed state that is *measured*. Both are
    on one response because they answer one question - "can I trust what I am looking at?" -
    and splitting them would let a client render a state without the numbers behind it.

    The path, in order
    ------------------
    1. ``StrategyService.get_strategy`` resolves the strategy on the caller's RLS-scoped
       client with an explicit ``user_id`` filter. Not this tenant's, or not there at all:
       **404**, the same answer for both.
    2. The strategy's **current version** graph is parsed by ``schema.load_graph`` (a v1
       payload is migrated at read time) and compiled by ``get_compiler().compile_plan`` -
       the one validator and the one compiler (Requirement 3.1). The compiled plan is what
       supplies ``warmup_bars``, which is the figure Requirement 19.10 compares against, so
       a version that cannot compile has no compiled warmup and gets a **422** carrying the
       validation report rather than a report attributed to a graph that cannot run.
    3. The market comes from the plan's own DATA nodes through the shared scan. No venue is
       accepted or returned (SB-06, Requirement 12.1).
    4. Feed state is ``feed_state.evaluate_feed_state`` over
       ``feed_state.observe_feed``'s reading of the platform's existing connection monitor.
       There is no timer and no default: an unmeasurable feed is reported as such.
    5. The quality report is ``market_data_validation``'s, over a bounded window. Nothing on
       this path validates or grades anything itself.

    Feed state, literally
    ---------------------
    One of ``LIVE``, ``DELAYED``, ``STALE``, ``DISCONNECTED``, ``INSUFFICIENT_DATA``, with
    ``age_seconds``, ``age_text``, ``expected_interval_seconds``, the two thresholds in both
    multiples and seconds, ``available_bars`` / ``warmup_bars`` / ``bars_missing``, the
    age-only ``age_state``, and a rendered ``display`` sentence ("Last candle 4m 12s ago,
    expected every 5m"). ``LIVE`` requires a measured age below 1.5 x the expected interval
    (Property 26), so stale data cannot be labelled ``LIVE``; see
    :mod:`backend_app.backend.feed_state` for the boundaries and the precedence.

    Degrading honestly
    ------------------
    No feed, no monitor, no market data: **still a 200**, reporting ``DISCONNECTED`` with
    ``age_seconds: null`` and ``data_quality.available: false``. An unknown age is never
    reported as a fresh one, an unavailable report is never reported as a good one, and
    neither is a 500.

    Status codes
        **200** feed state, and a quality report when one could be produced.
        **404** no such strategy for this user.
        **422** the version carries no readable graph, does not compile, or declares no
        single market.
        **429** from the existing limiter.
        **401** from the auth dependency.
    """
    from backend_app.backend import feed_state as FS
    from backend_app.backend.strategy_compiler import get_compiler
    from backend_app.backend.strategy_dag.registry import get_registry
    from backend_app.backend.strategy_dag.schema import (
        GraphParseError,
        UnsupportedSchemaVersion,
        load_graph,
    )

    # ── 1. Ownership, before anything is read or fetched ───────────────────
    try:
        service = await get_strategy_service()
        owned = await service.get_strategy(user=user, strategy_id=strategy_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Data quality could not resolve strategy %s: %s", strategy_id, e
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "STRATEGY_GET_FAILED", "message": str(e)},
        )
    if not owned:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "STRATEGY_NOT_FOUND",
                "message": f"Strategy {strategy_id} not found",
            },
        )

    # ── 2. The current version's graph, parsed and compiled ────────────────
    version = owned.get("version") or {}
    payload = version.get("graph_json") or version.get("blueprint")
    if not payload:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "DATA_QUALITY_GRAPH_UNAVAILABLE",
                "message": (
                    "This strategy's current version carries no readable graph, so there "
                    "is no configured data source to report on."
                ),
            },
        )

    try:
        graph = load_graph(payload)
    except (GraphParseError, UnsupportedSchemaVersion) as e:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "STRATEGY_GRAPH_UNREADABLE",
                "message": str(e),
                "hint": "Send a canonical schema_version 2 graph with 'nodes' and 'edges'.",
            },
        )

    registry = get_registry()
    try:
        plan = get_compiler().compile_plan(graph, registry)
    except ValidationError as e:
        report = getattr(e, "report", None)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "STRATEGY_VALIDATION_FAILED",
                "message": str(e),
                "report": report.to_dict() if report is not None else None,
                "codes": list(e.codes()) if hasattr(e, "codes") else [],
                "hint": (
                    "The compiled warmup is what available bars are compared against, so "
                    "a version that does not compile has no warmup to compare to."
                ),
            },
        )
    except CompilerError as e:
        logger.error("Data quality compilation failed: %s", e)
        raise HTTPException(
            status_code=400,
            detail={"error": "STRATEGY_COMPILATION_FAILED", "message": str(e)},
        )

    market = _data_quality_market(plan)
    warmup_bars = int(getattr(plan, "warmup_bars", 0) or 0)
    window = _data_quality_window(warmup_bars)

    # ── 3. The quality report, over a bounded window ───────────────────────
    quality = await _data_quality_payload(
        market["symbol"], market["timeframe"], window["bars"]
    )

    # ── 4. Feed state, from the measured age and nothing else ──────────────
    try:
        observation = FS.observe_feed(market["symbol"])
    except Exception as e:  # an observation failure is a DISCONNECTED, not a 500
        logger.warning("Feed observation failed for %s: %s", market["symbol"], e)
        observation = FS.FeedObservation(
            connected=None,
            source=FS.OBSERVATION_UNAVAILABLE,
            detail={"error": type(e).__name__},
        )

    # The bar count is only comparable to the warmup when the window could have held it;
    # otherwise it is a truncation and asserting a shortfall from it would be a guess.
    countable_bars = (
        quality.get("available_bars") if window["covers_warmup"] else None
    )
    feed = FS.evaluate_feed_state(
        timeframe=market["timeframe"],
        connected=observation.connected,
        age_seconds=observation.age_seconds,
        last_event_at=observation.last_event_at,
        available_bars=countable_bars,
        warmup_bars=warmup_bars,
        observation_source=observation.source,
        detail={
            **(observation.detail or {}),
            "connections_matched": observation.connections_matched,
            "bars_countable": window["covers_warmup"],
        },
    )

    # A liveness reading must not be cached: a cached age is a stale reading served as a
    # current one, which is the exact defect this endpoint exists to prevent.
    response.headers["Cache-Control"] = "no-store"

    return {
        "strategy_id": strategy_id,
        "version_id": version.get("id"),
        "version": version.get("version"),
        "dag_hash": plan.dag_hash,
        "compiler_version": plan.compiler_version,
        "market": market,
        "warmup_bars": warmup_bars,
        "feed": feed.to_dict(),
        "data_quality": {**quality, "window": window},
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
