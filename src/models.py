"""SQLAlchemy models for Pump Signal app"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey, Index, CheckConstraint, Enum
from sqlalchemy.ext.declarative import declarative_base
from pgvector.sqlalchemy import Vector
import enum

Base = declarative_base()

class AlertStatus(enum.Enum):
    POSTED = "posted"
    SKIPPED = "skipped"
    FAILED = "failed"

class Token(Base):
    __tablename__ = "tokens"
    
    id = Column(Integer, primary_key=True)
    mint = Column(String(255), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    symbol = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    market_cap = Column(Float, nullable=True)
    volume_24h = Column(Float, nullable=True)
    holders = Column(Integer, nullable=True)
    price_change_5m = Column(Float, nullable=True)
    price_change_1h = Column(Float, nullable=True)
    created_timestamp = Column(DateTime, nullable=True)
    last_tx_timestamp = Column(DateTime, nullable=True)
    embedding = Column(Vector(384), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("mint LIKE '%pump'", name='check_ca_ends_with_pump'),
        Index('idx_tokens_created_at', 'created_at', postgresql_ops={'created_at': 'DESC'}),
        Index('idx_tokens_updated_at', 'updated_at', postgresql_ops={'updated_at': 'DESC'}),
    )

class Signal(Base):
    __tablename__ = "signals"
    
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), nullable=False)
    score = Column(Integer, nullable=False, index=True)
    status_score = Column(Integer, nullable=True)
    market_cap_score = Column(Integer, nullable=True)
    holders_score = Column(Integer, nullable=True)
    volume_score = Column(Integer, nullable=True)
    liquidity_score = Column(Integer, nullable=True)
    age_penalty = Column(Integer, nullable=True)
    whale_risk = Column(Integer, nullable=True)
    narrative_score = Column(Integer, nullable=True)
    narrative_type = Column(String(50), nullable=True)
    risk_level = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_signals_score', 'score', postgresql_ops={'score': 'DESC'}),
        Index('idx_signals_created_at', 'created_at', postgresql_ops={'created_at': 'DESC'}),
    )

class Alert(Base):
    __tablename__ = "alerts"
    
    id = Column(Integer, primary_key=True)
    signal_id = Column(Integer, ForeignKey("signals.id"), nullable=False)
    status = Column(Enum(AlertStatus), nullable=False)
    telegram_message_id = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_alerts_status', 'status'),
        Index('idx_alerts_created_at', 'created_at', postgresql_ops={'created_at': 'DESC'}),
    )

class ScanLog(Base):
    __tablename__ = "scan_log"
    
    id = Column(Integer, primary_key=True)
    scan_date = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    tokens_found = Column(Integer, nullable=True)
    alerts_posted = Column(Integer, nullable=True)
    min_score = Column(Float, nullable=True)
    max_score = Column(Float, nullable=True)
    avg_score = Column(Float, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    error_count = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    
    __table_args__ = (
        Index('idx_scan_log_date', 'scan_date', postgresql_ops={'scan_date': 'DESC'}),
    )

class Settings(Base):
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True)
    key = Column(String(255), nullable=False, unique=True)
    value = Column(String(1024), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TokenPriceHistory(Base):
    __tablename__ = "token_price_history"
    
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), nullable=False)
    price = Column(Float, nullable=True)
    market_cap = Column(Float, nullable=True)
    volume_24h = Column(Float, nullable=True)
    holders = Column(Integer, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_token_price_history_token_recorded', 'token_id', 'recorded_at'),
    )
