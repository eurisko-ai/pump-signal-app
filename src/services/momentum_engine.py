"""
Momentum Engine — Multi-timeframe rolling window analysis using pandas.

Core of Phase 2: Tracks trade flow at 1s/15s/30s/1m windows per token,
computes momentum metrics in-memory, and flushes snapshots to DB.

Design:
- Each tracked token has an in-memory DataFrame of recent trades (max 2 min)
- Every 1s tick: compute all timeframe metrics for all active tokens
- Every 10s: flush token_momentum rows to DB
- Auto-cleanup: drop tokens with no trades for 5 min
- Max concurrent tokens: 200 (LRU eviction if exceeded)
"""
import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.utils.logger import setup_logger
from src.services.signal_degradation import degradation_engine

logger = setup_logger("momentum_engine")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WHALE_THRESHOLD_SOL = 0.5       # ≥0.5 SOL = whale trade
MAX_TRACKED_TOKENS = 200        # LRU cap
TRADE_WINDOW_SECONDS = 120      # Keep 2 min of trades in memory
STALE_TIMEOUT_SECONDS = 300     # Drop token after 5 min silence
DB_FLUSH_INTERVAL = 10          # Flush to DB every 10 seconds

# Momentum trigger thresholds
PRE_PUMP_MOMENTUM_15S = 2.0     # Volume doubling every 15s
PRE_PUMP_MIN_DURATION_S = 30    # Sustained for 30+ seconds
PRE_PUMP_MIN_UNIQUE_TRADERS = 50
WHALE_DUMP_CONCENTRATION = 0.70  # Top 5 = 70%+ volume
POST_PUMP_VOLUME_SPIKE = 5.0    # 5x baseline


