"""Health check routes"""
from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta
import asyncpg
from src.config import get_settings
from src.utils.logger import setup_logger

router = APIRouter()
logger = setup_logger("health")
settings = get_settings()

@router.get("/health")
async def health():
    """Liveness check"""
    return {
        "status": "alive",
        "timestamp": datetime.utcnow().isoformat()
    }

@router.get("/ready")
async def readiness():
    """Readiness check: DB connection + recent scan"""
    try:
        # Check database connection
        conn = await asyncpg.connect(settings.database_url)
        
        # Check if we have recent scans
        result = await conn.fetchval(
            "SELECT COUNT(*) FROM scan_log WHERE created_at > NOW() - INTERVAL '15 minutes'"
        )
        
        await conn.close()
        
        ready = result and result > 0
        
        return {
            "ready": ready,
            "db_connected": True,
            "recent_scans": result or 0,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        raise HTTPException(status_code=503, detail="Service not ready")
