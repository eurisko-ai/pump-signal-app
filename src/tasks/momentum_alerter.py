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
    return await asyncpg.connect(settings.database_url)

async def start_momentum_alerter():
    """Watch momentum engine every 1 second and post alerts"""
    logger.info("Momentum alerter started")
    
    alerted_tokens = set()  # Track which tokens we've already alerted on
    
    while True:
        try:
            await check_momentum_signals(alerted_tokens)
        except Exception as e:
            logger.error(f"Alerter error: {e}")
        
        await asyncio.sleep(1)  # Check every 1 second

async def check_momentum_signals(alerted_tokens: set):
    """Check all tokens for pump signals"""
    conn = await get_db()
    
    for token_id in list(momentum_engine.token_trades.keys()):
        try:
            metrics = momentum_engine.get_all_metrics(token_id)
            
            if not metrics:
                continue
            
            token = await conn.fetchrow(
                "SELECT mint, name, symbol FROM tokens WHERE id = $1",
                token_id
            )
            
            if not token:
                continue
            
            # Check for PRE-PUMP signal
            if (metrics['momentum_15s'] > 50 and 
                metrics['acceleration_15s'] > 1.5 and 
                not metrics['is_whale_dump'] and
                token_id not in alerted_tokens):
                
                await post_prepump_alert(token, metrics)
                alerted_tokens.add(token_id)
            
            # Check for momentum fading
            elif metrics['pump_signal'] < 20 and token_id in alerted_tokens:
                await post_momentum_fading_alert(token, metrics)
                alerted_tokens.discard(token_id)
            
            # Check for whale dump
            if metrics['is_whale_dump']:
                await post_whale_dump_alert(token, metrics)
        
        except Exception as e:
            logger.error(f"Error checking token {token_id}: {e}")
    
    await conn.close()

async def post_prepump_alert(token: dict, metrics: dict):
    """Post PRE-PUMP alert"""
    message = f"""<b>🔥 PRE-PUMP DETECTED: {token['name']} (${token['symbol']})</b>

<b>Momentum Building:</b>
• 15s Momentum: {metrics['momentum_15s']}/100
• Acceleration: {metrics['acceleration_15s']:.2f}x
• 1m Sustained: {metrics['momentum_1m']}/100
• Whale Concentration: {metrics['whale_concentration']*100:.1f}%

<b>Status:</b>
✅ Strong pre-migration momentum building
✅ Whales accumulating (not selling)
✅ High buy pressure

<b>Action:</b>
Token likely to migrate soon. Monitor for post-migration pump.

CA: <code>{token['mint']}</code>"""
    
    await telegram.send_alert(message)
    logger.info(f"✅ PRE-PUMP alert posted for {token['name']}")

async def post_momentum_fading_alert(token: dict, metrics: dict):
    """Post momentum fading alert"""
    message = f"""<b>⚠️ MOMENTUM FADING: {token['name']} (${token['symbol']})</b>

Momentum declining. Consider exiting if you're in.

Pump Signal: {metrics['pump_signal']}/100"""
    
    await telegram.send_alert(message)
    logger.info(f"⚠️ Momentum fading alert for {token['name']}")

async def post_whale_dump_alert(token: dict, metrics: dict):
    """Post whale dump alert"""
    message = f"""<b>🐋 WHALE DUMP DETECTED: {token['name']} (${token['symbol']})</b>

Top holders selling. HIGH RISK.

Whale Concentration: {metrics['whale_concentration']*100:.1f}%
⛔ AVOID or EXIT IMMEDIATELY

CA: <code>{token['mint']}</code>"""
    
    await telegram.send_alert(message)
    logger.info(f"🐋 Whale dump alert for {token['name']}")
