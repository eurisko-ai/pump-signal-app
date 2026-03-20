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
                 market_cap_sol: float = 0.0):
        """
        Called on every incoming trade. Updates health state.
        
        Args:
            token_id: Token ID
            amount_sol: Trade amount in SOL
            direction: "buy" or "sell"
            market_cap_sol: Current market cap in SOL (from trade event)
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
        # 3. VOLUME MOMENTUM CHECK
        # =============================================
        # We get volume data from momentum engine buffers during apply_degradation
        # This is handled at score-application time since volume data lives in
        # the momentum engine, not here. See apply_signal_degradation().

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

    # Update reasons list
    existing_reasons = breakdown.get("reasons", [])
    breakdown["reasons"] = (existing_reasons + degrade_reasons)[:8]

    return final_score, breakdown


# Singleton
degradation_engine = SignalDegradationEngine()
