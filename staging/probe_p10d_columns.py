import psycopg2, pathlib
dsn = pathlib.Path("staging/pg_dsn.txt").read_text(encoding="utf-8").strip()
c = psycopg2.connect(dsn); cur = c.cursor()
for t in ("referral_payouts","fee_records","referral_obligations"):
    cur.execute("""select column_name, data_type, is_nullable, column_default
                   from information_schema.columns
                   where table_schema='public' and table_name=%s order by ordinal_position""",(t,))
    rows = cur.fetchall()
    print("="*72); print("TABLE", t, "-- ABSENT" if not rows else "")
    for r in rows: print(f"  {r[0]:<28} {r[1]:<28} null={r[2]:<4} default={r[3]}")
    cur.execute("select conname, pg_get_constraintdef(oid) from pg_constraint where conrelid=to_regclass(%s)",(f"public.{t}",))
    for r in cur.fetchall(): print(f"  CONSTRAINT {r[0]}: {r[1]}")
    cur.execute(f'select count(*) from public."{t}"') if rows else None
    if rows: print("  ROWCOUNT:", cur.fetchone()[0])
c.close()
