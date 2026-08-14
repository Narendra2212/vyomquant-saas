import json
import os
import re

# 1. Load Live DB Metadata
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

live_triggers = {}
for trg in live_meta['triggers']:
    schema, tbl, trg_name, event_manip, action_stmt, timing = trg
    if schema == 'public':
        if tbl not in live_triggers:
            live_triggers[tbl] = []
        live_triggers[tbl].append({
            'name': trg_name,
            'event': event_manip,
            'action': action_stmt,
            'timing': timing
        })

live_functions = {f[1]: {'schema': f[0], 'args': f[2], 'return_type': f[3], 'secdef': f[4]} for f in live_meta['functions']}

# 2. Detailed Column Usage Extraction from Backend Code
backend_table_columns_used = {}
supabase_table_pattern = re.compile(r'\.(?:table|from_?)\([\'"]([a-zA-Z0-9_]+)[\'"]')
select_pattern = re.compile(r'\.select\([\'"]([^\'"]+)[\'"]')
insert_pattern = re.compile(r'\.insert\(\{([^\}]+)\}\)')
update_pattern = re.compile(r'\.update\(\{([^\}]+)\}\)')
upsert_pattern = re.compile(r'\.upsert\(\{([^\}]+)\}\)')

for root, dirs, files in os.walk('backend_app'):
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file).replace('\\', '/')
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                for idx, line in enumerate(lines):
                    for match in supabase_table_pattern.finditer(line):
                        tbl = match.group(1).lower()
                        if tbl not in backend_table_columns_used:
                            backend_table_columns_used[tbl] = set()
                        # Extract select columns
                        for sel in select_pattern.finditer(line):
                            cols_str = sel.group(1)
                            for c in cols_str.split(','):
                                c = c.strip().split(':')[0].strip().split('(')[0].strip()
                                if c and c != '*' and c != 'count':
                                    backend_table_columns_used[tbl].add(c)

# 3. Analyze Column Mismatches
column_mismatches = []
for tbl, cols in backend_table_columns_used.items():
    if tbl in live_columns:
        for c in cols:
            if c not in live_columns[tbl] and '.' not in c and '(' not in c:
                column_mismatches.append({
                    'table': tbl,
                    'column': c,
                    'issue': 'Column referenced in code but missing from live table'
                })

print("Column Mismatches in existing tables:", column_mismatches)

# 4. RPC Functions Check
rpc_needed = ['create_referral_code_for_user', 'increment_ml_addon', 'process_referral_commission', 'reverse_referral_commission']
rpc_status = {}
for rpc in rpc_needed:
    rpc_status[rpc] = {
        'exists_in_live_db': rpc in live_functions,
        'details': live_functions.get(rpc, None)
    }

print("\nRPC Functions Status:")
print(json.dumps(rpc_status, indent=2))

# 5. Missing Tables Analysis
questdb_tables = {'executions', 'equity_curve', 'account_health', 'live_user_pnl', 'kline_1m', 'portfolio_allocation', 'system_metrics'}
missing_tables = []
with open('reports/deep_db_audit_analysis.json', 'r', encoding='utf-8') as f:
    deep_analysis = json.load(f)

for tbl, info in deep_analysis['backend_tables_actual'].items():
    if tbl not in public_tables and tbl not in questdb_tables:
        missing_tables.append({
            'table': tbl,
            'usages_count': len(info['usages']),
            'sample_files': list(set([u['file'] for u in info['usages']]))
        })

print(f"\nMissing Tables ({len(missing_tables)}):")
for mt in missing_tables:
    print(f" - {mt['table']:30} | Usages: {mt['usages_count']:2} | Files: {mt['sample_files']}")

# Save full results
report_data = {
    'public_tables': list(public_tables.keys()),
    'missing_tables': missing_tables,
    'column_mismatches': column_mismatches,
    'rpc_status': rpc_status,
    'live_columns': live_columns,
    'live_pks': live_pks,
    'live_fks': live_fks,
    'live_uniques': live_uniques,
    'live_indexes': live_indexes,
    'live_rls': live_rls,
    'live_policies': live_policies,
    'live_triggers': live_triggers,
    'live_functions': list(live_functions.keys())
}

with open('reports/final_forensic_data.json', 'w', encoding='utf-8') as out:
    json.dump(report_data, out, indent=2)

print("\nSuccessfully compiled final forensic data to reports/final_forensic_data.json")
