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
    inspector = sa.inspect(bind)

    # WHY THESE TWO OBJECTS ARE GUARDED.
    #
    # `profiles` is not created by any migration. Its only declaration in this
    # repository is `backend_app/migrations/020_declare_pre_existing_tables.sql`, which
    # exists to reproduce the tables Supabase already had -- and it declares
    # `available_discounts` among them. So on every database where `profiles` exists at
    # all, including production, this column exists before this revision runs, and the
    # bare `add_column` below failed with:
    #
    #     psycopg2.errors.DuplicateColumn: column "available_discounts" of relation
    #     "profiles" already exists
    #
    # That is not a staging artefact. `alembic upgrade head` against production would
    # fail here for the same reason, and because PostgreSQL aborts the transaction on
    # the failed statement, the revision could not record itself either -- so the
    # failure would repeat on every subsequent attempt.
    #
    # `referrals` gets the same treatment because `migrations/referral_system_redesign.sql`
    # and `migrations/006_reconcile_production_database.sql` also declare it. Which one
    # runs first is a function of provisioning order, and this revision should not
    # depend on winning that race. The policy below was already guarded this way by its
    # author; the column and the table were the gaps.
    #
    # `sa.inspect` rather than `ADD COLUMN IF NOT EXISTS`: this revision branches on
    # dialect and is expected to run on SQLite too, where that syntax does not exist.
    if 'available_discounts' not in {
        col['name'] for col in inspector.get_columns('profiles')
    }:
        op.add_column(
            'profiles',
            sa.Column('available_discounts', sa.Integer(), server_default='0', nullable=False),
        )
    else:
        print("profiles.available_discounts already present; skipping")

    # Create referrals table
    if 'referrals' in inspector.get_table_names():
        print("referrals already present; skipping table creation")
    else:
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

    # Re-inspect: the table may have just been created, and the index check needs to
    # see the post-create state rather than the snapshot taken at the top of upgrade().
    existing_indexes = {ix['name'] for ix in sa.inspect(bind).get_indexes('referrals')}
    if 'idx_referrals_referrer_id' in existing_indexes:
        print("idx_referrals_referrer_id already present; skipping")
    else:
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
