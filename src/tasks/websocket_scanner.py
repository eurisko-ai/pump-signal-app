"""
PumpPortal WebSocket Scanner — Real-time token listener + scorer + alerter.

Replaces the polling-based scanner.py + moralis.py with a single WebSocket
connection to wss://pumpportal.fun/api/data.

Flow: WS event → parse → filter → score → DB insert → Telegram alert (< 2s)
"""
import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import asyncpg
import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI, InvalidHandshake

from src.config import get_settings
from src.services.scoring import scoring
from src.services.telegram_service import TelegramService
from src.tasks.trade_tracker import trade_tracker
from src.utils.logger import setup_logger

logger = setup_logger("ws_scanner")
settings = get_settings()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PUMPPORTAL_WS_URI = getattr(settings, "pumpportal_ws_uri", "wss://pumpportal.fun/api/data")
WS_RECONNECT_MAX_DELAY = getattr(settings, "ws_reconnect_max_delay", 30)
SOL_PRICE_CACHE_TTL = getattr(settings, "sol_price_cache_seconds", 60)

# Full bonding curve ≈ 85 SOL raised before migration
BONDING_CURVE_TARGET_LAMPORTS = 85_000_000_000  # 85 SOL in lamports

# Known scam creator addresses (extend as needed)
KNOWN_SCAM_CREATORS: set[str] = set()

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
seen_tokens: dict[str, datetime] = {}  # {mint: first_seen_utc}
_sol_price_usd: float = 0.0
_sol_price_updated: float = 0.0  # monotonic timestamp
_db_pool: Optional[asyncpg.Pool] = None
_telegram: Optional[TelegramService] = None

# Stats
_stats = {
    "connected_at": None,
    "events_total": 0,
    "migrations_processed": 0,
    "alerts_posted": 0,
    "last_event_at": None,
}


# ---------------------------------------------------------------------------
# SOL Price
# ---------------------------------------------------------------------------
async def _refresh_sol_price() -> float:
    """Fetch SOL/USD from CoinGecko (free, no key). Returns price or last known."""
    global _sol_price_usd, _sol_price_updated
    import aiohttp

    now = time.monotonic()
    if _sol_price_usd > 0 and (now - _sol_price_updated) < SOL_PRICE_CACHE_TTL:
        return _sol_price_usd

    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                price = float(data["solana"]["usd"])
                _sol_price_usd = price
                _sol_price_updated = now
                logger.info(f"SOL price updated: ${price:.2f}")
                return price
    except Exception as e:
        logger.warning(f"SOL price fetch failed: {e} — using last known ${_sol_price_usd:.2f}")
        if _sol_price_usd == 0:
            _sol_price_usd = 140.0  # safe fallback
            logger.warning("No prior SOL price; using fallback $140")
        return _sol_price_usd


# ---------------------------------------------------------------------------
# DB Helpers
# ---------------------------------------------------------------------------
async def _get_pool() -> asyncpg.Pool:
    """Lazy-init a connection pool."""
    global _db_pool
    if _db_pool is None or _db_pool._closed:
        _db_pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=5)
        logger.info("DB connection pool created")
    return _db_pool


async def _insert_token_and_signal(
    token: Dict, score: int, breakdown: Dict
) -> Optional[int]:
    """Insert token + signal + alert rows. Returns signal_id or None."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Upsert token
            token_id = await conn.fetchval(
                """
                INSERT INTO tokens (mint, name, symbol, description, market_cap,
                                    volume_24h, holders, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (mint) DO UPDATE SET
                    market_cap = EXCLUDED.market_cap,
                    volume_24h = EXCLUDED.volume_24h,
                    holders = EXCLUDED.holders,
                    updated_at = NOW()
                RETURNING id
                """,
                token["mint"],
                token.get("name", ""),
                token.get("symbol", ""),
                token.get("description", ""),
                float(token.get("market_cap", 0)),
                float(token.get("volume_24h", 0)),
                int(token.get("holders", 0)),
            )

            if score < settings.alert_threshold:
                return None  # token logged, no signal needed

            signal_id = await conn.fetchval(
                """
                INSERT INTO signals (token_id, score, status_score, market_cap_score,
                                     holders_score, volume_score, liquidity_score,
                                     age_penalty, whale_risk, narrative_score,
                                     narrative_type, risk_level)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
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
                breakdown.get("risk_level", ""),
            )

            await conn.execute(
                "INSERT INTO alerts (signal_id, status) VALUES ($1, 'posted')",
                signal_id,
            )
            return signal_id


# ---------------------------------------------------------------------------
# Telegram (instant)
# ---------------------------------------------------------------------------
def _get_telegram() -> TelegramService:
    global _telegram
    if _telegram is None:
        _telegram = TelegramService()
    return _telegram


