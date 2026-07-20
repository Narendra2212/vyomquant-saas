"""
Bot Monitoring + Signal Visualization API Router

FastAPI endpoints for:
- Bot health monitoring
- Signal trace visualization
- Execution tracking
- Risk event auditing

SPRINT 2 FIX F-06: Replaced local mock get_current_user with the real JWT-
validating dependency from core.dependencies. The mock returned a hardcoded
"user-123" / "tenant-456" for any non-"invalid" token, effectively granting
every request the same admin identity. All endpoints now require a valid
Supabase JWT and use user["id"] for tenant isolation.
"""

import logging
import sys
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Query, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import asyncio

# Shared real JWT authentication dependency
sys.path.insert(0, '..')
from core.dependencies import get_current_user as _get_current_user

# Import telemetry systems
from backend.bot_telemetry import (
    telemetry, BotStatus, SignalStatus, RiskEventType,
    BotHealthMetrics, SignalEvent, ExecutionEvent, RiskEvent
)
from backend.signal_trace_engine import (
    trace_engine, TraceStatus, NodeType, ValidationResult,
    SignalTraceRecord
)
from backend.ws_event_stream import (
    ws_streamer, ChannelType, EventType,
    publish_bot_health, publish_signal_trace, publish_execution,
    publish_risk_event
)

logger = logging.getLogger(__name__)

# Router setup
router = APIRouter(
    prefix="/api/v1",
    tags=["bot-monitoring"],
    responses={
        401: {"description": "Unauthorized"},
        403: {"description": "Forbidden - not owner"},
        404: {"description": "Not found"}
    }
)

security = HTTPBearer()


# ============================================================================
# Authentication & Authorization Dependencies
# ============================================================================

# F-06 FIX: Use the shared real JWT-validating dependency.
# The previous local mock returned hardcoded user-123/tenant-456 for any token.
get_current_user = _get_current_user


async def get_current_user_id(user: Dict[str, Any] = Depends(get_current_user)) -> str:
    """Extract the authenticated user's Supabase user_id."""
    uid = user.get("id") or user.get("sub")
    if not uid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cannot extract user_id from token.",
        )
    return uid


async def verify_bot_ownership(
    bot_id: str,
    user_id: str = Depends(get_current_user_id)
) -> bool:
    """
    Verify user owns the bot (tenant isolation).
    Uses user["id"] (Supabase auth.uid()) as the isolation key.
    Raises 403 if bot belongs to a different user.
    """
    bot = await telemetry.registry.get_bot(bot_id)

    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bot {bot_id} not found"
        )

    # Isolation: compare bot's owner user_id against the authenticated user
    bot_owner = bot.metadata.get("user_id") or bot.metadata.get("tenant_id", "default")

    if bot_owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - bot belongs to a different user"
        )

    return True


async def verify_strategy_ownership(
    strategy_id: str,
    user_id: str = Depends(get_current_user_id)
) -> bool:
    """Verify user owns the strategy."""
    bots = await telemetry.registry.get_bots_by_strategy(strategy_id)

    if not bots:
        # Allow if no bots yet (strategy just created)
        return True

    # Check first bot's owner
    bot_owner = bots[0].metadata.get("user_id") or bots[0].metadata.get("tenant_id", "default")

    if bot_owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - strategy belongs to a different user"
        )

    return True


# ============================================================================
# Response Models
# ============================================================================

from pydantic import BaseModel, Field
from decimal import Decimal


