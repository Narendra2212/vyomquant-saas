fpath = r'backend_app/backend/distributed_execution/execution_coordinator.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()

content = "from .worker_registry import DeterministicReassignmentCoordinator\n" + content

with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)
