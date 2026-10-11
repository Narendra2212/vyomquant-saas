import psycopg2, pathlib
dsn = pathlib.Path("staging/pg_dsn.txt").read_text(encoding="utf-8").strip()
c = psycopg2.connect(dsn); cur = c.cursor()
cur.execute("""select column_name, data_type, is_nullable, column_default
               from information_schema.columns
               where table_schema='public' and table_name='execution_records'
               order by ordinal_position""")
for r in cur.fetchall(): print(f"  {r[0]:<24} {r[1]:<30} null={r[2]:<4} def={r[3]}")
print("--- enums ---")
cur.execute("""select t.typname, string_agg(e.enumlabel, ',' order by e.enumsortorder)
               from pg_type t join pg_enum e on e.enumtypid=t.oid
               where t.typname in ('feetype','feestatus','executionstatus') group by t.typname""")
for r in cur.fetchall(): print(" ", r[0], "=", r[1])
c.close()
