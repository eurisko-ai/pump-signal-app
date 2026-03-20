"""Frontend API endpoints for dashboard"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import asyncpg
import json
from datetime import datetime, timedelta
from src.config import get_settings
from src.utils.logger import setup_logger
from src.services.momentum_engine import momentum_engine

logger = setup_logger("frontend_api")
settings = get_settings()

router = APIRouter(prefix="/api", tags=["frontend"])

# Bonding curve constants (Pump.fun)
INITIAL_VIRTUAL_SOL = 30.0  # starting vSol
BONDING_COMPLETE_SOL = 85.0  # ~85 SOL = 100% bonded
TOTAL_TOKEN_SUPPLY = 1_000_000_000  # 1B tokens

async def get_db():
    return await asyncpg.connect(settings.database_url)


def compute_bonding_curve_percent(v_sol: float) -> float:
    """Compute bonding curve progress from vSolInBondingCurve"""
    if not v_sol or v_sol <= INITIAL_VIRTUAL_SOL:
        return 0.0
    progress = ((v_sol - INITIAL_VIRTUAL_SOL) / (BONDING_COMPLETE_SOL - INITIAL_VIRTUAL_SOL)) * 100
    return min(max(progress, 0.0), 100.0)


def compute_dev_holding_percent(create_event: dict, latest_buy_event: dict = None) -> float:
    """Estimate dev holding from initial buy vs total supply"""
    if not create_event:
        return 0.0
    initial_buy = create_event.get("initialBuy", 0)
    if initial_buy and TOTAL_TOKEN_SUPPLY > 0:
        return (initial_buy / TOTAL_TOKEN_SUPPLY) * 100
    return 0.0


def compute_market_cap_usd(market_cap_sol: float, sol_price_usd: float = 140.0) -> float:
    """Convert SOL market cap to USD"""
    if not market_cap_sol:
        return 0.0
    return market_cap_sol * sol_price_usd


# ============================================================================
# ACTIVE TOKENS - RICH DATA
# ============================================================================
@router.get("/tokens/active")
async def get_active_tokens():
    """Get all tokens with computed metrics for the dashboard"""
    try:
        conn = await get_db()
        logger.info("Getting active tokens with full metrics...")

        # Get tokens with buy/sell counts and latest event data
        tokens = await conn.fetch(
            """
            SELECT 
                t.id, t.mint, t.name, t.symbol, t.image_url, t.market_cap, 
                t.holders, t.created_at, t.description, t.raw_create_event,
                COALESCE((SELECT COUNT(*) FROM token_events WHERE token_id=t.id AND event_type='buy'), 0) as buy_count,
                COALESCE((SELECT COUNT(*) FROM token_events WHERE token_id=t.id AND event_type='sell'), 0) as sell_count,
                (SELECT raw_event FROM token_events WHERE token_id=t.id AND event_type='buy' ORDER BY id DESC LIMIT 1) as latest_buy_event,
                (SELECT raw_event FROM token_events WHERE token_id=t.id AND event_type='create' LIMIT 1) as create_event,
                (SELECT created_at FROM token_events WHERE token_id=t.id AND event_type='buy' ORDER BY id DESC LIMIT 1) as last_trade_at
            FROM tokens t
            ORDER BY t.created_at DESC
            LIMIT 200
            """
        )

        logger.info(f"Found {len(tokens)} tokens")

        result = []
        for t in tokens:
            # Parse events
            create_event = {}
            latest_buy = {}
            if t["create_event"]:
                try:
                    create_event = json.loads(t["create_event"]) if isinstance(t["create_event"], str) else t["create_event"]
                except:
                    pass
            if t["latest_buy_event"]:
                try:
                    latest_buy = json.loads(t["latest_buy_event"]) if isinstance(t["latest_buy_event"], str) else t["latest_buy_event"]
                except:
                    pass

            # Compute bonding curve from latest event
            v_sol = latest_buy.get("vSolInBondingCurve") or create_event.get("vSolInBondingCurve", 0)
            bonding_pct = compute_bonding_curve_percent(v_sol)

            # Dev holding
            dev_pct = compute_dev_holding_percent(create_event)

            # Market cap from latest event
            mc_sol = latest_buy.get("marketCapSol") or create_event.get("marketCapSol", 0)
            mc_usd = compute_market_cap_usd(mc_sol)

            # Extract social links from IPFS metadata URI 
            uri = create_event.get("uri", "")
            creator = create_event.get("traderPublicKey", "")

            # Determine status
            is_migrated = bonding_pct >= 100
            
            # Compute top holder % (approximate from newTokenBalance if available)
            top_holder_pct = 0.0
            if latest_buy.get("newTokenBalance") and TOTAL_TOKEN_SUPPLY > 0:
                top_holder_pct = (latest_buy["newTokenBalance"] / TOTAL_TOKEN_SUPPLY) * 100
                top_holder_pct = min(top_holder_pct, 100.0)

            # Signal score (simplified)
            score = 0
            if bonding_pct >= 100:
                score += 30
            elif bonding_pct >= 80:
                score += 20
            elif bonding_pct >= 50:
                score += 10
            
            buy_count = t["buy_count"] or 0
            sell_count = t["sell_count"] or 0
            
            if buy_count > 100:
                score += 20
            elif buy_count > 50:
                score += 15
            elif buy_count > 20:
                score += 10
            elif buy_count > 5:
                score += 5
            
            if mc_usd > 50000:
                score += 15
            elif mc_usd > 10000:
                score += 10
            elif mc_usd > 5000:
                score += 5
            
            if dev_pct < 2:
                score += 10
            elif dev_pct < 5:
                score += 5
            
            if top_holder_pct < 5:
                score += 5

            # Signal badge
            if score >= 70:
                badge = "STRONG_BUY"
            elif score >= 50:
                badge = "BUY"
            elif score >= 30:
                badge = "NEUTRAL"
            else:
                badge = "NONE"

            # Determine creator activity from events
            creator_activity = "unknown"
            creator_holding = dev_pct
            if creator and latest_buy.get("traderPublicKey") == creator:
                # Last trade was by the creator
                last_type = latest_buy.get("txType", "")
                if last_type == "buy":
                    creator_activity = "buying"
                elif last_type == "sell":
                    creator_activity = "selling"
                else:
                    creator_activity = "holding"
            elif creator:
                creator_activity = "holding"

            # Build top 10 holders approximation
            top_10_holders = []
            if creator and dev_pct > 0:
                top_10_holders.append({
                    "address": creator,
                    "percent": round(dev_pct, 2),
                    "is_creator": True,
                })
            if top_holder_pct > 0 and latest_buy.get("traderPublicKey"):
                trader = latest_buy["traderPublicKey"]
                if trader != creator:
                    top_10_holders.append({
                        "address": trader,
                        "percent": round(top_holder_pct, 2),
                        "is_creator": False,
                    })

            token_data = {
                "id": t["id"],
                "mint": t["mint"],
                "name": t["name"],
                "symbol": t["symbol"],
                "image_url": t["image_url"],
                "market_cap": mc_usd if mc_usd > 0 else t["market_cap"],
                "market_cap_sol": mc_sol,
                "holders": t["holders"] or 0,
                "created_at": t["created_at"].isoformat() if t["created_at"] else datetime.utcnow().isoformat(),
                "status": "migrated" if is_migrated else "active",
                "bonding_curve_percent": round(bonding_pct, 1),
                "dev_holding_percent": round(dev_pct, 2),
                "top_holder_percent": round(top_holder_pct, 2),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "signal_score": score,
                "signal_badge": badge,
                "creator_address": creator,
                "creator_holding_percent": round(creator_holding, 2),
                "creator_activity": creator_activity,
                "top_10_holders": top_10_holders,
                "metadata_uri": uri,
                "description": t["description"] or "",
                "last_trade_at": t["last_trade_at"].isoformat() if t["last_trade_at"] else None,
                # Social links (will be empty for now, could be fetched from IPFS)
                "twitter": None,
                "telegram": None,
                "website": None,
            }
            result.append(token_data)

        await conn.close()
        return result

    except Exception as e:
        logger.error(f"Error fetching active tokens: {e}", exc_info=True)
        return []


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
    signal_type: str = Query(None)
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

        total_tokens = await conn.fetchval("SELECT COUNT(*) FROM tokens")

        # Count tokens with high bonding curve (>80%)
        # We'll estimate from events
        good_signals = await conn.fetchval(
            "SELECT COUNT(*) FROM signals WHERE score >= $1",
            settings.alert_threshold
        )

        today = datetime.utcnow().date()
        signals_today = await conn.fetchval(
            "SELECT COUNT(*) FROM signals WHERE DATE(created_at) = $1",
            today
        )

        fifteen_mins_ago = datetime.utcnow() - timedelta(minutes=15)
        new_pairs_15m = await conn.fetchval(
            "SELECT COUNT(*) FROM tokens WHERE created_at > $1",
            fifteen_mins_ago
        )

        # Count tokens created in last hour
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        graduated_1h = await conn.fetchval(
            "SELECT COUNT(*) FROM tokens WHERE created_at > $1",
            one_hour_ago
        )

        await conn.close()

        return {
            "total_tokens": total_tokens or 0,
            "good_signals": good_signals or 0,
            "signals_today": signals_today or 0,
            "active_tracked": len(momentum_engine.token_trades) if hasattr(momentum_engine, 'token_trades') else 0,
            "new_pairs_15m": new_pairs_15m or 0,
            "graduated_1h": graduated_1h or 0
        }
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# TOKEN STATS (counts + signal breakdowns)
# ============================================================================
@router.get("/tokens/stats")
async def get_token_stats():
    """Get token counts broken down by category and signal badge"""
    try:
        conn = await get_db()

        # Fetch all tokens with the same scoring logic as /tokens/active
        tokens = await conn.fetch(
            """
            SELECT 
                t.id, t.mint, t.name, t.symbol, t.market_cap, t.holders,
                t.raw_create_event,
                COALESCE((SELECT COUNT(*) FROM token_events WHERE token_id=t.id AND event_type='buy'), 0) as buy_count,
                COALESCE((SELECT COUNT(*) FROM token_events WHERE token_id=t.id AND event_type='sell'), 0) as sell_count,
                (SELECT raw_event FROM token_events WHERE token_id=t.id AND event_type='buy' ORDER BY id DESC LIMIT 1) as latest_buy_event,
                (SELECT raw_event FROM token_events WHERE token_id=t.id AND event_type='create' LIMIT 1) as create_event
            FROM tokens t
            ORDER BY t.created_at DESC
            LIMIT 500
            """
        )
        await conn.close()

        # Initialize counters
        by_signal = {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0}
        by_category = {"new_pairs": 0, "final_stretch": 0, "migrated": 0}
        by_category_and_signal = {
            "new_pairs": {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0},
            "final_stretch": {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0},
            "migrated": {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0},
        }

        for t in tokens:
            create_event = {}
            latest_buy = {}
            if t["create_event"]:
                try:
                    create_event = json.loads(t["create_event"]) if isinstance(t["create_event"], str) else t["create_event"]
                except:
                    pass
            if t["latest_buy_event"]:
                try:
                    latest_buy = json.loads(t["latest_buy_event"]) if isinstance(t["latest_buy_event"], str) else t["latest_buy_event"]
                except:
                    pass

            v_sol = latest_buy.get("vSolInBondingCurve") or create_event.get("vSolInBondingCurve", 0)
            bonding_pct = compute_bonding_curve_percent(v_sol)
            dev_pct = compute_dev_holding_percent(create_event)
            mc_sol = latest_buy.get("marketCapSol") or create_event.get("marketCapSol", 0)
            mc_usd = compute_market_cap_usd(mc_sol)

            top_holder_pct = 0.0
            if latest_buy.get("newTokenBalance") and TOTAL_TOKEN_SUPPLY > 0:
                top_holder_pct = (latest_buy["newTokenBalance"] / TOTAL_TOKEN_SUPPLY) * 100
                top_holder_pct = min(top_holder_pct, 100.0)

            # Compute score (same logic as active tokens)
            score = 0
            if bonding_pct >= 100:
                score += 30
            elif bonding_pct >= 80:
                score += 20
            elif bonding_pct >= 50:
                score += 10

            buy_count = t["buy_count"] or 0
            if buy_count > 100:
                score += 20
            elif buy_count > 50:
                score += 15
            elif buy_count > 20:
                score += 10
            elif buy_count > 5:
                score += 5

            if mc_usd > 50000:
                score += 15
            elif mc_usd > 10000:
                score += 10
            elif mc_usd > 5000:
                score += 5

            if dev_pct < 2:
                score += 10
            elif dev_pct < 5:
                score += 5

            if top_holder_pct < 5:
                score += 5

            # Badge
            if score >= 70:
                badge = "strong_buy"
            elif score >= 50:
                badge = "buy"
            elif score >= 30:
                badge = "neutral"
            else:
                badge = "none"

            # Category
            if bonding_pct >= 100:
                cat = "migrated"
            elif bonding_pct >= 80:
                cat = "final_stretch"
            else:
                cat = "new_pairs"

            by_signal[badge] += 1
            by_category[cat] += 1
            by_category_and_signal[cat][badge] += 1

        total = len(tokens)

        return {
            "total_tokens": total,
            "by_signal": by_signal,
            "by_category": by_category,
            "by_category_and_signal": by_category_and_signal,
        }

    except Exception as e:
        logger.error(f"Error fetching token stats: {e}", exc_info=True)
        return {
            "total_tokens": 0,
            "by_signal": {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0},
            "by_category": {"new_pairs": 0, "final_stretch": 0, "migrated": 0},
            "by_category_and_signal": {
                "new_pairs": {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0},
                "final_stretch": {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0},
                "migrated": {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0},
            },
        }


# ============================================================================
# FILTERED TOKENS
# ============================================================================
@router.get("/tokens/filtered")
async def get_filtered_tokens(
    signal_min: Optional[int] = Query(None, description="Min signal score"),
    dev_holding_max: Optional[float] = Query(None, description="Max dev holding %"),
    top_holder_max: Optional[float] = Query(None, description="Max top holder %"),
    bonding_curve_min: Optional[float] = Query(None, description="Min bonding curve %"),
    market_cap_min: Optional[float] = Query(None, description="Min market cap USD"),
    market_cap_max: Optional[float] = Query(None, description="Max market cap USD"),
    age_min: Optional[int] = Query(None, description="Min age in seconds"),
    age_max: Optional[int] = Query(None, description="Max age in seconds"),
):
    """Get tokens with server-side filtering"""
    all_tokens = await get_active_tokens()
    now = datetime.utcnow()
    filtered = []

    for t in all_tokens:
        # Signal score filter
        if signal_min is not None and (t.get("signal_score", 0) or 0) < signal_min:
            continue
        # Dev holding filter
        if dev_holding_max is not None and (t.get("dev_holding_percent", 0) or 0) > dev_holding_max:
            continue
        # Top holder filter
        if top_holder_max is not None and (t.get("top_holder_percent", 0) or 0) > top_holder_max:
            continue
        # Bonding curve filter
        if bonding_curve_min is not None and (t.get("bonding_curve_percent", 0) or 0) < bonding_curve_min:
            continue
        # Market cap filters
        mc = t.get("market_cap", 0) or 0
        if market_cap_min is not None and mc < market_cap_min:
            continue
        if market_cap_max is not None and mc > market_cap_max:
            continue
        # Age filters
        if age_min is not None or age_max is not None:
            try:
                created = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00").replace("+00:00", ""))
                age_s = (now - created).total_seconds()
            except:
                age_s = 0
            if age_min is not None and age_s < age_min:
                continue
            if age_max is not None and age_s > age_max:
                continue
        filtered.append(t)

    # Build stats for filtered set
    signal_counts = {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0}
    for t in filtered:
        badge = (t.get("signal_badge") or "NONE").lower()
        if badge in signal_counts:
            signal_counts[badge] += 1

    return {
        "tokens": filtered,
        "total": len(all_tokens),
        "filtered_count": len(filtered),
        "signal_counts": signal_counts,
    }
