import os
import re
import json

backend_dir = 'backend_app'
frontend_dir = os.path.join('algo22-terminal', 'src')

backend_db_refs = []
frontend_db_refs = []
rpc_refs = []

# Pattern matchers
table_patterns = [
    re.compile(r'__tablename__\s*=\s*[\'"]([a-zA-Z0-9_]+)[\'"]'),
    re.compile(r'\.(?:table|from_?)\([\'"]([a-zA-Z0-9_]+)[\'"]\)'),
    re.compile(r'FROM\s+([a-zA-Z0-9_]+)', re.IGNORECASE),
    re.compile(r'INSERT\s+INTO\s+([a-zA-Z0-9_]+)', re.IGNORECASE),
    re.compile(r'UPDATE\s+([a-zA-Z0-9_]+)', re.IGNORECASE),
    re.compile(r'DELETE\s+FROM\s+([a-zA-Z0-9_]+)', re.IGNORECASE),
    re.compile(r'JOIN\s+([a-zA-Z0-9_]+)', re.IGNORECASE),
]

rpc_pattern = re.compile(r'\.rpc\([\'"]([a-zA-Z0-9_]+)[\'"]')

# Scan backend
for root, dirs, files in os.walk(backend_dir):
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file)
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                lines = content.splitlines()
                for line_idx, line in enumerate(lines):
                    for pat in table_patterns:
                        for match in pat.finditer(line):
                            tbl = match.group(1).lower()
                            if tbl not in ('select', 'where', 'values', 'set', 'table', 'dual', 'pg_tables', 'pg_class', 'pg_namespace', 'pg_indexes', 'information_schema', 'pg_policies', 'pg_trigger', 'pg_proc'):
                                backend_db_refs.append({
                                    'table': tbl,
                                    'file': filepath.replace('\\', '/'),
                                    'line': line_idx + 1,
                                    'snippet': line.strip()
                                })
                    for match in rpc_pattern.finditer(line):
                        rpc_refs.append({
                            'rpc': match.group(1),
                            'file': filepath.replace('\\', '/'),
                            'line': line_idx + 1,
                            'snippet': line.strip()
                        })

# Scan frontend
for root, dirs, files in os.walk(frontend_dir):
    for file in files:
        if file.endswith(('.js', '.jsx', '.ts', '.tsx')):
            filepath = os.path.join(root, file)
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                lines = content.splitlines()
                for line_idx, line in enumerate(lines):
                    for pat in table_patterns:
                        for match in pat.finditer(line):
                            tbl = match.group(1).lower()
                            if tbl not in ('select', 'where', 'values', 'set', 'table', 'react', 'lucide-react', 'axios'):
                                frontend_db_refs.append({
                                    'table': tbl,
                                    'file': filepath.replace('\\', '/'),
                                    'line': line_idx + 1,
                                    'snippet': line.strip()
                                })
                    for match in rpc_pattern.finditer(line):
                        rpc_refs.append({
                            'rpc': match.group(1),
                            'file': filepath.replace('\\', '/'),
                            'line': line_idx + 1,
                            'snippet': line.strip()
                        })

backend_tables = sorted(list(set([r['table'] for r in backend_db_refs])))
frontend_tables = sorted(list(set([r['table'] for r in frontend_db_refs])))
all_rpcs = sorted(list(set([r['rpc'] for r in rpc_refs])))

print(f'Backend Referenced Tables ({len(backend_tables)}):')
print(backend_tables)
print(f'\nFrontend Referenced Tables ({len(frontend_tables)}):')
print(frontend_tables)
print(f'\nReferenced RPCs ({len(all_rpcs)}):')
print(all_rpcs)

os.makedirs('reports', exist_ok=True)
with open('reports/code_db_refs.json', 'w', encoding='utf-8') as f:
    json.dump({
        'backend_refs': backend_db_refs,
        'frontend_refs': frontend_db_refs,
        'rpc_refs': rpc_refs,
        'backend_tables': backend_tables,
        'frontend_tables': frontend_tables,
        'all_rpcs': all_rpcs
    }, f, indent=2)
