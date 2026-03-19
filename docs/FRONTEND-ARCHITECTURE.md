# Pump Signal Dashboard — Frontend Architecture

> **Version:** 1.0  
> **Date:** 2026-03-19  
> **Backend:** FastAPI on `localhost:8000` (existing `pump-signal-app`)  
> **Frontend:** Next.js 14 + TypeScript + Tailwind + TradingView Lightweight Charts

---

## 1. New Backend API Endpoints

These endpoints are added to the existing FastAPI app (`src/routers/dashboard_api.py`).

### 1.1 REST Endpoints

```
GET  /api/dashboard/tokens/active
GET  /api/dashboard/tokens/{token_id}/metrics
GET  /api/dashboard/signals/active
GET  /api/dashboard/signals/history
GET  /api/dashboard/analytics/summary
WS   /ws/stream
```

#### `GET /api/dashboard/tokens/active`

Returns all tokens currently tracked by the momentum engine.

```jsonc
// Response
[
  {
    "id": 42,
    "mint": "6xKn...pump",
    "name": "DEGEN",
    "symbol": "DEGEN",
    "created_at": "2026-03-19T22:15:00Z",
    "status": "detecting",        // "detecting" | "migrated"
    "pump_signal_score": 72,
    "is_hot": true,
    "signal_type": "PRE_PUMP"     // null | "PRE_PUMP" | "PUMP_DETECTED" | "FADING" | "WHALE_DUMP"
  }
]
```

Query params: `?limit=50&offset=0&hot_only=false&sort=score`

**Implementation:** Query `token_momentum` table joined to `tokens`. Filter `is_hot` if `hot_only=true`.

---

#### `GET /api/dashboard/tokens/{token_id}/metrics`

Full momentum snapshot for a single token.

```jsonc
// Response
{
  "token_id": 42,
  "mint": "6xKn...pump",
  "name": "DEGEN",
  "symbol": "DEGEN",

  // 1-second window
  "trades_1s": 5,
  "volume_1s": 1.23,
  "buy_pressure_1s": 0.65,
  "whale_buys_1s": 1,

  // 15-second window
  "momentum_15s": 2.4,
  "whale_concentration": 0.35,
  "velocity": 1.2,

  // 30-second window
  "pump_signal_30s": 7.2,
  "trend_slope": 0.18,

  // 1-minute window
  "momentum_1m": 1.8,
  "sustainability_score": 0.72,

  // Composite
  "pump_signal_score": 78,
  "unique_traders": 34,
  "is_hot": true,
  "signal_type": "PRE_PUMP",
  "last_updated": "2026-03-19T22:16:05Z"
}
```

**Implementation:** Read directly from `momentum_engine.get_buffer(token_id).metrics` (in-memory, sub-ms latency). Fall back to `token_momentum` DB table if buffer evicted.

---

#### `GET /api/dashboard/signals/active`

Signals grouped by urgency category — what the dashboard "signal board" renders.

```jsonc
// Response
{
  "BUY": [
    {
      "token_id": 42,
      "mint": "6xKn...pump",
      "name": "DEGEN",
      "symbol": "DEGEN",
      "signal_type": "PRE_PUMP",
      "pump_signal_score": 78,
      "momentum_15s": 2.4,
      "timestamp": "2026-03-19T22:16:05Z"
    }
  ],
  "PUMP": [
    // tokens with signal_type = "PUMP_DETECTED"
  ],
  "SELL": [
    // tokens with signal_type = "FADING"
  ],
  "DANGER": [
    // tokens with signal_type = "WHALE_DUMP"
  ],
  "updated_at": "2026-03-19T22:16:05Z"
}
```

**Implementation:** Iterate `momentum_engine._buffers`, classify by `signal_type`.

---

#### `GET /api/dashboard/signals/history`

Historical signals for win-rate analysis.

