"""Frontend API endpoints for dashboard — v3 with activity-based scoring"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import asyncpg
import json
from datetime import datetime, timedelta
from src.config import get_settings
from src.utils.logger import setup_logger
from src.services.scoring import scoring_v3
from src.services.momentum_engine import momentum_engine
from src.services.signal_degradation import degradation_engine, apply_signal_degradation

logger = setup_logger("frontend_api")
settings = get_settings()

router = APIRouter(prefix="/api", tags=["frontend"])

# Bonding curve constants (Pump.fun)
INITIAL_VIRTUAL_SOL = 30.0
BONDING_COMPLETE_SOL = 85.0
TOTAL_TOKEN_SUPPLY = 1_000_000_000

async def get_db():
    return await asyncpg.connect(settings.database_url)


def compute_bonding_curve_percent(v_sol: float) -> float:
    if not v_sol or v_sol <= INITIAL_VIRTUAL_SOL:
        return 0.0
    progress = ((v_sol - INITIAL_VIRTUAL_SOL) / (BONDING_COMPLETE_SOL - INITIAL_VIRTUAL_SOL)) * 100
    return min(max(progress, 0.0), 100.0)


def compute_dev_holding_percent(create_event: dict) -> float:
    if not create_event:
        return 0.0
    initial_buy = create_event.get("initialBuy", 0)
    if initial_buy and TOTAL_TOKEN_SUPPLY > 0:
        return (initial_buy / TOTAL_TOKEN_SUPPLY) * 100
    return 0.0


def compute_market_cap_usd(market_cap_sol: float, sol_price_usd: float = 140.0) -> float:
    if not market_cap_sol:
        return 0.0
    return market_cap_sol * sol_price_usd


def compute_buy_sell_volume(conn_rows) -> dict:
    """Compute buy/sell volume from token events. solAmount is already in SOL (not lamports)."""
    buy_vol = 0.0
    sell_vol = 0.0
    buy_count = 0
    sell_count = 0
    for r in conn_rows:
        evt = r
        if isinstance(evt, dict):
            amount = float(evt.get("solAmount", 0) or 0)
            # solAmount is already in SOL units from pump.fun websocket
            if evt.get("txType") == "buy":
                buy_vol += amount
                buy_count += 1
            elif evt.get("txType") == "sell":
                sell_vol += amount
                sell_count += 1
    return {
        "buy_volume_sol": buy_vol,
        "sell_volume_sol": sell_vol,
        "buy_count": buy_count,
        "sell_count": sell_count,
    }


# ============================================================================
# ACTIVE TOKENS — v3 with activity-based scoring
# ============================================================================
@router.get("/tokens/active")
async def get_active_tokens():
    """Get all tokens with activity-based signal scoring."""
    try:
        conn = await get_db()
        logger.info("Getting active tokens with v3 scoring...")

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
            WHERE t.mint LIKE '%pump'
            ORDER BY t.updated_at DESC
            LIMIT 100
            """
        )

        # Batch fetch recent trade events for volume calculation (last 30 min)
        thirty_min_ago = datetime.utcnow() - timedelta(minutes=30)
        recent_events = await conn.fetch(
            """
            SELECT te.token_id, te.raw_event, te.created_at
            FROM token_events te
            JOIN tokens t ON te.token_id = t.id
            WHERE te.event_type IN ('buy', 'sell')
              AND te.created_at > $1
              AND t.mint LIKE '%pump'
            ORDER BY te.created_at DESC
            """,
            thirty_min_ago
        )

        # Group events by token_id
        events_by_token = {}
        for evt in recent_events:
            tid = evt["token_id"]
            if tid not in events_by_token:
                events_by_token[tid] = []
            raw = evt["raw_event"]
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except:
                    continue
            if isinstance(raw, dict):
                events_by_token[tid].append(raw)

        logger.info(f"Found {len(tokens)} tokens, {len(recent_events)} recent events")

        result = []
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
            # Market cap in USD is stored in DB and updated with every trade
            mc_usd = t.get("market_cap") or 0
            # Convert back to SOL for display (using current SOL price)
            from src.tasks.websocket_scanner import _sol_price_usd
            sol_price = _sol_price_usd if _sol_price_usd > 0 else 140.0
            mc_sol = mc_usd / sol_price if mc_usd > 0 else 0
            mc_initial_sol = create_event.get("marketCapSol", 0)
            mc_initial_usd = compute_market_cap_usd(mc_initial_sol)

            is_migrated = bonding_pct >= 100

            top_holder_pct = 0.0
            if latest_buy.get("newTokenBalance") and TOTAL_TOKEN_SUPPLY > 0:
                top_holder_pct = (latest_buy["newTokenBalance"] / TOTAL_TOKEN_SUPPLY) * 100
                top_holder_pct = min(top_holder_pct, 100.0)

            buy_count = t["buy_count"] or 0
            sell_count = t["sell_count"] or 0

            # --- Compute volume from recent events ---
            token_events = events_by_token.get(t["id"], [])
            vol_data = compute_buy_sell_volume(token_events) if token_events else {
                "buy_volume_sol": 0, "sell_volume_sol": 0,
                "buy_count": buy_count, "sell_count": sell_count,
            }
            # Use total counts if recent events are empty
            if vol_data["buy_count"] == 0 and vol_data["sell_count"] == 0:
                vol_data["buy_count"] = buy_count
                vol_data["sell_count"] = sell_count

            # --- Price change estimate ---
            price_change_pct = 0.0
            if mc_initial_usd > 0 and mc_usd > 0:
                price_change_pct = ((mc_usd - mc_initial_usd) / mc_initial_usd) * 100

            # --- Age ---
            age_seconds = 0
            if t["created_at"]:
                age_seconds = (datetime.utcnow() - t["created_at"]).total_seconds()

            # --- Txns per minute ---
            txns_per_min = 0.0
            if age_seconds > 0:
                txns_per_min = (buy_count + sell_count) / max(age_seconds / 60, 1)

            # --- Creator activity ---
            creator = create_event.get("traderPublicKey", "")
            creator_activity = "unknown"
            creator_holding = dev_pct
            if creator and latest_buy.get("traderPublicKey") == creator:
                last_type = latest_buy.get("txType", "")
                if last_type == "buy":
                    creator_activity = "buying"
                elif last_type == "sell":
                    creator_activity = "selling"
                else:
                    creator_activity = "holding"
            elif creator:
                creator_activity = "holding"

            # ============================================
            # v3 SCORING — activity-based
            # ============================================
            scoring_data = {
                "buy_volume_sol": vol_data["buy_volume_sol"],
                "sell_volume_sol": vol_data["sell_volume_sol"],
                "buy_count": vol_data["buy_count"],
                "sell_count": vol_data["sell_count"],
                "price_change_pct": price_change_pct,
                "mc_current": mc_usd if mc_usd > 0 else t["market_cap"] or 0,
                "mc_initial": mc_initial_usd,
                "dev_holding_percent": dev_pct,
                "top_holder_percent": top_holder_pct,
                "holders": t["holders"] or 0,
                "age_seconds": age_seconds,
                "creator_activity": creator_activity,
                "txns_per_minute": txns_per_min,
            }

            base_score, breakdown = scoring_v3.score_token(scoring_data)

            # ============================================
            # REAL-TIME SIGNAL DEGRADATION
            # ============================================
            degrade_info = degradation_engine.get_degradation_info(t["id"])

            # Get 1-min volume from momentum engine buffer
            vol_1m_sol = 0.0
            buy_1m = 0
            sell_1m = 0
            me_buf = momentum_engine.get_buffer(t["id"])
            if me_buf and not me_buf.trades.empty:
                one_min_ago = datetime.utcnow() - timedelta(seconds=60)
                recent = me_buf.trades[me_buf.trades["timestamp"] >= one_min_ago]
                if not recent.empty:
                    vol_1m_sol = float(recent["amount_sol"].sum())
                    buy_1m = int((recent["direction"] == "buy").sum())
                    sell_1m = int((recent["direction"] == "sell").sum())

            score, breakdown = apply_signal_degradation(
                base_score, breakdown, degrade_info,
                volume_1m_sol=vol_1m_sol,
                buy_count_1m=buy_1m,
                sell_count_1m=sell_1m,
            )

            badge = breakdown.get("badge", "NONE")
            uri = create_event.get("uri", "")

            # Top holders list
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
                # v3 signal breakdown
                "signal_breakdown": {
                    "momentum_score": breakdown.get("momentum_score", 0),
                    "volume_score": breakdown.get("volume_score", 0),
                    "mc_growth_score": breakdown.get("mc_growth_score", 0),
                    "risk_score": breakdown.get("risk_score", 0),
                    "mc_growth_pct": breakdown.get("mc_growth_pct", 0),
                    "momentum_indicator": breakdown.get("momentum_indicator", "dead"),
                    "volume_indicator": breakdown.get("volume_indicator", "dead"),
                    "growth_indicator": breakdown.get("growth_indicator", "flat"),
                    "risk_indicator": breakdown.get("risk_indicator", "moderate"),
                    "reasons": breakdown.get("reasons", []),
                    "kill_reason": breakdown.get("kill_reason"),
                },
                "buy_volume_sol": round(vol_data["buy_volume_sol"], 3),
                "sell_volume_sol": round(vol_data["sell_volume_sol"], 3),
                "price_change_pct": round(price_change_pct, 1),
                "txns_per_minute": round(txns_per_min, 2),
                "creator_address": creator,
                "creator_holding_percent": round(creator_holding, 2),
                "creator_activity": creator_activity,
                "top_10_holders": top_10_holders,
                "metadata_uri": uri,
                "description": t["description"] or "",
                "last_trade_at": t["last_trade_at"].isoformat() if t["last_trade_at"] else None,
                "twitter": None,
                "telegram": None,
                "website": None,
                # Real-time degradation data
                "degraded": breakdown.get("degraded", False),
                "original_score": breakdown.get("original_score"),
                "seconds_since_trade": breakdown.get("seconds_since_trade", 0),
                "degradation_reasons": breakdown.get("degradation_reasons", []),
            }
            result.append(token_data)

        await conn.close()
        return result

    except Exception as e:
        logger.error(f"Error fetching active tokens: {e}", exc_info=True)
        return []


