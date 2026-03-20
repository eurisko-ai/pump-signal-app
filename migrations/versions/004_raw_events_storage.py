"""Add raw_create_event JSONB to tokens and create token_events table

Revision ID: 004
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None

def upgrade():
    # Add raw_create_event column to tokens table
    op.add_column('tokens', sa.Column('raw_create_event', JSONB, nullable=True))

    # Create token_events table for all trade/transaction events
    op.create_table(
        'token_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('token_id', sa.Integer(), sa.ForeignKey('tokens.id'), nullable=False),
        sa.Column('event_type', sa.String(20), nullable=False),  # 'create', 'buy', 'sell', 'migration'
        sa.Column('raw_event', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('NOW()'), nullable=False),
    )

    # Indexes for efficient querying
    op.create_index('idx_token_events_token_id', 'token_events', ['token_id'])
    op.create_index('idx_token_events_type', 'token_events', ['event_type'])
    op.create_index('idx_token_events_created_at', 'token_events', ['created_at'], postgresql_ops={'created_at': 'DESC'})

def downgrade():
    op.drop_table('token_events')
    op.drop_column('tokens', 'raw_create_event')