```jsonc
// Query params: ?limit=100&offset=0&signal_type=BUY&from=2026-03-19T00:00:00Z&to=...

// Response
{
  "signals": [
    {
      "id": 1001,
      "token_id": 42,
      "token_name": "DEGEN",
      "token_symbol": "DEGEN",
      "mint": "6xKn...pump",
      "signal_type": "PRE_PUMP",
      "pump_signal_score": 78,
      "momentum_15s": 2.4,
      "whale_concentration": 0.35,
      "timestamp": "2026-03-19T22:16:05Z",
      // P&L tracking (Phase 3)
      "entry_price": null,
      "exit_price": null,
      "pnl_percent": null,
      "outcome": null     // "WIN" | "LOSS" | "PENDING" | null
    }
  ],
  "total": 340,
  "page": 1,
  "per_page": 100
}
```

**Implementation:** New `signal_events` table that logs each signal transition from `momentum_engine.drain_alerts()`. The alerter already detects transitions; we just persist them.

---

#### `GET /api/dashboard/analytics/summary`

Aggregate stats for the analytics page.

```jsonc
// Response
{
  "total_signals_24h": 142,
  "buy_signals_24h": 38,
  "sell_signals_24h": 29,
  "whale_dumps_24h": 15,

  "win_rate_7d": 0.62,         // signals where price went up after BUY
  "avg_gain_7d": 34.5,         // percent
  "avg_loss_7d": -18.2,        // percent
  "total_pnl_7d": 12.4,        // SOL (simulated or real)

  "tokens_tracked_now": 87,
  "hot_tokens_now": 4,

  "hourly_signal_counts": [
    { "hour": "2026-03-19T22:00:00Z", "buy": 5, "sell": 3, "pump": 2, "danger": 1 }
    // last 24 hours
  ]
}
```

---

### 1.2 WebSocket Endpoint

#### `WS /ws/stream`

Single multiplexed WebSocket for all real-time data.

**Client → Server (subscribe)**
```jsonc
{ "action": "subscribe", "channels": ["tokens", "signals", "metrics"] }
{ "action": "subscribe_token", "token_id": 42 }  // subscribe to single token detail
{ "action": "unsubscribe_token", "token_id": 42 }
```

**Server → Client (push)**

```jsonc
// Token list update (every 2s, only changed tokens)
{
  "type": "token_update",
  "data": {
    "id": 42,
    "pump_signal_score": 78,
    "is_hot": true,
    "signal_type": "PRE_PUMP",
    "volume_1s": 1.23,
    "momentum_15s": 2.4,
    "velocity": 1.2,
    "last_updated": "2026-03-19T22:16:05Z"
  }
}

// New signal fired (immediate)
{
  "type": "signal",
  "data": {
    "token_id": 42,
    "mint": "6xKn...pump",
    "name": "DEGEN",
    "symbol": "DEGEN",
    "signal_type": "PRE_PUMP",
    "pump_signal_score": 78,
    "metrics": { /* full metrics snapshot */ },
    "timestamp": "2026-03-19T22:16:05Z"
  }
}

// Full metrics for subscribed token (every 1s)
{
  "type": "token_metrics",
  "data": {
    "token_id": 42,
    /* all fields from GET /tokens/{id}/metrics */
  }
}

// Heartbeat (every 30s)
{
  "type": "heartbeat",
  "data": {
    "tracked_tokens": 87,
    "hot_tokens": 4,
    "uptime_seconds": 3600
  }
}
```

**Implementation:** FastAPI WebSocket endpoint. A background task reads from `momentum_engine` every 1s, diffs against previous state, pushes only changed tokens. Signal alerts are pushed immediately via `drain_alerts()`.

---

## 2. New DB Tables

```sql
-- Signal event log (for history/analytics)
CREATE TABLE signal_events (
    id SERIAL PRIMARY KEY,
    token_id INTEGER NOT NULL REFERENCES tokens(id),
    signal_type VARCHAR(20) NOT NULL,  -- PRE_PUMP, PUMP_DETECTED, FADING, WHALE_DUMP
    pump_signal_score INTEGER NOT NULL,
    metrics JSONB NOT NULL,            -- full metrics snapshot at signal time
    entry_price FLOAT,                 -- filled by trade tracker (Phase 3)
    exit_price FLOAT,
    pnl_percent FLOAT,
    outcome VARCHAR(10),               -- WIN, LOSS, PENDING
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_signal_events_type ON signal_events(signal_type);
CREATE INDEX idx_signal_events_created ON signal_events(created_at DESC);
CREATE INDEX idx_signal_events_token ON signal_events(token_id);
```