# ---------------------------------------------------------------------------
# Token Trade Buffer (in-memory per token)
# ---------------------------------------------------------------------------
class TokenTradeBuffer:
    """Rolling trade buffer for a single token. Stores raw trades as a DataFrame."""

    __slots__ = ("token_id", "mint", "trades", "last_trade_time",
                 "metrics", "first_momentum_time", "_is_migrated")

    def __init__(self, token_id: int, mint: str, is_migrated: bool = False):
        self.token_id = token_id
        self.mint = mint
        self._is_migrated = is_migrated
        self.trades = pd.DataFrame(columns=[
            "timestamp", "trader", "amount_sol", "direction", "is_whale"
        ])
        self.trades["timestamp"] = pd.to_datetime(self.trades["timestamp"])
        self.last_trade_time = time.monotonic()
        self.first_momentum_time: Optional[float] = None  # When momentum first detected
        self.metrics: Dict = {}

    def add_trade(self, trader: str, amount_sol: float, direction: str, ts: datetime):
        is_whale = amount_sol >= WHALE_THRESHOLD_SOL
        new_row = pd.DataFrame([{
            "timestamp": ts,
            "trader": trader,
            "amount_sol": amount_sol,
            "direction": direction,
            "is_whale": is_whale,
        }])
        self.trades = pd.concat([self.trades, new_row], ignore_index=True)
        self.last_trade_time = time.monotonic()

        # Trim to window
        cutoff = ts - timedelta(seconds=TRADE_WINDOW_SECONDS)
        self.trades = self.trades[self.trades["timestamp"] >= cutoff].reset_index(drop=True)

    def is_stale(self) -> bool:
        return (time.monotonic() - self.last_trade_time) > STALE_TIMEOUT_SECONDS

    def compute_metrics(self, now: datetime) -> Dict:
        """Compute all timeframe metrics. Returns flat dict."""
        df = self.trades
        if df.empty:
            self.metrics = self._empty_metrics()
            return self.metrics

        m = {}

        # ---- 1-SECOND WINDOW ----
        t1 = now - timedelta(seconds=1)
        w1 = df[df["timestamp"] >= t1]
        m["trades_1s"] = len(w1)
        m["volume_1s"] = float(w1["amount_sol"].sum()) if len(w1) else 0.0

        buys_1s = w1[w1["direction"] == "buy"]["amount_sol"].sum() if len(w1) else 0.0
        sells_1s = w1[w1["direction"] == "sell"]["amount_sol"].sum() if len(w1) else 0.0
        total_1s = buys_1s + sells_1s
        m["buy_pressure_1s"] = float((buys_1s - sells_1s) / total_1s) if total_1s > 0 else 0.0
        m["whale_buys_1s"] = int(w1[(w1["is_whale"]) & (w1["direction"] == "buy")].shape[0]) if len(w1) else 0

        # ---- 15-SECOND WINDOW ----
        t15 = now - timedelta(seconds=15)
        t30_back = now - timedelta(seconds=30)
        w15 = df[df["timestamp"] >= t15]
        w15_prev = df[(df["timestamp"] >= t30_back) & (df["timestamp"] < t15)]

        vol_15 = float(w15["amount_sol"].sum()) if len(w15) else 0.0
        vol_15_prev = float(w15_prev["amount_sol"].sum()) if len(w15_prev) else 0.0
        m["momentum_15s"] = (vol_15 / vol_15_prev) if vol_15_prev > 0 else (2.0 if vol_15 > 0 else 0.0)
        m["velocity"] = len(w15) / 15.0 if len(w15) else 0.0

        # Whale concentration: top 5 traders by volume / total volume in 15s window
        if len(w15) > 0:
            trader_vols = w15.groupby("trader")["amount_sol"].sum().sort_values(ascending=False)
            top5_vol = float(trader_vols.head(5).sum())
            total_vol = float(trader_vols.sum())
            m["whale_concentration"] = top5_vol / total_vol if total_vol > 0 else 0.0
        else:
            m["whale_concentration"] = 0.0

        # ---- 30-SECOND WINDOW ----
        t30 = now - timedelta(seconds=30)
        w30 = df[df["timestamp"] >= t30]

        # Pump signal: composite of volume spike + momentum + whale buying
        vol_30 = float(w30["amount_sol"].sum()) if len(w30) else 0.0
        w30_buys = float(w30[w30["direction"] == "buy"]["amount_sol"].sum()) if len(w30) else 0.0
        w30_whale_buys = float(w30[(w30["is_whale"]) & (w30["direction"] == "buy")]["amount_sol"].sum()) if len(w30) else 0.0
        m["pump_signal_30s"] = self._calc_pump_signal(vol_30, m["momentum_15s"], w30_whale_buys, w30_buys)

        # Trend: simple linear regression slope on cumulative buy pressure over 30s
        m["trend_slope"] = self._calc_trend(w30) if len(w30) >= 3 else 0.0

        # ---- 1-MINUTE WINDOW ----
        t60 = now - timedelta(seconds=60)
        w60 = df[df["timestamp"] >= t60]

        # Sustained momentum: compare first-half vs second-half of 60s window
        t60_mid = now - timedelta(seconds=30)
        first_half = df[(df["timestamp"] >= t60) & (df["timestamp"] < t60_mid)]
        second_half = w30

        vol_first = float(first_half["amount_sol"].sum()) if len(first_half) else 0.0
        vol_second = float(second_half["amount_sol"].sum()) if len(second_half) else 0.0
        m["momentum_1m"] = (vol_second / vol_first) if vol_first > 0 else (1.5 if vol_second > 0 else 0.0)

        # Sustainability: consistency across timeframes
        m["sustainability_score"] = self._calc_sustainability(m)

        # ---- AGGREGATE METRICS ----
        m["unique_traders"] = int(df[df["timestamp"] >= t60]["trader"].nunique()) if len(w60) else 0

        # Pump signal score (0-100)
        m["pump_signal_score"] = self._calc_composite_score(m)

        # Determine signal type
        m["signal_type"] = self._classify_signal(m)
        m["is_hot"] = m["pump_signal_score"] >= 70

        self.metrics = m

        # Track momentum duration
        if m["momentum_15s"] >= PRE_PUMP_MOMENTUM_15S and self.first_momentum_time is None:
            self.first_momentum_time = time.monotonic()
        elif m["momentum_15s"] < PRE_PUMP_MOMENTUM_15S * 0.5:
            self.first_momentum_time = None  # Reset if momentum dies

        return m

    # ---------------------------------------------------------------------------
    # Internal scoring helpers
    # ---------------------------------------------------------------------------
    @staticmethod
    def _calc_pump_signal(vol_30: float, momentum_15s: float,
                          whale_buys: float, total_buys: float) -> float:
        """Composite 30s pump signal [0-10]."""
        vol_component = min(vol_30 / 5.0, 3.0)         # Up to 3 pts for 5+ SOL volume
        momentum_component = min(momentum_15s / 2.0, 3.0)  # Up to 3 pts
        whale_component = min(whale_buys / 2.0, 2.0)    # Up to 2 pts
        buy_ratio = (total_buys / (vol_30 + 0.001))
        pressure_component = buy_ratio * 2.0             # Up to 2 pts

        return round(vol_component + momentum_component + whale_component + pressure_component, 2)

    @staticmethod
    def _calc_trend(w30: pd.DataFrame) -> float:
        """Linear regression slope on cumulative buy pressure."""
        if len(w30) < 3:
            return 0.0
        try:
            # Assign numeric index (seconds from first trade)
            ts = (w30["timestamp"] - w30["timestamp"].iloc[0]).dt.total_seconds().values
            signed = w30.apply(
                lambda r: r["amount_sol"] if r["direction"] == "buy" else -r["amount_sol"], axis=1
            ).cumsum().values

            if len(ts) < 2:
                return 0.0

            # np.polyfit degree 1
            slope, _ = np.polyfit(ts, signed, 1)
            return float(slope)
        except Exception:
            return 0.0

    @staticmethod
    def _calc_sustainability(m: Dict) -> float:
        """
        Score 0-1: how consistent is momentum across all 4 timeframes?
        High = steady acceleration. Low = spiky/fading.
        """
        signals = []

        # 1s: is there activity right now?
        signals.append(min(m.get("trades_1s", 0) / 3.0, 1.0))

        # 15s: is momentum positive?
        mom_15 = m.get("momentum_15s", 0)
        signals.append(min(mom_15 / 2.0, 1.0) if mom_15 > 1.0 else 0.0)

        # 30s: is pump signal strong?
        signals.append(min(m.get("pump_signal_30s", 0) / 7.0, 1.0))

        # 1m: is second half stronger than first?
        mom_1m = m.get("momentum_1m", 0)
        signals.append(min(mom_1m / 1.5, 1.0) if mom_1m > 1.0 else 0.0)

        # Sustainability = geometric mean (all must be positive for high score)
        if all(s > 0 for s in signals):
            return float(np.prod(signals) ** (1.0 / len(signals)))
        return float(np.mean(signals) * 0.5)  # Penalize if any timeframe is dead

    def _calc_composite_score(self, m: Dict) -> int:
        """
        Composite momentum score 0-100.

        Weighted:
        - 30s pump signal: 30%
        - 15s momentum: 20%
        - Sustainability: 20%
        - Unique traders: 15%
        - Trend slope: 15%

        Penalties:
        - Whale concentration > 70%: -20
        - Buy pressure negative: -15
        """
        score = 0.0

        # Pump signal (0-10 → 0-30)
        score += min(m.get("pump_signal_30s", 0) / 10.0, 1.0) * 30

        # Momentum 15s (0-4+ → 0-20)
        score += min(m.get("momentum_15s", 0) / 4.0, 1.0) * 20

        # Sustainability (0-1 → 0-20)
        score += m.get("sustainability_score", 0) * 20

        # Unique traders (0-100+ → 0-15)
        score += min(m.get("unique_traders", 0) / 100.0, 1.0) * 15

        # Trend slope (positive is good, 0-0.5+ → 0-15)
        slope = m.get("trend_slope", 0)
        score += min(max(slope, 0) / 0.5, 1.0) * 15

        # ---- Penalties ----
        if m.get("whale_concentration", 0) > WHALE_DUMP_CONCENTRATION:
            score -= 20

        if m.get("buy_pressure_1s", 0) < -0.5:
            score -= 15

        return max(0, min(100, int(round(score))))

    def _classify_signal(self, m: Dict) -> Optional[str]:
        """Classify the current state into a signal type."""
        score = m.get("pump_signal_score", 0)
        momentum_15s = m.get("momentum_15s", 0)
        whale_conc = m.get("whale_concentration", 0)
        unique_traders = m.get("unique_traders", 0)
        sustainability = m.get("sustainability_score", 0)
        buy_pressure = m.get("buy_pressure_1s", 0)

        # Whale dump: high concentration + selling
        if whale_conc > WHALE_DUMP_CONCENTRATION and buy_pressure < -0.3:
            return "WHALE_DUMP"

        # Pre-pump: strong momentum, sustained, organic
        momentum_duration = 0
        if self.first_momentum_time is not None:
            momentum_duration = time.monotonic() - self.first_momentum_time

        if (not self._is_migrated
                and momentum_15s >= PRE_PUMP_MOMENTUM_15S
                and momentum_duration >= PRE_PUMP_MIN_DURATION_S
                and unique_traders >= PRE_PUMP_MIN_UNIQUE_TRADERS):
            return "PRE_PUMP"

        # Post-migration pump
        if (self._is_migrated
                and momentum_15s >= POST_PUMP_VOLUME_SPIKE
                and buy_pressure > 0.3):
            return "PUMP_DETECTED"

        # Fading: was hot, now dying
        if score < 30 and sustainability < 0.2 and m.get("volume_1s", 0) < 0.01:
            return "FADING"

        return None

    @staticmethod
    def _empty_metrics() -> Dict:
        return {
            "trades_1s": 0, "volume_1s": 0.0, "buy_pressure_1s": 0.0,
            "whale_buys_1s": 0, "momentum_15s": 0.0, "whale_concentration": 0.0,
            "velocity": 0.0, "pump_signal_30s": 0.0, "trend_slope": 0.0,
            "momentum_1m": 0.0, "sustainability_score": 0.0, "unique_traders": 0,
            "pump_signal_score": 0, "signal_type": None, "is_hot": False,
        }


