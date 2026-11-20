"""add foreign key constraints

Revision ID: add_foreign_keys
Revises: d97ffff9c3bb
Create Date: 2026-08-17 00:00:00.000000

DB-CRITICAL-002 FIX: Add foreign key constraints to ensure referential integrity
and prevent orphaned records or invalid tenant references.

WHY THIS REVISION DECLARES TWO CONSTRAINTS AND NOT SEVEN
--------------------------------------------------------
It used to declare seven, each wrapped in ``try/except Exception`` with a printed
warning. Five of them could not be created at this point in history and never could:
``tenant_id`` is ``varchar(36)`` on orders, positions, fills and dag_tasks while
``profiles.id`` is ``uuid``, and ``execution_records.task_id`` is ``uuid`` against a
``varchar(36)`` primary key. PostgreSQL has no equality operator between uuid and
varchar, so those five are not a transient failure -- they are a type conflict.

And the ``try/except`` could not contain it. A failed statement marks the whole
transaction aborted, so every later statement -- including alembic's own
``UPDATE alembic_version`` -- failed with ``InFailedSqlTransaction``. The ``except``
caught the error, printed its warning, and the revision died a few statements later
anyway. Measured against a database provisioned from this repository:

    Warning: Could not add FK for execution_records.task_id: (DatatypeMismatch)
      Key columns "task_id" and "task_id" are of incompatible types:
      uuid and character varying.
    ...
    sqlalchemy.exc.InternalError: (psycopg2.errors.InFailedSqlTransaction)
    [SQL: UPDATE alembic_version SET version_num='add_foreign_keys' ...]

That mattered well beyond this file. ``alembic history`` puts
``implement_rls_policies`` immediately downstream, on the only path to ``head``. While
this revision could not commit, that one never ran, so the nine tenant-isolation
policies it declares -- on orders, positions, fills, dag_tasks, execution_records,
idempotency_keys, transaction_records, transaction_checkpoints and
reconciliation_mismatches -- were installed on no database built by migration. Those
are the execution and financial tables, and they carried no row filtering at all.

WHY THERE ARE NO SAVEPOINTS HERE ANY MORE
-----------------------------------------
An earlier repair gave each constraint its own SAVEPOINT, which made the revision
commit and unblocked the RLS migration. It worked, and it was the wrong shape: a
savepoint belongs around a statement that is INTENTIONALLY optional, and referential
integrity on financial tables is not optional. What the savepoints actually did was
convert a real schema defect into a warning in a log, which is how the defect had
survived in the first place.

So this revision now declares only what is deterministically creatable here, and does
it strictly -- if either constraint fails, the revision fails, loudly, and alembic
rolls it back. The remaining five are created by ``a1b2c3d4e5f6``, immediately after
it normalises ``tenant_id`` and ``dag_tasks.task_id`` to ``uuid``. Splitting them that
way follows the real dependency: the five need a type change first, and a migration
cannot create a constraint whose precondition a later migration supplies.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'add_foreign_keys'
down_revision: Union[str, Sequence[str], None] = 'd97ffff9c3bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# WHY THE DDL IS LITERAL TEXT RATHER THAN BUILT FROM PARTS.
#
# `tests/test_bandit_sast_gate_is_green.py` refuses SQL assembled by interpolating a
# value nothing constrains, and it is right to: it is the check the repository's
# `# nosec B608` suppressions rest on. It permits an interpolated identifier only when
# something guarantees it is one. A tuple unpacked into function parameters gives it
# nothing to verify, so a later edit that fed a variable in here would look identical to
# a safe one. Keeping each statement whole removes the question; nothing is interpolated
# into SQL anywhere in this revision.
#
#: (constraint_name, table, add_sql, drop_sql)
_FOREIGN_KEYS = (
    (
        "fk_execution_records_order_id", "execution_records",
        "ALTER TABLE execution_records ADD CONSTRAINT fk_execution_records_order_id "
        "FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL",
        "ALTER TABLE execution_records DROP CONSTRAINT IF EXISTS fk_execution_records_order_id",
    ),
    (
        "fk_execution_records_tenant_id", "execution_records",
        "ALTER TABLE execution_records ADD CONSTRAINT fk_execution_records_tenant_id "
        "FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE",
        "ALTER TABLE execution_records DROP CONSTRAINT IF EXISTS fk_execution_records_tenant_id",
    ),
)


def _constraint_exists(conn, name: str, table: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE c.conname = :name AND t.relname = :table"
            ),
            {"name": name, "table": table},
        ).scalar()
    )


def upgrade() -> None:
    """Create the two constraints whose column types already agree.

    Strict: no savepoint, no ``try/except``. If PostgreSQL refuses one of these, that is
    a genuine defect in the schema this revision was written against, and the correct
    outcome is a failed migration the operator has to look at -- not a warning.

    Idempotent only in the sense that an already-present constraint is skipped, which is
    needed because ``a1b2c3d4e5f6`` also ensures these two exist for databases that got
    past this revision while it was still best-effort.
    """
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        print("add_foreign_keys: non-PostgreSQL dialect, nothing to do")
        return

    created = 0
    for name, table, add_sql, _drop_sql in _FOREIGN_KEYS:
        if _constraint_exists(conn, name, table):
            print(f"add_foreign_keys: {name} already present")
            continue
        op.execute(add_sql)
        print(f"add_foreign_keys: {name} created")
        created += 1

    print(
        f"add_foreign_keys: {created} constraint(s) created, "
        f"{len(_FOREIGN_KEYS) - created} already present. The five tenant_id/task_id "
        f"constraints are created by a1b2c3d4e5f6, after it normalises those columns "
        f"to uuid."
    )


def downgrade() -> None:
    """Drop what this revision created.

    ``DROP CONSTRAINT IF EXISTS`` does not error on a missing constraint, so no guard is
    needed and no savepoint is warranted.
    """
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    for _name, _table, _add_sql, drop_sql in reversed(_FOREIGN_KEYS):
        op.execute(drop_sql)
