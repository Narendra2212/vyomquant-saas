import re

# 1. market_data_validation.py
fpath = r'backend_app/backend/market_data_validation.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("min(o, new_close) - synthetic_range * random.random()", "low_val = min(o, new_close) - synthetic_range * random.random()")
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

# 2. heartbeat_manager.py F601
fpath = r'backend_app/backend/distributed_execution/heartbeat_manager.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
# Replace duplicate dictionary keys
content = content.replace('"processing_rate": metrics.throughput', '"throughput": metrics.throughput')
content = content.replace('"processing_rate": heartbeat.metrics.throughput', '"throughput": heartbeat.metrics.throughput')
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)
