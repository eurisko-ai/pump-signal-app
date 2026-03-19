# Deployment Guide

## Quick Start (5 minutes)

### 1. Clone the repository
```bash
cd /home/eurisko/projects
git clone https://github.com/soldevFL/pump-signal-app.git
cd pump-signal-app
```

### 2. Configure
```bash
cp .env.template .env
# Edit .env with your Moralis API key + Telegram bot token
nano .env
```

### 3. Deploy
```bash
bash scripts/setup.sh
```

This will:
- Build Docker images
- Start PostgreSQL + FastAPI
- Run database migrations
- Verify all services are healthy

### 4. Verify
```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/signals
```

---

## Manual Deployment

If `setup.sh` doesn't work:

### Build
```bash
docker compose build
```

### Start
```bash
docker compose up -d
```

### Verify
```bash
docker compose ps
# Both containers should show "Up"

docker compose logs app | tail -20
```

### Test
```bash
curl http://localhost:8000/health
# Should return: {"status": "alive", "timestamp": "..."}
```

---

## Health Check (Cron)

### Install
```bash
chmod +x scripts/health-check.sh
(crontab -l 2>/dev/null; echo "*/5 * * * * /home/eurisko/projects/pump-signal-app/scripts/health-check.sh") | crontab -
```

### Verify
```bash
crontab -l | grep health-check
tail -f /var/log/pump-signal/health.log
```

---

## Common Tasks

### View Logs
```bash
bash scripts/logs.sh app      # App logs
bash scripts/logs.sh db       # Database logs
bash scripts/logs.sh          # Both
bash scripts/logs.sh health   # Health check logs
```

### Restart
```bash
docker compose restart app
# or
docker compose restart
```

### Stop
```bash
docker compose down
```

### Reset Database
```bash
bash scripts/reset.sh
```

---

## Environment Variables

All settings in `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MORALIS_API_KEY` | (required) | Moralis JWT token |
| `TELEGRAM_BOT_TOKEN` | (optional) | Telegram bot token |
| `TELEGRAM_GROUP_ID` | (optional) | Telegram group ID |
| `DB_NAME` | pump_signal | PostgreSQL database |
| `DB_USER` | pump_user | PostgreSQL user |
| `DB_PASSWORD` | (required) | PostgreSQL password |
| `SCAN_INTERVAL_SECONDS` | 60 | Scan frequency |
| `ALERT_THRESHOLD` | 70 | Minimum score to alert |
| `MIN_MARKET_CAP` | 10000 | Minimum market cap ($) |
| `MIN_HOLDERS` | 50 | Minimum holders |

---

## Troubleshooting

### Containers won't start
```bash
docker compose logs app
# Check for Python errors

docker compose logs postgres
# Check for database errors
```

### Database connection fails
```bash
# Verify PostgreSQL is healthy
docker compose ps postgres
# Should show "healthy"

# Check network
docker network ls
docker network inspect pump-signal-app_pump_signal_network
```

### API not responding
```bash
# Test endpoint directly
docker compose exec app curl http://localhost:8000/health

# Check if app is running
docker compose ps app
```

### Migrations didn't run
```bash
# Run manually
docker compose exec app python3 scripts/run_migrations.py

# Restart app
docker compose restart app
```

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for more.

---

## Production Considerations

- [ ] Use strong PostgreSQL password
- [ ] Keep .env file secure (gitignore)
- [ ] Monitor disk space (`/var/lib/docker/volumes`)
- [ ] Rotate logs regularly
- [ ] Set up monitoring/alerting for health checks
- [ ] Use environment-specific .env files
- [ ] Consider load balancing for multiple instances

---

## Support

For issues, check:
1. Logs: `bash scripts/logs.sh`
2. Health: `/var/log/pump-signal/health.log`
3. README.md for feature details
4. TROUBLESHOOTING.md for common issues
