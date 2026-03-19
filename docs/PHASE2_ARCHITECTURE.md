# Phase 2: High-Frequency Momentum Detection

## Overview

Phase 1 (current): Snapshot scoring at migration time → everyone sees it, too late.
Phase 2: Track trade flow at 1s/15s/30s/1m intervals → catch momentum BEFORE migration.

## Architecture Diagram

```
PumpPortal WebSocket
         │
         ├──────────────────────────────────────┐
         │ subscribeNewToken                     │ subscribeTokenTrade
         │ subscribeMigration                    │ (per-token, up to 200)
         ▼                                       ▼
┌─────────────────────┐              ┌─────────────────────┐
│ websocket_scanner   │──track_token─→│  trade_tracker      │
│ (existing Phase 1)  │              │  (new Phase 2)      │
│                     │              │                     │
│ • Token creation    │              │ • Parse trades      │
│ • Migration detect  │              │ • Feed momentum eng │
│ • Score + alert     │              │ • Batch DB inserts  │
└─────────────────────┘              └─────────┬───────────┘
                                               │
                                               │ add_trade() <1ms
                                               ▼
                                    ┌─────────────────────────┐
                                    │  momentum_engine        │
                                    │  (pandas in-memory)     │
                                    │                         │
                                    │  Per-token DataFrame:   │
                                    │  ┌─────────────────┐    │
                                    │  │ 1s   window     │    │
                                    │  │ 15s  window     │    │
                                    │  │ 30s  window     │    │
                                    │  │ 1min window     │    │
                                    │  └─────────────────┘    │
                                    │                         │
                                    │  1s tick → recompute    │
                                    │  10s flush → DB upsert  │
                                    │  5min stale → evict     │
                                    └─────────┬───────────────┘
                                              │
                                              │ drain_alerts()
                                              ▼
                                    ┌─────────────────────────┐
                                    │  momentum_alerter       │
                                    │                         │
                                    │  • Signal transitions   │
                                    │  • Dedup (5min window)  │
                                    │  • Telegram formatting  │
                                    │  • DB audit trail       │
                                    └─────────────────────────┘
```

## New Files

| File | Purpose |
|------|---------|
| `src/services/momentum_engine.py` | Core: pandas rolling windows, scoring, signal classification |
| `src/tasks/trade_tracker.py` | WS subscription manager, trade ingestion, DB batch writes |
| `src/tasks/momentum_alerter.py` | Signal transition detection, Telegram alerts, dedup |
| `migrations/versions/002_momentum_tables.py` | DB schema: token_trades, token_momentum, momentum_alerts |

## Modified Files

| File | Change |
|------|--------|
| `src/main.py` | Add trade_tracker + momentum_alerter to lifespan |
| `src/tasks/websocket_scanner.py` | Call `track_token()` on create + migration events |
| `requirements.txt` | Add `pandas==2.1.4` |

## Data Flow (Latency Budget)

```
PumpPortal event → WS receive     (~5ms network)
→ JSON parse                       (~0.1ms)
→ momentum_engine.add_trade()      (~0.1ms, in-memory pandas concat)
→ Buffer for DB batch              (~0.01ms, deque append)
                            TOTAL: <10ms end-to-end to in-memory

DB flush (async, every 10s):
→ executemany batch insert         (~50-200ms per batch)

Momentum recompute (every 1s):
→ Pandas window slicing + metrics  (~1-5ms per token)
→ 200 tokens max                   (~200ms-1s total)
```

## Multi-Timeframe Metrics

### 1-Second Window
- `trades_1s`: Trade count
- `volume_1s`: SOL volume
- `buy_pressure_1s`: (buy_vol - sell_vol) / total_vol  [-1, +1]
- `whale_buys_1s`: Count of ≥0.5 SOL buys

### 15-Second Window
- `momentum_15s`: Volume acceleration (current_15s / previous_15s)
- `whale_concentration`: Top 5 traders volume / total volume
- `velocity`: Trades per second