async def _post_alert(token: Dict, score: int, breakdown: Dict, signal_id: int):
    """Format and send Telegram alert. Fire-and-forget style (errors logged)."""
    try:
        svc = _get_telegram()
        signal = {"id": signal_id, "score": score}
        message = svc.format_alert_message(token, signal, breakdown)
        ok = await svc.send_alert(message)
        if ok:
            _stats["alerts_posted"] += 1
            logger.info(f"✅ ALERT POSTED: {token.get('name')} score={score}")
        else:
            logger.warning(f"Alert send failed for {token.get('name')}")
    except Exception as e:
        logger.error(f"Alert posting error: {e}")


# ---------------------------------------------------------------------------
# Event Processing
# ---------------------------------------------------------------------------
def _is_paused() -> bool:
    """Check if scanner is paused via Telegram /pause command."""
    try:
        from src.bot.telegram import telegram_bot
        return telegram_bot.scanner_paused
    except Exception:
        return False


def _build_token_dict_from_migration(event: Dict, sol_price: float) -> Dict:
    """
    Build a token dict compatible with scoring from a migration event.

    Migration events are high-signal: bonding curve completed, liquidity on DEX.
    We set optimistic defaults for fields not in the event.
    """
    market_cap_sol = float(event.get("marketCapSol", 0))
    market_cap_usd = market_cap_sol * sol_price

    return {
        "mint": event["mint"],
        "name": event.get("name", ""),
        "symbol": event.get("symbol", ""),
        "description": "",
        "market_cap": market_cap_usd,
        "volume_24h": 0,  # unknown at migration time
        "holders": 150,   # conservative estimate; bonding requires broad buying
        "age_hours": 0.5, # just migrated — very fresh
        "liquidity_ratio": 15.0,  # newly added liquidity is typically decent
        "top_10_holders_ratio": 0.3,  # default; real data needs on-chain query
    }


def _build_token_dict_from_create(event: Dict, sol_price: float) -> Dict:
    """Build token dict from a 'create' event. Used for DB tracking only."""
    market_cap_sol = float(event.get("marketCapSol", 0))
    v_sol = float(event.get("vSolInBondingCurve", 0))
    bonding_pct = (v_sol / BONDING_CURVE_TARGET_LAMPORTS * 100) if BONDING_CURVE_TARGET_LAMPORTS > 0 else 0

    return {
        "mint": event.get("mint", ""),
        "name": event.get("name", ""),
        "symbol": event.get("symbol", ""),
        "description": "",
        "market_cap": market_cap_sol * sol_price,
        "volume_24h": 0,
        "holders": 1,
        "age_hours": 0,
        "liquidity_ratio": 0,
        "bonding_progress": bonding_pct,
        "creator": event.get("traderPublicKey", ""),
    }


async def _handle_migration(event: Dict, sol_price: float):
    """Process a migration (graduated) event end-to-end."""
    mint = event.get("mint", "")

    # --- Filters ---
    if not mint.endswith("pump"):
        return

    if mint in seen_tokens:
        age = (datetime.utcnow() - seen_tokens[mint]).total_seconds() / 3600
        if age < settings.dedup_window_hours:
            return

    creator = event.get("traderPublicKey", "")
    if creator in KNOWN_SCAM_CREATORS:
        logger.debug(f"Skipping known scam creator: {creator[:12]}...")
        return

    # --- Build token data ---
    token = _build_token_dict_from_migration(event, sol_price)
    market_cap = token["market_cap"]

    if market_cap < settings.min_market_cap:
        logger.debug(f"Skipping {mint[:16]}... mcap=${market_cap:.0f} < min")
        return

    # --- Score ---
    score, breakdown = scoring.score_token(token)
    seen_tokens[mint] = datetime.utcnow()
    _stats["migrations_processed"] += 1

    logger.info(
        f"Migration: {token['name']} ({token['symbol']}) "
        f"mcap=${market_cap:,.0f} score={score}"
    )

    # --- DB insert (always for migrations) ---
    signal_id = await _insert_token_and_signal(token, score, breakdown)

    # --- Phase 2: Start tracking trades for this migrated token ---
    if signal_id:
        try:
            # Fetch token_id from DB for trade tracker
            pool = await _get_pool()
            async with pool.acquire() as conn:
                token_id = await conn.fetchval(
                    "SELECT id FROM tokens WHERE mint = $1", mint
                )
            if token_id:
                await trade_tracker.track_token(mint, token_id)
                logger.info(f"📊 Trade tracker monitoring {token['name']} post-migration")
        except Exception as e:
            logger.debug(f"Trade tracker registration error: {e}")

    # --- Alert if threshold met ---
    if signal_id and score >= settings.alert_threshold:
        await _post_alert(token, score, breakdown, signal_id)