---

## 3. Frontend Project Structure

```
pump-signal-dashboard/
├── next.config.ts
├── tailwind.config.ts
├── tsconfig.json
├── package.json
├── .env.local                    # NEXT_PUBLIC_API_URL=http://localhost:8000
│
├── public/
│   ├── sounds/
│   │   ├── buy-alert.mp3
│   │   ├── sell-alert.mp3
│   │   └── danger-alert.mp3
│   └── favicon.ico
│
├── src/
│   ├── app/                      # Next.js App Router
│   │   ├── layout.tsx            # Root layout (sidebar + header)
│   │   ├── page.tsx              # Redirect to /dashboard
│   │   ├── dashboard/
│   │   │   └── page.tsx          # Live token tracker + signal board
│   │   ├── signals/
│   │   │   └── page.tsx          # Signal history + win rate
│   │   ├── tokens/
│   │   │   └── [id]/
│   │   │       └── page.tsx      # Token detail + momentum chart
│   │   └── analytics/
│   │       └── page.tsx          # P&L + signal performance
│   │
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx       # Nav: Dashboard, Signals, Analytics
│   │   │   ├── Header.tsx        # Connection status, tracked count
│   │   │   └── AlertToast.tsx    # Floating signal notifications
│   │   │
│   │   ├── tokens/
│   │   │   ├── TokenCard.tsx     # Name, symbol, momentum meter, signal badge
│   │   │   ├── TokenGrid.tsx     # Grid of TokenCards (sortable)
│   │   │   └── TokenTable.tsx    # Table view alternative
│   │   │
│   │   ├── charts/
│   │   │   ├── MomentumChart.tsx # TradingView Lightweight Chart
│   │   │   ├── MomentumMeter.tsx # Circular gauge (0-100 score)
│   │   │   └── VolumeBar.tsx     # Mini volume bar (1s window)
│   │   │
│   │   ├── signals/
│   │   │   ├── SignalBoard.tsx   # 4-column: BUY | PUMP | SELL | DANGER
│   │   │   ├── SignalAlert.tsx   # Individual signal card with urgency
│   │   │   ├── SignalHistory.tsx # Paginated table with filters
│   │   │   └── SignalBadge.tsx   # Inline badge (PRE_PUMP, etc.)
│   │   │
│   │   └── analytics/
│   │       ├── StatsCard.tsx     # Win rate, avg gain, total P&L
│   │       ├── PnlChart.tsx      # Cumulative P&L over time
│   │       └── HourlyHeatmap.tsx # Signal frequency by hour
│   │
│   ├── hooks/
│   │   ├── useWebSocket.ts      # WebSocket connection + reconnect
│   │   ├── useTokens.ts         # Token list state (REST + WS merge)
│   │   ├── useSignals.ts        # Active signals state
│   │   ├── useTokenMetrics.ts   # Single token metrics subscription
│   │   └── useSoundAlerts.ts    # Audio alerts on signal events
│   │
│   ├── lib/
│   │   ├── api.ts               # REST API client (fetch wrappers)
│   │   ├── ws.ts                # WebSocket manager (singleton)
│   │   ├── types.ts             # TypeScript interfaces
│   │   └── utils.ts             # Formatters, color scales, etc.
│   │
│   └── stores/
│       └── dashboardStore.ts    # Zustand store (global state)
│
└── README.md
```

---

## 4. TypeScript Types (`src/lib/types.ts`)

