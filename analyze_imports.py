import os
import ast
import json

packages_to_check = [
    'aiosmtplib', 'alembic', 'asyncpg', 'databases', 'elasticsearch', 
    'opentelemetry', 'psutil', 'jwt', 'requests', 'rq', 'scipy', 
    'slowapi', 'sqlalchemy', 'torch', 'psycopg2'
]

results = {pkg: [] for pkg in packages_to_check}

def find_imports():
    for root, dirs, files in os.walk('backend_app'):
        for file in files:
            if file.endswith('.py'):
                filepath = os.path.join(root, file)
                with open(filepath, 'r', encoding='utf-8') as f:
                    try:
                        tree = ast.parse(f.read(), filename=filepath)
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Import):
                                for name in node.names:
                                    pkg = name.name.split('.')[0]
                                    if pkg in packages_to_check and filepath not in results[pkg]:
                                        results[pkg].append(filepath)
                            elif isinstance(node, ast.ImportFrom):
                                if node.module and node.level == 0:
                                    pkg = node.module.split('.')[0]
                                    if pkg in packages_to_check and filepath not in results[pkg]:
                                        results[pkg].append(filepath)
                    except Exception:
                        pass

find_imports()

for pkg, files in results.items():
    print(f"\n--- {pkg} ---")
    if files:
        for f in files[:5]:
            print(f"  {f}")
        if len(files) > 5:
            print(f"  ... and {len(files)-5} more")
    else:
        print("  (No direct imports found)")
