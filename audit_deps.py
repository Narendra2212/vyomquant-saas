import ast
import os
import re

def get_imports(directory):
    imports = set()
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                filepath = os.path.join(root, file)
                with open(filepath, 'r', encoding='utf-8') as f:
                    try:
                        tree = ast.parse(f.read(), filename=filepath)
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Import):
                                for name in node.names:
                                    imports.add(name.name.split('.')[0])
                            elif isinstance(node, ast.ImportFrom):
                                if node.module and node.level == 0:
                                    imports.add(node.module.split('.')[0])
                    except Exception:
                        pass
    return imports

def get_requirements(filepath):
    if not os.path.exists(filepath):
        return set()
    reqs = set()
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.split('#')[0].strip()
            if line:
                # Get package name, e.g. from `uvicorn[standard]==0.30.6`
                match = re.match(r'^([a-zA-Z0-9_\-]+)', line)
                if match:
                    reqs.add(match.group(1).lower().replace('-', '_'))
    return reqs

stdlib = set(__import__("sys").stdlib_module_names) if hasattr(__import__("sys"), "stdlib_module_names") else set()
imports = get_imports('backend_app')
external_imports = {i for i in imports if i not in stdlib and i not in ['backend_app', 'core', 'api', 'backend', 'mds', 'api_ws', 'routers']}

root_reqs = get_requirements('requirements.txt')
backend_reqs = get_requirements('backend_app/requirements.txt')

print("=== External Imports ===")
print(sorted(external_imports))
print("\n=== Root requirements.txt ===")
print(sorted(root_reqs))
print("\n=== Backend requirements.txt ===")
print(sorted(backend_reqs))

# Map some common import names to pip package names
pkg_map = {
    'dotenv': 'python_dotenv',
    'jwt': 'pyjwt',
    'passlib': 'passlib',
    'yaml': 'pyyaml',
    'jose': 'python_jose',
    'dateutil': 'python_dateutil',
    'sklearn': 'scikit_learn',
    'cv2': 'opencv_python',
    'PIL': 'pillow',
    'telebot': 'pytelegrambotapi'
}

missing_in_root = []
for imp in external_imports:
    pkg = pkg_map.get(imp, imp).lower().replace('-', '_')
    # Special cases
    if pkg == 'opentelemetry':
        if not any(r.startswith('opentelemetry') for r in root_reqs):
            missing_in_root.append(pkg)
    elif pkg == 'sentry_sdk':
        if 'sentry_sdk' not in root_reqs:
            missing_in_root.append(pkg)
    else:
        if pkg not in root_reqs:
            missing_in_root.append(pkg)

print("\n=== Missing in Root ===")
print(sorted(missing_in_root))

missing_in_backend = []
for imp in external_imports:
    pkg = pkg_map.get(imp, imp).lower().replace('-', '_')
    if pkg == 'opentelemetry':
        if not any(r.startswith('opentelemetry') for r in backend_reqs):
            missing_in_backend.append(pkg)
    elif pkg == 'sentry_sdk':
        if 'sentry_sdk' not in backend_reqs:
            missing_in_backend.append(pkg)
    else:
        if pkg not in backend_reqs:
            missing_in_backend.append(pkg)

print("\n=== Missing in Backend ===")
print(sorted(missing_in_backend))