```typescript
// ---- Token ----
export interface Token {
  id: number;
  mint: string;
  name: string;
  symbol: string;
  created_at: string;
  status: 'detecting' | 'migrated';
  pump_signal_score: number;
  is_hot: boolean;
  signal_type: SignalType | null;
}

export type SignalType = 'PRE_PUMP' | 'PUMP_DETECTED' | 'FADING' | 'WHALE_DUMP';

// ---- Metrics ----
export interface TokenMetrics {
  token_id: number;
  mint: string;
  name: string;
  symbol: string;

  // 1s
  trades_1s: number;
  volume_1s: number;
  buy_pressure_1s: number;
  whale_buys_1s: number;

  // 15s
  momentum_15s: number;
  whale_concentration: number;
  velocity: number;

  // 30s
  pump_signal_30s: number;
  trend_slope: number;

  // 1m
  momentum_1m: number;
  sustainability_score: number;

  // Composite
  pump_signal_score: number;
  unique_traders: number;
  is_hot: boolean;
  signal_type: SignalType | null;
  last_updated: string;
}

// ---- Signals ----
export interface SignalEvent {
  id: number;
  token_id: number;
  token_name: string;
  token_symbol: string;
  mint: string;
  signal_type: SignalType;
  pump_signal_score: number;
  momentum_15s: number;
  whale_concentration: number;
  timestamp: string;
  entry_price: number | null;
  exit_price: number | null;
  pnl_percent: number | null;
  outcome: 'WIN' | 'LOSS' | 'PENDING' | null;
}

export interface ActiveSignals {
  BUY: SignalEvent[];
  PUMP: SignalEvent[];
  SELL: SignalEvent[];
  DANGER: SignalEvent[];
  updated_at: string;
}

// ---- Analytics ----
export interface AnalyticsSummary {
  total_signals_24h: number;
  buy_signals_24h: number;
  sell_signals_24h: number;
  whale_dumps_24h: number;
  win_rate_7d: number;
  avg_gain_7d: number;
  avg_loss_7d: number;
  total_pnl_7d: number;
  tokens_tracked_now: number;
  hot_tokens_now: number;
  hourly_signal_counts: HourlyCount[];
}

export interface HourlyCount {
  hour: string;
  buy: number;
  sell: number;
  pump: number;
  danger: number;
}

// ---- WebSocket Messages ----
export type WSMessageType = 'token_update' | 'signal' | 'token_metrics' | 'heartbeat';

export interface WSMessage<T = unknown> {
  type: WSMessageType;
  data: T;
}

export interface WSSubscribe {
  action: 'subscribe' | 'subscribe_token' | 'unsubscribe_token';
  channels?: string[];
  token_id?: number;
}
```

---

## 5. Key Component Designs

### 5.1 `TokenCard`

```
┌─────────────────────────────────────────┐
│  🔥 DEGEN ($DEGEN)        [PRE_PUMP]   │
│                                          │
│  ████████████░░░░░░  78/100             │
│  Momentum Score                          │
│                                          │
│  15s: 2.4x   Traders: 34   Vol: 1.2 SOL│
│  Whale: 35%   Trend: ↗                  │
│                                          │
│  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░         │
│  [mini momentum sparkline]              │
└─────────────────────────────────────────┘
```

- Score ≥ 70: green glow border + 🔥
- Score 40-69: yellow border
- Score < 40: gray border
- Signal badge color: PRE_PUMP=green, PUMP_DETECTED=cyan, FADING=orange, WHALE_DUMP=red
- Flash animation on score change (green flash if up, red if down)

### 5.2 `SignalBoard` (Dashboard main)

```
┌──────────────────────────────────────────────────────────────┐
│  🟢 BUY (3)      │  🚀 PUMP (1)    │  🟠 SELL (2)    │  🔴 DANGER (1)  │
│                   │                  │                  │                  │
│  DEGEN    78      │  MOON   85       │  FROG    28      │  RUG     15      │
│  PEPE     72      │                  │  APE     22      │                  │
│  SOL2     70      │                  │                  │                  │
│                   │                  │                  │                  │
└──────────────────────────────────────────────────────────────┘
```

