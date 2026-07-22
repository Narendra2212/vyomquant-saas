import os
import ast

def get_imports(filepath):
    imports = set()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=filepath)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        imports.add(name.name.split('.')[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.level == 0:
                        imports.add(node.module.split('.')[0])
                    elif node.level > 0:
                        # Handle relative imports inside backend_app
                        pass
    except Exception:
        pass
    return imports

def get_internal_imports(filepath):
    internal = set()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=filepath)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for name in node.names:
                        parts = name.name.split('.')
                        if parts[0] == 'backend_app':
                            internal.add(os.path.join(*parts) + '.py')
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith('backend_app'):
                        parts = node.module.split('.')
                        internal.add(os.path.join(*parts) + '.py')
                        internal.add(os.path.join(*parts, '__init__.py'))
    except Exception:
        pass
    return internal

visited = set()
to_visit = ['backend_app/main.py', 'backend_app/worker.py']

while to_visit:
    current = to_visit.pop(0)
    # We replace \ with / for consistency
    current = current.replace('\\', '/')
    if current in visited:
        continue
    visited.add(current)
    
    if os.path.exists(current):
        internals = get_internal_imports(current)
        for i in internals:
            if i not in visited:
                to_visit.append(i)
    else:
        # Check if it's a directory (package)
        dir_path = current.replace('.py', '')
        if os.path.exists(dir_path) and os.path.isdir(dir_path):
            init_file = os.path.join(dir_path, '__init__.py')
            if os.path.exists(init_file) and init_file not in visited:
                to_visit.append(init_file)

print(f"Total internal files parsed in startup tree: {len(visited)}")

external_deps = set()
for file in visited:
    if os.path.exists(file):
        imports = get_imports(file)
        external_deps.update(imports)

packages_to_check = [
    'aiosmtplib', 'alembic', 'asyncpg', 'databases', 'elasticsearch', 
    'opentelemetry', 'psutil', 'jwt', 'requests', 'rq', 'scipy', 
    'slowapi', 'sqlalchemy', 'torch', 'psycopg2'
]

print("\n--- Dependencies reachable from startup (main.py + worker.py) ---")
for pkg in packages_to_check:
    if pkg in external_deps:
        print(f"REQUIRED: {pkg}")
    else:
        print(f"EXCLUDED: {pkg}")
