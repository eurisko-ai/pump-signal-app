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
    """Get all tokens (ordered by newest) with image_url and market_cap"""
    try:
        conn = await get_db()
        logger.info("Getting active tokens...")
        
        tokens = await conn.fetch(
            """
            SELECT id, mint, name, symbol, image_url, market_cap, created_at
            FROM tokens
            ORDER BY created_at DESC
            LIMIT 50
            """
        )
        
        logger.info(f"Found {len(tokens)} tokens")
        await conn.close()
        
        return [
            {
                "id": t["id"],
                "mint": t["mint"],
                "name": t["name"],
                "symbol": t["symbol"],
                "image_url": t["image_url"],
                "market_cap": t["market_cap"],
                "status": "active"
            }
            for t in tokens
        ]
    except Exception as e:
        logger.error(f"Error fetching active tokens: {e}", exc_info=True)
        return {"error": str(e)}

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

        # New pairs in last 15 mins
        fifteen_mins_ago = datetime.utcnow() - timedelta(minutes=15)
        new_pairs_15m = await conn.fetchval(
            "SELECT COUNT(*) FROM tokens WHERE created_at > $1",
            fifteen_mins_ago
        )
        
        await conn.close()
        
        return {
            "total_tokens": total_tokens or 0,
            "good_signals": good_signals or 0,
            "signals_today": signals_today or 0,
            "active_tracked": len(momentum_engine.token_trades) if hasattr(momentum_engine, 'token_trades') else 0,
            "new_pairs_15m": new_pairs_15m or 0
        }
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# FILTERED TOKENS
# ============================================================================
@router.get("/tokens/filtered")
async def get_filtered_tokens(
    age: Optional[str] = Query(None),
    min_bonding: Optional[float] = Query(None),
    max_bonding: Optional[float] = Query(None),
    min_mc: Optional[float] = Query(None),
    max_mc: Optional[float] = Query(None),
    min_signal: Optional[int] = Query(None)
):
    """Get tokens with advanced filtering"""
    try:
        conn = await get_db()
        
        query = """
            SELECT t.id, t.mint, t.name, t.symbol, t.image_url, t.market_cap, t.created_at,
                   COALESCE(s.score, 0) as signal_score,
                   COALESCE(s.narrative_type, 'NONE') as signal_badge
            FROM tokens t
            LEFT JOIN signals s ON s.token_id = t.id AND s.id = (
                SELECT id FROM signals WHERE token_id = t.id ORDER BY created_at DESC LIMIT 1
            )
            WHERE 1=1
        """
        params = []
        param_idx = 1
        
        if age:
            if age == "0-5":
                query += f" AND t.created_at > NOW() - INTERVAL '5 minutes'"
            elif age == "5-15":
                query += f" AND t.created_at <= NOW() - INTERVAL '5 minutes' AND t.created_at > NOW() - INTERVAL '15 minutes'"
            elif age == "15-60":
                query += f" AND t.created_at <= NOW() - INTERVAL '15 minutes' AND t.created_at > NOW() - INTERVAL '60 minutes'"
        
        if min_mc is not None:
            query += f" AND t.market_cap >= ${param_idx}"
            params.append(min_mc)
            param_idx += 1
            
        if max_mc is not None:
            query += f" AND t.market_cap <= ${param_idx}"
            params.append(max_mc)
            param_idx += 1
            
        if min_signal is not None:
            query += f" AND COALESCE(s.score, 0) >= ${param_idx}"
            params.append(min_signal)
            param_idx += 1
            
        query += " ORDER BY t.created_at DESC LIMIT 100"
        
        tokens = await conn.fetch(query, *params)
        await conn.close()
        
        return [
            {
                "id": t["id"],
                "mint": t["mint"],
                "name": t["name"],
                "symbol": t["symbol"],
                "image_url": t["image_url"],
                "market_cap": t["market_cap"],
                "created_at": t["created_at"].isoformat(),
                "signal_score": t["signal_score"],
                "signal_badge": "STRONG_BUY" if t["signal_score"] >= 80 else "BUY" if t["signal_score"] >= 60 else "NEUTRAL" if t["signal_score"] >= 40 else "NONE",
                "status": "active"
            }
            for t in tokens
        ]
    except Exception as e:
        logger.error(f"Error fetching filtered tokens: {e}")
        return {"error": str(e)}
