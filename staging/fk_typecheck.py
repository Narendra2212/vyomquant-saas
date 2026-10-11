"""Type-check every foreign key `add_foreign_keys_20260817.py` attempts, independently.

The migration runs all seven ALTERs in one transaction with a `try/except` around each.
PostgreSQL aborts the whole transaction on the first failure, so the first defect masks
the other six and the migration's own warnings become useless. Each ALTER is attempted
here inside its own SAVEPOINT and rolled back, so every FK's real outcome is observed and
nothing is left behind.

Reports the referencing and referenced column types alongside each result, because the
failure mode is always a type mismatch between two declarations in the repository and the
fix depends on which side is authoritative.
"""

from __future__ import annotations

import pathlib
import sys

import psycopg2

REPO = pathlib.Path(__file__).resolve().parent.parent

# (constraint, table, column, ref_table, ref_column, on_delete)
FKS = [
    ("fk_execution_records_task_id", "execution_records", "task_id", "dag_tasks", "task_id", "SET NULL"),
    ("fk_execution_records_order_id", "execution_records", "order_id", "orders", "id", "SET NULL"),
    ("fk_fills_tenant_id", "fills", "tenant_id", "profiles", "id", "CASCADE"),
    ("fk_orders_tenant_id", "orders", "tenant_id", "profiles", "id", "CASCADE"),
    ("fk_positions_tenant_id", "positions", "tenant_id", "profiles", "id", "CASCADE"),
    ("fk_dag_tasks_tenant_id", "dag_tasks", "tenant_id", "profiles", "id", "CASCADE"),
    ("fk_execution_records_tenant_id", "execution_records", "tenant_id", "profiles", "id", "CASCADE"),
]

TYPE_Q = """
SELECT data_type, coalesce(character_maximum_length::text, '') 
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
"""


def col_type(cur, table: str, column: str) -> str:
    cur.execute(TYPE_Q, (table, column))
    row = cur.fetchone()
    if not row:
        return "<COLUMN ABSENT>"
    dt, ln = row
    return f"{dt}({ln})" if ln else dt


def main() -> int:
    url = (REPO / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
    conn = psycopg2.connect(url)
    conn.autocommit = False
    out: list[str] = []

    def emit(s: str = "") -> None:
        out.append(s)
        print(s.encode("ascii", "replace").decode("ascii"))

    emit("=" * 78)
    emit("add_foreign_keys_20260817.py -- all 7 FKs attempted independently")
    emit("=" * 78)

    ok = 0
    bad: list[tuple[str, str]] = []
    with conn.cursor() as cur:
        for name, tbl, col, rtbl, rcol, ondel in FKS:
            lhs = col_type(cur, tbl, col)
            rhs = col_type(cur, rtbl, rcol)
            cur.execute("SAVEPOINT fkprobe")
            sql = (
                f"ALTER TABLE {tbl} ADD CONSTRAINT {name} "
                f"FOREIGN KEY ({col}) REFERENCES {rtbl}({rcol}) ON DELETE {ondel}"
            )
            try:
                cur.execute(sql)
                status = "WOULD SUCCEED"
                detail = ""
                ok += 1
            except Exception as exc:  # noqa: BLE001 - every failure class is reportable
                status = "WOULD FAIL"
                detail = str(exc).strip().splitlines()[0]
                cls = type(exc).__name__
                detail = f"{cls}: {detail}"
                bad.append((name, detail))
            finally:
                cur.execute("ROLLBACK TO SAVEPOINT fkprobe")

            emit()
            emit(f"{status}  {name}")
            emit(f"   {tbl}.{col}  [{lhs}]  ->  {rtbl}.{rcol}  [{rhs}]")
            if detail:
                emit(f"   {detail}")

    conn.rollback()
    conn.close()

    emit()
    emit("-" * 78)
    emit(f"SUMMARY: {ok}/{len(FKS)} would succeed, {len(bad)} would fail")
    for name, detail in bad:
        emit(f"  FAIL {name}")
    emit()
    emit("Because all seven run in ONE transaction, a single failure aborts the revision")
    emit("and `UPDATE alembic_version` never commits -- so `upgrade head` cannot pass")
    emit("`add_foreign_keys`, and `implement_rls_policies` downstream of it never runs.")

    (REPO / "staging" / "fk_typecheck.txt").write_text("\n".join(out), encoding="utf-8")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