# ---------------------------------------------------------------------------
# Momentum Engine (orchestrator)
# ---------------------------------------------------------------------------
class MomentumEngine:
    """
    Manages all token trade buffers. Called by trade_tracker on each trade.
    Runs a 1s tick loop to recompute metrics and a 10s flush loop to persist.
    """

    def __init__(self):
        # OrderedDict for LRU: most recently accessed at end
        self._buffers: OrderedDict[int, TokenTradeBuffer] = OrderedDict()
        self._running = False
        self._db_pool = None
        self._pending_alerts: List[Tuple[int, str, Dict]] = []  # (token_id, mint, metrics)

    @property
    def tracked_count(self) -> int:
        return len(self._buffers)

    @property
    def hot_tokens(self) -> List[Dict]:
        """Return list of hot token summaries."""
        return [
            {"token_id": buf.token_id, "mint": buf.mint, **buf.metrics}
            for buf in self._buffers.values()
            if buf.metrics.get("is_hot")
        ]

    def get_buffer(self, token_id: int) -> Optional[TokenTradeBuffer]:
        return self._buffers.get(token_id)

    # ---------------------------------------------------------------------------
    # Trade ingestion
    # ---------------------------------------------------------------------------
    def register_token(self, token_id: int, mint: str, is_migrated: bool = False):
        """Start tracking a token. Creates buffer if not exists."""
        if token_id in self._buffers:
            self._buffers.move_to_end(token_id)
            return
        # Evict oldest if at capacity
        while len(self._buffers) >= MAX_TRACKED_TOKENS:
            evicted_id, evicted_buf = self._buffers.popitem(last=False)
            degradation_engine.remove_token(evicted_id)
            logger.debug(f"Evicted token {evicted_buf.mint[:16]} (LRU)")

        self._buffers[token_id] = TokenTradeBuffer(token_id, mint, is_migrated)
        degradation_engine.register_token(token_id, mint)
        logger.debug(f"Tracking token {mint[:16]} (id={token_id}, migrated={is_migrated})")

    def add_trade(self, token_id: int, trader: str, amount_sol: float,
                  direction: str, ts: datetime, market_cap_sol: float = 0.0):
        """Ingest a single trade. Must call register_token first."""
        buf = self._buffers.get(token_id)
        if buf is None:
            return
        buf.add_trade(trader, amount_sol, direction, ts)
        self._buffers.move_to_end(token_id)
        # Feed degradation engine
        degradation_engine.on_trade(token_id, amount_sol, direction, market_cap_sol)

    def mark_migrated(self, token_id: int):
        """Update token status to migrated (affects signal classification)."""
        buf = self._buffers.get(token_id)
        if buf:
            buf._is_migrated = True

    # ---------------------------------------------------------------------------
    # Tick loop (1s) — compute metrics
    # ---------------------------------------------------------------------------
    async def start(self, db_pool):
        """Start the 1s tick + 10s flush loops."""
        self._db_pool = db_pool
        self._running = True
        logger.info("MomentumEngine started")

        # Run both loops concurrently
        await asyncio.gather(
            self._tick_loop(),
            self._flush_loop(),
        )

    async def stop(self):
        self._running = False

    async def _tick_loop(self):
        """Every 1 second: recompute metrics for all active tokens."""
        while self._running:
            try:
                now = datetime.utcnow()
                stale_ids = []

                for token_id, buf in list(self._buffers.items()):
                    if buf.is_stale():
                        stale_ids.append(token_id)
                        continue

                    old_signal = buf.metrics.get("signal_type")
                    m = buf.compute_metrics(now)
                    new_signal = m.get("signal_type")

                    # Detect signal transitions → queue alert
                    if new_signal and new_signal != old_signal:
                        self._pending_alerts.append((token_id, buf.mint, dict(m)))

                # Cleanup stale
                for tid in stale_ids:
                    buf = self._buffers.pop(tid, None)
                    if buf:
                        logger.debug(f"Removed stale token {buf.mint[:16]}")
                    degradation_engine.remove_token(tid)

                # Run degradation tick (evaluates all tracked tokens)
                try:
                    degradation_engine.tick()
                except Exception as de:
                    logger.error(f"Degradation tick error: {de}")

            except Exception as e:
                logger.error(f"Tick loop error: {e}")

            await asyncio.sleep(1.0)

    async def _flush_loop(self):
        """Every 10 seconds: persist token_momentum snapshots to DB."""
        while self._running:
            await asyncio.sleep(DB_FLUSH_INTERVAL)
            try:
                await self._flush_to_db()
            except Exception as e:
                logger.error(f"Flush loop error: {e}")

    async def _flush_to_db(self):
        """Upsert all active token momentum rows."""
        if not self._db_pool or not self._buffers:
            return

        now = datetime.utcnow()
        rows = []
        for token_id, buf in self._buffers.items():
            m = buf.metrics
            if not m:
                continue
            rows.append((
                token_id,
                m.get("trades_1s", 0),
                m.get("volume_1s", 0.0),
                m.get("buy_pressure_1s", 0.0),
                m.get("whale_buys_1s", 0),
                m.get("momentum_15s", 0.0),
                m.get("whale_concentration", 0.0),
                m.get("velocity", 0.0),
                m.get("pump_signal_30s", 0.0),
                m.get("trend_slope", 0.0),
                m.get("momentum_1m", 0.0),
                m.get("sustainability_score", 0.0),
                m.get("pump_signal_score", 0),
                m.get("unique_traders", 0),
                m.get("is_hot", False),
                m.get("signal_type"),
                now,
            ))

        if not rows:
            return

        async with self._db_pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO token_momentum (
                    token_id, trades_1s, volume_1s, buy_pressure_1s, whale_buys_1s,
                    momentum_15s, whale_concentration, velocity,
                    pump_signal_30s, trend_slope, momentum_1m, sustainability_score,
                    pump_signal_score, unique_traders, is_hot, signal_type, last_updated
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                ON CONFLICT (token_id) DO UPDATE SET
                    trades_1s = EXCLUDED.trades_1s,
                    volume_1s = EXCLUDED.volume_1s,
                    buy_pressure_1s = EXCLUDED.buy_pressure_1s,
                    whale_buys_1s = EXCLUDED.whale_buys_1s,
                    momentum_15s = EXCLUDED.momentum_15s,
                    whale_concentration = EXCLUDED.whale_concentration,
                    velocity = EXCLUDED.velocity,
                    pump_signal_30s = EXCLUDED.pump_signal_30s,
                    trend_slope = EXCLUDED.trend_slope,
                    momentum_1m = EXCLUDED.momentum_1m,
                    sustainability_score = EXCLUDED.sustainability_score,
                    pump_signal_score = EXCLUDED.pump_signal_score,
                    unique_traders = EXCLUDED.unique_traders,
                    is_hot = EXCLUDED.is_hot,
                    signal_type = EXCLUDED.signal_type,
                    last_updated = EXCLUDED.last_updated
                """,
                rows,
            )

        logger.debug(f"Flushed {len(rows)} momentum rows to DB")

    # ---------------------------------------------------------------------------
    # Alert queue
    # ---------------------------------------------------------------------------
    def drain_alerts(self) -> List[Tuple[int, str, Dict]]:
        """Pop all pending alerts. Called by momentum_alerter."""
        alerts = self._pending_alerts[:]
        self._pending_alerts.clear()
        return alerts

    def get_stats(self) -> Dict:
        return {
            "tracked_tokens": len(self._buffers),
            "hot_tokens": sum(1 for b in self._buffers.values() if b.metrics.get("is_hot")),
            "pending_alerts": len(self._pending_alerts),
        }


# Singleton
momentum_engine = MomentumEngine()