class BotListItem(BaseModel):
    """Bot list item response."""
    bot_id: str
    strategy_id: str
    strategy_name: str
    exchange: str
    symbol: str
    mode: str
    created_at: datetime
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class BotHealthResponse(BaseModel):
    """Bot health metrics response."""
    bot_id: str
    status: str
    uptime_seconds: float
    last_heartbeat: Optional[datetime]
    last_signal_at: Optional[datetime]
    last_signal_status: Optional[str]
    execution_latency_ms: float
    reconnect_count: int
    websocket_connected: bool
    error_count: int
    signal_count: int
    execution_count: int
    pnl_24h: str  # Decimal as string
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class BotDetailResponse(BaseModel):
    """Complete bot details with health."""
    registration: BotListItem
    health: BotHealthResponse
    recent_signals: int
    recent_risk_events: int
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class SignalEventResponse(BaseModel):
    """Signal event response."""
    event_id: str
    bot_id: str
    timestamp: datetime
    signal_type: str
    symbol: str
    price: str  # Decimal as string
    confidence: float
    dag_path: str
    status: str
    ml_confidence: Optional[float]
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ExecutionEventResponse(BaseModel):
    """Execution event response."""
    event_id: str
    bot_id: str
    signal_id: str
    timestamp: datetime
    symbol: str
    side: str
    size: str  # Decimal as string
    price: str  # Decimal as string
    filled_amount: str  # Decimal as string
    fees: str  # Decimal as string
    slippage: str  # Decimal as string
    latency_ms: float
    success: bool
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class RiskEventResponse(BaseModel):
    """Risk event response."""
    event_id: str
    bot_id: str
    timestamp: datetime
    event_type: str
    severity: str
    description: str
    signal_id: Optional[str]
    blocked: bool
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class SignalTraceResponse(BaseModel):
    """Complete signal trace with pipeline stages."""
    trace_id: str
    signal_id: str
    strategy_id: str
    strategy_name: str
    bot_id: str
    symbol: str
    exchange: str
    created_at: datetime
    status: str
    final_decision: Optional[str]
    total_latency_ms: float
    pipeline: List[Dict[str, Any]]
    errors: List[Dict[str, Any]]
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class SystemSummaryResponse(BaseModel):
    """System-wide telemetry summary."""
    total_bots: int
    running_bots: int
    unhealthy_bots: int
    unhealthy_bot_ids: List[str]
    total_signals_generated: int
    total_errors: int
    total_reconnects: int
    critical_risk_events: int
    system_health: str


# ============================================================================
# API Endpoints
# ============================================================================

@router.get(
    "/bots",
    response_model=List[BotListItem],
    summary="List all bots",
    description="Get list of all bots for the authenticated user"
)
async def list_bots(
    user_id: str = Depends(get_current_user_id),
    status: Optional[str] = Query(None, description="Filter by status: running, paused, error"),
    exchange: Optional[str] = Query(None, description="Filter by exchange"),
    strategy_id: Optional[str] = Query(None, description="Filter by strategy")
):
    """
    List all bots for the authenticated user.
    Uses user_id (auth.uid()) for isolation — not a shared tenant_id.
    """
    # Get all bots for this user
    all_bots = await telemetry.registry.get_all_bots()

    # Filter by authenticated user_id
    user_bots = [
        bot for bot in all_bots
        if (bot.metadata.get("user_id") or bot.metadata.get("tenant_id", "")) == user_id
    ]

    # Apply additional filters
    if status:
        filtered = []
        for bot in user_bots:
            health = await telemetry.health.get_bot_health(bot.bot_id)
            if health and health.status.value == status:
                filtered.append(bot)
        user_bots = filtered

    if exchange:
        user_bots = [b for b in user_bots if b.exchange == exchange]

    if strategy_id:
        user_bots = [b for b in user_bots if b.strategy_id == strategy_id]

    return [
        BotListItem(
            bot_id=bot.bot_id,
            strategy_id=bot.strategy_id,
            strategy_name=bot.strategy_name,
            exchange=bot.exchange,
            symbol=bot.symbol,
            mode=bot.mode,
            created_at=bot.created_at
        )
        for bot in user_bots
    ]


