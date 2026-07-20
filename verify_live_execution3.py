import asyncio
import os
import sys
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import core.execution_engine
from core.models.execution_record import ExecutionRecordRepository

original_init = ExecutionRecordRepository.__init__
ExecutionRecordRepository.__init__ = lambda self, *args, **kwargs: None
ExecutionRecordRepository.check_idempotent_execution = MagicMock(return_value=("exec_123", "execute", None))
ExecutionRecordRepository.claim_execution = MagicMock(return_value=(True, None))
ExecutionRecordRepository.update_status = MagicMock()
ExecutionRecordRepository.record_attempt = MagicMock()

from core.execution_engine import ExecutionEngine
from backend.exchange_executor import CCXTExchangeExecutor

ExecutionEngine._validate_strategy_exists = lambda self, tenant_id, strategy_id: True
core.execution_engine.SessionLocal = MagicMock()

import core.global_safety
core.global_safety.get_global_kill_switch().is_active = AsyncMock(return_value=False)

async def main():
    print("==================================================")
    print("TEST 3: EXECUTION PATH")
    print("==================================================")
    
    mock_ccxt = AsyncMock()
    mock_ccxt.create_order.return_value = {
        'id': 'sim_order_999',
        'status': 'closed',
        'filled': 0.1,
        'remaining': 0.0,
        'average': 50000.0
    }
    
    executor = CCXTExchangeExecutor("binance", "fake_key", "fake_secret", sandbox=True)
    executor._exchange = mock_ccxt
    executor._connected = True
    
    portfolio_state = {"total_equity": 1000000.0}
    
    print("Verify: ExecutionEngine is not paper-only.")
    
    # 1. LIVE MODE
    engine_live = ExecutionEngine(fee_rate=0.001, slippage=0.0005, portfolio_state=portfolio_state, exchange_executor=executor)
    tenant_id = uuid.uuid4()
    
    res_live = await engine_live.execute_with_idempotency(tenant_id=tenant_id, strategy_id="123", symbol="BTC/USDT", side="buy", size=Decimal("0.1"), price=Decimal("0"), source="bot_runner")
    live_id = res_live.get('result', {}).get('trade_result', {}).get('order_id')
    is_live = bool(live_id and live_id != "paper") and "paper" not in str(live_id)
    
    # 2. PAPER MODE
    engine_paper = ExecutionEngine(fee_rate=0.001, slippage=0.0005, portfolio_state=portfolio_state, exchange_executor=None)
    res_paper = await engine_paper.execute_with_idempotency(tenant_id=tenant_id, strategy_id="123", symbol="BTC/USDT", side="buy", size=Decimal("0.1"), price=Decimal("50000"), source="bot_runner")
    trade_info_paper = res_paper.get('result', {})
    
    paper_id = trade_info_paper.get('trade_result', {}).get('order_id') if 'trade_result' in trade_info_paper else trade_info_paper.get('order_id')
    print("res_paper:", res_paper)
    is_paper = bool(paper_id and "paper" in str(paper_id))
    
    print(f"Verify: live mode routes through exchange adapter: {is_live}")
    print(f"Verify: paper mode uses simulated execution: {is_paper}")
    
    if is_live and is_paper:
        print("\nOutput: PASS")
    else:
        print("\nOutput: FAIL")

if __name__ == "__main__":
    asyncio.run(main())
