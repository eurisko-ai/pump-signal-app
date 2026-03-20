"""One-time backfill of image URLs for existing tokens missing them.

Runs once on startup, fetches from DexScreener API with rate limiting.
"""
import asyncio
import asyncpg
import aiohttp
from src.config import get_settings
from src.utils.logger import setup_logger

logger = setup_logger("image_backfill")
settings = get_settings()


async def _fetch_image_from_dexscreener(mint: str) -> str | None:
    """Fetch token image URL from DexScreener."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                pairs = data.get("pairs") or []
                if pairs:
                    info = pairs[0].get("info", {})
                    return info.get("imageUrl") or info.get("header")
    except Exception as e:
        logger.debug(f"DexScreener fetch failed for {mint[:16]}: {e}")
    return None


async def backfill_token_images():
    """Backfill image URLs for tokens that don't have one yet."""
    try:
        conn = await asyncpg.connect(settings.database_url)
        tokens = await conn.fetch(
            "SELECT id, mint FROM tokens WHERE image_url IS NULL ORDER BY created_at DESC LIMIT 100"
        )
        await conn.close()

        if not tokens:
            logger.info("No tokens need image backfill")
            return

        logger.info(f"Backfilling images for {len(tokens)} tokens...")
        updated = 0

        for t in tokens:
            image_url = await _fetch_image_from_dexscreener(t["mint"])
            if image_url:
                try:
                    conn = await asyncpg.connect(settings.database_url)
                    await conn.execute(
                        "UPDATE tokens SET image_url = $1, updated_at = NOW() WHERE id = $2",
                        image_url, t["id"]
                    )
                    await conn.close()
                    updated += 1
                    logger.debug(f"Updated image for token {t['id']}: {image_url[:60]}")
                except Exception as e:
                    logger.error(f"DB update error for token {t['id']}: {e}")

            # Rate limit: DexScreener allows ~300 req/min, be conservative
            await asyncio.sleep(1)

        logger.info(f"Image backfill complete: {updated}/{len(tokens)} tokens updated")

    except Exception as e:
        logger.error(f"Image backfill failed: {e}", exc_info=True)