4 columns, each scrollable. New entries animate in from top. Removed entries fade out.

### 5.3 `MomentumChart` (Token detail page)

TradingView Lightweight Charts with 4 overlaid area series:

```
┌──────────────────────────────────────────────────────────────┐
│  DEGEN Momentum                                    [1s|15s|30s|1m]  │
│                                                                      │
│  ████                                                                │
│  █  ██                                                               │
│  █   ██   ████                                                       │
│  █    ██ █    █    ← 15s momentum (primary, blue)                    │
│  █     ██      █                                                     │
│  █              █                                                    │
│  ───────────────────  1.0 baseline                                   │
│                                                                      │
│  Volume bars at bottom (green=buy, red=sell)                         │
└──────────────────────────────────────────────────────────────┘
```

- Real-time: new data point every 1s via WebSocket
- Max visible range: 2 minutes (rolling)
- Horizontal line at momentum = 1.0 (neutral) and 2.0 (PRE_PUMP threshold)
- Whale events marked as triangle markers

---

## 6. Data Flow

```
                    ┌──────────────────────┐
                    │   Pump.fun WebSocket  │
                    │  (trade stream)       │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   trade_tracker.py    │
                    │  (ingests trades)     │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   MomentumEngine     │
                    │  (1s tick loop)       │
                    │  - compute metrics    │
                    │  - detect signals     │
                    │  - queue alerts       │
                    └──────┬────┬──────────┘
                           │    │
              ┌────────────┘    └────────────┐
              ▼                              ▼
    ┌──────────────────┐          ┌──────────────────┐
    │  momentum_alerter │          │  ws_broadcaster   │
    │  (Telegram alerts)│          │  (new component)  │
    └──────────────────┘          └────────┬─────────┘
                                           │
                              ┌────────────┼────────────┐
                              ▼            ▼            ▼
                        /dashboard     /tokens/42   /signals
                        (token grid)   (detail)     (history)
                              │            │            │
                              └────────────┼────────────┘
                                           │
                                    ┌──────▼──────┐
                                    │   Zustand    │
                                    │   Store      │
                                    │              │
                                    │ tokens[]     │
                                    │ signals{}    │
                                    │ metrics{}    │
                                    │ connected    │
                                    └─────────────┘
```

### Flow by scenario:

**New token appears:**
1. `trade_tracker` registers with `MomentumEngine`
2. Engine starts computing metrics (1s tick)
3. `ws_broadcaster` pushes `token_update` to all connected dashboards
4. Frontend Zustand store adds token → `TokenGrid` re-renders

**Momentum crosses threshold → BUY signal:**
1. Engine `_classify_signal()` returns `PRE_PUMP`
2. Engine queues alert via `_pending_alerts`
3. `momentum_alerter` drains → sends Telegram
4. `ws_broadcaster` pushes `{ type: "signal", data: {...} }`
5. Dashboard: `SignalBoard` BUY column gets new entry (animated)
6. `AlertToast` shows floating notification
7. `useSoundAlerts` plays `buy-alert.mp3`
8. `signal_events` table gets new row

**User opens token detail:**
1. Frontend sends `{ action: "subscribe_token", token_id: 42 }`
2. WS server starts pushing `token_metrics` every 1s for that token
3. `MomentumChart` appends new data point, chart scrolls
4. User navigates away → sends `unsubscribe_token`

---

## 7. WebSocket Manager (`src/lib/ws.ts`)