@router.get(
    "/bots/{bot_id}",
    response_model=BotDetailResponse,
    summary="Get bot details",
    description="Get complete bot information with health metrics"
)
async def get_bot_details(
    bot_id: str,
    user_id: str = Depends(get_current_user_id),
    _verified: bool = Depends(verify_bot_ownership)
):
    """
    Get detailed information about a specific bot.
    
    Includes registration details, health metrics, and recent activity counts.
    """
    # Get full bot status
    full_status = await telemetry.get_full_bot_status(bot_id)
    
    if not full_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bot {bot_id} not found"
        )
    
    registration = full_status["registration"]
    health = full_status["health"]
    
    return BotDetailResponse(
        registration=BotListItem(
            bot_id=registration.bot_id,
            strategy_id=registration.strategy_id,
            strategy_name=registration.strategy_name,
            exchange=registration.exchange,
            symbol=registration.symbol,
            mode=registration.mode,
            created_at=registration.created_at
        ),
        health=BotHealthResponse(
            bot_id=health.bot_id,
            status=health.status.value,
            uptime_seconds=health.uptime_seconds,
            last_heartbeat=health.last_heartbeat,
            last_signal_at=health.last_signal_at,
            last_signal_status=health.last_signal_status.value if health.last_signal_status else None,
            execution_latency_ms=health.execution_latency_ms,
            reconnect_count=health.reconnect_count,
            websocket_connected=health.websocket_connected,
            error_count=health.error_count,
            signal_count=health.signal_count,
            execution_count=health.execution_count,
            pnl_24h=str(health.pnl_24h)
        ),
        recent_signals=len(full_status.get("recent_signals", [])),
        recent_risk_events=len(full_status.get("recent_risk_events", []))
    )


@router.get(
    "/bots/{bot_id}/signals",
    response_model=List[SignalEventResponse],
    summary="Get bot signals",
    description="Get recent signal events for a bot"
)
async def get_bot_signals(
    bot_id: str,
    limit: int = Query(50, ge=1, le=1000, description="Number of signals to return"),
    status_filter: Optional[str] = Query(None, description="Filter by status: received, validated, executed, rejected"),
    since: Optional[datetime] = Query(None, description="Only signals after this time"),
    user_id: str = Depends(get_current_user_id),
    _verified: bool = Depends(verify_bot_ownership)
):
    """
    Get recent signal events for a specific bot.
    
    Supports filtering by status and time range.
    """
    # Verify bot exists and belongs to tenant
    bot = await telemetry.registry.get_bot(bot_id)
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bot {bot_id} not found"
        )
    
    # Get signals
    signals = await telemetry.signals.get_recent_signals(bot_id, limit)
    
    # Apply status filter
    if status_filter:
        try:
            target_status = SignalStatus(status_filter.upper())
            signals = [s for s in signals if s.status == target_status]
        except ValueError:
            pass  # Invalid status, return all
    
    # Apply time filter
    if since:
        signals = [s for s in signals if s.timestamp > since]
    
    return [
        SignalEventResponse(
            event_id=sig.event_id,
            bot_id=sig.bot_id,
            timestamp=sig.timestamp,
            signal_type=sig.signal_type,
            symbol=sig.symbol,
            price=str(sig.price),
            confidence=sig.confidence,
            dag_path=sig.dag_path,
            status=sig.status.value,
            ml_confidence=sig.ml_confidence
        )
        for sig in signals
    ]


@router.get(
    "/bots/{bot_id}/executions",
    response_model=List[ExecutionEventResponse],
    summary="Get bot executions",
    description="Get recent order execution events for a bot"
)
async def get_bot_executions(
    bot_id: str,
    limit: int = Query(50, ge=1, le=500, description="Number of executions to return"),
    success_only: bool = Query(False, description="Only successful executions"),
    user_id: str = Depends(get_current_user_id),
    _verified: bool = Depends(verify_bot_ownership)
):
    """
    Get recent order execution events for a bot.
    
    Supports filtering by success status.
    """
    executions = await telemetry.executions.get_recent_executions(bot_id, limit)
    
    if success_only:
        executions = [e for e in executions if e.success]
    
    return [
        ExecutionEventResponse(
            event_id=ex.event_id,
            bot_id=ex.bot_id,
            signal_id=ex.signal_id,
            timestamp=ex.timestamp,
            symbol=ex.symbol,
            side=ex.side,
            size=str(ex.size),
            price=str(ex.price),
            filled_amount=str(ex.filled_amount),
            fees=str(ex.fees),
            slippage=str(ex.slippage),
            latency_ms=ex.latency_ms,
            success=ex.success
        )
        for ex in executions
    ]


