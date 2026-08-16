import psycopg2
import sys
import os
import re

db_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")

def run_migration():
    migration_file = 'migrations/006_reconcile_production_database.sql'
    if not os.path.exists(migration_file):
        print(f"Error: {migration_file} not found")
        sys.exit(1)
        
    with open(migration_file, 'r', encoding='utf-8') as f:
        sql = f.read()

    # Remove top-level BEGIN and COMMIT for pgbouncer pooler compatibility
    sql_clean = re.sub(r'^\s*BEGIN\s*;\s*', '', sql, flags=re.MULTILINE | re.IGNORECASE)
    sql_clean = re.sub(r'^\s*COMMIT\s*;\s*', '', sql_clean, flags=re.MULTILINE | re.IGNORECASE)

    print(f"Connecting to production PostgreSQL database...")
    conn = psycopg2.connect(db_url, connect_timeout=15)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        print("Executing migration 006_reconcile_production_database.sql...")
        cur.execute(sql_clean)
        print("MIGRATION COMMITTED SUCCESSFULLY!")
    except Exception as e:
        print(f"MIGRATION FAILED: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    run_migration()
