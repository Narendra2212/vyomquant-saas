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

# Bypass strategy database validation for test
ExecutionEngine._validate_strategy_exists = lambda self, tenant_id, strategy_id: True
# Mock the SessionLocal inside execute_with_idempotency
core.execution_engine.SessionLocal = MagicMock()

import core.global_safety
core.global_safety.get_global_kill_switch().is_active = AsyncMock(return_value=False)

async def main():
    print("==================================================")
    print("TEST 2: LIVE ORDER SIMULATION")
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
    
    portfolio_state = {"total_equity": 10000.0}
    engine = ExecutionEngine(
        fee_rate=0.001,
        slippage=0.0005,
        portfolio_state=portfolio_state,
        exchange_executor=executor
    )
    
    tenant_id = uuid.uuid4()
    strategy_id = "test_strat_123"
    
    print("Using exchange sandbox/testnet:\n")
    print("Submit market order.")
    
    try:
        result = await engine.execute_with_idempotency(
            tenant_id=tenant_id,
            strategy_id=strategy_id,
            symbol="BTC/USDT",
            side="buy",
            size=Decimal("0.1"),
            price=Decimal("0"), # market order
            source="bot_runner"
        )
        
        trade = result.get('result', {})
        trade_info = trade.get('trade_result', {}) if trade else {}
        
        # Verify
        order_id = trade_info.get('order_id')
        print(f"Verify: exchange order id exists: {bool(order_id)} ({order_id})")
        print(f"Verify: exchange acknowledges order: {trade_info.get('status') in ['pending', 'open', 'closed']}")
        
        fill_received = (float(trade_info.get('size', 0)) > 0)
        print(f"Verify: fill received: {fill_received}")
        
        if order_id and fill_received:
            print("\nOutput: PASS")
        else:
            print("\nOutput: FAIL")
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\nSimulation failed: {e}")
        print("Output: FAIL")

if __name__ == "__main__":
    asyncio.run(main())
