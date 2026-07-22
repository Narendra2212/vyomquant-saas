# Define DeterministicReassignmentCoordinator in worker_registry.py
fpath = r'backend_app/backend/distributed_execution/worker_registry.py'
with open(fpath, 'r', encoding='utf-8') as f:
    content = f.read()

# Add it at the bottom
if 'class DeterministicReassignmentCoordinator' not in content:
    content += '\n\nclass DeterministicReassignmentCoordinator:\n    """Coordinates deterministic reassignment of orphaned workflows."""\n    pass\n'

with open(fpath, 'w', encoding='utf-8') as f:
    f.write(content)

# And add the import to orphan_recovery_manager.py
fpath2 = r'backend_app/backend/distributed_execution/orphan_recovery_manager.py'
with open(fpath2, 'r', encoding='utf-8') as f:
    content2 = f.read()

content2 = content2.replace('from .worker_registry import WorkerRegistry', 'from .worker_registry import WorkerRegistry, DeterministicReassignmentCoordinator')
with open(fpath2, 'w', encoding='utf-8') as f:
    f.write(content2)

# And add the import to execution_coordinator.py
fpath3 = r'backend_app/backend/distributed_execution/execution_coordinator.py'
with open(fpath3, 'r', encoding='utf-8') as f:
    content3 = f.read()

content3 = content3.replace('    DeterministicReassignmentCoordinator\n', '')
content3 = content3.replace('from .worker_registry import WorkerRegistry', 'from .worker_registry import WorkerRegistry, DeterministicReassignmentCoordinator')
with open(fpath3, 'w', encoding='utf-8') as f:
    f.write(content3)
