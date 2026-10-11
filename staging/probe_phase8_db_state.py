"""Phase 8 baseline: the live state of the tables 8A and 8B are about.

Reads only catalogues and column metadata. Writes nothing. Run before designing either
migration, because a migration written against a model is a claim about the model and a
migration written against the live schema is a claim about the database.
"""

import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

dsn = (
    pathlib.Path(__file__).resolve().parent / "pg_dsn.txt"
).read_text(encoding="utf-8").strip()

conn = psycopg2.connect(dsn, connect_timeout=10)
cur = conn.cursor()

FEE = "fee_records"
REFERRAL = ("referral_commissions", "referral_wallets", "referral_payouts")


def section(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


section("1. fee_records — does it exist, and what are its ACTUAL column types?")
cur.execute(
    """
    SELECT column_name, data_type, udt_name, is_nullable, column_default,
           character_maximum_length
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = %s
    ORDER BY ordinal_position
    """,
    (FEE,),
)
rows = cur.fetchall()
if not rows:
    print("fee_records=ABSENT")
for name, dtype, udt, nullable, default, maxlen in rows:
    print(
        f"  {name:16} {dtype:28} udt={udt:22} null={nullable:3} "
        f"len={maxlen} default={default}"
    )

section("2. fee_records — RLS, policies, grants, constraints, indexes")
cur.execute(
    "SELECT relrowsecurity, relforcerowsecurity, relowner::regrole::text "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname='public' AND c.relname=%s",
    (FEE,),
)
row = cur.fetchone()
print(f"  rls_enabled={row[0] if row else 'N/A'} forced={row[1] if row else 'N/A'} "
      f"owner={row[2] if row else 'N/A'}")

cur.execute(
    "SELECT policyname, cmd, roles::text, qual, with_check FROM pg_policies "
    "WHERE schemaname='public' AND tablename=%s ORDER BY policyname",
    (FEE,),
)
pol = cur.fetchall()
print(f"  policies={len(pol)}")
for p in pol:
    print(f"    {p[0]} cmd={p[1]} roles={p[2]} using={p[3]} check={p[4]}")

cur.execute(
    "SELECT grantee, string_agg(privilege_type, ',' ORDER BY privilege_type) "
    "FROM information_schema.role_table_grants "
    "WHERE table_schema='public' AND table_name=%s GROUP BY grantee ORDER BY grantee",
    (FEE,),
)
for grantee, privs in cur.fetchall():
    print(f"  grant {grantee:16} {privs}")

cur.execute(
    "SELECT conname, contype, pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE conrelid = 'public.fee_records'::regclass ORDER BY conname"
)
for name, kind, definition in cur.fetchall():
    print(f"  constraint {name:42} [{kind}] {definition}")

cur.execute(
    "SELECT indexname, indexdef FROM pg_indexes "
    "WHERE schemaname='public' AND tablename=%s ORDER BY indexname",
    (FEE,),
)
for name, definition in cur.fetchall():
    print(f"  index {name:42} {definition}")

cur.execute("SELECT count(*) FROM public.fee_records")
print(f"  ROW_COUNT={cur.fetchone()[0]}")

section("3. fee_records — does the all-zero sentinel tenant actually occur?")
cur.execute(
    "SELECT count(*) FROM public.fee_records "
    "WHERE tenant_id = '00000000-0000-0000-0000-000000000000'::uuid"
)
print(f"  SENTINEL_TENANT_ROWS={cur.fetchone()[0]}")

section("4. The enum types the model declares — do they exist on the server?")
for enum_name in ("feetype", "feestatus"):
    cur.execute(
        "SELECT t.typname, string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) "
        "FROM pg_type t LEFT JOIN pg_enum e ON e.enumtypid = t.oid "
        "WHERE t.typname = %s GROUP BY t.typname",
        (enum_name,),
    )
    got = cur.fetchone()
    print(f"  {enum_name}={got[1] if got else 'ABSENT'}")

section("5. The precedent: execution_records and positions ownership + RLS")
for table in ("execution_records", "positions", "profiles"):
    cur.execute(
        "SELECT column_name, udt_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s "
        "AND column_name IN ('user_id','tenant_id','id','position_id','execution_id')"
        " ORDER BY column_name",
        (table,),
    )
    cols = ", ".join(f"{c}:{t}" for c, t in cur.fetchall())
    cur.execute(
        "SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=%s",
        (table,),
    )
    r = cur.fetchone()
    print(f"  {table:20} rls={r[0] if r else 'ABSENT'}  {cols}")

section("6. referral ledger — currency columns present?")
for table in REFERRAL:
    cur.execute(
        "SELECT column_name, udt_name, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (table,),
    )
    cols = cur.fetchall()
    if not cols:
        print(f"  {table}=ABSENT")
        continue
    names = [c[0] for c in cols]
    print(f"  {table}: {len(cols)} columns")
    print(f"    has_currency_column={'currency' in names}")
    print(f"    usd_named={[n for n in names if 'usd' in n.lower()]}")
    cur.execute(
        "SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=%s",
        (table,),
    )
    print(f"    rls={cur.fetchone()[0]}")
    cur.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = ('public.' || %s)::regclass AND contype = 'c' "
        "ORDER BY conname",
        (table,),
    )
    for name, definition in cur.fetchall():
        print(f"    check {name}: {definition}")

section("7. process_referral_commission — is migration 024 actually applied here?")
cur.execute(
    "SELECT p.oid::regprocedure::text FROM pg_proc p "
    "JOIN pg_namespace n ON n.oid = p.pronamespace "
    "WHERE n.nspname='public' AND p.proname = 'process_referral_commission'"
)
overloads = [r[0] for r in cur.fetchall()]
print(f"  OVERLOADS={len(overloads)}: {overloads}")
cur.execute(
    "SELECT prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
    "WHERE n.nspname='public' AND p.proname = 'process_referral_commission' LIMIT 1"
)
src = cur.fetchone()
if src:
    body = src[0]
    print(f"  HAS_ON_CONFLICT_PAYMENT_ID={'ON CONFLICT (payment_id)' in body}")
    print(f"  MENTIONS_CURRENCY={'currency' in body.lower()}")
cur.execute(
    "SELECT indexname FROM pg_indexes WHERE schemaname='public' "
    "AND indexname = 'uq_referral_commission_payment'"
)
print(f"  uq_referral_commission_payment={'PRESENT' if cur.fetchone() else 'ABSENT'}")

section("8. schema_migrations — which files this database has applied")
cur.execute(
    "SELECT count(*) FROM information_schema.tables "
    "WHERE table_schema='public' AND table_name='schema_migrations'"
)
if cur.fetchone()[0]:
    cur.execute("SELECT count(*) FROM public.schema_migrations")
    print(f"  APPLIED_FILES={cur.fetchone()[0]}")
    cur.execute(
        "SELECT filename FROM public.schema_migrations "
        "WHERE filename LIKE '%%02_%%' OR filename LIKE '%%referral%%' "
        "ORDER BY filename"
    )
    for (name,) in cur.fetchall():
        print(f"    {name}")
else:
    print("  schema_migrations=ABSENT")

section("9. Roles present (RLS tests need anon / authenticated / service_role)")
cur.execute(
    "SELECT rolname, rolbypassrls, rolcanlogin FROM pg_roles "
    "WHERE rolname IN ('anon','authenticated','service_role','postgres') "
    "ORDER BY rolname"
)
for name, bypass, login in cur.fetchall():
    print(f"  {name:16} bypassrls={bypass} canlogin={login}")

cur.close()
conn.close()
print("\nPROBE_COMPLETE=True")
