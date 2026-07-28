"""add referrals and available_discounts

Revision ID: 6f1b3d9c8a7e
Revises: e88f9911b5a2
Create Date: 2026-07-28 17:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6f1b3d9c8a7e'
down_revision: Union[str, Sequence[str], None] = 'e88f9911b5a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # Add available_discounts to profiles
    op.add_column('profiles', sa.Column('available_discounts', sa.Integer(), server_default='0', nullable=False))

    # Create referrals table
    op.create_table(
        'referrals',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()') if is_postgres else None),
        sa.Column('referrer_id', sa.UUID(), sa.ForeignKey('profiles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('referred_id', sa.UUID(), sa.ForeignKey('profiles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=50), server_default='pending', nullable=False),
        sa.Column('commission_usd', sa.Float(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()') if is_postgres else sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()') if is_postgres else sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('referred_id', name='uq_referrals_referred_id')
    )
    op.create_index('idx_referrals_referrer_id', 'referrals', ['referrer_id'], unique=False)

    # RLS Policies for referrals (only applied on PostgreSQL)
    if is_postgres:
        op.execute("ALTER TABLE referrals ENABLE ROW LEVEL SECURITY")
        
        # Referrer can see their referrals
        op.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_policies 
                    WHERE tablename = 'referrals' AND policyname = 'referrals_referrer_select'
                ) THEN
                    CREATE POLICY "referrals_referrer_select" ON referrals
                    FOR SELECT
                    TO authenticated
                    USING (referrer_id = auth.uid());
                END IF;
            END $$;
        """)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute('DROP POLICY IF EXISTS "referrals_referrer_select" ON referrals')

    op.drop_index('idx_referrals_referrer_id', table_name='referrals')
    op.drop_table('referrals')
    op.drop_column('profiles', 'available_discounts')
