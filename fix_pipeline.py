import re

with open('test_live_telemetry_pipeline.py', 'r') as f:
    c = f.read()

replacement = '''
async def trigger_execution_pipeline():
    from backend_app.backend.distributed_execution.orchestrator import ExecutionOrchestrator
    from backend_app.backend.exchange_telemetry import exchange_telemetry
    from decimal import Decimal
    
    bot_id = "test_bot_live_123"
    exchange_telemetry.register_exchange_bot(bot_id, "binance", "BTC/USDT")
    
    orchestrator = ExecutionOrchestrator()
    orchestrator.exchange_gateways["binance"] = "fake_gateway"
    
    tenant_id = "test_user_id_123"
    
    print("Executing standard BUY order...")
    await orchestrator.submit_execution_job(
        tenant_id=tenant_id,
        strategy_id="strat_test",
        bot_id=bot_id,
        signal_id="sig_123",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=Decimal("0.1"),
        price=Decimal("0")
    )
    
    print("Executing reject scenario...")
    # Trigger a reject
    await exchange_telemetry.on_order_rejected(
        exchange="binance",
        symbol="BTC/USDT",
        bot_id=bot_id,
        signal_id="sig_123",
        side="buy",
        size=Decimal("10000.0"),
        price=Decimal("0"),
        reason="Insufficient Margin",
        tenant_id=tenant_id
    )
'''
c = re.sub(r'async def trigger_execution_pipeline\(\):.*?async def main\(\):', replacement + '\nasync def main():', c, flags=re.DOTALL)

with open('test_live_telemetry_pipeline.py', 'w') as f:
    f.write(c)
