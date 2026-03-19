"""API routes for signals and tokens"""
from fastapi import APIRouter, Query
from typing import List, Optional
import asyncpg
from src.config import get_settings
from src.utils.logger import setup_logger

router = APIRouter()
logger = setup_logger("api")
settings = get_settings()

async def get_db():
    """Get DB connection"""
    return await asyncpg.connect(settings.database_url)

@router.get("/signals")
async def get_signals(limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0)):
    """Get recent signals sorted by score (descending)"""
    try:
        conn = await get_db()
        signals = await conn.fetch(
            """
            SELECT s.id, s.token_id, s.score, s.narrative_type, s.risk_level,
                   t.name, t.symbol, t.mint, t.market_cap
            FROM signals s
            JOIN tokens t ON s.token_id = t.id
            ORDER BY s.score DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset
        )
        await conn.close()
        
        return {
            "signals": [dict(s) for s in signals],
            "count": len(signals)
        }
    except Exception as e:
        logger.error(f"Error fetching signals: {e}")
        return {"error": str(e), "signals": []}

@router.get("/signals/top")
async def get_top_signals(limit: int = Query(10, ge=1, le=100)):
    """Get top scoring signals this hour"""
    try:
        conn = await get_db()
        signals = await conn.fetch(
            """
            SELECT s.id, s.token_id, s.score, s.narrative_type, s.risk_level,
                   t.name, t.symbol, t.mint, t.market_cap, t.volume_24h
            FROM signals s
            JOIN tokens t ON s.token_id = t.id
            WHERE s.created_at > NOW() - INTERVAL '1 hour'
            ORDER BY s.score DESC
            LIMIT $1
            """,
            limit
        )
        await conn.close()
        
        return {
            "signals": [dict(s) for s in signals],
            "count": len(signals)
        }
    except Exception as e:
        logger.error(f"Error fetching top signals: {e}")
        return {"error": str(e), "signals": []}

@router.get("/tokens")
async def get_tokens(limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0)):
    """Get all tracked tokens"""
    try:
        conn = await get_db()
        tokens = await conn.fetch(
            """
            SELECT id, mint, name, symbol, market_cap, volume_24h, holders, created_at
            FROM tokens
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset
        )
        await conn.close()
        
        return {
            "tokens": [dict(t) for t in tokens],
            "count": len(tokens)
        }
    except Exception as e:
        logger.error(f"Error fetching tokens: {e}")
        return {"error": str(e), "tokens": []}

@router.get("/tokens/{ca}")
async def get_token(ca: str):
    """Get single token by contract address (CA)"""
    try:
        conn = await get_db()
        token = await conn.fetchrow(
            "SELECT * FROM tokens WHERE mint = $1",
            ca
        )
        await conn.close()
        
        if not token:
            return {"error": "Token not found"}
        
        return dict(token)
    except Exception as e:
        logger.error(f"Error fetching token: {e}")
        return {"error": str(e)}

@router.get("/settings")
async def get_settings_endpoint():
    """Get current scanner settings"""
    try:
        conn = await get_db()
        settings_list = await conn.fetch("SELECT key, value FROM settings")
        await conn.close()
        
        return {dict(s) for s in settings_list}
    except Exception as e:
        logger.error(f"Error fetching settings: {e}")
        return {"error": str(e)}

@router.post("/settings")
async def update_settings(key: str, value: str):
    """Update a setting"""
    try:
        conn = await get_db()
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = $2",
            key, value
        )
        await conn.close()
        
        return {"status": "updated", "key": key, "value": value}
    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        return {"error": str(e)}
