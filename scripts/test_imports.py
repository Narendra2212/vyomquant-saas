import sys
import os

print("Testing imports...")
sys.path.insert(0, os.getcwd())

try:
    from backend_app.core.database import SessionLocal
    print("SessionLocal imported successfully.")
except Exception as e:
    print(f"SessionLocal error: {e}")

try:
    from backend_app.core.models.execution_record import ExecutionRecordRepository
    print("ExecutionRecordRepository imported successfully.")
except Exception as e:
    print(f"ExecutionRecordRepository error: {e}")
