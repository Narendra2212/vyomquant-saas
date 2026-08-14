import json

with open('reports/deep_db_audit_analysis.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

backend_refs = data['backend_tables_actual']
public_tables = set(data['public_tables'])
questdb_tables = set(data['questdb_tables'])

print('=== DETAILED MISSING TABLES BREAKDOWN ===')
for tbl, info in sorted(backend_refs.items()):
    if tbl not in public_tables and tbl not in questdb_tables:
        usages = info['usages']
        print(f"\nTable: {tbl} (Total Usages: {len(usages)})")
        for u in usages[:5]:
            print(f"   [{u['file']}:{u['line']}] {u['snippet']}")
