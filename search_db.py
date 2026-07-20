import sqlite3
import os

dbs = [
    "algo22.db",
    "aerora_quant_backend_updated_final1/algo22.db",
    "aerora_quant_backend_updated_final1/aerora_fallback.db"
]

for db in dbs:
    if not os.path.exists(db):
        print(f"Skipping {db} - not found")
        continue
    print(f"Checking {db}...")
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [t[0] for t in cur.fetchall()]
        for table in tables:
            try:
                cur.execute(f"SELECT * FROM {table}")
                rows = cur.fetchall()
                for row in rows:
                    row_str = str(row)
                    if "NH-" in row_str or "NC-" in row_str:
                        print(f"  Found in {table}: {row_str}")
            except Exception as e:
                print(f"  Error reading table {table}: {e}")
        conn.close()
    except Exception as e:
        print(f"  Error opening db {db}: {e}")
