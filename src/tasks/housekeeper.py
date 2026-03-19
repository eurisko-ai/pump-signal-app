"""Database housekeeping - purge old data"""
import asyncio
import asyncpg
from datetime import datetime, timedelta
from src.config import get_settings
from src.utils.logger import setup_logger

logger = setup_logger("housekeeper")
settings = get_settings()

async def get_db():
    return await asyncpg.connect(settings.database_url)

async def start_housekeeper():
    """Run housekeeping tasks periodically"""
    logger.info(f"Housekeeper starting: cleanup interval={settings.housekeeper_interval_minutes}m, retention={settings.data_retention_hours}h")
    
    while True:
        try:
            await run_cleanup()
        except Exception as e:
            logger.error(f"Housekeeper error: {e}")
        
        await asyncio.sleep(settings.housekeeper_interval_minutes * 60)

async def run_cleanup():
    """Execute cleanup tasks"""
    try:
        conn = await get_db()
        cutoff_time = datetime.utcnow() - timedelta(hours=settings.data_retention_hours)
        
        logger.info(f"Running cleanup - retention cutoff: {cutoff_time.isoformat()}")
        
        # 1. Delete old scan logs (keep last N hours)
        deleted_logs = await conn.fetchval(
            "DELETE FROM scan_log WHERE created_at < $1 RETURNING COUNT(*)",
            cutoff_time
        )
        logger.info(f"Cleaned scan_log: {deleted_logs} records deleted")
        
        # 2. Delete old token price history (keep last N hours)
        deleted_history = await conn.fetchval(
            "DELETE FROM token_price_history WHERE recorded_at < $1 RETURNING COUNT(*)",
            cutoff_time
        )
        logger.info(f"Cleaned token_price_history: {deleted_history} records deleted")
        
        # 3. Delete old alerts (keep last N hours) - but keep "posted" status for reference
        deleted_alerts = await conn.fetchval(
            """
            DELETE FROM alerts 
            WHERE created_at < $1 AND status IN ('failed', 'skipped')
            RETURNING COUNT(*)
            """,
            cutoff_time
        )
        logger.info(f"Cleaned alerts: {deleted_alerts} old/failed records deleted")
        
        # 4. Delete orphaned signals (no associated alerts, older than retention)
        deleted_signals = await conn.fetchval(
            """
            DELETE FROM signals s
            WHERE s.created_at < $1
            AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.signal_id = s.id AND a.created_at > $1)
            RETURNING COUNT(*)
            """,
            cutoff_time
        )
        logger.info(f"Cleaned signals: {deleted_signals} old/orphaned records deleted")
        
        # 5. Keep tokens forever (they're the source data), but log inactive ones
        inactive_tokens = await conn.fetchval(
            """
            SELECT COUNT(*) FROM tokens t
            WHERE t.updated_at < $1
            """,
            cutoff_time
        )
        logger.info(f"Inactive tokens (no recent signals): {inactive_tokens}")
        
        # 6. Vacuum to reclaim space
        await conn.execute("VACUUM ANALYZE")
        logger.info("✅ Cleanup complete - VACUUM ANALYZE done")
        
        await conn.close()
        
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        raise

async def get_cleanup_stats():
    """Get cleanup statistics"""
    try:
        conn = await get_db()
        
        stats = {
            "scan_log_count": await conn.fetchval("SELECT COUNT(*) FROM scan_log"),
            "tokens_count": await conn.fetchval("SELECT COUNT(*) FROM tokens"),
            "signals_count": await conn.fetchval("SELECT COUNT(*) FROM signals"),
            "alerts_count": await conn.fetchval("SELECT COUNT(*) FROM alerts"),
            "price_history_count": await conn.fetchval("SELECT COUNT(*) FROM token_price_history"),
            "db_size": await conn.fetchval(
                "SELECT pg_size_pretty(pg_database_size(current_database()))"
            ),
        }
        
        await conn.close()
        return stats
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return {}
