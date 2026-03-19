#!/bin/bash

# Pump Signal Health Check (runs every 5 minutes via cron)

LOG_DIR="/var/log/pump-signal"
LOG_FILE="${LOG_DIR}/health.log"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Check 1: API liveness
API_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health)

# Check 2: DB freshness (recent scans)
DB_CHECK=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ready)

echo "[$TIMESTAMP] Health Check: API=$API_STATUS, DB=$DB_CHECK" >> "$LOG_FILE"

# If both healthy, exit
if [ "$API_STATUS" = "200" ] && [ "$DB_CHECK" = "200" ]; then
    echo "[$TIMESTAMP] ✅ All systems healthy" >> "$LOG_FILE"
    exit 0
fi

# If not healthy, restart
echo "[$TIMESTAMP] ⚠️  Service unhealthy - restarting..." >> "$LOG_FILE"
cd /home/eurisko/projects/pump-signal-app
docker-compose restart app >> "$LOG_FILE" 2>&1

echo "[$TIMESTAMP] ✅ Restart completed" >> "$LOG_FILE"