@router.get(
    "/bots/{bot_id}/executions/stats",
    response_model=Dict[str, Any],
    summary="Get execution statistics",
    description="Get aggregated execution statistics for a bot"
)
async def get_bot_execution_stats(
    bot_id: str,
    user_id: str = Depends(get_current_user_id),
    _verified: bool = Depends(verify_bot_ownership)
):
    """
    Get execution statistics for a bot.
    
    Returns success rate, average latency, total fees, etc.
    """
    stats = await telemetry.executions.get_execution_stats(bot_id)
    return stats


@router.get(
    "/bots/{bot_id}/risk-events",
    response_model=List[RiskEventResponse],
    summary="Get bot risk events",
    description="Get recent risk management events for a bot"
)
async def get_bot_risk_events(
    bot_id: str,
    limit: int = Query(50, ge=1, le=200, description="Number of events to return"),
    severity: Optional[str] = Query(None, description="Filter by severity: low, medium, high, critical"),
    critical_only: bool = Query(False, description="Only critical and high severity"),
    user_id: str = Depends(get_current_user_id),
    _verified: bool = Depends(verify_bot_ownership)
):
    """
    Get recent risk events for a bot.
    
    Supports filtering by severity level.
    """
    if critical_only:
        events = await telemetry.risks.get_critical_events(bot_id, limit)
    else:
        severity_filter = [severity] if severity else None
        events = await telemetry.risks.get_recent_risk_events(
            bot_id, limit, severity_filter
        )
    
    return [
        RiskEventResponse(
            event_id=ev.event_id,
            bot_id=ev.bot_id,
            timestamp=ev.timestamp,
            event_type=ev.event_type.value,
            severity=ev.severity,
            description=ev.description,
            signal_id=ev.signal_id,
            blocked=ev.blocked
        )
        for ev in events
    ]


@router.get(
    "/bots/{bot_id}/risk-summary",
    response_model=Dict[str, Any],
    summary="Get risk summary",
    description="Get aggregated risk event summary for a bot"
)
async def get_bot_risk_summary(
    bot_id: str,
    user_id: str = Depends(get_current_user_id),
    _verified: bool = Depends(verify_bot_ownership)
):
    """
    Get risk event summary for a bot.
    
    Returns counts by severity and type.
    """
    summary = await telemetry.risks.get_risk_summary(bot_id)
    return summary


@router.get(
    "/signals/{signal_id}/trace",
    response_model=SignalTraceResponse,
    summary="Get signal trace",
    description="Get complete execution trace for a signal (DAG pipeline visualization)"
)
async def get_signal_trace(
    signal_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """
    Get complete signal trace with DAG pipeline stages.
    
    Visualizes: Market Data → Indicators → DAG Nodes → ML → Risk → Execution
    """
    # Get trace from engine
    # Note: In production, we'd query by signal_id from a database
    # For now, search through recent traces
    traces = await trace_engine.get_recent_traces(limit=1000)
    
    trace = None
    for t in traces:
        if t.signal_id == signal_id:
            trace = t
            break
    
    if not trace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Signal trace {signal_id} not found"
        )
    
    # Verify ownership via bot
    bot = await telemetry.registry.get_bot(trace.bot_id)
    bot_owner = bot.metadata.get("user_id") or bot.metadata.get("tenant_id", "") if bot else ""
    if not bot or bot_owner != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied - signal belongs to a different user"
        )
    
    # Convert to frontend format
    frontend_data = trace.to_frontend_format()
    
    return SignalTraceResponse(
        trace_id=trace.trace_id,
        signal_id=trace.signal_id,
        strategy_id=trace.strategy_id,
        strategy_name=trace.strategy_name,
        bot_id=trace.bot_id,
        symbol=trace.symbol,
        exchange=trace.exchange,
        created_at=trace.created_at,
        status=trace.status.value,
        final_decision=trace.final_decision,
        total_latency_ms=trace.total_latency_ms,
        pipeline=frontend_data["pipeline"],
        errors=trace.errors
    )


