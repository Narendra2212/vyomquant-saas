"""Dump the TENANT-01 matrix result for the map document.

Reuses the test module's own helpers, so the numbers in
staging/TENANT_ISOLATION_MAP.md are the numbers the test asserts on -- not a second
implementation that could disagree.
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault(
    "AERORA_TEST_DATABASE_URL", "postgresql://postgres:@127.0.0.1:62674/postgres"
)

NONCE = sys.argv[1] if len(sys.argv) > 1 else uuid.uuid4().hex[:12]
print(f"NONCE={NONCE}")

from tests import test_tenant01_cross_tenant_matrix_postgres as M  # noqa: E402

conn = M._connect()
cur = conn.cursor()
M._reset_role(cur)

a_id = str(uuid.uuid4())
b_id = str(uuid.uuid4())
M._seed_identity(cur, a_id)
M._seed_identity(cur, b_id)
print(f"identity seed errors: {M._IDENTITY_SEED_ERRORS or 'none'}")
print()

probed = {}
skipped = {}
for table in M.TARGET_TABLES:
    if not M._table_exists(cur, table):
        skipped[table] = "absent on this database"
        continue
    cols = M._columns(cur, table)
    owner_col = M._owner_column(cols, table)
    if owner_col is None:
        skipped[table] = "no owner column"
        continue
    pk = M._pk_column(cur, table)
    if pk is None:
        skipped[table] = "no primary key"
        continue
    a_pk = M._insert_row(cur, table, owner_col, a_id)
    b_pk = M._insert_row(cur, table, owner_col, b_id)
    if not b_pk:
        skipped[table] = M._LAST_INSERT_ERROR.get(table, "unseedable")
        continue
    probed[table] = {
        "owner_col": owner_col,
        "a_seeded": bool(a_pk),
        **M._probe(conn, table, owner_col, pk, a_id, b_id, b_pk),
    }

print("=" * 104)
print(f"{'table':28} {'owner col':13} {'read':5} {'upd':4} {'del':4} {'victim ok':10} verdict")
print("=" * 104)
leaks = 0
for table, f in sorted(probed.items()):
    read = f.get("select_visible", 0)
    upd = f.get("update_rows", 0)
    dele = f.get("delete_rows", 0)
    ok = f.get("victim_still_present") and f.get("victim_owner_unchanged")
    leaked = read or upd or dele or not ok
    if leaked:
        leaks += 1
    print(
        f"{table:28} {f['owner_col']:13} {read:<5} {upd:<4} {dele:<4} "
        f"{str(bool(ok)):10} {'LEAK' if leaked else 'DENIED'}"
    )

print()
print(f"TABLES PROBED      : {len(probed)}")
print(f"CROSS-TENANT LEAKS : {leaks}")
print(f"TABLES SKIPPED     : {len(skipped)}")
print()
print("skipped, with reasons:")
for table, reason in sorted(skipped.items()):
    print(f"   {table:28} {reason[:95]}")

M._reset_role(cur)
cur.close()
conn.close()
print()
print(f"NONCE_END={NONCE}")
