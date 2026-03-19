"""002 - Add token_trades and token_momentum tables for Phase 2

High-frequency momentum detection: trade-level data capture + multi-timeframe scoring.
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade():
    # --- token_trades: individual trade events from PumpPortal ---
    op.create_table(
        "token_trades",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("token_id", sa.Integer, sa.ForeignKey("tokens.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trader_address", sa.String(255), nullable=False),
        sa.Column("amount_sol", sa.Float, nullable=False),
        sa.Column("direction", sa.String(4), nullable=False),  # 'buy' or 'sell'
        sa.Column("is_whale", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("tx_signature", sa.String(255), nullable=True),
        sa.Column("timestamp", sa.DateTime, nullable=False),
    )
    # Primary query pattern: trades for a token in time range
    op.create_index("idx_token_trades_token_ts", "token_trades", ["token_id", "timestamp"])
    # For whale queries
    op.create_index("idx_token_trades_whale", "token_trades", ["token_id", "is_whale", "timestamp"])
    # For cleanup by age
    op.create_index("idx_token_trades_ts", "token_trades", ["timestamp"])

    # --- token_momentum: latest computed momentum snapshot per token ---
    op.create_table(
        "token_momentum",
        sa.Column("token_id", sa.Integer, sa.ForeignKey("tokens.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("trades_1s", sa.Integer, server_default="0"),
        sa.Column("volume_1s", sa.Float, server_default="0"),
        sa.Column("buy_pressure_1s", sa.Float, server_default="0"),
        sa.Column("whale_buys_1s", sa.Integer, server_default="0"),
        sa.Column("momentum_15s", sa.Float, server_default="0"),
        sa.Column("whale_concentration", sa.Float, server_default="0"),
        sa.Column("velocity", sa.Float, server_default="0"),
        sa.Column("pump_signal_30s", sa.Float, server_default="0"),
        sa.Column("trend_slope", sa.Float, server_default="0"),
        sa.Column("momentum_1m", sa.Float, server_default="0"),
        sa.Column("sustainability_score", sa.Float, server_default="0"),
        sa.Column("pump_signal_score", sa.Integer, server_default="0"),
        sa.Column("unique_traders", sa.Integer, server_default="0"),
        sa.Column("is_hot", sa.Boolean, server_default="false"),
        sa.Column("signal_type", sa.String(50), nullable=True),  # PRE_PUMP, PUMP_DETECTED, FADING, WHALE_DUMP
        sa.Column("last_updated", sa.DateTime, nullable=False),
    )
    op.create_index("idx_token_momentum_hot", "token_momentum", ["is_hot", "pump_signal_score"])

    # --- momentum_alerts: dedup + history of momentum-based alerts ---
    op.create_table(
        "momentum_alerts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("token_id", sa.Integer, sa.ForeignKey("tokens.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signal_type", sa.String(50), nullable=False),
        sa.Column("pump_signal_score", sa.Integer, nullable=False),
        sa.Column("details", sa.Text, nullable=True),
        sa.Column("telegram_message_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("idx_momentum_alerts_token_type", "momentum_alerts", ["token_id", "signal_type", "created_at"])


def downgrade():
    op.drop_table("momentum_alerts")
    op.drop_table("token_momentum")
    op.drop_table("token_trades")
