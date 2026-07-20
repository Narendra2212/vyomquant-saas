import os
from dulwich.repo import Repo

repo = Repo('.')
index = repo.open_index()

files_to_restore = [
    b'aerora_quant_backend_updated_final1/core/security_vault.py',
    b'aerora_quant_backend_updated_final1/backend/telemetry_engine.py',
    b'aerora_quant_backend_updated_final1/backend/redis_manager.py',
    b'aerora_quant_backend_updated_final1/backend/startup_recovery.py',
    b'test_full_system_strict.py'
]

for name in index:
    if any(k in name for k in [b'security_vault', b'telemetry_engine', b'redis_manager', b'startup_recovery', b'test_full_system_strict']):
        try:
            sha = index[name].sha
            blob = repo.get_object(sha)
            # Make sure directory exists if we are writing relative to root
            os.makedirs(os.path.dirname(name), exist_ok=True)
            with open(name, 'wb') as f:
                f.write(blob.data)
            print(f"Restored {name.decode('utf-8')}")
        except Exception as e:
            print(f"Error restoring {name.decode('utf-8')}: {e}")