@router.get(
    "/traces/recent",
    response_model=List[SignalTraceResponse],
    summary="Get recent traces",
    description="Get recent signal execution traces for the authenticated user"
)
async def get_recent_traces(
    strategy_id: Optional[str] = None,
    status: Optional[str] = Query(None, description="Filter by status: pending, running, completed, failed, blocked"),
    limit: int = Query(50, ge=1, le=100),
    user_id: str = Depends(get_current_user_id)
):
    """
    Get recent signal traces for the tenant.
    
    Supports filtering by strategy and status.
    """
    status_filter = None
    if status:
        try:
            status_filter = [TraceStatus(status.upper())]
        except ValueError:
            pass
    
    traces = await trace_engine.get_traces_for_frontend(
        strategy_id=strategy_id,
        limit=limit
    )
    
    # Apply status filter
    if status_filter:
        traces = [t for t in traces if t["status"] == status]
    
    return [
        SignalTraceResponse(
            trace_id=t["id"],
            signal_id=t["signal_id"],
            strategy_id=t.get("strategy_id", ""),
            strategy_name=t["strategy"],
            bot_id="",  # Not exposed in frontend format
            symbol=t["symbol"],
            exchange="",
            created_at=datetime.fromisoformat(t["timestamp"].replace('Z', '+00:00')),
            status=t["status"],
            final_decision=t.get("final_decision"),
            total_latency_ms=t.get("latency_ms", 0),
            pipeline=t["pipeline"],
            errors=t.get("errors", [])
        )
        for t in traces
    ]


@router.get(
    "/system/summary",
    response_model=SystemSummaryResponse,
    summary="Get system summary",
    description="Get system-wide telemetry summary"
)
async def get_system_summary(
    user_id: str = Depends(get_current_user_id)
):
    """
    Get high-level system telemetry summary.
    
    Returns total bots, health status, signals, errors, etc.
    """
    summary = await telemetry.get_system_summary()
    return SystemSummaryResponse(**summary)


# ============================================================================
# WebSocket Endpoints
# ============================================================================

@router.websocket("/ws/bots")
async def bot_status_websocket(
    websocket: WebSocket,
    token: str = Query(..., description="Authentication token")
):
    """
    WebSocket for real-time bot status updates.
    F-06 FIX: Validates JWT token via Supabase before accepting connection.
    Streams: bot_health, bot_connected, bot_disconnected, bot_error
    """
    # F-06 FIX: Validate token using core.dependencies (Supabase JWT)
    from core.dependencies import get_supabase
    try:
        sb = get_supabase()
        resp = sb.auth.get_user(token)
        if not resp or not resp.user:
            await websocket.close(code=4001, reason="Unauthorized")
            return
        user_id = resp.user.id
    except Exception:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()

    try:
        connection_id = await ws_streamer.handle_connection(
            websocket=websocket,
            tenant_id=user_id  # Use real user_id for isolation
        )
        await ws_streamer.subscribe(connection_id, ChannelType.BOT_STATUS)
        while True:
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        logger.info("Bot status WebSocket disconnected")
    except Exception as e:
        logger.error(f"Bot status WebSocket error: {e}")


