"""Trade tracker - manages per-token subscriptions and feeds momentum engine"""
import asyncio
import json
from typing import Dict, Set
from src.config import get_settings
from src.services.momentum_engine import momentum_engine
from src.utils.logger import setup_logger

logger = setup_logger("trade_tracker")
settings = get_settings()

class TradeTracker:
    """Manage dynamic per-token trade subscriptions"""
    
    def __init__(self):
        self.tracked_tokens: Set[str] = set()  # Set of mints being tracked
        self.token_to_id: Dict[str, int] = {}  # {mint: token_id}
        self.websocket = None
    
    async def start(self, websocket):
        """Start tracking trades"""
        self.websocket = websocket
        logger.info("Trade tracker started")
    
    async def track_token(self, mint: str, token_id: int):
        """Subscribe to trades for a specific token"""
        if mint in self.tracked_tokens:
            return
        
        self.tracked_tokens.add(mint)
        self.token_to_id[mint] = token_id
        
        # Send subscription
        payload = {
            "method": "subscribeTokenTrade",
            "keys": [mint]
        }
        
        try:
            await self.websocket.send(json.dumps(payload))
            logger.info(f"Subscribed to trades for {mint[:16]}... (token_id={token_id})")
        except Exception as e:
            logger.error(f"Failed to subscribe to {mint}: {e}")
            self.tracked_tokens.discard(mint)
    
    async def untrack_token(self, mint: str):
        """Unsubscribe from a token's trades"""
        if mint not in self.tracked_tokens:
            return
        
        self.tracked_tokens.discard(mint)
        del self.token_to_id[mint]
        
        # Note: PumpPortal doesn't have unsubscribe, but we stop processing
        logger.info(f"Untracked {mint[:16]}... (idle)")
    
    async def on_trade_event(self, event: dict):
        """Handle incoming trade event"""
        mint = event.get("mint", "")
        if mint not in self.tracked_tokens:
            return
        
        token_id = self.token_to_id[mint]
        
        # Extract trade data
        amount_sol = float(event.get("solAmount", 0))
        is_buy = event.get("txType", "").lower() == "buy"
        is_whale = amount_sol > 0.5  # >0.5 SOL = whale
        
        # Add to momentum engine
        momentum_engine.add_trade(token_id, amount_sol, is_buy, is_whale)
    
    def get_tracked_count(self) -> int:
        """Get number of actively tracked tokens"""
        return len(self.tracked_tokens)
    
    async def cleanup_idle_tokens(self, max_idle_minutes: int = 5):
        """Remove tokens with no trades for N minutes"""
        momentum_engine.cleanup_old_tokens(max_idle_minutes)
        
        # Sync with momentum engine
        to_remove = [
            mint for mint in self.tracked_tokens
            if mint not in momentum_engine.token_last_seen or 
            (asyncio.get_event_loop().time() - momentum_engine.token_last_seen.get(mint, datetime.utcnow()).timestamp()) > max_idle_minutes * 60
        ]
        
        for mint in to_remove:
            await self.untrack_token(mint)

trade_tracker = TradeTracker()
