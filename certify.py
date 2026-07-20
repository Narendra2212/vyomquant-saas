import sys
import time
import os
sys.path.insert(0, '.')
sys.path.insert(0, 'backend_app')

from fastapi.testclient import TestClient
from backend_app.main import app
import json

client = TestClient(app)

print("--- RUNTIME CERTIFICATION SCRIPT ---")
results = {}

# 1. Browse
try:
    start = time.time()
    res = client.get("/api/library")
    dur = time.time() - start
    print(f"Browse: {res.status_code} in {dur:.4f}s")
    if res.status_code == 200:
        results["browse"] = "PASS"
    else:
        results["browse"] = f"FAIL - {res.status_code}: {res.text}"
except Exception as e:
    results["browse"] = f"FAIL - {str(e)}"

# Print results
print("\nResults:")
for k, v in results.items():
    print(f"{k}: {v}")

# Force exit to avoid hanging threads
os._exit(0)