# ============================================================================
# TOKEN METRICS (momentum engine)
# ============================================================================
@router.get("/tokens/{token_id}/metrics")
async def get_token_metrics(token_id: int):
    """Get real-time momentum metrics for a token."""
    try:
        buf = momentum_engine.get_buffer(token_id)
        if buf is None:
            return {
                "momentum_15s": 0, "momentum_1m": 0,
                "whale_concentration": 0, "pump_signal_score": 0,
                "is_hot": False,
                "last_updated": datetime.utcnow().isoformat()
            }
        m = buf.metrics
        return {
            "momentum_15s": m.get("momentum_15s", 0),
            "momentum_1m": m.get("momentum_1m", 0),
            "whale_concentration": m.get("whale_concentration", 0),
            "pump_signal_score": m.get("pump_signal_score", 0),
            "velocity": m.get("velocity", 0),
            "is_hot": m.get("is_hot", False),
            "signal_type": m.get("signal_type"),
            "last_updated": datetime.utcnow().isoformat()
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
    try:
        conn = await get_db()
        signals = await conn.fetch(
            """
            SELECT s.id, s.token_id, s.created_at, t.name, t.symbol, t.mint, s.score
            FROM signals s
            JOIN tokens t ON s.token_id = t.id
            WHERE t.mint LIKE '%pump'
            ORDER BY s.created_at DESC
            LIMIT $1
            """,
            limit
        )
        await conn.close()
        return [
            {
                "id": s["id"], "token_id": s["token_id"],
                "name": s["name"], "symbol": s["symbol"],
                "mint": s["mint"], "score": s["score"],
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
    try:
        conn = await get_db()
        high_score_signals = await conn.fetch(
            """
            SELECT s.id, s.token_id, s.created_at, s.score, t.name, t.symbol, t.mint
            FROM signals s
            JOIN tokens t ON s.token_id = t.id
            WHERE s.score >= $1 AND s.created_at > NOW() - INTERVAL '1 hour'
              AND t.mint LIKE '%pump'
            ORDER BY s.created_at DESC
            LIMIT 50
            """,
            settings.alert_threshold
        )
        await conn.close()
        return {
            "high_score": [
                {
                    "id": s["id"], "token_id": s["token_id"],
                    "name": s["name"], "symbol": s["symbol"],
                    "mint": s["mint"], "score": s["score"],
                    "created_at": s["created_at"].isoformat()
                }
                for s in high_score_signals
            ],
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
    try:
        conn = await get_db()
        total_tokens = await conn.fetchval("SELECT COUNT(*) FROM tokens WHERE mint LIKE '%pump'")
        good_signals = await conn.fetchval(
            "SELECT COUNT(*) FROM signals s JOIN tokens t ON s.token_id = t.id WHERE s.score >= $1 AND t.mint LIKE '%pump'",
            settings.alert_threshold
        )
        today = datetime.utcnow().date()
        signals_today = await conn.fetchval(
            "SELECT COUNT(*) FROM signals s JOIN tokens t ON s.token_id = t.id WHERE DATE(s.created_at) = $1 AND t.mint LIKE '%pump'",
            today
        )
        fifteen_mins_ago = datetime.utcnow() - timedelta(minutes=15)
        new_pairs_15m = await conn.fetchval(
            "SELECT COUNT(*) FROM tokens WHERE created_at > $1 AND mint LIKE '%pump'",
            fifteen_mins_ago
        )
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        graduated_1h = await conn.fetchval(
            "SELECT COUNT(*) FROM tokens WHERE created_at > $1 AND mint LIKE '%pump'",
            one_hour_ago
        )
        await conn.close()

        tracked = momentum_engine.tracked_count if hasattr(momentum_engine, 'tracked_count') else 0

        return {
            "total_tokens": total_tokens or 0,
            "good_signals": good_signals or 0,
            "signals_today": signals_today or 0,
            "active_tracked": tracked,
            "new_pairs_15m": new_pairs_15m or 0,
            "graduated_1h": graduated_1h or 0
        }
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# TOKEN STATS — uses v3 scoring
# ============================================================================
@router.get("/tokens/stats")
async def get_token_stats():
    """Get token counts by signal badge (recalculated with v3)."""
    try:
        all_tokens = await get_active_tokens()

        by_signal = {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0}
        by_category = {"new_pairs": 0, "final_stretch": 0, "migrated": 0}
        by_category_and_signal = {
            "new_pairs": {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0},
            "final_stretch": {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0},
            "migrated": {"strong_buy": 0, "buy": 0, "neutral": 0, "none": 0},
        }

        for t in all_tokens:
            badge = (t.get("signal_badge") or "NONE").lower()
            bonding_pct = t.get("bonding_curve_percent", 0)

            if badge in by_signal:
                by_signal[badge] += 1

            if bonding_pct >= 100:
                cat = "migrated"
            elif bonding_pct >= 80:
                cat = "final_stretch"
            else:
                cat = "new_pairs"

            by_category[cat] += 1
            if badge in by_category_and_signal[cat]:
                by_category_and_signal[cat][badge] += 1

        return {
            "total_tokens": len(all_tokens),
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
# SIGNAL DEGRADATION STATUS
# ============================================================================
@router.get("/degradation/status")
async def get_degradation_status():
    """Get real-time signal degradation status for all tracked tokens."""
    try:
        all_degrade = degradation_engine.get_all_degradation()
        killed = [d for d in all_degrade.values() if d and d.get("kill")]
        demoted = [d for d in all_degrade.values() if d and not d.get("kill") and d.get("degradation_points", 0) > 0]
        healthy = [d for d in all_degrade.values() if d and not d.get("kill") and d.get("degradation_points", 0) == 0]

        return {
            "total_tracked": degradation_engine.tracked_count,
            "killed_count": len(killed),
            "demoted_count": len(demoted),
            "healthy_count": len(healthy),
            "killed": killed[:20],
            "demoted": demoted[:20],
        }
    except Exception as e:
        logger.error(f"Error fetching degradation status: {e}")
        return {"error": str(e)}


# ============================================================================
# FILTERED TOKENS
# ============================================================================
@router.get("/tokens/filtered")
async def get_filtered_tokens(
    signal_min: Optional[int] = Query(None),
    dev_holding_max: Optional[float] = Query(None),
    top_holder_max: Optional[float] = Query(None),
    bonding_curve_min: Optional[float] = Query(None),
    market_cap_min: Optional[float] = Query(None),
    market_cap_max: Optional[float] = Query(None),
    age_min: Optional[int] = Query(None),
    age_max: Optional[int] = Query(None),
):
    """Get tokens with server-side filtering."""
    all_tokens = await get_active_tokens()
    now = datetime.utcnow()
    filtered = []

    for t in all_tokens:
        if signal_min is not None and (t.get("signal_score", 0) or 0) < signal_min:
            continue
        if dev_holding_max is not None and (t.get("dev_holding_percent", 0) or 0) > dev_holding_max:
            continue
        if top_holder_max is not None and (t.get("top_holder_percent", 0) or 0) > top_holder_max:
            continue
        if bonding_curve_min is not None and (t.get("bonding_curve_percent", 0) or 0) < bonding_curve_min:
            continue
        mc = t.get("market_cap", 0) or 0
        if market_cap_min is not None and mc < market_cap_min:
            continue
        if market_cap_max is not None and mc > market_cap_max:
            continue
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
