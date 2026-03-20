"""Add bonding_curve_ca column to tokens table"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column('tokens', sa.Column('bonding_curve_ca', sa.String(255), nullable=True))
    op.create_index('idx_tokens_bonding_curve_ca', 'tokens', ['bonding_curve_ca'])


def downgrade():
    op.drop_index('idx_tokens_bonding_curve_ca')
    op.drop_column('tokens', 'bonding_curve_ca')
