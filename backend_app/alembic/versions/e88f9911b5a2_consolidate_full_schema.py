"""consolidate full schema

Revision ID: e88f9911b5a2
Revises: d97ffff9c3bb
Create Date: 2026-07-28 12:35:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e88f9911b5a2'
down_revision: Union[str, Sequence[str], None] = 'd97ffff9c3bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema to include copilot, waitlist, library strategies & ratings, and RLS policies."""
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # 1. copilot_sessions & copilot_messages
    op.create_table(
        'copilot_sessions',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()') if is_postgres else None),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False, server_default='New Chat'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_copilot_sessions_user_id', 'copilot_sessions', ['user_id'], unique=False)

    op.create_table(
        'copilot_messages',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()') if is_postgres else None),
        sa.Column('session_id', sa.UUID(), sa.ForeignKey('copilot_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('context_snapshot', sa.JSON(), nullable=True),
        sa.Column('token_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name='chk_copilot_message_role')
    )
    op.create_index('idx_copilot_messages_session_id', 'copilot_messages', ['session_id'], unique=False)

    # 2. waitlist table
    op.create_table(
        'waitlist',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()') if is_postgres else None),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), nullable=False, unique=True),
        sa.Column('telegram', sa.Text(), nullable=True),
        sa.Column('experience_level', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('utm_source', sa.Text(), nullable=True),
        sa.Column('utm_medium', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('trader_type', sa.Text(), nullable=True),
        sa.Column('monthly_volume', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("experience_level IN ('beginner', 'intermediate', 'advanced', 'professional')", name='chk_waitlist_exp'),
        sa.CheckConstraint("status IN ('pending', 'approved', 'invited', 'converted')", name='chk_waitlist_status'),
        sa.CheckConstraint("trader_type IS NULL OR trader_type IN ('retail', 'discretionary', 'prop', 'institutional')", name='chk_waitlist_trader_type'),
        sa.CheckConstraint("monthly_volume IS NULL OR monthly_volume IN ('<100k', '100k-1M', '1M-10M', '>10M')", name='chk_waitlist_volume')
    )
    op.create_index('idx_waitlist_created_at', 'waitlist', [sa.literal_column('created_at DESC')], unique=False)
    op.create_index('idx_waitlist_status', 'waitlist', ['status'], unique=False)
    op.create_index('idx_waitlist_experience', 'waitlist', ['experience_level'], unique=False)
    op.create_index('idx_waitlist_email', 'waitlist', ['email'], unique=False)

    # 3. library_strategies & library_ratings
    op.create_table(
        'library_strategies',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()') if is_postgres else None),
        sa.Column('author_id', sa.UUID(), nullable=False),
        sa.Column('source_strategy_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('difficulty', sa.Text(), nullable=False, server_default='beginner'),
        sa.Column('tags', sa.JSON() if not is_postgres else sa.ARRAY(sa.Text()), nullable=True),
        sa.Column('symbol', sa.Text(), nullable=False),
        sa.Column('timeframe', sa.Text(), nullable=False),
        sa.Column('exchange_id', sa.Text(), nullable=False),
        sa.Column('node_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('has_ml_model', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('backtest_total_return_pct', sa.Numeric(10, 4), nullable=True),
        sa.Column('backtest_sharpe_ratio', sa.Numeric(10, 4), nullable=True),
        sa.Column('backtest_max_drawdown_pct', sa.Numeric(10, 4), nullable=True),
        sa.Column('backtest_win_rate_pct', sa.Numeric(10, 4), nullable=True),
        sa.Column('backtest_profit_factor', sa.Numeric(10, 4), nullable=True),
        sa.Column('backtest_total_trades', sa.Integer(), nullable=True),
        sa.Column('backtest_start_date', sa.Date(), nullable=True),
        sa.Column('backtest_end_date', sa.Date(), nullable=True),
        sa.Column('backtest_initial_capital', sa.Numeric(16, 2), nullable=True),
        sa.Column('equity_curve_snapshot', sa.JSON(), nullable=True),
        sa.Column('risk_stop_loss_pct', sa.Numeric(8, 4), nullable=True),
        sa.Column('risk_take_profit_pct', sa.Numeric(8, 4), nullable=True),
        sa.Column('risk_max_position_size', sa.Numeric(8, 4), nullable=True),
        sa.Column('risk_max_drawdown_pct', sa.Numeric(8, 4), nullable=True),
        sa.Column('clone_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('avg_rating', sa.Numeric(3, 2), nullable=True),
        sa.Column('rating_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('moderation_status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('moderation_notes', sa.Text(), nullable=True),
        sa.Column('moderated_by', sa.UUID(), nullable=True),
        sa.Column('moderated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_featured', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_library_strategies_author', 'library_strategies', ['author_id'], unique=False)

    op.create_table(
        'library_ratings',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()') if is_postgres else None),
        sa.Column('library_id', sa.UUID(), sa.ForeignKey('library_strategies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('rating', sa.SmallInteger(), nullable=False),
        sa.Column('review_text', sa.Text(), nullable=True),
        sa.Column('is_verified_clone', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('library_id', 'user_id', name='library_ratings_user_library_idx')
    )

    # 4. Postgres-specific triggers, functions, and RLS policies
    if is_postgres:
        op.execute("""
        ALTER TABLE dag_tasks ENABLE ROW LEVEL SECURITY;
        ALTER TABLE execution_records ENABLE ROW LEVEL SECURITY;
        ALTER TABLE copilot_sessions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE copilot_messages ENABLE ROW LEVEL SECURITY;
        ALTER TABLE waitlist ENABLE ROW LEVEL SECURITY;
        ALTER TABLE library_strategies ENABLE ROW LEVEL SECURITY;
        ALTER TABLE library_ratings ENABLE ROW LEVEL SECURITY;
        """)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('library_ratings')
    op.drop_table('library_strategies')
    op.drop_table('waitlist')
    op.drop_table('copilot_messages')
    op.drop_table('copilot_sessions')
