"""
routers/signal_trace.py — Signal Trace API Router

Professional execution audit system API endpoints.
Complete signal lifecycle tracking from strategy decision to final execution.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Request, APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from backend_app.backend.signal_service import get_signal_service, SignalStatus, SignalDecision
from backend_app.core.dependencies import get_current_user
from backend_app.core.rate_limit import limiter

router = APIRouter()
logger = logging.getLogger("SignalTraceRouter")


# ══════════════════════════════════════════════════════════════════════════
# REQUEST MODELS
# ══════════════════════════════════════════════════════════════════════════

class SignalCreateRequest(BaseModel):
    """Request model for creating a signal."""
    strategy_id: str = Field(..., description="Strategy ID")
    strategy_version: str = Field(..., description="Strategy version")
    deployment_id: str = Field(..., description="Deployment ID")
    exchange_id: str = Field(..., description="Exchange ID")
    symbol: str = Field(..., description="Trading pair")
    timeframe: str = Field(..., description="Timeframe")
    worker_id: str = Field(..., description="Worker ID")
    decision: str = Field(..., description="Signal decision: BUY, SELL, EXIT, CLOSE, HOLD")
    indicators: Dict = Field(..., description="Indicator values")
    market_info: Dict = Field(..., description="Market information")
    ml_info: Optional[Dict] = Field(None, description="ML/DL information")


class RiskDecisionRequest(BaseModel):
    """Request model for updating risk decision."""
    risk_passed: bool = Field(..., description="Whether risk check passed")
    risk_reason: str = Field(..., description="Reason for risk decision")
    position_size: Optional[float] = Field(None, description="Calculated position size")
    capital: Optional[float] = Field(None, description="Available capital")
    exposure: Optional[float] = Field(None, description="Current exposure")
    expected_loss: Optional[float] = Field(None, description="Expected loss")
    expected_reward: Optional[float] = Field(None, description="Expected reward")
    drawdown_check: Optional[bool] = Field(None, description="Drawdown check result")


class OrderUpdateRequest(BaseModel):
    """Request model for updating order information."""
    order_id: str = Field(..., description="Order ID")
    exchange_order_id: Optional[str] = Field(None, description="Exchange order ID")
    order_status: str = Field(..., description="Order status")
    quantity: float = Field(..., description="Order quantity")
    filled: float = Field(..., description="Filled quantity")
    remaining: float = Field(..., description="Remaining quantity")
    average_price: float = Field(..., description="Average fill price")
    fees: float = Field(..., description="Trading fees")
    slippage: float = Field(..., description="Slippage")
    latency_ms: float = Field(..., description="Order latency in milliseconds")


class ExecutionUpdateRequest(BaseModel):
    """Request model for updating execution information."""
    trade_id: str = Field(..., description="Trade ID")
    pnl: float = Field(..., description="PnL")
    realized_pnl: Optional[float] = Field(None, description="Realized PnL")


# ══════════════════════════════════════════════════════════════════════════
# SIGNAL CRUD OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

@router.post("/signals")
@limiter.limit("100/minute")
async def create_signal(request: Request,
    body: SignalCreateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Create a new signal record.
    
    Signal generation event in the execution audit trail.
    """
    try:
        service = await get_signal_service()
        
        signal = await service.create_signal(
            user=user,
            strategy_id=request.strategy_id,
            strategy_version=request.strategy_version,
            deployment_id=request.deployment_id,
            exchange_id=request.exchange_id,
            symbol=request.symbol,
            timeframe=request.timeframe,
            worker_id=request.worker_id,
            decision=request.decision,
            indicators=request.indicators,
            market_info=request.market_info,
            ml_info=request.ml_info
        )
        
        return signal
    except Exception as e:
        logger.error(f"Error creating signal for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SIGNAL_CREATE_FAILED", "message": str(e)}
        )


