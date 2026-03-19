"""Moralis API integration with 3-tier fallback"""
import requests
import asyncio
from typing import List, Dict, Optional
from src.config import get_settings
from src.utils.logger import setup_logger
from datetime import datetime, timedelta

logger = setup_logger("moralis")
settings = get_settings()

class MoralisService:
    """3-tier API fallback: Moralis → DexScreener → Pump.fun"""
    
    def __init__(self):
        self.moralis_headers = {
            "Authorization": f"Bearer {settings.moralis_api_key}",
            "Accept": "application/json"
        }
        self.dex_headers = {
            "User-Agent": "Mozilla/5.0 (Pump Signal)"
        }
        self.pump_headers = {
            "User-Agent": "Mozilla/5.0 (Pump Signal)",
            "Referer": "https://pump.fun/"
        }
    
    async def get_graduated_tokens(self) -> List[Dict]:
        """
        Get graduated Pump.fun tokens (CA ends with 'pump')
        Returns list of token dicts with: mint, name, symbol, market_cap, volume_24h, holders
        """
        
        # Tier 1: Moralis API
        try:
            logger.info("Tier 1: Fetching from Moralis API...")
            tokens = await self._fetch_moralis_graduated()
            if tokens:
                logger.info(f"✅ Moralis returned {len(tokens)} graduated tokens")
                return tokens
        except Exception as e:
            logger.warning(f"Moralis API failed: {str(e)[:100]}")
        
        # Tier 2: DexScreener fallback
        try:
            logger.info("Tier 2: Falling back to DexScreener...")
            tokens = await self._fetch_dexscreener_pump()
            if tokens:
                logger.info(f"✅ DexScreener returned {len(tokens)} tokens")
                return tokens
        except Exception as e:
            logger.warning(f"DexScreener fallback failed: {str(e)[:100]}")
        
        # Tier 3: Pump.fun direct API
        try:
            logger.info("Tier 3: Falling back to Pump.fun direct...")
            tokens = await self._fetch_pump_direct()
            if tokens:
                logger.info(f"✅ Pump.fun direct returned {len(tokens)} tokens")
                return tokens
        except Exception as e:
            logger.warning(f"Pump.fun direct failed: {str(e)[:100]}")
        
        logger.error("All 3 API tiers failed!")
        return []
    
    async def _fetch_moralis_graduated(self) -> List[Dict]:
        """Fetch from Moralis /tokens endpoint"""
        try:
            # Moralis endpoint for Pump.fun graduated tokens
            url = f"{settings.moralis_api_url}/tokens"
            params = {
                "chain": "solana",
                "filter": "pump.fun",
                "limit": 100
            }
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(url, headers=self.moralis_headers, params=params, timeout=10)
            )
            response.raise_for_status()
            
            data = response.json()
            tokens = data.get("result", []) or data.get("data", []) or []
            
            # Filter for CA ending with "pump"
            filtered = [t for t in tokens if t.get("address", "").endswith("pump")]
            return filtered
        except Exception as e:
            logger.error(f"Moralis error: {e}")
            raise
    
    async def _fetch_dexscreener_pump(self) -> List[Dict]:
        """Fallback: DexScreener for Solana Pump.fun tokens"""
        try:
            url = f"{settings.dexscreener_api_url}/token/solana"
            
            # Get recent Pump.fun launches from DexScreener
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(url, headers=self.dex_headers, timeout=10)
            )
            response.raise_for_status()
            
            data = response.json()
            pairs = data.get("pairs", [])
            
            tokens = []
            for pair in pairs[:50]:  # Top 50
                if "pump" in pair.get("baseToken", {}).get("address", "").lower():
                    tokens.append({
                        "mint": pair["baseToken"]["address"],
                        "name": pair["baseToken"]["name"],
                        "symbol": pair["baseToken"]["symbol"],
                        "market_cap": float(pair.get("marketCap", 0)),
                        "volume_24h": float(pair.get("volume", {}).get("h24", 0)),
                        "holders": pair.get("holders"),
                    })
            
            return tokens
        except Exception as e:
            logger.error(f"DexScreener error: {e}")
            raise
    
    async def _fetch_pump_direct(self) -> List[Dict]:
        """Last resort: Direct Pump.fun API"""
        try:
            url = f"{settings.pump_fun_api_url}/coins"
            params = {"limit": 100, "sort": "last_tx_timestamp", "order": "DESC"}
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(url, params=params, headers=self.pump_headers, timeout=10)
            )
            response.raise_for_status()
            
            coins = response.json()
            if not isinstance(coins, list):
                coins = coins.get("data", [])
            
            # Filter for high market cap (completed bonding)
            tokens = [
                {
                    "mint": c.get("mint"),
                    "name": c.get("name"),
                    "symbol": c.get("symbol"),
                    "market_cap": float(c.get("market_cap", 0)),
                    "volume_24h": float(c.get("volume_24h", 0)),
                    "holders": c.get("holders"),
                    "created_timestamp": c.get("created_timestamp"),
                    "last_tx_timestamp": c.get("last_tx_timestamp"),
                }
                for c in coins
                if c.get("mint", "").endswith("pump") and float(c.get("market_cap", 0)) > 10000
            ]
            
            return tokens
        except Exception as e:
            logger.error(f"Pump.fun direct error: {e}")
            raise
    
    async def enrich_token_data(self, token: Dict) -> Dict:
        """Enrich token data with additional metrics"""
        # Already has basic data from API
        # Add derived metrics
        
        market_cap = float(token.get("market_cap", 0))
        volume_24h = float(token.get("volume_24h", 0))
        holders = int(token.get("holders", 0))
        
        # Calculate liquidity ratio
        liquidity_ratio = (volume_24h / market_cap * 100) if market_cap > 0 else 0
        
        # Calculate age
        created_ts = token.get("created_timestamp")
        age_hours = 0
        if created_ts:
            try:
                created_dt = datetime.fromisoformat(str(created_ts).replace('Z', '+00:00'))
                age_hours = (datetime.utcnow() - created_dt).total_seconds() / 3600
            except:
                pass
        
        token["liquidity_ratio"] = liquidity_ratio
        token["age_hours"] = age_hours
        
        return token

moralis = MoralisService()
