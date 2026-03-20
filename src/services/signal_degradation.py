"""
Signal Degradation Service — Real-time signal health monitoring.

Prevents stale signals by continuously demoting tokens based on:
1. Trade inactivity (no trades → demote/kill)
2. Price drops (crash detection)
3. Volume momentum (dying volume / sell pressure)

Runs on the momentum engine's 1s tick loop and modifies effective scores.
"""
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from src.utils.logger import setup_logger

logger = setup_logger("signal_degradation")


class TokenHealthState:
    """Per-token real-time health tracking."""
    __slots__ = (
        "token_id", "mint",
        "last_trade_time",          # monotonic timestamp of last trade
        "last_trade_utc",           # UTC datetime of last trade
        "price_history_1m",         # list of (monotonic_ts, market_cap_sol) for 1-min window
        "volume_1m_buys_sol",       # rolling 1-min buy volume in SOL
        "volume_1m_sells_sol",      # rolling 1-min sell volume in SOL
        "degradation_points",       # total demotion points to subtract from score
        "kill",                     # if True, score forced to 0
        "kill_reason",              # why it was killed
        "degradation_reasons",      # list of active demotion reasons
        "last_price",               # last known market cap SOL (proxy for price)
        "bonus_points",             # bonus points for strong momentum
        # --- Transaction size tracking ---
        "trade_history_1m",         # list of (monotonic_ts, amount_sol, direction, trader) for 1-min window
        "largest_buy_1m_sol",       # largest single buy in last 1m
        "largest_sell_1m_sol",      # largest single sell in last 1m
        "large_trade_bonus",        # instant bonus/penalty from large trade detection
        "whale_activity_label",     # whale accumulation / whale exit / neutral
        # --- Holder concentration ---
        "unique_traders_1m",        # distinct trader addresses in 1m window
        "holder_concentration_pts", # penalty/bonus from holder concentration
    )

    def __init__(self, token_id: int, mint: str):
        self.token_id = token_id
        self.mint = mint
        self.last_trade_time = time.monotonic()
        self.last_trade_utc = datetime.utcnow()
        self.price_history_1m = []  # [(monotonic_ts, price)]
        self.volume_1m_buys_sol = 0.0
        self.volume_1m_sells_sol = 0.0
        self.degradation_points = 0
        self.kill = False
        self.kill_reason = None
        self.degradation_reasons = []
        self.last_price = 0.0
        self.bonus_points = 0
        # --- Transaction size tracking ---
        self.trade_history_1m = []  # [(monotonic_ts, amount_sol, direction, trader)]
        self.largest_buy_1m_sol = 0.0
        self.largest_sell_1m_sol = 0.0
        self.large_trade_bonus = 0  # net pts from large trade detection
        self.whale_activity_label = "neutral"
        # --- Holder concentration ---
        self.unique_traders_1m = 0
        self.holder_concentration_pts = 0


