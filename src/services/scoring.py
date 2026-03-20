"""
Signal Scoring Service v3 — Activity-Based Momentum Scoring

Weights:
  - Price Momentum:  40%
  - Volume & Demand: 35%
  - MC Growth:       15%
  - Risk:            10%

Hard kills:
  - Volume == 0 → SCORE = 0 (SKIP)
  - Price == 0  → SCORE = 0 (SKIP)
  - Momentum < -10% → SCORE = 0 (SKIP)

Penalties:
  - dev_holding > 5% → SCORE *= 0.5
  - top_holder  > 2% → SCORE *= 0.5

Badges:
  - STRONG_BUY: >= 70
  - BUY:        >= 50
  - NEUTRAL:    >= 30
  - NONE:       < 30
"""
from typing import Dict, Tuple, Optional
from src.utils.logger import setup_logger

logger = setup_logger("scoring_v3")


class SignalScoringV3:
    """Activity-based signal scoring with momentum, volume, MC growth, and risk."""

    def score_token(self, data: Dict) -> Tuple[int, Dict]:
        """
        Score a token 0-100 based on real market activity.

        Expected `data` keys:
          - buy_volume_sol: total SOL buy volume (recent window)
          - sell_volume_sol: total SOL sell volume (recent window)
          - buy_count: number of buy transactions
          - sell_count: number of sell transactions
          - price_change_pct: price change % over recent window (e.g. since first seen)
          - mc_current: current market cap USD
          - mc_initial: market cap at first seen (or bonding start)
          - dev_holding_percent: creator holding %
          - top_holder_percent: biggest non-creator wallet %
          - holders: holder count
          - age_seconds: how old the token is
          - creator_activity: "buying" | "selling" | "holding" | "unknown"
          - txns_per_minute: transactions per minute rate
        """
        breakdown = {}
        reasons = []

        # ---- Extract data ----
        buy_vol = float(data.get("buy_volume_sol", 0))
        sell_vol = float(data.get("sell_volume_sol", 0))
        total_vol = buy_vol + sell_vol
        buy_count = int(data.get("buy_count", 0))
        sell_count = int(data.get("sell_count", 0))
        total_txns = buy_count + sell_count
        price_change = float(data.get("price_change_pct", 0))
        mc_current = float(data.get("mc_current", 0))
        mc_initial = float(data.get("mc_initial", 0))
        dev_pct = float(data.get("dev_holding_percent", 0))
        top_pct = float(data.get("top_holder_percent", 0))
        holders = int(data.get("holders", 0))
        age_seconds = float(data.get("age_seconds", 0))
        creator_activity = data.get("creator_activity", "unknown")
        txns_per_min = float(data.get("txns_per_minute", 0))

        # ============================================================
        # HARD KILLS — return 0 immediately
        # ============================================================
        if total_vol <= 0 and total_txns <= 0:
            breakdown.update({
                "final_score": 0, "badge": "NONE",
                "momentum_score": 0, "volume_score": 0,
                "mc_growth_score": 0, "risk_score": 0,
                "kill_reason": "DEAD_TOKEN_NO_VOLUME",
                "reasons": ["❌ No volume or transactions — dead token"],
            })
            return 0, breakdown

        if mc_current <= 0:
            breakdown.update({
                "final_score": 0, "badge": "NONE",
                "momentum_score": 0, "volume_score": 0,
                "mc_growth_score": 0, "risk_score": 0,
                "kill_reason": "NO_PRICE_DATA",
                "reasons": ["❌ No market cap data — skipped"],
            })
            return 0, breakdown

        if price_change < -10:
            breakdown.update({
                "final_score": 0, "badge": "NONE",
                "momentum_score": 0, "volume_score": 0,
                "mc_growth_score": 0, "risk_score": 0,
                "kill_reason": "PRICE_DUMPING",
                "reasons": [f"❌ Price falling {price_change:.1f}% — dumping"],
            })
            return 0, breakdown

        # ============================================================
        # 1. PRICE MOMENTUM SCORE (0-100, weight 40%)
        # ============================================================
        momentum_score = 0.0

        # Price change component (0-60 of momentum)
        if price_change >= 50:
            momentum_score += 60
            reasons.append("🚀 Price up 50%+")
        elif price_change >= 20:
            momentum_score += 45
            reasons.append("📈 Price up 20%+")
        elif price_change >= 10:
            momentum_score += 35
            reasons.append("📈 Price up 10%+")
        elif price_change >= 5:
            momentum_score += 25
            reasons.append("↗️ Price up 5%+")
        elif price_change >= 0:
            momentum_score += 15
            reasons.append("➡️ Price stable")
        elif price_change >= -5:
            momentum_score += 5
            reasons.append("↘️ Price slightly down")
        else:
            momentum_score += 0
            reasons.append("📉 Price declining")

        # Buy pressure component (0-40 of momentum)
        if total_txns > 0:
            buy_ratio = buy_count / total_txns
            if buy_ratio >= 0.7:
                momentum_score += 40
                reasons.append("🔥 Strong buy pressure (70%+ buys)")
            elif buy_ratio >= 0.55:
                momentum_score += 25
                reasons.append("👍 Positive buy pressure")
            elif buy_ratio >= 0.45:
                momentum_score += 10
            else:
                momentum_score += 0
                reasons.append("⚠️ More sells than buys")

        momentum_score = min(100, momentum_score)
        breakdown["momentum_score"] = round(momentum_score, 1)

        # ============================================================
        # 2. VOLUME & DEMAND SCORE (0-100, weight 35%)
        # ============================================================
        volume_score = 0.0

        # Total buy volume in SOL (0-40)
        if buy_vol >= 10:
            volume_score += 40
            reasons.append(f"💰 High buy volume ({buy_vol:.1f} SOL)")
        elif buy_vol >= 5:
            volume_score += 30
            reasons.append(f"💰 Good buy volume ({buy_vol:.1f} SOL)")
        elif buy_vol >= 2:
            volume_score += 20
        elif buy_vol >= 0.5:
            volume_score += 10
        elif buy_vol > 0:
            volume_score += 5

        # Buy/sell volume ratio (0-30)
        if total_vol > 0:
            vol_ratio = buy_vol / total_vol
            if vol_ratio >= 0.7:
                volume_score += 30
            elif vol_ratio >= 0.55:
                volume_score += 20
            elif vol_ratio >= 0.45:
                volume_score += 10
            else:
                volume_score += 0

        # Transaction rate / activity (0-30)
        if txns_per_min >= 5:
            volume_score += 30
            reasons.append("⚡ Very active trading")
        elif txns_per_min >= 2:
            volume_score += 20
        elif txns_per_min >= 0.5:
            volume_score += 10
        elif total_txns >= 5:
            volume_score += 5

        volume_score = min(100, volume_score)
        breakdown["volume_score"] = round(volume_score, 1)

        # ============================================================
        # 3. MARKET CAP GROWTH SCORE (0-100, weight 15%)
        # ============================================================
        mc_growth_score = 0.0

        if mc_initial > 0 and mc_current > 0:
            mc_growth_pct = ((mc_current - mc_initial) / mc_initial) * 100

            if mc_growth_pct >= 100:
                mc_growth_score = 100
                reasons.append(f"🔥 MC doubled ({mc_growth_pct:.0f}% growth)")
            elif mc_growth_pct >= 50:
                mc_growth_score = 75
            elif mc_growth_pct >= 20:
                mc_growth_score = 50
            elif mc_growth_pct >= 5:
                mc_growth_score = 30
            elif mc_growth_pct >= 0:
                mc_growth_score = 15
            else:
                mc_growth_score = 0
                reasons.append("📉 MC declining")

            breakdown["mc_growth_pct"] = round(mc_growth_pct, 1)
        else:
            # No initial MC data — neutral
            mc_growth_score = 20
            breakdown["mc_growth_pct"] = 0

        # MC sweet spot bonus: $10K - $500K is ideal
        if 10000 <= mc_current <= 500000:
            mc_growth_score = min(100, mc_growth_score + 10)

        breakdown["mc_growth_score"] = round(mc_growth_score, 1)

        # ============================================================
        # 4. RISK & DISTRIBUTION SCORE (0-100, weight 10%)
        # ============================================================
        risk_score = 50.0  # Start neutral

        # Dev holding
        if dev_pct <= 1:
            risk_score += 20
            reasons.append("✅ Low dev holding")
        elif dev_pct <= 2:
            risk_score += 10
        elif dev_pct <= 5:
            risk_score += 0
        else:
            risk_score -= 20
            reasons.append(f"⚠️ High dev holding ({dev_pct:.1f}%)")

        # Top holder concentration
        if top_pct <= 2:
            risk_score += 15
        elif top_pct <= 5:
            risk_score += 5
        else:
            risk_score -= 10
            reasons.append(f"⚠️ Whale detected ({top_pct:.1f}%)")

        # Holder count
        if holders >= 100:
            risk_score += 15
        elif holders >= 50:
            risk_score += 10
        elif holders >= 20:
            risk_score += 5

        # Creator activity
        if creator_activity == "buying":
            risk_score += 10
            reasons.append("👑 Creator buying")
        elif creator_activity == "selling":
            risk_score -= 15
            reasons.append("🚨 Creator selling")

        risk_score = max(0, min(100, risk_score))
        breakdown["risk_score"] = round(risk_score, 1)

        # ============================================================
        # FINAL WEIGHTED SCORE
        # ============================================================
        raw_score = (
            momentum_score * 0.40 +
            volume_score * 0.35 +
            mc_growth_score * 0.15 +
            risk_score * 0.10
        )

        # ---- Penalty multipliers ----
        if dev_pct > 5:
            raw_score *= 0.5
            reasons.append(f"⚠️ Dev penalty: holding {dev_pct:.1f}%")

        if top_pct > 2:
            raw_score *= 0.5
            reasons.append(f"⚠️ Concentration penalty: top holder {top_pct:.1f}%")

        final_score = max(0, min(100, int(round(raw_score))))

        # ---- Badge ----
        if final_score >= 70:
            badge = "STRONG_BUY"
        elif final_score >= 50:
            badge = "BUY"
        elif final_score >= 30:
            badge = "NEUTRAL"
        else:
            badge = "NONE"

        breakdown["final_score"] = final_score
        breakdown["badge"] = badge
        breakdown["reasons"] = reasons[:6]  # Cap reasons at 6

        # Indicator summaries for frontend
        breakdown["momentum_indicator"] = (
            "strong" if momentum_score >= 60 else
            "positive" if momentum_score >= 30 else
            "weak" if momentum_score >= 10 else "dead"
        )
        breakdown["volume_indicator"] = (
            "high" if volume_score >= 60 else
            "moderate" if volume_score >= 30 else
            "low" if volume_score >= 10 else "dead"
        )
        breakdown["growth_indicator"] = (
            "accelerating" if mc_growth_score >= 60 else
            "growing" if mc_growth_score >= 30 else
            "flat" if mc_growth_score >= 10 else "declining"
        )
        breakdown["risk_indicator"] = (
            "safe" if risk_score >= 60 else
            "moderate" if risk_score >= 40 else "risky"
        )

        return final_score, breakdown


# Singleton
scoring_v3 = SignalScoringV3()
