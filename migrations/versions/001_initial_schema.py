"""Initial schema setup with 6 core tables

Revision ID: 001
Revises: 
Create Date: 2026-03-19

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Create ENUM for alert status
    alert_status = postgresql.ENUM('posted', 'skipped', 'failed', name='alert_status_enum')
    alert_status.create(op.get_bind(), checkfirst=True)
    
    # 1. tokens table
    op.create_table(
        'tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mint', sa.String(255), nullable=False, unique=True, index=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('symbol', sa.String(50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('market_cap', sa.Float(), nullable=True),
        sa.Column('volume_24h', sa.Float(), nullable=True),
        sa.Column('holders', sa.Integer(), nullable=True),
        sa.Column('price_change_5m', sa.Float(), nullable=True),
        sa.Column('price_change_1h', sa.Float(), nullable=True),
        sa.Column('created_timestamp', sa.DateTime(), nullable=True),
        sa.Column('last_tx_timestamp', sa.DateTime(), nullable=True),
        sa.Column('embedding', postgresql.VECTOR(dim=384), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.CheckConstraint("mint LIKE '%pump'", name='check_ca_ends_with_pump'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_tokens_created_at', 'tokens', ['created_at'], postgresql_ops={'created_at': 'DESC'})
    op.create_index('idx_tokens_updated_at', 'tokens', ['updated_at'], postgresql_ops={'updated_at': 'DESC'})
    
    # 2. signals table
    op.create_table(
        'signals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token_id', sa.Integer(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False, index=True),
        sa.Column('status_score', sa.Integer(), nullable=True),
        sa.Column('market_cap_score', sa.Integer(), nullable=True),
        sa.Column('holders_score', sa.Integer(), nullable=True),
        sa.Column('volume_score', sa.Integer(), nullable=True),
        sa.Column('liquidity_score', sa.Integer(), nullable=True),
        sa.Column('age_penalty', sa.Integer(), nullable=True),
        sa.Column('whale_risk', sa.Integer(), nullable=True),
        sa.Column('narrative_score', sa.Integer(), nullable=True),
        sa.Column('narrative_type', sa.String(50), nullable=True),
        sa.Column('risk_level', sa.String(20), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['token_id'], ['tokens.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_signals_score', 'signals', ['score'], postgresql_ops={'score': 'DESC'})
    op.create_index('idx_signals_created_at', 'signals', ['created_at'], postgresql_ops={'created_at': 'DESC'})
    
    # 3. alerts table
    op.create_table(
        'alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('signal_id', sa.Integer(), nullable=False),
        sa.Column('status', alert_status, nullable=False),
        sa.Column('telegram_message_id', sa.String(255), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['signal_id'], ['signals.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_alerts_status', 'alerts', ['status'])
    op.create_index('idx_alerts_created_at', 'alerts', ['created_at'], postgresql_ops={'created_at': 'DESC'})
    
    # 4. scan_log table
    op.create_table(
        'scan_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scan_date', sa.DateTime(), server_default=sa.func.now(), nullable=False, index=True),
        sa.Column('tokens_found', sa.Integer(), nullable=True),
        sa.Column('alerts_posted', sa.Integer(), nullable=True),
        sa.Column('min_score', sa.Float(), nullable=True),
        sa.Column('max_score', sa.Float(), nullable=True),
        sa.Column('avg_score', sa.Float(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('error_count', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_scan_log_date', 'scan_log', ['scan_date'], postgresql_ops={'scan_date': 'DESC'})
    
    # 5. settings table
    op.create_table(
        'settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(255), nullable=False, unique=True),
        sa.Column('value', sa.String(1024), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 6. token_price_history table
    op.create_table(
        'token_price_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token_id', sa.Integer(), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('market_cap', sa.Float(), nullable=True),
        sa.Column('volume_24h', sa.Float(), nullable=True),
        sa.Column('holders', sa.Integer(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['token_id'], ['tokens.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_token_price_history_token_recorded', 'token_price_history', ['token_id', 'recorded_at'])

def downgrade() -> None:
    op.drop_table('token_price_history')
    op.drop_table('settings')
    op.drop_table('scan_log')
    op.drop_table('alerts')
    op.drop_table('signals')
    op.drop_table('tokens')
    
    # Drop ENUM
    alert_status = postgresql.ENUM('posted', 'skipped', 'failed', name='alert_status_enum')
    alert_status.drop(op.get_bind(), checkfirst=True)
