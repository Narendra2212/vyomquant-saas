import psycopg2, pathlib
dsn = pathlib.Path("staging/pg_dsn.txt").read_text(encoding="utf-8").strip()
c = psycopg2.connect(dsn); cur = c.cursor()
cur.execute("""select is_nullable from information_schema.columns
               where table_schema='public' and table_name='fee_records' and column_name='fee_pct'""")
print("LIVE fee_records.fee_pct is_nullable =", cur.fetchone()[0])
cur.execute("select col_description(to_regclass('public.fee_records')::oid, ordinal_position) from information_schema.columns where table_schema='public' and table_name='fee_records' and column_name='fee_pct'")
print("comment:", (cur.fetchone() or [None])[0])
c.close()
