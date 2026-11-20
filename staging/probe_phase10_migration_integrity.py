"""Stage 7 — migration & schema integrity, read-only.

Verifies against the local verification database:
  * the numbered raw-SQL migrations 026-031 are recorded in schema_migrations with their
    SHA-256, and each recorded checksum matches the file on disk (so none drifted after
    being applied);
  * the alembic head;
  * that the ORM FeeRecordModel.fee_pct and the provisioned column AGREE (both nullable),
    i.e. a fresh create_all() and the migrated DB would not diverge on this column;
  * the referral_payouts and fee_records column contracts the Phase 10 code depends on.

Writes nothing."""
from __future__ import annotations

import hashlib
import pathlib
import psycopg2

ROOT = pathlib.Path(__file__).resolve().parents[1]
dsn = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
c = psycopg2.connect(dsn)
cur = c.cursor()

print("=" * 72)
print("NUMBERED RAW-SQL MIGRATIONS (schema_migrations) vs files on disk")
print("=" * 72)
cur.execute("SELECT filename, sha256, step FROM public.schema_migrations ORDER BY filename")
recorded = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
for num in ("026", "027", "028", "029", "030", "031"):
    matches = [f for f in recorded if f"/{num}_" in f or f.endswith(f"{num}.sql")
               or (f.split("/")[-1].startswith(num))]
    # find the file
    files = list((ROOT / "backend_app" / "migrations").glob(f"{num}_*.sql"))
    if not files:
        print(f"  {num}: no file on disk")
        continue
    f = files[0]
    name = f"{f.parent.name}/{f.name}"
    disk_sha = hashlib.sha256(f.read_text(encoding="utf-8").encode()).hexdigest()
    rec = recorded.get(name)
    if rec is None:
        print(f"  {num}: file={f.name} NOT RECORDED in schema_migrations")
    elif rec[0] == disk_sha:
        print(f"  {num}: file={f.name} RECORDED, checksum MATCHES (step={rec[1]}) {disk_sha[:16]}...")
    else:
        print(f"  {num}: file={f.name} CHECKSUM MISMATCH recorded={rec[0][:16]} disk={disk_sha[:16]}")

print()
print("alembic head:")
cur.execute("SELECT version_num FROM alembic_version")
print("  ", [r[0] for r in cur.fetchall()])

print()
print("=" * 72)
print("ORM vs PROVISIONED agreement — fee_records.fee_pct")
print("=" * 72)
from backend_app.backend.fee_engine import FeeRecordModel  # noqa: E402
orm_nullable = FeeRecordModel.__table__.columns["fee_pct"].nullable
cur.execute("""SELECT is_nullable FROM information_schema.columns
               WHERE table_schema='public' AND table_name='fee_records'
                 AND column_name='fee_pct'""")
db_nullable = cur.fetchone()[0]
print(f"  ORM FeeRecordModel.fee_pct.nullable = {orm_nullable}")
print(f"  DB  fee_records.fee_pct is_nullable  = {db_nullable}")
print(f"  AGREE = {bool(orm_nullable) == (db_nullable == 'YES')}")

# fee_amount / fee_asset must stay NOT NULL in both
for colname in ("fee_amount", "fee_asset"):
    orm_n = FeeRecordModel.__table__.columns[colname].nullable
    cur.execute("""SELECT is_nullable FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='fee_records'
                     AND column_name=%s""", (colname,))
    db_n = cur.fetchone()[0]
    print(f"  {colname}: ORM nullable={orm_n}  DB={db_n}  (both must be mandatory)")

print()
print("=" * 72)
print("referral_payouts contract the Phase 10 code depends on")
print("=" * 72)
cur.execute("""SELECT column_name, is_nullable FROM information_schema.columns
               WHERE table_schema='public' AND table_name='referral_payouts'
                 AND column_name IN ('payout_method','payout_details','currency','status')
               ORDER BY column_name""")
for row in cur.fetchall():
    print("  ", row)

c.close()