```typescript
class WSManager {
  private ws: WebSocket | null = null;
  private url: string;
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30000;
  private listeners: Map<string, Set<(data: any) => void>> = new Map();
  private subscriptions: Set<string> = new Set();

  constructor(url: string) {
    this.url = url;
  }

  connect() {
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      this.reconnectDelay = 1000;
      // Re-subscribe on reconnect
      if (this.subscriptions.size > 0) {
        this.ws?.send(JSON.stringify({
          action: 'subscribe',
          channels: Array.from(this.subscriptions)
        }));
      }
    };

    this.ws.onmessage = (event) => {
      const msg: WSMessage = JSON.parse(event.data);
      const handlers = this.listeners.get(msg.type);
      handlers?.forEach(handler => handler(msg.data));
    };

    this.ws.onclose = () => {
      setTimeout(() => this.connect(), this.reconnectDelay);
      this.reconnectDelay = Math.min(
        this.reconnectDelay * 2,
        this.maxReconnectDelay
      );
    };
  }

  subscribe(channel: string) {
    this.subscriptions.add(channel);
    this.ws?.send(JSON.stringify({
      action: 'subscribe',
      channels: [channel]
    }));
  }

  on(type: string, handler: (data: any) => void) {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, new Set());
    }
    this.listeners.get(type)!.add(handler);
    return () => this.listeners.get(type)?.delete(handler);
  }

  subscribeToken(tokenId: number) {
    this.ws?.send(JSON.stringify({
      action: 'subscribe_token',
      token_id: tokenId
    }));
  }

  unsubscribeToken(tokenId: number) {
    this.ws?.send(JSON.stringify({
      action: 'unsubscribe_token',
      token_id: tokenId
    }));
  }
}

// Singleton
export const wsManager = new WSManager(
  process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws/stream'
);
```

---

## 8. Zustand Store (`src/stores/dashboardStore.ts`)

```typescript
interface DashboardState {
  // Connection
  connected: boolean;
  setConnected: (v: boolean) => void;

  // Tokens
  tokens: Map<number, Token>;
  updateToken: (data: Partial<Token> & { id: number }) => void;
  setTokens: (tokens: Token[]) => void;

  // Metrics (for subscribed token detail)
  tokenMetrics: Map<number, TokenMetrics>;
  updateTokenMetrics: (data: TokenMetrics) => void;

  // Signals
  activeSignals: ActiveSignals;
  setActiveSignals: (s: ActiveSignals) => void;
  addSignal: (signal: SignalEvent) => void;

  // Alerts (toast queue)
  alerts: SignalEvent[];
  pushAlert: (alert: SignalEvent) => void;
  dismissAlert: (id: number) => void;

  // Sort/filter
  sortBy: 'score' | 'time' | 'volume';
  setSortBy: (s: 'score' | 'time' | 'volume') => void;
  filterHotOnly: boolean;
  setFilterHotOnly: (v: boolean) => void;
}
```

---

## 9. Page Layouts

### 9.1 `/dashboard` — Live Tracker

```
┌──────────┬───────────────────────────────────────────────────┐
│          │  Header: ● Connected | 87 tokens | 4 hot  [🔔 3] │
│          ├───────────────────────────────────────────────────┤
│          │                                                    │
│  Sidebar │  ┌─── Signal Board ─────────────────────────────┐ │
│          │  │ 🟢 BUY (3)  │ 🚀 PUMP (1) │ 🟠 SELL │ 🔴 DNG │ │
│  📊 Dash │  │  ...        │  ...         │  ...    │  ...   │ │
│  📡 Sigs │  └──────────────────────────────────────────────┘ │
│  📈 Anal │                                                    │
│          │  ┌─── Token Grid ───────────────────────────────┐ │
│          │  │ [Sort: Score ▼] [🔥 Hot only]  [Grid|Table]  │ │
│          │  │                                               │ │
│          │  │  ┌─────────┐ ┌─────────┐ ┌─────────┐        │ │
│          │  │  │ DEGEN   │ │ PEPE    │ │ MOON    │        │ │
│          │  │  │ 78/100  │ │ 72/100  │ │ 85/100  │        │ │
│          │  │  │ PRE_PUMP│ │ PRE_PUMP│ │ PUMP    │        │ │
│          │  │  └─────────┘ └─────────┘ └─────────┘        │ │
│          │  │                                               │ │
│          │  └───────────────────────────────────────────────┘ │
└──────────┴───────────────────────────────────────────────────┘
```

### 9.2 `/tokens/{id}` — Token Detail

