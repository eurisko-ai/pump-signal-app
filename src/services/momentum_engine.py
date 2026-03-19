"""Multi-timeframe momentum detection using pandas rolling windows"""
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from src.utils.logger import setup_logger

logger = setup_logger("momentum_engine")

class MomentumEngine:
    """Real-time momentum analysis across 1s, 15s, 30s, 1m timeframes"""
    
    def __init__(self, max_trades_per_token: int = 1000):
        """
        Initialize momentum engine.
        
        Args:
            max_trades_per_token: Keep last N trades per token in memory
        """
        self.max_trades = max_trades_per_token
        self.token_trades: Dict[int, list] = {}  # {token_id: [{sol, is_buy, is_whale, timestamp}]}
        self.token_last_seen: Dict[int, datetime] = {}  # {token_id: last_trade_time}
    
    def add_trade(self, token_id: int, amount_sol: float, is_buy: bool, is_whale: bool = False):
        """Add a trade and update momentum calculations"""
        if token_id not in self.token_trades:
            self.token_trades[token_id] = []
        
        trade = {
            'sol': amount_sol,
            'is_buy': is_buy,
            'is_whale': is_whale,
            'timestamp': datetime.utcnow()
        }
        
        self.token_trades[token_id].append(trade)
        self.token_last_seen[token_id] = datetime.utcnow()
        
        # Keep last N trades only
        if len(self.token_trades[token_id]) > self.max_trades:
            self.token_trades[token_id] = self.token_trades[token_id][-self.max_trades:]
    
    def _get_trades_in_window(self, token_id: int, seconds: int) -> list:
        """Get all trades in last N seconds"""
        if token_id not in self.token_trades or not self.token_trades[token_id]:
            return []
        
        cutoff = datetime.utcnow() - timedelta(seconds=seconds)
        return [t for t in self.token_trades[token_id] if t['timestamp'] > cutoff]
    
    def get_momentum_1s(self, token_id: int) -> int:
        """Get 1-second momentum score (0-100)"""
        trades = self._get_trades_in_window(token_id, 1)
        if not trades:
            return 0
        
        buy_volume = sum(t['sol'] for t in trades if t['is_buy'])
        sell_volume = sum(t['sol'] for t in trades if not t['is_buy'])
        total_volume = buy_volume + sell_volume
        
        if total_volume == 0:
            return 0
        
        buy_pressure = (buy_volume - sell_volume) / total_volume
        score = int((buy_pressure + 1) * 50)  # -1 to +1 range → 0-100
        return max(0, min(100, score))
    
    def get_momentum_15s(self, token_id: int) -> Tuple[int, float]:
        """Get 15-second momentum and acceleration"""
        trades_15s = self._get_trades_in_window(token_id, 15)
        trades_prev_15s = self._get_trades_in_window(token_id, 30)
        
        if not trades_15s or not trades_prev_15s:
            return 0, 1.0
        
        # Calculate volume
        vol_15s = sum(t['sol'] for t in trades_15s)
        vol_prev_15s = sum(t['sol'] for t in trades_prev_15s if t not in trades_15s)
        
        if vol_prev_15s == 0:
            acceleration = 2.0 if vol_15s > 0 else 1.0
        else:
            acceleration = vol_15s / vol_prev_15s
        
        # Score based on momentum
        momentum_score = min(100, int(acceleration * 30))
        
        return momentum_score, acceleration
    
    def get_momentum_30s(self, token_id: int) -> int:
        """Get 30-second pump signal score"""
        trades = self._get_trades_in_window(token_id, 30)
        if not trades:
            return 0
        
        # Factors
        trade_count = len(trades)
        buy_volume = sum(t['sol'] for t in trades if t['is_buy'])
        sell_volume = sum(t['sol'] for t in trades if not t['is_buy'])
        whale_buys = sum(1 for t in trades if t['is_whale'] and t['is_buy'])
        
        total_volume = buy_volume + sell_volume
        if total_volume == 0:
            return 0
        
        # Scoring
        volume_factor = min(50, (total_volume / 100)) if total_volume > 0 else 0
        buy_pressure = (buy_volume - sell_volume) / total_volume if total_volume > 0 else 0
        buy_pressure_factor = (buy_pressure + 1) * 25  # 0-50
        whale_factor = min(20, whale_buys * 5)
        
        score = int(volume_factor + buy_pressure_factor + whale_factor)
        return max(0, min(100, score))
    
    def get_momentum_1m(self, token_id: int) -> int:
        """Get 1-minute sustained momentum (consistency check)"""
        trades = self._get_trades_in_window(token_id, 60)
        if not trades:
            return 0
        
        # Check consistency across windows
        trades_15s = self._get_trades_in_window(token_id, 15)
        trades_30s = self._get_trades_in_window(token_id, 30)
        trades_45s = self._get_trades_in_window(token_id, 45)
        
        # If momentum sustained across all windows = high score
        windows_with_momentum = sum([
            len(trades_15s) > 5,
            len(trades_30s) > 10,
            len(trades_45s) > 15
        ])
        
        consistency_score = (windows_with_momentum / 3) * 100
        return int(consistency_score)
    
    def get_whale_concentration(self, token_id: int) -> float:
        """Get top 5 traders concentration ratio (0-1)"""
        trades = self._get_trades_in_window(token_id, 60)
        if not trades:
            return 0.0
        
        # Sum by trader (simplified - in real impl, track by address)
        whale_trades = [t for t in trades if t['is_whale']]
        total_volume = sum(t['sol'] for t in trades)
        whale_volume = sum(t['sol'] for t in whale_trades)
        
        if total_volume == 0:
            return 0.0
        
        return whale_volume / total_volume
    
    def detect_whale_dump(self, token_id: int) -> bool:
        """Detect if top holders are selling (dump risk)"""
        trades_30s = self._get_trades_in_window(token_id, 30)
        if not trades_30s:
            return False
        
        whale_sells = sum(1 for t in trades_30s if t['is_whale'] and not t['is_buy'])
        whale_buys = sum(1 for t in trades_30s if t['is_whale'] and t['is_buy'])
        
        # If more whale sells than buys in last 30s = dump
        return whale_sells > whale_buys and whale_sells > 2
    
    def get_all_metrics(self, token_id: int) -> Dict:
        """Get complete momentum profile"""
        momentum_1s = self.get_momentum_1s(token_id)
        momentum_15s, acceleration_15s = self.get_momentum_15s(token_id)
        momentum_30s = self.get_momentum_30s(token_id)
        momentum_1m = self.get_momentum_1m(token_id)
        whale_concentration = self.get_whale_concentration(token_id)
        is_whale_dump = self.detect_whale_dump(token_id)
        
        # Overall pump signal
        pump_signal = max(momentum_30s, momentum_1m)
        
        return {
            'momentum_1s': momentum_1s,
            'momentum_15s': momentum_15s,
            'acceleration_15s': acceleration_15s,
            'momentum_30s': momentum_30s,
            'momentum_1m': momentum_1m,
            'whale_concentration': whale_concentration,
            'is_whale_dump': is_whale_dump,
            'pump_signal': pump_signal,
            'is_hot': momentum_15s > 50 and not is_whale_dump,
            'timestamp': datetime.utcnow()
        }
    
    def cleanup_old_tokens(self, max_idle_minutes: int = 5):
        """Remove tokens with no trades in N minutes"""
        now = datetime.utcnow()
        cutoff = now - timedelta(minutes=max_idle_minutes)
        
        to_remove = [
            token_id for token_id, last_seen in self.token_last_seen.items()
            if last_seen < cutoff
        ]
        
        for token_id in to_remove:
            del self.token_trades[token_id]
            del self.token_last_seen[token_id]
        
        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} idle tokens")

momentum_engine = MomentumEngine()
