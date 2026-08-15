import sys
import os
sys.path.insert(0, os.getcwd())

for mod in [
    "backend_app",
    "backend_app.core",
    "backend_app.core.database",
    "backend_app.core.models.execution_record",
    "backend_app.core.execution_engine",
    "backend_app.backend.exchange_executor"
]:
    try:
        __import__(mod)
        print(f"  {mod}: OK")
    except Exception as e:
        print(f"  {mod}: FAILED -> {e}")
