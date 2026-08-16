import os
import sys
import psycopg2
import re

db_url = "postgresql://postgres.wrkexcjqnidkdrayhlsi:Narendra%40221203221203@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres"

def run_migration():
    migration_file = 'migrations/add_billing_currency_and_dag_hash.sql'
    if not os.path.exists(migration_file):
        print(f"Error: {migration_file} not found")
        sys.exit(1)
        
    with open(migration_file, 'r', encoding='utf-8') as f:
        sql = f.read()

    # Remove top-level BEGIN and COMMIT for pgbouncer pooler compatibility
    sql_clean = re.sub(r'^\s*BEGIN\s*;\s*', '', sql, flags=re.MULTILINE | re.IGNORECASE)
    sql_clean = re.sub(r'^\s*COMMIT\s*;\s*', '', sql_clean, flags=re.MULTILINE | re.IGNORECASE)

    print("Connecting to production PostgreSQL database...")
    conn = psycopg2.connect(db_url, connect_timeout=15)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        print(f"Executing migration {migration_file}...")
        cur.execute(sql_clean)
        print("MIGRATION EXECUTED SUCCESSFULLY!")
        
        # Verify columns exist
        print("\nVerifying added columns in production database:")
        cur.execute("""
            SELECT table_name, column_name, data_type, column_default
            FROM information_schema.columns
            WHERE table_name IN ('profiles', 'strategies')
              AND column_name IN (
                'preferred_currency', 'billing_currency',
                'dag_hash', 'dag_version', 'dag_schema_version', 'execution_order'
              )
            ORDER BY table_name, column_name;
        """)
        rows = cur.fetchall()
        for r in rows:
            print(f"  [COLUMN CONFIRMED] {r[0]}.{r[1]} ({r[2]}) DEFAULT {r[3]}")
            
    except Exception as e:
        print(f"MIGRATION FAILED: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

if __name__ == '__main__':
    run_migration()