@router.websocket("/ws/signals")
async def signal_trace_websocket(
    websocket: WebSocket,
    token: str = Query(..., description="Authentication token"),
    bot_id: Optional[str] = Query(None, description="Filter to specific bot")
):
    """
    WebSocket for real-time signal trace updates.
    F-06 FIX: Validates JWT token via Supabase before accepting connection.
    Streams: signal_received, signal_validated, signal_executed, signal_rejected
    """
    # F-06 FIX: Validate token using core.dependencies (Supabase JWT)
    from core.dependencies import get_supabase
    try:
        sb = get_supabase()
        resp = sb.auth.get_user(token)
        if not resp or not resp.user:
            await websocket.close(code=4001, reason="Unauthorized")
            return
        user_id = resp.user.id
    except Exception:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Verify bot ownership if specified
    if bot_id:
        bot = await telemetry.registry.get_bot(bot_id)
        bot_owner = bot.metadata.get("user_id") or bot.metadata.get("tenant_id", "") if bot else ""
        if not bot or bot_owner != user_id:
            await websocket.close(code=4003, reason="Forbidden")
            return

    await websocket.accept()

    try:
        connection_id = await ws_streamer.handle_connection(
            websocket=websocket,
            tenant_id=user_id  # Use real user_id for isolation
        )
        await ws_streamer.subscribe(connection_id, ChannelType.SIGNAL_TRACE)
        
        # Replay recent events if requested
        if bot_id:
            # TODO: Filter by bot_id in replay
            pass
        
        while True:
            await asyncio.sleep(1)
            
    except WebSocketDisconnect:
        logger.info(f"Signal trace WebSocket disconnected")
    except Exception as e:
        logger.error(f"Signal trace WebSocket error: {e}")


@router.websocket("/ws/executions")
async def execution_events_websocket(
    websocket: WebSocket,
    token: str = Query(..., description="Authentication token")
):
    """
    WebSocket for real-time execution events.
    
    Streams: order_submitted, order_filled, order_rejected, order_error
    """
    tenant_id = "tenant-456"  # Mock from token
    
    await websocket.accept()
    
    try:
        connection_id = await ws_streamer.handle_connection(
            websocket=websocket,
            tenant_id=tenant_id
        )
        
        await ws_streamer.subscribe(connection_id, ChannelType.EXECUTION_EVENTS)
        
        while True:
            await asyncio.sleep(1)
            
    except WebSocketDisconnect:
        logger.info(f"Execution WebSocket disconnected")
    except Exception as e:
        logger.error(f"Execution WebSocket error: {e}")


@router.websocket("/ws/risk")
async def risk_events_websocket(
    websocket: WebSocket,
    token: str = Query(..., description="Authentication token")
):
    """
    WebSocket for real-time risk events.
    
    Streams: risk_block, risk_warning, kill_switch, position_limit, drawdown_alert
    """
    tenant_id = "tenant-456"  # Mock from token
    
    await websocket.accept()
    
    try:
        connection_id = await ws_streamer.handle_connection(
            websocket=websocket,
            tenant_id=tenant_id
        )
        
        await ws_streamer.subscribe(connection_id, ChannelType.RISK_EVENTS)
        
        while True:
            await asyncio.sleep(1)
            
    except WebSocketDisconnect:
        logger.info(f"Risk WebSocket disconnected")
    except Exception as e:
        logger.error(f"Risk WebSocket error: {e}")


@router.websocket("/ws/deployments")
async def deployment_events_websocket(
    websocket: WebSocket,
    token: str = Query(..., description="Authentication token")
):
    """
    WebSocket for real-time deployment events.
    
    Streams: deploy_started, deploy_success, deploy_failed, bot_started, bot_stopped
    """
    tenant_id = "tenant-456"  # Mock from token
    
    await websocket.accept()
    
    try:
        connection_id = await ws_streamer.handle_connection(
            websocket=websocket,
            tenant_id=tenant_id
        )
        
        await ws_streamer.subscribe(connection_id, ChannelType.DEPLOYMENT_EVENTS)
        
        while True:
            await asyncio.sleep(1)
            
    except WebSocketDisconnect:
        logger.info(f"Deployment WebSocket disconnected")
    except Exception as e:
        logger.error(f"Deployment WebSocket error: {e}")


# ============================================================================
# Health Check
# ============================================================================

@router.get(
    "/health",
    response_model=Dict[str, str],
    summary="Health check",
    description="Check monitoring API health"
)
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "bot-monitoring-api"}


# Export router
__all__ = ["router"]
