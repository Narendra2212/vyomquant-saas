with open('test_live_telemetry_pipeline.py', 'r') as f:
    c = f.read()
c = c.replace('"test_tenant"', '"test_user_id_123"')
with open('test_live_telemetry_pipeline.py', 'w') as f:
    f.write(c)