class SignalDegradationEngine:
    """
    Monitors all tracked tokens for signal staleness and demotes/kills as needed.
    
    Called from MomentumEngine tick loop (every 1s) and from trade ingestion.
    """

    def __init__(self):
        self._states: Dict[int, TokenHealthState] = {}

    @property
    def tracked_count(self) -> int:
        return len(self._states)

    def register_token(self, token_id: int, mint: str):
        """Start health tracking for a token."""
        if token_id not in self._states:
            self._states[token_id] = TokenHealthState(token_id, mint)

    def remove_token(self, token_id: int):
        """Stop tracking a token."""
        self._states.pop(token_id, None)

    def on_trade(self, token_id: int, amount_sol: float, direction: str,
                 market_cap_sol: float = 0.0, trader: str = ""):
        """
        Called on every incoming trade. Updates health state.
        
        Args:
            token_id: Token ID
            amount_sol: Trade amount in SOL
            direction: "buy" or "sell"
            market_cap_sol: Current market cap in SOL (from trade event)
            trader: Trader public key (for holder concentration tracking)
        """
        state = self._states.get(token_id)
        if state is None:
            return

        now_mono = time.monotonic()
        state.last_trade_time = now_mono
        state.last_trade_utc = datetime.utcnow()

        # Update price history
        if market_cap_sol > 0:
            state.last_price = market_cap_sol
            state.price_history_1m.append((now_mono, market_cap_sol))

        # Trim price history to 1-minute window
        cutoff = now_mono - 60
        state.price_history_1m = [
            (ts, p) for ts, p in state.price_history_1m if ts >= cutoff
        ]

        # --- Transaction size tracking ---
        state.trade_history_1m.append((now_mono, amount_sol, direction, trader))
        # Trim trade history to 1-minute window
        state.trade_history_1m = [
            (ts, amt, d, t) for ts, amt, d, t in state.trade_history_1m if ts >= cutoff
        ]

        # Recompute rolling buy/sell volumes from trade history
        buy_vol = 0.0
        sell_vol = 0.0
        largest_buy = 0.0
        largest_sell = 0.0
        traders_set = set()
        for _ts, amt, d, t in state.trade_history_1m:
            if t:
                traders_set.add(t)
            if d == "buy":
                buy_vol += amt
                if amt > largest_buy:
                    largest_buy = amt
            else:
                sell_vol += amt
                if amt > largest_sell:
                    largest_sell = amt

        state.unique_traders_1m = len(traders_set)

        state.volume_1m_buys_sol = buy_vol
        state.volume_1m_sells_sol = sell_vol
        state.largest_buy_1m_sol = largest_buy
        state.largest_sell_1m_sol = largest_sell

        # --- Large trade instant detection (applied on THIS trade) ---
        self._score_large_trade(state, amount_sol, direction)

    def tick(self) -> Dict[int, Dict]:
        """
        Run degradation checks for all tracked tokens.
        Called every 1 second from momentum engine tick loop.
        
        Returns dict of {token_id: degradation_info} for tokens with changes.
        """
        now_mono = time.monotonic()
        results = {}

        stale_ids = []
        for token_id, state in self._states.items():
            old_points = state.degradation_points
            old_kill = state.kill

            self._evaluate_token(state, now_mono)

            # Only report if something changed
            if state.degradation_points != old_points or state.kill != old_kill:
                results[token_id] = self.get_degradation_info(token_id)

            # Auto-remove tokens that have been dead for 5+ minutes
            seconds_since_trade = now_mono - state.last_trade_time
            if seconds_since_trade > 300:
                stale_ids.append(token_id)

        for tid in stale_ids:
            self._states.pop(tid, None)

        return results

    @staticmethod
    def _score_large_trade(state: TokenHealthState, amount_sol: float, direction: str):
        """
        Compute instant bonus/penalty for a single large trade.
        Called on every trade from on_trade(). Accumulated in state.large_trade_bonus.
        The bonus is recalculated fresh each tick from the 1m trade history.
        """
        # We recalculate in _evaluate_whale_activity instead of accumulating
        # (to keep it window-based, not unbounded). This method is intentionally
        # a no-op now — whale scoring happens in tick via _evaluate_whale_activity.
        pass

    def _evaluate_token(self, state: TokenHealthState, now_mono: float):
        """Evaluate a single token's health and compute degradation."""
        state.degradation_points = 0
        state.bonus_points = 0
        state.kill = False
        state.kill_reason = None
        state.degradation_reasons = []

        seconds_since_trade = now_mono - state.last_trade_time

        # =============================================
        # 1. TRADE ACTIVITY CHECK
        # =============================================
        if seconds_since_trade >= 60:
            # No trades in 60s → KILL
            state.kill = True
            state.kill_reason = f"DEAD_NO_TRADES_{int(seconds_since_trade)}s"
            state.degradation_reasons.append(
                f"💀 No trades in {int(seconds_since_trade)}s — token dead"
            )
            return  # No need to check further
        elif seconds_since_trade >= 15:
            # No trades in 15s → heavy demotion
            state.degradation_points += 50
            state.degradation_reasons.append(
                f"⚠️ No trades in {int(seconds_since_trade)}s — stalling"
            )
        elif seconds_since_trade >= 5:
            # No trades in 5s → moderate demotion
            state.degradation_points += 30
            state.degradation_reasons.append(
                f"⏳ No trades in {int(seconds_since_trade)}s — slowing"
            )

        # =============================================
        # 2. PRICE STABILITY CHECK (1-min window)
        # =============================================
        price_change_pct = self._calc_price_change_1m(state, now_mono)

        if price_change_pct is not None:
            if price_change_pct <= -20:
                # Crash: >20% drop in 1 min
                state.degradation_points += 40
                state.degradation_reasons.append(
                    f"📉 Price crashed {price_change_pct:.1f}% in 1m"
                )
            elif price_change_pct <= -10:
                # Significant drop
                state.degradation_points += 20
                state.degradation_reasons.append(
                    f"📉 Price down {price_change_pct:.1f}% in 1m"
                )
            elif price_change_pct >= 100:
                # Massive pump — bonus
                state.bonus_points += 10
                state.degradation_reasons.append(
                    f"🚀 Price up {price_change_pct:.1f}% in 1m — real momentum"
                )

        # =============================================
        # 3. TRANSACTION SIZE WEIGHTING
        # =============================================
        self._evaluate_whale_activity(state, now_mono)

        # =============================================
        # 4. HOLDER CONCENTRATION CHECK
        # =============================================
        self._evaluate_holder_concentration(state)

        # =============================================
        # 5. VOLUME MOMENTUM CHECK
        # =============================================
        # Additional volume checks handled in apply_signal_degradation()
        # since raw volume data lives in momentum engine.

    @staticmethod
    def _evaluate_whale_activity(state: TokenHealthState, now_mono: float):
        """
        Transaction-size-weighted analysis on the 1-minute trade window.

        Applies:
        1. Large-trade detection bonus/penalty per individual trade.
        2. Weighted buy-vs-sell volume comparison.
        3. Whale-activity concentration label.
        """
        # Trim trade history (safety, in case tick fires before on_trade trimmed)
        cutoff = now_mono - 60
        state.trade_history_1m = [
            (ts, amt, d, t) for ts, amt, d, t in state.trade_history_1m if ts >= cutoff
        ]

        trades = state.trade_history_1m
        if not trades:
            state.whale_activity_label = "neutral"
            state.large_trade_bonus = 0
            return

        # ----- 1. Per-trade large transaction scoring -----
        large_trade_pts = 0
        for _ts, amt, d, _t in trades:
            if d == "buy":
                if amt >= 10.0:
                    large_trade_pts += 30   # major whale buy
                elif amt >= 5.0:
                    large_trade_pts += 20   # whale buy
                elif amt >= 1.0:
                    large_trade_pts += 10   # large buy
            else:  # sell
                if amt >= 10.0:
                    large_trade_pts -= 50   # dumping
                elif amt >= 5.0:
                    large_trade_pts -= 40   # danger sell
                elif amt >= 1.0:
                    large_trade_pts -= 20   # warning sell

        # Cap so a single window can't swing more than ±60 net
        large_trade_pts = max(-60, min(60, large_trade_pts))
        state.large_trade_bonus = large_trade_pts

        if large_trade_pts > 0:
            state.bonus_points += large_trade_pts
        elif large_trade_pts < 0:
            state.degradation_points += abs(large_trade_pts)

        # ----- 2. Weighted buy vs sell volume -----
        buy_vol = state.volume_1m_buys_sol
        sell_vol = state.volume_1m_sells_sol
        total_vol = buy_vol + sell_vol

        if total_vol > 0:
            if sell_vol > buy_vol:
                # Net selling pressure — the bigger the gap the worse
                sell_dominance = (sell_vol - buy_vol) / total_vol  # 0-1
                penalty = int(round(sell_dominance * 30))  # up to -30
                state.degradation_points += penalty
                if penalty >= 15:
                    state.degradation_reasons.append(
                        f"🐋💨 Sell volume dominates: {sell_vol:.2f} vs {buy_vol:.2f} SOL"
                    )
            elif buy_vol > sell_vol * 1.5:
                # Strong net buying — boost
                buy_dominance = (buy_vol - sell_vol) / total_vol  # 0-1
                bonus = int(round(buy_dominance * 20))  # up to +20
                state.bonus_points += bonus
                if bonus >= 10:
                    state.degradation_reasons.append(
                        f"🐋🟢 Buy volume dominates: {buy_vol:.2f} vs {sell_vol:.2f} SOL"
                    )

        # ----- 3. Whale activity concentration -----
        large_buy_vol = sum(amt for _ts, amt, d, _t in trades if d == "buy" and amt >= 1.0)
        large_sell_vol = sum(amt for _ts, amt, d, _t in trades if d == "sell" and amt >= 1.0)
        large_total = large_buy_vol + large_sell_vol
        total_trades = len(trades)
        large_trade_count = sum(1 for _ts, amt, _d, _t in trades if amt >= 1.0)

        if total_trades > 0 and large_trade_count > 0:
            large_ratio = large_trade_count / total_trades

            if large_total > 0:
                large_buy_pct = large_buy_vol / large_total

                if large_buy_pct >= 0.80 and large_ratio >= 0.3:
                    state.whale_activity_label = "whale_accumulation"
                    state.bonus_points += 15
                    state.degradation_reasons.append(
                        f"🐋📈 Whale accumulation: {large_buy_pct:.0%} of large-trade vol is buys"
                    )
                elif (1 - large_buy_pct) >= 0.80 and large_ratio >= 0.3:
                    state.whale_activity_label = "whale_exit"
                    state.degradation_points += 25
                    state.degradation_reasons.append(
                        f"🐋📉 Whale exit: {(1-large_buy_pct):.0%} of large-trade vol is sells"
                    )
                else:
                    state.whale_activity_label = "mixed"
            else:
                state.whale_activity_label = "retail_only"
        else:
            state.whale_activity_label = "neutral"

        # Log large-trade reasons for visibility
        if state.largest_buy_1m_sol >= 5.0:
            state.degradation_reasons.append(
                f"🐋 Largest buy in 1m: {state.largest_buy_1m_sol:.2f} SOL"
            )
        if state.largest_sell_1m_sol >= 5.0:
            state.degradation_reasons.append(
                f"🐋⚠️ Largest sell in 1m: {state.largest_sell_1m_sol:.2f} SOL"
            )

    @staticmethod
    def _evaluate_holder_concentration(state: TokenHealthState):
        """
        Penalize tokens with low unique trader counts (proxy for holder concentration).

        Uses unique traders seen in the 1-minute trade window.
        Fewer distinct traders = higher concentration risk = any single wallet
        can crash the price.

        Thresholds:
          <25 unique traders → -50 pts (DANGER)
          <50 unique traders → -30 pts (WARNING)
          <100 unique traders → -10 pts (CAUTION)
          ≥500 unique traders → +5 pts (healthy)
        """
        traders = state.unique_traders_1m
        state.holder_concentration_pts = 0

        if traders < 25:
            state.holder_concentration_pts = -50
            state.degradation_points += 50
            state.degradation_reasons.append(
                f"🚨 Only {traders} unique traders — extreme concentration risk"
            )
        elif traders < 50:
            state.holder_concentration_pts = -30
            state.degradation_points += 30
            state.degradation_reasons.append(
                f"⚠️ Only {traders} unique traders — high concentration"
            )
        elif traders < 100:
            state.holder_concentration_pts = -10
            state.degradation_points += 10
            state.degradation_reasons.append(
                f"⚡ {traders} unique traders — moderate concentration"
            )
        elif traders >= 500:
            state.holder_concentration_pts = 5
            state.bonus_points += 5
            state.degradation_reasons.append(
                f"✅ {traders} unique traders — healthy distribution"
            )

    def _calc_price_change_1m(self, state: TokenHealthState,
                              now_mono: float) -> Optional[float]:
        """Calculate price change % over the last minute."""
        if len(state.price_history_1m) < 2:
            return None

        # Get earliest price in window
        earliest_price = state.price_history_1m[0][1]
        latest_price = state.price_history_1m[-1][1]

        if earliest_price <= 0:
            return None

        return ((latest_price - earliest_price) / earliest_price) * 100

    def get_degradation_info(self, token_id: int) -> Optional[Dict]:
        """Get current degradation state for a token."""
        state = self._states.get(token_id)
        if state is None:
            return None

        return {
            "token_id": token_id,
            "mint": state.mint,
            "degradation_points": state.degradation_points,
            "bonus_points": state.bonus_points,
            "kill": state.kill,
            "kill_reason": state.kill_reason,
            "degradation_reasons": state.degradation_reasons,
            "seconds_since_trade": round(time.monotonic() - state.last_trade_time, 1),
            "last_trade_utc": state.last_trade_utc.isoformat() if state.last_trade_utc else None,
            "last_price": state.last_price,
            # --- Transaction size fields ---
            "buy_volume_1m_sol": state.volume_1m_buys_sol,
            "sell_volume_1m_sol": state.volume_1m_sells_sol,
            "largest_buy_1m_sol": state.largest_buy_1m_sol,
            "largest_sell_1m_sol": state.largest_sell_1m_sol,
            "large_trade_bonus": state.large_trade_bonus,
            "whale_activity": state.whale_activity_label,
            "total_trades_1m": len(state.trade_history_1m),
            "large_trades_1m": sum(1 for _ts, amt, _d, _t in state.trade_history_1m if amt >= 1.0),
            # --- Holder concentration ---
            "unique_traders_1m": state.unique_traders_1m,
            "holder_concentration_pts": state.holder_concentration_pts,
        }

    def get_all_degradation(self) -> Dict[int, Dict]:
        """Get degradation info for all tracked tokens."""
        return {
            tid: self.get_degradation_info(tid)
            for tid in self._states
        }


