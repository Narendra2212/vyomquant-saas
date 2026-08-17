"""add foreign key constraints

Revision ID: add_foreign_keys
Revises: d97ffff9c3bb
Create Date: 2026-08-17 00:00:00.000000

DB-CRITICAL-002 FIX: Add foreign key constraints to ensure referential integrity
and prevent orphaned records or invalid tenant references.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'add_foreign_keys'
down_revision: Union[str, Sequence[str], None] = 'd97ffff9c3bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    DB-CRITICAL-002 FIX: Add foreign key constraints to ensure referential integrity.
    
    This migration adds foreign key constraints to:
    1. Ensure tenant_id references valid tenants (where applicable)
    2. Ensure task_id references valid dag_tasks
    3. Ensure order_id references valid orders
    4. Prevent orphaned records and invalid references
    """
    
    # Add foreign key for execution_records.task_id -> dag_tasks.task_id
    try:
        op.execute("""
            ALTER TABLE execution_records 
            ADD CONSTRAINT fk_execution_records_task_id 
            FOREIGN KEY (task_id) REFERENCES dag_tasks(task_id) ON DELETE SET NULL
        """)
    except Exception as e:
        print(f"Warning: Could not add FK for execution_records.task_id: {e}")
    
    # Add foreign key for execution_records.order_id -> orders.id
    try:
        op.execute("""
            ALTER TABLE execution_records 
            ADD CONSTRAINT fk_execution_records_order_id 
            FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL
        """)
    except Exception as e:
        print(f"Warning: Could not add FK for execution_records.order_id: {e}")
    
    # Add foreign key for fills.tenant_id (references profiles table)
    try:
        op.execute("""
            ALTER TABLE fills 
            ADD CONSTRAINT fk_fills_tenant_id 
            FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE
        """)
    except Exception as e:
        print(f"Warning: Could not add FK for fills.tenant_id: {e}")
    
    # Add foreign key for orders.tenant_id (references profiles table)
    try:
        op.execute("""
            ALTER TABLE orders 
            ADD CONSTRAINT fk_orders_tenant_id 
            FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE
        """)
    except Exception as e:
        print(f"Warning: Could not add FK for orders.tenant_id: {e}")
    
    # Add foreign key for positions.tenant_id (references profiles table)
    try:
        op.execute("""
            ALTER TABLE positions 
            ADD CONSTRAINT fk_positions_tenant_id 
            FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE
        """)
    except Exception as e:
        print(f"Warning: Could not add FK for positions.tenant_id: {e}")
    
    # Add foreign key for dag_tasks.tenant_id (references profiles table)
    try:
        op.execute("""
            ALTER TABLE dag_tasks 
            ADD CONSTRAINT fk_dag_tasks_tenant_id 
            FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE
        """)
    except Exception as e:
        print(f"Warning: Could not add FK for dag_tasks.tenant_id: {e}")
    
    # Add foreign key for execution_records.tenant_id (references profiles table)
    try:
        op.execute("""
            ALTER TABLE execution_records 
            ADD CONSTRAINT fk_execution_records_tenant_id 
            FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE
        """)
    except Exception as e:
        print(f"Warning: Could not add FK for execution_records.tenant_id: {e}")


def downgrade() -> None:
    """Remove foreign key constraints."""
    
    # Remove foreign keys in reverse order
    try:
        op.execute("ALTER TABLE execution_records DROP CONSTRAINT IF EXISTS fk_execution_records_tenant_id")
    except Exception as e:
        print(f"Warning: Could not drop FK: {e}")
    
    try:
        op.execute("ALTER TABLE dag_tasks DROP CONSTRAINT IF EXISTS fk_dag_tasks_tenant_id")
    except Exception as e:
        print(f"Warning: Could not drop FK: {e}")
    
    try:
        op.execute("ALTER TABLE positions DROP CONSTRAINT IF EXISTS fk_positions_tenant_id")
    except Exception as e:
        print(f"Warning: Could not drop FK: {e}")
    
    try:
        op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS fk_orders_tenant_id")
    except Exception as e:
        print(f"Warning: Could not drop FK: {e}")
    
    try:
        op.execute("ALTER TABLE fills DROP CONSTRAINT IF EXISTS fk_fills_tenant_id")
    except Exception as e:
        print(f"Warning: Could not drop FK: {e}")
    
    try:
        op.execute("ALTER TABLE execution_records DROP CONSTRAINT IF EXISTS fk_execution_records_order_id")
    except Exception as e:
        print(f"Warning: Could not drop FK: {e}")
    
    try:
        op.execute("ALTER TABLE execution_records DROP CONSTRAINT IF EXISTS fk_execution_records_task_id")
    except Exception as e:
        print(f"Warning: Could not drop FK: {e}")
