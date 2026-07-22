fpath = r'backend_app/backend/distributed_execution/execution_coordinator.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace("from .deterministic_reassignment_model import \\\n", "")
# It already has the worker_registry import because I added it in fix_final_coordinator.py

with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)
