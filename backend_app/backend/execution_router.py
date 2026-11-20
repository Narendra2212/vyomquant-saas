import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend_app.core.dependencies import get_current_user
from backend_app.core.execution_engine import \
    ExecutionEngine as UnifiedExecutionEngine
from backend_app.core.feature_flags import ExecutionContext

logger = logging.getLogger(__name__)

router = APIRouter()

class SignalRequest(BaseModel):
    strategy_id: str
    symbol: Optional[str] = "BTC/USDT"
    signal: str
    confidence: Optional[float] = 1.0
    price: Optional[float] = 0.0
    
async def _measured_total_equity(tenant_id: str) -> Optional[Decimal]:
    """Return the tenant's measured account equity, or None when unknown.

    Deliberately returns None today: there is no verified equity source for this
    endpoint, so it refuses rather than guessing (SEC-14). Replace with a real
    exchange/ledger-backed lookup; never a constant.
    """
    return None


@router.post("/signal")
async def process_signal(body: SignalRequest, user: dict = Depends(get_current_user)):
    logger.info(f"Received signal for strategy {body.strategy_id}: {body.signal}")

    # SEC-14: risk limits are sized against the account's MEASURED equity. No
    # trustworthy per-tenant equity source is wired to this endpoint, and a
    # fabricated figure would let every account trade against a $100k limit.
    # Fail closed until a measured value is supplied.
    total_equity = await _measured_total_equity(user["id"])
    if total_equity is None or total_equity <= 0:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MEASURED_EQUITY_UNAVAILABLE",
                "message": "Execution refused: no measured account equity is available.",
            },
        )
    engine = UnifiedExecutionEngine()

    try:
        res = await engine.execute_trade(
            tenant_id=user["id"],
            strategy_id=body.strategy_id,
            symbol=body.symbol,
            side=body.signal.lower(),
            size=Decimal("0.01"),
            price=Decimal(str(body.price)) if body.price and body.price > 0 else Decimal("0"),
            context=ExecutionContext.API_ORDERS,
            metadata={"portfolio_state": {"total_equity": total_equity}}
        )
        return {
            "status": "completed" if res.success else "failed",
            "strategy_id": body.strategy_id,
            "signal": body.signal,
            "execution_id": res.execution_id,
            "message": res.message
        }
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        # Even if it fails, we want to return 200/201 if it created a row or executed gracefully but failed
        raise HTTPException(status_code=500, detail=str(e))
