# 🐉 Pump Signal App

**Real-time Pump.fun token signal scanner with Telegram integration**

Monitors graduated Pump.fun tokens (CA ends with `pump`), scores them on 8 factors, and alerts on high-potential opportunities (70+ score).

## Quick Start

### 1. Clone & Setup

```bash
cd /home/eurisko/projects/pump-signal-app
cp .env.template .env
# Edit .env with your Moralis API key + Telegram bot token
```

### 2. Deploy with Docker

```bash
docker-compose up -d
```

This will:
- Start PostgreSQL with pgvector
- Run database migrations automatically
- Start FastAPI scanner on `http://localhost:8000`

### 3. Verify

```bash
# Check health
curl http://localhost:8000/health

# View recent signals
curl http://localhost:8000/api/signals?limit=10

# View logs
docker-compose logs -f app
```

## Configuration

All settings in `.env`:

```env
# Moralis (required)
MORALIS_API_KEY=your_jwt_key_here

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_GROUP_ID=your_group_id

# Scanner
SCAN_INTERVAL_SECONDS=60
ALERT_THRESHOLD=70
MIN_MARKET_CAP=10000
MIN_HOLDERS=50
DEDUP_WINDOW_HOURS=6
```

Changes take effect on next container restart:
```bash
docker-compose restart app
```

## API Endpoints

- `GET /health` — Liveness check
- `GET /ready` — Readiness + DB freshness
- `GET /api/signals` — Recent signals
- `GET /api/signals/top` — Top signals this hour
- `GET /api/tokens` — All tokens
- `GET /api/tokens/{ca}` — Single token

## Scoring Algorithm (0-100)

- **Status** (+35) — Graduated Pump.fun
- **Market Cap** (+15) — $50k-$2M sweet spot
- **Holders** (+15) — Decentralization (200+ ideal)
- **Volume 24h** (+12) — Trading activity
- **Liquidity** (+12) — Volume/market cap ratio
- **Age Penalty** (-5 to -15) — Too new or too old
- **Whale Risk** (-10) — Top 10 holders >80%
- **Narrative** (+8) — AI/Meme/Politics/etc

## Database

PostgreSQL with pgvector (6 tables):
- `tokens` — CA, name, market cap, volume, holders
- `signals` — Scores + breakdown
- `alerts` — Telegram delivery status
- `scan_log` — Scan metrics
- `settings` — Configurable parameters
- `token_price_history` — Price tracking

## Health Check

Cron job every 5 minutes:
```bash
*/5 * * * * /home/eurisko/projects/pump-signal-app/scripts/health-check.sh
```

Checks API + DB freshness, auto-restarts if unhealthy.

## Telegram Commands (WIP)

- `/status` — Scanner status
- `/alerts` — Last 10 alerts
- `/top` — Top 5 this hour
- `/settings` — View/change settings
- `/logs` — Error logs
- `/pause` / `/resume` — Control scanning

## Troubleshooting

**Container won't start:**
```bash
docker-compose logs app
```

**DB connection errors:**
```bash
docker-compose logs postgres
```

**No tokens found:**
- Check Moralis API key in `.env`
- Verify network connectivity
- Check logs: `docker-compose logs app | grep Tier`

## Development

Run locally (requires PostgreSQL):
```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn src.main:app --reload
```

## License

TBD
