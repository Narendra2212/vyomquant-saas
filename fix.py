with open('test_live_telemetry_pipeline.py', 'r') as f:
    c = f.read()
c = c.replace('AsyncMock', 'MagicMock')
c = c.replace('log_level="error"', 'log_level="info"')
with open('test_live_telemetry_pipeline.py', 'w') as f:
    f.write(c)
