"""Add dexscreener_profile JSONB column to tokens table

Revision ID: 005
Revises: 004
Create Date: 2026-03-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = '005'
down_revision = '004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tokens', sa.Column('dexscreener_profile', JSONB, nullable=True))
    # Index for querying verified tokens
    op.create_index(
        'idx_tokens_dexscreener_verified',
        'tokens',
        [sa.text("(dexscreener_profile->>'verified')")],
        postgresql_where=sa.text("dexscreener_profile IS NOT NULL"),
    )


def downgrade():
    op.drop_index('idx_tokens_dexscreener_verified', table_name='tokens')
    op.drop_column('tokens', 'dexscreener_profile')