```
┌──────────┬───────────────────────────────────────────────────┐
│          │  ← Back   DEGEN ($DEGEN)  [PRE_PUMP]  78/100     │
│          ├───────────────────────────────────────────────────┤
│          │                                                    │
│  Sidebar │  ┌─── Momentum Chart (TradingView) ─────────────┐│
│          │  │                                                ││
│          │  │  [1s momentum line, 15s line, 30s line]        ││
│          │  │  [Volume bars at bottom]                       ││
│          │  │  Rolling 2-minute window, updates every 1s     ││
│          │  │                                                ││
│          │  └────────────────────────────────────────────────┘│
│          │                                                    │
│          │  ┌─── Metrics Grid ──────────────────────────────┐│
│          │  │ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐  ││
│          │  │ │ 1s Vol │ │15s Mom │ │Whale % │ │Traders │  ││
│          │  │ │ 1.23   │ │ 2.4x   │ │ 35%    │ │  34    │  ││
│          │  │ └────────┘ └────────┘ └────────┘ └────────┘  ││
│          │  │ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐  ││
│          │  │ │30s Pump│ │ Trend  │ │1m Sust │ │Velocity│  ││
│          │  │ │ 7.2/10 │ │  ↗ +   │ │ 0.72   │ │1.2 t/s │  ││
│          │  │ └────────┘ └────────┘ └────────┘ └────────┘  ││
│          │  └────────────────────────────────────────────────┘│
│          │                                                    │
│          │  ┌─── Signal Timeline ──────────────────────────┐ │
│          │  │ 22:15:05  PRE_PUMP detected (score 78)       │ │
│          │  │ 22:16:30  Score peaked at 85                  │ │
│          │  │ 22:18:00  FADING (score dropped to 28)        │ │
│          │  └──────────────────────────────────────────────┘ │
└──────────┴───────────────────────────────────────────────────┘
```

### 9.3 `/signals` — Signal History

```
┌──────────┬───────────────────────────────────────────────────┐
│          │  Signal History                [Filter ▼] [Export] │
│          ├───────────────────────────────────────────────────┤
│          │                                                    │
│  Sidebar │  ┌─── Stats Row ────────────────────────────────┐ │
│          │  │ Win Rate: 62% │ Avg Gain: +34% │ P&L: +12 SOL│ │
│          │  └──────────────────────────────────────────────┘ │
│          │                                                    │
│          │  ┌─── Signal Table ─────────────────────────────┐ │
│          │  │ Time     │ Token │ Signal   │ Score │ P&L    │ │
│          │  │ 22:16:05 │ DEGEN │ PRE_PUMP │  78   │ +45%   │ │
│          │  │ 22:10:30 │ MOON  │ PUMP     │  85   │ +12%   │ │
│          │  │ 22:08:15 │ FROG  │ FADING   │  28   │ -8%    │ │
│          │  │ 22:05:00 │ RUG   │ WHALE    │  15   │ -52%   │ │
│          │  └──────────────────────────────────────────────┘ │
│          │                                                    │
│          │  [< 1  2  3  4  5 >]                              │
└──────────┴───────────────────────────────────────────────────┘
```

### 9.4 `/analytics` — Performance

```
┌──────────┬───────────────────────────────────────────────────┐
│          │  Analytics                     [7d | 30d | All]    │
│          ├───────────────────────────────────────────────────┤
│          │                                                    │
│  Sidebar │  ┌─ Stats Cards ───────────────────────────────┐  │
│          │  │ Win Rate │ Avg Gain │ Avg Loss │ Total P&L  │  │
│          │  │   62%    │  +34.5%  │  -18.2%  │ +12.4 SOL  │  │
│          │  └──────────────────────────────────────────────┘  │
│          │                                                    │
│          │  ┌─ Cumulative P&L Chart ──────────────────────┐  │
│          │  │  (line chart, SOL over time)                 │  │
│          │  └─────────────────────────────────────────────┘  │
│          │                                                    │
│          │  ┌─ Hourly Signal Heatmap ─────────────────────┐  │
│          │  │  (24h × signal_type heatmap)                │  │
│          │  └─────────────────────────────────────────────┘  │
│          │                                                    │
│          │  ┌─ Signal Type Breakdown (pie) ───────────────┐  │
│          │  │  BUY: 38  PUMP: 12  SELL: 29  DANGER: 15   │  │
│          │  └─────────────────────────────────────────────┘  │
└──────────┴───────────────────────────────────────────────────┘
```

