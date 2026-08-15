import sys
import traceback
sys.path.insert(0, '.')
try:
    from backend_app.core.execution_engine import ExecutionEngine
    print("OK: ExecutionEngine imported")
except Exception as e:
    traceback.print_exc()
