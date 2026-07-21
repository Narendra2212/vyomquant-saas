import os, hashlib
from pathlib import Path
from datetime import datetime

parent = Path(r'C:\aerora_quant_backend_updated_final1')
repo = Path(r'C:\aerora_quant_backend_updated_final1\vyomquant-saas')

key_files = [
    'backend_app/core/auth_middleware.py',
    'startup.sh',
    'ecs-task-definition-full.json',
    'ecs-secrets-manager-setup.sh',
    'ecs-execution-role-policy.json',
    'ECS_DEPLOYMENT_GUIDE.md',
    'ECS_PRODUCTION_DEPLOYMENT_REPORT.md',
    'Dockerfile',
    'requirements.txt',
    'Procfile',
    'healthcheck.sh',
]

IGNORE = {
    '.git', '.venv', 'node_modules', '__pycache__', 'dist', 'build',
    'vyomquant-saas', '.pytest_cache', '.ruff_cache', '.codex_verify_venv',
    'python_portable', '.windsurf'
}

def sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        return 'ERROR:' + str(e)[:30]

def get_mtime(p):
    try:
        return datetime.fromtimestamp(os.path.getmtime(p)).strftime('%Y-%m-%d %H:%M:%S')
    except:
        return 'N/A'

print('=== KEY FILES COMPARISON (Phase 1) ===')
print()

identical = []
differ = []
only_parent = []
only_repo = []

for f in key_files:
    p_path = parent / f
    r_path = repo / f
    p_exists = p_path.exists()
    r_exists = r_path.exists()

    if p_exists and r_exists:
        p_hash = sha256(p_path)
        r_hash = sha256(r_path)
        p_size = p_path.stat().st_size
        r_size = r_path.stat().st_size
        if p_hash == r_hash:
            status = 'IDENTICAL'
            identical.append(f)
        else:
            status = 'DIFFER'
            differ.append(f)
        print('[' + status + '] ' + f)
        print('  Parent: size=' + str(p_size) + ', mtime=' + get_mtime(p_path) + ', sha256=' + p_hash[:32] + '...')
        print('  Repo:   size=' + str(r_size) + ', mtime=' + get_mtime(r_path) + ', sha256=' + r_hash[:32] + '...')
    elif p_exists and not r_exists:
        p_size = p_path.stat().st_size
        print('[ONLY_IN_PARENT] ' + f + '  size=' + str(p_size) + ', mtime=' + get_mtime(p_path))
        only_parent.append(f)
    elif not p_exists and r_exists:
        r_size = r_path.stat().st_size
        print('[ONLY_IN_REPO] ' + f + '  size=' + str(r_size) + ', mtime=' + get_mtime(r_path))
        only_repo.append(f)
    else:
        print('[MISSING_BOTH] ' + f)
    print()

print()
print('=== FULL PROJECT SCAN (files in parent not in repo) ===')
print()

def walk_parent():
    for root, dirs, files in os.walk(parent):
        # Filter ignored dirs
        dirs[:] = [d for d in dirs if d not in IGNORE and not d.startswith('.')]
        for fname in files:
            full_path = Path(root) / fname
            rel_path = full_path.relative_to(parent)
            yield rel_path, full_path

only_in_parent_count = 0
for rel, full in walk_parent():
    rel_str = str(rel).replace('\\', '/')
    r_path = repo / rel
    if not r_path.exists():
        # Skip binary/large files
        try:
            size = full.stat().st_size
            if size < 10 * 1024 * 1024:  # skip > 10MB
                only_in_parent_count += 1
                if only_in_parent_count <= 50:
                    print('  ONLY_IN_PARENT: ' + rel_str + ' (' + str(size) + ' bytes)')
        except:
            pass

if only_in_parent_count > 50:
    print('  ... and ' + str(only_in_parent_count - 50) + ' more files only in parent')
print()
print('Total files only in parent (not in repo): ' + str(only_in_parent_count))

print()
print('=== SUMMARY ===')
print('Identical: ' + str(len(identical)))
print('Differ: ' + str(len(differ)) + ' -> ' + str(differ))
print('Only in parent: ' + str(len(only_parent)) + ' -> ' + str(only_parent))
print('Only in repo: ' + str(len(only_repo)) + ' -> ' + str(only_repo))
