"""Main scanning loop"""
import asyncio
import asyncpg
from datetime import datetime
from src.config import get_settings
from src.services.moralis import moralis
from src.services.scoring import scoring
from src.utils.logger import setup_logger

logger = setup_logger("scanner")
settings = get_settings()

# Track seen tokens (dedup)
seen_tokens = {}  # {ca: timestamp}

async def get_db():
    return await asyncpg.connect(settings.database_url)

async def start_scanner():
    """Main scanning loop - runs continuously"""
    logger.info(f"Scanner starting: interval={settings.scan_interval_seconds}s, threshold={settings.alert_threshold}")
    
    while True:
        try:
            await run_scan()
        except Exception as e:
            logger.error(f"Scan error: {e}")
        
        await asyncio.sleep(settings.scan_interval_seconds)

async def run_scan():
    """Execute one scan"""
    scan_start = datetime.utcnow()
    tokens_found = 0
    alerts_posted = 0
    scores = []
    errors = []
    
    try:
        # Fetch tokens from Moralis (3-tier fallback)
        tokens = await moralis.get_graduated_tokens()
        logger.info(f"Fetched {len(tokens)} graduated tokens")
        
        conn = await get_db()
        
        for token in tokens:
            try:
                ca = token.get("mint", "")
                if not ca or not ca.endswith("pump"):
                    continue
                
                # Dedup check: skip if seen in last 6 hours
                if ca in seen_tokens:
                    age = (datetime.utcnow() - seen_tokens[ca]).total_seconds() / 3600
                    if age < settings.dedup_window_hours:
                        continue
                
                # Enrich token data
                token = await moralis.enrich_token_data(token)
                
                # Score token
                score, breakdown = scoring.score_token(token)
                scores.append(score)
                tokens_found += 1
                
                logger.debug(f"{token.get('name')} ({ca}): score={score}")
                
                # Insert token
                token_id = await conn.fetchval(
                    """
                    INSERT INTO tokens (mint, name, symbol, description, market_cap, volume_24h, holders, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                    ON CONFLICT (mint) DO UPDATE SET updated_at = NOW()
                    RETURNING id
                    """,
                    ca,
                    token.get("name", ""),
                    token.get("symbol", ""),
                    token.get("description", ""),
                    float(token.get("market_cap", 0)),
                    float(token.get("volume_24h", 0)),
                    int(token.get("holders", 0))
                )
                
                # Insert signal
                if score >= settings.alert_threshold:
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
                    
                    # Create alert
                    await conn.execute(
                        "INSERT INTO alerts (signal_id, status) VALUES ($1, 'posted')",
                        signal_id
                    )
                    alerts_posted += 1
                    logger.info(f"✅ ALERT: {token.get('name')} (score={score})")
                
                # Mark as seen
                seen_tokens[ca] = datetime.utcnow()
            
            except Exception as e:
                logger.error(f"Error processing token {ca}: {e}")
                errors.append(str(e))
        
        # Log scan
        try:
            duration = (datetime.utcnow() - scan_start).total_seconds()
            min_score = min(scores) if scores else None
            max_score = max(scores) if scores else None
            avg_score = sum(scores) / len(scores) if scores else None
            
            await conn.execute(
                """
                INSERT INTO scan_log (tokens_found, alerts_posted, min_score, max_score, avg_score, 
                                       duration_seconds, error_count, error_message)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                tokens_found,
                alerts_posted,
                min_score,
                max_score,
                avg_score,
                duration,
                len(errors),
                "\n".join(errors) if errors else None
            )
        except Exception as e:
            logger.error(f"Error logging scan: {e}")
        
        await conn.close()
        
        logger.info(f"Scan complete: {tokens_found} tokens, {alerts_posted} alerts")
    
    except Exception as e:
        logger.error(f"Critical scan error: {e}")
