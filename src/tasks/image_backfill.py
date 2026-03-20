"""Backfill image URLs for existing tokens.

1. Fix tokens with metadata URIs stored as image_url (resolve to actual image)
2. Fill NULL image_url from DexScreener API

Runs once on startup with rate limiting.
"""
import asyncio
import asyncpg
import aiohttp
from src.config import get_settings
from src.utils.logger import setup_logger

logger = setup_logger("image_backfill")
settings = get_settings()


async def _resolve_metadata_uri(uri: str) -> str | None:
    """Fetch metadata JSON and extract the 'image' field."""
    if not uri or not uri.startswith("http"):
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(uri, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.debug(f"Metadata URI returned {resp.status}: {uri[:60]}")
                    return None
                content_type = resp.headers.get("content-type", "")
                if "image" in content_type:
                    # It's actually a direct image, not metadata JSON
                    logger.debug(f"URI is direct image (content-type={content_type}): {uri[:60]}")
                    return uri
                data = await resp.json()
                image_url = data.get("image") or data.get("image_url") or data.get("imageUrl")
                if image_url and isinstance(image_url, str) and image_url.startswith("http"):
                    return image_url
                logger.debug(f"Metadata JSON had no image field. Keys: {list(data.keys())[:5]}")
    except Exception as e:
        logger.debug(f"Metadata resolve failed for {uri[:60]}: {type(e).__name__}: {e}")
    return None


def _is_metadata_uri(url: str) -> bool:
    """Check if a URL looks like a metadata URI (JSON) rather than an image."""
    if not url:
        return False
    # Direct image patterns — if URL clearly points to an image, it's not metadata
    image_patterns = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]
    url_lower = url.lower()
    if any(url_lower.endswith(ext) for ext in image_patterns):
        return False
    # Known direct-image hosts (not metadata)
    image_hosts = ["edge.uxento.io/image/", "cdn.digitaloceanspaces.com", "myfilebase.com/ipfs/"]
    if any(h in url for h in image_hosts):
        return False
    # Common metadata URI patterns (case-sensitive where needed)
    metadata_patterns_cs = [
        "/ipfs/Qm",        # IPFS CIDv0 (base58, starts with Qm — typically metadata JSON)
    ]
    metadata_patterns_ci = [
        ".json",
        "/ipfs/bafkrei",   # IPFS CIDv1 base32
        "/ipfs/bafybei",   # IPFS CIDv1 base32 (dag-pb)
        "meta.uxento.io",
        "metadata.rapidlaunch.io",
        "arweave.net",
    ]
    # Case-sensitive check
    if any(p in url for p in metadata_patterns_cs):
        return True
    # Case-insensitive check
    if any(p in url_lower for p in metadata_patterns_ci):
        return True
    return False


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
    """Backfill image URLs for tokens that need fixing."""
    try:
        conn = await asyncpg.connect(settings.database_url)
        
        # Phase 1: Fix tokens with metadata URIs stored as image_url
        all_tokens_with_images = await conn.fetch(
            "SELECT id, mint, image_url FROM tokens WHERE image_url IS NOT NULL ORDER BY created_at DESC LIMIT 200"
        )
        
        metadata_tokens = [t for t in all_tokens_with_images if _is_metadata_uri(t["image_url"])]
        
        if metadata_tokens:
            logger.info(f"Phase 1: Resolving {len(metadata_tokens)} metadata URIs to actual images...")
            resolved = 0
            for t in metadata_tokens:
                real_image = await _resolve_metadata_uri(t["image_url"])
                if real_image:
                    await conn.execute(
                        "UPDATE tokens SET image_url = $1, updated_at = NOW() WHERE id = $2",
                        real_image, t["id"]
                    )
                    resolved += 1
                    logger.debug(f"Resolved token {t['id']}: {real_image[:60]}")
                await asyncio.sleep(0.3)  # Rate limit metadata fetches
            logger.info(f"Phase 1 complete: {resolved}/{len(metadata_tokens)} metadata URIs resolved")
        
        # Phase 2: Fill NULL image_url from DexScreener
        null_tokens = await conn.fetch(
            "SELECT id, mint FROM tokens WHERE image_url IS NULL ORDER BY created_at DESC LIMIT 100"
        )
        await conn.close()

        if not null_tokens:
            logger.info("Phase 2: No tokens need DexScreener image backfill")
            return

        logger.info(f"Phase 2: Backfilling images for {len(null_tokens)} tokens from DexScreener...")
        updated = 0

        for t in null_tokens:
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

        logger.info(f"Phase 2 complete: {updated}/{len(null_tokens)} tokens updated from DexScreener")

    except Exception as e:
        logger.error(f"Image backfill failed: {e}", exc_info=True)
