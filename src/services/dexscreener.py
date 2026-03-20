"""
DexScreener API Integration — Token Legitimacy Verification.

Fetches profile data from DexScreener to verify token legitimacy:
- Profile existence
- Verified/paid status
- Social links (Twitter, Telegram, website)
- Pair data

Scoring:
  Has DexScreener profile    = +10 pts
  Profile verified/paid      = +20 pts
  Has Twitter                = +10 pts
  Has website                = +5 pts
  Has Telegram               = +5 pts
  Missing profile            = -20 pts
  No socials at all          = -15 pts
"""
import time
from typing import Dict, Optional, Tuple

import aiohttp

from src.utils.logger import setup_logger

logger = setup_logger("dexscreener")

# Cache: {mint: (profile_data, monotonic_timestamp)}
_profile_cache: Dict[str, Tuple[Optional[Dict], float]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


class DexScreenerService:
    """Fetches and scores DexScreener profile data for token legitimacy."""

    BASE_URL = "https://api.dexscreener.com/latest/dex/tokens"

    async def fetch_profile(self, mint: str) -> Optional[Dict]:
        """
        Fetch DexScreener profile data for a token mint address.
        Returns normalized profile dict or None if not found / error.
        """
        # Check cache
        cached = _profile_cache.get(mint)
        if cached:
            data, ts = cached
            if (time.monotonic() - ts) < CACHE_TTL_SECONDS:
                logger.debug(f"DexScreener cache hit for {mint[:16]}")
                return data

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.BASE_URL}/{mint}"
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"User-Agent": "PumpSignal/1.0"},
                ) as resp:
                    if resp.status == 429:
                        logger.warning("DexScreener rate limited, skipping")
                        return None
                    if resp.status != 200:
                        logger.debug(f"DexScreener returned {resp.status} for {mint[:16]}")
                        # Cache the miss to avoid hammering
                        _profile_cache[mint] = (None, time.monotonic())
                        return None

                    data = await resp.json()
                    profile = self._parse_response(data, mint)

                    # Cache result
                    _profile_cache[mint] = (profile, time.monotonic())

                    if profile:
                        logger.info(
                            f"DexScreener profile for {mint[:16]}: "
                            f"verified={profile.get('verified', False)}, "
                            f"socials={len(profile.get('socials', []))}"
                        )
                    else:
                        logger.debug(f"No DexScreener profile for {mint[:16]}")

                    return profile

        except aiohttp.ClientError as e:
            logger.warning(f"DexScreener API error for {mint[:16]}: {e}")
            return None
        except Exception as e:
            logger.error(f"DexScreener unexpected error: {e}")
            return None

    def _parse_response(self, data: Dict, mint: str) -> Optional[Dict]:
        """
        Parse DexScreener API response into normalized profile dict.

        DexScreener response structure:
        {
            "pairs": [
                {
                    "pairAddress": "...",
                    "baseToken": {"address": "...", "name": "...", "symbol": "..."},
                    "info": {
                        "imageUrl": "...",
                        "header": "...",
                        "description": "...",
                        "websites": [{"url": "..."}],
                        "socials": [{"type": "twitter", "url": "..."}, ...]
                    },
                    "boosts": {"active": 0},
                    ...
                }
            ]
        }
        """
        pairs = data.get("pairs")
        if not pairs:
            return None

        # Find the pair matching our mint
        pair = None
        for p in pairs:
            base = p.get("baseToken", {})
            if base.get("address", "").lower() == mint.lower():
                pair = p
                break

        if pair is None:
            # Use first pair as fallback
            pair = pairs[0]

        info = pair.get("info") or {}
        base_token = pair.get("baseToken", {})
        boosts = pair.get("boosts") or {}

        # Extract socials
        socials_list = info.get("socials") or []
        websites = info.get("websites") or []

        twitter_url = None
        telegram_url = None
        discord_url = None

        for social in socials_list:
            s_type = (social.get("type") or "").lower()
            s_url = social.get("url", "")
            if s_type == "twitter" and s_url:
                twitter_url = s_url
            elif s_type == "telegram" and s_url:
                telegram_url = s_url
            elif s_type == "discord" and s_url:
                discord_url = s_url

        website_url = websites[0].get("url") if websites else None

        # Determine verified status
        # DexScreener "verified" = has paid profile / info section populated
        # Active boosts also indicate paid/legitimate presence
        has_info = bool(info)
        has_boosts = int(boosts.get("active", 0)) > 0
        is_verified = has_info and (
            bool(info.get("description"))
            or bool(websites)
            or len(socials_list) >= 2
            or has_boosts
        )

        profile = {
            "has_profile": True,
            "verified": is_verified,
            "pair_address": pair.get("pairAddress", ""),
            "dex_url": pair.get("url", ""),
            "description": info.get("description", ""),
            "image_url": info.get("imageUrl", ""),
            "header_url": info.get("header", ""),
            "website": website_url,
            "twitter": twitter_url,
            "telegram": telegram_url,
            "discord": discord_url,
            "socials": socials_list,
            "websites": websites,
            "boosts_active": int(boosts.get("active", 0)),
            "liquidity_usd": float(pair.get("liquidity", {}).get("usd", 0)),
            "fdv": float(pair.get("fdv", 0)),
            "pair_created_at": pair.get("pairCreatedAt"),
        }

        return profile

    def score_legitimacy(self, profile: Optional[Dict]) -> Tuple[int, list]:
        """
        Score token legitimacy based on DexScreener profile data.

        Returns:
            (score_adjustment, reasons) — score_adjustment can be negative
        """
        if profile is None:
            return -20, ["🚫 No DexScreener profile — sketchy"]

        if not profile.get("has_profile"):
            return -20, ["🚫 No DexScreener profile — sketchy"]

        score = 0
        reasons = []

        # Has profile = +10
        score += 10
        reasons.append("✅ Listed on DexScreener")

        # Verified/paid = +20
        if profile.get("verified"):
            score += 20
            reasons.append("🏅 DexScreener verified profile")

        # Active boosts = extra signal of legitimacy
        boosts = profile.get("boosts_active", 0)
        if boosts > 0:
            score += 5
            reasons.append(f"🚀 {boosts} active boost(s)")

        # Social scoring
        has_any_social = False

        if profile.get("twitter"):
            score += 10
            has_any_social = True
            reasons.append("🐦 Has Twitter")

        if profile.get("website"):
            score += 5
            has_any_social = True
            reasons.append("🌐 Has website")

        if profile.get("telegram"):
            score += 5
            has_any_social = True
            reasons.append("💬 Has Telegram")

        # No socials penalty
        if not has_any_social:
            score -= 15
            reasons.append("👻 No socials — anonymous rug risk")

        return score, reasons

    def clear_cache(self):
        """Clear the profile cache."""
        _profile_cache.clear()
        logger.info("DexScreener profile cache cleared")

    def get_cache_stats(self) -> Dict:
        """Return cache statistics."""
        now = time.monotonic()
        valid = sum(1 for _, (_, ts) in _profile_cache.items() if (now - ts) < CACHE_TTL_SECONDS)
        return {
            "total_cached": len(_profile_cache),
            "valid_cached": valid,
            "expired_cached": len(_profile_cache) - valid,
            "cache_ttl_seconds": CACHE_TTL_SECONDS,
        }


# Singleton
dexscreener_service = DexScreenerService()
