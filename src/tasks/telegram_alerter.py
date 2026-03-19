"""Telegram alerter - post high-score signals to group"""
import asyncio
import asyncpg
from datetime import datetime
from src.config import get_settings
from src.utils.logger import setup_logger
from src.services.telegram_service import TelegramService

logger = setup_logger("telegram_alerter")
settings = get_settings()

# Initialize service
telegram_service = TelegramService()

async def get_db():
    return await asyncpg.connect(settings.database_url)

async def start_telegram_alerter():
    """Watch for high-score signals and post to Telegram"""
    logger.info("Telegram alerter started")
    
    while True:
        try:
            await post_pending_alerts()
        except Exception as e:
            logger.error(f"Alerter error: {e}")
        
        await asyncio.sleep(15)  # Check every 15 seconds

async def post_pending_alerts():
    """Find unposted high-score signals and post them"""
    try:
        conn = await get_db()
        
        # Get signals with score >= threshold that haven't been posted yet
        pending = await conn.fetch(
            """
            SELECT s.id, s.score, t.id as token_id, t.mint, t.name, t.symbol,
                   t.market_cap, t.volume_24h, t.holders, t.age_hours, t.liquidity_ratio,
                   s.narrative_type, s.risk_level,
                   s.status_score, s.market_cap_score, s.holders_score,
                   s.volume_score, s.liquidity_score, s.narrative_score
            FROM signals s
            JOIN tokens t ON s.token_id = t.id
            WHERE s.score >= $1
            AND NOT EXISTS (
                SELECT 1 FROM alerts a 
                WHERE a.signal_id = s.id AND a.status::text = 'posted'
            )
            ORDER BY s.score DESC
            LIMIT 5
            """,
            settings.alert_threshold
        )
        
        if not pending:
            await conn.close()
            return
        
        logger.info(f"Found {len(pending)} pending alerts")
        
        for signal_row in pending:
            try:
                # Prepare token dict
                token = {
                    "mint": signal_row["mint"],
                    "name": signal_row["name"],
                    "symbol": signal_row["symbol"],
                    "market_cap": signal_row["market_cap"],
                    "volume_24h": signal_row["volume_24h"],
                    "holders": signal_row["holders"],
                    "age_hours": signal_row["age_hours"],
                    "liquidity_ratio": signal_row["liquidity_ratio"],
                }
                
                # Prepare signal dict
                signal = {
                    "id": signal_row["id"],
                    "score": signal_row["score"],
                }
                
                # Prepare breakdown
                breakdown = {
                    "status": signal_row["status_score"],
                    "market_cap": signal_row["market_cap_score"],
                    "holders": signal_row["holders_score"],
                    "volume": signal_row["volume_score"],
                    "liquidity": signal_row["liquidity_score"],
                    "narrative": signal_row["narrative_score"],
                    "narrative_type": signal_row["narrative_type"],
                    "risk_level": signal_row["risk_level"],
                }
                
                # Format message
                message = telegram_service.format_alert_message(token, signal, breakdown)
                
                # Send to Telegram
                if await telegram_service.send_alert(message):
                    # Mark as posted
                    alert_id = await conn.fetchval(
                        """
                        INSERT INTO alerts (signal_id, status)
                        VALUES ($1, 'posted')
                        ON CONFLICT DO NOTHING
                        RETURNING id
                        """,
                        signal["id"]
                    )
                    logger.info(f"✅ Alert posted for {token['name']} (score={signal['score']})")
                else:
                    logger.warning(f"Failed to post alert for {token['name']}")
                    # Create failed alert record
                    await conn.execute(
                        "INSERT INTO alerts (signal_id, status) VALUES ($1, 'failed') ON CONFLICT DO NOTHING",
                        signal["id"]
                    )
            
            except Exception as e:
                logger.error(f"Error posting alert: {e}")
        
        await conn.close()
    
    except Exception as e:
        logger.error(f"Failed to post pending alerts: {e}")
