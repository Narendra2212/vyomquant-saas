import json
import os
import re

# Load live DB metadata
with open('reports/live_db_metadata.json', 'r', encoding='utf-8') as f:
    live_meta = json.load(f)

public_tables = {t[1]: t for t in live_meta['tables'] if t[0] == 'public'}

# Check alembic_version in live DB
import psycopg2
db_url = 'postgresql://postgres.wrkexcjqnidkdrayhlsi:Narendra%40221203221203@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres'
conn = psycopg2.connect(db_url)
cur = conn.cursor()
cur.execute("SELECT version_num FROM alembic_version;")
alembic_versions = [r[0] for r in cur.fetchall()]
print(f"Current live alembic_version: {alembic_versions}")

# Check migrations in backend_app/alembic/versions
alembic_files = sorted([f for f in os.listdir('backend_app/alembic/versions') if f.endswith('.py') and not f.startswith('__')])
print("\nAlembic migration files in repo:")
for af in alembic_files:
    print(f" - {af}")

# Scan all SQL migration files in migrations/
sql_migration_dir = 'migrations'
sql_files = sorted([f for f in os.listdir(sql_migration_dir) if f.endswith('.sql')])
print(f"\nSQL files in migrations/:")
for sf in sql_files:
    print(f" - {sf}")

# Let's inspect which tables are defined in each SQL migration file
migration_tables = {}
for sf in sql_files:
    path = os.path.join(sql_migration_dir, sf)
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
        tables_created = re.findall(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z0-9_\.]+)', content, re.IGNORECASE)
        migration_tables[sf] = [t.replace('public.', '') for t in tables_created]

print("\nTables created in migrations/*.sql:")
for sf, tbls in migration_tables.items():
    print(f" {sf}: {tbls}")
