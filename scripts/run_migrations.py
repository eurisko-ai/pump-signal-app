#!/usr/bin/env python3
"""Run migrations - currently disabled, using SQLAlchemy create_all"""
import os
import sys
sys.path.insert(0, '/app')

from src.models import Base
from src.config import get_settings
import sqlalchemy as sa

settings = get_settings()

try:
    engine = sa.create_engine(settings.database_url)
    
    print(f"[Migration] Connecting to {settings.db_host}:{settings.db_port}/{settings.db_name}")
    
    # Create all tables (SQLAlchemy will skip if they exist)
    Base.metadata.create_all(engine)
    
    print("[Migration] ✅ Database schema created successfully")
    
except Exception as e:
    print(f"[Migration] ❌ Error: {e}")
    sys.exit(1)
