"""baseline

Revision ID: 4ef23035a692
Revises:
Create Date: 2026-06-16 21:03:10.680058

DB-ALM-01
---------
This revision has no ``down_revision`` -- it is the FIRST thing ``alembic upgrade head``
runs against a database Alembic has never stamped. Its own ``upgrade()`` unconditionally
``DROP TABLE ... CASCADE``s ``orders``, ``positions``, ``fills``, ``transaction_records``,
``transaction_checkpoints``, ``transaction_rollbacks`` and ``idempotency_keys`` on the
Postgres branch (and the sqlite branch drops the same seven without ``CASCADE``) before
recreating emptied-out versions of some of them in later revisions. On a FRESH database
that is a no-op by construction -- there is nothing in those tables to drop. On an
EXISTING, unstamped production database -- this platform's own parallel raw-SQL migration
system (``backend_app/migrations/*.sql``) already creates ``orders``/``positions``/``fills``
with real rows in them, independently of Alembic -- running this revision for the first
time would silently destroy every one of them. Nothing before this guard distinguished
those two cases.

``_refuse_if_any_target_table_has_rows`` checks every one of the seven tables this
revision drops for even one row (``EXISTS(SELECT 1 FROM ... LIMIT 1)``, not a full
``COUNT(*)`` -- the question is only ever "is there anything to lose", not "how much")
and raises, having altered nothing, if any of them does. A populated database is stamped
at this revision (``alembic stamp 4ef23035a692``) instead of upgraded through it, per the
remediation note; that is an operator decision this migration cannot make for them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = '4ef23035a692'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Every table this revision's upgrade() drops, on either dialect branch. Kept as one
#: list so the guard and the drop statements cannot silently drift apart.
_TABLES_THIS_REVISION_DROPS = (
    "transaction_rollbacks",
    "orders",
    "transaction_checkpoints",
    "transaction_records",
    "positions",
    "idempotency_keys",
    "fills",
)


def _table_exists(bind, table: str) -> bool:
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


def _refuse_if_any_target_table_has_rows(bind) -> None:
    """DB-ALM-01: refuse, having altered nothing, if any table this revision is about
    to drop already holds a row.

    A table that does not exist yet (the fresh-database case this revision was written
    for) is skipped -- nothing to lose, nothing to check.
    """
    populated = []
    for table in _TABLES_THIS_REVISION_DROPS:
        if not _table_exists(bind, table):
            continue
        has_a_row = bind.execute(
            sa.text(f'SELECT EXISTS(SELECT 1 FROM "{table}" LIMIT 1)')  # nosec B608 - identifier from the module-level constant above, never request input
        ).scalar()
        if has_a_row:
            populated.append(table)

    if populated:
        raise RuntimeError(
            f"DB-ALM-01: baseline migration 4ef23035a692 refuses to run against this "
            f"database. The following table(s) it would DROP already contain at least "
            f"one row: {populated!r}. NOTHING HAS BEEN ALTERED.\n"
            f"This revision has no down_revision -- it is meant for a FRESH database "
            f"where these tables are empty or do not exist yet. Running it against an "
            f"existing, populated database would destroy real data. If this database "
            f"already has the schema this revision chain expects (e.g. created by "
            f"backend_app/migrations/*.sql, this platform's own parallel raw-SQL "
            f"migration system), stamp it at this revision instead of upgrading "
            f"through it: `alembic stamp 4ef23035a692`."
        )


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    _refuse_if_any_target_table_has_rows(bind)
    if bind.dialect.name == "postgresql":
        op.execute('DROP INDEX IF EXISTS ix_transaction_rollbacks_tenant_id CASCADE')
        op.execute('DROP INDEX IF EXISTS ix_transaction_rollbacks_transaction_id CASCADE')
        op.execute('DROP TABLE IF EXISTS transaction_rollbacks CASCADE')
        op.execute('DROP INDEX IF EXISTS ix_orders_tenant_id CASCADE')
        op.execute('DROP TABLE IF EXISTS orders CASCADE')
        op.execute('DROP INDEX IF EXISTS ix_transaction_checkpoints_tenant_id CASCADE')
        op.execute('DROP INDEX IF EXISTS ix_transaction_checkpoints_transaction_id CASCADE')
        op.execute('DROP TABLE IF EXISTS transaction_checkpoints CASCADE')
        op.execute('DROP INDEX IF EXISTS ix_transaction_records_tenant_id CASCADE')
        op.execute('DROP TABLE IF EXISTS transaction_records CASCADE')
        op.execute('DROP INDEX IF EXISTS ix_positions_tenant_id CASCADE')
        op.execute('DROP TABLE IF EXISTS positions CASCADE')
        op.execute('DROP TABLE IF EXISTS idempotency_keys CASCADE')
        op.execute('DROP INDEX IF EXISTS ix_fills_tenant_id CASCADE')
        op.execute('DROP TABLE IF EXISTS fills CASCADE')
    else:
        op.execute('DROP INDEX IF EXISTS ix_transaction_rollbacks_tenant_id')
        op.execute('DROP INDEX IF EXISTS ix_transaction_rollbacks_transaction_id')
        op.execute('DROP TABLE IF EXISTS transaction_rollbacks')
        op.execute('DROP INDEX IF EXISTS ix_orders_tenant_id')
        op.execute('DROP TABLE IF EXISTS orders')
        op.execute('DROP INDEX IF EXISTS ix_transaction_checkpoints_tenant_id')
        op.execute('DROP INDEX IF EXISTS ix_transaction_checkpoints_transaction_id')
        op.execute('DROP TABLE IF EXISTS transaction_checkpoints')
        op.execute('DROP INDEX IF EXISTS ix_transaction_records_tenant_id')
        op.execute('DROP TABLE IF EXISTS transaction_records')
        op.execute('DROP INDEX IF EXISTS ix_positions_tenant_id')
        op.execute('DROP TABLE IF EXISTS positions')
        op.execute('DROP TABLE IF EXISTS idempotency_keys')
        op.execute('DROP INDEX IF EXISTS ix_fills_tenant_id')
        op.execute('DROP TABLE IF EXISTS fills')
    
    # Only create index if table exists (dag_tasks doesn't exist either so this would fail)
    # op.execute('CREATE INDEX IF NOT EXISTS ix_dag_tasks_worker_id ON dag_tasks (worker_id)')
    
    # execution_records does not exist, so alter/drop columns will fail.
    # reconciliation_mismatches does not exist.
    # The safest Alembic equivalent is to leave these out since the tables don't exist in PostgreSQL.
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('reconciliation_mismatches', 'tenant_id',
               existing_type=sa.UUID(),
               type_=sa.NUMERIC(),
               existing_nullable=False)
    op.add_column('execution_records', sa.Column('signal_id', sa.VARCHAR(length=128), nullable=True))
    op.add_column('execution_records', sa.Column('retry_count', sa.INTEGER(), server_default=sa.text('0'), nullable=True))
    op.add_column('execution_records', sa.Column('idempotency_key', sa.VARCHAR(length=128), nullable=True))
    op.alter_column('execution_records', 'task_id',
               existing_type=sa.UUID(),
               type_=sa.NUMERIC(),
               existing_nullable=True)
    op.alter_column('execution_records', 'tenant_id',
               existing_type=sa.UUID(),
               type_=sa.NUMERIC(),
               existing_nullable=False)
    op.drop_index(op.f('ix_dag_tasks_worker_id'), table_name='dag_tasks')
    op.create_table('fills',
    sa.Column('id', sa.VARCHAR(length=36), nullable=False),
    sa.Column('tenant_id', sa.VARCHAR(length=36), nullable=True),
    sa.Column('data', sqlite.JSON(), nullable=True),
    sa.Column('created_at', sa.DATETIME(), nullable=True),
    sa.Column('updated_at', sa.DATETIME(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_fills_tenant_id'), 'fills', ['tenant_id'], unique=False)
    op.create_table('idempotency_keys',
    sa.Column('idempotency_key', sa.VARCHAR(length=128), nullable=False),
    sa.Column('tenant_id', sa.VARCHAR(length=36), nullable=False),
    sa.Column('result', sqlite.JSON(), nullable=True),
    sa.Column('created_at', sa.DATETIME(), nullable=True),
    sa.PrimaryKeyConstraint('idempotency_key', 'tenant_id')
    )
    op.create_table('positions',
    sa.Column('id', sa.VARCHAR(length=36), nullable=False),
    sa.Column('tenant_id', sa.VARCHAR(length=36), nullable=True),
    sa.Column('data', sqlite.JSON(), nullable=True),
    sa.Column('created_at', sa.DATETIME(), nullable=True),
    sa.Column('updated_at', sa.DATETIME(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_positions_tenant_id'), 'positions', ['tenant_id'], unique=False)
    op.create_table('transaction_records',
    sa.Column('transaction_id', sa.VARCHAR(length=36), nullable=False),
    sa.Column('tenant_id', sa.VARCHAR(length=36), nullable=True),
    sa.Column('state', sa.VARCHAR(length=32), nullable=True),
    sa.Column('operations_count', sa.INTEGER(), nullable=True),
    sa.Column('checksum', sa.VARCHAR(length=64), nullable=True),
    sa.Column('created_at', sa.DATETIME(), nullable=True),
    sa.Column('started_at', sa.DATETIME(), nullable=True),
    sa.Column('completed_at', sa.DATETIME(), nullable=True),
    sa.PrimaryKeyConstraint('transaction_id')
    )
    op.create_index(op.f('ix_transaction_records_tenant_id'), 'transaction_records', ['tenant_id'], unique=False)
    op.create_table('transaction_checkpoints',
    sa.Column('checkpoint_id', sa.VARCHAR(length=36), nullable=False),
    sa.Column('transaction_id', sa.VARCHAR(length=36), nullable=True),
    sa.Column('tenant_id', sa.VARCHAR(length=36), nullable=True),
    sa.Column('state_snapshot', sqlite.JSON(), nullable=True),
    sa.Column('operations_snapshot', sqlite.JSON(), nullable=True),
    sa.Column('checksum', sa.VARCHAR(length=64), nullable=True),
    sa.Column('created_at', sa.DATETIME(), nullable=True),
    sa.PrimaryKeyConstraint('checkpoint_id')
    )
    op.create_index(op.f('ix_transaction_checkpoints_transaction_id'), 'transaction_checkpoints', ['transaction_id'], unique=False)
    op.create_index(op.f('ix_transaction_checkpoints_tenant_id'), 'transaction_checkpoints', ['tenant_id'], unique=False)
    op.create_table('orders',
    sa.Column('id', sa.VARCHAR(length=36), nullable=False),
    sa.Column('tenant_id', sa.VARCHAR(length=36), nullable=True),
    sa.Column('data', sqlite.JSON(), nullable=True),
    sa.Column('created_at', sa.DATETIME(), nullable=True),
    sa.Column('updated_at', sa.DATETIME(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_orders_tenant_id'), 'orders', ['tenant_id'], unique=False)
    op.create_table('transaction_rollbacks',
    sa.Column('rollback_id', sa.VARCHAR(length=36), nullable=False),
    sa.Column('transaction_id', sa.VARCHAR(length=36), nullable=True),
    sa.Column('tenant_id', sa.VARCHAR(length=36), nullable=True),
    sa.Column('checkpoint_id', sa.VARCHAR(length=36), nullable=True),
    sa.Column('created_at', sa.DATETIME(), nullable=True),
    sa.PrimaryKeyConstraint('rollback_id')
    )
    op.create_index(op.f('ix_transaction_rollbacks_transaction_id'), 'transaction_rollbacks', ['transaction_id'], unique=False)
    op.create_index(op.f('ix_transaction_rollbacks_tenant_id'), 'transaction_rollbacks', ['tenant_id'], unique=False)
    # ### end Alembic commands ###
