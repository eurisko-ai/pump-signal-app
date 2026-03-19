# Troubleshooting Guide

## Container Issues

### Containers won't start

**Check logs:**
```bash
docker compose logs app
docker compose logs postgres
```

**Common causes:**
- Port already in use → Kill the process or change port in docker-compose.yml
- Not enough disk space → `df -h`
- Docker daemon not running → `systemctl start docker`

**Solution:**
```bash
# Kill any lingering processes
lsof -i :8000 | awk 'NR>1 {print $2}' | xargs kill -9

# Restart from scratch
docker compose down -v
docker compose up -d
```

---

## Database Issues

### "relation does not exist"

**Cause:** Migrations didn't run

**Fix:**
```bash
docker compose exec app python3 scripts/run_migrations.py
docker compose restart app
```

### Connection refused

**Check PostgreSQL:**
```bash
docker compose exec postgres psql -U pump_user -d pump_signal -c "SELECT 1"
```

**If failed:**
```bash
# Restart DB
docker compose restart postgres
sleep 5
docker compose restart app
```

### Too many connections

**Solution:**
```bash
# Restart containers
docker compose restart
```

---

## API Issues

### /health returns error

```bash
# Check if app is running
docker compose ps app

# View logs
docker compose logs app | tail -50

# Restart
docker compose restart app
```

### /api/signals returns empty

This is normal if no tokens have been scanned yet. Check scanner logs:

```bash
docker compose logs app | grep -i "found\|alert\|score"
```

### Curl/connection refused

**Check if port is exposed:**
```bash
docker compose ps
# Should show "0.0.0.0:8000->8000/tcp"

# Test from within container
docker compose exec app curl http://localhost:8000/health
```

---

## Scanning Issues

### No tokens found

**Possible causes:**
1. Moralis API key invalid → Check .env
2. API quota exceeded → Wait or upgrade Moralis plan
3. No Pump.fun tokens currently available → Normal during low activity

**Debug:**
```bash
docker compose logs app | grep -i "moralis\|tier\|fallback"
```

### Scanning hangs

**Solution:**
```bash
docker compose restart app
```

**Check for infinite loops:**
```bash
docker stats  # Monitor CPU/memory
```

---

## Configuration Issues

### Wrong environment variables

**Verify .env is loaded:**
```bash
docker compose exec app python3 -c "from src.config import get_settings; s = get_settings(); print(f'DB: {s.db_host}:{s.db_port}/{s.db_name}')"
```

### Changes not taking effect

**Restart app:**
```bash
docker compose restart app
```

**If still not working:**
```bash
docker compose down
docker compose up -d
```

---

## Performance Issues

### High CPU usage

**Check what's running:**
```bash
docker stats
```

**Reduce scan frequency:**
Edit `.env`:
```
SCAN_INTERVAL_SECONDS=120  # Increase from 60
```

Then restart:
```bash
docker compose restart app
```

### High memory usage

**Check container memory limits:**
```bash
docker compose config | grep -i memory

# Increase limits in docker-compose.yml if needed
```

### Disk space full

**Check usage:**
```bash
df -h /var/lib/docker
docker system df
```

**Clean up:**
```bash
docker system prune -a  # WARNING: Removes unused images
```

---

## Logging Issues

### Can't find logs

**Check log locations:**
```bash
# Container logs
docker compose logs app

# Health check logs
tail -f /var/log/pump-signal/health.log

# App logs inside container
docker compose exec app cat logs/main.log
```

### Logs are too verbose

**Change log level in .env:**
```
LOG_LEVEL=WARNING  # INFO, WARNING, ERROR
```

---

## Health Check Issues

### Health check keeps restarting container

**Check health script:**
```bash
bash /home/eurisko/projects/pump-signal-app/scripts/health-check.sh
tail -20 /var/log/pump-signal/health.log
```

**Increase wait time in health check:**
Edit `scripts/health-check.sh`, increase timeout values.

### Cron job not running

**Verify cron is installed:**
```bash
which cron
systemctl status cron
```

**Check cron logs:**
```bash
grep CRON /var/log/syslog | tail -20
```

**Reinstall cron job:**
```bash
crontab -e
# Add: */5 * * * * /home/eurisko/projects/pump-signal-app/scripts/health-check.sh
```

---

## Network Issues

### Docker containers can't reach each other

**Check network:**
```bash
docker network inspect pump-signal-app_pump_signal_network
```

**Restart network:**
```bash
docker compose down
docker compose up -d
```

### Container can't reach external APIs

**Test from within container:**
```bash
docker compose exec app curl https://api.dexscreener.com/ping
```

**Check firewall:**
```bash
sudo ufw status
# May need to allow outbound traffic
```

---

## PostgreSQL Issues

### Can't connect to database

**Verify credentials:**
```bash
# Check .env
grep DB_ .env

# Test connection
docker compose exec postgres psql -U pump_user -d pump_signal -c "SELECT version()"
```

**Reset PostgreSQL:**
```bash
docker compose down -v
docker compose up -d postgres
sleep 10
docker compose up -d
```

### pgvector extension not loaded

**Check:**
```bash
docker compose exec postgres psql -U pump_user -d pump_signal -c "SELECT * FROM pg_extension WHERE extname = 'vector'"
```

**If missing, reinit:**
```bash
docker compose down -v
docker compose up -d
```

---

## Still Stuck?

1. **Collect diagnostics:**
   ```bash
   docker compose ps
   docker compose logs --tail=100 > /tmp/pump-signal-logs.txt
   cat /var/log/pump-signal/health.log >> /tmp/pump-signal-logs.txt
   ```

2. **Check error messages carefully** — often self-explanatory

3. **Try nuclear option:**
   ```bash
   docker compose down -v
   rm -rf /var/lib/docker/volumes/pump-signal-app_*
   docker compose up -d
   ```

4. **Reach out with logs** — include Docker version, OS, relevant logs