### 30-Second Window
- `pump_signal_30s`: Composite [0-10] of volume + momentum + whale buying
- `trend_slope`: Linear regression slope on cumulative buy pressure

### 1-Minute Window
- `momentum_1m`: Second-half volume / first-half volume
- `sustainability_score`: Geometric mean consistency across all 4 timeframes

## Composite Score (0-100)

| Component | Weight | Source |
|-----------|--------|--------|
| Pump signal 30s | 30% | Volume spike + momentum + whales |
| Momentum 15s | 20% | Volume acceleration |
| Sustainability | 20% | Cross-timeframe consistency |
| Unique traders | 15% | Organic participation |
| Trend slope | 15% | Price direction |

**Penalties:**
- Whale concentration > 70%: -20 points
- Buy pressure < -50%: -15 points

## Signal Types

| Signal | Trigger | Meaning |
|--------|---------|---------|
| 🔥 PRE_PUMP | 15s momentum > 2x, sustained 30s+, 50+ unique traders | Building momentum, likely to graduate |
| 🚀 PUMP_DETECTED | Post-migration, 5x volume spike, positive buy pressure | Real pump on DEX |
| ⚠️ FADING | Score < 30, sustainability < 20%, volume near zero | Interest dying, exit signal |
| 🐋 WHALE_DUMP | Top 5 traders > 70% volume + selling | Coordinated dump risk |

## Whale Detection

- **Threshold:** ≥0.5 SOL per trade = whale
- **Tracking:** Top 10 traders by volume per token (in 15s window)
- **Risk flag:** Top 5 traders > 70% of total volume = dump risk
- **Used in:** whale_concentration metric + WHALE_DUMP signal + composite score penalty

## Memory Management

- **Per-token buffer:** pandas DataFrame, max 2 minutes of trades (~1-5 KB)
- **Max concurrent tokens:** 200 (LRU eviction)
- **Peak memory estimate:** 200 tokens × 5 KB ≈ 1 MB (negligible)
- **Stale eviction:** No trades for 5 minutes → drop from tracking
- **DB flush:** Every 10s, async batch upsert

## Database Schema

### token_trades (append-only, high volume)
```sql
id BIGSERIAL PRIMARY KEY
token_id INT → tokens(id)
trader_address VARCHAR(255)
amount_sol FLOAT
direction VARCHAR(4)  -- 'buy' or 'sell'
is_whale BOOLEAN
tx_signature VARCHAR(255)
timestamp TIMESTAMP
-- Indexes: (token_id, timestamp), (token_id, is_whale, timestamp), (timestamp)
```

### token_momentum (upsert, one row per tracked token)
```sql
token_id INT PRIMARY KEY → tokens(id)
trades_1s, volume_1s, buy_pressure_1s, whale_buys_1s
momentum_15s, whale_concentration, velocity
pump_signal_30s, trend_slope
momentum_1m, sustainability_score
pump_signal_score INT, unique_traders INT
is_hot BOOLEAN, signal_type VARCHAR(50)
last_updated TIMESTAMP
-- Index: (is_hot, pump_signal_score)
```

### momentum_alerts (audit trail)
```sql
id SERIAL PRIMARY KEY
token_id INT → tokens(id)
signal_type VARCHAR(50)
pump_signal_score INT
details TEXT (JSON)
telegram_message_id VARCHAR(255)
created_at TIMESTAMP
-- Index: (token_id, signal_type, created_at)
```

## Deployment

1. Run migration: `python scripts/run_migrations.py` (002_momentum_tables)
2. Install pandas: `pip install pandas==2.1.4`
3. Restart app — trade_tracker + momentum_alerter start automatically via FastAPI lifespan
4. No config changes needed — uses same PumpPortal WS + DB + Telegram settings

## Monitoring

New stats available via `/status` command:
- `tracked_tokens`: Currently monitored tokens
- `hot_tokens`: Tokens with momentum score ≥ 70
- `trades_received`: Total trades processed
- `trades_flushed_to_db`: Trades persisted
- `alerts_sent`: Momentum alerts posted
- `alerts_deduplicated`: Skipped (too recent)
