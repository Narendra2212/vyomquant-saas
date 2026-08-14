import psycopg2

db_url = 'postgresql://postgres.wrkexcjqnidkdrayhlsi:Narendra%40221203221203@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres'
conn = psycopg2.connect(db_url, connect_timeout=10)
conn.autocommit = True
cur = conn.cursor()

print("Setting timeouts...")
cur.execute("SET statement_timeout = 10000;")
cur.execute("SET lock_timeout = 5000;")

print("Applying profiles.max_api_slots...")
cur.execute("ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS max_api_slots INTEGER DEFAULT 1 NOT NULL;")
print("Profiles max_api_slots applied!")

print("Applying execution_records.user_id...")
cur.execute("ALTER TABLE public.execution_records ADD COLUMN IF NOT EXISTS user_id UUID;")
print("execution_records user_id column added!")

print("Backfilling user_id...")
cur.execute("UPDATE public.execution_records SET user_id = tenant_id WHERE user_id IS NULL;")
print("Backfill complete!")

print("Creating index...")
cur.execute("CREATE INDEX IF NOT EXISTS idx_execution_records_user_created ON public.execution_records(user_id, created_at DESC);")
print("Index created!")

print("Updating RLS policy...")
cur.execute("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'execution_records') THEN
        DROP POLICY IF EXISTS "execution_records_user_access" ON public.execution_records;
        DROP POLICY IF EXISTS "execution_records_service" ON public.execution_records;
        CREATE POLICY "execution_records_user_access" ON public.execution_records FOR ALL TO authenticated USING (auth.uid() = user_id OR auth.uid() = tenant_id) WITH CHECK (auth.uid() = user_id OR auth.uid() = tenant_id);
        CREATE POLICY "execution_records_service" ON public.execution_records FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;
""")
print("RLS policy applied!")

print("ALL REMAINING DATABASE COLUMNS AND POLICIES APPLIED SUCCESSFULLY!")
cur.close()
conn.close()
