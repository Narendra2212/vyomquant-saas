import os

filepath = 'backend_app/main.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove the redundant docstring
doc_to_remove = '"""\n\n  ALGO22  FASTAPI MASTER SERVER                                          \n  Wires every backend engine into a production-grade async HTTP + WS API  \n\n"""\n'
content = content.replace(doc_to_remove, '')

# 2. Extract sys.path block
sys_block = """import sys
import os


sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

#  SYSTEM FREEZE: Import safety config FIRST to block all execution
# This must be imported before any engine that could execute trades
from backend_app.core.safety_config import ExecutionFlags, SafetyMonitor
import os

env_mode = os.getenv("AERORA_MODE", "safe").lower()
if env_mode == "live":
    ExecutionFlags.enable_live_trading()
elif env_mode == "paper":
    ExecutionFlags.enable_paper_trading()
else:
    SafetyMonitor.assert_safe_mode()  # Crash if not explicitly valid safe state
"""
content = content.replace(sys_block, '')

# 3. Extract asgi block
asgi_block = """try:
    from asgi_correlation_id import CorrelationIdMiddleware
except ImportError:
    CorrelationIdMiddleware = None
"""
content = content.replace(asgi_block, '')

# Now, we find where the imports end.
# Look for "# Shared runtime service status"
insert_point = "# Shared runtime service status"
blocks_to_insert = sys_block + "\n" + asgi_block + "\n" + insert_point
content = content.replace(insert_point, blocks_to_insert)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
