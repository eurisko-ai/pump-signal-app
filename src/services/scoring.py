"""Scoring service v2 with improved signal detection"""
from typing import Dict, Tuple
from src.utils.logger import setup_logger

logger = setup_logger("scoring")

class ScoringService:
    """Score tokens 0-100 for trading potential"""
    
    NARRATIVES = {
        "AI": ["ai", "agent", "gpt", "llm", "neural", "autonomous", "claude", "openai"],
        "Politics": ["trump", "biden", "maga", "election", "gop", "dnc"],
        "Celebrity": ["elon", "musk", "doge", "shib", "inu"],
        "Meme": ["pepe", "wojak", "chad", "frog", "based", "degen"],
        "Sports": ["nfl", "nba", "soccer", "esports"],
        "Crypto": ["defi", "blockchain", "web3", "token", "swap"],
    }
    
    def score_token(self, token: Dict) -> Tuple[int, Dict]:
        """
        Score token 0-100. Returns (score, breakdown).
        
        Components:
        - Status: +35 (graduated Pump.fun)
        - Market Cap: +15 (sweet spot $50k-$2M)
        - Holders: +15 (200+)
        - Volume 24h: +12 (>$100k)
        - Liquidity: +12 (vol/mcap > 5%)
        - Age Penalty: -5 to -15 (too new or too old)
        - Whale Risk: -10 (top 10 holders >80%)
        - Narrative: +8 (identifiable story)
        """
        
        score = 0
        breakdown = {}
        
        # 1. Status (+35) - Graduated Pump.fun only
        status_score = 35  # Assumed graduated (filtered in API)
        score += status_score
        breakdown["status"] = status_score
        
        # 2. Market Cap (+15) - Sweet spot: $50k-$2M
        market_cap = float(token.get("market_cap", 0))
        market_cap_score = 0
        if 50000 <= market_cap <= 2000000:
            market_cap_score = 15
        elif 20000 <= market_cap < 50000:
            market_cap_score = 10
        elif 2000000 < market_cap <= 10000000:
            market_cap_score = 10
        elif market_cap > 10000000:
            market_cap_score = 5  # Too big (less upside)
        
        score += market_cap_score
        breakdown["market_cap"] = market_cap_score
        
        # 3. Holders (+15) - Decentralization signal
        holders = int(token.get("holders", 0))
        holders_score = 0
        if holders >= 1000:
            holders_score = 15
        elif holders >= 500:
            holders_score = 12
        elif holders >= 200:
            holders_score = 10
        elif holders >= 100:
            holders_score = 6
        elif holders >= 50:
            holders_score = 3
        
        score += holders_score
        breakdown["holders"] = holders_score
        
        # 4. 24h Volume (+12) - Trading activity
        volume_24h = float(token.get("volume_24h", 0))
        volume_score = 0
        if volume_24h > 1000000:
            volume_score = 12
        elif volume_24h > 500000:
            volume_score = 10
        elif volume_24h > 200000:
            volume_score = 8
        elif volume_24h > 50000:
            volume_score = 5
        elif volume_24h > 10000:
            volume_score = 2
        
        score += volume_score
        breakdown["volume"] = volume_score
        
        # 5. Liquidity (+12) - Ease of trading
        liquidity_ratio = token.get("liquidity_ratio", 0)
        liquidity_score = 0
        if liquidity_ratio > 50:
            liquidity_score = 12  # Very liquid
        elif liquidity_ratio > 20:
            liquidity_score = 10
        elif liquidity_ratio > 10:
            liquidity_score = 8
        elif liquidity_ratio > 5:
            liquidity_score = 6
        elif liquidity_ratio > 2:
            liquidity_score = 3
        
        score += liquidity_score
        breakdown["liquidity"] = liquidity_score
        
        # 6. Age Penalty (-5 to -15)
        age_hours = token.get("age_hours", 0)
        age_penalty = 0
        
        if age_hours < 0.5:  # <30 min - likely noise
            age_penalty = -15
        elif age_hours < 1:  # <1 hour - risky
            age_penalty = -12
        elif age_hours < 2:  # <2 hours - new but plausible
            age_penalty = -5
        elif age_hours > 720:  # >30 days - likely dead
            age_penalty = -10
        elif age_hours > 360:  # >15 days
            age_penalty = -5
        
        score += age_penalty
        breakdown["age_penalty"] = age_penalty
        
        # 7. Whale Risk (-10) - Check for concentration
        whale_risk = 0
        top_holder_ratio = token.get("top_10_holders_ratio", 0)
        if top_holder_ratio > 0.8:  # Top 10 hold >80% = dump risk
            whale_risk = -10
        elif top_holder_ratio > 0.6:
            whale_risk = -6
        elif top_holder_ratio > 0.4:
            whale_risk = -3
        
        score += whale_risk
        breakdown["whale_risk"] = whale_risk
        
        # 8. Narrative (+8) - Story/theme
        narrative_type = self._detect_narrative(token.get("name", ""), token.get("description", ""))
        narrative_score = 8 if narrative_type != "Unknown" else 0
        
        score += narrative_score
        breakdown["narrative"] = narrative_score
        breakdown["narrative_type"] = narrative_type
        
        # Cap at 100
        final_score = min(100, max(0, score))
        
        # Determine risk level
        if final_score >= 80:
            risk_level = "🟢 LOW"
        elif final_score >= 60:
            risk_level = "🟡 MEDIUM"
        elif final_score >= 40:
            risk_level = "🟠 HIGH"
        else:
            risk_level = "🔴 CRITICAL"
        
        breakdown["risk_level"] = risk_level
        
        return final_score, breakdown
    
    def _detect_narrative(self, name: str, description: str = "") -> str:
        """Detect narrative theme from token name/description"""
        text = (name + " " + description).lower()
        
        for narrative, keywords in self.NARRATIVES.items():
            if any(kw in text for kw in keywords):
                return narrative
        
        return "Unknown"

scoring = ScoringService()