def apply_signal_degradation(
    base_score: int,
    base_breakdown: Dict,
    degradation_info: Optional[Dict],
    volume_1m_sol: float = 0.0,
    buy_count_1m: int = 0,
    sell_count_1m: int = 0,
    dexscreener_profile: Optional[Dict] = None,
) -> Tuple[int, Dict]:
    """
    Apply real-time degradation to a token's base signal score.

    Args:
        base_score: Original score from SignalScoringV3
        base_breakdown: Original breakdown dict
        degradation_info: From SignalDegradationEngine.get_degradation_info()
        volume_1m_sol: 1-minute volume in SOL (from momentum engine)
        buy_count_1m: Buy count in last minute
        sell_count_1m: Sell count in last minute
        dexscreener_profile: Cached DexScreener profile data (optional)

    Returns:
        (adjusted_score, adjusted_breakdown)
    """
    breakdown = dict(base_breakdown)
    adjusted = float(base_score)
    degrade_reasons = []

    # ---- Volume momentum penalties (applied here because volume data is external) ----
    if volume_1m_sol < 0.5:
        adjusted -= 25
        degrade_reasons.append(f"📉 Low 1m volume ({volume_1m_sol:.2f} SOL)")

    total_1m = buy_count_1m + sell_count_1m
    if total_1m > 0 and sell_count_1m > buy_count_1m:
        adjusted -= 15
        sell_ratio = sell_count_1m / total_1m * 100
        degrade_reasons.append(f"⚠️ Sell pressure ({sell_ratio:.0f}% sells)")

    # ---- Size-weighted volume cross-check (from degradation state) ----
    if degradation_info:
        buy_vol_sol = degradation_info.get("buy_volume_1m_sol", 0)
        sell_vol_sol = degradation_info.get("sell_volume_1m_sol", 0)
        whale_label = degradation_info.get("whale_activity", "neutral")
        largest_sell = degradation_info.get("largest_sell_1m_sol", 0)

        # Severe sell-volume dominance that wasn't caught by count-based check
        if sell_vol_sol > 0 and buy_vol_sol > 0:
            vol_ratio = sell_vol_sol / (buy_vol_sol + sell_vol_sol)
            if vol_ratio >= 0.7 and sell_vol_sol >= 2.0:
                adjusted -= 20
                degrade_reasons.append(
                    f"🐋💨 SOL sell vol {sell_vol_sol:.1f} >> buy vol {buy_vol_sol:.1f}"
                )

        # Whale dump detection: single large sell ≥10 SOL
        if largest_sell >= 10.0:
            adjusted -= 15
            degrade_reasons.append(
                f"🚨 Whale dump detected: {largest_sell:.1f} SOL single sell"
            )

    # ---- Apply degradation engine results ----
    if degradation_info:
        if degradation_info.get("kill"):
            breakdown["degraded"] = True
            breakdown["degraded_to_zero"] = True
            breakdown["kill_reason"] = degradation_info.get("kill_reason", "DEAD")
            breakdown["degradation_reasons"] = degradation_info.get("degradation_reasons", [])
            breakdown["original_score"] = base_score
            breakdown["final_score"] = 0
            breakdown["badge"] = "NONE"
            breakdown["seconds_since_trade"] = degradation_info.get("seconds_since_trade", 0)
            return 0, breakdown

        adjusted -= degradation_info.get("degradation_points", 0)
        adjusted += degradation_info.get("bonus_points", 0)
        degrade_reasons.extend(degradation_info.get("degradation_reasons", []))

    # ---- DexScreener legitimacy adjustment (if profile data available) ----
    if dexscreener_profile is not None:
        from src.services.dexscreener import dexscreener_service
        dex_adj, dex_reasons = dexscreener_service.score_legitimacy(dexscreener_profile)
        adjusted += dex_adj
        degrade_reasons.extend(dex_reasons)
        breakdown["dexscreener_score"] = dex_adj
        breakdown["dexscreener_verified"] = bool(
            dexscreener_profile and dexscreener_profile.get("verified")
        )

    # Clamp
    final_score = max(0, min(100, int(round(adjusted))))

    # Re-badge based on adjusted score
    if final_score >= 70:
        badge = "STRONG_BUY"
    elif final_score >= 50:
        badge = "BUY"
    elif final_score >= 30:
        badge = "NEUTRAL"
    else:
        badge = "NONE"

    if final_score != base_score:
        breakdown["degraded"] = True
        breakdown["original_score"] = base_score
        breakdown["original_badge"] = base_breakdown.get("badge", "NONE")

    breakdown["final_score"] = final_score
    breakdown["badge"] = badge
    breakdown["degradation_reasons"] = degrade_reasons
    breakdown["seconds_since_trade"] = (
        degradation_info.get("seconds_since_trade", 0) if degradation_info else 0
    )

    # Attach transaction-size metadata for downstream consumers
    if degradation_info:
        breakdown["buy_volume_1m_sol"] = degradation_info.get("buy_volume_1m_sol", 0)
        breakdown["sell_volume_1m_sol"] = degradation_info.get("sell_volume_1m_sol", 0)
        breakdown["largest_buy_1m_sol"] = degradation_info.get("largest_buy_1m_sol", 0)
        breakdown["largest_sell_1m_sol"] = degradation_info.get("largest_sell_1m_sol", 0)
        breakdown["whale_activity"] = degradation_info.get("whale_activity", "neutral")
        breakdown["large_trades_1m"] = degradation_info.get("large_trades_1m", 0)
        breakdown["total_trades_1m"] = degradation_info.get("total_trades_1m", 0)
        breakdown["unique_traders_1m"] = degradation_info.get("unique_traders_1m", 0)
        breakdown["holder_concentration_pts"] = degradation_info.get("holder_concentration_pts", 0)

    # Update reasons list
    existing_reasons = breakdown.get("reasons", [])
    breakdown["reasons"] = (existing_reasons + degrade_reasons)[:8]

    return final_score, breakdown


# Singleton
degradation_engine = SignalDegradationEngine()
