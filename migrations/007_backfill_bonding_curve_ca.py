"""Backfill bonding_curve_ca from raw_create_event JSON"""
from alembic import op
import sqlalchemy as sa


def upgrade():
    # Extract bondingCurve from raw_create_event JSONB and update bonding_curve_ca
    op.execute("""
        UPDATE tokens
        SET bonding_curve_ca = raw_create_event->>'bondingCurve'
        WHERE bonding_curve_ca IS NULL
        AND raw_create_event IS NOT NULL
        AND raw_create_event->>'bondingCurve' IS NOT NULL;
    """)


def downgrade():
    # Nothing to downgrade - we're just filling NULL values
    pass
