"""Frontend API endpoints for dashboard"""
from fastapi import APIRouter, HTTPException, Query
import asyncpg
from datetime import datetime, timedelta
from src.config import get_settings
from src.utils.logger import setup_logger
from src.services.momentum_engine import momentum_engine

logger = setup_logger("frontend_api")
settings = get_settings()

router = APIRouter(prefix="/api", tags=["frontend"])

async def get_db():
    return await asyncpg.connect(settings.database_url)

# ============================================================================
# ACTIVE TOKENS
# ============================================================================
@router.get("/tokens/active")
async def get_active_tokens():
    """Get all active/recently created tokens"""
    try:
        conn = await get_db()
        
        # Get tokens created in last 2 hours
        two_hours_ago = datetime.utcnow() - timedelta(hours=2)
        
        tokens = await conn.fetch(
            """
            SELECT id, mint, name, symbol, created_at,
                   CASE 
                       WHEN created_at < NOW() - INTERVAL '2 minutes' THEN 'migrated'
                       ELSE 'detecting'
                   END as status
            FROM tokens
            WHERE created_at > $1
            ORDER BY created_at DESC
            LIMIT 50
            """,
            two_hours_ago
        )
        
        await conn.close()
        
        return [
            {
                "id": t["id"],
                "mint": t["mint"],
                "name": t["name"],
                "symbol": t["symbol"],
                "created_at": t["created_at"].isoformat(),
                "status": t["status"]
            }
            for t in tokens
        ]
    except Exception as e:
        logger.error(f"Error fetching active tokens: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# TOKEN METRICS
# ============================================================================
@router.get("/tokens/{token_id}/metrics")
async def get_token_metrics(token_id: int):
    """Get real-time momentum metrics for a token"""
    try:
        if token_id not in momentum_engine.token_trades:
            return {
                "momentum_1s": 0,
                "momentum_15s": 0,
                "momentum_30s": 0,
                "momentum_1m": 0,
                "whale_concentration": 0,
                "pump_signal": 0,
                "is_hot": False,
                "last_updated": datetime.utcnow().isoformat()
            }
        
        metrics = momentum_engine.get_all_metrics(token_id)
        
        return {
            "momentum_1s": metrics.get("momentum_1s", 0),
            "momentum_15s": metrics.get("momentum_15s", 0),
            "acceleration_15s": metrics.get("acceleration_15s", 1.0),
            "momentum_30s": metrics.get("momentum_30s", 0),
            "momentum_1m": metrics.get("momentum_1m", 0),
            "whale_concentration": metrics.get("whale_concentration", 0),
            "pump_signal": metrics.get("pump_signal", 0),
            "is_hot": metrics.get("is_hot", False),
            "is_whale_dump": metrics.get("is_whale_dump", False),
            "last_updated": metrics.get("timestamp", datetime.utcnow()).isoformat()
        }
    except Exception as e:
        logger.error(f"Error fetching metrics for token {token_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# SIGNAL HISTORY
# ============================================================================
@router.get("/signals/history")
async def get_signal_history(
    limit: int = Query(100, le=500),
    signal_type: str = Query(None)  # BUY, SELL, PUMP, WHALE_DUMP, or None for all
):
    """Get historical signals"""
    try:
        conn = await get_db()
        
        signals = await conn.fetch(
            """
            SELECT s.id, s.token_id, s.created_at, t.name, t.symbol, t.mint, s.score
            FROM signals s
            JOIN tokens t ON s.token_id = t.id
            ORDER BY s.created_at DESC
            LIMIT $1
            """,
            limit
        )
        
        await conn.close()
        
        return [
            {
                "id": s["id"],
                "token_id": s["token_id"],
                "name": s["name"],
                "symbol": s["symbol"],
                "mint": s["mint"],
                "score": s["score"],
                "created_at": s["created_at"].isoformat()
            }
            for s in signals
        ]
    except Exception as e:
        logger.error(f"Error fetching signal history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# ACTIVE SIGNALS
# ============================================================================
@router.get("/signals/active")
async def get_active_signals():
    """Get currently active signals (high-scoring recent tokens)"""
    try:
        conn = await get_db()
        
        # Get recent high-scoring signals
        high_score_signals = await conn.fetch(
            """
            SELECT s.id, s.token_id, s.created_at, s.score, t.name, t.symbol, t.mint
            FROM signals s
            JOIN tokens t ON s.token_id = t.id
            WHERE s.score >= $1 AND s.created_at > NOW() - INTERVAL '1 hour'
            ORDER BY s.created_at DESC
            LIMIT 50
            """,
            settings.alert_threshold
        )
        
        await conn.close()
        
        def format_signals(signals):
            return [
                {
                    "id": s["id"],
                    "token_id": s["token_id"],
                    "name": s["name"],
                    "symbol": s["symbol"],
                    "mint": s["mint"],
                    "score": s["score"],
                    "created_at": s["created_at"].isoformat()
                }
                for s in signals
            ]
        
        return {
            "high_score": format_signals(high_score_signals),
            "total_active": len(high_score_signals)
        }
    except Exception as e:
        logger.error(f"Error fetching active signals: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# DASHBOARD STATS
# ============================================================================
@router.get("/stats/dashboard")
async def get_dashboard_stats():
    """Get overall dashboard statistics"""
    try:
        conn = await get_db()
        
        # Total tokens tracked
        total_tokens = await conn.fetchval("SELECT COUNT(*) FROM tokens")
        
        # Tokens with score >= 70 (good signals)
        good_signals = await conn.fetchval(
            "SELECT COUNT(*) FROM signals WHERE score >= $1",
            settings.alert_threshold
        )
        
        # Signals created today
        today = datetime.utcnow().date()
        signals_today = await conn.fetchval(
            "SELECT COUNT(*) FROM signals WHERE DATE(created_at) = $1",
            today
        )
        
        await conn.close()
        
        return {
            "total_tokens": total_tokens or 0,
            "good_signals": good_signals or 0,
            "signals_today": signals_today or 0,
            "active_tracked": len(momentum_engine.token_trades) if hasattr(momentum_engine, 'token_trades') else 0
        }
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))