@router.get("/signals/{signal_id}")
@limiter.limit("200/minute")
async def get_signal(request: Request, 
    signal_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get complete signal data.
    
    Returns all signal information including:
    - Signal metadata
    - Risk decision
    - Order information
    - Execution information
    - Timeline
    """
    try:
        service = await get_signal_service()
        
        signal = await service.get_signal(user, signal_id)
        if not signal:
            raise HTTPException(
                status_code=404,
                detail={"error": "SIGNAL_NOT_FOUND", "message": f"Signal {signal_id} not found"}
            )
        
        # Get timeline
        timeline = await service.get_signal_timeline(user, signal_id)
        
        return {
            "signal": signal,
            "timeline": timeline
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting signal {signal_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SIGNAL_GET_FAILED", "message": str(e)}
        )


@router.get("/signals")
@limiter.limit("200/minute")
async def list_signals(request: Request, 
    strategy_id: Optional[str] = Query(None),
    exchange_id: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    worker_id: Optional[str] = Query(None),
    deployment_id: Optional[str] = Query(None),
    decision: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    ml_type: Optional[str] = Query(None, description="ml or rule_based"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user)
):
    """
    List signals with comprehensive filters.
    
    Supports filtering by:
    - Strategy, Exchange, Symbol, Worker, Deployment
    - Decision (BUY, SELL, EXIT, CLOSE, HOLD)
    - Status (pending, accepted, rejected, executed, failed, cancelled, expired)
    - ML type (ml, rule_based)
    - Date range
    - Search (signal_id)
    """
    try:
        service = await get_signal_service()
        
        signals = await service.list_signals(
            user=user,
            strategy_id=strategy_id,
            exchange_id=exchange_id,
            symbol=symbol,
            worker_id=worker_id,
            deployment_id=deployment_id,
            decision=decision,
            status=status,
            ml_type=ml_type,
            date_from=date_from,
            date_to=date_to,
            search=search,
            limit=limit,
            offset=offset
        )
        
        return {
            "signals": signals,
            "total": len(signals),
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        logger.error(f"Error listing signals for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SIGNALS_LIST_FAILED", "message": str(e)}
        )


@router.put("/signals/{signal_id}/risk")
@limiter.limit("100/minute")
async def update_risk_decision(request: Request, 
    signal_id: str,
    body: RiskDecisionRequest,
    user: dict = Depends(get_current_user)
):
    """
    Update signal with risk decision.
    
    Risk evaluation event in the execution audit trail.
    """
    try:
        service = await get_signal_service()
        
        signal = await service.update_risk_decision(
            user=user,
            signal_id=signal_id,
            risk_passed=request.risk_passed,
            risk_reason=request.risk_reason,
            position_size=request.position_size,
            capital=request.capital,
            exposure=request.exposure,
            expected_loss=request.expected_loss,
            expected_reward=request.expected_reward,
            drawdown_check=request.drawdown_check
        )
        
        return signal
    except Exception as e:
        logger.error(f"Error updating risk decision for signal {signal_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "RISK_UPDATE_FAILED", "message": str(e)}
        )


@router.put("/signals/{signal_id}/order")
@limiter.limit("100/minute")
async def update_order(request: Request, 
    signal_id: str,
    body: OrderUpdateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Update signal with order information.
    
    Order generation and exchange response event in the execution audit trail.
    """
    try:
        service = await get_signal_service()
        
        signal = await service.update_order(
            user=user,
            signal_id=signal_id,
            order_id=request.order_id,
            exchange_order_id=request.exchange_order_id,
            order_status=request.order_status,
            quantity=request.quantity,
            filled=request.filled,
            remaining=request.remaining,
            average_price=request.average_price,
            fees=request.fees,
            slippage=request.slippage,
            latency_ms=request.latency_ms
        )
        
        return signal
    except Exception as e:
        logger.error(f"Error updating order for signal {signal_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "ORDER_UPDATE_FAILED", "message": str(e)}
        )


@router.put("/signals/{signal_id}/execution")
@limiter.limit("100/minute")
async def update_execution(request: Request, 
    signal_id: str,
    body: ExecutionUpdateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Update signal with execution and PnL information.
    
    Execution event in the execution audit trail.
    """
    try:
        service = await get_signal_service()
        
        signal = await service.update_execution(
            user=user,
            signal_id=signal_id,
            trade_id=request.trade_id,
            pnl=request.pnl,
            realized_pnl=request.realized_pnl
        )
        
        return signal
    except Exception as e:
        logger.error(f"Error updating execution for signal {signal_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "EXECUTION_UPDATE_FAILED", "message": str(e)}
        )


@router.get("/signals/{signal_id}/timeline")
@limiter.limit("200/minute")
async def get_signal_timeline(request: Request, 
    signal_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get complete signal timeline.
    
    Returns all events in chronological order:
    - SIGNAL_GENERATED
    - RISK_EVALUATED
    - ORDER_CREATED
    - EXCHANGE_RESPONSE
    - EXECUTED
    """
    try:
        service = await get_signal_service()
        
        timeline = await service.get_signal_timeline(user, signal_id)
        
        return {
            "signal_id": signal_id,
            "timeline": timeline
        }
    except Exception as e:
        logger.error(f"Error getting timeline for signal {signal_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "TIMELINE_GET_FAILED", "message": str(e)}
        )


@router.get("/signals/export")
@limiter.limit("50/minute")
async def export_signals(request: Request, 
    format: str = Query("json", pattern="^(json|csv)$"),
    strategy_id: Optional[str] = Query(None),
    exchange_id: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    user: dict = Depends(get_current_user)
):
    """
    Export signals with filters.
    
    Supports JSON and CSV formats for external compliance audit.
    """
    try:
        service = await get_signal_service()
        
        filters = {
            "strategy_id": strategy_id,
            "exchange_id": exchange_id,
            "symbol": symbol,
            "status": status,
            "date_from": date_from,
            "date_to": date_to
        }
        
        data = await service.export_signals(user, filters, format)
        
        if format == "csv":
            return Response(
                content=data,
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=signal_trace.csv"}
            )
        
        return Response(
            content=data,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=signal_trace.json"}
        )
    except Exception as e:
        logger.error(f"Error exporting signals for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "EXPORT_FAILED", "message": str(e)}
        )
