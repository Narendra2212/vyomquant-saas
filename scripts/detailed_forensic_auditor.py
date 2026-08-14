import os
import re
import json

# Load live DB metadata
with open('reports/live_db_metadata.json', 'r', encoding='utf-8') as f:
    live_meta = json.load(f)

public_tables = {t[1]: t for t in live_meta['tables'] if t[0] == 'public'}
live_columns = {}
for col in live_meta['columns']:
    schema, tbl, col_name, dtype, udt, nullable, default = col
    if schema == 'public':
        if tbl not in live_columns:
            live_columns[tbl] = {}
        live_columns[tbl][col_name] = {
            'type': dtype,
            'udt': udt,
            'nullable': nullable,
            'default': default
        }

live_pks = {}
live_fks = {}
live_uniques = {}
for con in live_meta['constraints']:
    schema, tbl, con_name, con_type, col, f_schema, f_tbl, f_col = con
    if schema == 'public':
        if con_type == 'PRIMARY KEY':
            if tbl not in live_pks:
                live_pks[tbl] = []
            live_pks[tbl].append(col)
        elif con_type == 'FOREIGN KEY':
            if tbl not in live_fks:
                live_fks[tbl] = []
            live_fks[tbl].append({'column': col, 'ref_table': f_tbl, 'ref_column': f_col})
        elif con_type == 'UNIQUE':
            if tbl not in live_uniques:
                live_uniques[tbl] = []
            live_uniques[tbl].append(col)

live_indexes = {}
for idx in live_meta['indexes']:
    schema, tbl, idx_name, idx_def = idx
    if schema == 'public':
        if tbl not in live_indexes:
            live_indexes[tbl] = []
        live_indexes[tbl].append({'name': idx_name, 'def': idx_def})

live_rls = {r[1]: r[2] for r in live_meta['rls_status'] if r[0] == 'public'}
live_policies = {}
for pol in live_meta['policies']:
    schema, tbl, p_name, perm, roles, cmd, qual, wcheck = pol
    if schema == 'public':
        if tbl not in live_policies:
            live_policies[tbl] = []
        live_policies[tbl].append({
            'name': p_name,
            'cmd': cmd,
            'roles': roles,
            'qual': qual,
            'with_check': wcheck
        })

live_functions = {f[1]: {'schema': f[0], 'args': f[2], 'return_type': f[3], 'secdef': f[4]} for f in live_meta['functions']}

# Detailed Route & Backend Scan
routes = []
with open('reports/routes_to_db_map.json', 'r', encoding='utf-8') as f:
    routes = json.load(f)

# Frontend Page to API Scan
frontend_pages = {}
frontend_dir = os.path.join('algo22-terminal', 'src', 'pages')
for f in sorted(os.listdir(frontend_dir)):
    if f.endswith(('.js', '.jsx', '.tsx')):
        page_name = os.path.splitext(f)[0]
        filepath = os.path.join(frontend_dir, f).replace('\\', '/')
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as page_file:
            content = page_file.read()
            # find API calls
            api_calls = re.findall(r'(?:apiClient|axios|fetch)\.(?:get|post|put|delete|patch)\([\'"]([^\'"]+)[\'"]', content)
            api_calls += re.findall(r'[\'"](/api/[^\'"]+)[\'"]', content)
            supabase_calls = re.findall(r'supabase\.(?:table|from_?)\([\'"]([a-zA-Z0-9_]+)[\'"]\)', content)
            frontend_pages[page_name] = {
                'file': filepath,
                'api_calls': sorted(list(set(api_calls))),
                'supabase_calls': sorted(list(set(supabase_calls)))
            }

print(f"Scanned {len(frontend_pages)} Frontend Pages.")

# Save findings
with open('reports/frontend_page_map.json', 'w', encoding='utf-8') as out:
    json.dump(frontend_pages, out, indent=2)

print("Saved reports/frontend_page_map.json")
