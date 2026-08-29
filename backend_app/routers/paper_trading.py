"""
routers/paper_trading.py — Paper Trading API Router for VyomQuant.

Endpoints for managing virtual paper accounts, paper orders, positions, and performance.
Enforces tenant isolation, parameter validation, rate limiting, and execution safety.
"""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend_app.backend.paper_trading_service import get_paper_trading_service
from backend_app.core.dependencies import get_current_user
from backend_app.core.rate_limit import limiter

router = APIRouter()
logger = logging.getLogger("PaperTradingRouter")


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class PaperOrderRequest(BaseModel):
    symbol: str = Field(..., example="BTC-USDT", description="Trading pair symbol")
    side: str = Field(..., example="buy", description="Order side: 'buy' or 'sell'")
    order_type: str = Field("market", example="market", description="Order type: 'market' or 'limit'")
    quantity: float = Field(..., gt=0, example=0.01, description="Order quantity")
    price: Optional[float] = Field(None, gt=0, example=65000.0, description="Limit price (required for limit orders)")
    strategy_id: Optional[str] = Field(None, description="Optional associated strategy ID")
    deployment_id: Optional[str] = Field(None, description="Optional associated deployment ID")


class PaperResetRequest(BaseModel):
    capital: float = Field(100000.0, gt=0, example=100000.0, description="New virtual starting capital")


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/account")
@limiter.limit("120/minute")
async def get_paper_account(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Get the current user's virtual paper trading account details."""
    service = get_paper_trading_service()
    account = service.get_or_create_account(user["id"])
    return account


@router.post("/account/reset")
@limiter.limit("30/minute")
async def reset_paper_account(
    request: Request,
    body: PaperResetRequest,
    user: dict = Depends(get_current_user)
):
    """Reset virtual paper account balance to specified capital and clear positions."""
    service = get_paper_trading_service()
    account = service.reset_account(user["id"], capital=body.capital)

    try:
        from backend_app.core.notification_dispatcher import dispatch_user_notification
        await dispatch_user_notification(
            user_id=user["id"],
            event_type="paper_account_reset",
            category="system",
            severity="info",
            title="Paper Account Reset",
            message=f"Paper account balance reset to ${body.capital:,.2f}.",
            metadata={"capital": body.capital, "idempotency_key": f"paper_reset:{user['id']}:{account.get('reset_at', '')}"},
        )
    except Exception as notif_err:
        logger.debug(f"[PAPER] Reset notification error: {notif_err}")

    return {
        "status": "success",
        "message": f"Paper account reset with ${body.capital:,.2f} virtual capital",
        "account": account
    }


@router.get("/positions")
@limiter.limit("120/minute")
async def get_paper_positions(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Get all open paper trading positions with current unrealized PnL."""
    service = get_paper_trading_service()
    positions = service.get_positions(user["id"])
    return {"positions": positions, "count": len(positions)}


@router.get("/orders")
@limiter.limit("120/minute")
async def get_paper_orders(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status (OPEN, FILLED, CANCELLED)"),
    user: dict = Depends(get_current_user)
):
    """Get list of paper trading orders for user."""
    service = get_paper_trading_service()
    orders = service.get_orders(user["id"], status=status)
    return {"orders": orders, "count": len(orders)}


@router.post("/orders")
@limiter.limit("60/minute")
async def place_paper_order(
    request: Request,
    body: PaperOrderRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: dict = Depends(get_current_user)
):
    """Place a new paper trading market or limit order."""
    service = get_paper_trading_service()
    try:
        order = await service.place_order(
            user_id=user["id"],
            symbol=body.symbol,
            side=body.side,
            order_type=body.order_type,
            quantity=body.quantity,
            price=body.price,
            strategy_id=body.strategy_id,
            deployment_id=body.deployment_id,
            idempotency_key=idempotency_key,
        )

        try:
            from backend_app.core.notification_dispatcher import dispatch_user_notification
            await dispatch_user_notification(
                user_id=user["id"],
                event_type="paper_order_placed",
                category="trade",
                severity="info",
                title=f"Paper Order: {body.side.upper()} {body.symbol}",
                message=f"Paper {body.order_type} order for {body.quantity} {body.symbol} executed.",
                strategy_id=body.strategy_id,
                metadata={
                    "order_id": order.get("id"),
                    "symbol": body.symbol,
                    "side": body.side,
                    "quantity": body.quantity,
                    "price": body.price,
                    "idempotency_key": f"paper_order:{user['id']}:{order.get('id')}"
                }
            )
        except Exception as notif_err:
            logger.debug(f"[PAPER] Order notification error: {notif_err}")

        return {"status": "success", "order": order}
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "PAPER_ORDER_INVALID", "message": str(e)})
    except Exception as e:
        logger.error(f"Paper order placement failed: {e}")
        raise HTTPException(status_code=500, detail={"error": "PAPER_ORDER_FAILED", "message": str(e)})


@router.delete("/orders/{order_id}")
@limiter.limit("60/minute")
async def cancel_paper_order(
    request: Request,
    order_id: str,
    user: dict = Depends(get_current_user)
):
    """Cancel an open paper limit order."""
    service = get_paper_trading_service()
    try:
        order = await service.cancel_order(user["id"], order_id)
        return {"status": "cancelled", "order": order}
    except PermissionError:
        raise HTTPException(status_code=403, detail={"error": "FORBIDDEN", "message": "Access denied"})
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "CANCEL_FAILED", "message": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "CANCEL_ERROR", "message": str(e)})


@router.get("/trades")
@limiter.limit("120/minute")
async def get_paper_trades(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user)
):
    """Get trade fill history for paper trading."""
    service = get_paper_trading_service()
    trades = service.get_trades(user["id"], limit=limit)
    return {"trades": trades, "count": len(trades)}


@router.get("/summary")
@limiter.limit("120/minute")
async def get_paper_summary(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Get complete paper trading performance summary, win rates, and account balance."""
    service = get_paper_trading_service()
    summary = service.get_performance_summary(user["id"])
    return summary
