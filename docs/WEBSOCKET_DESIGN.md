# PumpPortal WebSocket Integration Design

## Overview

Replace the Moralis polling scanner (`src/tasks/scanner.py` + `src/services/moralis.py`) with a real-time PumpPortal WebSocket listener. Target: event → alert in **< 2 seconds**.

## Architecture

```
PumpPortal WSS ──→ websocket_scanner.py ──→ score_token() ──→ DB insert ──→ Telegram alert
  (real-time)         (parse + filter)       (reuse scoring)    (asyncpg)    (instant post)
```

### What Changes

| Component | Before (Polling) | After (WebSocket) |
|-----------|-------------------|---------------------|
| Data source | Moralis API (HTTP poll every 60s) | PumpPortal WSS (real-time push) |
| Entry point | `start_scanner()` in scanner.py | `start_websocket_scanner()` in websocket_scanner.py |
| Latency | 60s poll interval + API round trip | < 2s event → alert |
| Alerting | Separate `telegram_alerter.py` polls DB | Inline: score → alert in same coroutine |
| Enrichment | `moralis.enrich_token_data()` | Derived from event data directly |
| Reconnection | N/A (HTTP) | Exponential backoff (1s→30s max) |

### What Stays the Same

- `src/services/scoring.py` — reused as-is
- `src/services/telegram_service.py` — reused as-is
- DB schema (tokens, signals, alerts, scan_log) — no changes
- `src/tasks/housekeeper.py` — unchanged
- `src/bot/telegram.py` — unchanged (pause still works)
- Config via `.env` and `src/config.py` — add 2 new settings

## PumpPortal WebSocket Protocol

### Connection
```
URI: wss://pumpportal.fun/api/data
```

### Subscriptions (send after connect)
```json
{"method": "subscribeNewToken"}
```
```json
{"method": "subscribeMigration"}
```

### Event: New Token Created
```json
{
  "txType": "create",
  "signature": "...",
  "mint": "ABCDpump",
  "traderPublicKey": "...",
  "initialBuy": 30000000,
  "bondingCurveKey": "...",
  "vTokensInBondingCurve": 793100000000000,
  "vSolInBondingCurve": 30000000,
  "marketCapSol": 37.89,
  "name": "TokenName",
  "symbol": "TKN",
  "uri": "https://..."
}
```

### Event: Migration (Graduated to Raydium)
```json
{
  "txType": "migration",
  "signature": "...",
  "mint": "ABCDpump",
  "pool": "...",
  "quoteMint": "So11111111111111111111111111111111111111112",
  "marketCapSol": 85.0
}
```

## Token Data Mapping

| Scoring Field | Source from PumpPortal Event |
|---------------|---------------------------|
| `mint` | `event["mint"]` |
| `name` | `event["name"]` |
| `symbol` | `event["symbol"]` |
| `market_cap` | `event["marketCapSol"] * sol_price_usd` |
| `volume_24h` | Estimated from `initialBuy` or set 0 for new tokens |
| `holders` | 1 at creation; migration events imply 100+ (bonding complete) |
| `age_hours` | 0 for new tokens; derive from creation for migrations |
| `liquidity_ratio` | Derive from bonding curve progress |
| `created_timestamp` | `datetime.utcnow()` for new; lookup for migrations |
| `description` | Fetch from `uri` metadata (optional, async) |

### Bonding Curve → Market Cap
```python
# PumpPortal provides marketCapSol directly
# Convert to USD using cached SOL price
market_cap_usd = event["marketCapSol"] * sol_price_usd

# Bonding curve progress (0-100%)
# Full bonding curve = ~85 SOL raised → migration
bonding_progress = (event.get("vSolInBondingCurve", 0) / 85_000_000_000) * 100
```

## Processing Logic

### Priority: Migration Events (Score Immediately)

Migration = token graduated from Pump.fun bonding curve → now on Raydium DEX. These are the **highest signal** events because:
1. Bonding curve completed (community bought ~85 SOL worth)
2. Liquidity added to DEX (tradeable)
3. Implied holder count ≥ 100+

```
Migration event arrives
  → CA ends with "pump"? 
  → Not in seen_tokens (dedup)?
  → Enrich with bonding curve data
  → Score immediately
  → Score >= 70? POST ALERT
  → Insert token + signal + alert to DB
```

### Secondary: New Token Events (Track, Don't Alert)

New tokens are just created — no liquidity, 1 holder. Log them for tracking but don't alert unless they later migrate.

```
New token event arrives
  → CA ends with "pump"?
  → Insert to tokens table (for tracking)
  → Don't score or alert (too early)
```

## Config Additions

```python
# In Settings class (config.py)
pumpportal_ws_uri: str = "wss://pumpportal.fun/api/data"
ws_reconnect_max_delay: int = 30  # seconds
sol_price_cache_seconds: int = 60  # how often to refresh SOL/USD
```

## File: `src/tasks/websocket_scanner.py`

See implementation below. Key design decisions:

1. **Single async function** `start_websocket_scanner()` — drop-in replacement for `start_scanner()`
2. **Reconnection loop** wraps the WebSocket connection with exponential backoff
3. **SOL price cache** — fetched every 60s from CoinGecko (free, no key)
4. **Inline alerting** — no separate alerter polling; score → alert → Telegram in one flow
5. **Known scam list** — configurable set of creator addresses to blacklist
6. **DB connection pool** — use asyncpg pool instead of per-query connections

## Changes to `main.py`

```python
# Replace:
from src.tasks.scanner import start_scanner
# With:
from src.tasks.websocket_scanner import start_websocket_scanner

# In lifespan:
scanner_task = asyncio.create_task(start_websocket_scanner())
```

## Changes to `config.py`

Add:
```python
pumpportal_ws_uri: str = "wss://pumpportal.fun/api/data"
ws_reconnect_max_delay: int = 30
sol_price_cache_seconds: int = 60
```

## Removed Dependencies

- `src/services/moralis.py` — no longer needed (keep file, remove import)
- `src/tasks/telegram_alerter.py` — alerting is now inline in websocket_scanner

## New Dependencies

```
websockets>=12.0
```

## Reconnection Strategy

```
Disconnect detected
  → Log event
  → Wait: min(2^attempt, 30) seconds
  → Reconnect
  → Re-subscribe to both channels
  → Reset backoff on successful message received
```

## Monitoring

The scanner logs:
- Every connection/disconnection event
- Every migration event processed (with score)
- Every alert posted
- SOL price updates
- Reconnection attempts with backoff timing
- Periodic stats (events/min, alerts/hour)

## Risk Mitigations

1. **PumpPortal goes down**: Log warning, reconnect loop keeps trying. No data loss (just gap).
2. **SOL price fetch fails**: Use last known price. Alert if stale > 5 min.
3. **DB connection fails**: Retry with backoff. Buffer up to 10 events in memory.
4. **Telegram rate limit**: Queue alerts, respect 1msg/sec limit.
5. **Memory leak (seen_tokens)**: Housekeeper already cleans old data. Prune seen_tokens dict after 6h.
