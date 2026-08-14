import json
import os
import re

# Load live DB metadata
with open('reports/live_db_metadata.json', 'r', encoding='utf-8') as f:
    live_meta = json.load(f)

public_tables = {t[1]: t for t in live_meta['tables'] if t[0] == 'public'}
columns_by_table = {}
for col in live_meta['columns']:
    schema, tbl, col_name, dtype, udt, nullable, default = col
    if schema == 'public':
        if tbl not in columns_by_table:
            columns_by_table[tbl] = {}
        columns_by_table[tbl][col_name] = {
            'data_type': dtype,
            'udt_name': udt,
            'is_nullable': nullable,
            'column_default': default
        }

constraints_by_table = {}
for con in live_meta['constraints']:
    schema, tbl, con_name, con_type, col, f_schema, f_tbl, f_col = con
    if schema == 'public':
        if tbl not in constraints_by_table:
            constraints_by_table[tbl] = []
        constraints_by_table[tbl].append({
            'name': con_name,
            'type': con_type,
            'column': col,
            'foreign_table': f_tbl,
            'foreign_column': f_col
        })

indexes_by_table = {}
for idx in live_meta['indexes']:
    schema, tbl, idx_name, idx_def = idx
    if schema == 'public':
        if tbl not in indexes_by_table:
            indexes_by_table[tbl] = []
        indexes_by_table[tbl].append({
            'name': idx_name,
            'def': idx_def
        })

rls_by_table = {}
for rls in live_meta['rls_status']:
    schema, tbl, enabled = rls
    if schema == 'public':
        rls_by_table[tbl] = enabled

policies_by_table = {}
for pol in live_meta['policies']:
    schema, tbl, pol_name, permissive, roles, cmd, qual, with_check = pol
    if schema == 'public':
        if tbl not in policies_by_table:
            policies_by_table[tbl] = []
        policies_by_table[tbl].append({
            'name': pol_name,
            'permissive': permissive,
            'roles': roles,
            'cmd': cmd,
            'qual': qual,
            'with_check': with_check
        })

triggers_by_table = {}
for trg in live_meta['triggers']:
    schema, tbl, trg_name, event_manip, action_stmt, timing = trg
    if schema == 'public':
        if tbl not in triggers_by_table:
            triggers_by_table[tbl] = []
        triggers_by_table[tbl].append({
            'name': trg_name,
            'event': event_manip,
            'action': action_stmt,
            'timing': timing
        })

functions = {f[1]: f for f in live_meta['functions']}

# Detailed analysis of backend files for exact table usage
backend_tables_actual = {}
# Known QuestDB tables
questdb_tables = {'executions', 'equity_curve', 'account_health', 'live_user_pnl', 'kline_1m', 'portfolio_allocation', 'system_metrics'}

# Scan backend files specifically for supabase.table / supabase.from_ and SQLAlchemy models
supabase_table_pattern = re.compile(r'\.(?:table|from_?)\([\'"]([a-zA-Z0-9_]+)[\'"]')
sa_model_pattern = re.compile(r'__tablename__\s*=\s*[\'"]([a-zA-Z0-9_]+)[\'"]')

for root, dirs, files in os.walk('backend_app'):
    for file in files:
        if file.endswith('.py'):
            filepath = os.path.join(root, file).replace('\\', '/')
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                for idx, line in enumerate(lines):
                    for match in supabase_table_pattern.finditer(line):
                        tbl = match.group(1).lower()
                        if tbl not in backend_tables_actual:
                            backend_tables_actual[tbl] = {'type': 'supabase', 'usages': []}
                        backend_tables_actual[tbl]['usages'].append({
                            'file': filepath,
                            'line': idx + 1,
                            'snippet': line.strip()
                        })
                    for match in sa_model_pattern.finditer(line):
                        tbl = match.group(1).lower()
                        if tbl not in backend_tables_actual:
                            backend_tables_actual[tbl] = {'type': 'sqlalchemy', 'usages': []}
                        backend_tables_actual[tbl]['usages'].append({
                            'file': filepath,
                            'line': idx + 1,
                            'snippet': line.strip()
                        })

# Scan frontend files for supabase.from / .table
frontend_tables_actual = {}
for root, dirs, files in os.walk(os.path.join('algo22-terminal', 'src')):
    for file in files:
        if file.endswith(('.js', '.jsx', '.ts', '.tsx')):
            filepath = os.path.join(root, file).replace('\\', '/')
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                for idx, line in enumerate(lines):
                    for match in supabase_table_pattern.finditer(line):
                        tbl = match.group(1).lower()
                        if tbl not in frontend_tables_actual:
                            frontend_tables_actual[tbl] = []
                        frontend_tables_actual[tbl].append({
                            'file': filepath,
                            'line': idx + 1,
                            'snippet': line.strip()
                        })

print('=== ACTUAL BACKEND TABLES IN CODE ===')
for tbl in sorted(backend_tables_actual.keys()):
    is_qdb = tbl in questdb_tables
    in_live_pg = tbl in public_tables
    print(f" - {tbl:30} | In Live PG: {str(in_live_pg):5} | QuestDB: {str(is_qdb):5} | Usages: {len(backend_tables_actual[tbl]['usages'])}")

print('\n=== ACTUAL FRONTEND TABLES IN CODE ===')
for tbl in sorted(frontend_tables_actual.keys()):
    in_live_pg = tbl in public_tables
    print(f" - {tbl:30} | In Live PG: {str(in_live_pg):5} | Usages: {len(frontend_tables_actual[tbl])}")

print('\n=== LIVE PUBLIC POSTGRES TABLES ===')
for tbl in sorted(public_tables.keys()):
    in_code = tbl in backend_tables_actual or tbl in frontend_tables_actual
    has_rls = rls_by_table.get(tbl, False)
    num_cols = len(columns_by_table.get(tbl, {}))
    num_pols = len(policies_by_table.get(tbl, []))
    num_idx = len(indexes_by_table.get(tbl, []))
    print(f" - {tbl:30} | In Code: {str(in_code):5} | RLS: {str(has_rls):5} | Policies: {num_pols:2} | Cols: {num_cols:2} | Indexes: {num_idx:2}")

# Save complete audit analysis
analysis = {
    'backend_tables_actual': backend_tables_actual,
    'frontend_tables_actual': frontend_tables_actual,
    'public_tables': list(public_tables.keys()),
    'columns_by_table': columns_by_table,
    'constraints_by_table': constraints_by_table,
    'indexes_by_table': indexes_by_table,
    'rls_by_table': rls_by_table,
    'policies_by_table': policies_by_table,
    'triggers_by_table': triggers_by_table,
    'functions': functions,
    'questdb_tables': list(questdb_tables)
}

with open('reports/deep_db_audit_analysis.json', 'w', encoding='utf-8') as f:
    json.dump(analysis, f, indent=2)
