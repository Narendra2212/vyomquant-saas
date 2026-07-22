import ast
import glob
import sys
import os

stdlib = sys.stdlib_module_names if hasattr(sys, 'stdlib_module_names') else set()
# fallback for older pythons if needed, but 3.12 has it

def get_imports(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        try:
            tree = ast.parse(f.read(), filename=filepath)
        except Exception:
            return set()
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                imports.add(name.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                imports.add(node.module.split('.')[0])
    return imports

all_imports = set()
for root, dirs, files in os.walk('backend_app'):
    for file in files:
        if file.endswith('.py'):
            path = os.path.join(root, file)
            all_imports.update(get_imports(path))

external = sorted([i for i in all_imports if i not in stdlib and i != 'backend_app' and i != 'core' and i != 'api'])
print(external)
