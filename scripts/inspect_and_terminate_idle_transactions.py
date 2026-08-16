import psycopg2

db_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")
conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor()

cur.execute("""
    SELECT pid, usename, state, now() - state_change as duration, query
    FROM pg_stat_activity 
    WHERE state = 'idle in transaction' AND pid != pg_backend_pid();
""")
rows = cur.fetchall()
print(f"Found {len(rows)} idle in transaction connections:")
for r in rows:
    pid = r[0]
    print(f"Terminating idle-in-transaction PID {pid} (duration: {r[3]})...")
    cur.execute(f"SELECT pg_terminate_backend({pid});")

cur.close()
conn.close()
