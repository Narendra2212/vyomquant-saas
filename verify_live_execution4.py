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

# Mock check_idempotent_execution to simulate the first time and the second time
mock_check = MagicMock(side_effect=[
    # First time: it's a new execution, action = "execute"
    ("exec_123", "execute", None),
    # Second time: same idempotency key hits, action = "skip_return_result"
    ("exec_123", "skip_return_result", {'order_id': 'sim_order_999'})
])
ExecutionRecordRepository.check_idempotent_execution = mock_check
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
    print("TEST 4: IDEMPOTENCY")
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
    
    print("Verify: ExecutionEngine.execute_with_idempotency cannot submit the same order twice if signal is replayed within time window.")
    
    engine = ExecutionEngine(fee_rate=0.001, slippage=0.0005, portfolio_state=portfolio_state, exchange_executor=executor)
    tenant_id = uuid.uuid4()
    
    # 1st call
    res1 = await engine.execute_with_idempotency(tenant_id=tenant_id, strategy_id="123", symbol="BTC/USDT", side="buy", size=Decimal("0.1"), price=Decimal("0"), source="bot_runner")
    
    # 2nd call (simulate replay)
    res2 = await engine.execute_with_idempotency(tenant_id=tenant_id, strategy_id="123", symbol="BTC/USDT", side="buy", size=Decimal("0.1"), price=Decimal("0"), source="bot_runner")
    
    print(f"\nFirst call status: {res1.get('status')}")
    print(f"Second call status: {res2.get('status')}")
    print(f"Exchange create_order call count: {mock_ccxt.create_order.call_count}")
    
    if res1.get('status') == 'completed' and res2.get('status') == 'skipped_completed' and mock_ccxt.create_order.call_count == 1:
        print("\nOutput: PASS")
    else:
        print("\nOutput: FAIL")

if __name__ == "__main__":
    asyncio.run(main())