---

## 10. Styling & Theme

**Dark theme** (crypto dashboard standard):

```
Background:     #0a0a0f (near-black)
Surface:        #111118 (cards)
Surface hover:  #1a1a24
Border:         #2a2a3a
Text primary:   #e4e4ef
Text secondary: #8888a0

Green (BUY):    #00ff88
Cyan (PUMP):    #00ccff
Orange (SELL):  #ff8800
Red (DANGER):   #ff2244
Yellow (HOT):   #ffcc00

Score gradient: 0 → #ff2244, 50 → #ffcc00, 100 → #00ff88
```

**Animations:**
- Token score change: 200ms flash (green=up, red=down) on the score number
- New signal: slide-in from right in SignalBoard column
- Signal removed: fade-out 300ms
- Hot token: subtle pulse glow on card border (CSS `@keyframes pulse`)
- Toast: slide-in from top-right, auto-dismiss after 5s

---

## 11. Sound Alerts

```typescript
// src/hooks/useSoundAlerts.ts
const SOUNDS = {
  PRE_PUMP:      '/sounds/buy-alert.mp3',    // Short positive chime
  PUMP_DETECTED: '/sounds/buy-alert.mp3',    // Same chime, maybe louder
  FADING:        '/sounds/sell-alert.mp3',   // Descending tone
  WHALE_DUMP:    '/sounds/danger-alert.mp3', // Warning siren (short)
};

// Play on new signal event from WebSocket
// Respect user preference (localStorage toggle)
// Rate-limit: max 1 sound per 3 seconds
```

---

## 12. Dependencies

```json
{
  "dependencies": {
    "next": "^14.2.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "lightweight-charts": "^4.1.0",
    "zustand": "^4.5.0",
    "tailwindcss": "^3.4.0",
    "@tanstack/react-table": "^8.15.0",
    "date-fns": "^3.6.0",
    "lucide-react": "^0.370.0"
  },
  "devDependencies": {
    "typescript": "^5.4.0",
    "@types/react": "^18.3.0",
    "@types/node": "^20.12.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0"
  }
}
```

---

## 13. Deployment

- Frontend runs as a separate Docker container alongside the existing FastAPI app
- `next.config.ts` rewrites `/api/*` → `http://pump-signal-app:8000/api/*` (container network)
- WebSocket proxied via Next.js middleware or direct connection to backend
- Single `docker-compose.yml` addition:

```yaml
  dashboard:
    build:
      context: ./pump-signal-dashboard
      dockerfile: Dockerfile
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://localhost:8000
      - NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws/stream
    depends_on:
      - pump-signal-app
```

---

## 14. Implementation Priority

| Phase | What | Effort |
|-------|------|--------|
| **P0** | Backend: `/ws/stream` endpoint + `ws_broadcaster` task | 4h |
| **P0** | Backend: 4 dashboard REST endpoints | 2h |
| **P0** | Backend: `signal_events` table + migration | 1h |
| **P1** | Frontend: project scaffold + layout + routing | 2h |
| **P1** | Frontend: `useWebSocket` hook + Zustand store | 2h |
| **P1** | Frontend: `/dashboard` page (SignalBoard + TokenGrid) | 4h |
| **P2** | Frontend: `/tokens/{id}` page + MomentumChart | 3h |
| **P2** | Frontend: `/signals` page + history table | 2h |
| **P3** | Frontend: `/analytics` page + P&L charts | 3h |
| **P3** | Sound alerts + toast notifications | 1h |
| **P3** | Dark theme polish + animations | 2h |

**Total estimate: ~26 hours**

P0+P1 = working dashboard with live data in ~12 hours.
