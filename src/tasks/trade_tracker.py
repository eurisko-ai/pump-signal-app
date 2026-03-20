"""Trade tracker - manages per-token subscriptions and feeds momentum engine"""
import asyncio
import json
import websockets
import asyncpg
from typing import Dict, Set, Optional
from datetime import datetime
from src.config import get_settings
from src.services.momentum_engine import momentum_engine
from src.utils.logger import setup_logger

logger = setup_logger("trade_tracker")
settings = get_settings()

PUMPPORTAL_WS_URI = getattr(settings, "pumpportal_ws_uri", "wss://pumpportal.fun/api/data")
WS_RECONNECT_MAX_DELAY = getattr(settings, "ws_reconnect_max_delay", 30)

# Module-level DB pool for market cap updates
_trade_db_pool: Optional[asyncpg.Pool] = None

async def _get_trade_db_pool() -> asyncpg.Pool:
    """Lazy-init DB pool for trade tracker."""
    global _trade_db_pool
    if _trade_db_pool is None or _trade_db_pool._closed:
        _trade_db_pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=3)
    return _trade_db_pool

class TradeTracker:
    """Manage dynamic per-token trade subscriptions"""
    
    def __init__(self):
        self.tracked_tokens: Set[str] = set()  # Set of mints being tracked
        self.token_to_id: Dict[str, int] = {}  # {mint: token_id}
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
    
    async def connect(self):
        """Establish WebSocket connection to PumpPortal"""
        delay = 1
        while self.running:
            try:
                self.websocket = await websockets.connect(PUMPPORTAL_WS_URI)
                logger.info(f"✅ Trade tracker WebSocket connected")
                return
            except Exception as e:
                logger.warning(f"Trade tracker connection failed: {e}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, WS_RECONNECT_MAX_DELAY)
    
    async def track_token(self, mint: str, token_id: int):
        """Subscribe to trades for a specific token"""
        if mint in self.tracked_tokens or not self.websocket:
            return
        
        self.tracked_tokens.add(mint)
        self.token_to_id[mint] = token_id
        
        payload = {
            "method": "subscribeTokenTrade",
            "keys": [mint]
        }
        
        try:
            await self.websocket.send(json.dumps(payload))
            logger.info(f"📊 Subscribed to trades for {mint[:16]}... (token_id={token_id})")
        except Exception as e:
            logger.error(f"Failed to subscribe to {mint}: {e}")
            self.tracked_tokens.discard(mint)
            if mint in self.token_to_id:
                del self.token_to_id[mint]
    
    async def untrack_token(self, mint: str):
        """Remove a token from tracking"""
        if mint not in self.tracked_tokens:
            return
        
        self.tracked_tokens.discard(mint)
        if mint in self.token_to_id:
            del self.token_to_id[mint]
        
        logger.info(f"⏹️ Untracked {mint[:16]}... (idle or max capacity)")
    
    async def on_trade_event(self, event: dict):
        """Handle incoming trade event — feeds momentum engine + updates market cap in DB + stores raw event"""
        mint = event.get("mint", "")
        if mint not in self.tracked_tokens:
            return
        
        token_id = self.token_to_id[mint]
        
        try:
            amount_sol = float(event.get("solAmount", 0))
            tx_type = event.get("txType", "").lower()
            direction = "buy" if tx_type == "buy" else "sell"
            trader = event.get("traderPublicKey", "unknown")
            timestamp = datetime.utcnow()
            
            # Add to momentum engine (include market cap for degradation price tracking)
            mc_sol = float(event.get("marketCapSol", 0) or 0)
            momentum_engine.add_trade(token_id, trader, amount_sol, direction, timestamp, mc_sol)
            
            # Store complete raw trade event in token_events table
            try:
                raw_event_json = json.dumps(event)
                event_type = tx_type if tx_type in ('buy', 'sell') else 'buy'
                pool = await _get_trade_db_pool()
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO token_events (token_id, event_type, raw_event)
                        VALUES ($1, $2, $3::jsonb)
                        """,
                        token_id,
                        event_type,
                        raw_event_json,
                    )
            except Exception as e:
                logger.debug(f"Raw event storage error for {mint[:16]}: {e}")
            
            # Update market cap in DB if present in event
            market_cap_sol = event.get("marketCapSol")
            if market_cap_sol is not None:
                try:
                    market_cap_sol = float(market_cap_sol)
                    # Get SOL price from the websocket scanner's cache
                    from src.tasks.websocket_scanner import _sol_price_usd
                    sol_price = _sol_price_usd if _sol_price_usd > 0 else 140.0
                    market_cap_usd = market_cap_sol * sol_price
                    
                    # Update DB (fire-and-forget, batched via pool)
                    pool = await _get_trade_db_pool()
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE tokens SET market_cap = $1, updated_at = NOW() WHERE id = $2",
                            market_cap_usd, token_id
                        )
                    
                    # Broadcast market cap update via SSE
                    from src.routers.sse import broadcast_market_cap_update
                    await broadcast_market_cap_update(token_id, market_cap_usd)
                    
                except Exception as e:
                    logger.debug(f"Market cap update error for {mint[:16]}: {e}")
        except Exception as e:
            logger.error(f"Error processing trade for {mint}: {e}")
    
    def get_tracked_count(self) -> int:
        """Get number of actively tracked tokens"""
        return len(self.tracked_tokens)
    
    async def listen(self):
        """Listen for trade events from WebSocket"""
        while self.running:
            try:
                async for message in self.websocket:
                    try:
                        event = json.loads(message)
                        await self.on_trade_event(event)
                    except json.JSONDecodeError:
                        pass
                    except Exception as e:
                        logger.error(f"Error in trade listener: {e}")
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Trade tracker connection closed. Reconnecting...")
                await self.connect()
            except Exception as e:
                logger.error(f"Trade tracker listener error: {e}")
                await asyncio.sleep(5)

trade_tracker = TradeTracker()

async def start_trade_tracker():
    """Start trade tracker task"""
    trade_tracker.running = True
    try:
        await trade_tracker.connect()
        await trade_tracker.listen()
    except Exception as e:
        logger.error(f"Trade tracker failed: {e}")
    finally:
        trade_tracker.running = False
