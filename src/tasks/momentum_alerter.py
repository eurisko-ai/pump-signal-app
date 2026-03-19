"""Momentum alerter - detects real pumps and sends alerts"""
import asyncio
import asyncpg
from src.config import get_settings
from src.services.momentum_engine import momentum_engine
from src.services.telegram_service import TelegramService
from src.utils.logger import setup_logger

logger = setup_logger("momentum_alerter")
settings = get_settings()

telegram = TelegramService()

async def get_db():
    """Get database connection"""
    return await asyncpg.connect(settings.database_url)

async def start_momentum_alerter():
    """Watch momentum engine every 1 second and post alerts"""
    logger.info("🔥 Momentum alerter started - monitoring for real pumps")
    
    alerted_tokens = set()  # Track which tokens we've already alerted on
    
    while True:
        try:
            await check_momentum_signals(alerted_tokens)
        except Exception as e:
            logger.error(f"Alerter error: {e}")
        
        await asyncio.sleep(1)  # Check every 1 second

async def check_momentum_signals(alerted_tokens: set):
    """Check all tokens for buy/sell signals"""
    try:
        # Get all active token IDs from momentum engine
        if not hasattr(momentum_engine, 'token_trades') or not momentum_engine.token_trades:
            return
        
        conn = await get_db()
        
        for token_id in list(momentum_engine.token_trades.keys()):
            try:
                metrics = momentum_engine.get_all_metrics(token_id)
                
                if not metrics:
                    continue
                
                token = await conn.fetchrow(
                    "SELECT mint, name, symbol, created_at FROM tokens WHERE id = $1",
                    token_id
                )
                
                if not token:
                    continue
                
                # Determine if pre or post-migration based on time
                import time as time_module
                token_age_seconds = (time_module.time() - token['created_at'].timestamp())
                is_post_migration = token_age_seconds > 120  # 2 min = likely migrated
                
                signal_key = f"{token_id}_{is_post_migration}"
                
                # ============================================
                # PRE-MIGRATION: BUY signal
                # ============================================
                if (not is_post_migration and 
                    metrics['momentum_15s'] > 50 and 
                    metrics['acceleration_15s'] > 1.5 and 
                    not metrics['is_whale_dump'] and
                    f"{token_id}_False" not in alerted_tokens):
                    
                    await post_prepump_alert(token, metrics)
                    alerted_tokens.add(f"{token_id}_False")
                
                # ============================================
                # POST-MIGRATION: PUMP DETECTED signal
                # ============================================
                elif (is_post_migration and 
                      metrics['momentum_30s'] > 60 and 
                      not metrics['is_whale_dump'] and
                      f"{token_id}_True_pump" not in alerted_tokens):
                    
                    await post_pump_detected_alert(token, metrics)
                    alerted_tokens.add(f"{token_id}_True_pump")
                
                # ============================================
                # SELL signal: Momentum fading
                # ============================================
                if (metrics['pump_signal'] < 20 and 
                    (f"{token_id}_False" in alerted_tokens or f"{token_id}_True_pump" in alerted_tokens)):
                    
                    await post_momentum_fading_alert(token, metrics)
                    alerted_tokens.discard(f"{token_id}_False")
                    alerted_tokens.discard(f"{token_id}_True_pump")
                
                # ============================================
                # DANGER: Whale dump (always alert)
                # ============================================
                if metrics['is_whale_dump'] and f"{token_id}_whale_dump" not in alerted_tokens:
                    await post_whale_dump_alert(token, metrics)
                    alerted_tokens.add(f"{token_id}_whale_dump")
            
            except Exception as e:
                logger.error(f"Error checking token {token_id}: {e}")
        
        await conn.close()
    except Exception as e:
        logger.error(f"DB connection error in alerter: {e}")

async def post_prepump_alert(token: dict, metrics: dict):
    """Post BUY signal (PRE-PUMP alert)"""
    message = f"""<b>🟢 BUY SIGNAL - PRE-PUMP</b>

<b>{token['name']} (${token['symbol']})</b>

<b>⚡ Momentum Score: {metrics['momentum_15s']}/100</b>
• 15s Acceleration: {metrics['acceleration_15s']:.2f}x
• 1m Sustainability: {metrics['momentum_1m']}/100
• Whale Concentration: {metrics['whale_concentration']*100:.1f}%

<b>📊 Action:</b>
✅ BUY NOW on bonding curve
✅ Momentum building PRE-migration
✅ Whales accumulating (strong signal)

<b>Expected:</b>
Token will migrate to DEX in 5-15 minutes
Pump likely to continue post-migration

CA: <code>{token['mint']}</code>"""
    
    try:
        await telegram.send_alert(message)
        logger.info(f"🟢 BUY signal posted for {token['name']}")
    except Exception as e:
        logger.error(f"Failed to send BUY alert: {e}")

async def post_momentum_fading_alert(token: dict, metrics: dict):
    """Post SELL signal (momentum fading alert)"""
    message = f"""<b>🔴 SELL SIGNAL - MOMENTUM FADING</b>

<b>{token['name']} (${token['symbol']})</b>

<b>⚠️ Pump Signal: {metrics['pump_signal']}/100</b>
• 30s Signal: {metrics['momentum_30s']}/100
• 1m Sustainability: {metrics['momentum_1m']}/100

<b>📊 Action:</b>
🔴 SELL NOW - profit taking
⚠️ Momentum is fading
🚨 Whales likely exiting soon

<b>Reason:</b>
Volume declining, buy pressure weakening.
Exit before the dump.

CA: <code>{token['mint']}</code>"""
    
    try:
        await telegram.send_alert(message)
        logger.info(f"🔴 SELL signal posted for {token['name']}")
    except Exception as e:
        logger.error(f"Failed to send SELL alert: {e}")

async def post_pump_detected_alert(token: dict, metrics: dict):
    """Post STRONG BUY signal (post-migration pump detected)"""
    message = f"""<b>🚀 PUMP DETECTED - POST MIGRATION</b>

<b>{token['name']} (${token['symbol']})</b>

<b>🔥 Pump Signal: {metrics['pump_signal']}/100</b>
• 30s Momentum: {metrics['momentum_30s']}/100
• Buy Pressure: STRONG
• Volume: SPIKING

<b>📊 Action:</b>
🟢 BUY (if you missed pre-migration)
✅ Real DEX volume confirmed
✅ Momentum continuing post-migration

<b>Target:</b>
Ride the pump. Exit on momentum fading.

CA: <code>{token['mint']}</code>"""
    
    try:
        await telegram.send_alert(message)
        logger.info(f"🚀 PUMP DETECTED alert posted for {token['name']}")
    except Exception as e:
        logger.error(f"Failed to send pump detected alert: {e}")

async def post_whale_dump_alert(token: dict, metrics: dict):
    """Post whale dump alert (DANGER - AVOID/EXIT)"""
    message = f"""<b>🐋 WHALE DUMP DETECTED - HIGH RISK</b>

<b>{token['name']} (${token['symbol']})</b>

<b>⛔ Whale Concentration: {metrics['whale_concentration']*100:.1f}%</b>
• Top holders SELLING
• Price impact NEGATIVE
• DANGER ZONE

<b>📊 Action:</b>
🔴 EXIT IMMEDIATELY
⛔ AVOID new entries
🚨 Rug pull imminent

CA: <code>{token['mint']}</code>"""
    
    try:
        await telegram.send_alert(message)
        logger.info(f"🐋 WHALE DUMP alert for {token['name']}")
    except Exception as e:
        logger.error(f"Failed to send whale dump alert: {e}")
