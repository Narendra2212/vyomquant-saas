import re

with open('test_live_telemetry_pipeline.py', 'r') as f:
    c = f.read()

replacement = '''
    await exchange_telemetry.on_order_rejected(
        exchange="binance",
        symbol="BTC/USDT",
        order_id="rej_ord_123",
        bot_id=bot_id,
        signal_id="sig_123",
        reason="Insufficient Margin",
        error_code="INSUFFICIENT_FUNDS",
        tenant_id=tenant_id
    )
'''

c = re.sub(r'await exchange_telemetry\.on_order_rejected\(.*?tenant_id=tenant_id\n    \)', replacement.strip(), c, flags=re.DOTALL)

with open('test_live_telemetry_pipeline.py', 'w') as f:
    f.write(c)