async def _handle_create(event: Dict, sol_price: float):
    """Log new token creation to DB for tracking. No alert."""
    mint = event.get("mint", "")
    if not mint.endswith("pump"):
        return

    creator = event.get("traderPublicKey", "")
    if creator in KNOWN_SCAM_CREATORS:
        return

    token = _build_token_dict_from_create(event, sol_price)

    # Insert token + start tracking trades (Phase 2: pre-migration momentum)
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            token_id = await conn.fetchval(
                """
                INSERT INTO tokens (mint, name, symbol, description, market_cap,
                                    volume_24h, holders, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (mint) DO UPDATE SET updated_at = NOW()
                RETURNING id
                """,
                token["mint"],
                token.get("name", ""),
                token.get("symbol", ""),
                "",
                float(token.get("market_cap", 0)),
                0,
                1,
            )
        # Phase 2: Track trades from creation to detect pre-migration momentum
        if token_id:
            track_token(token_id, token["mint"], is_migrated=False)
    except Exception as e:
        logger.debug(f"Create insert error (non-critical): {e}")


async def _process_event(raw: str, sol_price: float):
    """Route a raw WS message to the appropriate handler."""
    _stats["events_total"] += 1
    _stats["last_event_at"] = datetime.utcnow().isoformat()

    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Invalid JSON from WS: {raw[:120]}")
        return

    if _is_paused():
        return

    tx_type = event.get("txType", "")

    if tx_type == "migration":
        await _handle_migration(event, sol_price)
    elif tx_type == "create":
        await _handle_create(event, sol_price)
    # Other tx types (buy/sell/etc.) are ignored


# ---------------------------------------------------------------------------
# Dedup Cleanup
# ---------------------------------------------------------------------------
async def _prune_seen_tokens():
    """Remove entries older than dedup window."""
    cutoff = datetime.utcnow() - timedelta(hours=settings.dedup_window_hours)
    stale = [k for k, v in seen_tokens.items() if v < cutoff]
    for k in stale:
        del seen_tokens[k]
    if stale:
        logger.debug(f"Pruned {len(stale)} stale entries from seen_tokens")


# ---------------------------------------------------------------------------
# Main WebSocket Loop
# ---------------------------------------------------------------------------
async def start_websocket_scanner():
    """
    Entry point — replaces start_scanner().

    Connects to PumpPortal WS, subscribes to newToken + migration events,
    and processes them in real-time with auto-reconnect.
    """
    logger.info("=" * 60)
    logger.info("WebSocket Scanner starting")
    logger.info(f"  URI: {PUMPPORTAL_WS_URI}")
    logger.info(f"  Alert threshold: {settings.alert_threshold}")
    logger.info(f"  Reconnect max delay: {WS_RECONNECT_MAX_DELAY}s")
    logger.info("=" * 60)

    attempt = 0
    prune_counter = 0

    while True:
        try:
            # Refresh SOL price before connecting
            sol_price = await _refresh_sol_price()

            logger.info(f"Connecting to PumpPortal WS (attempt {attempt + 1})...")
            async with websockets.connect(
                PUMPPORTAL_WS_URI,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                max_size=2**20,  # 1 MB max message
            ) as ws:
                _stats["connected_at"] = datetime.utcnow().isoformat()
                logger.info("✅ WebSocket connected")

                # Subscribe to both channels
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                logger.info("Subscribed to: subscribeNewToken, subscribeMigration")

                # Reset backoff on successful connection
                attempt = 0

                # Message loop
                async for message in ws:
                    try:
                        # Periodically refresh SOL price (every ~60 events or SOL_PRICE_CACHE_TTL)
                        sol_price = await _refresh_sol_price()

                        await _process_event(message, sol_price)

                        # Prune seen_tokens every ~500 events
                        prune_counter += 1
                        if prune_counter >= 500:
                            prune_counter = 0
                            await _prune_seen_tokens()

                    except Exception as e:
                        logger.error(f"Event processing error: {e}")

        except (ConnectionClosed, ConnectionRefusedError) as e:
            logger.warning(f"WebSocket disconnected: {e}")
        except (InvalidURI, InvalidHandshake) as e:
            logger.error(f"WebSocket handshake failed: {e}")
        except asyncio.CancelledError:
            logger.info("WebSocket scanner cancelled — shutting down")
            break
        except Exception as e:
            logger.error(f"Unexpected WS error: {e}")

        # Exponential backoff: 1, 2, 4, 8, 16, 30, 30, 30...
        attempt += 1
        delay = min(2 ** attempt, WS_RECONNECT_MAX_DELAY)
        logger.info(f"Reconnecting in {delay}s (attempt {attempt})")
        await asyncio.sleep(delay)

    # Cleanup
    if _db_pool and not _db_pool._closed:
        await _db_pool.close()
        logger.info("DB pool closed")

    logger.info("WebSocket scanner stopped")


# ---------------------------------------------------------------------------
# Stats (for /status command)
# ---------------------------------------------------------------------------
def get_scanner_stats() -> Dict:
    """Return scanner stats for status reporting."""
    return {
        **_stats,
        "seen_tokens_count": len(seen_tokens),
        "sol_price_usd": _sol_price_usd,
        "mode": "websocket",
    }
