import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend_app.core.dependencies import get_current_user
from backend_app.core.feature_flags import ExecutionContext
from backend_app.core.unified_execution_engine import UnifiedExecutionEngine

logger = logging.getLogger(__name__)

router = APIRouter()

class SignalRequest(BaseModel):
    strategy_id: str
    symbol: Optional[str] = "BTC/USDT"
    signal: str
    confidence: Optional[float] = 1.0
    price: Optional[float] = 0.0
    
@router.post("/signal")
async def process_signal(body: SignalRequest, user: dict = Depends(get_current_user)):
    logger.info(f"Received signal for strategy {body.strategy_id}: {body.signal}")
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
            metadata={"portfolio_state": {"total_equity": Decimal("100000.0")}}
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
