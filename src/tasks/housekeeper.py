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
        try:
            deleted_logs = await conn.fetchval(
                "DELETE FROM scan_log WHERE scan_date < $1",
                cutoff_time
            )
            logger.info(f"Cleaned scan_log: {deleted_logs or 0} records deleted")
        except Exception as e:
            logger.warning(f"scan_log cleanup failed: {e}")
        
        # 2. Delete old token price history (keep last N hours)
        try:
            deleted_history = await conn.fetchval(
                "DELETE FROM token_price_history WHERE recorded_at < $1",
                cutoff_time
            )
            logger.info(f"Cleaned token_price_history: {deleted_history or 0} records deleted")
        except Exception as e:
            logger.warning(f"token_price_history cleanup failed: {e}")
        
        # 3. Delete old alerts (keep last N hours) - but keep "posted" status for reference
        try:
            deleted_alerts = await conn.fetchval(
                "DELETE FROM alerts WHERE created_at < $1 AND status::text != 'posted'",
                cutoff_time
            )
            logger.info(f"Cleaned alerts: {deleted_alerts or 0} old/failed records deleted")
        except Exception as e:
            logger.warning(f"alerts cleanup failed: {e}")
        
        # 4. Delete orphaned signals (no associated alerts, older than retention)
        try:
            await conn.execute(
                """
                DELETE FROM signals s
                WHERE s.created_at < $1
                AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.signal_id = s.id AND a.created_at > $1)
                """,
                cutoff_time
            )
            deleted_signals = await conn.fetchval(
                "SELECT COUNT(*) FROM signals WHERE created_at < $1",
                cutoff_time
            )
            logger.info(f"Cleaned signals: orphaned records deleted (remaining: {deleted_signals or 0})")
        except Exception as e:
            logger.warning(f"signals cleanup failed: {e}")
        
        # 5. Delete tokens older than 2 hours (hard lifetime limit)
        lifetime_cutoff = datetime.utcnow() - timedelta(hours=2)
        try:
            # First delete token_events for old tokens
            deleted_events = await conn.fetchval(
                """
                DELETE FROM token_events te
                WHERE te.token_id IN (SELECT id FROM tokens WHERE created_at < $1)
                """,
                lifetime_cutoff
            )
            
            # Then delete token_momentum for old tokens
            deleted_momentum = await conn.fetchval(
                """
                DELETE FROM token_momentum tm
                WHERE tm.token_id IN (SELECT id FROM tokens WHERE created_at < $1)
                """,
                lifetime_cutoff
            )
            
            # Finally delete the tokens themselves
            deleted_tokens = await conn.fetchval(
                "DELETE FROM tokens WHERE created_at < $1",
                lifetime_cutoff
            )
            
            if deleted_tokens:
                logger.info(f"🗑️ Deleted {deleted_tokens} old tokens (>2h lifetime) + {deleted_events or 0} events + {deleted_momentum or 0} momentum records")
        except Exception as e:
            logger.warning(f"Token cleanup failed: {e}")
        
        # 6. Vacuum to reclaim space
        try:
            await conn.execute("VACUUM ANALYZE")
            logger.info("✅ Cleanup complete - VACUUM ANALYZE done")
        except Exception as e:
            logger.warning(f"VACUUM failed: {e}")
        
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
