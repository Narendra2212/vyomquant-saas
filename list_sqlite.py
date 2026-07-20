import sqlite3
import os

db_path = os.path.join("aerora_quant_backend_updated_final1", "algo22.db")

print(f"Checking SQLite database at: {os.path.abspath(db_path)}")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print("Tables found:")
    for t in tables:
        print(f"- {t}")
        try:
            print("  Columns:", [col[1] for col in conn.execute(f"PRAGMA table_info({t})")])
        except Exception as e:
            print(f"  Error reading columns: {e}")
else:
    print("Database file does not exist.")
