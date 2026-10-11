import psycopg2, pathlib
dsn = pathlib.Path("staging/pg_dsn.txt").read_text(encoding="utf-8").strip()
c = psycopg2.connect(dsn); cur = c.cursor()
cur.execute("""select column_name, data_type, is_nullable, column_default
               from information_schema.columns
               where table_schema='public' and table_name='profiles'
                 and is_nullable='NO' order by ordinal_position""")
print("profiles NOT NULL columns:")
for r in cur.fetchall(): print(f"  {r[0]:<24} {r[1]:<28} def={r[3]}")
c.close()
