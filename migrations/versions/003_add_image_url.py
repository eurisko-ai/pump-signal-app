"""Add image_url column to tokens table

Revision ID: 003
"""
from alembic import op
import sqlalchemy as sa

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('tokens', sa.Column('image_url', sa.Text(), nullable=True))

def downgrade():
    op.drop_column('tokens', 'image_url')
