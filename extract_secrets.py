import json
with open(r'C:\Users\aa\.gemini\antigravity-ide\brain\b7168250-693d-43f6-9447-74f42ce4f1b1\scratch\audit_report.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

for sh in data['secret_hits']:
    real_hits = [h for h in sh['hits'] if h['key'] in ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'JWT_SECRET', 'MASTER_ENCRYPTION_KEYS', 'DATABASE_URL', 'AWS_ACCESS_KEY_ID', 'SUPABASE_ANON_KEY', 'SUPABASE_JWT_SECRET']]
    if real_hits:
        print(f"{sh['path']}:")
        for h in real_hits:
            print(f"  Line {h['line']}: {h['key']} = {h['masked']}")

for ef in data['env_files']:
    if ef['real'] > 0:
        print(f"\n{ef['path']}:")
        for k, v in ef.get('findings', {}).items():
            if isinstance(v, dict) and v.get('real'):
                print(f"  {k}: {v['masked']}")
