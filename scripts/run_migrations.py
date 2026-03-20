#!/usr/bin/env python3
"""Run migrations using Alembic"""
import os
import sys
import subprocess

sys.path.insert(0, '/app')

from src.config import get_settings

settings = get_settings()

try:
    print(f"[Migration] Connecting to {settings.db_host}:{settings.db_port}/{settings.db_name}")
    
    # Set environment for alembic
    os.environ['SQLALCHEMY_DATABASE_URL'] = settings.database_url
    
    # Run alembic upgrade to latest
    print("[Migration] Running Alembic migrations...")
    result = subprocess.run(
        ['alembic', 'upgrade', 'head'],
        cwd='/app',
        capture_output=True,
        text=True
    )
    
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)
    
    if result.returncode != 0:
        print(f"[Migration] ⚠️ Alembic returned code {result.returncode}, continuing...")
    else:
        print("[Migration] ✅ Database migrations completed successfully")
    
except Exception as e:
    print(f"[Migration] ❌ Error: {e}")
    sys.exit(1)
