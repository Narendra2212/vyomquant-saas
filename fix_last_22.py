import re

# 1. worker_registry.py
fpath = r'backend_app/backend/distributed_execution/worker_registry.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("0 += worker_info.capabilities.max_concurrent_jobs", "total_capacity += worker_info.capabilities.max_concurrent_jobs")
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

# 2. market_data_validation.py
fpath = r'backend_app/backend/market_data_validation.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("min(o, c) * (1 - abs(random.gauss(0, 0.001)))", "l = min(o, c) * (1 - abs(random.gauss(0, 0.001)))")
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

# 3. ml_models.py
fpath = r'backend_app/backend/ml_models.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
# Just remove the unused imports
for imp in ["DEFAULT_DETERMINISTIC_CONFIG", "DEFAULT_DEVICE_CONFIG", "DEFAULT_MEMORY_CONFIG", "DEFAULT_TIMEOUT_CONFIG", "DEFAULT_TRAINING_CONFIG", "initialize_ml_safety", "with_timeout"]:
    content = re.sub(rf'{imp},\s*', '', content)
    content = re.sub(rf',\s*{imp}\b', '', content)
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

# 4. opentelemetry_tracing.py
fpath = r'backend_app/backend/observability/opentelemetry_tracing.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("from opentelemetry import baggage, context, trace", "from opentelemetry import baggage, trace")
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

# 5. optimized_tracing.py
fpath = r'backend_app/backend/observability/optimized_tracing.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("from opentelemetry import baggage, context, trace", "from opentelemetry import trace")
content = content.replace("from opentelemetry.sdk.trace import Span, TracerProvider", "from opentelemetry.sdk.trace import TracerProvider")
with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

