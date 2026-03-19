"""Real-time WebSocket scanner using PumpPortal API"""
import asyncio
import json
import websockets
import asyncpg
from datetime import datetime, timedelta
from src.config import get_settings
from src.services.scoring import scoring
from src.utils.logger import setup_logger

logger = setup_logger("websocket_scanner")
settings = get_settings()

# Track seen tokens (dedup)
seen_tokens = {}  # {ca: timestamp}

async def get_db():
    return await asyncpg.connect(settings.database_url)

async def start_websocket_scanner():
    """Main WebSocket scanner - real-time PumpPortal listener"""
    logger.info("🚀 Real-time WebSocket scanner starting (PumpPortal)")
    
    reconnect_delay = 1  # Start at 1 second
    max_delay = 30  # Cap at 30 seconds
    
    while True:
        try:
            await connect_and_listen()
            reconnect_delay = 1  # Reset on successful connection
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            logger.info(f"Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, max_delay)

async def connect_and_listen():
    """Connect to PumpPortal WebSocket and listen for events"""
    uri = "wss://pumpportal.fun/api/data"
    
    async with websockets.connect(uri) as websocket:
        logger.info("✅ Connected to PumpPortal WebSocket")
        
        # Subscribe to new token events
        await websocket.send(json.dumps({
            "method": "subscribeNewToken"
        }))
        logger.info("Subscribed to: subscribeNewToken")
        
        # Subscribe to migration events
        await websocket.send(json.dumps({
            "method": "subscribeMigration"
        }))
        logger.info("Subscribed to: subscribeMigration")
        
        # Listen for events
        async for message in websocket:
            try:
                event = json.loads(message)
                await process_event(event)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON: {message[:100]}")
            except Exception as e:
                logger.error(f"Error processing event: {e}")

async def process_event(event: dict):
    """Process incoming token event"""
    try:
        # Extract token data based on event type
        if "txType" in event and event["txType"] == "create":
            # New token creation event
            await process_new_token(event)
        elif "event" in event and event["event"] == "migration":
            # Migration event (bonding → DEX)
            await process_migration(event)
    except Exception as e:
        logger.error(f"Failed to process event: {e}")

async def process_new_token(event: dict):
    """Process new token creation event"""
    try:
        ca = event.get("mint", "")
        
        # Filter: CA must end with "pump"
        if not ca or not ca.endswith("pump"):
            return
        
        # Dedup check
        if ca in seen_tokens:
            age = (datetime.utcnow() - seen_tokens[ca]).total_seconds() / 3600
            if age < settings.dedup_window_hours:
                return
        
        # Extract token data
        token = {
            "mint": ca,
            "name": event.get("name", "Unknown"),
            "symbol": event.get("symbol", "?"),
            "description": event.get("description", ""),
            "market_cap": float(event.get("marketCap", 0)),
            "volume_24h": 0,  # Will be 0 at creation
            "holders": int(event.get("initialBuy", 1)),  # Rough estimate from initial buys
            "created_timestamp": datetime.utcnow(),
            "last_tx_timestamp": datetime.utcnow(),
            "age_hours": 0,
            "liquidity_ratio": 0,  # No liquidity yet
            "price_change_5m": 0,
        }
        
        # Score token
        score, breakdown = scoring.score_token(token)
        
        logger.info(f"🆕 New token: {token['name']} ({ca}) - Score: {score}")
        
        # Only insert if score >= threshold
        if score >= settings.alert_threshold:
            await insert_token_and_alert(token, score, breakdown)
        
        # Mark as seen
        seen_tokens[ca] = datetime.utcnow()
    
    except Exception as e:
        logger.error(f"Error processing new token: {e}")

async def process_migration(event: dict):
    """Process migration event (graduated token)"""
    try:
        ca = event.get("mint", "")
        
        # Filter: CA must end with "pump"
        if not ca or not ca.endswith("pump"):
            return
        
        # Dedup check
        if ca in seen_tokens:
            age = (datetime.utcnow() - seen_tokens[ca]).total_seconds() / 3600
            if age < settings.dedup_window_hours:
                return
        
        # Extract migration data
        token = {
            "mint": ca,
            "name": event.get("name", "Unknown"),
            "symbol": event.get("symbol", "?"),
            "description": event.get("description", ""),
            "market_cap": float(event.get("marketCap", 0)),
            "volume_24h": float(event.get("volume24h", 0)),
            "holders": int(event.get("holders", 50)),
            "created_timestamp": datetime.utcnow() - timedelta(hours=float(event.get("ageHours", 0))),
            "last_tx_timestamp": datetime.utcnow(),
            "age_hours": float(event.get("ageHours", 0)),
            "liquidity_ratio": float(event.get("liquidityRatio", 0)),
            "price_change_5m": float(event.get("priceChange5m", 0)),
        }
        
        # Filter: must have minimum holders
        if token["holders"] < settings.min_holders:
            logger.debug(f"Skipped {ca}: only {token['holders']} holders")
            return
        
        # Score token
        score, breakdown = scoring.score_token(token)
        
        logger.info(f"📈 Migrated token: {token['name']} ({ca}) - Score: {score}")
        
        # Insert even if score < threshold (for tracking)
        if score >= settings.alert_threshold:
            await insert_token_and_alert(token, score, breakdown)
        else:
            # Still track low-score tokens
            await insert_token_only(token)
        
        # Mark as seen
        seen_tokens[ca] = datetime.utcnow()
    
    except Exception as e:
        logger.error(f"Error processing migration: {e}")

async def insert_token_and_alert(token: dict, score: int, breakdown: dict):
    """Insert token, signal, and alert into DB"""
    try:
        conn = await get_db()
        
        # Insert token
        token_id = await conn.fetchval(
            """
            INSERT INTO tokens (mint, name, symbol, description, market_cap, volume_24h, holders, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (mint) DO UPDATE SET updated_at = NOW()
            RETURNING id
            """,
            token["mint"],
            token["name"],
            token["symbol"],
            token["description"],
            token["market_cap"],
            token["volume_24h"],
            token["holders"]
        )
        
        # Insert signal
        signal_id = await conn.fetchval(
            """
            INSERT INTO signals (token_id, score, status_score, market_cap_score, 
                                holders_score, volume_score, liquidity_score, age_penalty,
                                whale_risk, narrative_score, narrative_type, risk_level)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING id
            """,
            token_id,
            score,
            breakdown.get("status", 0),
            breakdown.get("market_cap", 0),
            breakdown.get("holders", 0),
            breakdown.get("volume", 0),
            breakdown.get("liquidity", 0),
            breakdown.get("age_penalty", 0),
            breakdown.get("whale_risk", 0),
            breakdown.get("narrative", 0),
            breakdown.get("narrative_type", "Unknown"),
            breakdown.get("risk_level", "")
        )
        
        # Create alert (will be posted by telegram_alerter)
        await conn.execute(
            "INSERT INTO alerts (signal_id, status) VALUES ($1, 'posted')",
            signal_id
        )
        
        logger.info(f"✅ Alert created for {token['name']} (score={score})")
        
        await conn.close()
    
    except Exception as e:
        logger.error(f"Failed to insert token and alert: {e}")

async def insert_token_only(token: dict):
    """Insert token without signal/alert (for low-score tracking)"""
    try:
        conn = await get_db()
        
        await conn.fetchval(
            """
            INSERT INTO tokens (mint, name, symbol, description, market_cap, volume_24h, holders, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (mint) DO UPDATE SET updated_at = NOW()
            RETURNING id
            """,
            token["mint"],
            token["name"],
            token["symbol"],
            token["description"],
            token["market_cap"],
            token["volume_24h"],
            token["holders"]
        )
        
        await conn.close()
    
    except Exception as e:
        logger.error(f"Failed to insert token: {e}")
